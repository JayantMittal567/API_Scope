"""Integration tests for ApiScope CLI health command (apiscope health).

Covers: FR-24, FR-25, FR-26, FR-31, FR-32, FR-33, FR-34, NFR-04, NFR-06.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from apiscope.cli.main import app
from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.data.connection import DatabaseManager
from apiscope.data.repositories import RunRepository

runner = CliRunner()


def _create_sample_run(
    db_path: Path,
    cases: list[CaseResult],
    run_id: str = "run-cli-health-test",
) -> str:
    """Helper to populate an SQLite DB with a run and cases."""
    db_manager = DatabaseManager(db_path)
    try:
        repo = RunRepository(db_manager)
        now = datetime.now(UTC)
        run = RunResult(
            run_id=run_id,
            environment_name="test-env",
            started_at=now,
            finished_at=now,
            duration_ms=120.0,
            total_tests=len(cases),
            passed_tests=sum(1 for c in cases if c.status == CaseStatus.PASSED),
            failed_tests=sum(1 for c in cases if c.status == CaseStatus.FAILED),
            error_tests=sum(1 for c in cases if c.status == CaseStatus.ERROR),
            skipped_tests=sum(1 for c in cases if c.status == CaseStatus.SKIPPED),
            results=cases,
        )
        return repo.save_run(run)
    finally:
        db_manager.close()


def _make_cli_case(
    name: str = "test",
    module: str = "orders",
    method: str = "GET",
    url: str = "/orders",
    status: CaseStatus = CaseStatus.PASSED,
    elapsed_ms: float = 20.0,
    assertion_results: list[dict[str, Any]] | None = None,
) -> CaseResult:
    """Helper to build a CaseResult for CLI integration tests."""
    return CaseResult(
        test_name=name,
        module=module,
        tags=["positive"],
        status=status,
        verified=True,
        method=method,
        url=url,
        status_code=200 if status == CaseStatus.PASSED else 500,
        elapsed_ms=elapsed_ms,
        request_headers={},
        response_headers={},
        assertion_results=assertion_results
        or [
            {
                "rule_type": "status",
                "passed": status == CaseStatus.PASSED,
                "expected": 200,
                "actual": 200 if status == CaseStatus.PASSED else 500,
                "message": "Status code check",
            }
        ],
        extracted_variables={},
        extraction_errors=[],
        error_message="Failure occurred" if status == CaseStatus.FAILED else None,
    )


def test_cli_health_healthy_module_returns_exit_code_0_and_writes_json(
    tmp_path: Path,
) -> None:
    """Covers: FR-24, FR-26, FR-34.

    Verifies apiscope health returns exit code 0 on healthy module and exports JSON.
    """
    db_path = tmp_path / "history.db"
    cases = [
        _make_cli_case(name="get_orders", url="/orders"),
        _make_cli_case(name="get_order_id", url="/orders/101"),
    ]
    _create_sample_run(db_path, cases)

    spec_path = tmp_path / "openapi.yaml"
    spec_path.write_text(
        """openapi: "3.0.3"
info:
  title: "Test API"
  version: "1.0.0"
paths:
  /orders:
    get:
      tags: ["orders"]
      responses:
        "200":
          description: "OK"
  /orders/{order_id}:
    get:
      tags: ["orders"]
      responses:
        "200":
          description: "OK"
""",
        encoding="utf-8",
    )

    out_json = tmp_path / "health_report.json"
    result = runner.invoke(
        app,
        [
            "health",
            "--module",
            "orders",
            "--db",
            str(db_path),
            "--spec",
            str(spec_path),
            "--output",
            str(out_json),
        ],
    )

    assert result.exit_code == 0
    assert "Module Health: orders [HEALTHY]" in result.stdout
    assert "verified against" in result.stdout.lower()

    assert out_json.is_file()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["module"] == "orders"
    assert data["verdict"] == "HEALTHY"
    assert data["endpoint_coverage_pct"] == 100.0


def test_cli_health_unhealthy_or_incomplete_returns_exit_code_1(
    tmp_path: Path,
) -> None:
    """Covers: FR-26, FR-34.

    Verifies exit code 1 when verdict is INCOMPLETE or UNHEALTHY.
    """
    db_path = tmp_path / "history.db"
    spec_path = tmp_path / "openapi.yaml"
    spec_path.write_text(
        """openapi: "3.0.3"
