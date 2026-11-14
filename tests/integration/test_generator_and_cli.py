"""Integration tests for OpenAPI test generator, YAML roundtrip, and CLI commands.

Covers: FR-18, FR-19, FR-23, FR-24, FR-25, FR-26, NFR-04, NFR-06.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from typer.testing import CliRunner

from apiscope.cli.main import app
from apiscope.core.executor import RequestExecutor
from apiscope.core.results import CaseStatus
from apiscope.core.run_service import RunService
from apiscope.definitions.collection import TestCollection, load_collection
from apiscope.definitions.environment import Environment
from apiscope.generators.service import GeneratorService


def test_generated_yaml_roundtrips_through_load_collection_and_executes_in_run_service(
    tmp_path: Path,
) -> None:
    """Covers: FR-18, FR-23, NFR-06
    Generate YAML from OpenAPI spec, save it to disk, load back using load_collection,
    and execute the positive tests through RunService with a mock transport.
    """
    gen_service = GeneratorService()
    spec_path = Path("examples/m5_sample_openapi.yaml")
    output_suite = tmp_path / "generated_suite.yaml"

    collection = gen_service.generate_from_spec(
        spec_path=spec_path,
        output_path=output_suite,
    )
    assert len(collection) > 0

    # Verify that load_collection loads the generated suite back with full fidelity
    reloaded_collection = load_collection(output_suite, base_dir=tmp_path)
    assert len(reloaded_collection) == len(collection)

    # Filter to positive tests for mock execution
    positive_tests = [t for t in reloaded_collection if "positive" in t.tags]
    assert len(positive_tests) == 3

    # Setup mock transport for the 3 endpoints
    recorded_calls: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_calls.append(request)
        url_str = str(request.url)

        if url_str == "https://api.example.com/orders" and request.method == "POST":
            assert request.headers.get("authorization") == "Bearer secret-jwt-token"
            body = json.loads(request.content.decode("utf-8"))
            assert "product_id" in body
            assert "quantity" in body
            return httpx.Response(201, json={"order_id": 9901, "status": "CONFIRMED"})

        if url_str == "https://api.example.com/orders/1" and request.method == "GET":
            assert request.headers.get("authorization") == "Bearer secret-jwt-token"
            return httpx.Response(
                200, json={"order_id": 1, "product_id": 5, "quantity": 2}
            )

        if url_str == "https://api.example.com/products" and request.method == "GET":
            return httpx.Response(200, json=[{"id": 1, "name": "Standard Widget"}])

        return httpx.Response(404, json={"error": "not found"})

    executor = RequestExecutor(transport=httpx.MockTransport(mock_handler))
    run_service = RunService(executor=executor)

    test_env = Environment(
        name="integration-test",
        variables={
            "base_url": "https://api.example.com",
            "auth_token": "secret-jwt-token",
        },
    )

    pos_suite = TestCollection(
        name="Positive Generated Suite",
        module="store",
        tests=positive_tests,
        base_dir=tmp_path,
    )

    run_result = run_service.run_collection(
        pos_suite, environment=test_env, allow_mutation=True
    )

    assert run_result.all_passed is True
    assert run_result.total_tests == 3
    assert run_result.passed_tests == 3
    assert run_result.failed_tests == 0
    assert len(recorded_calls) == 3

    for case_res in run_result.results:
        assert case_res.status == CaseStatus.PASSED


def test_cli_import_valid_spec_returns_0_and_invalid_returns_3(
    tmp_path: Path,
) -> None:
    """Covers: FR-19, FR-24, FR-26
    Test CLI 'apiscope import' succeeds on valid spec and fails on invalid.
    """
    runner = CliRunner()

    # Valid spec import
    result_valid = runner.invoke(app, ["import", "examples/m5_sample_openapi.yaml"])
    assert result_valid.exit_code == 0
    assert "Imported OpenAPI 3.0.3" in result_valid.output
    assert "orders, products" in result_valid.output

    # Valid spec import with output flag
    norm_out = tmp_path / "normalized_spec.json"
    result_norm = runner.invoke(
        app,
        ["import", "examples/m5_sample_openapi.yaml", "-o", str(norm_out)],
    )
    assert result_norm.exit_code == 0
    assert norm_out.is_file()

    # Missing spec file -> exit code 3
    result_missing = runner.invoke(app, ["import", "does_not_exist.yaml"])
    assert result_missing.exit_code == 3
    assert "Error:" in result_missing.output


def test_cli_generate_writes_yaml_and_supports_module_filter(
    tmp_path: Path,
) -> None:
    """Covers: FR-23, FR-24, FR-25, FR-26
    Test CLI 'apiscope generate' writes YAML collection and restricts generation
    to the specified module when --module is passed.
    """
    runner = CliRunner()
    output_orders = tmp_path / "orders_only.yaml"

    result = runner.invoke(
        app,
        [
            "generate",
            "examples/m5_sample_openapi.yaml",
            "-o",
            str(output_orders),
            "--module",
            "orders",
        ],
    )
    assert result.exit_code == 0
    assert "Generated" in result.output
    assert "rule-based tests" in result.output
    assert output_orders.is_file()

    collection = load_collection(output_orders, base_dir=tmp_path)
    assert len(collection) > 0
    for t in collection.tests:
        assert t.module == "orders"


def test_cli_generate_unknown_module_or_unsafe_path_returns_exit_code_3(
    tmp_path: Path,
) -> None:
    """Covers: FR-25, FR-26, NFR-04
    Test CLI 'apiscope generate' exits with 3 on unknown module or directory traversal.
    """
    runner = CliRunner()
    out_file = tmp_path / "out.yaml"

    # Unknown module -> exit code 3
    result_unknown = runner.invoke(
        app,
        [
            "generate",
            "examples/m5_sample_openapi.yaml",
            "-o",
            str(out_file),
            "--module",
            "nonexistent_module_xyz",
        ],
    )
    assert result_unknown.exit_code == 3
    assert "Error:" in result_unknown.output

    # Unsafe path traversal -> exit code 3
    result_traversal = runner.invoke(
        app,
        [
            "generate",
            "examples/m5_sample_openapi.yaml",
            "-o",
            "../escaped_suite.yaml",
        ],
    )
    assert result_traversal.exit_code == 3
    assert "Error:" in result_traversal.output
