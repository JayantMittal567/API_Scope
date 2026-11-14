"""Integration tests for the unified trust assessment CLI command 'apiscope assess' (Task 25).

Verifies:
- Command help and available options.
- Correlating OpenAPI spec and test run files.
- JSON and YAML report export.
- SQLite database run resolution.
- Module filtering (--module).
- Strict exit code enforcement (EXIT_WARNINGS_STRICT = 2 vs EXIT_OK = 0).
- Error handling on missing files (EXIT_TOOL_ERROR = 3).
- Authentic StockPile smoke run assessment via CLI.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import yaml
from click.utils import strip_ansi
from typer.testing import CliRunner

from apiscope.cli.main import (
    EXIT_OK,
    EXIT_TOOL_ERROR,
    EXIT_WARNINGS_STRICT,
    app,
)
from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.data.connection import DatabaseManager
from apiscope.data.repositories import RunRepository

runner = CliRunner()


def _create_sample_spec(path: Path) -> Path:
    """Create a minimal valid OpenAPI spec."""
    content = """
openapi: 3.0.0
info:
  title: Test API
  version: 1.0.0
paths:
  /items:
    get:
      summary: List items
      responses:
        '200':
          description: OK
    post:
      summary: Create item
      responses:
        '201':
          description: Created
  /users:
    get:
      summary: List users
      responses:
        '200':
          description: OK
