"""Integration tests for ApiScope Silent Failure and Latency Drift CLI commands (Task 23).

Verifies:
- 'diagnose-silent' and 'latency-drift' command help and option displays.
- Offline evaluation of authentic StockPile passing results.
- Detection and correlation of application log errors via --app-log.
- Latency comparison between baseline and current runs via --baseline and --current.
- Rejection of cross-environment comparisons by default.
- Handling insufficient samples (<5).
- Structured JSON and YAML export functionality.
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


def _create_sample_run(
    run_id: str,
    latencies: list[float],
    env: str = "test",
    module: str = "items",
    method: str = "GET",
    url: str = "http://localhost:8080/api/items",
) -> dict:
    """Create sample run JSON structure."""
    return {
        "run_id": run_id,
        "environment_name": env,
        "duration_ms": sum(latencies),
        "total_tests": len(latencies),
        "passed_tests": len(latencies),
        "failed_tests": 0,
        "error_tests": 0,
        "skipped_tests": 0,
        "results": [
            {
                "test_name": f"{run_id}_case_{i}",
                "module": module,
                "tags": ["perf"],
                "status": "PASSED",
                "verified": True,
                "method": method,
                "url": url,
                "status_code": 200,
                "elapsed_ms": lat,
                "request_headers": {},
                "response_headers": {},
                "assertion_results": [],
                "extracted_variables": {},
                "extraction_errors": [],
            }
            for i, lat in enumerate(latencies)
        ],
    }


def test_diagnose_silent_cli_help() -> None:
    """Diagnose-silent displays help and options."""
    res = runner.invoke(app, ["diagnose-silent", "--help"])
    assert res.exit_code == EXIT_OK
    assert "--run-id" in res.output
    assert "--file" in res.output
    assert "--workflow" in res.output
    assert "--app-log" in res.output
    assert "--health-file" in res.output
    assert "--baseline-run" in res.output


def test_diagnose_silent_cli_stockpile_smoke() -> None:
    """Offline silent failure evaluation of StockPile smoke results."""
    stockpile_path = Path("stockpile_tests/stockpile_readonly_results.json")
    if not stockpile_path.is_file():
        pytest.skip("StockPile smoke results file not found")

    res = runner.invoke(app, ["diagnose-silent", "--file", str(stockpile_path)])
    assert res.exit_code == EXIT_OK
    assert "SILENT FAILURE DIAGNOSTIC REPORT" in res.output
    assert "Silent Failures: False" in res.output
    assert "0 verified mismatches" in res.output
    assert "side-effects unverified / not tested" in res.output


def test_diagnose_silent_cli_with_app_log(tmp_path: Path) -> None:
    """Diagnose-silent correlates application log errors with test run."""
    run_file = tmp_path / "test_run.json"
    run_data = _create_sample_run("run-1", [25.0])
    run_file.write_text(json.dumps(run_data), encoding="utf-8")

    log_file = tmp_path / "app.log"
    log_file.write_text(
        "2026-11-14T20:00:00Z ERROR [items] Database deadlocked during sync\n",
        encoding="utf-8",
    )

    res = runner.invoke(
        app,
        [
            "diagnose-silent",
            "--file",
            str(run_file),
            "--app-log",
            str(log_file),
        ],
    )
    assert res.exit_code == EXIT_OK
    assert "PASS WITH WARNINGS" in res.output
    assert "S1_LOG_ERROR" in res.output
    assert "Database deadlocked" in res.output


def test_diagnose_silent_cli_export_json_and_yaml(tmp_path: Path) -> None:
    """Diagnose-silent exports structured JSON and YAML reports."""
    run_file = tmp_path / "test_run.json"
    run_data = _create_sample_run("run-1", [25.0])
    run_file.write_text(json.dumps(run_data), encoding="utf-8")

    out_json = tmp_path / "silent_report.json"
    res_json = runner.invoke(
        app,
        ["diagnose-silent", "--file", str(run_file), "--output", str(out_json)],
    )
    assert res_json.exit_code == EXIT_OK
    assert out_json.is_file()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert "verdict_banner" in data
    assert "findings" in data

    out_yaml = tmp_path / "silent_report.yaml"
    res_yaml = runner.invoke(
        app,
        [
            "diagnose-silent",
            "--file",
            str(run_file),
            "--output",
            str(out_yaml),
            "--format",
            "yaml",
        ],
    )
    assert res_yaml.exit_code == EXIT_OK
    assert out_yaml.is_file()
    yaml_data = yaml.safe_load(out_yaml.read_text(encoding="utf-8"))
    assert "verdict_banner" in yaml_data


def test_latency_drift_cli_help() -> None:
    """Latency-drift displays help and options."""
    res = runner.invoke(app, ["latency-drift", "--help"])
    assert res.exit_code == EXIT_OK
    assert "--baseline" in res.output
    assert "--current" in res.output
    assert "--min-samples" in res.output
    assert "--tolerance-pct" in res.output
    assert "--min-abs-ms" in res.output
    assert "--strict-env" in res.output


def test_latency_drift_cli_with_files(tmp_path: Path) -> None:
    """Latency-drift flags regression between baseline and current JSON files."""
    base_file = tmp_path / "base_run.json"
    base_data = _create_sample_run("base", [50.0] * 6, env="staging")
    base_file.write_text(json.dumps(base_data), encoding="utf-8")

    curr_file = tmp_path / "curr_run.json"
    curr_data = _create_sample_run("curr", [120.0] * 6, env="staging")
    curr_file.write_text(json.dumps(curr_data), encoding="utf-8")

    out_file = tmp_path / "drift_report.json"
    res = runner.invoke(
        app,
        [
            "latency-drift",
            "--baseline",
            str(base_file),
            "--current",
            str(curr_file),
            "--output",
            str(out_file),
        ],
    )
    assert res.exit_code == EXIT_OK
    assert "LATENCY DRIFT DIAGNOSTIC REPORT" in res.output
    assert "REGRESSION_DETECTED" in res.output
    assert out_file.is_file()

    rep_dict = json.loads(out_file.read_text(encoding="utf-8"))
    assert rep_dict["summary_metrics"]["has_regressions"] is True
    assert rep_dict["summary_metrics"]["regressions_count"] == 1


def test_latency_drift_cli_rejects_incompatible_environments(tmp_path: Path) -> None:
    """Latency-drift strictly rejects comparisons across different target environments."""
    base_file = tmp_path / "base_run.json"
    base_data = _create_sample_run(
        "base", [50.0] * 6, env="staging", url="http://staging.internal:8080/api/items"
    )
    base_file.write_text(json.dumps(base_data), encoding="utf-8")

    curr_file = tmp_path / "curr_run.json"
    curr_data = _create_sample_run(
        "curr", [120.0] * 6, env="prod", url="http://prod.internal:8080/api/items"
    )
    curr_file.write_text(json.dumps(curr_data), encoding="utf-8")

    res = runner.invoke(
        app,
        [
            "latency-drift",
            "--baseline",
            str(base_file),
            "--current",
            str(curr_file),
        ],
    )
    assert res.exit_code == EXIT_OK
    assert "INCOMPATIBLE_CONTEXT" in res.output
    assert "Target environment mismatch" in res.output


def test_latency_drift_cli_insufficient_samples(tmp_path: Path) -> None:
    """Latency-drift flags INSUFFICIENT_DATA when sample counts < min_samples."""
    base_file = tmp_path / "base_run.json"
    base_data = _create_sample_run("base", [50.0] * 2, env="staging")
    base_file.write_text(json.dumps(base_data), encoding="utf-8")

    curr_file = tmp_path / "curr_run.json"
    curr_data = _create_sample_run("curr", [150.0] * 2, env="staging")
    curr_file.write_text(json.dumps(curr_data), encoding="utf-8")

    res = runner.invoke(
        app,
        [
            "latency-drift",
            "--baseline",
            str(base_file),
            "--current",
            str(curr_file),
            "--min-samples",
            "5",
        ],
    )
    assert res.exit_code == EXIT_OK
    assert "INSUFFICIENT_DATA" in res.output
    assert "Insufficient sampling" in res.output


def test_diagnose_silent_cli_with_db_run_id(tmp_path: Path) -> None:
    """Test diagnose-silent resolving run from SQLite database by run-id."""
    db_file = tmp_path / "diag.db"
    db_manager = DatabaseManager(db_file)
    repo = RunRepository(db_manager)

    now = datetime.now(UTC)
    case = CaseResult(
        test_name="case_1",
        module="orders",
        tags=["order"],
        status=CaseStatus.PASSED,
        verified=True,
        method="GET",
        url="http://localhost:8000/orders",
        status_code=200,
        elapsed_ms=25.0,
        request_headers={},
        response_headers={},
        assertion_results=[],
        extracted_variables={},
        extraction_errors=[],
    )
    run_res = RunResult(
        run_id="run-db-1",
        environment_name="staging",
        started_at=now,
        finished_at=now,
        duration_ms=25.0,
        total_tests=1,
        passed_tests=1,
        failed_tests=0,
        error_tests=0,
        skipped_tests=0,
        results=[case],
    )
    repo.save_run(run_res)

    res = runner.invoke(
        app,
        ["diagnose-silent", "--db", str(db_file), "--run-id", "run-db-1"],
    )
    assert res.exit_code == EXIT_OK
    assert "SILENT FAILURE DIAGNOSTIC REPORT" in res.output


def test_diagnose_silent_cli_nonexistent_run_id(tmp_path: Path) -> None:
    """Test diagnose-silent with missing run-id exits with error."""
    db_file = tmp_path / "empty.db"
    db_manager = DatabaseManager(db_file)
    RunRepository(db_manager)

    res = runner.invoke(
        app,
        ["diagnose-silent", "--db", str(db_file), "--run-id", "missing-run"],
    )
    assert res.exit_code == EXIT_TOOL_ERROR
    assert "Run 'missing-run' not found in SQLite database" in res.output


def test_diagnose_silent_cli_with_health_file(tmp_path: Path) -> None:
    """Test diagnose-silent with --health-file evaluating S2."""
    run_file = tmp_path / "run.json"
    run_file.write_text(json.dumps(_create_sample_run("r1", [20.0])), encoding="utf-8")

    health_file = tmp_path / "health.json"
    health_file.write_text(
        json.dumps({"status": "DEGRADED", "dependencies": {"redis": "DOWN"}}),
        encoding="utf-8",
    )

    res = runner.invoke(
        app,
        ["diagnose-silent", "--file", str(run_file), "--health-file", str(health_file)],
    )
    assert res.exit_code == EXIT_OK
    assert "S2_DEPENDENCY_PROBE" in res.output
    assert "DEGRADED" in res.output


def test_diagnose_silent_cli_with_baseline_run(tmp_path: Path) -> None:
    """Test diagnose-silent with --baseline-run evaluating S3 latency drift."""
    base_file = tmp_path / "base.json"
    base_file.write_text(
        json.dumps(_create_sample_run("b", [50.0] * 6)), encoding="utf-8"
    )

    curr_file = tmp_path / "curr.json"
    curr_file.write_text(
        json.dumps(_create_sample_run("c", [130.0] * 6)), encoding="utf-8"
    )

    res = runner.invoke(
        app,
        ["diagnose-silent", "--file", str(curr_file), "--baseline-run", str(base_file)],
    )
    assert res.exit_code == EXIT_OK
    assert "S3_LATENCY_DRIFT" in res.output


def test_diagnose_silent_cli_with_workflow(tmp_path: Path) -> None:
    """Test diagnose-silent with --workflow loading workflow JSON."""
    wf_data = {
        "workflow_id": "wf_smoke",
        "duration_ms": 35.0,
        "environment_name": "test",
        "setup_results": [],
        "test_results": [
            {
                "step_number": 1,
                "name": "Read Items",
                "step_type": "TEST",
                "status": "PASSED",
                "method": "GET",
                "url": "http://localhost:8000/api/items",
                "status_code": 200,
                "elapsed_ms": 35.0,
            }
        ],
        "cleanup_results": [],
    }
    wf_file = tmp_path / "wf.json"
    wf_file.write_text(json.dumps(wf_data), encoding="utf-8")

    res = runner.invoke(app, ["diagnose-silent", "--workflow", str(wf_file)])
    assert res.exit_code == EXIT_OK
    assert "SILENT FAILURE DIAGNOSTIC REPORT" in res.output


def test_latency_drift_cli_with_db_run_ids(tmp_path: Path) -> None:
    """Test latency-drift looking up baseline and current runs by ID from SQLite DB."""
    db_file = tmp_path / "drift.db"
    db_manager = DatabaseManager(db_file)
    repo = RunRepository(db_manager)

    now = datetime.now(UTC)
    cases_base = [
        CaseResult(
            test_name=f"case_b_{i}",
            module="items",
            tags=[],
            status=CaseStatus.PASSED,
            verified=True,
            method="GET",
            url="http://localhost:8000/items",
            status_code=200,
            elapsed_ms=50.0,
            request_headers={},
            response_headers={},
            assertion_results=[],
            extracted_variables={},
            extraction_errors=[],
        )
        for i in range(6)
    ]
    run_base = RunResult(
        run_id="run-base-db",
        environment_name="staging",
        started_at=now,
        finished_at=now,
        duration_ms=300.0,
        total_tests=6,
        passed_tests=6,
        failed_tests=0,
        error_tests=0,
        skipped_tests=0,
        results=cases_base,
    )
    repo.save_run(run_base)

    cases_curr = [
        CaseResult(
            test_name=f"case_c_{i}",
            module="items",
            tags=[],
            status=CaseStatus.PASSED,
            verified=True,
            method="GET",
            url="http://localhost:8000/items",
            status_code=200,
            elapsed_ms=120.0,
            request_headers={},
            response_headers={},
            assertion_results=[],
            extracted_variables={},
            extraction_errors=[],
        )
        for i in range(6)
    ]
    run_curr = RunResult(
        run_id="run-curr-db",
        environment_name="staging",
        started_at=now,
        finished_at=now,
        duration_ms=720.0,
        total_tests=6,
        passed_tests=6,
        failed_tests=0,
        error_tests=0,
        skipped_tests=0,
        results=cases_curr,
    )
    repo.save_run(run_curr)

    res = runner.invoke(
        app,
        [
            "latency-drift",
            "--db",
            str(db_file),
            "--baseline",
            "run-base-db",
            "--current",
            "run-curr-db",
        ],
    )
    assert res.exit_code == EXIT_OK
    assert "REGRESSION_DETECTED" in res.output


def test_latency_drift_cli_unresolvable_run_id(tmp_path: Path) -> None:
    """Test latency-drift with nonexistent ID exits with tool error."""
    db_file = tmp_path / "empty.db"
    db_manager = DatabaseManager(db_file)
    RunRepository(db_manager)

    res = runner.invoke(
        app,
        [
            "latency-drift",
            "--db",
            str(db_file),
            "--baseline",
            "missing-b",
            "--current",
            "missing-c",
        ],
    )
    assert res.exit_code == EXIT_TOOL_ERROR
    assert "Could not resolve test run" in res.output


def test_latency_drift_cli_export_yaml(tmp_path: Path) -> None:
    """Test latency-drift export with format=yaml."""
    base_file = tmp_path / "base.json"
    base_file.write_text(
        json.dumps(_create_sample_run("b", [50.0] * 6)), encoding="utf-8"
    )

    curr_file = tmp_path / "curr.json"
    curr_file.write_text(
        json.dumps(_create_sample_run("c", [52.0] * 6)), encoding="utf-8"
    )

    out_yaml = tmp_path / "drift.yaml"
    res = runner.invoke(
        app,
        [
            "latency-drift",
            "--baseline",
            str(base_file),
            "--current",
            str(curr_file),
            "--output",
            str(out_yaml),
            "--format",
            "yaml",
        ],
    )
    assert res.exit_code == EXIT_OK
    assert out_yaml.is_file()
    parsed = yaml.safe_load(out_yaml.read_text(encoding="utf-8"))
    assert "summary_metrics" in parsed
