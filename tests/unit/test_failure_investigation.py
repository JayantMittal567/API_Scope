"""Unit tests for ApiScope FailureInvestigationService (Task 22).

Covers:
- Deterministic failure triage across all 9 canonical diagnostic categories
- Cases A through H acceptance scenarios
- Causal workflow analysis: primary failure identification vs downstream cascade skips
- Fixture resolution failures, extraction failures, and missing variables
- Cleanup failures evaluated independently from passing main test assertions
- Network transport timeouts, server uncertainty, and connection refusals
- Policy blocks vs HTTP 401/403 responses
- Suspicious HTTP 500 server crashes without invented stack traces
- Secret credential redaction in findings and reports
- Missing evidence gaps identification
- Serialization round-trip (to_dict, to_json, to_yaml, from_dict)
- Optional Gemini AI hypotheses with mock provider (zero external network calls)
- WorkflowExecutionResult direct investigation
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from apiscope.contract.models import (
    CanonicalContract,
    CanonicalOperation,
    CanonicalResponse,
)
from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.execution.models import (
    WorkflowExecutionResult,
    WorkflowStepResult,
    WorkflowStepType,
)
from apiscope.investigation.models import (
    ConfidenceLevel,
    DiagnosticCategory,
    DiagnosticDisposition,
    ExecutionStage,
    FailureInvestigationReport,
    FindingSeverity,
)
from apiscope.investigation.service import FailureInvestigationService


class MockLLMProvider:
    """Mock LLM provider for deterministic offline testing of AI hypotheses."""

    def __init__(
        self,
        response_text: str = '["Downstream DB deadlock", "Null pointer in controller"]',
    ) -> None:
        self.response_text = response_text
        self.prompts: list[str] = []

    def generate(self, prompt: str, max_tokens: int = 600) -> str:
        _ = max_tokens
        self.prompts.append(prompt)
        return self.response_text


@pytest.fixture
def service() -> FailureInvestigationService:
    return FailureInvestigationService()


def _make_run(cases: list[CaseResult], run_id: str = "test-run-001") -> RunResult:
    return RunResult(
        run_id=run_id,
        environment_name="test-env",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        duration_ms=150.0,
        total_tests=len(cases),
        passed_tests=sum(1 for c in cases if c.status == CaseStatus.PASSED),
        failed_tests=sum(1 for c in cases if c.status == CaseStatus.FAILED),
        error_tests=sum(1 for c in cases if c.status == CaseStatus.ERROR),
        skipped_tests=sum(1 for c in cases if c.status == CaseStatus.SKIPPED),
        blocked_tests=sum(1 for c in cases if c.status == CaseStatus.BLOCKED),
        results=cases,
    )


def test_clean_passing_run(service: FailureInvestigationService) -> None:
    """A run with all passing cases produces zero failure findings."""
    cases = [
        CaseResult(
            test_name="GET /api/health",
            module="health",
            tags=["smoke"],
            status=CaseStatus.PASSED,
            verified=True,
            method="GET",
            url="http://localhost:8080/api/health",
            status_code=200,
            elapsed_ms=12.0,
            request_headers={},
            response_headers={},
            assertion_results=[
                {"type": "status", "passed": True, "message": "Expected 200"}
            ],
            extracted_variables={},
            extraction_errors=[],
        )
    ]
    run = _make_run(cases)
    report = service.investigate_run(run)

    assert report.total_cases_evaluated == 1
    assert report.total_failures_detected == 0
    assert report.primary_failures_count == 0
    assert report.downstream_consequences_count == 0
    assert not report.has_application_defects
    assert len(report.findings) == 0
    assert "All 1 test cases executed cleanly" in report.summary


# =========================================================================
# Case A — Genuine contract violation candidate
# =========================================================================
def test_case_a_genuine_contract_violation(
    service: FailureInvestigationService,
) -> None:
    """Case A: Valid request receives response contradicting documented schema/status."""
    contract = CanonicalContract(
        openapi_version="3.0.3",
        title="Store API",
        version="1.0.0",
        operations=[
            CanonicalOperation(
                operation_id="getProduct",
                method="GET",
                path="/api/products/{id}",
                responses={
                    "200": CanonicalResponse(
                        status_code="200", description="Product found"
                    )
                },
            )
        ],
    )

    cases = [
        CaseResult(
            test_name="[WF-PROD] [TEST:1] GET /api/products/101",
            module="products",
            tags=["workflow:wf-prod", "step-type:test"],
            status=CaseStatus.FAILED,
            verified=False,
            method="GET",
            url="http://localhost:8080/api/products/101",
            status_code=200,
            elapsed_ms=15.0,
            request_headers={},
            response_headers={"content-type": "application/json"},
            assertion_results=[
                {
                    "type": "json_schema",
                    "passed": False,
                    "message": "Response body missing required property 'sku'",
                }
            ],
            extracted_variables={},
            extraction_errors=[],
            error_message="Assertion failed: Response body missing required property 'sku'",
        )
    ]

    report = service.investigate_run(_make_run(cases), contract=contract)

    assert report.total_failures_detected == 1
    finding = report.findings[0]
    assert finding.is_primary_failure
    assert finding.category == DiagnosticCategory.CONTRACT_OR_RESPONSE_MISMATCH
    assert finding.confidence == ConfidenceLevel.VERIFIED_OBSERVATION
    assert finding.severity == FindingSeverity.HIGH
    assert finding.disposition == DiagnosticDisposition.CONTRACT_MISMATCH_DETECTED
    assert any("schema constraints" in c for c in finding.candidate_causes)


# =========================================================================
# Case B — Invalid fixture
# =========================================================================
def test_case_b_invalid_fixture(service: FailureInvestigationService) -> None:
    """Case B: Request fails due to missing or unresolved fixture ID, not application bug."""
    cases = [
        CaseResult(
            test_name="[WF-SURPLUS] [TEST:1] GET /api/surplus/{{chain_supplier_id}}",
            module="surplus",
            tags=["workflow:wf-surplus", "step-type:test"],
            status=CaseStatus.BLOCKED,
            verified=False,
            method="GET",
            url="http://localhost:8080/api/surplus/{{chain_supplier_id}}",
            status_code=None,
            elapsed_ms=0.0,
            request_headers={},
            response_headers={},
            assertion_results=[],
            extracted_variables={},
            extraction_errors=[],
            error_message="Cannot execute step: Missing variable 'chain_supplier_id'",
        )
    ]

    report = service.investigate_run(_make_run(cases))

    assert report.total_failures_detected == 1
    finding = report.findings[0]
    assert finding.category == DiagnosticCategory.FIXTURE_OR_DEPENDENCY_ISSUE
    assert finding.disposition == DiagnosticDisposition.FIXTURE_RESOLUTION_REQUIRED
    assert not report.has_application_defects
    assert report.has_test_or_fixture_issues
    assert "Missing variable 'chain_supplier_id'" in finding.observed_symptom


# =========================================================================
# Case C — Setup failure with causal workflow analysis
# =========================================================================
def test_case_c_setup_failure_and_downstream_cascades(
    service: FailureInvestigationService,
) -> None:
    """Case C: Setup step fails; downstream tests skipped are not blamed as separate bugs."""
    cases = [
        # Step 1: Setup fails (HTTP 500)
        CaseResult(
            test_name="[WF-PROD] [SETUP:1] Create Supplier",
            module="workflow",
            tags=["workflow:wf-prod", "step-type:setup"],
            status=CaseStatus.FAILED,
            verified=False,
            method="POST",
            url="http://localhost:8080/api/suppliers",
            status_code=500,
            elapsed_ms=25.0,
            request_headers={},
            response_headers={},
            assertion_results=[
                {"type": "status", "passed": False, "message": "Expected 201, got 500"}
            ],
            extracted_variables={},
            extraction_errors=[],
            error_message="HTTP 500 Internal Server Error",
        ),
        # Step 2: Setup skipped
        CaseResult(
            test_name="[WF-PROD] [SETUP:2] Create Product",
            module="workflow",
            tags=["workflow:wf-prod", "step-type:setup"],
            status=CaseStatus.SKIPPED,
            verified=False,
            method="POST",
            url="http://localhost:8080/api/products",
            status_code=None,
            elapsed_ms=0.0,
            request_headers={},
            response_headers={},
            assertion_results=[],
            extracted_variables={},
            extraction_errors=[],
            skip_reason="Setup step 1 [POST /api/suppliers] failed",
        ),
        # Step 3: Test step skipped
        CaseResult(
            test_name="[WF-PROD] [TEST:3] Get Product Movements",
            module="workflow",
            tags=["workflow:wf-prod", "step-type:test"],
            status=CaseStatus.SKIPPED,
            verified=False,
            method="GET",
            url="http://localhost:8080/api/products/{{prod_id}}/movements",
            status_code=None,
            elapsed_ms=0.0,
            request_headers={},
            response_headers={},
            assertion_results=[],
            extracted_variables={},
            extraction_errors=[],
            skip_reason="Setup step 1 [POST /api/suppliers] failed",
        ),
    ]

    report = service.investigate_run(_make_run(cases))

    assert report.total_cases_evaluated == 3
    assert report.total_failures_detected == 3
    assert report.primary_failures_count == 1
    assert report.downstream_consequences_count == 2

    # Primary finding is Step 1
    primary = report.findings[0]
    assert primary.is_primary_failure
    assert primary.finding_id == "FINDING-001"
    assert primary.execution_phase == ExecutionStage.SETUP
    assert primary.category == DiagnosticCategory.SUSPECTED_APPLICATION_FAILURE
    assert primary.downstream_impact_count == 2

    # Downstream findings are Steps 2 & 3
    downstream_1 = report.findings[1]
    assert not downstream_1.is_primary_failure
    assert downstream_1.primary_finding_id == "FINDING-001"
    assert downstream_1.category == DiagnosticCategory.FIXTURE_OR_DEPENDENCY_ISSUE
    assert "Upstream producer step failed" in downstream_1.candidate_causes[0]

    downstream_2 = report.findings[2]
    assert not downstream_2.is_primary_failure
    assert downstream_2.primary_finding_id == "FINDING-001"
    assert downstream_2.category == DiagnosticCategory.FIXTURE_OR_DEPENDENCY_ISSUE


# =========================================================================
# Case D — Timeout & Network Uncertainty
# =========================================================================
def test_case_d_network_timeout(service: FailureInvestigationService) -> None:
    """Case D: Request times out; transport issue with server uncertainty noted."""
    cases = [
        CaseResult(
            test_name="GET /api/reports/heavy",
            module="reports",
            tags=["perf"],
            status=CaseStatus.ERROR,
            verified=False,
            method="GET",
            url="http://localhost:8080/api/reports/heavy",
            status_code=None,
            elapsed_ms=5002.0,
            request_headers={},
            response_headers={},
            assertion_results=[],
            extracted_variables={},
            extraction_errors=[],
            error_message="ReadTimeout: Request timed out after 5.0 seconds",
        )
    ]

    report = service.investigate_run(_make_run(cases))

    assert report.total_failures_detected == 1
    finding = report.findings[0]
    assert finding.category == DiagnosticCategory.NETWORK_OR_TRANSPORT_ISSUE
    assert finding.confidence == ConfidenceLevel.VERIFIED_OBSERVATION
    assert finding.disposition == DiagnosticDisposition.NETWORK_OR_INFRASTRUCTURE_ISSUE
    assert any(
        "server-side request processing state is uncertain" in c.lower()
        for c in finding.candidate_causes
    )
    assert any("timeout" in g.lower() for g in report.missing_evidence_gaps)


# =========================================================================
# Case E — Cleanup failure evaluated independently
# =========================================================================
def test_case_e_cleanup_failure_independent(
    service: FailureInvestigationService,
) -> None:
    """Case E: Test step passes, but LIFO cleanup deletion fails with 500."""
    cases = [
        CaseResult(
            test_name="[WF-USER] [TEST:1] Query User Profile",
            module="users",
            tags=["workflow:wf-user", "step-type:test"],
            status=CaseStatus.PASSED,
            verified=True,
            method="GET",
            url="http://localhost:8080/api/users/42",
            status_code=200,
            elapsed_ms=10.0,
            request_headers={},
            response_headers={},
            assertion_results=[
                {"type": "status", "passed": True, "message": "Expected 200"}
            ],
            extracted_variables={},
            extraction_errors=[],
        ),
        CaseResult(
            test_name="[WF-USER] [CLEANUP:2] Delete User",
            module="users",
            tags=["workflow:wf-user", "step-type:cleanup"],
            status=CaseStatus.CLEANUP_FAILED,
            verified=False,
            method="DELETE",
            url="http://localhost:8080/api/users/42",
            status_code=500,
            elapsed_ms=18.0,
            request_headers={},
            response_headers={},
            assertion_results=[],
            extracted_variables={},
            extraction_errors=[],
            error_message="HTTP 500 Internal Server Error during teardown",
        ),
    ]

    report = service.investigate_run(_make_run(cases))

    assert report.total_cases_evaluated == 2
    assert report.total_failures_detected == 1
    assert report.primary_failures_count == 1
    finding = report.findings[0]
    assert finding.execution_phase == ExecutionStage.CLEANUP
    assert finding.category == DiagnosticCategory.CLEANUP_ISSUE
    assert finding.disposition == DiagnosticDisposition.CLEANUP_FAILURE_DETECTED
    assert finding.is_primary_failure


# =========================================================================
# Case F — Unsupported oracle
# =========================================================================
def test_case_f_unsupported_oracle(service: FailureInvestigationService) -> None:
    """Case F: Test expects undocumented immediate consistency; marked TEST_DEFINITION_ISSUE."""
    cases = [
        CaseResult(
            test_name="Verify immediate consistency for order",
            module="orders",
            tags=["orders"],
            status=CaseStatus.FAILED,
            verified=False,
            method="GET",
            url="http://localhost:8080/api/orders/99",
            status_code=200,
            elapsed_ms=8.0,
            request_headers={},
            response_headers={},
            assertion_results=[
                {
                    "type": "custom",
                    "passed": False,
                    "message": "Speculative assertion failed: order state not synchronized immediately",
                }
            ],
            extracted_variables={},
            extraction_errors=[],
            error_message="Speculative assertion failed: order state not synchronized immediately",
        )
    ]

    report = service.investigate_run(_make_run(cases))

    assert report.total_failures_detected == 1
    finding = report.findings[0]
    assert finding.category == DiagnosticCategory.TEST_DEFINITION_ISSUE
    assert finding.confidence == ConfidenceLevel.SUSPECTED_CAUSE
    assert not report.has_application_defects
    assert report.has_test_or_fixture_issues


# =========================================================================
# Case G — Policy block
# =========================================================================
def test_case_g_policy_block(service: FailureInvestigationService) -> None:
    """Case G: Mutating operation blocked by ExecutionSafetyPolicy."""
    cases = [
        CaseResult(
            test_name="[WF-CAT] Pre-execution Gate",
            module="workflow",
            tags=["workflow:wf-cat", "status:blocked"],
            status=CaseStatus.BLOCKED,
            verified=False,
            method="POLICY",
            url="http://localhost:8080",
            status_code=None,
            elapsed_ms=0.0,
            request_headers={},
            response_headers={},
            assertion_results=[],
            extracted_variables={},
            extraction_errors=[],
            error_message="Executing mutating workflow requires explicit '--confirm-isolated-env' confirmation.",
        )
    ]

    report = service.investigate_run(_make_run(cases))

    assert report.total_failures_detected == 1
    finding = report.findings[0]
    assert finding.execution_phase == ExecutionStage.GATE
    assert finding.category == DiagnosticCategory.ACCESS_OR_POLICY_BLOCK
    assert finding.confidence == ConfidenceLevel.VERIFIED_OBSERVATION
    assert finding.disposition == DiagnosticDisposition.POLICY_AUTHORIZATION_REQUIRED
    assert report.has_environment_or_policy_blocks
    assert not report.has_application_defects


# =========================================================================
# Case H — Suspicious server response (HTTP 500)
# =========================================================================
def test_case_h_suspicious_server_500(service: FailureInvestigationService) -> None:
    """Case H: API returns HTTP 500; accurate symptom without invented stack trace."""
    cases = [
        CaseResult(
            test_name="POST /api/checkout",
            module="checkout",
            tags=["checkout"],
            status=CaseStatus.FAILED,
            verified=False,
            method="POST",
            url="http://localhost:8080/api/checkout",
            status_code=500,
            elapsed_ms=34.0,
            request_headers={"authorization": "Bearer secret-token-xyz"},
            response_headers={},
            assertion_results=[
                {"type": "status", "passed": False, "message": "Expected 200, got 500"}
            ],
            extracted_variables={},
            extraction_errors=[],
            error_message="HTTP 500 Internal Server Error",
            response_body='{"error": "Internal server crash"}',
        )
    ]

    report = service.investigate_run(_make_run(cases))

    assert report.total_failures_detected == 1
    finding = report.findings[0]
    assert finding.status_code == 500
    assert finding.category == DiagnosticCategory.SUSPECTED_APPLICATION_FAILURE
    assert finding.confidence == ConfidenceLevel.SUSPECTED_CAUSE
    assert finding.severity == FindingSeverity.HIGH
    assert finding.disposition == DiagnosticDisposition.ACTIONABLE_DEFECT_SUSPECTED
    assert "Internal Server Error" in finding.observed_symptom
    assert any(
        "server logs" in r.lower()
        for r in finding.recommended_next_step.lower().split(".")
    )


# =========================================================================
# Extraction Failure Handling
# =========================================================================
def test_extraction_failure_propagation(service: FailureInvestigationService) -> None:
    """Setup step fails JSONPath extraction; dependent step is marked as cascade."""
    cases = [
        CaseResult(
            test_name="[WF-CAT] [SETUP:1] Create Category",
            module="workflow",
            tags=["workflow:wf-cat", "step-type:setup"],
            status=CaseStatus.FAILED,
            verified=False,
            method="POST",
            url="http://localhost:8080/api/categories",
            status_code=200,
            elapsed_ms=10.0,
            request_headers={},
            response_headers={},
            assertion_results=[
                {"type": "status", "passed": True, "message": "Expected 200"}
            ],
            extracted_variables={},
            extraction_errors=["JSONPath '$.id' matched nothing in response body"],
            error_message="Extraction failure: JSONPath '$.id' matched nothing",
        ),
        CaseResult(
            test_name="[WF-CAT] [TEST:2] Get Category",
            module="workflow",
            tags=["workflow:wf-cat", "step-type:test"],
            status=CaseStatus.SKIPPED,
            verified=False,
            method="GET",
            url="http://localhost:8080/api/categories/{{cat_id}}",
            status_code=None,
            elapsed_ms=0.0,
            request_headers={},
            response_headers={},
            assertion_results=[],
            extracted_variables={},
            extraction_errors=[],
            skip_reason="Setup step 1 [POST /api/categories] failed",
        ),
    ]

    report = service.investigate_run(_make_run(cases))

    assert report.primary_failures_count == 1
    assert report.downstream_consequences_count == 1
    primary = report.findings[0]
    assert primary.category == DiagnosticCategory.FIXTURE_OR_DEPENDENCY_ISSUE
    assert "JSONPath '$.id' matched nothing" in primary.observed_symptom


# =========================================================================
# Credential Redaction in Findings
# =========================================================================
def test_credential_redaction(service: FailureInvestigationService) -> None:
    """Secret tokens, passwords, and sensitive keys are redacted from findings."""
    cases = [
        CaseResult(
            test_name="GET /api/secure",
            module="auth",
            tags=["auth"],
            status=CaseStatus.FAILED,
            verified=False,
            method="GET",
            url="http://localhost:8080/api/secure?api_key=secret-key-12345&token=jwt-secret",
            status_code=500,
            elapsed_ms=10.0,
            request_headers={"authorization": "Bearer secret-jwt"},
            response_headers={},
            assertion_results=[],
            extracted_variables={},
            extraction_errors=[],
            error_message="Failed with password=supersecretpass and token=jwt-secret",
        )
    ]

    report = service.investigate_run(_make_run(cases))
    d = report.to_dict()

    finding_dict = d["findings"][0]
    assert "secret-key-12345" not in finding_dict["url"]
    assert "supersecretpass" not in finding_dict["error_message"]
    assert "***" in finding_dict["url"]
    assert "***" in finding_dict["error_message"]


# =========================================================================
# Serialization Round-Trip
# =========================================================================
def test_report_serialization_round_trip(service: FailureInvestigationService) -> None:
    """Report supports to_dict, to_json, to_yaml, and from_dict."""
    cases = [
        CaseResult(
            test_name="GET /api/fail",
            module="test",
            tags=["test"],
            status=CaseStatus.FAILED,
            verified=False,
            method="GET",
            url="http://localhost:8080/api/fail",
            status_code=500,
            elapsed_ms=10.0,
            request_headers={},
            response_headers={},
            assertion_results=[],
            extracted_variables={},
            extraction_errors=[],
            error_message="Server 500 error",
        )
    ]
    report = service.investigate_run(_make_run(cases))

    # JSON round trip
    json_str = report.to_json()
    parsed_json = json.loads(json_str)
    restored = FailureInvestigationReport.from_dict(parsed_json)

    assert restored.run_id == report.run_id
    assert restored.total_failures_detected == report.total_failures_detected
    assert len(restored.findings) == len(report.findings)
    assert restored.findings[0].category == report.findings[0].category

    # YAML string check
    yaml_str = report.to_yaml()
    assert "investigation_id" in yaml_str
    assert "summary_metrics" in yaml_str


# =========================================================================
# Direct WorkflowExecutionResult Investigation
# =========================================================================
def test_investigate_workflow_directly(service: FailureInvestigationService) -> None:
    """Service can investigate WorkflowExecutionResult objects directly."""
    wf_res = WorkflowExecutionResult(
        workflow_id="WF-DIRECT",
        title="Direct Workflow Test",
        status=CaseStatus.FAILED,
        started_at=datetime.now(UTC).isoformat(),
        finished_at=datetime.now(UTC).isoformat(),
        duration_ms=45.0,
        target_origin="http://localhost:8080",
        setup_results=[
            WorkflowStepResult(
                step_number=1,
                name="Create Item",
                step_type=WorkflowStepType.SETUP,
                status=CaseStatus.FAILED,
                method="POST",
                url="http://localhost:8080/api/items",
                status_code=500,
                error_message="Internal Server Error",
            )
        ],
        test_results=[
            WorkflowStepResult(
                step_number=2,
                name="Check Item",
                step_type=WorkflowStepType.TEST,
                status=CaseStatus.SKIPPED,
                method="GET",
                url="http://localhost:8080/api/items/1",
                skip_reason="Setup failed",
            )
        ],
    )

    report = service.investigate_workflow(wf_res)

    assert report.total_cases_evaluated == 2
    assert report.primary_failures_count == 1
    assert report.downstream_consequences_count == 1
    assert report.findings[0].is_primary_failure
    assert not report.findings[1].is_primary_failure


# =========================================================================
# Mocked AI Hypotheses
# =========================================================================
def test_mocked_ai_hypotheses(service: FailureInvestigationService) -> None:
    """Opt-in AI reasoning attaches hypotheses without making external network calls."""
    cases = [
        CaseResult(
            test_name="POST /api/orders",
            module="orders",
            tags=["orders"],
            status=CaseStatus.FAILED,
            verified=False,
            method="POST",
            url="http://localhost:8080/api/orders",
            status_code=500,
            elapsed_ms=20.0,
            request_headers={},
            response_headers={},
            assertion_results=[],
            extracted_variables={},
            extraction_errors=[],
            error_message="HTTP 500 Internal Server Error",
        )
    ]

    mock_llm = MockLLMProvider(
        '["Database deadlock on order line items", "Uncaught null pointer in order handler"]'
    )

    report = service.investigate_run(
        _make_run(cases),
        with_ai=True,
        llm_provider=mock_llm,
    )

    assert len(mock_llm.prompts) == 1
    assert "POST http://localhost:8080/api/orders" in mock_llm.prompts[0]
    assert report.ai_analysis is not None
    assert report.findings[0].ai_hypothesis is not None
    assert "[HYPOTHESIS]" in report.findings[0].ai_hypothesis


# =========================================================================
# Multi-Workflow Variable & Failure Isolation
# =========================================================================
def test_multi_workflow_failure_isolation(service: FailureInvestigationService) -> None:
    """Failures in Workflow A do not cascade into or contaminate Workflow B."""
    cases = [
        # Workflow A fails in setup
        CaseResult(
            test_name="[WF-A] [SETUP:1] Create Supplier A",
            module="workflow",
            tags=["workflow:wf-a", "step-type:setup"],
            status=CaseStatus.FAILED,
            verified=False,
            method="POST",
            url="http://localhost:8080/api/suppliers",
            status_code=500,
            elapsed_ms=10.0,
            request_headers={},
            response_headers={},
            assertion_results=[],
            extracted_variables={},
            extraction_errors=[],
            error_message="Server error",
        ),
        CaseResult(
            test_name="[WF-A] [TEST:2] Get Supplier A",
            module="workflow",
            tags=["workflow:wf-a", "step-type:test"],
            status=CaseStatus.SKIPPED,
            verified=False,
            method="GET",
            url="http://localhost:8080/api/suppliers/{{sup_a}}",
            status_code=None,
            elapsed_ms=0.0,
            request_headers={},
            response_headers={},
            assertion_results=[],
            extracted_variables={},
            extraction_errors=[],
            skip_reason="Setup step 1 failed",
        ),
        # Workflow B passes completely
        CaseResult(
            test_name="[WF-B] [SETUP:1] Create Category B",
            module="workflow",
            tags=["workflow:wf-b", "step-type:setup"],
            status=CaseStatus.PASSED,
            verified=True,
            method="POST",
            url="http://localhost:8080/api/categories",
            status_code=200,
            elapsed_ms=12.0,
            request_headers={},
            response_headers={},
            assertion_results=[],
            extracted_variables={"cat_b": 10},
            extraction_errors=[],
        ),
        CaseResult(
            test_name="[WF-B] [TEST:2] Get Category B",
            module="workflow",
            tags=["workflow:wf-b", "step-type:test"],
            status=CaseStatus.PASSED,
            verified=True,
            method="GET",
            url="http://localhost:8080/api/categories/10",
            status_code=200,
            elapsed_ms=8.0,
            request_headers={},
            response_headers={},
            assertion_results=[],
            extracted_variables={},
            extraction_errors=[],
        ),
    ]

    report = service.investigate_run(_make_run(cases))

    assert report.total_cases_evaluated == 4
    assert report.total_failures_detected == 2
    assert report.primary_failures_count == 1
    assert report.downstream_consequences_count == 1
    # WF-B cases do not appear in findings
    assert all(f.workflow_id == "wf-a" for f in report.findings)


# =========================================================================
# Connection Refused
# =========================================================================
def test_connection_refused_investigation(service: FailureInvestigationService) -> None:
    """Target down / connection refused diagnosed as network infrastructure issue."""
    cases = [
        CaseResult(
            test_name="GET /api/ping",
            module="network",
            tags=["ping"],
            status=CaseStatus.ERROR,
            verified=False,
            method="GET",
            url="http://localhost:9999/api/ping",
            status_code=None,
            elapsed_ms=1.5,
            request_headers={},
            response_headers={},
            assertion_results=[],
            extracted_variables={},
            extraction_errors=[],
            error_message="ConnectError: [Errno 111] Connection refused",
        )
    ]

    report = service.investigate_run(_make_run(cases))

    assert report.total_failures_detected == 1
    finding = report.findings[0]
    assert finding.category == DiagnosticCategory.NETWORK_OR_TRANSPORT_ISSUE
    assert finding.disposition == DiagnosticDisposition.NETWORK_OR_INFRASTRUCTURE_ISSUE
    assert "Connection refused" in finding.observed_symptom
    assert any(
        "server is not running" in c.lower() or "not running" in c.lower()
        for c in finding.candidate_causes
    )


# =========================================================================
# File Loading Support
# =========================================================================
def test_load_run_from_file_formats(
    tmp_path: Any, service: FailureInvestigationService
) -> None:
    """Tests loading from RunResult, WorkflowExecutionResult, and Cases JSON."""
    # 1. Standard RunResult JSON
    run_file = tmp_path / "run_res.json"
    run_file.write_text(
        json.dumps(
            {
                "run_id": "file-run-123",
                "total_tests": 1,
                "passed_tests": 0,
                "failed_tests": 1,
                "results": [
                    {
                        "test_name": "Test File Case",
                        "status": "FAILED",
                        "method": "GET",
                        "url": "http://localhost:8080/api/file",
                        "status_code": 500,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded_run = service.load_run_from_file(run_file)
    assert loaded_run.run_id == "file-run-123"
    assert loaded_run.total_tests == 1

    # 2. Workflow JSON
    wf_file = tmp_path / "wf_res.json"
    wf_file.write_text(
        json.dumps(
            {
                "workflow_id": "WF-FILE",
                "setup_results": [
                    {
                        "step_number": 1,
                        "name": "Setup Step",
                        "status": "PASSED",
                        "method": "POST",
                        "url": "http://localhost:8080/api/setup",
                        "status_code": 200,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded_wf = service.load_run_from_file(wf_file)
    assert "WF-FILE" in loaded_wf.run_id
    assert loaded_wf.total_tests == 1


# =========================================================================
# Additional Edge Cases & Error Handling
# =========================================================================
def test_network_dns_and_ssl_errors(service: FailureInvestigationService) -> None:
    """DNS and TLS errors are correctly triaged into transport category."""
    cases = [
        CaseResult(
            test_name="GET /api/dns",
            module="dns",
            tags=[],
            status=CaseStatus.ERROR,
            verified=False,
            method="GET",
            url="http://invalid.domain.example/api",
            status_code=None,
            elapsed_ms=0.5,
            request_headers={},
            response_headers={},
            assertion_results=[],
            extracted_variables={},
            extraction_errors=[],
            error_message="NameResolutionError: getaddrinfo failed for host",
        )
    ]
    report = service.investigate_run(_make_run(cases))
    assert report.findings[0].category == DiagnosticCategory.NETWORK_OR_TRANSPORT_ISSUE
    assert "Network or transport failure" in report.findings[0].observed_symptom


def test_assertion_syntax_error(service: FailureInvestigationService) -> None:
    """Assertion syntax defect is classified as TEST_DEFINITION_ISSUE."""
    cases = [
        CaseResult(
            test_name="Invalid Syntax Test",
            module="test",
            tags=[],
            status=CaseStatus.ERROR,
            verified=False,
            method="GET",
            url="http://localhost:8080/api/test",
            status_code=200,
            elapsed_ms=5.0,
            request_headers={},
            response_headers={},
            assertion_results=[],
            extracted_variables={},
            extraction_errors=[],
            error_message="Unsupported rule type 'custom_unknown' in assertion definition",
        )
    ]
    report = service.investigate_run(_make_run(cases))
    assert report.findings[0].category == DiagnosticCategory.TEST_DEFINITION_ISSUE
    assert (
        report.findings[0].disposition
        == DiagnosticDisposition.TEST_DEFINITION_FIX_NEEDED
    )


def test_unapproved_scenario_compiler_integration(
    service: FailureInvestigationService,
) -> None:
    """Scenario in compiler report marked NEEDS_REVIEW triggers policy cause."""

    class DummyScenario:
        scenario_id = "SCENARIO-99"
        title = "Sensitive Action"
        origin = "ai_proposed"
        status = "NEEDS_REVIEW"

    class DummyCompilerReport:
        scenarios = [DummyScenario()]

    cases = [
        CaseResult(
            test_name="[SCENARIO-99] Delete Customer",
            module="cust",
            tags=["policy"],
            status=CaseStatus.BLOCKED,
            verified=False,
            method="POLICY",
            url="http://localhost:8080/api/cust/1",
            status_code=None,
            elapsed_ms=0.0,
            request_headers={},
            response_headers={},
            assertion_results=[],
            extracted_variables={},
            extraction_errors=[],
            error_message="Approval required for mutating AI scenario",
        )
    ]
    report = service.investigate_run(
        _make_run(cases), compiler_report=DummyCompilerReport()
    )
    assert report.findings[0].category == DiagnosticCategory.ACCESS_OR_POLICY_BLOCK
    assert any("NEEDS_REVIEW" in c for c in report.findings[0].candidate_causes)


def test_missing_fixture_report_integration(
    service: FailureInvestigationService,
) -> None:
    """Fixture report missing entry links finding to missing fixture."""

    class DummyFixture:
        name = "auth_token"
        status = "MISSING"

    class DummyFixtureReport:
        fixtures = [DummyFixture()]

    cases = [
        CaseResult(
            test_name="GET /api/data with auth_token",
            module="data",
            tags=[],
            status=CaseStatus.BLOCKED,
            verified=False,
            method="GET",
            url="http://localhost:8080/api/data",
            status_code=None,
            elapsed_ms=0.0,
            request_headers={},
            response_headers={},
            assertion_results=[],
            extracted_variables={},
            extraction_errors=[],
            error_message="Missing fixture: auth_token",
        )
    ]
    report = service.investigate_run(
        _make_run(cases), fixture_report=DummyFixtureReport()
    )
    assert report.findings[0].category == DiagnosticCategory.FIXTURE_OR_DEPENDENCY_ISSUE
    assert any("auth_token" in c for c in report.findings[0].candidate_causes)


def test_load_run_from_file_error_handling(
    tmp_path: Any, service: FailureInvestigationService
) -> None:
    """File loading raises ConfigurationError on missing or invalid files."""
    from apiscope.core.exceptions import ConfigurationError

    with pytest.raises(ConfigurationError, match="Run file not found"):
        service.load_run_from_file(tmp_path / "nonexistent.json")

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("not json content", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Failed to parse JSON"):
        service.load_run_from_file(bad_json)

    unknown_schema = tmp_path / "unknown.json"
    unknown_schema.write_text('{"foo": "bar"}', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Unrecognized run result"):
        service.load_run_from_file(unknown_schema)