"""
    spec_path = path / "openapi.yaml"
    spec_path.write_text(content.strip(), encoding="utf-8")
    return spec_path


def _create_sample_run_file(path: Path, failed: bool = False) -> Path:
    """Create a sample test run JSON file."""
    path.mkdir(parents=True, exist_ok=True)
    cases = [
        {
            "test_name": "test_get_items",
            "module": "items",
            "method": "GET",
            "url": "http://localhost:8080/items",
            "status": "PASSED",
            "status_code": 200,
            "elapsed_ms": 45.0,
            "tags": [],
            "verified": False,
            "request_headers": {},
            "response_headers": {},
            "extracted_variables": {},
            "extraction_errors": [],
            "assertion_results": [{"type": "status", "passed": True}],
        }
    ]
    if failed:
        cases.append(
            {
                "test_name": "test_create_item",
                "module": "items",
                "method": "POST",
                "url": "http://localhost:8080/items",
                "status": "FAILED",
                "status_code": 500,
                "elapsed_ms": 120.0,
                "tags": [],
                "verified": False,
                "request_headers": {},
                "response_headers": {},
                "extracted_variables": {},
                "extraction_errors": [],
                "error_message": "Internal Server Error: NullPointerException in Handler",
                "assertion_results": [{"type": "status", "passed": False}],
            }
        )

    run_dict = {
        "run_id": "run-cli-test",
        "environment_name": "test",
        "started_at": datetime.now(UTC).isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "duration_ms": 165.0,
        "total_tests": len(cases),
        "passed_tests": 1,
        "failed_tests": 1 if failed else 0,
        "error_tests": 0,
        "skipped_tests": 0,
        "results": cases,
    }

    run_path = path / "run_results.json"
    run_path.write_text(json.dumps(run_dict, indent=2), encoding="utf-8")
    return run_path


def test_assess_cli_help() -> None:
    """Verify that 'apiscope assess --help' lists all expected flags and descriptions."""
    result = runner.invoke(app, ["assess", "--help"])
    result_help = strip_ansi(result.output)
    assert result.exit_code == EXIT_OK
    assert "Correlate multi-source diagnostic outputs" in result_help
    assert "--spec" in result_help
    assert "--run-file" in result_help
    assert "--repo" in result_help
    assert "--workflow-report" in result_help
    assert "--investigation-report" in result_help
    assert "--silent-report" in result_help
    assert "--inventory-report" in result_help
    assert "--strict" in result_help
    assert "--output" in result_help
    assert "--format" in result_help


def test_assess_cli_with_spec_and_clean_run(tmp_path: Path) -> None:
    """Verify running assess CLI on a clean passing test run."""
    spec_path = _create_sample_spec(tmp_path)
    run_path = _create_sample_run_file(tmp_path, failed=False)

    result = runner.invoke(
        app,
        ["assess", "--spec", str(spec_path), "--run-file", str(run_path)],
    )

    assert result.exit_code == EXIT_OK
    assert "UNIFIED TRUST & EVIDENCE ENGINE" in result.output
    assert "[Operation Coverage Summary]" in result.output
    assert "Diagnostic Verdict Breakdown" in result.output


def test_assess_cli_json_and_yaml_export(tmp_path: Path) -> None:
    """Verify that --output correctly writes JSON and YAML files."""
    spec_path = _create_sample_spec(tmp_path)
    run_path = _create_sample_run_file(tmp_path, failed=False)

    out_json = tmp_path / "trust_report.json"
    result_json = runner.invoke(
        app,
        [
            "assess",
            "--spec",
            str(spec_path),
            "--run-file",
            str(run_path),
            "--output",
            str(out_json),
            "--format",
            "json",
        ],
    )
    assert result_json.exit_code == EXIT_OK
    assert out_json.is_file()
    parsed_json = json.loads(out_json.read_text(encoding="utf-8"))
    assert parsed_json["target_context"] == "primary"
    assert "coverage" in parsed_json

    out_yaml = tmp_path / "trust_report.yaml"
    result_yaml = runner.invoke(
        app,
        [
            "assess",
            "--spec",
            str(spec_path),
            "--run-file",
            str(run_path),
            "--output",
            str(out_yaml),
            "--format",
            "yaml",
        ],
    )
    assert result_yaml.exit_code == EXIT_OK
    assert out_yaml.is_file()
    parsed_yaml = yaml.safe_load(out_yaml.read_text(encoding="utf-8"))
    assert parsed_yaml["target_context"] == "primary"


def test_assess_cli_sqlite_database_run(tmp_path: Path) -> None:
    """Verify that assess CLI can resolve runs from a SQLite database via --db and --run-id."""
    db_file = tmp_path / "history.db"
    db_mgr = DatabaseManager(db_file)
    repo = RunRepository(db_mgr)
    spec_path = _create_sample_spec(tmp_path)

    now = datetime.now(UTC)
    case = CaseResult(
        test_name="test_get_items",
        module="items",
        tags=[],
        status=CaseStatus.PASSED,
        verified=True,
        method="GET",
        url="http://localhost:8080/items",
        status_code=200,
        elapsed_ms=12.0,
        request_headers={},
        response_headers={},
        assertion_results=[],
        extracted_variables={},
        extraction_errors=[],
    )
    run_res = RunResult(
        run_id="run-cli-test",
        environment_name="staging",
        started_at=now,
        finished_at=now,
        duration_ms=12.0,
        total_tests=1,
        passed_tests=1,
        failed_tests=0,
        error_tests=0,
        skipped_tests=0,
        results=[case],
    )
    repo.save_run(run_res)

    result = runner.invoke(
        app,
        [
            "assess",
            "--db",
            str(db_file),
            "--run-id",
            "run-cli-test",
            "--spec",
            str(spec_path),
        ],
    )
    assert result.exit_code == EXIT_OK
    assert "UNIFIED TRUST & EVIDENCE ENGINE" in result.output


def test_assess_cli_strict_mode(tmp_path: Path) -> None:
    """Verify that --strict exits with code 2 on suspected defects and code 0 on clean runs."""
    spec_path = _create_sample_spec(tmp_path)

    # Clean run with strict mode -> exit code 0
    clean_run_path = _create_sample_run_file(tmp_path / "clean", failed=False)
    res_clean = runner.invoke(
        app,
        [
            "assess",
            "--spec",
            str(spec_path),
            "--run-file",
            str(clean_run_path),
            "--strict",
        ],
    )
    assert res_clean.exit_code == EXIT_OK

    # Failing run (HTTP 500 suspected defect) with strict mode -> exit code 2
    failing_run_path = _create_sample_run_file(tmp_path / "failing", failed=True)
    res_failing = runner.invoke(
        app,
        [
            "assess",
            "--spec",
            str(spec_path),
            "--run-file",
            str(failing_run_path),
            "--strict",
        ],
    )
    assert res_failing.exit_code == EXIT_WARNINGS_STRICT


def test_assess_cli_missing_spec_file_error() -> None:
    """Verify that specifying a nonexistent spec file raises EXIT_TOOL_ERROR (3)."""
    result = runner.invoke(
        app,
        ["assess", "--spec", "nonexistent_spec_12345.yaml"],
    )
    assert result.exit_code == EXIT_TOOL_ERROR


def test_assess_cli_stockpile_smoke() -> None:
    """Verify running assess CLI on authentic StockPile smoke run."""
    result = runner.invoke(
        app,
        [
            "assess",
            "--spec",
            "stockpile_tests/stockpile_openapi.yaml",
            "--run-file",
            "stockpile_tests/stockpile_readonly_results.json",
        ],
    )
    assert result.exit_code == EXIT_OK
    assert "UNIFIED TRUST & EVIDENCE ENGINE" in result.output
    assert "Attempted Operations:    8 (13.3%)" in result.output
    assert "Untested Operations:     52 (86.7%)" in result.output
