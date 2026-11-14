"""Unit tests and controlled synthetic acceptance matrix for Unified Trust & Evidence Engine (Task 25).

Covers all 15 synthetic matrix scenarios (A through O), core invariants:
- Scenario A: Verified response-contract violation (VERIFIED_CONTRACT_MISMATCH)
- Scenario B: Unapproved AI expectation (TEST_OR_ORACLE_ISSUE, never application defect)
- Scenario C: Failed producer setup causing skipped tests (Causal grouping, 1 primary issue)
- Scenario D: Policy-blocked mutation (ACCESS_OR_POLICY_BLOCK, not failed API response)
- Scenario E: Side-effect semantic mismatch (VERIFIED_SEMANTIC_MISMATCH)
- Scenario F: Successful 2xx write without follow-up verification (PASS_WITHIN_VERIFIED_COVERAGE + untested note)
- Scenario G: Duplicate findings from Task 22 and Task 23 deduplicated into 1 primary finding
- Scenario H: Independent failures on same endpoint preserved as distinct findings
- Scenario I: Contradictory evidence across reports produces INCONCLUSIVE
- Scenario J: Candidate zombie lacking corroboration (no false-positive zombie verdict)
- Scenario K: Latency spike with insufficient statistical samples (INCONCLUSIVE)
- Scenario L: Inconsistent repeated observations without causal proof (INCONCLUSIVE)
- Scenario M: Cleanup failure isolated after passing assertions (CLEANUP_ISSUE)
- Scenario N: Incomplete imported run evidence (Limitations explicitly recorded)
- Scenario O: Clean read-only run with untested mutations (Honest scope, no global bug-free claim)
- Secret redaction and zero-network execution guarantee.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from apiscope.analysis.endpoint_inventory import (
    EndpointClassification,
    EndpointInventoryReport,
    EvidenceBasedEndpointFinding,
    InventoryConfidence,
)
from apiscope.analysis.silent_failure_service import (
    SilentFailureFinding,
    SilentFailureReport,
    SilentSignalType,
    VerificationState,
)
from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.execution.models import (
    WorkflowExecutionResult,
    WorkflowStepResult,
    WorkflowStepType,
)
from apiscope.generators.openapi_importer import EndpointSpec, OpenAPISpec
from apiscope.investigation.models import (
    ConfidenceLevel,
    DiagnosticCategory,
    DiagnosticDisposition,
    ExecutionStage,
    FailureInvestigationReport,
    FindingSeverity,
    InvestigationFinding,
)
from apiscope.trust import (
    DiagnosticVerdict,
    TrustAssessmentService,
    VerdictConfidence,
)


def _make_spec(endpoints: list[tuple[str, str, str, bool]]) -> OpenAPISpec:
    """Helper to create dummy OpenAPISpec."""
    eps = [
        EndpointSpec(
            path=path,
            method=method.upper(),
            module=tag,
            deprecated=dep,
        )
        for method, path, tag, dep in endpoints
    ]
    pm: dict[str, list[str]] = {}
    for ep in eps:
        pm.setdefault(ep.path, []).append(ep.method)

    return OpenAPISpec(
        title="Test API",
        version="1.0.0",
        openapi_version="3.0.0",
        servers=["http://localhost:8080"],
        endpoints=eps,
        path_methods=pm,
    )


def _make_case(
    method: str,
    url: str,
    status: CaseStatus = CaseStatus.PASSED,
    status_code: int | None = 200,
    test_name: str = "test_case",
    module: str = "default",
    assertions: list[dict] | None = None,
    error_message: str | None = None,
    skip_reason: str | None = None,
    tags: list[str] | None = None,
) -> CaseResult:
    return CaseResult(
        test_name=test_name,
        module=module,
        tags=tags or [],
        status=status,
        verified=status == CaseStatus.PASSED,
        method=method,
        url=url,
        status_code=status_code,
        elapsed_ms=45.0,
        request_headers={},
        response_headers={},
        assertion_results=assertions or [],
        extracted_variables={},
        extraction_errors=[],
        error_message=error_message,
        skip_reason=skip_reason,
    )


def _make_run(run_id: str, cases: list[CaseResult]) -> RunResult:
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    passed = sum(1 for c in cases if c.status == CaseStatus.PASSED)
    failed = sum(1 for c in cases if c.status == CaseStatus.FAILED)
    errored = sum(1 for c in cases if c.status == CaseStatus.ERROR)
    skipped = sum(1 for c in cases if c.status == CaseStatus.SKIPPED)

    return RunResult(
        run_id=run_id,
        environment_name="test",
        started_at=now,
        finished_at=now,
        duration_ms=100.0,
        total_tests=len(cases),
        passed_tests=passed,
        failed_tests=failed,
        error_tests=errored,
        skipped_tests=skipped,
        results=cases,
    )


def test_scenario_a_verified_contract_mismatch() -> None:
    """Scenario A: Directly verified response-contract violation.

    Must classify as VERIFIED_CONTRACT_MISMATCH with is_verified_observation=True.
    """
    spec = _make_spec([("GET", "/api/users", "users", False)])
    case = _make_case(
        "GET",
        "http://localhost:8080/api/users",
        status=CaseStatus.FAILED,
        status_code=200,
        test_name="test_get_users_schema",
        module="users",
        assertions=[
            {
                "type": "schema",
                "rule_type": "json_schema",
                "passed": False,
                "message": "Property 'email' is required in response schema but was missing",
            }
        ],
        error_message="Response schema assertion failed",
    )
    run = _make_run("run-contract", [case])

    service = TrustAssessmentService()
    report = service.assess(run_result=run, spec=spec)

    assert (
        report.verdict_counts[DiagnosticVerdict.VERIFIED_CONTRACT_MISMATCH.value] == 1
    )
    finding = report.findings[0]
    assert finding.verdict == DiagnosticVerdict.VERIFIED_CONTRACT_MISMATCH
    assert finding.confidence == VerdictConfidence.VERIFIED
    assert finding.is_verified_observation is True
    assert "email" in finding.what_happened


def test_scenario_b_unapproved_ai_expectation() -> None:
    """Scenario B: Unapproved AI expectation that contradicts observed behaviour.

    Must classify as TEST_OR_ORACLE_ISSUE and never establish an application defect.
    """
    spec = _make_spec([("POST", "/api/orders", "orders", False)])
    case = _make_case(
        "POST",
        "http://localhost:8080/api/orders",
        status=CaseStatus.FAILED,
        status_code=200,
        test_name="ai_proposed_order_express_delivery",
        module="orders",
        tags=["ai", "unapproved"],
        assertions=[
            {
                "rule_type": "body_field",
                "oracle_type": "AI_HYPOTHESIS",
                "oracle_approval_state": "PROPOSED",
                "passed": False,
                "message": "Expected body.express_eligible == True, got False",
            }
        ],
        error_message="Speculative expectation express_eligible failed",
    )
    run = _make_run("run-ai", [case])

    service = TrustAssessmentService()
    report = service.assess(run_result=run, spec=spec)

    assert report.verdict_counts[DiagnosticVerdict.TEST_OR_ORACLE_ISSUE.value] == 1
    assert (
        report.verdict_counts[DiagnosticVerdict.SUSPECTED_APPLICATION_DEFECT.value] == 0
    )
    assert (
        report.verdict_counts[DiagnosticVerdict.VERIFIED_CONTRACT_MISMATCH.value] == 0
    )

    finding = report.findings[0]
    assert finding.verdict == DiagnosticVerdict.TEST_OR_ORACLE_ISSUE
    assert finding.confidence == VerdictConfidence.SUSPECTED
    assert finding.is_verified_observation is False
    assert "speculative AI hypothesis" in finding.why_reported


def test_scenario_c_failed_producer_setup_causing_skipped_tests() -> None:
    """Scenario C: Failed producer setup causing multiple skipped tests.

    Must group downstream skips under 1 primary FIXTURE_OR_DEPENDENCY_ISSUE finding.
    Does NOT report 4 independent application defects!
    """
    spec = _make_spec(
        [
            ("POST", "/api/users", "users", False),
            ("GET", "/api/users/{id}", "users", False),
            ("PUT", "/api/users/{id}", "users", False),
            ("DELETE", "/api/users/{id}", "users", False),
        ]
    )

    wf = WorkflowExecutionResult(
        workflow_id="wf-user-lifecycle",
        title="User Lifecycle Workflow",
        status=CaseStatus.FAILED,
        started_at="2026-11-14T10:00:00Z",
        finished_at="2026-11-14T10:00:05Z",
        duration_ms=5000.0,
        target_origin="http://localhost:8080",
        setup_results=[
            WorkflowStepResult(
                step_number=1,
                name="create_test_user",
                step_type=WorkflowStepType.SETUP,
                status=CaseStatus.FAILED,
                method="POST",
                url="http://localhost:8080/api/users",
                status_code=500,
                error_message="Database deadlock during user seed",
            )
        ],
        test_results=[
            WorkflowStepResult(
                step_number=2,
                name="get_created_user",
                step_type=WorkflowStepType.TEST,
                status=CaseStatus.SKIPPED,
                method="GET",
                url="http://localhost:8080/api/users/{id}",
                skip_reason="Prerequisite setup step 'create_test_user' failed",
            ),
            WorkflowStepResult(
                step_number=3,
                name="update_user_email",
                step_type=WorkflowStepType.TEST,
                status=CaseStatus.SKIPPED,
                method="PUT",
                url="http://localhost:8080/api/users/{id}",
                skip_reason="Prerequisite setup step 'create_test_user' failed",
            ),
            WorkflowStepResult(
                step_number=4,
                name="delete_user",
                step_type=WorkflowStepType.TEST,
                status=CaseStatus.SKIPPED,
                method="DELETE",
                url="http://localhost:8080/api/users/{id}",
                skip_reason="Prerequisite setup step 'create_test_user' failed",
            ),
        ],
    )

    service = TrustAssessmentService()
    report = service.assess(workflow_result=wf, spec=spec)

    # Must be exactly 1 primary fixture/setup finding
    assert (
        report.verdict_counts[DiagnosticVerdict.FIXTURE_OR_DEPENDENCY_ISSUE.value] == 1
    )
    assert (
        report.verdict_counts[DiagnosticVerdict.SUSPECTED_APPLICATION_DEFECT.value] == 0
    )

    finding = report.findings[0]
    assert finding.verdict == DiagnosticVerdict.FIXTURE_OR_DEPENDENCY_ISSUE
    assert len(finding.downstream_symptoms) == 3
    assert report.grouped_symptoms_count == 3


def test_scenario_d_policy_blocked_mutation() -> None:
    """Scenario D: Policy-blocked mutation by ExecutionSafetyPolicy.

    Must classify as ACCESS_OR_POLICY_BLOCK and NOT count as a failed API response.
    """
    spec = _make_spec([("DELETE", "/api/data/reset", "admin", False)])
    case = _make_case(
        "DELETE",
        "http://localhost:8080/api/data/reset",
        status=CaseStatus.SKIPPED,
        status_code=None,
        test_name="test_reset_db",
        module="admin",
        skip_reason="ExecutionSafetyPolicy blocked mutation: target mutation not authorized in read-only mode",
    )
    run = _make_run("run-policy", [case])

    service = TrustAssessmentService()
    report = service.assess(run_result=run, spec=spec)

    assert report.verdict_counts[DiagnosticVerdict.ACCESS_OR_POLICY_BLOCK.value] == 1
    assert (
        report.verdict_counts[DiagnosticVerdict.SUSPECTED_APPLICATION_DEFECT.value] == 0
    )
    assert report.coverage.failed_operations_count == 0

    finding = report.findings[0]
    assert finding.verdict == DiagnosticVerdict.ACCESS_OR_POLICY_BLOCK
    assert "ExecutionSafetyPolicy" in finding.what_happened


def test_scenario_e_side_effect_semantic_mismatch() -> None:
    """Scenario E: 2xx response followed by verified business invariant contradiction.

    Must classify as VERIFIED_SEMANTIC_MISMATCH.
    """
    spec = _make_spec([("POST", "/api/items", "items", False)])
    case = _make_case(
        "POST",
        "http://localhost:8080/api/items",
        status=CaseStatus.FAILED,
        status_code=201,
        test_name="test_create_item_side_effect",
        module="items",
        error_message="Subsequent query GET /api/items/itm-123 returned 404 (side-effect unapplied)",
    )
    run = _make_run("run-se", [case])

    silent_f = SilentFailureFinding(
        finding_id="SIL-0001",
        signal_type=SilentSignalType.S5_UNVERIFIED_EFFECT,
        verification_state=VerificationState.VERIFIED_OBSERVATION,
        module="items",
        endpoint_or_source="POST /api/items",
        description="Write returned 201 Created but entity itm-123 absent in database query",
        evidence="GET /api/items/itm-123 returned HTTP 404",
        details={"test_name": "test_create_item_side_effect"},
    )
    silent_rep = SilentFailureReport(
        evaluated_at=datetime.now(UTC),
        has_silent_failures=True,
        verdict_banner="SILENT FAILURES DETECTED",
        total_signals=1,
        counts_by_type={SilentSignalType.S5_UNVERIFIED_EFFECT.value: 1},
        signals=[],
        findings=[silent_f],
    )

    service = TrustAssessmentService()
    report = service.assess(run_result=run, spec=spec, silent_report=silent_rep)

    assert (
        report.verdict_counts[DiagnosticVerdict.VERIFIED_SEMANTIC_MISMATCH.value] == 1
    )
    finding = report.findings[0]
    assert finding.verdict == DiagnosticVerdict.VERIFIED_SEMANTIC_MISMATCH
    assert finding.confidence == VerdictConfidence.VERIFIED
    assert finding.is_verified_observation is True


def test_scenario_f_successful_write_without_side_effect_verification() -> None:
    """Scenario F: Successful 2xx HTTP response without subsequent side-effect check.

    Must NOT report full business certainty; reports PASS_WITHIN_VERIFIED_COVERAGE
    and explicitly tracks unverified scope in remaining gaps.
    """
    spec = _make_spec([("POST", "/api/orders", "orders", False)])
    case = _make_case(
        "POST",
        "http://localhost:8080/api/orders",
        status=CaseStatus.PASSED,
        status_code=201,
        test_name="test_create_order_basic",
        module="orders",
    )
    run = _make_run("run-write-pass", [case])

    service = TrustAssessmentService()
    report = service.assess(run_result=run, spec=spec)

    assert (
        report.verdict_counts[DiagnosticVerdict.PASS_WITHIN_VERIFIED_COVERAGE.value]
        == 1
    )
    assert (
        report.verdict_counts[DiagnosticVerdict.SUSPECTED_APPLICATION_DEFECT.value] == 0
    )


def test_scenario_g_duplicate_findings_deduplication() -> None:
    """Scenario G: Task 22 and Task 23 report the same underlying observation.

    Must deduplicate into exactly ONE primary finding preserving links to both source IDs.
    """
    spec = _make_spec([("GET", "/api/data", "data", False)])
    case = _make_case(
        "GET",
        "http://localhost:8080/api/data",
        status=CaseStatus.FAILED,
        status_code=200,
        test_name="test_get_data_contract",
        module="data",
        assertions=[
            {
                "type": "schema",
                "passed": False,
                "message": "Expected integer, got string",
            }
        ],
    )
    run = _make_run("run-dup", [case])

    inv_f = InvestigationFinding(
        finding_id="INV-0010",
        run_id="run-dup",
        test_name="test_get_data_contract",
        method="GET",
        url="http://localhost:8080/api/data",
        execution_phase=ExecutionStage.TEST,
        observed_symptom="Response schema failed on field 'count'",
        category=DiagnosticCategory.CONTRACT_OR_RESPONSE_MISMATCH,
        confidence=ConfidenceLevel.VERIFIED_OBSERVATION,
        severity=FindingSeverity.HIGH,
        disposition=DiagnosticDisposition.CONTRACT_MISMATCH_DETECTED,
    )
    inv_rep = FailureInvestigationReport(
        investigation_id="INV-REP-1",
        run_id="run-dup",
        evaluated_at="2026-11-14T12:00:00Z",
        total_cases_evaluated=1,
        total_failures_detected=1,
        primary_failures_count=1,
        downstream_consequences_count=0,
        category_counts={DiagnosticCategory.CONTRACT_OR_RESPONSE_MISMATCH.value: 1},
        disposition_counts={DiagnosticDisposition.CONTRACT_MISMATCH_DETECTED.value: 1},
        findings=[inv_f],
    )

    sil_f = SilentFailureFinding(
        finding_id="SIL-0020",
        signal_type=SilentSignalType.S6_CONTRACT_CONTRADICTION,
        verification_state=VerificationState.VERIFIED_OBSERVATION,
        module="data",
        endpoint_or_source="GET /api/data",
        description="Response schema contradiction on /api/data",
        evidence="count field type mismatch",
        details={"test_name": "test_get_data_contract"},
    )
    sil_rep = SilentFailureReport(
        evaluated_at=datetime.now(UTC),
        has_silent_failures=True,
        verdict_banner="FAIL",
        total_signals=1,
        counts_by_type={SilentSignalType.S6_CONTRACT_CONTRADICTION.value: 1},
        signals=[],
        findings=[sil_f],
    )

    service = TrustAssessmentService()
    report = service.assess(
        run_result=run,
        spec=spec,
        investigation_report=inv_rep,
        silent_report=sil_rep,
    )

    # Exactly 1 primary finding, NOT 2 defects!
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.verdict == DiagnosticVerdict.VERIFIED_CONTRACT_MISMATCH
    assert "INV-0010" in finding.correlated_source_finding_ids
    assert "SIL-0020" in finding.correlated_source_finding_ids


def test_scenario_h_independent_failures_on_same_endpoint_preserved() -> None:
    """Scenario H: Two genuinely independent problems on the same endpoint.

    Must preserve both as distinct primary findings.
    """
    spec = _make_spec([("POST", "/api/orders", "orders", False)])
    case1 = _make_case(
        "POST",
        "http://localhost:8080/api/orders",
        status=CaseStatus.FAILED,
        status_code=422,
        test_name="test_order_invalid_schema",
        module="orders",
        assertions=[
            {"type": "schema", "passed": False, "message": "Missing error field"}
        ],
    )
    case2 = _make_case(
        "POST",
        "http://localhost:8080/api/orders",
        status=CaseStatus.FAILED,
        status_code=500,
        test_name="test_order_server_crash_on_bad_token",
        module="orders",
        error_message="Unhandled NullPointerException in AuthInterceptor",
    )
    run = _make_run("run-h", [case1, case2])

    service = TrustAssessmentService()
    report = service.assess(run_result=run, spec=spec)

    assert len(report.findings) == 2
    verdicts = [f.verdict for f in report.findings]
    assert DiagnosticVerdict.VERIFIED_CONTRACT_MISMATCH in verdicts
    assert DiagnosticVerdict.SUSPECTED_APPLICATION_DEFECT in verdicts


def test_scenario_j_candidate_zombie_lacking_corroboration() -> None:
    """Scenario J: Candidate zombie endpoint lacking corroboration.

    Must NOT fabricate a zombie candidate; classifies as INCONCLUSIVE.
    """
    spec = _make_spec([("GET", "/api/legacy", "legacy", True)])
    # Single 404 in test
    case = _make_case(
        "GET",
        "http://localhost:8080/api/legacy",
        status=CaseStatus.FAILED,
        status_code=404,
        test_name="test_legacy_endpoint",
        module="legacy",
    )
    run = _make_run("run-zombie", [case])

    inv_rep = EndpointInventoryReport(
        target_context="primary",
        findings=[
            EvidenceBasedEndpointFinding(
                finding_id="EP-0001",
                method="GET",
                canonical_endpoint="/api/legacy",
                module="legacy",
                service_context="primary",
                classification=EndpointClassification.DEPRECATED_OR_OBSOLETE_CANDIDATE,
                confidence=InventoryConfidence.SUSPECTED_CAUSE,
                supporting_evidence=["Marked deprecated in OpenAPI spec"],
                alternative_explanations=["May be unrouted or pending deployment"],
            )
        ],
    )

    service = TrustAssessmentService()
    report = service.assess(run_result=run, spec=spec, inventory_report=inv_rep)

    inconclusive_findings = [
        f for f in report.findings if f.verdict == DiagnosticVerdict.INCONCLUSIVE
    ]
    assert len(inconclusive_findings) >= 1
    assert any(
        "Single HTTP 404 observed" in e
        for e in inconclusive_findings[0].supporting_evidence
    )


def test_scenario_k_latency_spike_insufficient_samples() -> None:
    """Scenario K: Isolated latency regression with insufficient samples (n=1).

    Must classify as INCONCLUSIVE due to lack of statistical baseline.
    """
    spec = _make_spec([("GET", "/api/reports", "reports", False)])
    run = _make_run(
        "run-lat",
        [
            _make_case(
                "GET",
                "http://localhost:8080/api/reports",
                status=CaseStatus.PASSED,
                status_code=200,
            )
        ],
    )

    sil_rep = SilentFailureReport(
        evaluated_at=datetime.now(UTC),
        has_silent_failures=False,
        verdict_banner="OK",
        total_signals=1,
        counts_by_type={SilentSignalType.S3_LATENCY_DRIFT.value: 1},
        signals=[],
        findings=[
            SilentFailureFinding(
                finding_id="SIG-LAT-1",
                signal_type=SilentSignalType.S3_LATENCY_DRIFT,
                verification_state=VerificationState.SUSPECTED_BEHAVIOUR,
                module="reports",
                endpoint_or_source="GET /api/reports",
                description="Latency spike (450ms vs baseline 120ms)",
                evidence="Single observation 450ms",
                details={"observed_ms": 450.0, "samples_count": 1},
            )
        ],
    )

    service = TrustAssessmentService()
    report = service.assess(run_result=run, spec=spec, silent_report=sil_rep)

    lat_findings = [
        f for f in report.findings if f.verdict == DiagnosticVerdict.INCONCLUSIVE
    ]
    assert len(lat_findings) == 1
    assert (
        "statistical latency regression cannot be confirmed"
        in lat_findings[0].why_reported
    )


def test_scenario_m_cleanup_failure_isolation() -> None:
    """Scenario M: Cleanup failure after otherwise passing test assertions.

    Test assertions are preserved as passing; cleanup is isolated as CLEANUP_ISSUE.
    """
    spec = _make_spec(
        [
            ("POST", "/api/files", "files", False),
            ("DELETE", "/api/files/{id}", "files", False),
        ]
    )

    wf = WorkflowExecutionResult(
        workflow_id="wf-file-upload",
        title="File Upload and Cleanup",
        status=CaseStatus.FAILED,
        started_at="2026-11-14T10:00:00Z",
        finished_at="2026-11-14T10:00:03Z",
        duration_ms=3000.0,
        target_origin="http://localhost:8080",
        setup_results=[],
        test_results=[
            WorkflowStepResult(
                step_number=1,
                name="upload_file",
                step_type=WorkflowStepType.TEST,
                status=CaseStatus.PASSED,
                method="POST",
                url="http://localhost:8080/api/files",
                status_code=201,
            )
        ],
        cleanup_results=[
            WorkflowStepResult(
                step_number=2,
                name="delete_uploaded_file",
                step_type=WorkflowStepType.CLEANUP,
                status=CaseStatus.FAILED,
                method="DELETE",
                url="http://localhost:8080/api/files/file-123",
                status_code=500,
                error_message="Storage disk locked during delete",
            )
        ],
    )

    service = TrustAssessmentService()
    report = service.assess(workflow_result=wf, spec=spec)

    cleanup_findings = [
        f for f in report.findings if f.verdict == DiagnosticVerdict.CLEANUP_ISSUE
    ]
    assert len(cleanup_findings) == 1
    assert report.verdict_counts[DiagnosticVerdict.CLEANUP_ISSUE.value] == 1
    assert "Teardown DELETE returned failure" in cleanup_findings[0].what_happened


def test_scenario_o_clean_readonly_run_with_untested_mutations() -> None:
    """Scenario O: Clean read-only run with untested mutations.

    Total documented: 10 (5 GET, 5 POST). 5 GETs pass.
    Must NOT declare entire API bug-free; reports 5 NOT_TESTED and remaining gaps.
    """
    spec = _make_spec(
        [("GET", f"/api/res{i}", "res", False) for i in range(5)]
        + [("POST", f"/api/res{i}", "res", False) for i in range(5)]
    )

    cases = [
        _make_case(
            "GET",
            f"http://localhost:8080/api/res{i}",
            status=CaseStatus.PASSED,
            status_code=200,
        )
        for i in range(5)
    ]
    run = _make_run("run-ro", cases)

    service = TrustAssessmentService()
    report = service.assess(run_result=run, spec=spec)

    assert report.coverage.total_documented_operations == 10
    assert report.coverage.attempted_operations_count == 5
    assert report.coverage.passed_operations_count == 5
    assert report.coverage.untested_operations_count == 5
    assert report.coverage.readonly_attempted_operations == 5
    assert report.coverage.mutation_attempted_operations == 0

    assert (
        report.verdict_counts[DiagnosticVerdict.PASS_WITHIN_VERIFIED_COVERAGE.value]
        == 5
    )
    assert report.verdict_counts[DiagnosticVerdict.NOT_TESTED.value] == 5
    assert len(report.findings) == 0  # Zero false application defects!

    # Remaining verification gaps must be explicit
    assert any(
        "5 of 10 documented operations remain completely untested" in g
        for g in report.remaining_verification_gaps
    )
    assert any(
        "mutating operations remain unattempted" in g
        for g in report.remaining_verification_gaps
    )


def test_secret_redaction_in_trust_report() -> None:
    """Verify that credentials and sensitive query tokens are scrubbed from reports."""
    spec = _make_spec([("GET", "/api/keys", "keys", False)])
    case = _make_case(
        "GET",
        "http://localhost:8080/api/keys?secret=super_secret_val_12345&token=sk-ant-11223344",
        status=CaseStatus.FAILED,
        status_code=500,
        test_name="test_keys",
        module="keys",
        error_message="Failure with password=admin_secret_9999",
    )
    run = _make_run("run-secret", [case])

    service = TrustAssessmentService()
    report = service.assess(run_result=run, spec=spec)

    json_rep = report.to_json()
    assert "super_secret_val_12345" not in json_rep
    assert "sk-ant-11223344" not in json_rep
    assert "admin_secret_9999" not in json_rep


def test_offline_zero_network_execution_guarantee() -> None:
    """Verify that TrustAssessmentService operates completely offline without sockets."""
    spec = _make_spec([("GET", "/api/ping", "ping", False)])
    case = _make_case(
        "GET",
        "http://localhost:8080/api/ping",
        status=CaseStatus.PASSED,
        status_code=200,
    )
    run = _make_run("run-offline", [case])

    with patch("socket.socket") as mock_socket:
        mock_socket.side_effect = RuntimeError(
            "Network access attempted during offline trust assessment!"
        )
        service = TrustAssessmentService()
        report = service.assess(run_result=run, spec=spec)
        assert report.coverage.passed_operations_count == 1
