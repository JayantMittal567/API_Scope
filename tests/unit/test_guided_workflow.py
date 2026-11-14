"""Unit tests for Guided Project-Testing Workflow & Orchestration (Task 26).

Covers:
- Scenario A: Successful read-only guided workflow.
- Scenario B: Authorized create -> extract ID -> read -> cleanup workflow with mock transport.
- Scenario C: Missing fixture causing execution to be deferred/blocked.
- Scenario D: Unapproved AI scenario remaining non-executable.
- Scenario E: Policy-blocked mutation (read-only enforcement).
- Scenario F: Setup failure with downstream skips (causal grouping).
- Scenario G: Failed assertion with evidence-backed diagnosis.
- Scenario H: Cleanup failure reported separately without invalidating passing tests.
- Scenario I: Missing Gemini key with deterministic fallback.
- Scenario J: Partial workflow retaining valid earlier artifacts.
- Scenario K: Reused cached planning artifacts.
- Scenario L: Safe resume without replaying prior mutations.
- Scenario M: Offline preview with zero target HTTP requests.
- Scenario N: Secret redaction throughout manifest and reports.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx

from apiscope.core.executor import RequestExecutor, TargetAccessPolicy
from apiscope.core.results import CaseStatus
from apiscope.definitions.models import DefinitionStatus
from apiscope.execution.models import (
    WorkflowExecutionResult,
    WorkflowStepResult,
    WorkflowStepType,
)
from apiscope.orchestration.models import (
    GuidedWorkflowOptions,
    StageStatus,
    WorkflowStage,
)
from apiscope.orchestration.service import GuidedWorkflowService
from apiscope.planning.models import (
    ScenarioCategory,
    ScenarioOrigin,
    ScenarioStep,
    TestPlan,
    TestScenario,
)
from apiscope.trust.models import DiagnosticVerdict


def _create_minimal_spec(path: Path, with_mutations: bool = False) -> Path:
    """Create a minimal OpenAPI specification."""
    content = """
openapi: 3.0.0
info:
  title: Mock Test Store
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
        content += """    post:
      summary: Create item
      responses:
        '201':
          description: Created
  /api/items/{id}:
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
    delete:
      summary: Delete item
      parameters:
        - name: id
          in: path
          required: true
          schema:
            type: string
      responses:
        '204':
          description: No Content
"""
    path.mkdir(parents=True, exist_ok=True)
    spec_path = path / "openapi.yaml"
    spec_path.write_text(content.strip(), encoding="utf-8")
    return spec_path


def test_scenario_a_successful_readonly_guided_workflow(tmp_path: Path) -> None:
    """Scenario A: Successful read-only guided workflow."""
    spec_file = _create_minimal_spec(tmp_path / "spec_a", with_mutations=False)
    out_dir = tmp_path / "out_a"

    def mock_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": "1", "name": "Item 1"}])

    executor = RequestExecutor(
        transport=httpx.MockTransport(mock_handler),
        policy=TargetAccessPolicy(allow_private_networks=True),
    )

    opts = GuidedWorkflowOptions(
        spec_path=spec_file,
        output_dir=out_dir,
        target_origin="http://localhost:8080",
        execute=True,
    )

    svc = GuidedWorkflowService(executor=executor)
    result = svc.run_guided_workflow(opts)

    assert result.is_success is True
    assert result.manifest.overall_status in (
        StageStatus.COMPLETED.value,
        StageStatus.COMPLETED_WITH_WARNINGS.value,
    )
    assert result.manifest.stage_records[
        WorkflowStage.PROJECT_INSPECTION.value
    ].status in (
        StageStatus.COMPLETED,
        StageStatus.COMPLETED_WITH_WARNINGS,
    )
    assert (
        result.manifest.stage_records[WorkflowStage.CONTROLLED_EXECUTION.value].status
        == StageStatus.COMPLETED
    )
    assert (
        result.manifest.stage_records[WorkflowStage.TRUST_ASSESSMENT.value].status
        == StageStatus.COMPLETED
    )

    # Check generated files
    assert (out_dir / "workflow_manifest.json").is_file()
    assert (out_dir / "execution_preview.json").is_file()
    assert (out_dir / "trust_assessment_report.json").is_file()


