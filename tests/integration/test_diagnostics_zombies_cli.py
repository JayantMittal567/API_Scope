"""Integration tests for diagnose-zombies and enhanced zombie CLI commands (Task 24).

Verifies:
- diagnose-zombies command help and options.
- Specification and run report parsing.
- JSON and YAML file export.
- SQLite database run resolution.
- Source repository reconciliation flag (--repo).
- Strict exit code enforcement.
- Backward compatibility of the legacy 'zombie' CLI command.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import yaml
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


def _create_sample_spec_file(path: Path) -> Path:
    """Create sample OpenAPI specification file."""
    spec_content = """
openapi: 3.0.0
info:
  title: Sample Store API
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
  /items/{id}:
    get:
      summary: Get item
      parameters:
        - name: id
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: OK
"""
    path.write_text(spec_content.strip(), encoding="utf-8")
    return path


def _create_sample_run_file(path: Path) -> Path:
    """Create sample test run JSON file."""
    run_data = {
        "run_id": "run-test-1",
        "total_tests": 2,
        "passed_tests": 2,
        "failed_tests": 0,
        "error_tests": 0,
        "skipped_tests": 0,
        "results": [
            {
                "test_name": "test_list_items",
                "module": "items",
                "method": "GET",
                "url": "http://localhost:8080/items",
                "status": "PASSED",
                "status_code": 200,
                "elapsed_ms": 15.0,
            },
            {
                "test_name": "test_get_item_42",
                "module": "items",
                "method": "GET",
                "url": "http://localhost:8080/items/42",
                "status": "PASSED",
                "status_code": 200,
                "elapsed_ms": 20.0,
            },
        ],
    }
    path.write_text(json.dumps(run_data), encoding="utf-8")
    return path


def test_diagnose_zombies_cli_help() -> None:
    """Verify diagnose-zombies --help displays options."""
    res = runner.invoke(app, ["diagnose-zombies", "--help"])
    assert res.exit_code == EXIT_OK
    assert "--spec" in res.output
    assert "--repo" in res.output
    assert "--run-file" in res.output
    assert "--plan" in res.output
    assert "--output" in res.output
    assert "--strict" in res.output


def test_diagnose_zombies_cli_with_run_file(tmp_path: Path) -> None:
    """Verify diagnose-zombies with spec and run file computes coverage metrics."""
    spec_path = _create_sample_spec_file(tmp_path / "openapi.yaml")
    run_path = _create_sample_run_file(tmp_path / "run.json")

    res = runner.invoke(
        app,
        [
            "diagnose-zombies",
            "--spec",
            str(spec_path),
            "--run-file",
            str(run_path),
        ],
    )
    assert res.exit_code == EXIT_OK
    assert "API ENDPOINT INVENTORY & COVERAGE INTELLIGENCE" in res.output
    assert "Documented Operations:   3" in res.output
    assert "Attempted Operations:    2" in res.output
    assert "Untested Operations:     1" in res.output
    assert "Read-Only Operations:    2 / 2 (100.0%)" in res.output
    assert "Mutation Operations:     0 / 1 (0.0%)" in res.output


def test_diagnose_zombies_cli_export_json_and_yaml(tmp_path: Path) -> None:
    """Verify export to structured JSON and YAML files."""
    spec_path = _create_sample_spec_file(tmp_path / "openapi.yaml")
    run_path = _create_sample_run_file(tmp_path / "run.json")

    # 1. JSON export
    out_json = tmp_path / "report.json"
    res_json = runner.invoke(
        app,
        [
            "diagnose-zombies",
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
    assert res_json.exit_code == EXIT_OK
    assert out_json.is_file()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert "coverage_summary" in data
    assert data["coverage_summary"]["total_documented_operations"] == 3

    # 2. YAML export
    out_yaml = tmp_path / "report.yaml"
    res_yaml = runner.invoke(
        app,
        [
            "diagnose-zombies",
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
    assert res_yaml.exit_code == EXIT_OK
    assert out_yaml.is_file()
    yaml_data = yaml.safe_load(out_yaml.read_text(encoding="utf-8"))
    assert "coverage_summary" in yaml_data


def test_diagnose_zombies_cli_with_db_run_id(tmp_path: Path) -> None:
    """Verify loading test run by ID from SQLite database."""
    spec_path = _create_sample_spec_file(tmp_path / "openapi.yaml")
    db_file = tmp_path / "test.db"
    db_mgr = DatabaseManager(db_file)
    repo = RunRepository(db_mgr)

    now = datetime.now(UTC)
    case = CaseResult(
        test_name="case_1",
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
        run_id="run-sqlite-001",
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

    res = runner.invoke(
        app,
        [
            "diagnose-zombies",
            "--spec",
            str(spec_path),
            "--db",
            str(db_file),
            "--run-id",
            "run-sqlite-001",
        ],
    )
    assert res.exit_code == EXIT_OK
    assert "Attempted Operations:    1" in res.output


def test_diagnose_zombies_cli_nonexistent_run_id(tmp_path: Path) -> None:
    """Verify error exit when run ID does not exist in SQLite database."""
    db_file = tmp_path / "empty.db"
    db_mgr = DatabaseManager(db_file)
    RunRepository(db_mgr)

    res = runner.invoke(
        app,
        [
            "diagnose-zombies",
            "--db",
            str(db_file),
            "--run-id",
            "missing-run-id",
        ],
    )
    assert res.exit_code == EXIT_TOOL_ERROR
    assert "Run 'missing-run-id' not found in SQLite database" in res.output


def test_diagnose_zombies_cli_with_repo(tmp_path: Path) -> None:
    """Verify --repo flag triggers source scanner and reconciliation."""
    spec_path = _create_sample_spec_file(tmp_path / "openapi.yaml")

    # Create dummy source repo with controller
    repo_dir = tmp_path / "src_repo"
    repo_dir.mkdir()
    ctrl = repo_dir / "item_controller.py"
    ctrl.write_text(
        "from fastapi import APIRouter\n\n"
        "router = APIRouter()\n\n"
        "@router.get('/items')\n"
        "def get_items():\n"
        "    return []\n\n"
        "@router.post('/items/bulk-delete')\n"
        "def bulk_delete():\n"
        "    return {}\n",
        encoding="utf-8",
    )

    res = runner.invoke(
        app,
        [
            "diagnose-zombies",
            "--spec",
            str(spec_path),
            "--repo",
            str(repo_dir),
        ],
    )
    assert res.exit_code == EXIT_OK
    assert "SOURCE_CODE_REPOSITORY" in res.output
    # bulk-delete is source-only candidate
    assert "SOURCE_ONLY_CANDIDATE" in res.output


def test_diagnose_zombies_cli_strict_exit_code(tmp_path: Path) -> None:
    """Verify --strict exits with code 2 when runtime mismatch is detected."""
    spec_path = _create_sample_spec_file(tmp_path / "openapi.yaml")

    # Run with undocumented endpoint /debug/unexpected
    run_file = tmp_path / "undoc.json"
    run_data = {
        "run_id": "run-undoc",
        "total_tests": 1,
        "results": [
            {
                "test_name": "test_undoc",
                "module": "debug",
                "method": "GET",
                "url": "http://localhost:8080/debug/unexpected",
                "status": "PASSED",
                "status_code": 200,
            }
        ],
    }
    run_file.write_text(json.dumps(run_data), encoding="utf-8")

    res = runner.invoke(
        app,
        [
            "diagnose-zombies",
            "--spec",
            str(spec_path),
            "--run-file",
            str(run_file),
            "--strict",
        ],
    )
    assert res.exit_code == EXIT_WARNINGS_STRICT


def test_legacy_zombie_cli_with_repo_flag(tmp_path: Path) -> None:
    """Verify legacy zombie command functions normally and accepts --repo."""
    spec_path = _create_sample_spec_file(tmp_path / "openapi.yaml")
    log_file = tmp_path / "access.log"
    log_file.write_text(
        '127.0.0.1 - - [14/Nov/2026:10:00:00 +0000] "GET /items HTTP/1.1" 200 123\n',
        encoding="utf-8",
    )

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    res = runner.invoke(
        app,
        [
            "zombie",
            "--spec",
            str(spec_path),
            "--access-log",
            str(log_file),
            "--repo",
            str(repo_dir),
        ],
    )
    assert res.exit_code == EXIT_OK
    assert "Zombie API Analysis" in res.output
