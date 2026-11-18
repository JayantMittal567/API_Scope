"""Integration tests for Typer CLI benchmark command (Task 28)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from apiscope.cli.main import app

runner = CliRunner()


def test_cli_benchmark_console_output(tmp_path: Path) -> None:
    """Verify CLI benchmark command prints formatted console report."""
    result = runner.invoke(
        app,
        ["benchmark", "--output-dir", str(tmp_path), "--mode", "clean"],
    )
    assert result.exit_code == 0
    assert (
        "APISCOPE 1.0 — DEFECT-DETECTION BENCHMARK & ACCURACY REPORT" in result.output
    )
    assert "Precision:" in result.output
    assert "Confusion Matrix:" in result.output
    assert "Authentic StockPile Evidence Verification:" in result.output


def test_cli_benchmark_json_output(tmp_path: Path) -> None:
    """Verify CLI benchmark command supports --format json."""
    result = runner.invoke(
        app,
        [
            "benchmark",
            "--output-dir",
            str(tmp_path),
            "--mode",
            "clean",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "manifest_version" in data
    assert "metrics" in data
    assert data["metrics"]["total_cases"] == 16


def test_cli_benchmark_yaml_output(tmp_path: Path) -> None:
    """Verify CLI benchmark command supports --format yaml."""
    result = runner.invoke(
        app,
        [
            "benchmark",
            "--output-dir",
            str(tmp_path),
            "--mode",
            "clean",
            "--format",
            "yaml",
        ],
    )
    assert result.exit_code == 0
    assert (
        "manifest_version: '1.0.0'" in result.output
        or "manifest_version: 1.0.0" in result.output
    )
    assert "metrics:" in result.output
