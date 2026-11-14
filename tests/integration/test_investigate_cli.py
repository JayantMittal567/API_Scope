"""Integration tests for ApiScope Failure Investigation CLI (Task 22).

Verifies:
- Command help and option display
- Offline evaluation of authentic StockPile passing results (0 HTTP calls)
- Error handling on missing arguments or non-existent run IDs
- Investigation of mock failures with primary vs downstream cascade reporting
- JSON and YAML structured report exports
- SQLite historical run retrieval and investigation
- OpenAPI contract reconciliation via CLI --spec option
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from apiscope.cli.main import (
    EXIT_OK,
    EXIT_TOOL_ERROR,
    app,
)
from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.data.connection import DatabaseManager
from apiscope.data.repositories import RunRepository

runner = CliRunner()


def test_investigate_cli_help() -> None:
    """Investigate command displays help and options."""
    res = runner.invoke(app, ["investigate", "--help"])
    assert res.exit_code == EXIT_OK
    assert "--run-id" in res.output
    assert "--file" in res.output
    assert "--output" in res.output
    assert "--format" in res.output
    assert "--with-ai" in res.output
    assert "--spec" in res.output


def test_investigate_cli_missing_args() -> None:
    """Investigate command fails closed with clean error when neither --run-id nor --file is provided."""
    res = runner.invoke(app, ["investigate"])
    assert res.exit_code == EXIT_TOOL_ERROR
    assert "Please provide either --run-id <RUN_ID> or --file <PATH>" in res.output


def test_investigate_cli_real_stockpile_smoke() -> None:
    """Offline investigation of authentic StockPile smoke results (8/8 passed)."""
    stockpile_smoke_path = Path("stockpile_tests/stockpile_readonly_results.json")
    if not stockpile_smoke_path.is_file():
        pytest.skip("StockPile smoke results file not found")

    res = runner.invoke(app, ["investigate", "--file", str(stockpile_smoke_path)])
    assert res.exit_code == EXIT_OK
    assert "Evaluated Cases:         8" in res.output
    assert "Failures / Blocks:       0" in res.output
    assert "All cases executed cleanly" in res.output


def test_investigate_cli_real_stockpile_extended() -> None:
    """Offline investigation of authentic StockPile extended read-only results (30/30 passed)."""
    stockpile_ext_path = Path("stockpile_tests/stockpile_extended_results.json")
    if not stockpile_ext_path.is_file():
        pytest.skip("StockPile extended results file not found")

    res = runner.invoke(app, ["investigate", "--file", str(stockpile_ext_path)])
    assert res.exit_code == EXIT_OK
    assert "Evaluated Cases:         30" in res.output
    assert "Failures / Blocks:       0" in res.output


def test_investigate_cli_mock_failure_with_json_output(tmp_path: Path) -> None:
    """Investigates a mock workflow failure scenario and exports structured JSON report."""
    mock_run_data = {
        "run_id": "mock-wf-fail-001",
        "total_tests": 3,
        "results": [
            {
                "test_name": "[WF-ORD] [SETUP:1] Create Order Item",
                "module": "workflow",
                "tags": ["workflow:wf-ord", "step-type:setup"],
                "status": "FAILED",
                "method": "POST",
                "url": "http://localhost:8080/api/orders",
                "status_code": 500,
                "error_message": "HTTP 500 Internal Server Error: unhandled null pointer",
                "assertion_results": [
                    {
                        "type": "status",
                        "passed": False,
                        "message": "Expected 201, got 500",
                    }
                ],
            },
            {
                "test_name": "[WF-ORD] [SETUP:2] Link Inventory",
                "module": "workflow",
                "tags": ["workflow:wf-ord", "step-type:setup"],
                "status": "SKIPPED",
                "method": "POST",
                "url": "http://localhost:8080/api/inventory",
                "skip_reason": "Setup step 1 [POST /api/orders] failed",
            },
            {
                "test_name": "[WF-ORD] [TEST:3] Query Order Status",
                "module": "workflow",
                "tags": ["workflow:wf-ord", "step-type:test"],
                "status": "SKIPPED",
                "method": "GET",
                "url": "http://localhost:8080/api/orders/123",
                "skip_reason": "Setup step 1 [POST /api/orders] failed",
            },
        ],
    }
    input_file = tmp_path / "mock_run.json"
    input_file.write_text(json.dumps(mock_run_data), encoding="utf-8")

    out_file = tmp_path / "investigation_report.json"

    res = runner.invoke(
        app,
        ["investigate", "--file", str(input_file), "--output", str(out_file)],
    )

    assert res.exit_code == EXIT_OK
    assert "Primary Failures:        1" in res.output
    assert "Downstream Consequences: 2" in res.output
    assert "FINDING-001" in res.output
    assert "SUSPECTED_APPLICATION_FAILURE" in res.output
    assert "Downstream Cascades (Caused by Upstream Failures)" in res.output
    assert "Investigation report written to:" in res.output

    # Verify generated report file
    assert out_file.is_file()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["summary_metrics"]["primary_failures_count"] == 1
    assert data["summary_metrics"]["downstream_consequences_count"] == 2
    assert len(data["findings"]) == 3
    assert data["findings"][0]["is_primary_failure"] is True
    assert data["findings"][1]["is_primary_failure"] is False


def test_investigate_cli_yaml_output(tmp_path: Path) -> None:
    """Exports investigation report in YAML format."""
    mock_run_data = {
        "run_id": "mock-yaml-run",
        "total_tests": 1,
        "results": [
            {
                "test_name": "Timeout Test",
                "status": "ERROR",
                "method": "GET",
                "url": "http://localhost:8080/api/timeout",
                "error_message": "ReadTimeout: Request timed out",
            }
        ],
    }
    input_file = tmp_path / "mock_timeout.json"
    input_file.write_text(json.dumps(mock_run_data), encoding="utf-8")

    out_yaml = tmp_path / "report.yaml"

    res = runner.invoke(
        app,
        [
            "investigate",
            "--file",
            str(input_file),
            "--output",
            str(out_yaml),
            "--format",
            "yaml",
        ],
    )

    assert res.exit_code == EXIT_OK
    assert out_yaml.is_file()
    yaml_content = out_yaml.read_text(encoding="utf-8")
    parsed = yaml.safe_load(yaml_content)
    assert parsed["summary_metrics"]["total_failures_detected"] == 1
    assert parsed["findings"][0]["category"] == "NETWORK_OR_TRANSPORT_ISSUE"


def test_investigate_cli_sqlite_integration(tmp_path: Path) -> None:
    """Retrieves a historical test run from SQLite and runs deterministic investigation."""
    db_path = tmp_path / "test_history.db"
    db_manager = DatabaseManager(db_path)
    repo = RunRepository(db_manager)

    run_obj = RunResult(
        run_id="sqlite-hist-run-001",
        environment_name="dev",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        duration_ms=60.0,
        total_tests=2,
        passed_tests=1,
        failed_tests=1,
        error_tests=0,
        skipped_tests=0,
        results=[
            CaseResult(
                test_name="Passing Case",
                module="demo",
                tags=["smoke"],
                status=CaseStatus.PASSED,
                verified=True,
                method="GET",
                url="http://localhost:8080/api/pass",
                status_code=200,
                elapsed_ms=5.0,
                request_headers={},
                response_headers={},
                assertion_results=[
                    {"type": "status", "passed": True, "message": "Expected 200"}
                ],
                extracted_variables={},
                extraction_errors=[],
            ),
            CaseResult(
                test_name="Failing Case",
                module="demo",
                tags=["smoke"],
                status=CaseStatus.FAILED,
                verified=False,
                method="POST",
                url="http://localhost:8080/api/fail",
                status_code=500,
                elapsed_ms=12.0,
                request_headers={},
                response_headers={},
                assertion_results=[
                    {
                        "type": "status",
                        "passed": False,
                        "message": "Expected 201, got 500",
                    }
                ],
                extracted_variables={},
                extraction_errors=[],
                error_message="HTTP 500 Internal Server Error",
            ),
        ],
    )
    repo.save_run(run_obj)

    res = runner.invoke(
        app,
        ["investigate", "--run-id", "sqlite-hist-run-001", "--db", str(db_path)],
    )

    assert res.exit_code == EXIT_OK
    assert "Run ID:                  sqlite-hist-run-001" in res.output
    assert "Evaluated Cases:         2" in res.output
    assert "Primary Failures:        1" in res.output
    assert "SUSPECTED_APPLICATION_FAILURE" in res.output


def test_investigate_cli_nonexistent_run_id(tmp_path: Path) -> None:
    """Returns tool error when requested run ID does not exist in SQLite."""
    db_path = tmp_path / "empty.db"
    res = runner.invoke(
        app,
        ["investigate", "--run-id", "nonexistent-run-xyz", "--db", str(db_path)],
    )
    assert res.exit_code == EXIT_TOOL_ERROR
    assert "not found in SQLite database" in res.output


def test_investigate_cli_with_spec_reconciliation(tmp_path: Path) -> None:
    """Verifies that providing --spec reconciles failures against the OpenAPI contract."""
    mock_run_data = {
        "run_id": "mock-spec-run",
        "total_tests": 1,
        "results": [
            {
                "test_name": "GET /api/products",
                "status": "FAILED",
                "method": "GET",
                "url": "http://localhost:8080/api/products",
                "status_code": 404,
                "assertion_results": [
                    {
                        "type": "status",
                        "passed": False,
                        "message": "Expected 200, got 404",
                    }
                ],
            }
        ],
    }
    input_file = tmp_path / "mock_spec_case.json"
    input_file.write_text(json.dumps(mock_run_data), encoding="utf-8")

    spec_path = Path("stockpile_tests/stockpile_openapi.yaml")
    if not spec_path.is_file():
        pytest.skip("StockPile OpenAPI spec not found")

    res = runner.invoke(
        app,
        ["investigate", "--file", str(input_file), "--spec", str(spec_path)],
    )

    assert res.exit_code == EXIT_OK
    assert "CONTRACT_OR_RESPONSE_MISMATCH" in res.output