info:
  title: "Test API"
  version: "1.0.0"
paths:
  /orders:
    get:
      tags: ["orders"]
      responses:
        "200":
          description: "OK"
  /orders/{order_id}:
    delete:
      tags: ["orders"]
      responses:
        "204":
          description: "No Content"
""",
        encoding="utf-8",
    )

    # Subcase A: Incomplete coverage (only 1 of 2 endpoints tested = 50% < 80%)
    cases_incomplete = [_make_cli_case(name="get_orders", url="/orders")]
    _create_sample_run(db_path, cases_incomplete, run_id="run-incomplete")
    res_incomplete = runner.invoke(
        app,
        [
            "health",
            "--module",
            "orders",
            "--db",
            str(db_path),
            "--spec",
            str(spec_path),
            "--threshold",
            "80.0",
        ],
    )
    assert res_incomplete.exit_code == 1
    assert "Module Health: orders [INCOMPLETE]" in res_incomplete.stdout

    # Subcase B: Unhealthy (failed test)
    cases_unhealthy = [
        _make_cli_case(name="fail_orders", url="/orders", status=CaseStatus.FAILED)
    ]
    _create_sample_run(db_path, cases_unhealthy, run_id="run-unhealthy")
    res_unhealthy = runner.invoke(
        app,
        [
            "health",
            "--module",
            "orders",
            "--db",
            str(db_path),
            "--run-id",
            "run-unhealthy",
        ],
    )
    assert res_unhealthy.exit_code == 1
    assert "Module Health: orders [UNHEALTHY]" in res_unhealthy.stdout


def test_cli_health_warnings_returns_0_default_and_2_with_strict(
    tmp_path: Path,
) -> None:
    """Covers: FR-26, FR-34.

    Verifies exit code 0 for warnings without --strict, and exit code 2 with --strict.
    """
    db_path = tmp_path / "history.db"
    # Case with high latency (85ms)
    cases = [_make_cli_case(name="order_slow", url="/orders", elapsed_ms=85.0)]
    _create_sample_run(db_path, cases)

    # Without --strict: should succeed (exit 0)
    res_default = runner.invoke(
        app,
        [
            "health",
            "--module",
            "orders",
            "--db",
            str(db_path),
            "--baseline-p95",
            "40.0",
        ],
    )
    assert res_default.exit_code == 0
    assert "Module Health: orders [HEALTHY WITH WARNINGS]" in res_default.stdout

    # With --strict: should exit 2
    res_strict = runner.invoke(
        app,
        [
            "health",
            "--module",
            "orders",
            "--db",
            str(db_path),
            "--baseline-p95",
            "40.0",
            "--strict",
        ],
    )
    assert res_strict.exit_code == 2
    assert "Module Health: orders [HEALTHY WITH WARNINGS]" in res_strict.stdout


def test_cli_health_missing_run_or_unsafe_output_path_returns_exit_code_3(
    tmp_path: Path,
) -> None:
    """Covers: FR-26, FR-34, NFR-04.

    Verifies exit code 3 for missing test run and directory traversal in output path.
    """
    db_path = tmp_path / "history.db"

    # Subcase A: Missing DB / Run
    res_missing = runner.invoke(
        app,
        [
            "health",
            "--db",
            str(tmp_path / "non_existent.db"),
        ],
    )
    assert res_missing.exit_code == 3
    err_text = res_missing.stderr + res_missing.stdout
    assert "Error: No test run found" in err_text

    # Subcase B: Directory traversal in output path
    cases = [_make_cli_case(name="order_ok", url="/orders")]
    _create_sample_run(db_path, cases)

    res_traversal = runner.invoke(
        app,
        [
            "health",
            "--db",
            str(db_path),
            "--output",
            "../forbidden_report.json",
        ],
    )
    assert res_traversal.exit_code == 3
    assert "traversal" in (res_traversal.stderr + res_traversal.stdout).lower()