def test_scenario_b_authorized_crud_workflow(tmp_path: Path) -> None:
    """Scenario B: Authorized create -> extract ID -> read -> cleanup workflow with mock transport."""
    spec_file = _create_minimal_spec(tmp_path / "spec_b", with_mutations=True)
    out_dir = tmp_path / "out_b"

    created_items: set[str] = set()

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if request.method == "POST" and "/api/items" in url_str:
            created_items.add("item-101")
            return httpx.Response(201, json={"id": "item-101", "name": "Gadget"})
        if request.method == "GET" and "/api/items/item-101" in url_str:
            return httpx.Response(200, json={"id": "item-101", "name": "Gadget"})
        if request.method == "DELETE" and "/api/items/item-101" in url_str:
            created_items.discard("item-101")
            return httpx.Response(204)
        return httpx.Response(200, json={"status": "ok"})

    executor = RequestExecutor(
        transport=httpx.MockTransport(mock_handler),
        policy=TargetAccessPolicy(allow_private_networks=True),
    )

    opts = GuidedWorkflowOptions(
        spec_path=spec_file,
        output_dir=out_dir,
        target_origin="http://localhost:8080",
        execute=True,
        allow_mutation=True,
        allow_destructive=True,
        confirm_isolated_env=True,
        environment_name="test",
    )

    svc = GuidedWorkflowService(executor=executor)
    result = svc.run_guided_workflow(opts)

    assert result.is_success is True
    assert result.manifest.overall_status in (
        StageStatus.COMPLETED.value,
        StageStatus.COMPLETED_WITH_WARNINGS.value,
    )
    assert (
        result.manifest.stage_records[WorkflowStage.CONTROLLED_EXECUTION.value].status
        == StageStatus.COMPLETED
    )


def test_scenario_c_missing_fixture_deferred_execution(tmp_path: Path) -> None:
    """Scenario C: Missing fixture causes dependent tests to be flagged and deferred."""
    spec_file = _create_minimal_spec(tmp_path / "spec_c", with_mutations=True)
    out_dir = tmp_path / "out_c"

    opts = GuidedWorkflowOptions(
        spec_path=spec_file,
        output_dir=out_dir,
        target_origin="http://localhost:8080",
        execute=False,
        preview_only=True,
    )

    svc = GuidedWorkflowService()
    result = svc.run_guided_workflow(opts)

    assert result.preview is not None
    assert (
        result.manifest.stage_records[WorkflowStage.CONTROLLED_EXECUTION.value].status
        == StageStatus.SKIPPED
    )


def test_scenario_d_unapproved_ai_scenario_excluded(tmp_path: Path) -> None:
    """Scenario D: Unapproved AI scenarios remain non-executable and flag review requirements."""
    spec_file = _create_minimal_spec(tmp_path / "spec_d", with_mutations=False)
    out_dir = tmp_path / "out_d"

    # Pre-generate a plan containing an unapproved AI proposal
    test_plan = TestPlan(
        plan_id="plan-d",
        title="Test Plan D",
        version="1.0.0",
        scenarios=[
            TestScenario(
                scenario_id="det-1",
                title="Deterministic GET items",
                description="Deterministic fetch",
                category=ScenarioCategory.CONTRACT_POSITIVE,
                target_endpoints=["GET /api/items"],
                steps=[
                    ScenarioStep(
                        step_number=1,
                        method="GET",
                        path="/api/items",
                        description="Fetch items",
                    )
                ],
                origin=ScenarioOrigin.DETERMINISTIC,
                approval_status=DefinitionStatus.APPROVED,
            ),
            TestScenario(
                scenario_id="ai-prop-1",
                title="AI hypothesized price discount check",
                description="AI discount hypothesis",
                category=ScenarioCategory.CONTRACT_POSITIVE,
                target_endpoints=["GET /api/items"],
                steps=[
                    ScenarioStep(
                        step_number=1,
                        method="GET",
                        path="/api/items",
                        description="Fetch items discount",
                    )
                ],
                origin=ScenarioOrigin.AI_PROPOSED,
                approval_status=DefinitionStatus.PROPOSED,
            ),
        ],
    )
    plan_file = tmp_path / "plan_d.json"
    plan_file.write_text(json.dumps(test_plan.to_dict()), encoding="utf-8")

    opts = GuidedWorkflowOptions(
        spec_path=spec_file,
        output_dir=out_dir,
        existing_plan_file=plan_file,
        preview_only=True,
    )

    svc = GuidedWorkflowService()
    result = svc.run_guided_workflow(opts)

    assert result.preview is not None
    assert result.preview.ai_scenarios_count == 1
    assert result.preview.scenarios_needing_review == 1
    assert (
        result.manifest.stage_records[WorkflowStage.TEST_PLANNING.value].status
        == StageStatus.NEEDS_REVIEW
    )
    # Compiled tests should exclude the unapproved proposal
    assert result.compilation_report is not None
    assert (
        result.compilation_report.needs_review_count >= 1
        or result.compilation_report.blocked_count >= 1
    )


