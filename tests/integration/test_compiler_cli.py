"""Integration tests for ApiScope CLI compile-plan command (Task 19).

Verifies end-to-end command-line compilation of Task 18 TestPlans into
native executable YAML collections, detailed compilation reports,
oracle gating, fixture reporting, and real StockPile acceptance checks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from apiscope.cli.main import EXIT_OK, EXIT_TOOL_ERROR, app
from apiscope.definitions.collection import load_collection
from apiscope.definitions.models import DefinitionStatus
from apiscope.planning.models import (
    RiskClassification,
    ScenarioCategory,
    ScenarioOrigin,
    ScenarioPriority,
    ScenarioStep,
    TestPlan,
    TestScenario,
)

runner = CliRunner()


@pytest.fixture
def temp_openapi_file(tmp_path: Path) -> Path:
    """Create a temporary valid OpenAPI specification file."""
    spec_path = tmp_path / "openapi.yaml"
    spec_data = {
        "openapi": "3.0.3",
        "info": {
            "title": "Warehouse API",
            "version": "1.0.0",
            "description": "Warehouse stock and product service",
        },
        "servers": [{"url": "http://localhost:8080"}],
        "paths": {
            "/api/products": {
                "get": {
                    "tags": ["products"],
                    "summary": "List products",
                    "responses": {
                        "200": {
                            "description": "Products list",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {"id": {"type": "integer"}},
                                        },
                                    }
                                }
                            },
                        }
                    },
                },
                "post": {
                    "tags": ["products"],
                    "summary": "Create product",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Product created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "integer"}},
                                    }
                                }
                            },
                        }
                    },
                },
            },
            "/api/products/{id}": {
                "get": {
                    "tags": ["products"],
                    "summary": "Get product",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Product details",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "integer"}},
                                    }
                                }
                            },
                        },
                        "404": {"description": "Product not found"},
                    },
                }
            },
        },
    }
    spec_path.write_text(yaml.safe_dump(spec_data), encoding="utf-8")
    return spec_path


@pytest.fixture
def temp_test_plan(tmp_path: Path, temp_openapi_file: Path) -> Path:
    """Create a temporary valid TestPlan YAML file."""
    plan_path = tmp_path / "test_plan.yaml"
    scn_list = [
        TestScenario(
            scenario_id="SCN-PROD-001",
            title="List Products Positive",
            description="Verify GET /api/products returns 200",
            category=ScenarioCategory.CONTRACT_POSITIVE,
            origin=ScenarioOrigin.DETERMINISTIC,
            priority=ScenarioPriority.HIGH,
            risk_classification=RiskClassification.LOW,
            approval_status=DefinitionStatus.APPROVED,
            steps=[
                ScenarioStep(
                    step_number=1,
                    method="GET",
                    path="/api/products",
                    description="List products",
                    expected_status=["200"],
                )
            ],
        ),
        TestScenario(
            scenario_id="SCN-PROD-002",
            title="Get Product Missing Fixture",
            description="Parameterized path lacking resource ID",
            category=ScenarioCategory.CONTRACT_POSITIVE,
            origin=ScenarioOrigin.DETERMINISTIC,
            priority=ScenarioPriority.HIGH,
            risk_classification=RiskClassification.LOW,
            approval_status=DefinitionStatus.APPROVED,
            missing_dependencies=[
                "needs_existing_resource_id for path '/api/products/{id}'"
            ],
            steps=[
                ScenarioStep(
                    step_number=1,
                    method="GET",
                    path="/api/products/{id}",
                    description="Get product by ID",
                    expected_status=["200"],
                )
            ],
        ),
        TestScenario(
            scenario_id="SCN-PROD-003",
            title="Probe Nonexistent Product",
            description="Probes ID 999999 expecting 404",
            category=ScenarioCategory.RESOURCE_EXISTENCE,
            origin=ScenarioOrigin.DETERMINISTIC,
            priority=ScenarioPriority.LOW,
            risk_classification=RiskClassification.LOW,
            approval_status=DefinitionStatus.APPROVED,
            steps=[
                ScenarioStep(
                    step_number=1,
                    method="GET",
                    path="/api/products/999999",
                    description="Probe nonexistent ID",
                    expected_status=["404"],
                )
            ],
        ),
    ]
    plan = TestPlan(
        plan_id="PLAN-CLI-001",
        title="Warehouse CLI Test Plan",
        version="1.0.0",
        contract_ref=str(temp_openapi_file),
        scenarios=scn_list,
    )
    plan_path.write_text(yaml.safe_dump(plan.to_dict()), encoding="utf-8")
    return plan_path


# =====================================================================
# CLI TESTS
# =====================================================================


def test_cli_compile_plan_help() -> None:
    """Verify compile-plan --help displays expected options and documentation."""
    result = runner.invoke(app, ["compile-plan", "--help"])
    assert result.exit_code == EXIT_OK
    assert "compile-plan" in result.output
    assert "--plan" in result.output
    assert "--spec" in result.output
    assert "--contract" in result.output
    assert "--output" in result.output
    assert "--report" in result.output
    assert "--base-url-var" in result.output
    assert "--include-proposed" in result.output


def test_cli_compile_plan_missing_plan_fails() -> None:
    """Verify command fails if --plan argument is omitted."""
    result = runner.invoke(app, ["compile-plan"])
    assert result.exit_code != EXIT_OK


def test_cli_compile_plan_nonexistent_plan_fails(temp_openapi_file: Path) -> None:
    """Verify command fails cleanly if specified plan file does not exist."""
    result = runner.invoke(
        app,
        [
            "compile-plan",
            "--plan",
            "nonexistent_plan.yaml",
            "--spec",
            str(temp_openapi_file),
        ],
    )
    assert result.exit_code == EXIT_TOOL_ERROR
    assert "Error:" in result.output


def test_cli_compile_plan_missing_spec_and_contract_fails(tmp_path: Path) -> None:
    """Verify compilation fails closed if neither spec nor contract is available."""
    bare_plan_path = tmp_path / "bare_plan.yaml"
    plan = TestPlan(
        plan_id="PLAN-BARE",
        title="Bare Plan",
        version="1.0.0",
        contract_ref=None,
        scenarios=[],
    )
    bare_plan_path.write_text(yaml.safe_dump(plan.to_dict()), encoding="utf-8")

    result = runner.invoke(app, ["compile-plan", "--plan", str(bare_plan_path)])
    assert result.exit_code == EXIT_TOOL_ERROR
    assert "valid OpenAPI specification" in result.output


def test_cli_compile_plan_successful_suite_and_report_generation(
    temp_test_plan: Path,
    temp_openapi_file: Path,
    tmp_path: Path,
) -> None:
    """Verify end-to-end plan compilation and file outputs."""
    out_suite = tmp_path / "out_suite.yaml"
    out_report = tmp_path / "out_report.json"

    result = runner.invoke(
        app,
        [
            "compile-plan",
            "--plan",
            str(temp_test_plan),
            "--spec",
            str(temp_openapi_file),
            "--output",
            str(out_suite),
            "--report",
            str(out_report),
        ],
    )
    assert result.exit_code == EXIT_OK
    assert (
        "=== ApiScope Test Plan Compilation: Warehouse CLI Test Plan ==="
        in result.output
    )
    assert "[COMPILED]     : 1" in result.output
    assert "[NEEDS_REVIEW] : 1" in result.output
    assert "[NEEDS_FIXTURE]: 1" in result.output
    assert "[BLOCKED]      : 0" in result.output
    assert "Total Executable Tests Synthesized: 1" in result.output
    assert "Unresolved Fixtures" in result.output

    # Verify native suite
    assert out_suite.is_file()
    col = load_collection(out_suite)
    assert len(col.tests) == 1
    assert col.tests[0].name == "[SCN-PROD-001] GET /api/products"

    # Verify JSON report
    assert out_report.is_file()
    rep_data = json.loads(out_report.read_text(encoding="utf-8"))
    assert rep_data["plan_id"] == "PLAN-CLI-001"
    assert rep_data["summary"]["compiled_count"] == 1
    assert rep_data["summary"]["needs_review_count"] == 1
    assert rep_data["summary"]["needs_fixture_count"] == 1


def test_cli_compile_plan_stockpile_real_fixtures(tmp_path: Path) -> None:
    """Verify CLI compile-plan against real StockPile Task 18 artifacts."""
    plan_path = Path("stockpile_tests/stockpile_test_plan.yaml")
    spec_path = Path("stockpile_tests/stockpile_openapi.yaml")

    if not plan_path.is_file() or not spec_path.is_file():
        pytest.skip("StockPile test plan or spec not found on disk.")

    out_suite = tmp_path / "stockpile_compiled_test.yaml"
    out_report = tmp_path / "stockpile_compilation_report_test.json"

    result = runner.invoke(
        app,
        [
            "compile-plan",
            "--plan",
            str(plan_path),
            "--spec",
            str(spec_path),
            "--output",
            str(out_suite),
            "--report",
            str(out_report),
        ],
    )

    assert result.exit_code == EXIT_OK
    assert "Total Scenarios Evaluated: 205" in result.output
    assert "[COMPILED]     : 66" in result.output
    assert "[NEEDS_REVIEW] : 89" in result.output
    assert "[NEEDS_FIXTURE]: 50" in result.output
    assert "[BLOCKED]      : 0" in result.output
    assert "Total Executable Tests Synthesized: 66" in result.output

    assert out_suite.is_file()
    col = load_collection(out_suite)
    assert len(col.tests) == 66

    assert out_report.is_file()
    rep_data = json.loads(out_report.read_text(encoding="utf-8"))
    assert rep_data["summary"]["total_scenarios"] == 205
    assert rep_data["summary"]["compiled_count"] == 66
    assert rep_data["summary"]["needs_review_count"] == 89
    assert rep_data["summary"]["needs_fixture_count"] == 50
    assert rep_data["summary"]["blocked_count"] == 0
