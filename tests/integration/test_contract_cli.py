"""Integration tests for ApiScope CLI analyze-contract command.

Verifies end-to-end command line execution for API contract normalization,
quality analysis, source reconciliation, strict-mode quality gates, YAML/JSON
exports, and real fixture analysis.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from apiscope.cli.main import (
    EXIT_OK,
    EXIT_TOOL_ERROR,
    EXIT_WARNINGS_STRICT,
    app,
)

runner = CliRunner()


@pytest.fixture
def temp_openapi_file(tmp_path: Path) -> Path:
    """Create a temporary valid OpenAPI specification file."""
    spec_path = tmp_path / "openapi.yaml"
    spec_data = {
        "openapi": "3.0.1",
        "info": {
            "title": "Warehouse Management API",
            "version": "2.4.0",
            "description": "Logistics and warehouse management",
        },
        "servers": [{"url": "http://localhost:8080"}],
        "paths": {
            "/api/v1/inventory": {
                "get": {
                    "tags": ["warehouse-controller"],
                    "operationId": "getInventory",
                    "summary": "List all inventory",
                    "parameters": [
                        {
                            "name": "category",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Inventory list",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "$ref": "#/components/schemas/StockItem"
                                        },
                                    }
                                }
                            },
                        }
                    },
                },
                "post": {
                    "tags": ["warehouse-controller"],
                    "operationId": "addInventory",
                    "summary": "Add new inventory",
                    "security": [{"BearerAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/StockItemInput"
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Created item",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/StockItem"}
                                }
                            },
                        }
                    },
                },
            },
            "/api/v1/inventory/{id}": {
                "get": {
                    "tags": ["warehouse-controller"],
                    "operationId": "getInventoryItem",
                    "summary": "Get item by id",
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
                            "description": "Item details",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/StockItem"}
                                }
                            },
                        },
                        "404": {"description": "Item not found"},
                    },
                },
                "delete": {
                    "tags": ["warehouse-controller"],
                    "operationId": "deleteInventoryItem",
                    "summary": "Delete item by id",
                    "security": [{"BearerAuth": []}],
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {"204": {"description": "Item deleted"}},
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
                "StockItem": {
                    "type": "object",
                    "required": ["id", "sku", "quantity"],
                    "properties": {
                        "id": {"type": "integer"},
                        "sku": {"type": "string"},
                        "quantity": {"type": "integer", "minimum": 0},
                    },
                },
                "StockItemInput": {
                    "type": "object",
                    "required": ["sku", "quantity"],
                    "properties": {
                        "sku": {"type": "string"},
                        "quantity": {"type": "integer", "minimum": 0},
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


def test_cli_analyze_contract_spec_only(temp_openapi_file: Path) -> None:
    """Verify analyze-contract prints structured contract analysis table."""
    result = runner.invoke(app, ["analyze-contract", "--spec", str(temp_openapi_file)])

    assert result.exit_code == EXIT_OK
    assert "API Contract Quality Audit" in result.output
    assert "Warehouse Management API" in result.output
    assert "Total Operations: 4" in result.output
    assert "READY: 4" in result.output


def test_cli_analyze_contract_output_yaml(
    temp_openapi_file: Path, tmp_path: Path
) -> None:
    """Verify analyze-contract exports valid YAML report when --output is provided."""
    output_file = tmp_path / "contract_quality.yaml"
    result = runner.invoke(
        app,
        [
            "analyze-contract",
            "--spec",
            str(temp_openapi_file),
            "--output",
            str(output_file),
        ],
    )

    assert result.exit_code == EXIT_OK
    assert output_file.exists()

    with open(output_file, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert data["title"] == "Warehouse Management API"
    assert data["total_operations"] == 4
    assert "operations" in data
    assert "findings" in data
    assert "readiness_summary" in data


def test_cli_analyze_contract_output_json(
    temp_openapi_file: Path, tmp_path: Path
) -> None:
    """Verify analyze-contract exports valid JSON report when .json path is provided."""
    output_file = tmp_path / "contract_quality.json"
    result = runner.invoke(
        app,
        [
            "analyze-contract",
            "--spec",
            str(temp_openapi_file),
            "--output",
            str(output_file),
        ],
    )

    assert result.exit_code == EXIT_OK
    assert output_file.exists()

    with open(output_file, encoding="utf-8") as f:
        data = json.load(f)

    assert data["title"] == "Warehouse Management API"
    assert data["total_operations"] == 4
    assert len(data["operations"]) == 4


def test_cli_analyze_contract_with_repo(
    temp_openapi_file: Path, tmp_path: Path
) -> None:
    """Verify analyze-contract correlates routes and performs source reconciliation."""
    repo_dir = tmp_path / "warehouse-service"
    repo_dir.mkdir()

    controller_code = (
        "package com.example;\n"
        "@RestController\n"
        '@RequestMapping("/api/v1/inventory")\n'
        "public class InventoryController {\n"
        "    @GetMapping\n"
        "    public List<StockItem> list() { return null; }\n"
        "    @PostMapping\n"
        "    @PreAuthorize(\"hasRole('ADMIN')\")\n"
        "    public StockItem create() { return null; }\n"
        "}\n"
    )
    (repo_dir / "InventoryController.java").write_text(controller_code)

    result = runner.invoke(
        app,
        [
            "analyze-contract",
            "--spec",
            str(temp_openapi_file),
            "--repo",
            str(repo_dir),
        ],
    )

    assert result.exit_code == EXIT_OK
    assert "Source Reconciliation" in result.output
    assert "Matched Operations: 2" in result.output


def test_cli_analyze_contract_with_intelligence(
    temp_openapi_file: Path, tmp_path: Path
) -> None:
    """Verify analyze-contract incorporates Project Intelligence Report."""
    intel_file = tmp_path / "intel_report.yaml"
    intel_data = {
        "schema_version": "1.0.0",
        "generated_at": "2026-11-14T10:00:00Z",
        "project": {"name": "Warehouse Management API", "version": "2.4.0"},
        "endpoints": [
            {
                "path": "/api/v1/inventory",
                "method": "GET",
                "operation_id": "getInventory",
                "inferred_module": "inventory_core",
            }
        ],
        "modules": [
            {
                "name": "inventory_core",
                "endpoints_count": 1,
                "endpoints": ["GET /api/v1/inventory"],
                "status": "CONFIRMED",
            }
        ],
        "entities": [
            {
                "name": "WarehouseLocation",
                "fields": ["locationId", "zone"],
                "verification_status": "CONFIRMED",
            }
        ],
        "relationships": [],
        "auth": {"auth_type": "bearer_jwt", "roles": ["Admin"]},
        "workflows": [],
        "business_rules": [],
        "test_data_requirements": [],
        "risk_areas": [],
        "missing_information": [],
    }
    intel_file.write_text(yaml.safe_dump(intel_data), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "analyze-contract",
            "--spec",
            str(temp_openapi_file),
            "--intelligence",
            str(intel_file),
        ],
    )

    assert result.exit_code == EXIT_OK
    assert "Schema Reconciliation" in result.output
    assert "WarehouseLocation" in result.output


def test_cli_analyze_contract_strict_mode(tmp_path: Path) -> None:
    """Verify --strict exits with code 2 when HIGH severity defect is present."""
    # Create spec with missing path parameter (HIGH severity finding)
    broken_spec_path = tmp_path / "broken_spec.yaml"
    broken_data = {
        "openapi": "3.0.0",
        "info": {"title": "Broken API", "version": "1.0"},
        "paths": {
            "/api/broken/{missingParam}": {
                "get": {
                    "operationId": "brokenOp",
                    "parameters": [],  # missingParam not declared!
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    broken_spec_path.write_text(yaml.safe_dump(broken_data), encoding="utf-8")

    # Without --strict, completes with EXIT_OK (warning shown)
    res_normal = runner.invoke(
        app, ["analyze-contract", "--spec", str(broken_spec_path)]
    )
    assert res_normal.exit_code == EXIT_OK

    # With --strict, fails closed with EXIT_WARNINGS_STRICT (code 2)
    res_strict = runner.invoke(
        app, ["analyze-contract", "--spec", str(broken_spec_path), "--strict"]
    )
    assert res_strict.exit_code == EXIT_WARNINGS_STRICT


def test_cli_analyze_contract_missing_spec_file() -> None:
    """Verify command fails closed with code 3 when spec file does not exist."""
    result = runner.invoke(
        app, ["analyze-contract", "--spec", "nonexistent_spec_file.yaml"]
    )

    assert result.exit_code == EXIT_TOOL_ERROR


def test_cli_analyze_contract_stockpile_fixture() -> None:
    """Verify end-to-end execution against real StockPile fixture."""
    spec_path = Path("stockpile_tests/stockpile_openapi.yaml")
    intel_path = Path("stockpile_tests/stockpile_intelligence_report.yaml")
    repo_path = Path(r"E:\REsume projects\StockPile")

    if not spec_path.exists():
        pytest.skip("StockPile OpenAPI specification fixture not found.")

    args = ["analyze-contract", "--spec", str(spec_path)]
    if intel_path.exists():
        args.extend(["--intelligence", str(intel_path)])
    if repo_path.exists():
        args.extend(["--repo", str(repo_path)])

    result = runner.invoke(app, args)

    assert result.exit_code == EXIT_OK
    assert "Total Operations: 60" in result.output
    assert "Declared Component Schemas: 66" in result.output
    # 0 BLOCKED operations
    assert "BLOCKED: 0" in result.output
