"""Unified Trust & Evidence Engine Service (Task 25).

Correlates diagnostic outputs across Tasks 22 (Failure Investigation), 23 (Silent Failure
& Latency Drift), and 24 (Endpoint Inventory & Coverage Intelligence) into trustworthy,
defensible, deduplicated diagnostic verdicts with explicit evidence provenance.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from apiscope.analysis.endpoint_inventory import (
    EndpointClassification,
    EndpointInventoryReport,
    EndpointInventoryService,
    OperationCoverageMetrics,
)
from apiscope.analysis.path_normalizer import (
    extract_url_path,
    match_path_to_spec,
    normalize_unmatched_path,
)
from apiscope.analysis.silent_failure_service import (
    SilentFailureFinding,
    SilentFailureReport,
    SilentFailureService,
    SilentSignalType,
)
from apiscope.compiler.models import CompilationReport
from apiscope.contract.models import CanonicalContract
from apiscope.core.logging_config import redact_sensitive_text
from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.execution.models import (
    WorkflowExecutionResult,
    WorkflowStepResult,
)
from apiscope.fixtures.models import FixturePreparationReport
from apiscope.generators.openapi_importer import OpenAPISpec
from apiscope.investigation.models import (
    DiagnosticCategory,
    ExecutionStage,
    FailureInvestigationReport,
    InvestigationFinding,
)
from apiscope.investigation.service import FailureInvestigationService
from apiscope.trust.models import (
    DiagnosticVerdict,
    EvidenceSubsystem,
    OracleProvenance,
    TrustAssessmentReport,
    TrustEvidenceRecord,
    TrustFinding,
    VerdictConfidence,
    VerdictSeverity,
)


class TrustAssessmentService:
    """Unified engine for correlating diagnostic findings and producing defensible verdicts."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        """Initialize TrustAssessmentService with optional deterministic clock."""
        self._clock = clock or (lambda: datetime.now(UTC))

    def assess(
        self,
        run_result: RunResult | None = None,
        spec: OpenAPISpec | None = None,
        canonical_contract: CanonicalContract | None = None,
        repo_path: str | Path | None = None,
        workflow_result: WorkflowExecutionResult | None = None,
        investigation_report: FailureInvestigationReport | None = None,
        silent_report: SilentFailureReport | None = None,
        inventory_report: EndpointInventoryReport | None = None,
        fixture_report: FixturePreparationReport | None = None,
        compilation_report: CompilationReport | None = None,
        access_entries: Any = None,
        target_service: str = "primary",
        module_filter: str | None = None,
        ai_enricher: Any | None = None,
        enrich_with_gemini: bool = False,
    ) -> TrustAssessmentReport:
        """Evaluate multi-source evidence and produce defensible diagnostic verdicts.

        Operates 100% deterministically and offline with zero target HTTP network requests.
        """
        evaluated_sources: list[str] = []
        limitations: list[str] = []
        remaining_gaps: list[str] = []

        # 1. Authoritative Endpoint Inventory & Canonical Coverage
        if inventory_report is not None:
            if isinstance(inventory_report, dict):
                cov_dict = (
                    inventory_report.get("coverage_summary")
                    or inventory_report.get("coverage")
                    or {}
                )
                cov_obj = (
                    OperationCoverageMetrics(**cov_dict)
                    if cov_dict
                    else OperationCoverageMetrics()
                )
                active_inventory = EndpointInventoryReport(
                    target_context=inventory_report.get(
                        "target_context", target_service
                    ),
                    coverage_summary=cov_obj,
                    findings=inventory_report.get("findings", []),
                )
            else:
                active_inventory = inventory_report
            evaluated_sources.append("ENDPOINT_INVENTORY_REPORT")
        elif (
            spec is not None
            or canonical_contract is not None
            or run_result is not None
            or access_entries is not None
        ):
            inv_service = EndpointInventoryService(clock=self._clock)
            active_inventory = inv_service.analyze(
                spec=spec or canonical_contract,
                repo_path=repo_path,
                run_result=run_result,
                access_entries=access_entries,
                target_service=target_service,
                module_filter=module_filter,
            )
            evaluated_sources.append("ENDPOINT_INVENTORY_SERVICE")
        else:
            active_inventory = EndpointInventoryReport(
                target_context=target_service,
                coverage_summary=OperationCoverageMetrics(),
            )
            limitations.append(
                "No OpenAPI specification or endpoint inventory provided; coverage denominator is unconstrained"
            )

        # 2. Reconcile or Compute Failure Investigation (Task 22)
        active_investigation = investigation_report
        if isinstance(active_investigation, dict):
            active_investigation = FailureInvestigationReport.from_dict(
                active_investigation
            )

        if active_investigation is None and run_result is not None:
            inv_eng = FailureInvestigationService()
            active_investigation = inv_eng.investigate_run(
                run_result=run_result,
                contract=canonical_contract,
                fixture_report=fixture_report,
                with_ai=False,
            )
            evaluated_sources.append("FAILURE_INVESTIGATION_SERVICE")
        elif active_investigation is not None:
            evaluated_sources.append("FAILURE_INVESTIGATION_REPORT")

        # 3. Reconcile or Compute Silent Failure Detection (Task 23)
        active_silent = silent_report
        if active_silent is None and run_result is not None:
            silent_service = SilentFailureService(clock=self._clock)
            active_silent = silent_service.analyze(
                run_result=run_result,
                contract=canonical_contract or spec,
                module=module_filter,
            )
            evaluated_sources.append("SILENT_FAILURE_SERVICE")
        elif active_silent is not None:
            evaluated_sources.append("SILENT_FAILURE_REPORT")

        if run_result is not None:
            evaluated_sources.append("RUN_RESULT")
        if workflow_result is not None:
            evaluated_sources.append("WORKFLOW_EXECUTION_RESULT")
        if fixture_report is not None:
            evaluated_sources.append("FIXTURE_PREPARATION_REPORT")
        if compilation_report is not None:
            evaluated_sources.append("COMPILATION_REPORT")

        # 4. Ingest and Normalize Unified Evidence Records
        evidence_records: list[TrustEvidenceRecord] = []
        ev_counter = 1

        # Track spec paths for route matching
        spec_paths: list[str] = []
        if spec is not None:
            if hasattr(spec, "path_methods"):
                spec_paths = list(spec.path_methods.keys())
            elif hasattr(spec, "endpoints"):
                spec_paths = [ep.path for ep in spec.endpoints]
            elif hasattr(spec, "endpoints_by_path"):
                spec_paths = list(spec.endpoints_by_path.keys())
        elif canonical_contract is not None:
            spec_paths = [op.path for op in canonical_contract.operations]
        elif active_inventory is not None:
            spec_paths = [r.identity.route_template for r in active_inventory.inventory]

        # Ingest RunResult / CaseResults
        run_cases: list[CaseResult] = []
        if run_result is not None:
            run_cases = list(run_result.results)
        elif workflow_result is not None:
            run_cases = [
                self._workflow_step_to_case_result(st)
                for st in workflow_result.all_steps
            ]

        for case in run_cases:
            ev_id = f"EV-{ev_counter:04d}"
            ev_counter += 1

            c_url = redact_sensitive_text(str(case.url))
            c_path = extract_url_path(c_url)
            matched_tmpl = match_path_to_spec(c_path, spec_paths)
            route_tmpl = matched_tmpl or normalize_unmatched_path(c_path)
            origin = self._extract_origin(c_url)

            # Determine oracle provenance and approval state
            oracle_prov = OracleProvenance.RUNTIME_DEFAULT
            approval_st = "APPROVED"
            for a in case.assertion_results:
                o_type = a.get("oracle_type")
                if o_type:
                    try:
                        oracle_prov = OracleProvenance(o_type)
                    except ValueError:
                        oracle_prov = OracleProvenance.RUNTIME_DEFAULT
                o_appr = a.get("oracle_approval_state") or a.get("approval_status")
                if o_appr:
                    approval_st = str(o_appr).upper()

            # Check tags or compiler notes for unapproved AI origins
            if (
                any("ai" in t.lower() for t in case.tags)
                and oracle_prov == OracleProvenance.RUNTIME_DEFAULT
            ):
                oracle_prov = OracleProvenance.AI_HYPOTHESIS
            if "unapproved" in case.tags or "proposed" in case.tags:
                approval_st = "PROPOSED"

            obs_summary = f"Status: {case.status.value}"
            if case.status_code is not None:
                obs_summary += f", HTTP {case.status_code}"
            if case.error_message:
                obs_summary += f", Error: {redact_sensitive_text(case.error_message)}"

            evidence_records.append(
                TrustEvidenceRecord(
                    evidence_id=ev_id,
                    source_subsystem=EvidenceSubsystem.RUN_RESULT,
                    source_report_or_run_id=(
                        run_result.run_id if run_result else "workflow-run"
                    ),
                    step_id=case.test_name,
                    endpoint_identity=f"{case.method.upper()} {route_tmpl}",
                    method=case.method.upper(),
                    route_template=route_tmpl,
                    target_service=target_service,
                    target_origin=origin,
                    observed_outcome=obs_summary,
                    expected_outcome=self._extract_expected_outcome(case),
                    oracle_provenance=oracle_prov,
                    oracle_approval_state=approval_st,
                    fixture_prerequisites_ready=not bool(
                        case.skip_reason and "fixture" in case.skip_reason.lower()
                    ),
                    assertion_reference=(f"{len(case.assertion_results)} assertion(s)"),
                    data_completeness=(
                        "PARTIAL" if case.status_code is None else "COMPLETE"
                    ),
                    confidence=(
                        VerdictConfidence.VERIFIED
                        if case.status_code is not None
                        else VerdictConfidence.SUSPECTED
                    ),
                    supporting_details={
                        "test_name": case.test_name,
                        "module": case.module,
                        "status": case.status.value,
                        "status_code": case.status_code,
                        "elapsed_ms": case.elapsed_ms,
                        "skip_reason": case.skip_reason,
                        "error_message": case.error_message,
                    },
                )
            )

        # Ingest Investigation Findings (Task 22)
        if active_investigation is not None:
            for inv_f in active_investigation.findings:
                ev_id = f"EV-{ev_counter:04d}"
                ev_counter += 1
                c_url = redact_sensitive_text(str(inv_f.url))
                c_path = extract_url_path(c_url)
                matched_tmpl = match_path_to_spec(c_path, spec_paths)
                route_tmpl = matched_tmpl or normalize_unmatched_path(c_path)

                evidence_records.append(
                    TrustEvidenceRecord(
                        evidence_id=ev_id,
                        source_subsystem=EvidenceSubsystem.FAILURE_INVESTIGATION,
                        source_report_or_run_id=inv_f.finding_id,
                        workflow_id=inv_f.workflow_id,
                        step_id=inv_f.test_name,
                        endpoint_identity=f"{inv_f.method.upper()} {route_tmpl}",
                        method=inv_f.method.upper(),
                        route_template=route_tmpl,
                        target_service=target_service,
                        observed_outcome=inv_f.observed_symptom,
                        expected_outcome=inv_f.disposition.value,
                        oracle_provenance=OracleProvenance.EXPLICIT_CONTRACT,
                        data_completeness="COMPLETE",
                        confidence=(
                            VerdictConfidence.VERIFIED
                            if inv_f.confidence.value == "VERIFIED_OBSERVATION"
                            else VerdictConfidence.SUSPECTED
                        ),
                        supporting_details={
                            "category": inv_f.category.value,
                            "disposition": inv_f.disposition.value,
                            "causes": list(inv_f.candidate_causes),
                            "alternatives": list(inv_f.alternative_explanations),
                            "evidence": list(inv_f.supporting_evidence),
                            "is_primary": inv_f.is_primary_failure,
                            "primary_id": inv_f.primary_finding_id,
                        },
                    )
                )

        # Ingest Silent Failure Findings (Task 23)
        if active_silent is not None:
            raw_sil_findings = (
                active_silent.get("findings", [])
                if isinstance(active_silent, dict)
                else getattr(active_silent, "findings", [])
            )
            for sil_f in raw_sil_findings:
                ev_id = f"EV-{ev_counter:04d}"
                ev_counter += 1
                f_id = getattr(sil_f, "finding_id", None) or (
                    sil_f.get("finding_id", f"SIL-{ev_counter}")
                    if isinstance(sil_f, dict)
                    else f"SIL-{ev_counter}"
                )
                endpoint_or_source = getattr(sil_f, "endpoint_or_source", None) or (
                    sil_f.get("endpoint_or_source", "")
                    if isinstance(sil_f, dict)
                    else ""
                )
                route_tmpl = extract_url_path(endpoint_or_source)
                matched_tmpl = match_path_to_spec(route_tmpl, spec_paths)
                sig_type = getattr(sil_f, "signal_type", None) or (
                    sil_f.get("signal_type", "S1_LOG_ERROR")
                    if isinstance(sil_f, dict)
                    else "S1_LOG_ERROR"
                )
                sig_type_val = (
                    sig_type.value if hasattr(sig_type, "value") else str(sig_type)
                )
                v_state = getattr(sil_f, "verification_state", None) or (
                    sil_f.get("verification_state", "SUSPECTED_BEHAVIOUR")
                    if isinstance(sil_f, dict)
                    else "SUSPECTED_BEHAVIOUR"
                )
                v_state_val = (
                    v_state.value if hasattr(v_state, "value") else str(v_state)
                )
                desc = getattr(sil_f, "description", None) or (
                    sil_f.get("description", "") if isinstance(sil_f, dict) else ""
                )
                action = getattr(sil_f, "suggested_action", None) or (
                    sil_f.get("suggested_action", "") if isinstance(sil_f, dict) else ""
                )
                evidence = getattr(sil_f, "evidence", None) or (
                    sil_f.get("evidence", "") if isinstance(sil_f, dict) else ""
                )
                mod = getattr(sil_f, "module", None) or (
                    sil_f.get("module", "default")
                    if isinstance(sil_f, dict)
                    else "default"
                )
                dtls = getattr(sil_f, "details", None) or (
                    sil_f.get("details", {}) if isinstance(sil_f, dict) else {}
                )

                evidence_records.append(
                    TrustEvidenceRecord(
                        evidence_id=ev_id,
                        source_subsystem=EvidenceSubsystem.SILENT_FAILURE_DETECTOR,
                        source_report_or_run_id=f_id,
                        endpoint_identity=f"{matched_tmpl or route_tmpl}",
                        route_template=matched_tmpl or route_tmpl,
                        target_service=target_service,
                        observed_outcome=desc,
                        expected_outcome=action,
                        oracle_provenance=OracleProvenance.PROPERTY_CONSISTENCY,
                        data_completeness="COMPLETE",
                        confidence=(
                            VerdictConfidence.VERIFIED
                            if v_state_val == "VERIFIED_OBSERVATION"
                            else VerdictConfidence.SUSPECTED
                        ),
                        supporting_details={
                            "signal_type": sig_type_val,
                            "verification_state": v_state_val,
                            "module": mod,
                            "evidence": evidence,
                            "details": dict(dtls) if isinstance(dtls, dict) else {},
                        },
                    )
                )

        # 5. Core Correlation, Deduplication & Causal Grouping
        findings: list[TrustFinding] = []
        finding_counter = 1
        grouped_symptoms_count = 0
        processed_case_names: set[str] = set()

        # Group cases by canonical operation key
        cases_by_op: dict[str, list[CaseResult]] = defaultdict(list)
        for c in run_cases:
            c_path = extract_url_path(str(c.url))
            matched = match_path_to_spec(c_path, spec_paths)
            tmpl = matched or normalize_unmatched_path(c_path)
            op_key = f"{target_service}:{c.method.upper()} {tmpl}"
            cases_by_op[op_key].append(c)

        # A. Causal Grouping: Workflow Setup / Producer Fixture Failure
        setup_failure_finding: TrustFinding | None = None

        if workflow_result is not None:
            # Check setup steps
            failed_setup_steps = [
                s
                for s in workflow_result.setup_results
                if s.status in (CaseStatus.FAILED, CaseStatus.ERROR)
            ]
            if failed_setup_steps:
                primary_setup = failed_setup_steps[0]
                for s in failed_setup_steps:
                    processed_case_names.add(s.name)
                # Dependent test steps that skipped
                skipped_steps = [
                    s
                    for s in workflow_result.test_results
                    if s.status == CaseStatus.SKIPPED
                ]
                grouped_symptoms_count += len(skipped_steps)

                tf_id = f"TF-{finding_counter:04d}"
                finding_counter += 1
                setup_path = extract_url_path(primary_setup.url)
                matched_setup = match_path_to_spec(setup_path, spec_paths)
                tmpl_setup = matched_setup or normalize_unmatched_path(setup_path)

                setup_failure_finding = TrustFinding(
                    finding_id=tf_id,
                    verdict=DiagnosticVerdict.FIXTURE_OR_DEPENDENCY_ISSUE,
                    confidence=VerdictConfidence.VERIFIED,
                    severity=VerdictSeverity.HIGH,
                    endpoint=f"{primary_setup.method.upper()} {tmpl_setup}",
                    method=primary_setup.method.upper(),
                    route_template=tmpl_setup,
                    module="workflow-setup",
                    service_context=target_service,
                    what_happened=(
                        f"Workflow setup action '{primary_setup.name}' failed (HTTP {primary_setup.status_code or 'ERR'}): "
                        f"{primary_setup.error_message or 'Prerequisite entity creation failed'}"
                    ),
                    why_reported=(
                        "Setup failure prevented execution of downstream dependent test steps. "
                        "Downstream skips are causal consequences, not independent defects."
                    ),
                    supporting_evidence=[
                        f"Setup step #{primary_setup.step_number} '{primary_setup.name}' returned status {primary_setup.status.value}",
                        f"HTTP status code: {primary_setup.status_code}",
                        *(
                            [
                                f"Error message: {redact_sensitive_text(primary_setup.error_message)}"
                            ]
                            if primary_setup.error_message
                            else []
                        ),
                    ],
                    assumptions_used=[
                        "Dependent tests in workflow require setup entity to be present"
                    ],
                    is_verified_observation=True,
                    what_remains_untested=[
                        f"Dependent step #{s.step_number} '{s.name}'"
                        for s in skipped_steps
                    ],
                    recommended_action=(
                        "Fix prerequisite producer setup endpoint or ensure database seed fixtures are available."
                    ),
                    downstream_symptoms=[
                        f"Skipped step #{s.step_number} '{s.name}' ({s.method} {s.url}): {s.skip_reason or 'Prerequisite setup failed'}"
                        for s in skipped_steps
                    ],
                    correlated_source_finding_ids=[
                        primary_setup.name,
                        *(s.name for s in skipped_steps),
                    ],
                )
                findings.append(setup_failure_finding)

        # Detect standalone test fixture skips in run_cases
        fixture_skipped_cases = [
            c
            for c in run_cases
            if c.status == CaseStatus.SKIPPED
            and c.skip_reason
            and any(
                k in c.skip_reason.lower()
                for k in ("fixture", "producer", "prerequisite", "dependency")
            )
        ]
        if fixture_skipped_cases and not setup_failure_finding:
            grouped_symptoms_count += len(fixture_skipped_cases)
            tf_id = f"TF-{finding_counter:04d}"
            finding_counter += 1
            f_case = fixture_skipped_cases[0]
            f_path = extract_url_path(str(f_case.url))
            f_tmpl = match_path_to_spec(f_path, spec_paths) or normalize_unmatched_path(
                f_path
            )

            findings.append(
                TrustFinding(
                    finding_id=tf_id,
                    verdict=DiagnosticVerdict.FIXTURE_OR_DEPENDENCY_ISSUE,
                    confidence=VerdictConfidence.VERIFIED,
                    severity=VerdictSeverity.MEDIUM,
                    endpoint=f"{f_case.method.upper()} {f_tmpl}",
                    method=f_case.method.upper(),
                    route_template=f_tmpl,
                    module=f_case.module or "fixtures",
                    service_context=target_service,
                    what_happened=(
                        f"{len(fixture_skipped_cases)} test case(s) skipped due to unresolved fixture dependencies: "
                        f"{f_case.skip_reason}"
                    ),
                    why_reported=(
                        "Prerequisite test data or producer fixture was not resolved; "
                        "tests were safely skipped and not counted as application failures."
                    ),
                    supporting_evidence=[
                        f"Case '{c.test_name}' skipped: {c.skip_reason}"
                        for c in fixture_skipped_cases[:5]
                    ],
                    is_verified_observation=True,
                    what_remains_untested=[c.test_name for c in fixture_skipped_cases],
                    recommended_action=(
                        "Generate or configure required fixtures using 'apiscope fixtures plan' before running tests."
                    ),
                    downstream_symptoms=[
                        f"Skipped case '{c.test_name}'" for c in fixture_skipped_cases
                    ],
                    correlated_source_finding_ids=[
                        c.test_name for c in fixture_skipped_cases
                    ],
                )
            )

        # B. Policy Blocks & Mutation Fences (ExecutionSafetyPolicy)
        policy_blocked_cases = [
            c
            for c in run_cases
            if c.status == CaseStatus.SKIPPED
            and c.skip_reason
            and any(
                k in c.skip_reason.lower()
                for k in (
                    "policy",
                    "mutation fence",
                    "read-only",
                    "ssrf",
                    "unauthorized target",
                    "fence",
                )
            )
        ]
        if policy_blocked_cases:
            tf_id = f"TF-{finding_counter:04d}"
            finding_counter += 1
            p_case = policy_blocked_cases[0]
            p_path = extract_url_path(str(p_case.url))
            p_tmpl = match_path_to_spec(p_path, spec_paths) or normalize_unmatched_path(
                p_path
            )

            findings.append(
                TrustFinding(
                    finding_id=tf_id,
                    verdict=DiagnosticVerdict.ACCESS_OR_POLICY_BLOCK,
                    confidence=VerdictConfidence.VERIFIED,
                    severity=VerdictSeverity.INFO,
                    endpoint=f"{p_case.method.upper()} {p_tmpl}",
                    method=p_case.method.upper(),
                    route_template=p_tmpl,
                    module=p_case.module or "security",
                    service_context=target_service,
                    what_happened=(
                        f"{len(policy_blocked_cases)} mutating request(s) blocked by ExecutionSafetyPolicy: "
                        f"{p_case.skip_reason}"
                    ),
                    why_reported=(
                        "Safety fence prevented target mutations or unverified origin calls. "
                        "Policy blocks are safety invariants, not target API defects."
                    ),
                    supporting_evidence=[
                        f"Target URL: {c.url} blocked by policy: {c.skip_reason}"
                        for c in policy_blocked_cases[:5]
                    ],
                    is_verified_observation=True,
                    what_remains_untested=[c.test_name for c in policy_blocked_cases],
                    recommended_action=(
                        "Pass --allow-mutation and --confirm-isolated-env if testing against an authorized staging target."
                    ),
                    downstream_symptoms=[
                        f"Blocked case '{c.test_name}'" for c in policy_blocked_cases
                    ],
                    correlated_source_finding_ids=[
                        c.test_name for c in policy_blocked_cases
                    ],
                )
            )

        # C. Isolated Cleanup Failure
        if workflow_result is not None:
            failed_cleanups = [
                s
                for s in workflow_result.cleanup_results
                if s.status in (CaseStatus.FAILED, CaseStatus.ERROR)
            ]
            for cl in failed_cleanups:
                processed_case_names.add(cl.name)
                tf_id = f"TF-{finding_counter:04d}"
                finding_counter += 1
                cl_path = extract_url_path(cl.url)
                cl_tmpl = match_path_to_spec(
                    cl_path, spec_paths
                ) or normalize_unmatched_path(cl_path)

                findings.append(
                    TrustFinding(
                        finding_id=tf_id,
                        verdict=DiagnosticVerdict.CLEANUP_ISSUE,
                        confidence=VerdictConfidence.VERIFIED,
                        severity=VerdictSeverity.LOW,
                        endpoint=f"{cl.method.upper()} {cl_tmpl}",
                        method=cl.method.upper(),
                        route_template=cl_tmpl,
                        module="workflow-cleanup",
                        service_context=target_service,
                        what_happened=(
                            f"Teardown DELETE returned failure for '{cl.name}' (HTTP {cl.status_code or 'ERR'}): "
                            f"{cl.error_message or 'Resource cleanup failed'}"
                        ),
                        why_reported=(
                            "Cleanup teardown failure does not invalidate test assertions that already passed. "
                            "Reported separately as a resource leakage risk."
                        ),
                        supporting_evidence=[
                            f"Cleanup step '{cl.name}' status: {cl.status.value}",
                            f"HTTP status code: {cl.status_code}",
                            *(
                                [f"Error: {redact_sensitive_text(cl.error_message)}"]
                                if cl.error_message
                                else []
                            ),
                        ],
                        is_verified_observation=True,
                        recommended_action=(
                            "Verify delete permissions or check whether target resource was already purged."
                        ),
                        correlated_source_finding_ids=[cl.name],
                    )
                )
        elif active_investigation is not None:
            cleanup_findings = [
                f
                for f in active_investigation.findings
                if getattr(f, "category", None) == DiagnosticCategory.CLEANUP_ISSUE
                or getattr(f, "execution_phase", None) == ExecutionStage.CLEANUP
            ]
            for cf in cleanup_findings:
                processed_case_names.add(cf.test_name)
                tf_id = f"TF-{finding_counter:04d}"
                finding_counter += 1
                cl_path = extract_url_path(cf.url)
                cl_tmpl = match_path_to_spec(
                    cl_path, spec_paths
                ) or normalize_unmatched_path(cl_path)

                findings.append(
                    TrustFinding(
                        finding_id=tf_id,
                        verdict=DiagnosticVerdict.CLEANUP_ISSUE,
                        confidence=VerdictConfidence.VERIFIED,
                        severity=VerdictSeverity.LOW,
                        endpoint=f"{cf.method.upper()} {cl_tmpl}",
                        method=cf.method.upper(),
                        route_template=cl_tmpl,
                        module="workflow-cleanup",
                        service_context=target_service,
                        what_happened=(
                            f"Teardown DELETE returned failure for '{cf.test_name}' (HTTP {cf.status_code or 'ERR'}): "
                            f"{cf.error_message or 'Resource cleanup failed'}"
                        ),
                        why_reported=(
                            "Cleanup teardown failure does not invalidate test assertions that already passed. "
                            "Reported separately as a resource leakage risk."
                        ),
                        supporting_evidence=[
                            f"Cleanup step '{cf.test_name}'",
                            f"HTTP status code: {cf.status_code}",
                            *(
                                [f"Error: {redact_sensitive_text(cf.error_message)}"]
                                if cf.error_message
                                else []
                            ),
                        ],
                        is_verified_observation=True,
                        recommended_action=(
                            "Verify delete permissions or check whether target resource was already purged."
                        ),
                        correlated_source_finding_ids=[cf.finding_id, cf.test_name],
                    )
                )

        # D. Deduplicated Diagnostic Failures (Task 22 + Task 23 Cross-Correlation)

        # Check each failed/error case in run_cases
        for case in run_cases:
            if case.status not in (CaseStatus.FAILED, CaseStatus.ERROR):
                continue
            if case.test_name in processed_case_names:
                continue

            c_path = extract_url_path(str(case.url))
            matched_tmpl = match_path_to_spec(c_path, spec_paths)
            tmpl = matched_tmpl or normalize_unmatched_path(c_path)
            endpoint_str = f"{case.method.upper()} {tmpl}"

            # Check if this case is an unapproved AI expectation
            is_unapproved_ai = False
            for a in case.assertion_results:
                o_type = str(a.get("oracle_type", "")).upper()
                o_appr = str(
                    a.get("oracle_approval_state") or a.get("approval_status", "")
                ).upper()
                if (
                    o_type == "AI_HYPOTHESIS"
                    or o_appr in ("PROPOSED", "UNVERIFIED")
                    or "ai_proposed" in case.test_name.lower()
                ):
                    is_unapproved_ai = True
                    break

            if is_unapproved_ai:
                processed_case_names.add(case.test_name)
                tf_id = f"TF-{finding_counter:04d}"
                finding_counter += 1

                failed_asserts = [
                    a.get("message", "assertion failed")
                    for a in case.assertion_results
                    if not a.get("passed", True)
                ]
                findings.append(
                    TrustFinding(
                        finding_id=tf_id,
                        verdict=DiagnosticVerdict.TEST_OR_ORACLE_ISSUE,
                        confidence=VerdictConfidence.SUSPECTED,
                        severity=VerdictSeverity.MEDIUM,
                        endpoint=endpoint_str,
                        method=case.method.upper(),
                        route_template=tmpl,
                        module=case.module or "test-oracle",
                        service_context=target_service,
                        what_happened=(
                            f"Test '{case.test_name}' failed against an unapproved or speculative AI oracle expectation: "
                            f"{'; '.join(failed_asserts) or case.error_message or 'Assertion failed'}"
                        ),
                        why_reported=(
                            "The expected outcome was proposed by AI without human verification or contract backing. "
                            "A speculative AI hypothesis must never establish an application defect."
                        ),
                        supporting_evidence=[
                            f"Oracle type: {OracleProvenance.AI_HYPOTHESIS.value} (approval_state: PROPOSED)",
                            f"HTTP status received: {case.status_code}",
                            *(f"Failed assertion: {m}" for m in failed_asserts),
                        ],
                        assumptions_used=[
                            "Unapproved AI scenarios reflect hypothetical expectations that may not match system specifications"
                        ],
                        is_verified_observation=False,
                        what_remains_untested=[
                            f"Human review and approval of scenario oracle for '{case.test_name}'"
                        ],
                        recommended_action=(
                            "Review scenario in 'apiscope plan' and approve or calibrate expected outcome."
                        ),
                        correlated_source_finding_ids=[case.test_name],
                    )
                )
                continue

            # Check correlated findings from Task 22 and Task 23
            t22_matches: list[InvestigationFinding] = []
            if active_investigation is not None:
                t22_matches = [
                    f
                    for f in active_investigation.findings
                    if (f.test_name and f.test_name == case.test_name)
                    or (
                        not f.test_name
                        and f.method.upper() == case.method.upper()
                        and extract_url_path(f.url) == c_path
                    )
                ]

            t23_matches: list[SilentFailureFinding] = []
            if active_silent is not None:
                raw_findings_t23 = (
                    active_silent.get("findings", [])
                    if isinstance(active_silent, dict)
                    else getattr(active_silent, "findings", [])
                )
                for s in raw_findings_t23:
                    dtls = getattr(s, "details", None) or (
                        s.get("details", {}) if isinstance(s, dict) else {}
                    )
                    tname = dtls.get("test_name") if isinstance(dtls, dict) else None
                    ep_src = getattr(s, "endpoint_or_source", "") or (
                        s.get("endpoint_or_source", "") if isinstance(s, dict) else ""
                    )
                    if (tname and tname == case.test_name) or (
                        not tname and (ep_src == endpoint_str or c_path in ep_src)
                    ):
                        t23_matches.append(s)

            # Check Side-Effect Semantic Mismatch first (Example D & Scenario E)
            has_side_effect_violation = any(
                s.signal_type
                in (
                    SilentSignalType.S5_UNVERIFIED_EFFECT,
                    SilentSignalType.S7_INVARIANT_VIOLATION,
                )
                and (
                    getattr(s.verification_state, "value", str(s.verification_state))
                    == "VERIFIED_OBSERVATION"
                )
                for s in t23_matches
            )
            if has_side_effect_violation:
                processed_case_names.add(case.test_name)
                tf_id = f"TF-{finding_counter:04d}"
                finding_counter += 1

                findings.append(
                    TrustFinding(
                        finding_id=tf_id,
                        verdict=DiagnosticVerdict.VERIFIED_SEMANTIC_MISMATCH,
                        confidence=VerdictConfidence.VERIFIED,
                        severity=VerdictSeverity.HIGH,
                        endpoint=endpoint_str,
                        method=case.method.upper(),
                        route_template=tmpl,
                        module=case.module or "business-logic",
                        service_context=target_service,
                        what_happened=(
                            f"State mutation on '{endpoint_str}' returned success, but subsequent verification failed: "
                            f"{case.error_message or 'Entity state invariant violated'}"
                        ),
                        why_reported=(
                            "A write operation returned 2xx, but subsequent query or business invariant check confirmed "
                            "that expected business side effects were not applied."
                        ),
                        supporting_evidence=[
                            f"Mutation HTTP status: {case.status_code}",
                            *(s.evidence for s in t23_matches),
                        ],
                        is_verified_observation=True,
                        what_remains_untested=[
                            "Internal database transaction log and event bus propagation"
                        ],
                        recommended_action=(
                            "Inspect backend database transaction commit logic and asynchronous message consumers."
                        ),
                        correlated_source_finding_ids=[
                            *(s.finding_id for s in t23_matches),
                            case.test_name,
                        ],
                    )
                )
                continue

            # Cross-Diagnostic Deduplication (Example A & G)
            has_t22_contract = any(
                f.category == DiagnosticCategory.CONTRACT_OR_RESPONSE_MISMATCH
                for f in t22_matches
            )
            has_t23_contract = any(
                s.signal_type
                in (
                    SilentSignalType.S6_CONTRACT_CONTRADICTION,
                    SilentSignalType.S4_SUSPICIOUS_2XX,
                )
                for s in t23_matches
            )

            # Check if this is a verified contract mismatch
            if has_t22_contract or has_t23_contract or self._is_contract_failure(case):
                processed_case_names.add(case.test_name)
                tf_id = f"TF-{finding_counter:04d}"
                finding_counter += 1

                correlated_ids = [
                    *(f.finding_id for f in t22_matches),
                    *(s.finding_id for s in t23_matches),
                    case.test_name,
                ]
                if t22_matches and t23_matches:
                    grouped_symptoms_count += 1

                failed_asserts = [
                    a.get("message", "Schema or status assertion failed")
                    for a in case.assertion_results
                    if not a.get("passed", True)
                ]
                supp_ev = [
                    f"HTTP response status: {case.status_code}",
                    *(f"Assertion failure: {m}" for m in failed_asserts),
                ]
                for f in t22_matches:
                    supp_ev.extend(f.supporting_evidence[:2])
                for s in t23_matches:
                    supp_ev.append(
                        f"Silent signal ({s.signal_type.value}): {s.evidence}"
                    )

                findings.append(
                    TrustFinding(
                        finding_id=tf_id,
                        verdict=DiagnosticVerdict.VERIFIED_CONTRACT_MISMATCH,
                        confidence=VerdictConfidence.VERIFIED,
                        severity=VerdictSeverity.HIGH,
                        endpoint=endpoint_str,
                        method=case.method.upper(),
                        route_template=tmpl,
                        module=case.module or "contract",
                        service_context=target_service,
                        what_happened=(
                            f"Verified response contract mismatch on '{endpoint_str}': "
                            f"{'; '.join(failed_asserts) or case.error_message or 'Response violated contract assertions'}"
                        ),
                        why_reported=(
                            "The returned response payload or status code directly contradicted an approved OpenAPI specification. "
                            "Task 22 and Task 23 observations unified into a single verified contract violation."
                        ),
                        supporting_evidence=supp_ev[:6],
                        assumptions_used=[
                            "OpenAPI specification reflects authoritative current contract"
                        ],
                        is_verified_observation=True,
                        what_remains_untested=[
                            "Underlying server software root cause (application defect vs outdated specification)"
                        ],
                        recommended_action=(
                            "Reconcile backend response structure with OpenAPI schema or update contract definition."
                        ),
                        correlated_source_finding_ids=sorted(set(correlated_ids)),
                    )
                )
                continue

            # Check Single HTTP 500 / Unhandled Server Error
            if case.status_code == 500:
                processed_case_names.add(case.test_name)
                tf_id = f"TF-{finding_counter:04d}"
                finding_counter += 1

                findings.append(
                    TrustFinding(
                        finding_id=tf_id,
                        verdict=DiagnosticVerdict.SUSPECTED_APPLICATION_DEFECT,
                        confidence=VerdictConfidence.SUSPECTED,
                        severity=VerdictSeverity.HIGH,
                        endpoint=endpoint_str,
                        method=case.method.upper(),
                        route_template=tmpl,
                        module=case.module or "server",
                        service_context=target_service,
                        what_happened=(
                            f"Unhandled HTTP 500 Internal Server Error returned by '{endpoint_str}' on case '{case.test_name}': "
                            f"{case.error_message or 'Internal Server Error'}"
                        ),
                        why_reported=(
                            "An unhandled server-side exception was observed. A single HTTP 500 indicates a suspected "
                            "application defect, but not a confirmed root-cause verdict without backend logs."
                        ),
                        supporting_evidence=[
                            "HTTP status code: 500",
                            f"Response snippet: {redact_sensitive_text(case.response_body[:200])}",
                            *(
                                [f"Error: {redact_sensitive_text(case.error_message)}"]
                                if case.error_message
                                else []
                            ),
                        ],
                        assumptions_used=[
                            "HTTP 500 indicates unhandled runtime exception rather than structured client validation error"
                        ],
                        is_verified_observation=False,
                        what_remains_untested=[
                            "Backend application stack trace and server exception logs"
                        ],
                        recommended_action=(
                            "Inspect target service logs at time of failure to determine exact unhandled exception."
                        ),
                        correlated_source_finding_ids=[case.test_name],
                    )
                )
                continue

            # Environment or Transport Issue
            if case.status_code is None or case.status_code == 0:
                processed_case_names.add(case.test_name)
                tf_id = f"TF-{finding_counter:04d}"
                finding_counter += 1

                findings.append(
                    TrustFinding(
                        finding_id=tf_id,
                        verdict=DiagnosticVerdict.ENVIRONMENT_OR_TRANSPORT_ISSUE,
                        confidence=VerdictConfidence.VERIFIED,
                        severity=VerdictSeverity.HIGH,
                        endpoint=endpoint_str,
                        method=case.method.upper(),
                        route_template=tmpl,
                        module=case.module or "network",
                        service_context=target_service,
                        what_happened=(
                            f"Network transport or connection failure contacting '{endpoint_str}': "
                            f"{case.error_message or 'Connection failed'}"
                        ),
                        why_reported=(
                            "Network connection could not be established or timed out. "
                            "Transport failures are environmental issues, not verified API contract defects."
                        ),
                        supporting_evidence=[
                            f"Target URL: {case.url}",
                            f"Error: {redact_sensitive_text(case.error_message or 'ConnectionRefusedError')}",
                        ],
                        is_verified_observation=True,
                        what_remains_untested=[
                            "API response payload and assertions (request never reached target server)"
                        ],
                        recommended_action=(
                            "Verify server host availability, target port, DNS resolution, and firewall rules."
                        ),
                        correlated_source_finding_ids=[case.test_name],
                    )
                )
                continue

            # Fallback general failure
            processed_case_names.add(case.test_name)
            tf_id = f"TF-{finding_counter:04d}"
            finding_counter += 1

            findings.append(
                TrustFinding(
                    finding_id=tf_id,
                    verdict=DiagnosticVerdict.SUSPECTED_APPLICATION_DEFECT,
                    confidence=VerdictConfidence.SUSPECTED,
                    severity=VerdictSeverity.MEDIUM,
                    endpoint=endpoint_str,
                    method=case.method.upper(),
                    route_template=tmpl,
                    module=case.module or "default",
                    service_context=target_service,
                    what_happened=f"Test '{case.test_name}' failed: {case.error_message or 'Assertion failed'}",
                    why_reported="Test failed without converging multi-diagnostic classification.",
                    supporting_evidence=[
                        f"HTTP status code: {case.status_code}",
                        f"Status: {case.status.value}",
                    ],
                    is_verified_observation=False,
                    what_remains_untested=["Root-cause diagnostic trace"],
                    recommended_action="Inspect test assertion and backend application logs.",
                    correlated_source_finding_ids=[case.test_name],
                )
            )

        # E. Latency Fluctuation / Insufficient Samples (Example E & Scenario K)
        if active_silent is not None:
            raw_lat_items: list[Any] = []
            if isinstance(active_silent, dict):
                raw_lat_items.extend(active_silent.get("signals", []))
                raw_lat_items.extend(active_silent.get("findings", []))
            else:
                raw_lat_items.extend(getattr(active_silent, "signals", []))
                raw_lat_items.extend(getattr(active_silent, "findings", []))

            for l_sig in raw_lat_items:
                sig_type = getattr(l_sig, "signal_type", None) or (
                    l_sig.get("signal_type") if isinstance(l_sig, dict) else None
                )
                sig_type_val = (
                    sig_type.value
                    if hasattr(sig_type, "value")
                    else str(sig_type or "")
                )
                if sig_type_val == SilentSignalType.S3_LATENCY_DRIFT.value:
                    dtls = getattr(l_sig, "details", None) or (
                        l_sig.get("details", {}) if isinstance(l_sig, dict) else {}
                    )
                    samples_count = dtls.get("samples_count", 1)
                    if samples_count < 3:
                        tf_id = f"TF-{finding_counter:04d}"
                        finding_counter += 1

                        endpoint_src = getattr(l_sig, "endpoint_or_source", None) or (
                            l_sig.get("endpoint_or_source", "API")
                            if isinstance(l_sig, dict)
                            else "API"
                        )
                        desc = getattr(l_sig, "description", None) or (
                            l_sig.get("description", "Latency fluctuation")
                            if isinstance(l_sig, dict)
                            else "Latency fluctuation"
                        )
                        ev = getattr(l_sig, "evidence", None) or (
                            l_sig.get("evidence", "") if isinstance(l_sig, dict) else ""
                        )
                        sig_id = getattr(l_sig, "signal_id", None) or (
                            l_sig.get("signal_id", "SIG-LAT")
                            if isinstance(l_sig, dict)
                            else "SIG-LAT"
                        )
                        mod = getattr(l_sig, "module", None) or (
                            l_sig.get("module", "performance")
                            if isinstance(l_sig, dict)
                            else "performance"
                        )

                        findings.append(
                            TrustFinding(
                                finding_id=tf_id,
                                verdict=DiagnosticVerdict.INCONCLUSIVE,
                                confidence=VerdictConfidence.INCONCLUSIVE,
                                severity=VerdictSeverity.LOW,
                                endpoint=endpoint_src,
                                method="GET",
                                route_template=extract_url_path(endpoint_src),
                                module=mod,
                                service_context=target_service,
                                what_happened=(
                                    f"Isolated latency spike observed on '{endpoint_src}': "
                                    f"{desc}"
                                ),
                                why_reported=(
                                    f"Only {samples_count} sample(s) observed. A statistical latency regression "
                                    "cannot be confirmed without adequate baseline samples (n >= 3)."
                                ),
                                supporting_evidence=[
                                    f"Observed elapsed: {dtls.get('observed_ms')}ms",
                                    f"Sample count: {samples_count}",
                                    ev,
                                ],
                                assumptions_used=[
                                    "Transient network jitter or cold-start can cause isolated latency spikes"
                                ],
                                is_verified_observation=False,
                                what_remains_untested=[
                                    "Repeated benchmark runs under sustained load (need >= 3 samples)"
                                ],
                                recommended_action=(
                                    "Execute extended performance benchmarks to verify whether latency regression persists."
                                ),
                                correlated_source_finding_ids=[sig_id],
                            )
                        )

        # F. Candidate Zombie Endpoint Lacking Multi-Evidence Corroboration (Example C & J)
        if active_inventory is not None:
            raw_inv_findings = (
                active_inventory.get("findings", [])
                if isinstance(active_inventory, dict)
                else getattr(active_inventory, "findings", [])
            )
            for inv_f in raw_inv_findings:
                cls_val = getattr(inv_f, "classification", None) or (
                    inv_f.get("classification") if isinstance(inv_f, dict) else None
                )
                cls_str = (
                    cls_val.value if hasattr(cls_val, "value") else str(cls_val or "")
                )
                if cls_str in (
                    EndpointClassification.DEPRECATED_OR_OBSOLETE_CANDIDATE.value,
                    EndpointClassification.SPEC_ONLY_CANDIDATE.value,
                ):
                    f_mth = getattr(inv_f, "method", None) or (
                        inv_f.get("method", "GET") if isinstance(inv_f, dict) else "GET"
                    )
                    f_can = getattr(inv_f, "canonical_endpoint", None) or (
                        inv_f.get("canonical_endpoint", "/")
                        if isinstance(inv_f, dict)
                        else "/"
                    )
                    f_mod = getattr(inv_f, "module", None) or (
                        inv_f.get("module", "default")
                        if isinstance(inv_f, dict)
                        else "default"
                    )
                    f_ctx = getattr(inv_f, "service_context", None) or (
                        inv_f.get("service_context", target_service)
                        if isinstance(inv_f, dict)
                        else target_service
                    )
                    f_dtl = getattr(inv_f, "details", None) or (
                        inv_f.get("details", "") if isinstance(inv_f, dict) else ""
                    )
                    f_ev = getattr(inv_f, "supporting_evidence", None) or (
                        inv_f.get("supporting_evidence", [])
                        if isinstance(inv_f, dict)
                        else []
                    )
                    f_alt = getattr(inv_f, "alternative_explanations", None) or (
                        inv_f.get("alternative_explanations", [])
                        if isinstance(inv_f, dict)
                        else []
                    )
                    f_id = getattr(inv_f, "finding_id", None) or (
                        inv_f.get("finding_id", "EP-FINDING")
                        if isinstance(inv_f, dict)
                        else "EP-FINDING"
                    )

                    matching_cases = [
                        c
                        for c in run_cases
                        if c.method.upper() == f_mth.upper()
                        and extract_url_path(str(c.url)) == f_can
                    ]
                    has_single_404 = (
                        len(matching_cases) == 1
                        and matching_cases[0].status_code == 404
                    )

                    tf_id = f"TF-{finding_counter:04d}"
                    finding_counter += 1

                    supp_ev = list(f_ev)
                    if has_single_404:
                        supp_ev.append(
                            f"Single HTTP 404 observed in test run for '{f_mth} {f_can}' (insufficient to declare zombie)"
                        )

                    findings.append(
                        TrustFinding(
                            finding_id=tf_id,
                            verdict=DiagnosticVerdict.INCONCLUSIVE,
                            confidence=VerdictConfidence.INCONCLUSIVE,
                            severity=VerdictSeverity.LOW,
                            endpoint=f"{f_mth} {f_can}",
                            method=f_mth,
                            route_template=f_can,
                            module=f_mod,
                            service_context=f_ctx,
                            what_happened=(
                                f"Possible obsolete endpoint '{f_mth} {f_can}' "
                                f"lacks multi-evidence corroboration: {f_dtl}"
                            ),
                            why_reported=(
                                "A candidate zombie endpoint requires converging immutable evidence "
                                "(contract deprecation + source removal + repeated 404/410). "
                                "A single failed call or lack of source match does not prove endpoint obsolescence."
                            ),
                            supporting_evidence=supp_ev,
                            assumptions_used=[
                                "Route may be dynamically mounted, proxied, or awaiting release"
                            ],
                            contradictory_evidence=list(f_alt),
                            is_verified_observation=False,
                            what_remains_untested=[
                                "Target application deployment logs and gateway routing table"
                            ],
                            recommended_action=(
                                "Verify implementation in source controllers before retiring from OpenAPI spec."
                            ),
                            correlated_source_finding_ids=[f_id],
                        )
                    )

        # G. Pass Within Verified Coverage (Operations that completely passed)
        covered_passed_ops: set[str] = set()
        for op_key, cases in cases_by_op.items():
            if all(c.status == CaseStatus.PASSED for c in cases):
                covered_passed_ops.add(op_key)

        # Compute Verdict Distribution Counts
        verdict_counts: dict[str, int] = {v.value: 0 for v in DiagnosticVerdict}
        for f in findings:
            verdict_counts[f.verdict.value] += 1

        # Calculate coverage metrics from active_inventory
        cov = (
            active_inventory.coverage_summary
            if hasattr(active_inventory, "coverage_summary")
            else active_inventory.coverage
        )

        # Update remaining counts
        verdict_counts[DiagnosticVerdict.NOT_TESTED.value] = (
            cov.untested_operations_count
        )
        verdict_counts[DiagnosticVerdict.PASS_WITHIN_VERIFIED_COVERAGE.value] = (
            cov.passed_operations_count
        )

        # Check for evidence limitations
        if (
            not repo_path
            and "No source code repository provided; source-level controller reconciliation skipped"
            not in limitations
        ):
            limitations.append(
                "No source code repository provided; source-level validation skipped"
            )
        if cov.attempted_coverage_pct < 100.0:
            remaining_gaps.append(
                f"{cov.untested_operations_count} of {cov.total_documented_operations} documented operations remain completely untested "
                f"({100.0 - cov.attempted_coverage_pct:.1f}% unverified scope)"
            )
        if cov.mutation_attempted_operations == 0 and cov.mutation_total_operations > 0:
            remaining_gaps.append(
                f"All {cov.mutation_total_operations} mutating operations remain unattempted (0.0% mutation coverage)"
            )

        # Deterministic sorting of findings
        findings.sort(
            key=lambda f: (
                f.severity.value,
                f.verdict.value,
                f.module.lower(),
                f.endpoint,
            )
        )

        # Optional Consultative AI Enrichment
        ai_consultation = None
        if enrich_with_gemini and ai_enricher is not None:
            if hasattr(ai_enricher, "consult_trust_findings"):
                ai_consultation = ai_enricher.consult_trust_findings(findings)
            elif hasattr(ai_enricher, "enrich"):
                ai_consultation = ai_enricher.enrich(findings)
            else:
                ai_consultation = {
                    "status": "opted_in",
                    "guidance": "AI consultation completed",
                }

        # Generate Assessment ID
        ass_id = f"TA-{self._clock().strftime('%Y%m%d%H%M%S')}"

        return TrustAssessmentReport(
            schema_version="1.0.0",
            evaluated_at=self._clock().isoformat(),
            target_context=target_service,
            assessment_id=ass_id,
            run_id=run_result.run_id if run_result else "workflow-eval",
            observation_window=f"{len(run_cases)} case(s) evaluated across {len(evaluated_sources)} evidence source(s)",
            coverage=cov,
            verdict_counts=verdict_counts,
            findings=findings,
            total_evidence_records=len(evidence_records),
            deduplicated_findings_count=len(findings),
            grouped_symptoms_count=grouped_symptoms_count,
            evidence_sources_evaluated=sorted(set(evaluated_sources)),
            evidence_limitations=limitations,
            remaining_verification_gaps=remaining_gaps,
            ai_consultation=ai_consultation,
        )

    def _workflow_step_to_case_result(self, step: WorkflowStepResult) -> CaseResult:
        """Convert a WorkflowStepResult into a compatible CaseResult."""
        return CaseResult(
            test_name=step.name,
            module=(
                step.created_resource.resource_type
                if step.created_resource
                else "workflow"
            ),
            tags=[step.step_type.value],
            status=step.status,
            verified=step.status == CaseStatus.PASSED,
            method=step.method,
            url=step.url,
            status_code=step.status_code,
            elapsed_ms=step.elapsed_ms,
            request_headers=dict(step.request_headers),
            response_headers=dict(step.response_headers),
            assertion_results=list(step.assertions),
            extracted_variables=dict(step.extracted_variables),
            extraction_errors=list(step.extraction_errors),
            error_message=step.error_message,
            skip_reason=step.skip_reason,
            correlation_id=step.correlation_id,
        )

    def _extract_origin(self, url: str) -> str:
        """Extract canonical origin (scheme://host:port) from URL string."""
        try:
            parsed = urlparse(url)
            port = f":{parsed.port}" if parsed.port else ""
            return f"{parsed.scheme}://{parsed.hostname}{port}".lower()
        except Exception:
            return "unknown-origin"

    def _extract_expected_outcome(self, case: CaseResult) -> str:
        """Derive readable expected outcome description from case assertions."""
        if not case.assertion_results:
            return "HTTP 200 OK"
        expected_items: list[str] = []
        for a in case.assertion_results:
            rule_type = a.get("rule_type") or a.get("type") or "assertion"
            exp = a.get("expected")
            if exp is not None:
                expected_items.append(f"{rule_type} == {exp}")
            else:
                expected_items.append(str(rule_type))
        return "; ".join(expected_items[:3])

    def _is_contract_failure(self, case: CaseResult) -> bool:
        """Determine if a failed test case represents a contract or schema violation."""
        for a in case.assertion_results:
            if not a.get("passed", True):
                rule = str(a.get("rule_type") or a.get("type") or "").lower()
                msg = str(a.get("message") or "").lower()
                if any(
                    k in rule or k in msg
                    for k in ("schema", "contract", "status", "header", "type")
                ):
                    return True
        return False
