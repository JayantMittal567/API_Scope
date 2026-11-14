"""Integration tests for ApiScope CLI inspect-target command.

Verifies end-to-end command line execution with OpenAPI specs, optional repository
inspection, YAML/JSON output export, error handling, and real fixture verification.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from apiscope.cli.main import EXIT_OK, EXIT_TOOL_ERROR, app

runner = CliRunner()


@pytest.fixture
def temp_openapi_file(tmp_path: Path) -> Path:
    """Create a temporary valid OpenAPI spec file."""
    spec_path = tmp_path / "openapi.yaml"
    spec_data = {
        "openapi": "3.0.1",
        "info": {
            "title": "Warehouse Management API",
            "version": "2.4.0",
            "description": "Logistics and warehouse management",
        },
        "paths": {
            "/api/v1/inventory": {
                "get": {
                    "tags": ["generic-controller"],
                    "operationId": "getInventory",
                    "responses": {"200": {"description": "OK"}},
                },
                "post": {
                    "tags": ["generic-controller"],
                    "operationId": "addInventory",
                    "responses": {"201": {"description": "Created"}},
                },
            },
            "/api/v1/inventory/{id}": {
                "get": {
                    "tags": ["generic-controller"],
                    "operationId": "getInventoryItem",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
    }
    spec_path.write_text(yaml.safe_dump(spec_data), encoding="utf-8")
    return spec_path


def test_cli_inspect_target_spec_only(temp_openapi_file: Path) -> None:
    """Verify inspect-target with OpenAPI prints structured intelligence."""
    result = runner.invoke(app, ["inspect-target", "--spec", str(temp_openapi_file)])

    assert result.exit_code == EXIT_OK
    assert (
        "=== Project Intelligence: Warehouse Management API (v2.4.0) ==="
        in result.output
    )
    assert "Discovered Endpoints: 3" in result.output
    assert "inventory: 3 endpoints [INFERRED]" in result.output
    assert "Risk Areas" in result.output


def test_cli_inspect_target_with_repo(temp_openapi_file: Path, tmp_path: Path) -> None:
    """Verify inspect-target with repository scans language, roles, and routes."""
    repo_dir = tmp_path / "test_repo"
    repo_dir.mkdir()

    # Add Python route with shadow endpoint
    py_routes = repo_dir / "routes.py"
    py_routes.write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/api/v1/inventory')\n"
        "def list_inv(): return []\n"
        "@router.delete('/api/v1/emergency-reset')\n"
        "def reset(): return {}\n"
    )

    result = runner.invoke(
        app,
        ["inspect-target", "--spec", str(temp_openapi_file), "--repo", str(repo_dir)],
    )

    assert result.exit_code == EXIT_OK
    assert "Language / Framework: python/fastapi" in result.output
    assert "SHADOW_ENDPOINT" in result.output
    assert "emergency-reset" in result.output


def test_cli_inspect_target_output_yaml(
    temp_openapi_file: Path, tmp_path: Path
) -> None:
    """Verify inspect-target writes valid YAML report when --output is passed."""
    out_file = tmp_path / "output_report.yaml"
    result = runner.invoke(
        app,
        [
            "inspect-target",
            "--spec",
            str(temp_openapi_file),
            "--output",
            str(out_file),
        ],
    )

    assert result.exit_code == EXIT_OK
    assert out_file.is_file()

    with open(out_file, encoding="utf-8") as f:
        data = yaml.safe_load(f.read())

    assert data["schema_version"] == "1.0.0"
    assert data["project"]["name"] == "Warehouse Management API"
    assert len(data["endpoints"]) == 3
    assert len(data["modules"]) == 1


def test_cli_inspect_target_output_json(
    temp_openapi_file: Path, tmp_path: Path
) -> None:
    """Verify inspect-target writes valid JSON report when .json path is given."""
    out_file = tmp_path / "output_report.json"
    result = runner.invoke(
        app,
        [
            "inspect-target",
            "--spec",
            str(temp_openapi_file),
            "--output",
            str(out_file),
        ],
    )

    assert result.exit_code == EXIT_OK
    assert out_file.is_file()

    with open(out_file, encoding="utf-8") as f:
        data = json.load(f)

    assert data["schema_version"] == "1.0.0"
    assert data["project"]["name"] == "Warehouse Management API"
    assert len(data["endpoints"]) == 3


def test_cli_inspect_target_missing_spec_error() -> None:
    """Verify error exit code when OpenAPI specification path does not exist."""
    result = runner.invoke(
        app,
        ["inspect-target", "--spec", "non_existent_spec_file.yaml"],
    )

    assert result.exit_code == EXIT_TOOL_ERROR
    assert "Error:" in result.output


def test_cli_inspect_target_stockpile_fixture() -> None:
    """Verify inspect-target against real StockPile OpenAPI fixture."""
    stockpile_spec = Path("stockpile_tests/stockpile_openapi.yaml")
    if not stockpile_spec.is_file():
        pytest.skip("StockPile OpenAPI fixture not available in workspace")

    result = runner.invoke(
        app,
        ["inspect-target", "--spec", str(stockpile_spec)],
    )

    assert result.exit_code == EXIT_OK
    assert "Discovered Endpoints: 60" in result.output
    # Verify module inference broke monolithic controller down into functional domains
    assert "products:" in result.output
    assert "purchase_orders:" in result.output
    assert "suppliers:" in result.output
    assert (
        "stockpile-controller"
        not in result.output.split("--- Modules ---")[1].split("--- Domain Entities")[0]
    )
