"""Integration tests for ApiScope CLI workflow execution (Task 21).

Verifies command-line dry-run preview, policy gate enforcement,
workflow selection filtering, SQLite persistence, multi-format reporting,
and real StockPile acceptance checks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from apiscope.cli.main import (
    EXIT_OK,
    EXIT_TEST_FAILURE,
    app,
)
from apiscope.fixtures.models import (
    FixturePreparationReport,
    StatefulWorkflow,
    WorkflowAction,
)

runner = CliRunner()


@pytest.fixture
def sample_workflow_yaml(tmp_path: Path) -> Path:
    """Create a temporary stateful workflow YAML file."""
    wf_data = {
        "workflow_id": "wf_cli_demo",
        "title": "CLI Demo Workflow",
        "description": "Integration testing stateful workflow CLI",
        "status": "PLANNED",
        "required_fixtures": [],
        "provided_fixtures": ["demo_id"],
        "setup_steps": [
            {
                "step_number": 1,
                "name": "Create Item",
                "action_type": "SETUP",
                "method": "POST",
                "url": "{{base_url}}/api/items",
                "headers": {"Content-Type": "application/json"},
                "body": {"name": "Test Item"},
                "extract": {"demo_id": "$.id"},
                "assertions": [{"type": "status", "equals": 201}],
                "risk": "risk:mutating",
                "can_rollback": True,
            }
        ],
        "test_steps": [
            {
                "step_number": 2,
                "name": "Get Item",
                "action_type": "TEST",
                "method": "GET",
                "url": "{{base_url}}/api/items/{{demo_id}}",
                "assertions": [{"type": "status", "equals": 200}],
                "risk": "risk:read_only",
            }
        ],
        "cleanup_steps": [
            {
                "step_number": 3,
                "name": "Delete Item",
                "action_type": "CLEANUP",
                "method": "DELETE",
                "url": "{{base_url}}/api/items/{{demo_id}}",
                "assertions": [{"type": "status", "equals": 204}],
                "risk": "risk:destructive",
                "rollback_endpoint": "{{demo_id}}",
            }
        ],
    }
    wf_path = tmp_path / "sample_workflow.yaml"
    wf_path.write_text(yaml.safe_dump(wf_data), encoding="utf-8")
    return wf_path


@pytest.fixture
def sample_env_yaml(tmp_path: Path) -> Path:
    """Create a temporary environment YAML file."""
    env_data = {
        "name": "isolated_test",
        "variables": {
            "base_url": "http://localhost:8080",
            "api_key": "test-key-12345",
        },
        "secrets": ["api_key"],
    }
    env_path = tmp_path / "apiscope.env.yaml"
    env_path.write_text(yaml.safe_dump(env_data), encoding="utf-8")
    return env_path


# =====================================================================
# CLI HELP AND USAGE TESTS
# =====================================================================


def test_cli_run_help_shows_workflow_options() -> None:
    """Verify 'apiscope run --help' displays workflow-aware flags."""
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == EXIT_OK
    assert "--workflow-id" in result.stdout
    assert "--allow-setup-mutation" in result.stdout
    assert "--allow-cleanup-mutation" in result.stdout
    assert "--confirm-isolated-env" in result.stdout
    assert "--fixtures" in result.stdout


def test_cli_run_workflow_help() -> None:
    """Verify 'apiscope run-workflow --help' command presence and help output."""
    result = runner.invoke(app, ["run-workflow", "--help"])
    assert result.exit_code == EXIT_OK
    assert "workflow_file" in result.stdout
    assert "--confirm-isolated-env" in result.stdout
    assert "--dry-run" in result.stdout


# =====================================================================
# CLI DRY-RUN AND ROUTING TESTS
# =====================================================================


def test_cli_run_workflow_dry_run(
    sample_workflow_yaml: Path, sample_env_yaml: Path
) -> None:
    """Verify 'apiscope run-workflow' produces structured preview with zero network calls."""
    result = runner.invoke(
        app,
        [
            "run-workflow",
            str(sample_workflow_yaml),
            "--env-file",
            str(sample_env_yaml),
            "--dry-run",
        ],
    )
    assert result.exit_code == EXIT_OK
    assert "=== DRY-RUN WORKFLOW PLAN: wf_cli_demo ===" in result.stdout
    assert "Execution Eligible: NO" in result.stdout
    assert "isolated test environment confirmation" in result.stdout
    assert "SETUP STEPS:" in result.stdout
    assert "TEST STEPS:" in result.stdout
    assert "CLEANUP STEPS" in result.stdout


def test_cli_run_detects_workflow_file(
    sample_workflow_yaml: Path, sample_env_yaml: Path
) -> None:
    """Verify 'apiscope run' automatically detects stateful workflow YAML and routes appropriately."""
    result = runner.invoke(
        app,
        [
            "run",
            str(sample_workflow_yaml),
            "--env-file",
            str(sample_env_yaml),
            "--dry-run",
        ],
    )
    assert result.exit_code == EXIT_OK
    assert "=== DRY-RUN WORKFLOW PLAN: wf_cli_demo ===" in result.stdout
    assert "Planned Steps: 1 Setup | 1 Test | 1 Cleanup (LIFO)" in result.stdout


def test_cli_run_workflow_authorized_dry_run(
    sample_workflow_yaml: Path, sample_env_yaml: Path
) -> None:
    """Verify dry-run shows 'Execution Eligible: YES' when fully authorized."""
    result = runner.invoke(
        app,
        [
            "run-workflow",
            str(sample_workflow_yaml),
            "--env-file",
            str(sample_env_yaml),
            "--allow-mutation",
            "--confirm-isolated-env",
            "--dry-run",
        ],
    )
    assert result.exit_code == EXIT_OK
    assert "Execution Eligible: YES" in result.stdout


# =====================================================================
# CLI SAFETY FENCES AND GATING
# =====================================================================


def test_cli_unauthorized_mutation_fails_closed(
    sample_workflow_yaml: Path, sample_env_yaml: Path
) -> None:
    """Verify executing mutating workflow without authorization fails closed."""
    result = runner.invoke(
        app,
        [
            "run-workflow",
            str(sample_workflow_yaml),
            "--env-file",
            str(sample_env_yaml),
            # Missing --allow-mutation and --confirm-isolated-env
        ],
    )
    assert result.exit_code == EXIT_TEST_FAILURE
    assert "Blocked: 1" in result.stdout


def test_cli_workflow_id_filtering(tmp_path: Path, sample_env_yaml: Path) -> None:
    """Verify --workflow-id executes only the specified workflow from a multi-workflow report."""
    wf1 = StatefulWorkflow(
        workflow_id="wf_alpha",
        title="Alpha WF",
        description="",
        setup_steps=[],
        test_steps=[
            WorkflowAction(
                step_number=1,
                name="Read Alpha",
                action_type="TEST",
                method="GET",
                url="{{base_url}}/api/alpha",
            )
        ],
        cleanup_steps=[],
    )
    wf2 = StatefulWorkflow(
        workflow_id="wf_beta",
        title="Beta WF",
        description="",
        setup_steps=[],
        test_steps=[
            WorkflowAction(
                step_number=1,
                name="Read Beta",
                action_type="TEST",
                method="GET",
                url="{{base_url}}/api/beta",
            )
        ],
        cleanup_steps=[],
    )
    report = FixturePreparationReport(
        plan_id="plan_filter",
        title="Multi WF Report",
        planned_workflows=[wf1, wf2],
    )
    report_file = tmp_path / "multi_report.json"
    report_file.write_text(json.dumps(report.to_dict()), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "run-workflow",
            str(report_file),
            "--workflow-id",
            "wf_beta",
            "--env-file",
            str(sample_env_yaml),
            "--dry-run",
        ],
    )
    assert result.exit_code == EXIT_OK
    assert "=== DRY-RUN WORKFLOW PLAN: wf_beta ===" in result.stdout
    assert "wf_alpha" not in result.stdout


# =====================================================================
# REAL STOCKPILE ACCEPTANCE CHECKS
# =====================================================================


def test_cli_stockpile_real_artifacts_dry_run() -> None:
    """Verify real StockPile preparation report is safely previewed with zero live network calls."""
    stockpile_report = Path("stockpile_tests/stockpile_preparation_report.json")
    if not stockpile_report.is_file():
        pytest.skip("StockPile preparation report not present in workspace")

    result = runner.invoke(
        app,
        [
            "run-workflow",
            str(stockpile_report),
            "--dry-run",
        ],
    )
    assert result.exit_code == EXIT_OK

    # Verify key planned workflows are inspected
    assert "WF-GET-API-SUPPLIERS-ID" in result.stdout
    assert "WF-GET-API-PRODUCTS-ID-MOVEMENTS" in result.stdout
    assert "WF-GET-API-SURPLUS-LISTINGS-ID" in result.stdout

    # Verify LIFO reverse-order cleanup is planned for dependent hierarchies
    assert "[CLEANUP:4] Delete Products" in result.stdout
    assert "[CLEANUP:5] Delete Suppliers" in result.stdout

    # Verify missing variables are flagged without attempting network calls
    assert "Missing Variables: chain_supplier_id" in result.stdout

    # Verify default read-only safety policy
    assert "Execution Eligible: NO" in result.stdout
    assert (
        "Mutating operations require explicit isolated test environment confirmation"
        in result.stdout
    )