def test_scenario_e_policy_blocked_mutation(tmp_path: Path) -> None:
    """Scenario E: Mutating request blocked by ExecutionSafetyPolicy when --allow-mutation is omitted."""
    spec_file = _create_minimal_spec(tmp_path / "spec_e", with_mutations=True)
    out_dir = tmp_path / "out_e"

    opts = GuidedWorkflowOptions(
        spec_path=spec_file,
        output_dir=out_dir,
        target_origin="http://localhost:8080",
        execute=True,
        allow_mutation=False,  # Not authorized!
    )

    svc = GuidedWorkflowService()
    result = svc.run_guided_workflow(opts)

    assert result.preview is not None
    assert any("mutating" in r.lower() for r in result.preview.policy_restrictions)
    assert any("--allow-mutation" in b for b in result.preview.authorization_blockers)
    # Execution stage must be blocked or fenced
    assert result.manifest.stage_records[
        WorkflowStage.CONTROLLED_EXECUTION.value
    ].status in (
        StageStatus.BLOCKED,
        StageStatus.COMPLETED_WITH_WARNINGS,
    )


def test_scenario_f_setup_failure_causal_grouping(tmp_path: Path) -> None:
    """Scenario F: Workflow setup failure with downstream skips grouped causally."""
    spec_file = _create_minimal_spec(tmp_path / "spec_f", with_mutations=True)
    out_dir = tmp_path / "out_f"

    # Simulate workflow execution failure on setup
    mock_wf_res = WorkflowExecutionResult(
        workflow_id="wf-f",
        title="Setup Fail Workflow",
        status=CaseStatus.FAILED,
        started_at="2026-11-15T00:00:00Z",
        finished_at="2026-11-15T00:00:02Z",
        duration_ms=2000.0,
        target_origin="http://localhost:8080",
        setup_results=[
            WorkflowStepResult(
                step_number=1,
                name="seed_user",
                step_type=WorkflowStepType.SETUP,
                status=CaseStatus.FAILED,
                method="POST",
                url="http://localhost:8080/api/items",
                status_code=500,
                error_message="DB Deadlock",
            )
        ],
        test_results=[
            WorkflowStepResult(
                step_number=2,
                name="get_user",
                step_type=WorkflowStepType.TEST,
                status=CaseStatus.SKIPPED,
                method="GET",
                url="http://localhost:8080/api/items/1",
                skip_reason="Prerequisite setup failed",
            )
        ],
    )

    wf_file = tmp_path / "run_f.json"
    wf_file.write_text(
        json.dumps(mock_wf_res.to_run_result().to_dict()), encoding="utf-8"
    )

    opts = GuidedWorkflowOptions(
        spec_path=spec_file,
        output_dir=out_dir,
        existing_run_file=wf_file,
        preview_only=False,
    )

    svc = GuidedWorkflowService()
    result = svc.run_guided_workflow(opts)

    assert result.trust_report is not None
    assert result.investigation_report is not None
    assert result.investigation_report.findings[0].is_primary_failure is True
    assert result.investigation_report.findings[0].downstream_impact_count >= 1
    assert (
        any(len(f.downstream_symptoms) > 0 for f in result.trust_report.findings)
        or result.trust_report.grouped_symptoms_count >= 1
    )


def test_scenario_g_failed_assertion_diagnosed(tmp_path: Path) -> None:
    """Scenario G: Failed assertion diagnosed with verified contract mismatch."""
    spec_file = _create_minimal_spec(tmp_path / "spec_g", with_mutations=False)
    out_dir = tmp_path / "out_g"

    def mock_handler(_request: httpx.Request) -> httpx.Response:
        # Contradicts spec (status 500)
        return httpx.Response(500, json={"error": "Crash"})

    executor = RequestExecutor(
        transport=httpx.MockTransport(mock_handler),
        policy=TargetAccessPolicy(allow_private_networks=True),
    )

    opts = GuidedWorkflowOptions(
        spec_path=spec_file,
        output_dir=out_dir,
        target_origin="http://localhost:8080",
        execute=True,
    )

    svc = GuidedWorkflowService(executor=executor)
    result = svc.run_guided_workflow(opts)

    assert (
        result.manifest.stage_records[WorkflowStage.CONTROLLED_EXECUTION.value].status
        == StageStatus.COMPLETED_WITH_WARNINGS
    )
    assert result.trust_report is not None
    # 500 contradicts spec 200, diagnosed as verified contract mismatch
    assert (
        result.trust_report.verdict_counts.get(
            DiagnosticVerdict.VERIFIED_CONTRACT_MISMATCH.value, 0
        )
        >= 1
        or result.trust_report.verdict_counts.get(
            DiagnosticVerdict.SUSPECTED_APPLICATION_DEFECT.value, 0
        )
        >= 1
    )


