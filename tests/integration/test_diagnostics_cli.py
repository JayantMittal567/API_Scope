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
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from apiscope.cli.main import (
    EXIT_OK,
    app,
)

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
