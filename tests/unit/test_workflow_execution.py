"""Unit tests for Safe Autonomous Workflow Execution & Integration (Task 21).

Verifies policy-gated stateful workflow execution, dry-run zero-network guarantees,
dependency-ordered setup, reverse-order (LIFO) cleanup, strict entity ownership,
and variable context isolation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from apiscope.core.exceptions import DefinitionError
from apiscope.core.executor import RequestExecutor, TargetAccessPolicy
from apiscope.core.results import CaseStatus
from apiscope.definitions.environment import Environment
from apiscope.execution.models import (
    CreatedResource,
    WorkflowExecutionResult,
    WorkflowStepResult,
    WorkflowStepType,
)
from apiscope.execution.service import (
    WorkflowExecutionService,
)
from apiscope.fixtures.models import (
    FixturePreparationReport,
    StatefulWorkflow,
    WorkflowAction,
)

# =====================================================================
# FIXTURES AND HELPERS
# =====================================================================


def _make_step(
    step_number: int,
    name: str,
    action_type: str = "TEST",
    method: str = "GET",
    url: str = "{{base_url}}/api/items",
    headers: dict[str, Any] | None = None,
    body: Any = None,
    extract: dict[str, str] | None = None,
    assertions: list[dict[str, Any]] | None = None,
    risk: str = "risk:read_only",
    rollback_endpoint: str | None = None,
) -> WorkflowAction:
    """Helper to construct a test WorkflowAction."""
    return WorkflowAction(
        step_number=step_number,
        name=name,
        action_type=action_type,
        method=method,
        url=url,
        headers=headers or {},
        body=body,
        extract=extract or {},
        assertions=assertions or [],
        risk=risk,
        rollback_endpoint=rollback_endpoint,
    )


def _make_sample_workflow(
    workflow_id: str = "wf_product_lifecycle",
    title: str = "Product Lifecycle Workflow",
) -> StatefulWorkflow:
    """Helper to build a 3-step setup -> test -> cleanup workflow."""
    setup_step = _make_step(
        step_number=1,
        name="Create Category",
        action_type="SETUP",
        method="POST",
        url="{{base_url}}/api/categories",
        body={"name": "Electronics"},
        extract={"categoryId": "$.id"},
        assertions=[{"type": "status", "equals": 201}],
        risk="risk:mutating",
    )
    test_step = _make_step(
        step_number=2,
        name="Get Created Category",
        action_type="TEST",
        method="GET",
        url="{{base_url}}/api/categories/{{categoryId}}",
        assertions=[{"type": "status", "equals": 200}],
        risk="risk:read_only",
    )
    cleanup_step = _make_step(
        step_number=3,
        name="Delete Category",
        action_type="CLEANUP",
        method="DELETE",
        url="{{base_url}}/api/categories/{{categoryId}}",
        assertions=[{"type": "status", "equals": 204}],
        risk="risk:destructive",
        rollback_endpoint="{{categoryId}}",
    )
    return StatefulWorkflow(
        workflow_id=workflow_id,
        title=title,
        description="Tests category creation, retrieval, and cleanup",
        setup_steps=[setup_step],
        test_steps=[test_step],
        cleanup_steps=[cleanup_step],
        provided_fixtures=["categoryId"],
    )


# =====================================================================
# 1. DOMAIN MODELS UNIT TESTS
# =====================================================================


class TestWorkflowModels:
    """Test suite for Workflow domain models and result structures."""

    def test_created_resource_model(self) -> None:
        """Verify CreatedResource model serialization and tracking."""
        res = CreatedResource(
            entity="category",
            resource_id="cat_42",
            cleanup_url="http://localhost:8080/api/categories/cat_42",
            created_at_step=1,
        )
        assert res.entity == "category"
        assert res.resource_id == "cat_42"
        d = res.to_dict()
        assert d["entity"] == "category"
        assert d["resource_id"] == "cat_42"
        assert d["created_at_step"] == 1
        assert d["cleanup_url"] == "http://localhost:8080/api/categories/cat_42"

    def test_workflow_step_result_to_case_result(self) -> None:
        """Verify WorkflowStepResult converts to valid CaseResult."""
        step_res = WorkflowStepResult(
            step_number=1,
            name="Test Step 1",
            step_type=WorkflowStepType.SETUP,
            status=CaseStatus.PASSED,
            method="POST",
            url="http://localhost:8080/api/items",
            status_code=201,
            elapsed_ms=45.2,
            assertions=[
                {
                    "assertion_type": "status",
                    "passed": True,
                    "expected": 201,
                    "actual": 201,
                    "message": "OK",
                }
            ],
            extracted_variables={"itemId": "item_123"},
            correlation_id="corr-12345",
        )

        case = step_res.to_case_result(workflow_id="wf_001")
        assert case.test_name == "[wf_001] [SETUP:1] Test Step 1"
        assert case.module == "workflow"
        assert "workflow:wf_001" in case.tags
        assert "step-type:setup" in case.tags
        assert case.status == CaseStatus.PASSED
        assert case.status_code == 201
        assert case.extracted_variables == {"itemId": "item_123"}
        assert case.correlation_id == "corr-12345"

    def test_workflow_execution_result_aggregation(self) -> None:
        """Verify WorkflowExecutionResult aggregation and to_run_result."""
        step_passed = WorkflowStepResult(
            step_number=1,
            name="Test 1",
            step_type=WorkflowStepType.TEST,
            status=CaseStatus.PASSED,
            method="GET",
            url="http://localhost:8080/api/test",
            status_code=200,
            elapsed_ms=10.0,
        )

        wf_res = WorkflowExecutionResult(
            workflow_id="wf_demo",
            title="Demo WF",
            status=CaseStatus.PASSED,
            started_at="2026-01-01T00:00:00Z",
            finished_at="2026-01-01T00:00:01Z",
            duration_ms=15.0,
            target_origin="http://localhost:8080",
            setup_results=[],
            test_results=[step_passed],
            cleanup_results=[],
        )

        assert wf_res.status == CaseStatus.PASSED
        run_res = wf_res.to_run_result()
        assert run_res.run_id.startswith("run-wf-wf_demo")
        assert run_res.total_tests == 1
        assert run_res.passed_tests == 1
        assert run_res.failed_tests == 0
        assert run_res.all_passed is True

    def test_workflow_cleanup_failure_status(self) -> None:
        """Verify test passed but cleanup failed yields CLEANUP_FAILED status."""
        test_ok = WorkflowStepResult(
            step_number=1,
            name="Test 1",
            step_type=WorkflowStepType.TEST,
            status=CaseStatus.PASSED,
            method="GET",
            url="http://localhost:8080/api/test",
            status_code=200,
            elapsed_ms=10.0,
        )
        cleanup_fail = WorkflowStepResult(
            step_number=2,
            name="Cleanup 1",
            step_type=WorkflowStepType.CLEANUP,
            status=CaseStatus.CLEANUP_FAILED,
            method="DELETE",
            url="http://localhost:8080/api/items/1",
            status_code=500,
            elapsed_ms=10.0,
            error_message="Server 500 error",
        )

        wf_res = WorkflowExecutionResult(
            workflow_id="wf_cleanup_fail",
            title="Cleanup Fail WF",
            status=CaseStatus.CLEANUP_FAILED,
            started_at="2026-01-01T00:00:00Z",
            finished_at="2026-01-01T00:00:01Z",
            duration_ms=25.0,
            target_origin="http://localhost:8080",
            setup_results=[],
            test_results=[test_ok],
            cleanup_results=[cleanup_fail],
        )

        assert wf_res.status == CaseStatus.CLEANUP_FAILED
        run_res = wf_res.to_run_result()
        assert run_res.has_warnings is True
        assert run_res.all_passed is False


# =====================================================================
# 2. WORKFLOW FILE LOADING UNIT TESTS
# =====================================================================


class TestWorkflowLoading:
    """Test suite for loading workflows from YAML, JSON, and reports."""

    def test_load_workflow_from_direct_yaml(self, tmp_path: Path) -> None:
        """Verify loading standalone StatefulWorkflow YAML file."""
        wf = _make_sample_workflow()
        wf_path = tmp_path / "sample_wf.yaml"
        wf_path.write_text(json.dumps(wf.to_dict()), encoding="utf-8")

        service = WorkflowExecutionService()
        loaded = service.load_workflow_from_file(wf_path)
        assert loaded.workflow_id == "wf_product_lifecycle"
        assert len(loaded.setup_steps) == 1
        assert len(loaded.test_steps) == 1
        assert len(loaded.cleanup_steps) == 1

    def test_load_workflows_from_preparation_report(self, tmp_path: Path) -> None:
        """Verify loading all workflows from FixturePreparationReport JSON."""
        wf1 = _make_sample_workflow(workflow_id="wf_1", title="WF 1")
        wf2 = _make_sample_workflow(workflow_id="wf_2", title="WF 2")

        report = FixturePreparationReport(
            plan_id="plan_sample",
            title="StockPile Test Prep",
            total_scenarios_evaluated=10,
            fixture_ready_count=5,
            unresolved_fixture_count=3,
            workflows_planned_count=2,
            planned_workflows=[wf1, wf2],
        )
        report_path = tmp_path / "prep_report.json"
        report_path.write_text(json.dumps(report.to_dict()), encoding="utf-8")

        service = WorkflowExecutionService()
        all_wfs = service.load_workflows_from_file(report_path)
        assert len(all_wfs) == 2
        assert all_wfs[0].workflow_id == "wf_1"
        assert all_wfs[1].workflow_id == "wf_2"

        # Filter by workflow_id
        filtered = service.load_workflow_from_file(report_path, workflow_id="wf_2")
        assert filtered.workflow_id == "wf_2"

    def test_load_workflow_missing_id_raises_error(self, tmp_path: Path) -> None:
        """Verify loading non-existent workflow_id from report raises DefinitionError."""
        wf = _make_sample_workflow(workflow_id="wf_actual")
        report_path = tmp_path / "report.json"
        report_path.write_text(
            json.dumps({"planned_workflows": [wf.to_dict()]}),
            encoding="utf-8",
        )
        service = WorkflowExecutionService()
        with pytest.raises(DefinitionError, match="Workflow ID 'wf_missing' not found"):
            service.load_workflow_from_file(report_path, workflow_id="wf_missing")

    def test_load_workflow_missing_file_raises_error(self, tmp_path: Path) -> None:
        """Verify non-existent file raises DefinitionError."""
        service = WorkflowExecutionService()
        with pytest.raises(DefinitionError, match="not found"):
            service.load_workflow_from_file(tmp_path / "nonexistent.yaml")


# =====================================================================
# 3. DRY RUN ZERO-NETWORK GUARANTEE TESTS
# =====================================================================


class TestWorkflowDryRun:
    """Test suite for dry-run verification and zero-network execution guarantee."""

    def test_dry_run_zero_network_calls_guarantee(self) -> None:
        """Verify dry-run performs ZERO network calls even with valid steps."""

        def failing_transport(request: httpx.Request) -> httpx.Response:
            pytest.fail(
                f"Network call made during dry-run: {request.method} {request.url}"
            )

        executor = RequestExecutor(transport=httpx.MockTransport(failing_transport))
        service = WorkflowExecutionService(executor=executor)
        wf = _make_sample_workflow()

        env = Environment(
            name="development",
            variables={"base_url": "http://localhost:8080"},
        )

        report = service.dry_run_workflow(
            workflow=wf,
            environment=env,
            allow_mutation=True,
            confirm_isolated_env=True,
        )

        assert report.workflow_id == "wf_product_lifecycle"
        assert report.eligible_to_execute is True
        assert len(report.planned_setup_steps) == 1
        assert len(report.planned_test_steps) == 1
        assert len(report.planned_cleanup_steps) == 1
        assert report.missing_variables == []

    def test_dry_run_detects_missing_variables(self) -> None:
        """Verify dry-run flags required variables that have no producer or binding."""
        # Step requires undefined {{tenant_id}}
        step = _make_step(
            step_number=1,
            name="Tenant Test",
            action_type="TEST",
            url="{{base_url}}/api/items/{{tenant_id}}",
        )
        wf = StatefulWorkflow(
            workflow_id="wf_missing",
            title="Missing Var WF",
            description="",
            setup_steps=[],
            test_steps=[step],
            cleanup_steps=[],
        )

        env = Environment(
            name="test",
            variables={"base_url": "http://localhost:8080"},
        )

        service = WorkflowExecutionService()
        report = service.dry_run_workflow(workflow=wf, environment=env)

        assert report.eligible_to_execute is False
        assert "tenant_id" in report.missing_variables
        assert any("tenant_id" in r for r in report.ineligibility_reasons)

    def test_dry_run_production_environment_fence(self) -> None:
        """Verify dry-run blocks execution against production environments."""
        wf = _make_sample_workflow()
        for prod_env in ("production", "prod", "live"):
            env = Environment(
                name=prod_env,
                variables={"base_url": "https://api.stockpile.com"},
            )
            service = WorkflowExecutionService()
            report = service.dry_run_workflow(
                workflow=wf,
                environment=env,
                allow_mutation=True,
                confirm_isolated_env=True,
            )
            assert report.eligible_to_execute is False
            assert any("PRODUCTION" in r for r in report.ineligibility_reasons)

    def test_dry_run_missing_isolated_env_confirmation(self) -> None:
        """Verify dry-run blocks mutating workflow if isolated env is not confirmed."""
        wf = _make_sample_workflow()
        env = Environment(
            name="staging",
            variables={"base_url": "http://staging.local"},
        )
        service = WorkflowExecutionService()
        report = service.dry_run_workflow(
            workflow=wf,
            environment=env,
            allow_mutation=True,
            confirm_isolated_env=False,  # NOT confirmed
        )
        assert report.eligible_to_execute is False
        assert any("confirm-isolated-env" in r for r in report.ineligibility_reasons)

    def test_dry_run_secret_redaction(self) -> None:
        """Verify secret strings are redacted in dry-run step representations."""
        secret_token = "secret_tok_998877"
        step = _make_step(
            step_number=1,
            name="Auth Test",
            action_type="TEST",
            url=f"{{{{base_url}}}}/api/profile?token={secret_token}",
        )
        wf = StatefulWorkflow(
            workflow_id="wf_secret",
            title="Secret Masking WF",
            description="",
            setup_steps=[],
            test_steps=[step],
            cleanup_steps=[],
        )
        env = Environment(
            name="dev",
            variables={"base_url": "http://localhost:8080", "api_token": secret_token},
            secrets=["api_token"],
        )
        service = WorkflowExecutionService()
        report = service.dry_run_workflow(workflow=wf, environment=env)
        # Check that the secret token is not exposed in the planned URL
        test_step_preview = report.planned_test_steps[0]
        assert secret_token not in test_step_preview["url"]
        assert "***" in test_step_preview["url"]


# =====================================================================
# 4. WORKFLOW EXECUTION & LIFECYCLE TESTS
# =====================================================================


class TestWorkflowExecution:
    """Test suite for autonomous stateful workflow execution."""

    def test_read_only_workflow_passes(self) -> None:
        """Verify pure GET workflow passes without requiring mutation authorization."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            return httpx.Response(200, json={"items": [{"id": 1, "name": "Tool"}]})

        executor = RequestExecutor(
            transport=httpx.MockTransport(handler),
            policy=TargetAccessPolicy(
                allow_private_networks=False,
                allowed_targets=["http://localhost:8080"],
            ),
        )
        service = WorkflowExecutionService(executor=executor)

        step = _make_step(
            step_number=1,
            name="Get Items List",
            action_type="TEST",
            method="GET",
            url="http://localhost:8080/api/items",
            assertions=[{"type": "status", "equals": 200}],
        )
        wf = StatefulWorkflow(
            workflow_id="wf_read_only",
            title="Read Only Test",
            description="",
            setup_steps=[],
            test_steps=[step],
            cleanup_steps=[],
        )

        res = service.execute_workflow(
            workflow=wf,
            allow_mutation=False,
            confirm_isolated_env=False,
        )

        assert res.status == CaseStatus.PASSED
        assert len(res.test_results) == 1
        assert res.test_results[0].status == CaseStatus.PASSED
        assert len(res.setup_results) == 0
        assert len(res.cleanup_results) == 0

    def test_full_stateful_lifecycle_success(self) -> None:
        """Verify Setup POST -> Extract -> Test GET -> LIFO Cleanup DELETE succeeds."""
        requests_log: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            method = request.method
            url = str(request.url)
            requests_log.append((method, url))

            if method == "POST" and url == "http://localhost:8080/api/categories":
                return httpx.Response(201, json={"id": "cat_99", "name": "Electronics"})
            elif (
                method == "GET" and url == "http://localhost:8080/api/categories/cat_99"
            ):
                return httpx.Response(200, json={"id": "cat_99", "name": "Electronics"})
            elif (
                method == "DELETE"
                and url == "http://localhost:8080/api/categories/cat_99"
            ):
                return httpx.Response(204)
            return httpx.Response(404, json={"error": "Not Found"})

        executor = RequestExecutor(
            transport=httpx.MockTransport(handler),
            policy=TargetAccessPolicy(
                allow_private_networks=False,
                allowed_targets=["http://localhost:8080"],
            ),
        )
        service = WorkflowExecutionService(executor=executor)
        wf = _make_sample_workflow()

        env = Environment(
            name="test",
            variables={"base_url": "http://localhost:8080"},
        )

        result = service.execute_workflow(
            workflow=wf,
            environment=env,
            allow_mutation=True,
            confirm_isolated_env=True,
        )

        assert result.status == CaseStatus.PASSED
        assert len(result.setup_results) == 1
        assert result.setup_results[0].status == CaseStatus.PASSED
        assert result.setup_results[0].extracted_variables == {"categoryId": "cat_99"}

        assert len(result.test_results) == 1
        assert result.test_results[0].status == CaseStatus.PASSED

        assert len(result.cleanup_results) == 1
        assert result.cleanup_results[0].status == CaseStatus.PASSED

        # Verify created resources tracking
        assert len(result.created_resources) == 1
        assert result.created_resources[0]["resource_id"] == "cat_99"

        # Verify exact request order
        assert requests_log == [
            ("POST", "http://localhost:8080/api/categories"),
            ("GET", "http://localhost:8080/api/categories/cat_99"),
            ("DELETE", "http://localhost:8080/api/categories/cat_99"),
        ]

    def test_setup_failure_blocks_test_step(self) -> None:
        """Verify failed setup step marks subsequent test steps SKIPPED and does not run them."""
        requests_log: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests_log.append(f"{request.method} {request.url}")
            if request.method == "POST":
                # Simulate setup error (e.g. database error on target)
                return httpx.Response(500, json={"error": "Database locked"})
            return httpx.Response(200)

        executor = RequestExecutor(
            transport=httpx.MockTransport(handler),
            policy=TargetAccessPolicy(
                allow_private_networks=False,
                allowed_targets=["http://localhost:8080"],
            ),
        )
        service = WorkflowExecutionService(executor=executor)
        wf = _make_sample_workflow()

        env = Environment(
            name="test",
            variables={"base_url": "http://localhost:8080"},
        )

        result = service.execute_workflow(
            workflow=wf,
            environment=env,
            allow_mutation=True,
            confirm_isolated_env=True,
        )

        # Setup step failed
        assert result.setup_results[0].status == CaseStatus.FAILED
        # Test step was SKIPPED without sending network request
        assert result.test_results[0].status == CaseStatus.SKIPPED
        assert "setup failed" in str(result.test_results[0].skip_reason).lower()
        # Workflow verdict is FAILED
        assert result.status == CaseStatus.FAILED
        # Only POST was dispatched
        assert requests_log == ["POST http://localhost:8080/api/categories"]

    def test_extraction_failure_fails_closed(self) -> None:
        """Verify failure to extract ID from setup response does not fabricate an ID."""

        def handler(_request: httpx.Request) -> httpx.Response:
            # 201 OK but payload does NOT contain the expected 'id' field
            return httpx.Response(201, json={"status": "created_without_id"})

        executor = RequestExecutor(
            transport=httpx.MockTransport(handler),
            policy=TargetAccessPolicy(
                allow_private_networks=False,
                allowed_targets=["http://localhost:8080"],
            ),
        )
        service = WorkflowExecutionService(executor=executor)
        wf = _make_sample_workflow()

        env = Environment(
            name="test",
            variables={"base_url": "http://localhost:8080"},
        )

        result = service.execute_workflow(
            workflow=wf,
            environment=env,
            allow_mutation=True,
            confirm_isolated_env=True,
        )

        # Setup failed because required extraction failed
        assert result.setup_results[0].status == CaseStatus.ERROR
        assert len(result.setup_results[0].extraction_errors) > 0
        # Test step is SKIPPED
        assert result.test_results[0].status == CaseStatus.SKIPPED

    def test_test_assertion_failure_still_cleans_up(self) -> None:
        """Verify assertion failure in test preserves failure evidence but STILL attempts cleanup."""
        requests_log: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests_log.append(f"{request.method} {request.url}")
            if request.method == "POST":
                return httpx.Response(201, json={"id": "cat_test_fail"})
            elif request.method == "GET":
                # Returns 500 when test expected 200
                return httpx.Response(500, json={"error": "Internal Error"})
            elif request.method == "DELETE":
                return httpx.Response(204)
            return httpx.Response(404)

        executor = RequestExecutor(
            transport=httpx.MockTransport(handler),
            policy=TargetAccessPolicy(
                allow_private_networks=False,
                allowed_targets=["http://localhost:8080"],
            ),
        )
        service = WorkflowExecutionService(executor=executor)
        wf = _make_sample_workflow()

        env = Environment(
            name="test",
            variables={"base_url": "http://localhost:8080"},
        )

        result = service.execute_workflow(
            workflow=wf,
            environment=env,
            allow_mutation=True,
            confirm_isolated_env=True,
        )

        # Setup passed
        assert result.setup_results[0].status == CaseStatus.PASSED
        # Test step failed assertions
        assert result.test_results[0].status == CaseStatus.FAILED
        # Cleanup step STILL ran successfully
        assert result.cleanup_results[0].status == CaseStatus.PASSED
        # Workflow verdict is FAILED
        assert result.status == CaseStatus.FAILED
        # Verify cleanup DELETE was executed
        assert any(r.startswith("DELETE") for r in requests_log)

    def test_cleanup_failure_marks_verdict_cleanup_failed(self) -> None:
        """Verify failed cleanup DELETE produces CLEANUP_FAILED verdict."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(201, json={"id": "cat_cf"})
            elif request.method == "GET":
                return httpx.Response(200, json={"id": "cat_cf"})
            elif request.method == "DELETE":
                # Server fails cleanup
                return httpx.Response(500, json={"error": "Cannot delete"})
            return httpx.Response(404)

        executor = RequestExecutor(
            transport=httpx.MockTransport(handler),
            policy=TargetAccessPolicy(
                allow_private_networks=False,
                allowed_targets=["http://localhost:8080"],
            ),
        )
        service = WorkflowExecutionService(executor=executor)
        wf = _make_sample_workflow()

        env = Environment(
            name="test",
            variables={"base_url": "http://localhost:8080"},
        )

        result = service.execute_workflow(
            workflow=wf,
            environment=env,
            allow_mutation=True,
            confirm_isolated_env=True,
        )

        assert result.setup_results[0].status == CaseStatus.PASSED
        assert result.test_results[0].status == CaseStatus.PASSED
        assert result.cleanup_results[0].status == CaseStatus.CLEANUP_FAILED
        assert result.status == CaseStatus.CLEANUP_FAILED

    def test_lifo_cleanup_reverse_order(self) -> None:
        """Verify cleanup deletes dependent child resources before parent resources (LIFO)."""
        deleted_order: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and "suppliers" in str(request.url):
                return httpx.Response(201, json={"id": "sup_1"})
            elif request.method == "POST" and "products" in str(request.url):
                return httpx.Response(201, json={"id": "prod_1"})
            elif request.method == "GET":
                return httpx.Response(200, json={"status": "ok"})
            elif request.method == "DELETE":
                deleted_order.append(str(request.url))
                return httpx.Response(204)
            return httpx.Response(404)

        setup_sup = _make_step(
            step_number=1,
            name="Create Supplier",
            action_type="SETUP",
            method="POST",
            url="http://localhost:8080/api/suppliers",
            extract={"supplierId": "$.id"},
            assertions=[{"type": "status", "equals": 201}],
            risk="risk:mutating",
        )
        setup_prod = _make_step(
            step_number=2,
            name="Create Product",
            action_type="SETUP",
            method="POST",
            url="http://localhost:8080/api/products",
            body={"supplierId": "{{supplierId}}"},
            extract={"productId": "$.id"},
            assertions=[{"type": "status", "equals": 201}],
            risk="risk:mutating",
        )
        test_step = _make_step(
            step_number=3,
            name="Test Product",
            action_type="TEST",
            url="http://localhost:8080/api/products/{{productId}}",
            assertions=[{"type": "status", "equals": 200}],
            risk="risk:read_only",
        )
        cleanup_sup = _make_step(
            step_number=4,
            name="Delete Supplier",
            action_type="CLEANUP",
            method="DELETE",
            url="http://localhost:8080/api/suppliers/{{supplierId}}",
            assertions=[{"type": "status", "equals": 204}],
            risk="risk:destructive",
            rollback_endpoint="{{supplierId}}",
        )
        cleanup_prod = _make_step(
            step_number=5,
            name="Delete Product",
            action_type="CLEANUP",
            method="DELETE",
            url="http://localhost:8080/api/products/{{productId}}",
            assertions=[{"type": "status", "equals": 204}],
            risk="risk:destructive",
            rollback_endpoint="{{productId}}",
        )

        wf = StatefulWorkflow(
            workflow_id="wf_lifo",
            title="LIFO Hierarchy WF",
            description="",
            setup_steps=[setup_sup, setup_prod],
            test_steps=[test_step],
            cleanup_steps=[cleanup_sup, cleanup_prod],
            provided_fixtures=["supplierId", "productId"],
        )

        executor = RequestExecutor(
            transport=httpx.MockTransport(handler),
            policy=TargetAccessPolicy(
                allow_private_networks=False,
                allowed_targets=["http://localhost:8080"],
            ),
        )
        service = WorkflowExecutionService(executor=executor)
        result = service.execute_workflow(
            workflow=wf,
            allow_mutation=True,
            confirm_isolated_env=True,
        )

        assert result.status == CaseStatus.PASSED
        # Product MUST be deleted before Supplier
        assert deleted_order == [
            "http://localhost:8080/api/products/prod_1",
            "http://localhost:8080/api/suppliers/sup_1",
        ]

    def test_preexisting_fixtures_never_cleaned_up(self) -> None:
        """Verify resources passed as static fixtures or environment variables are NEVER deleted."""
        deleted_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "DELETE":
                deleted_urls.append(str(request.url))
                return httpx.Response(204)
            return httpx.Response(200, json={"id": "item_ok"})

        test_step = _make_step(
            step_number=1,
            name="Test Existing",
            action_type="TEST",
            url="http://localhost:8080/api/products/{{productId}}",
            assertions=[{"type": "status", "equals": 200}],
        )
        cleanup_step = _make_step(
            step_number=2,
            name="Cleanup Attempt",
            action_type="CLEANUP",
            method="DELETE",
            url="http://localhost:8080/api/products/{{productId}}",
            rollback_endpoint="{{productId}}",
        )

        wf = StatefulWorkflow(
            workflow_id="wf_fixture_protection",
            title="Protect User Fixture",
            description="",
            setup_steps=[],  # No setup created this!
            test_steps=[test_step],
            cleanup_steps=[cleanup_step],
        )

        executor = RequestExecutor(
            transport=httpx.MockTransport(handler),
            policy=TargetAccessPolicy(
                allow_private_networks=False,
                allowed_targets=["http://localhost:8080"],
            ),
        )
        service = WorkflowExecutionService(executor=executor)

        # productId is pre-existing in static_fixtures
        result = service.execute_workflow(
            workflow=wf,
            static_fixtures={"productId": "static_prod_99"},
            allow_mutation=True,
            confirm_isolated_env=True,
        )

        assert result.status == CaseStatus.PASSED
        # Zero DELETE requests sent because static_prod_99 was not created by this workflow
        assert len(deleted_urls) == 0

    def test_unauthorized_mutation_fails_closed(self) -> None:
        """Verify attempting mutating setup without allow_mutation fails closed."""
        wf = _make_sample_workflow()
        service = WorkflowExecutionService()

        env = Environment(
            name="test",
            variables={"base_url": "http://localhost:8080"},
        )

        result = service.execute_workflow(
            workflow=wf,
            environment=env,
            allow_mutation=False,  # NOT authorized
            confirm_isolated_env=True,
        )

        assert result.status == CaseStatus.BLOCKED
        assert "not authorized" in str(result.error_message)

    def test_production_environment_execution_fails_closed(self) -> None:
        """Verify execute_workflow against production fails closed with BLOCKED status."""
        wf = _make_sample_workflow()
        service = WorkflowExecutionService()
        env = Environment(
            name="production",
            variables={"base_url": "https://production.api"},
        )

        result = service.execute_workflow(
            workflow=wf,
            environment=env,
            allow_mutation=True,
            confirm_isolated_env=True,
        )
        assert result.status == CaseStatus.BLOCKED
        assert "PRODUCTION" in str(result.error_message)

    def test_multi_workflow_runtime_variable_isolation(self) -> None:
        """Verify execute_workflows isolates variables so workflow B cannot access workflow A's variables."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(201, json={"token": "tok_wf1"})
            return httpx.Response(200, json={"status": "ok"})

        executor = RequestExecutor(
            transport=httpx.MockTransport(handler),
            policy=TargetAccessPolicy(
                allow_private_networks=False,
                allowed_targets=["http://localhost:8080"],
            ),
        )
        service = WorkflowExecutionService(executor=executor)

        # Workflow A creates token
        setup_a = _make_step(
            step_number=1,
            name="Setup A",
            action_type="SETUP",
            method="POST",
            url="http://localhost:8080/api/auth",
            extract={"wf_token": "$.token"},
            assertions=[{"type": "status", "equals": 201}],
            risk="risk:mutating",
        )
        test_a = _make_step(
            step_number=2,
            name="Test A",
            action_type="TEST",
            url="http://localhost:8080/api/data?token={{wf_token}}",
            assertions=[{"type": "status", "equals": 200}],
        )
        wf_a = StatefulWorkflow(
            workflow_id="wf_a",
            title="Workflow A",
            description="",
            setup_steps=[setup_a],
            test_steps=[test_a],
            cleanup_steps=[],
            provided_fixtures=["wf_token"],
        )

        # Workflow B tries to use {{wf_token}} without setting it
        test_b = _make_step(
            step_number=1,
            name="Test B",
            action_type="TEST",
            url="http://localhost:8080/api/data?token={{wf_token}}",
            assertions=[{"type": "status", "equals": 200}],
        )
        wf_b = StatefulWorkflow(
            workflow_id="wf_b",
            title="Workflow B",
            description="",
            setup_steps=[],
            test_steps=[test_b],
            cleanup_steps=[],
        )

        wf_results, run_result = service.execute_workflows(
            workflows=[wf_a, wf_b],
            allow_mutation=True,
            confirm_isolated_env=True,
        )

        # Workflow A passed
        assert wf_results[0].status == CaseStatus.PASSED
        # Workflow B was BLOCKED because {{wf_token}} was not leaked to it
        assert wf_results[1].status == CaseStatus.BLOCKED
        assert "wf_token" in str(wf_results[1].test_results[0].skip_reason)