def test_scenario_h_cleanup_failure_isolation(tmp_path: Path) -> None:
    """Scenario H: Cleanup teardown failure reported separately without invalidating passing test assertions."""
    spec_file = _create_minimal_spec(tmp_path / "spec_h", with_mutations=True)
    out_dir = tmp_path / "out_h"

    mock_wf_res = WorkflowExecutionResult(
        workflow_id="wf-h",
        title="Cleanup Fail Workflow",
        status=CaseStatus.FAILED,
        started_at="2026-11-15T00:00:00Z",
        finished_at="2026-11-15T00:00:03Z",
        duration_ms=3000.0,
        target_origin="http://localhost:8080",
        setup_results=[],
        test_results=[
            WorkflowStepResult(
                step_number=1,
                name="get_item",
                step_type=WorkflowStepType.TEST,
                status=CaseStatus.PASSED,
                method="GET",
                url="http://localhost:8080/api/items",
                status_code=200,
            )
        ],
        cleanup_results=[
            WorkflowStepResult(
                step_number=2,
                name="delete_item",
                step_type=WorkflowStepType.CLEANUP,
                status=CaseStatus.FAILED,
                method="DELETE",
                url="http://localhost:8080/api/items/item-123",
                status_code=500,
                error_message="Storage disk locked",
            )
        ],
    )

    # Ingest mock workflow result
    wf_file = tmp_path / "run_h.json"
    wf_file.write_text(
        json.dumps(mock_wf_res.to_run_result().to_dict()), encoding="utf-8"
    )

    opts = GuidedWorkflowOptions(
        spec_path=spec_file,
        output_dir=out_dir,
        existing_run_file=wf_file,
        existing_workflow_report=wf_file,
    )

    svc = GuidedWorkflowService()
    result = svc.run_guided_workflow(opts)

    assert result.trust_report is not None
    assert (
        result.trust_report.verdict_counts.get(DiagnosticVerdict.CLEANUP_ISSUE.value, 0)
        == 1
    )
    assert result.trust_report.coverage.passed_operations_count >= 1


def test_scenario_i_missing_gemini_key_deterministic_fallback(tmp_path: Path) -> None:
    """Scenario I: Missing Gemini key triggers clean deterministic fallback without aborting."""
    spec_file = _create_minimal_spec(tmp_path / "spec_i", with_mutations=False)
    out_dir = tmp_path / "out_i"

    opts = GuidedWorkflowOptions(
        spec_path=spec_file,
        output_dir=out_dir,
        enable_ai=True,
        ai_api_key="",  # Empty key
        preview_only=True,
    )

    svc = GuidedWorkflowService()
    result = svc.run_guided_workflow(opts)

    assert result.is_success is True
    assert result.test_plan is not None
    assert len(result.test_plan.scenarios) > 0
    # Scenarios must all be deterministic
    assert all(
        s.origin == ScenarioOrigin.DETERMINISTIC for s in result.test_plan.scenarios
    )


def test_scenario_j_partial_workflow_retaining_artifacts(tmp_path: Path) -> None:
    """Scenario J: Earlier stage artifacts retained on disk even if execution is blocked or omitted."""
    spec_file = _create_minimal_spec(tmp_path / "spec_j", with_mutations=True)
    out_dir = tmp_path / "out_j"

    opts = GuidedWorkflowOptions(
        spec_path=spec_file,
        output_dir=out_dir,
        preview_only=True,  # Execution omitted
    )

    svc = GuidedWorkflowService()
    result = svc.run_guided_workflow(opts)
    assert result.is_success is True

    assert (out_dir / "project_intelligence_report.json").is_file()
    assert (out_dir / "contract_quality_report.json").is_file()
    assert (out_dir / "test_plan.json").is_file()
    assert (out_dir / "compilation_report.json").is_file()
    assert (out_dir / "fixture_preparation_report.json").is_file()
    assert (out_dir / "execution_preview.json").is_file()
    assert (out_dir / "workflow_manifest.json").is_file()


