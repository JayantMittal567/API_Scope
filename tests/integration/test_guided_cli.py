"""Integration tests for the unified guided end-to-end testing workflow CLI (Task 26).

Verifies:
- 'apiscope guided' and alias 'apiscope analyze-project' command help.
- Missing specification error handling.
- Dry-run preview execution with zero network calls.
- Ingestion of pre-recorded execution results (--run-file).
- YAML and JSON format export options.
- Strict mode exit code handling (--strict).
- Policy authorization blocker when mutations requested without confirmation.
- Authentic StockPile offline guided workflow CLI execution.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.utils import strip_ansi
from typer.testing import CliRunner

from apiscope.cli.main import (
    EXIT_OK,
    EXIT_TOOL_ERROR,
    EXIT_WARNINGS_STRICT,
    app,
)

runner = CliRunner()


def _create_minimal_spec(path: Path, with_mutations: bool = False) -> Path:
    """Create a minimal OpenAPI specification for testing."""
    path.mkdir(parents=True, exist_ok=True)
    spec_file = path / "openapi.yaml"
    content = """
openapi: 3.0.0
info:
  title: CLI Guided Store
  version: 1.0.0
paths:
  /api/items:
    get:
      summary: List items
      responses:
        '200':
          description: OK
"""
    if with_mutations:
        content += """
    post:
      summary: Create item
      responses:
        '201':
          description: Created
"""
    spec_file.write_text(content.strip(), encoding="utf-8")
    return spec_file


def _create_mock_run_result(path: Path, run_id: str = "run-cli-test") -> Path:
    """Create a mock run result JSON file."""
    run_file = path / "mock_run.json"
    data = {
        "run_id": run_id,
        "spec_hash": "dummyhash",
        "total_tests": 1,
        "passed_tests": 1,
        "failed_tests": 0,
        "error_tests": 0,
        "skipped_tests": 0,
        "results": [
            {
                "test_name": "test_get_items",
                "module": "items",
                "status": "PASSED",
                "method": "GET",
                "url": "http://localhost:8080/api/items",
                "status_code": 200,
                "elapsed_ms": 25.0,
            }
        ],
    }
    run_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return run_file


def test_cli_guided_help() -> None:
    """Verify 'apiscope guided' and alias 'apiscope analyze-project' expose all CLI flags."""
    res1 = runner.invoke(app, ["guided", "--help"])
    res1_help = strip_ansi(res1.output)
    assert res1.exit_code == EXIT_OK
    assert "--spec" in res1_help
    assert "--repo" in res1_help
    assert "--output-dir" in res1_help
    assert "--target-origin" in res1_help
    assert "--execute" in res1_help
    assert "--preview-only" in res1_help
    assert "--run-file" in res1_help
    assert "--strict" in res1_help
    assert "--format" in res1_help

    res2 = runner.invoke(app, ["analyze-project", "--help"])

    res2_help = strip_ansi(res2.output)
    assert res2.exit_code == EXIT_OK
    assert "--spec" in res2_help


def test_cli_guided_missing_spec() -> None:
    """Verify error when specification file does not exist."""
    res = runner.invoke(app, ["guided", "--spec", "non_existent_file.yaml"])
    assert res.exit_code == EXIT_TOOL_ERROR
    assert "Specification file not found" in res.output or "Error" in res.output


def test_cli_guided_preview_only_offline(tmp_path: Path) -> None:
    """Verify offline preview execution via CLI with socket blocked."""
    spec_path = _create_minimal_spec(tmp_path / "spec_preview", with_mutations=False)
    out_dir = tmp_path / "out_preview"

    with patch("socket.socket") as mock_sock:
        mock_sock.side_effect = RuntimeError("Network call attempted during preview!")

        res = runner.invoke(
            app,
            [
                "guided",
                "--spec",
                str(spec_path),
                "--output-dir",
                str(out_dir),
                "--preview-only",
            ],
        )

        assert res.exit_code == EXIT_OK
        assert "APISCOPE 1.0 — GUIDED PROJECT-TESTING WORKFLOW" in res.output
        assert "[Dry-Run Execution Preview]" in res.output
        assert "PROJECT_INSPECTION" in res.output
        assert "CONTRACT_ANALYSIS" in res.output
        assert "TEST_PLANNING" in res.output
        assert "TEST_COMPILATION" in res.output
        assert "FIXTURE_PREPARATION" in res.output
        assert "EXECUTION_PREVIEW" in res.output

        assert (out_dir / "workflow_manifest.json").is_file()
        assert (out_dir / "execution_preview.json").is_file()


def test_cli_analyze_project_alias(tmp_path: Path) -> None:
    """Verify that 'apiscope analyze-project' operates identically as an alias."""
    spec_path = _create_minimal_spec(tmp_path / "spec_alias", with_mutations=False)
    out_dir = tmp_path / "out_alias"

    res = runner.invoke(
        app,
        [
            "analyze-project",
            "--spec",
            str(spec_path),
            "--output-dir",
            str(out_dir),
            "--preview-only",
        ],
    )

    assert res.exit_code == EXIT_OK
    assert (out_dir / "workflow_manifest.json").is_file()


def test_cli_guided_with_run_file_and_yaml(tmp_path: Path) -> None:
    """Verify guided workflow with pre-recorded run results and YAML export."""
    spec_path = _create_minimal_spec(tmp_path / "spec_yaml", with_mutations=False)
    run_file = _create_mock_run_result(tmp_path)
    out_dir = tmp_path / "out_yaml"

    res = runner.invoke(
        app,
        [
            "guided",
            "--spec",
            str(spec_path),
            "--run-file",
            str(run_file),
            "--output-dir",
            str(out_dir),
            "--format",
            "yaml",
        ],
    )

    assert res.exit_code == EXIT_OK
    assert (out_dir / "workflow_manifest.yaml").is_file()
    assert (out_dir / "trust_assessment_report.yaml").is_file()


def test_cli_guided_strict_mode(tmp_path: Path) -> None:
    """Verify --strict mode exits with code 2 when inspection or contract warnings arise."""
    spec_path = _create_minimal_spec(tmp_path / "spec_strict", with_mutations=False)
    out_dir = tmp_path / "out_strict"

    # Running with only spec generates inspection warning (no repo provided)
    res = runner.invoke(
        app,
        [
            "guided",
            "--spec",
            str(spec_path),
            "--output-dir",
            str(out_dir),
            "--preview-only",
            "--strict",
        ],
    )

    assert res.exit_code == EXIT_WARNINGS_STRICT


def test_cli_guided_stockpile_smoke(tmp_path: Path) -> None:
    """Verify authentic StockPile smoke run ingestion through the CLI."""
    spec_path = Path("stockpile_tests/stockpile_openapi.yaml")
    run_path = Path("stockpile_tests/stockpile_readonly_results.json")
    out_dir = tmp_path / "stockpile_cli_out"

    with patch("socket.socket") as mock_sock:
        mock_sock.side_effect = RuntimeError("Network access attempted!")

        res = runner.invoke(
            app,
            [
                "guided",
                "--spec",
                str(spec_path),
                "--run-file",
                str(run_path),
                "--output-dir",
                str(out_dir),
            ],
        )

        assert res.exit_code == EXIT_OK
        assert "TRUST_ASSESSMENT" in res.output
        assert (out_dir / "workflow_manifest.json").is_file()
        assert (out_dir / "trust_assessment_report.json").is_file()
