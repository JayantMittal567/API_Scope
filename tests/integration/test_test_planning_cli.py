"""Integration tests for ApiScope CLI plan-tests command (Task 18).

Verifies end-to-end command-line execution for deterministic baseline
test planning, optional AI workflow planning, YAML/JSON export, error
handling, and real fixture analysis.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from apiscope.cli.main import EXIT_OK, EXIT_TOOL_ERROR, app
from apiscope.planning.models import (
    AIUsageRecord,
    DefinitionStatus,
    OracleType,
    RiskClassification,
    ScenarioCategory,
    ScenarioOrigin,
    ScenarioPriority,
    ScenarioStep,
    TestScenario,
)

runner = CliRunner()


@pytest.fixture
def temp_openapi_file(tmp_path: Path) -> Path:
    """Create a temporary valid OpenAPI specification file."""
    spec_path = tmp_path / "openapi.yaml"
    spec_data = {
        "openapi": "3.0.1",
        "info": {
            "title": "Logistics Dispatch API",
            "version": "1.2.0",
            "description": "Logistics and vehicle fleet dispatch",
        },
        "servers": [{"url": "http://localhost:8080"}],
        "paths": {
            "/api/v1/shipments": {
                "get": {
                    "tags": ["shipments"],
                    "operationId": "listShipments",
                    "summary": "List shipments",
                    "parameters": [
                        {
                            "name": "status",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Shipment list",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "$ref": "#/components/schemas/Shipment"
                                        },
                                    }
                                }
                            },
                        }
                    },
                },
                "post": {
                    "tags": ["shipments"],
                    "operationId": "createShipment",
                    "summary": "Create shipment",
                    "security": [{"BearerAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ShipmentInput"}
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Created shipment",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Shipment"}
                                }
                            },
                        }
                    },
                },
            },
            "/api/v1/shipments/{id}": {
                "get": {
                    "tags": ["shipments"],
                    "operationId": "getShipment",
                    "summary": "Get shipment by id",
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
                            "description": "Shipment details",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Shipment"}
                                }
                            },
                        },
                        "404": {"description": "Shipment not found"},
                    },
                },
                "delete": {
                    "tags": ["shipments"],
                    "operationId": "deleteShipment",
                    "summary": "Cancel shipment",
                    "security": [{"BearerAuth": []}],
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {"204": {"description": "Shipment cancelled"}},
                },
            },
        },
        "components": {
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                }
            },
            "schemas": {
                "Shipment": {
                    "type": "object",
                    "required": ["id", "trackingNumber"],
                    "properties": {
                        "id": {"type": "integer"},
                        "trackingNumber": {"type": "string"},
                        "weightKg": {"type": "number", "minimum": 0.1},
                    },
                },
                "ShipmentInput": {
                    "type": "object",
                    "required": ["trackingNumber"],
                    "properties": {
                        "trackingNumber": {"type": "string"},
                        "weightKg": {"type": "number", "minimum": 0.1},
                    },
                },
            },
        },
    }
    spec_path.write_text(yaml.safe_dump(spec_data), encoding="utf-8")
    return spec_path


# =====================================================================
# CLI TESTS
# =====================================================================


def test_cli_plan_tests_spec_only(temp_openapi_file: Path) -> None:
    """Verify plan-tests outputs structured test plan metrics offline."""
    result = runner.invoke(app, ["plan-tests", "--spec", str(temp_openapi_file)])

    assert result.exit_code == EXIT_OK
    assert "API Test Plan: Logistics Dispatch API" in result.output
    assert "Total Proposed Scenarios:" in result.output
    assert "Deterministic Contract Baseline:" in result.output
    assert "CONTRACT_POSITIVE" in result.output
    assert "Approval Status:" in result.output


def test_cli_plan_tests_output_yaml(temp_openapi_file: Path, tmp_path: Path) -> None:
    """Verify plan-tests saves valid YAML test plan when --output is provided."""
    out_yaml = tmp_path / "plan.yaml"
    result = runner.invoke(
        app,
        ["plan-tests", "--spec", str(temp_openapi_file), "--output", str(out_yaml)],
    )

    assert result.exit_code == EXIT_OK
    assert out_yaml.exists()

    with open(out_yaml, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert data["title"] == "Logistics Dispatch API"
    assert "scenarios" in data
    assert "summary" in data
    assert len(data["scenarios"]) >= 4


def test_cli_plan_tests_output_json(temp_openapi_file: Path, tmp_path: Path) -> None:
    """Verify plan-tests saves valid JSON test plan when .json path is provided."""
    out_json = tmp_path / "plan.json"
    result = runner.invoke(
        app,
        ["plan-tests", "--spec", str(temp_openapi_file), "--output", str(out_json)],
    )

    assert result.exit_code == EXIT_OK
    assert out_json.exists()

    with open(out_json, encoding="utf-8") as f:
        data = json.load(f)

    assert data["title"] == "Logistics Dispatch API"
    assert len(data["scenarios"]) >= 4


def test_cli_plan_tests_with_intelligence(
    temp_openapi_file: Path, tmp_path: Path
) -> None:
    """Verify plan-tests accepts Project Intelligence report."""
    intel_file = tmp_path / "intel.yaml"
    intel_data = {
        "schema_version": "1.0.0",
        "generated_at": "2026-11-14T10:00:00Z",
        "project": {"name": "Logistics Dispatch API", "version": "1.2.0"},
        "entities": [
            {
                "name": "Shipment",
                "properties": ["id", "trackingNumber"],
                "status": "CONFIRMED",
            }
        ],
        "business_rules": [],
        "workflows": [],
        "auth": {"auth_type": "bearer_jwt", "roles": ["Dispatcher"]},
        "test_data_requirements": [],
        "risk_areas": [],
        "missing_information": [],
    }
    intel_file.write_text(yaml.safe_dump(intel_data), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "plan-tests",
            "--spec",
            str(temp_openapi_file),
            "--intelligence",
            str(intel_file),
        ],
    )

    assert result.exit_code == EXIT_OK
    assert "Logistics Dispatch API" in result.output


def test_cli_plan_tests_with_ai_mocked(temp_openapi_file: Path) -> None:
    """Verify plan-tests with --ai integrates AI proposals and usage reporting."""
    mock_ai_scenario = TestScenario(
        scenario_id="SCN-AI-SHIPMENT-LIFECYCLE-01",
        title="Shipment Dispatch to Delivery Lifecycle",
        description="Create shipment, assign vehicle, verify dispatch state",
        category=ScenarioCategory.WORKFLOW_STATE_TRANSITION,
        priority=ScenarioPriority.HIGH,
        origin=ScenarioOrigin.AI_PROPOSED,
        target_module="shipments",
        target_endpoints=["POST /api/v1/shipments", "GET /api/v1/shipments/{id}"],
        steps=[
            ScenarioStep(
                step_number=1,
                method="POST",
                path="/api/v1/shipments",
                description="Create new shipment",
                expected_status=["201"],
                oracle_type=OracleType.AI_HYPOTHESIS,
            ),
            ScenarioStep(
                step_number=2,
                method="GET",
                path="/api/v1/shipments/{id}",
                description="Verify shipment status",
                expected_status=["200"],
                oracle_type=OracleType.AI_HYPOTHESIS,
            ),
        ],
        risk_classification=RiskClassification.MEDIUM,
        approval_status=DefinitionStatus.PROPOSED,
    )

    mock_usage = AIUsageRecord(
        status="COMPLETED",
        calls_made=1,
        prompt_tokens=450,
        completion_tokens=220,
        total_tokens=670,
        is_estimated=True,
        details="Generated 1 workflow scenario",
    )

    with patch(
        "apiscope.planning.ai_planner.GeminiTestPlanner.plan_project_scenarios",
        return_value=([mock_ai_scenario], mock_usage),
    ):
        result = runner.invoke(
            app,
            [
                "plan-tests",
                "--spec",
                str(temp_openapi_file),
                "--ai",
                "--api-key",
                "test-api-key",
            ],
        )

    assert result.exit_code == EXIT_OK
    assert "AI-Proposed Domain Scenarios: 1" in result.output
    assert "AI Reasoning Usage" in result.output
    assert "Status: COMPLETED" in result.output
    assert "Calls Made: 1" in result.output


def test_cli_plan_tests_ai_without_key_skips_gracefully(
    temp_openapi_file: Path,
) -> None:
    """Verify plan-tests with --ai but empty API key skips gracefully."""
    with patch.dict("os.environ", {}, clear=True):
        result = runner.invoke(
            app,
            [
                "plan-tests",
                "--spec",
                str(temp_openapi_file),
                "--ai",
                "--api-key",
                "",
            ],
        )

    assert result.exit_code == EXIT_OK
    assert "SKIPPED" in result.output
    assert "Deterministic Contract Baseline:" in result.output


def test_cli_plan_tests_missing_spec_and_contract() -> None:
    """Verify plan-tests fails closed when neither spec nor contract is provided."""
    result = runner.invoke(app, ["plan-tests"])

    assert result.exit_code == EXIT_TOOL_ERROR
    assert "Either --spec or --contract must be specified" in result.output


def test_cli_plan_tests_stockpile_fixture() -> None:
    """Verify plan-tests against real StockPile fixture generates comprehensive plan."""
    spec_path = Path("stockpile_tests/stockpile_openapi.yaml")
    intel_path = Path("stockpile_tests/stockpile_intelligence_report.yaml")

    if not spec_path.exists():
        pytest.skip("StockPile OpenAPI specification fixture not found.")

    args = ["plan-tests", "--spec", str(spec_path)]
    if intel_path.exists():
        args.extend(["--intelligence", str(intel_path)])

    result = runner.invoke(app, args)

    assert result.exit_code == EXIT_OK
    assert "Total Proposed Scenarios: 205" in result.output
    assert "CONTRACT_POSITIVE: 60" in result.output
    assert "RESOURCE_EXISTENCE: 27" in result.output
    assert "AUTH_SECURITY: 30" in result.output
    assert "Deterministic Contract Baseline: 205" in result.output
