"""Integration tests for ApiScope CLI prepare-tests command (Task 20).

Verifies end-to-end command-line preparation of tests with fixture resolution,
static fixture file loading, dependency ordering, stateful workflow planning,
LIFO cleanup sequencing, and real StockPile acceptance checks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from apiscope.cli.main import EXIT_OK, app
from apiscope.definitions.collection import load_collection
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
def sample_openapi_file(tmp_path: Path) -> Path:
    """Create a temporary valid OpenAPI spec file."""
    spec_path = tmp_path / "openapi.yaml"
    spec_data = {
        "openapi": "3.0.3",
        "info": {
            "title": "Warehouse Store API",
            "version": "1.0.0",
        },
        "paths": {
            "/api/suppliers": {
                "get": {
                    "summary": "List suppliers",
                    "responses": {"200": {"description": "OK"}},
                },
                "post": {
                    "summary": "Create supplier",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "email": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"201": {"description": "Created"}},
                },
            },
            "/api/suppliers/{id}": {
                "get": {
                    "summary": "Get supplier by ID",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                },
                "delete": {
                    "summary": "Delete supplier",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {"204": {"description": "No Content"}},
                },
            },
            "/api/products": {
                "get": {
                    "summary": "List products",
                    "responses": {"200": {"description": "OK"}},
                },
                "post": {
                    "summary": "Create product",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "supplierId": {"type": "integer"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"201": {"description": "Created"}},
                },
            },
            "/api/products/{id}": {
                "get": {
                    "summary": "Get product by ID",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                },
                "delete": {
                    "summary": "Delete product",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {"204": {"description": "No Content"}},
                },
            },
        },
    }
    spec_path.write_text(yaml.dump(spec_data), encoding="utf-8")
    return spec_path


@pytest.fixture
def sample_test_plan_file(tmp_path: Path) -> Path:
    """Create a temporary TestPlan YAML file."""
    plan_path = tmp_path / "test_plan.yaml"
    plan = TestPlan(
        plan_id="PLAN-CLI-01",
        title="Warehouse Test Plan",
        version="1.0.0",
        scenarios=[
            TestScenario(
                scenario_id="SCN-GET-SUPPLIERS",
                title="List Suppliers",
                description="Read suppliers collection",
                category=ScenarioCategory.CONTRACT_POSITIVE,
                origin=ScenarioOrigin.DETERMINISTIC,
                priority=ScenarioPriority.HIGH,
                risk_classification=RiskClassification.LOW,
                steps=[
                    ScenarioStep(
                        step_number=1,
                        method="GET",
                        path="/api/suppliers",
                        description="GET all",
                    )
                ],
            ),
            TestScenario(
                scenario_id="SCN-POST-PRODUCT-FK",
                title="Create Product with Dummy FK",
                description="Mutating POST with unverified FK",
                category=ScenarioCategory.CONTRACT_POSITIVE,
                origin=ScenarioOrigin.DETERMINISTIC,
                priority=ScenarioPriority.HIGH,
                risk_classification=RiskClassification.HIGH,
                steps=[
                    ScenarioStep(
                        step_number=1,
                        method="POST",
                        path="/api/products",
                        description="POST product",
                        request_template={"body": {"name": "Widget", "supplierId": 1}},
                    )
                ],
            ),
            TestScenario(
                scenario_id="SCN-GET-PRODUCT-ID",
                title="Get Product By ID",
                description="Read product with parameter",
                category=ScenarioCategory.RESOURCE_EXISTENCE,
                origin=ScenarioOrigin.DETERMINISTIC,
                priority=ScenarioPriority.MEDIUM,
                risk_classification=RiskClassification.LOW,
                steps=[
                    ScenarioStep(
                        step_number=1,
                        method="GET",
                        path="/api/products/{id}",
                        description="GET by ID",
                    )
                ],
            ),
        ],
    )
    plan_path.write_text(yaml.dump(plan.to_dict()), encoding="utf-8")
    return plan_path


# =====================================================================
# CLI TESTS: HELP & ARGUMENT VALIDATION
# =====================================================================


def test_cli_prepare_tests_help() -> None:
    """Verify prepare-tests --help displays all required options."""
    res = runner.invoke(app, ["prepare-tests", "--help"])
    assert res.exit_code == EXIT_OK
    assert "prepare-tests" in res.output
    assert "--plan" in res.output
    assert "--spec" in res.output
    assert "--contract" in res.output
    assert "--intelligence" in res.output
    assert "--fixtures" in res.output
    assert "--output" in res.output
    assert "--report" in res.output
    assert "--allow-mutating-setup" in res.output
    assert "--base-url-var" in res.output


def test_cli_prepare_tests_missing_plan_fails() -> None:
    """Verify prepare-tests fails closed without --plan argument."""
    res = runner.invoke(app, ["prepare-tests"])
    assert res.exit_code != EXIT_OK


# =====================================================================
# CLI TESTS: EXECUTION & FIXTURE RESOLUTION
# =====================================================================


def test_cli_prepare_tests_end_to_end(
    tmp_path: Path,
    sample_openapi_file: Path,
    sample_test_plan_file: Path,
) -> None:
    """Verify prepare-tests compiles ready tests and flags unverified placeholders."""
    output_suite = tmp_path / "prepared_suite.yaml"
    output_report = tmp_path / "prep_report.json"

    res = runner.invoke(
        app,
        [
            "prepare-tests",
            "--plan",
            str(sample_test_plan_file),
            "--spec",
            str(sample_openapi_file),
            "--output",
            str(output_suite),
            "--report",
            str(output_report),
        ],
    )
    assert res.exit_code == EXIT_OK
    assert "Test Data & Fixture Preparation" in res.output
    assert "[FIXTURE_READY]       : 1" in res.output
    assert "[UNVERIFIED_BODY_FK]  : 1" in res.output
    assert "[MUTATING_SETUP_ALLOWED]: False" in res.output

    # Verify output suite exists and is valid
    assert output_suite.is_file()
    suite = load_collection(output_suite)
    assert len(suite.tests) == 1
    assert "/api/suppliers" in suite.tests[0].name

    # Verify output report exists and contains planned workflows
    assert output_report.is_file()
    rep = json.loads(output_report.read_text(encoding="utf-8"))
    assert rep["summary"]["total_scenarios_evaluated"] == 3
    assert rep["summary"]["fixture_ready_count"] == 1
    assert len(rep["planned_workflows"]) > 0


def test_cli_prepare_tests_with_fixture_file(
    tmp_path: Path,
    sample_openapi_file: Path,
    sample_test_plan_file: Path,
) -> None:
    """Verify external fixtures file resolves placeholders."""
    fixtures_file = tmp_path / "fixtures.yaml"
    fixtures_file.write_text(
        yaml.dump(
            {
                "supplierId": 42,
                "id": 100,
            }
        ),
        encoding="utf-8",
    )

    output_suite = tmp_path / "resolved_suite.yaml"
    output_report = tmp_path / "resolved_report.json"

    res = runner.invoke(
        app,
        [
            "prepare-tests",
            "--plan",
            str(sample_test_plan_file),
            "--spec",
            str(sample_openapi_file),
            "--fixtures",
            str(fixtures_file),
            "--output",
            str(output_suite),
            "--report",
            str(output_report),
        ],
    )
    assert res.exit_code == EXIT_OK

    # With supplierId and id resolved, all 3 scenarios become fixture ready!
    rep = json.loads(output_report.read_text(encoding="utf-8"))
    assert rep["summary"]["fixture_ready_count"] == 3
    assert rep["summary"]["unverified_placeholder_count"] == 0


def test_cli_prepare_tests_allow_mutating_setup(
    sample_openapi_file: Path,
    sample_test_plan_file: Path,
) -> None:
    """Verify --allow-mutating-setup sets mutating setup allowed flag."""
    res = runner.invoke(
        app,
        [
            "prepare-tests",
            "--plan",
            str(sample_test_plan_file),
            "--spec",
            str(sample_openapi_file),
            "--allow-mutating-setup",
        ],
    )
    assert res.exit_code == EXIT_OK
    assert "[MUTATING_SETUP_ALLOWED]: True" in res.output


# =====================================================================
# CLI TESTS: REAL STOCKPILE ACCEPTANCE EVALUATION
# =====================================================================


def test_cli_prepare_tests_stockpile_evaluation(tmp_path: Path) -> None:
    """Verify prepare-tests on real StockPile test plan and OpenAPI spec."""
    stockpile_plan = Path("stockpile_tests/stockpile_test_plan.yaml")
    stockpile_spec = Path("stockpile_tests/stockpile_openapi.yaml")
    stockpile_intel = Path("stockpile_tests/stockpile_intelligence_report.yaml")

    if not (
        stockpile_plan.is_file()
        and stockpile_spec.is_file()
        and stockpile_intel.is_file()
    ):
        pytest.skip("StockPile test artifacts not found on disk.")

    out_suite = tmp_path / "stockpile_prep_suite.yaml"
    out_rep = tmp_path / "stockpile_prep_report.json"

    res = runner.invoke(
        app,
        [
            "prepare-tests",
            "--plan",
            str(stockpile_plan),
            "--spec",
            str(stockpile_spec),
            "--intelligence",
            str(stockpile_intel),
            "--output",
            str(out_suite),
            "--report",
            str(out_rep),
        ],
    )
    assert res.exit_code == EXIT_OK
    assert "Total Scenarios Evaluated: 205" in res.output
    assert "[UNVERIFIED_BODY_FK]  : 14" in res.output
    assert "[WORKFLOWS_PLANNED]   : 8" in res.output

    # Verify suite loads cleanly with load_collection
    assert out_suite.is_file()
    suite = load_collection(out_suite)
    assert len(suite.tests) > 100

    # Verify report contains surplus-listings foreign key issue
    assert out_rep.is_file()
    rep = json.loads(out_rep.read_text(encoding="utf-8"))
    assert rep["summary"]["total_scenarios_evaluated"] == 205

    # Check unverified entity references includes surplus listings
    unverified_ops = [
        ref.get("operation") for ref in rep["unverified_entity_references"]
    ]
    assert any("surplus-listings" in op for op in unverified_ops)