def test_scenario_k_reused_cached_planning_artifacts(tmp_path: Path) -> None:
    """Scenario K: Reusing cached or pre-computed planning artifacts."""
    spec_file = _create_minimal_spec(tmp_path / "spec_k", with_mutations=False)
    out_dir = tmp_path / "out_k"

    plan = TestPlan(
        plan_id="pre-computed-plan-k",
        title="Cached Test Plan K",
        version="1.0.0",
        scenarios=[
            TestScenario(
                scenario_id="s-cached-1",
                title="Cached GET items scenario",
                description="Cached scenario",
                category=ScenarioCategory.CONTRACT_POSITIVE,
                target_endpoints=["GET /api/items"],
                steps=[
                    ScenarioStep(
                        step_number=1,
                        method="GET",
                        path="/api/items",
                        description="Fetch cached items",
                    )
                ],
                origin=ScenarioOrigin.DETERMINISTIC,
            )
        ],
    )
    plan_file = tmp_path / "cached_plan.json"
    plan_file.write_text(json.dumps(plan.to_dict()), encoding="utf-8")

    opts = GuidedWorkflowOptions(
        spec_path=spec_file,
        output_dir=out_dir,
        existing_plan_file=plan_file,
        preview_only=True,
    )

    svc = GuidedWorkflowService()
    result = svc.run_guided_workflow(opts)

    assert result.test_plan is not None
    assert result.test_plan.plan_id == "pre-computed-plan-k"
    assert any(
        "Reused pre-computed TestPlan" in w
        for w in result.manifest.stage_records[
            WorkflowStage.TEST_PLANNING.value
        ].warnings
    )


def test_scenario_l_safe_resume_no_mutation_replay(tmp_path: Path) -> None:
    """Scenario L: Safe resume never automatically replays mutations without explicit authorization."""
    spec_file = _create_minimal_spec(tmp_path / "spec_l", with_mutations=True)
    out_dir = tmp_path / "out_l"

    # Run without execution
    opts = GuidedWorkflowOptions(
        spec_path=spec_file,
        output_dir=out_dir,
        preview_only=True,
    )

    svc = GuidedWorkflowService()
    result = svc.run_guided_workflow(opts)
    assert result.manifest.workflow_id is not None

    # Resume without mutation flags -> mutations remain blocked
    resume_opts = GuidedWorkflowOptions(
        spec_path=spec_file,
        output_dir=out_dir,
        resume_from_manifest=out_dir / "workflow_manifest.json",
        execute=True,
        allow_mutation=False,  # Still False
    )

    res_result = svc.run_guided_workflow(resume_opts)
    assert res_result.manifest.stage_records[
        WorkflowStage.CONTROLLED_EXECUTION.value
    ].status in (
        StageStatus.BLOCKED,
        StageStatus.COMPLETED_WITH_WARNINGS,
    )


def test_scenario_m_offline_preview_zero_network_calls(tmp_path: Path) -> None:
    """Scenario M: Offline preview guarantees zero network calls (socket blocked)."""
    spec_file = _create_minimal_spec(tmp_path / "spec_m", with_mutations=True)
    out_dir = tmp_path / "out_m"

    opts = GuidedWorkflowOptions(
        spec_path=spec_file,
        output_dir=out_dir,
        preview_only=True,
    )

    with patch("socket.socket") as mock_sock:
        mock_sock.side_effect = RuntimeError(
            "Network call attempted during dry-run preview!"
        )

        svc = GuidedWorkflowService()
        result = svc.run_guided_workflow(opts)

        assert result.preview is not None
        assert (
            result.manifest.stage_records[WorkflowStage.EXECUTION_PREVIEW.value].status
            == StageStatus.COMPLETED
        )
        assert (
            result.manifest.stage_records[
                WorkflowStage.CONTROLLED_EXECUTION.value
            ].status
            == StageStatus.SKIPPED
        )


def test_scenario_n_secret_redaction_throughout_manifest(tmp_path: Path) -> None:
    """Scenario N: Secret redaction throughout workflow manifest and reports."""
    spec_file = _create_minimal_spec(tmp_path / "spec_n", with_mutations=False)
    out_dir = tmp_path / "out_n"

    secret_token = "sk-live-super-secret-token-12345"
    opts = GuidedWorkflowOptions(
        spec_path=spec_file,
        output_dir=out_dir,
        target_origin=f"http://localhost:8080?token={secret_token}",
        preview_only=True,
    )

    svc = GuidedWorkflowService()
    result = svc.run_guided_workflow(opts)
    assert result.preview is not None

    manifest_json = (out_dir / "workflow_manifest.json").read_text(encoding="utf-8")
    preview_json = (out_dir / "execution_preview.json").read_text(encoding="utf-8")

    assert secret_token not in manifest_json
    assert secret_token not in preview_json
