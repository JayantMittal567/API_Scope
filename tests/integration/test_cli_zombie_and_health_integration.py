"""Integration tests for CLI zombie and health commands.

Covers: FR-24, FR-26, FR-32, FR-34, FR-38, FR-44.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from apiscope.cli.main import app
from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.data.connection import DatabaseManager
from apiscope.data.repositories import RunRepository

runner = CliRunner()


def _populate_orders_run(db_path: Path) -> str:
    """Helper to store a run with 100% endpoint coverage for module orders."""
    db_mgr = DatabaseManager(db_path)
    try:
        repo = RunRepository(db_mgr)
        now = datetime.now(UTC)
        cases = [
            CaseResult(
                test_name="test_post_orders",
                module="orders",
                tags=["smoke", "functional"],
                status=CaseStatus.PASSED,
                verified=True,
                method="POST",
                url="http://localhost:8000/orders",
                status_code=201,
                elapsed_ms=25.0,
                request_headers={},
                response_headers={},
                assertion_results=[{"rule_type": "status", "passed": True}],
                extracted_variables={},
                extraction_errors=[],
            ),
            CaseResult(
                test_name="test_get_order",
                module="orders",
                tags=["smoke", "functional"],
                status=CaseStatus.PASSED,
                verified=True,
                method="GET",
                url="http://localhost:8000/orders/101",
                status_code=200,
                elapsed_ms=20.0,
                request_headers={},
                response_headers={},
                assertion_results=[{"rule_type": "status", "passed": True}],
                extracted_variables={},
                extraction_errors=[],
            ),
            CaseResult(
                test_name="test_cancel_order",
                module="orders",
                tags=["smoke", "functional"],
                status=CaseStatus.PASSED,
                verified=True,
                method="POST",
                url="http://localhost:8000/orders/101/cancel",
                status_code=200,
                elapsed_ms=30.0,
                request_headers={},
                response_headers={},
                assertion_results=[{"rule_type": "status", "passed": True}],
                extracted_variables={},
                extraction_errors=[],
            ),
            CaseResult(
                test_name="test_sync_invoice",
                module="orders",
                tags=["smoke", "functional"],
                status=CaseStatus.PASSED,
                verified=True,
                method="POST",
                url="http://localhost:8000/orders/101/sync-invoice",
                status_code=200,
                elapsed_ms=35.0,
                request_headers={},
                response_headers={},
                assertion_results=[{"rule_type": "status", "passed": True}],
                extracted_variables={},
                extraction_errors=[],
            ),
        ]
        run_res = RunResult(
            run_id="run-orders-passing",
            environment_name="test",
            started_at=now,
            finished_at=now,
            duration_ms=110.0,
            total_tests=len(cases),
            passed_tests=len(cases),
            failed_tests=0,
            error_tests=0,
            skipped_tests=0,
            results=cases,
        )
        return repo.save_run(run_res)
    finally:
        db_mgr.close()


def test_cli_zombie_runs_on_sample_app_and_respects_strict_exit_code(
    tmp_path: Path,
) -> None:
    """Covers: FR-24, FR-26, FR-38.

    Run apiscope zombie against sample_app spec and access log:
    - Verifies exit code 0 without --strict.
    - Verifies exit code 2 with --strict (due to detected zombies).
    - Verifies JSON report creation on --output.
    """
    spec_path = "sample_app/openapi.yaml"
    log_path = "sample_app/logs/access.log"
    out_file = tmp_path / "zombie_report.json"

    # 1. Without strict: exit code 0
    res_normal = runner.invoke(
        app,
        [
            "zombie",
            "--spec",
            spec_path,
            "--access-log",
            log_path,
            "--output",
            str(out_file),
        ],
    )
    assert res_normal.exit_code == 0
    assert "Zombie API Analysis" in res_normal.stdout
    assert "UNUSED" in res_normal.stdout
    assert "UNDOCUMENTED" in res_normal.stdout
    assert "DEPRECATED-BUT-CALLED" in res_normal.stdout
    assert out_file.exists()

    saved_data = json.loads(out_file.read_text(encoding="utf-8"))
    assert saved_data["total_findings"] > 0
    assert "counts_by_class" in saved_data

    # 2. With strict: exit code 2
    res_strict = runner.invoke(
        app,
        [
            "zombie",
            "--spec",
            spec_path,
            "--access-log",
            log_path,
            "--strict",
        ],
    )
    assert res_strict.exit_code == 2


def test_cli_health_integrates_zombie_and_silent_failure_signals_into_verdict(
    tmp_path: Path,
) -> None:
    """Covers: FR-32, FR-34, FR-38, FR-44.

    Execute a passing run for module orders (100% endpoint coverage),
    then run apiscope health:
    - Without log/probe: HEALTHY (exit 0)
    - With --app-log, --health-file, and --strict: HEALTHY WITH WARNINGS
      with 'PASS WITH WARNINGS: APIs healthy, internal issue suspected' (exit 2).
    """
    db_file = tmp_path / "test_history.db"
    _populate_orders_run(db_file)

    spec_path = "sample_app/openapi.yaml"
    app_log_path = "sample_app/logs/app.log"
    health_file_path = "examples/m9_m10_health_probe_degraded.json"

    # 1. Baseline health check without silent failure signals -> HEALTHY (code 0)
    res_clean = runner.invoke(
        app,
        [
            "health",
            "--db",
            str(db_file),
            "--module",
            "orders",
            "--spec",
            spec_path,
            "--strict",
        ],
    )
    assert res_clean.exit_code == 0
    assert "HEALTHY" in res_clean.stdout
    assert "HEALTHY WITH WARNINGS" not in res_clean.stdout

    # 2. Enhanced health check with silent failure signals (code 2)
    res_flagged = runner.invoke(
        app,
        [
            "health",
            "--db",
            str(db_file),
            "--module",
            "orders",
            "--spec",
            spec_path,
            "--app-log",
            app_log_path,
            "--health-file",
            health_file_path,
            "--strict",
        ],
    )
    assert res_flagged.exit_code == 2
    assert "HEALTHY WITH WARNINGS" in res_flagged.stdout
    assert (
        "PASS WITH WARNINGS: APIs healthy, internal issue suspected"
        in res_flagged.stdout
    )
