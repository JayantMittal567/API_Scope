"""Failure investigation and evidence-based root-cause diagnostic service (Task 22).

Analyzes recorded test runs and workflow outcomes, differentiates application defects
from fixture omissions, test definition issues, environment faults, and policy blocks,
performs causal workflow triage, and reconciles evidence against API contracts.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apiscope.contract.models import CanonicalContract
from apiscope.core.exceptions import ConfigurationError
from apiscope.core.logging_config import get_logger, redact_sensitive_text
from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.execution.models import WorkflowExecutionResult
from apiscope.fixtures.models import FixturePreparationReport
from apiscope.generators.ai_generator import (
    LiveHttpLLMProvider,
    LLMConfig,
    LLMProviderProtocol,
)
from apiscope.investigation.models import (
    ConfidenceLevel,
    DiagnosticCategory,
    DiagnosticDisposition,
    ExecutionStage,
    FailureInvestigationReport,
    FindingSeverity,
    InvestigationFinding,
)

logger = get_logger(__name__)

# Keywords for execution safety policy blocks
_POLICY_BLOCK_PATTERNS = [
    "mutation",
    "mutation fence",
    "targetaccesspolicy",
    "executionsafetypolicy",
    "isolated test environment",
    "confirm_isolated_env",
    "unauthorized target",
    "production environment fence",
    "unapproved scenario",
    "approval required",
]

# Keywords for transport / network errors
_TRANSPORT_ERROR_PATTERNS = [
    "timeout",
    "timed out",
    "readtimeout",
    "connecttimeout",
    "pooltimeout",
    "connecterror",
    "connection refused",
    "connection reset",
    "remotedisconnected",
    "failed to establish a new connection",
    "getaddrinfo failed",
    "nameresolutionerror",
    "dns",
    "sslerror",
    "tls",
    "certificate verify failed",
    "redirectpolicyviolation",
    "redirectlooperror",
    "ssrf",
]


class FailureInvestigationService:
    """Deterministic failure investigation engine with optional AI hypotheses."""

    def __init__(self, db_manager: Any | None = None) -> None:
        """Initialize failure investigation service.

        Args:
            db_manager: Optional DatabaseManager for retrieving historical runs.
        """
        self.db_manager = db_manager

    def investigate_run(
        self,
        run_result: RunResult,
        contract: CanonicalContract | None = None,
        compiler_report: Any | None = None,
        fixture_report: FixturePreparationReport | None = None,
        with_ai: bool = False,
        llm_config: LLMConfig | None = None,
        llm_provider: LLMProviderProtocol | None = None,
    ) -> FailureInvestigationReport:
        """Deterministically investigate a recorded test or workflow run result.

        Args:
            run_result: Executed RunResult containing test cases and outcomes.
            contract: Optional canonical contract for response/status reconciliation.
            compiler_report: Optional test plan compiler report for scenario provenance.
            fixture_report: Optional fixture preparation report for fixture provenance.
            with_ai: Whether to query Gemini for optional root-cause hypotheses.
            llm_config: Optional LLM provider configuration.
            llm_provider: Optional injected LLM provider (useful for testing).

        Returns:
            Structured FailureInvestigationReport with categorized findings.
        """
        total_cases = len(run_result.results)
        findings: list[InvestigationFinding] = []

        # Group cases by workflow if workflow tags or prefixes exist
        workflow_groups = self._group_cases_by_workflow(run_result.results)

        finding_counter = 1
        for _wf_id, cases in workflow_groups.items():
            wf_findings, finding_counter = self._analyze_case_sequence(
                cases=cases,
                run_id=run_result.run_id,
                start_counter=finding_counter,
                contract=contract,
                compiler_report=compiler_report,
                fixture_report=fixture_report,
            )
            findings.extend(wf_findings)

        # Compute summary metrics
        category_counts: dict[str, int] = defaultdict(int)
        disposition_counts: dict[str, int] = defaultdict(int)
        primary_failures_count = 0
        downstream_count = 0

        for f in findings:
            category_counts[f.category.value] += 1
            disposition_counts[f.disposition.value] += 1
            if f.is_primary_failure:
                primary_failures_count += 1
            else:
                downstream_count += 1

        has_app_defects = any(
            f.is_primary_failure
            and f.category
            in (
                DiagnosticCategory.SUSPECTED_APPLICATION_FAILURE,
                DiagnosticCategory.CONTRACT_OR_RESPONSE_MISMATCH,
            )
            for f in findings
        )
        has_test_or_fixture_issues = any(
            f.category
            in (
                DiagnosticCategory.TEST_DEFINITION_ISSUE,
                DiagnosticCategory.FIXTURE_OR_DEPENDENCY_ISSUE,
            )
            for f in findings
        )
        has_env_or_policy_blocks = any(
            f.category
            in (
                DiagnosticCategory.ACCESS_OR_POLICY_BLOCK,
                DiagnosticCategory.ENVIRONMENT_OR_CONFIGURATION_ISSUE,
                DiagnosticCategory.NETWORK_OR_TRANSPORT_ISSUE,
            )
            for f in findings
        )

        missing_evidence_gaps = self._identify_evidence_gaps(
            _run_result=run_result,
            contract=contract,
            findings=findings,
        )

        summary_text = self._build_executive_summary(
            total_cases=total_cases,
            total_failures=len(findings),
            primary_count=primary_failures_count,
            downstream_count=downstream_count,
            has_app_defects=has_app_defects,
            has_test_or_fixture=has_test_or_fixture_issues,
            has_env_or_policy=has_env_or_policy_blocks,
        )

        ai_analysis_data: dict[str, Any] | None = None
        if with_ai and findings:
            ai_analysis_data = self._generate_ai_hypotheses(
                findings=findings,
                _run_result=run_result,
                _contract=contract,
                llm_config=llm_config,
                llm_provider=llm_provider,
            )
            if ai_analysis_data and "hypotheses" in ai_analysis_data:
                for idx, hyp in enumerate(ai_analysis_data["hypotheses"]):
                    if idx < len(findings):
                        findings[idx].ai_hypothesis = str(hyp)

        investigation_id = (
            f"inv-{run_result.run_id[:8]}-{int(datetime.now(UTC).timestamp())}"
        )

        return FailureInvestigationReport(
            investigation_id=investigation_id,
            run_id=run_result.run_id,
            evaluated_at=datetime.now(UTC).isoformat(),
            total_cases_evaluated=total_cases,
            total_failures_detected=len(findings),
            primary_failures_count=primary_failures_count,
            downstream_consequences_count=downstream_count,
            category_counts=dict(category_counts),
            disposition_counts=dict(disposition_counts),
            findings=findings,
            has_application_defects=has_app_defects,
            has_test_or_fixture_issues=has_test_or_fixture_issues,
            has_environment_or_policy_blocks=has_env_or_policy_blocks,
            summary=summary_text,
            missing_evidence_gaps=missing_evidence_gaps,
            ai_analysis=ai_analysis_data,
        )

    def investigate_workflow(
        self,
        workflow_result: WorkflowExecutionResult,
        contract: CanonicalContract | None = None,
        with_ai: bool = False,
        llm_config: LLMConfig | None = None,
        llm_provider: LLMProviderProtocol | None = None,
    ) -> FailureInvestigationReport:
        """Investigate a WorkflowExecutionResult directly.

        Args:
            workflow_result: The executed workflow result.
            contract: Optional canonical contract.
            with_ai: Whether to query Gemini for hypotheses.
            llm_config: Optional LLM config.
            llm_provider: Optional injected provider.

        Returns:
            Structured FailureInvestigationReport.
        """
        run_res = workflow_result.to_run_result()
        return self.investigate_run(
            run_result=run_res,
            contract=contract,
            with_ai=with_ai,
            llm_config=llm_config,
            llm_provider=llm_provider,
        )

    def load_run_from_file(self, file_path: Path | str) -> RunResult:
        """Load and normalize a RunResult from a JSON report or workflow export file.

        Args:
            file_path: Path to the JSON file.

        Returns:
            Reconstructed RunResult.

        Raises:
            ConfigurationError: If the file is missing or contains invalid JSON structure.
        """
        p = Path(file_path)
        if not p.is_file():
            raise ConfigurationError(f"Run file not found: {p}")

        try:
            content = p.read_text(encoding="utf-8")
            data = json.loads(content)
        except Exception as err:
            raise ConfigurationError(f"Failed to parse JSON in '{p}': {err}") from err

        return self.parse_run_data(data)

    def parse_run_data(self, data: dict[str, Any]) -> RunResult:
        """Convert arbitrary test run JSON structure into a normalized RunResult."""
        # 1. Direct ApiScope RunResult format
        if "results" in data and "total_tests" in data and "run_id" in data:
            case_results: list[CaseResult] = []
            for c in data.get("results", []):
                case_results.append(
                    CaseResult(
                        test_name=str(c.get("test_name", "Unnamed Case")),
                        module=str(c.get("module", "default")),
                        tags=list(c.get("tags", [])),
                        status=CaseStatus(c.get("status", CaseStatus.PASSED.value)),
                        verified=bool(c.get("verified", False)),
                        method=str(c.get("method", "GET")),
                        url=str(c.get("url", "")),
                        status_code=c.get("status_code"),
                        elapsed_ms=c.get("elapsed_ms"),
                        request_headers=dict(c.get("request_headers", {})),
                        response_headers=dict(c.get("response_headers", {})),
                        assertion_results=list(c.get("assertion_results", [])),
                        extracted_variables=dict(c.get("extracted_variables", {})),
                        extraction_errors=list(c.get("extraction_errors", [])),
                        error_message=c.get("error_message"),
                        skip_reason=c.get("skip_reason"),
                        row_index=c.get("row_index"),
                        response_body=str(c.get("response_body", "")),
                        correlation_id=str(c.get("correlation_id", "")),
                    )
                )
            return RunResult(
                run_id=str(data.get("run_id", "imported-run")),
                environment_name=data.get("environment_name"),
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                duration_ms=float(data.get("duration_ms", 0.0)),
                total_tests=int(data.get("total_tests", len(case_results))),
                passed_tests=int(data.get("passed_tests", 0)),
                failed_tests=int(data.get("failed_tests", 0)),
                error_tests=int(data.get("error_tests", 0)),
                skipped_tests=int(data.get("skipped_tests", 0)),
                results=case_results,
                has_warnings=bool(data.get("has_warnings", False)),
                blocked_tests=int(data.get("blocked_tests", 0)),
            )

        # 2. WorkflowExecutionResult format
        if "workflow_id" in data and (
            "setup_results" in data or "test_results" in data
        ):
            case_results = []
            for step_key in ("setup_results", "test_results", "cleanup_results"):
                for s in data.get(step_key, []):
                    case_results.append(
                        CaseResult(
                            test_name=f"[{data.get('workflow_id')}] [{s.get('step_type', 'STEP')}:{s.get('step_number', 1)}] {s.get('name', 'Step')}",
                            module="workflow",
                            tags=[
                                f"workflow:{str(data.get('workflow_id')).lower()}",
                                f"step-type:{str(s.get('step_type', 'test')).lower()}",
                            ],
                            status=CaseStatus(s.get("status", CaseStatus.PASSED.value)),
                            verified=(s.get("status") == CaseStatus.PASSED.value),
                            method=str(s.get("method", "GET")),
                            url=str(s.get("url", "")),
                            status_code=s.get("status_code"),
                            elapsed_ms=s.get("elapsed_ms", 0.0),
                            request_headers=dict(s.get("request_headers", {})),
                            response_headers=dict(s.get("response_headers", {})),
                            assertion_results=list(s.get("assertions", [])),
                            extracted_variables=dict(s.get("extracted_variables", {})),
                            extraction_errors=list(s.get("extraction_errors", [])),
                            error_message=s.get("error_message"),
                            skip_reason=s.get("skip_reason"),
                            correlation_id=str(s.get("correlation_id", "")),
                        )
                    )
            return RunResult(
                run_id=f"wf-run-{data.get('workflow_id', 'imported')}",
                environment_name=data.get("environment_name"),
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                duration_ms=float(data.get("duration_ms", 0.0)),
                total_tests=len(case_results),
                passed_tests=sum(
                    1 for c in case_results if c.status == CaseStatus.PASSED
                ),
                failed_tests=sum(
                    1 for c in case_results if c.status == CaseStatus.FAILED
                ),
                error_tests=sum(
                    1 for c in case_results if c.status == CaseStatus.ERROR
                ),
                skipped_tests=sum(
                    1 for c in case_results if c.status == CaseStatus.SKIPPED
                ),
                blocked_tests=sum(
                    1 for c in case_results if c.status == CaseStatus.BLOCKED
                ),
                results=case_results,
            )

        # 3. StockPile-style report JSON format (cases array)
        if "cases" in data:
            case_results = []
            for c in data.get("cases", []):
                status_str = str(c.get("status", "PASSED")).upper()
                try:
                    case_status = CaseStatus(status_str)
                except ValueError:
                    case_status = CaseStatus.FAILED

                case_results.append(
                    CaseResult(
                        test_name=str(c.get("name", "Test Case")),
                        module=str(c.get("module", "default")),
                        tags=list(c.get("tags", [])),
                        status=case_status,
                        verified=bool(c.get("verified", False)),
                        method=str(c.get("method", "GET")),
                        url=str(c.get("url", "")),
                        status_code=c.get("http_status", c.get("status_code")),
                        elapsed_ms=c.get("elapsed_ms"),
                        request_headers={},
                        response_headers={},
                        assertion_results=list(c.get("assertions", [])),
                        extracted_variables={},
                        extraction_errors=[],
                        error_message=c.get("error"),
                    )
                )
            return RunResult(
                run_id=str(data.get("run_id", "imported-cases-run")),
                environment_name=data.get("environment_name"),
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                duration_ms=float(data.get("duration_ms", 0.0)),
                total_tests=len(case_results),
                passed_tests=sum(
                    1 for c in case_results if c.status == CaseStatus.PASSED
                ),
                failed_tests=sum(
                    1 for c in case_results if c.status == CaseStatus.FAILED
                ),
                error_tests=sum(
                    1 for c in case_results if c.status == CaseStatus.ERROR
                ),
                skipped_tests=sum(
                    1 for c in case_results if c.status == CaseStatus.SKIPPED
                ),
                results=case_results,
            )

        raise ConfigurationError("Unrecognized run result file schema or structure.")

    def export_report(
        self,
        report: FailureInvestigationReport,
        output_path: Path | str,
        format: str = "json",
    ) -> Path:
        """Serialize and export investigation report to disk.

        Args:
            report: FailureInvestigationReport instance.
            output_path: Target output path.
            format: 'json' or 'yaml'.

        Returns:
            Resolved Path of the saved report.
        """
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)

        if format.lower() == "yaml":
            content = report.to_yaml()
        else:
            content = report.to_json(indent=2)

        p.write_text(content, encoding="utf-8")
        return p

    # --- Internal Deterministic Diagnostics ---

    def _group_cases_by_workflow(
        self, cases: list[CaseResult]
    ) -> dict[str, list[CaseResult]]:
        """Group cases by workflow ID to enable accurate causal sequence analysis."""
        groups: dict[str, list[CaseResult]] = defaultdict(list)
        for c in cases:
            wf_id = "default"
            # Extract from tags
            for t in c.tags:
                if t.startswith("workflow:"):
                    wf_id = t.split(":", 1)[1]
                    break
            # Extract from test_name brackets e.g. "[WF-1] ..."
            if wf_id == "default":
                match = re.match(r"^\[([a-zA-Z0-9_-]+)\]", c.test_name)
                if match:
                    wf_id = match.group(1).lower()

            groups[wf_id].append(c)
        return groups

    def _analyze_case_sequence(
        self,
        cases: list[CaseResult],
        run_id: str,
        start_counter: int,
        contract: CanonicalContract | None,
        compiler_report: Any | None,
        fixture_report: FixturePreparationReport | None,
    ) -> tuple[list[InvestigationFinding], int]:
        """Analyze a sequence of cases preserving setup -> test -> cleanup causality."""
        findings: list[InvestigationFinding] = []
        counter = start_counter

        primary_finding_in_chain: InvestigationFinding | None = None

        for case in cases:
            # Check if case succeeded
            if case.status == CaseStatus.PASSED:
                continue

            phase = self._detect_execution_stage(case)
            wf_id = self._extract_workflow_id(case)

            # Determine whether this step is a primary failure or a downstream consequence
            is_downstream = False
            linked_primary_id: str | None = None

            if (
                primary_finding_in_chain is not None
                and phase != ExecutionStage.CLEANUP
                and case.status in (CaseStatus.SKIPPED, CaseStatus.BLOCKED)
            ):
                # This step was skipped or blocked because an upstream step in this workflow failed
                is_downstream = True
                linked_primary_id = primary_finding_in_chain.finding_id
                primary_finding_in_chain.downstream_impact_count += 1

            finding = self._diagnose_single_case(
                case=case,
                finding_id=f"FINDING-{counter:03d}",
                run_id=run_id,
                phase=phase,
                workflow_id=wf_id,
                is_downstream=is_downstream,
                linked_primary_id=linked_primary_id,
                contract=contract,
                compiler_report=compiler_report,
                fixture_report=fixture_report,
            )

            # If this is the first primary failure in the chain, record it as active primary
            if finding.is_primary_failure and primary_finding_in_chain is None:
                primary_finding_in_chain = finding

            findings.append(finding)
            counter += 1

        return findings, counter

    def _detect_execution_stage(self, case: CaseResult) -> ExecutionStage:
        """Identify execution phase from tags, test names, or HTTP method."""
        if case.method.upper() == "POLICY" or "pre-execution" in case.test_name.lower():
            return ExecutionStage.GATE

        for tag in case.tags:
            tag_lower = tag.lower()
            if "step-type:setup" in tag_lower or "setup" in tag_lower:
                return ExecutionStage.SETUP
            if "step-type:cleanup" in tag_lower or "cleanup" in tag_lower:
                return ExecutionStage.CLEANUP
            if "step-type:test" in tag_lower:
                return ExecutionStage.TEST

        name_upper = case.test_name.upper()
        if "[SETUP:" in name_upper:
            return ExecutionStage.SETUP
        if "[CLEANUP:" in name_upper:
            return ExecutionStage.CLEANUP
        if "[TEST:" in name_upper:
            return ExecutionStage.TEST

        if case.status == CaseStatus.CLEANUP_FAILED:
            return ExecutionStage.CLEANUP

        return ExecutionStage.TEST

    def _extract_workflow_id(self, case: CaseResult) -> str | None:
        """Extract associated workflow identifier from tags or test name."""
        for tag in case.tags:
            if tag.startswith("workflow:"):
                return tag.split(":", 1)[1]
        match = re.match(r"^\[([a-zA-Z0-9_-]+)\]", case.test_name)
        if match:
            return match.group(1)
        return None

    def _diagnose_single_case(
        self,
        case: CaseResult,
        finding_id: str,
        run_id: str,
        phase: ExecutionStage,
        workflow_id: str | None,
        is_downstream: bool,
        linked_primary_id: str | None,
        contract: CanonicalContract | None,
        compiler_report: Any | None,
        fixture_report: FixturePreparationReport | None,
    ) -> InvestigationFinding:
        """Deterministically diagnose root cause and categorize single case failure."""
        err_msg = (case.error_message or "").strip()
        skip_msg = (case.skip_reason or "").strip()
        combined_text = f"{err_msg} {skip_msg}".lower()

        # Check compiler report for scenario approval or oracle evidence
        scenario_origin = ""
        is_unapproved_scenario = False
        if compiler_report is not None:
            scenarios = getattr(compiler_report, "scenarios", [])
            for sc in scenarios:
                sc_title = getattr(sc, "title", "")
                sc_id = getattr(sc, "scenario_id", "")
                if sc_id in case.test_name or (sc_title and sc_title in case.test_name):
                    scenario_origin = str(getattr(sc, "origin", ""))
                    if (
                        getattr(sc, "status", None)
                        and str(getattr(sc, "status", "")) == "NEEDS_REVIEW"
                    ):
                        is_unapproved_scenario = True
                    break

        # Check fixture report for missing dependencies
        missing_fixture_name = ""
        if fixture_report is not None:
            for fix in getattr(fixture_report, "fixtures", []):
                fix_name = getattr(fix, "name", "")
                fix_status = str(getattr(fix, "status", "")).upper()
                if (
                    fix_name
                    and (fix_name in case.url or fix_name in case.test_name)
                    and fix_status in ("MISSING", "INVALID")
                ):
                    missing_fixture_name = fix_name
                    break

        # 1. Downstream Skip / Cascade Handling
        if is_downstream:
            return InvestigationFinding(
                finding_id=finding_id,
                run_id=run_id,
                workflow_id=workflow_id,
                test_name=case.test_name,
                method=case.method,
                url=case.url,
                execution_phase=phase,
                is_primary_failure=False,
                primary_finding_id=linked_primary_id,
                observed_symptom=(
                    f"Step not executed ({case.status.value}): "
                    f"{skip_msg or err_msg or 'Upstream dependency or setup failed'}"
                ),
                category=DiagnosticCategory.FIXTURE_OR_DEPENDENCY_ISSUE,
                confidence=ConfidenceLevel.VERIFIED_OBSERVATION,
                severity=FindingSeverity.INFO,
                disposition=DiagnosticDisposition.FIXTURE_RESOLUTION_REQUIRED,
                candidate_causes=[
                    f"Upstream producer step failed, leaving required entity uncreated. Linked to {linked_primary_id}.",
                    "Prerequisite test data fixture not resolved before step dispatch.",
                ],
                alternative_explanations=[],
                supporting_evidence=[
                    f"Step status: {case.status.value}",
                    f"Upstream dependency indicator: {skip_msg or err_msg}",
                    "Zero HTTP requests dispatched for this step",
                ],
                recommended_next_step=(
                    f"Resolve primary failure {linked_primary_id} first. Do not attempt to fix or modify this dependent step."
                ),
                status_code=case.status_code,
                elapsed_ms=case.elapsed_ms,
                error_message=err_msg or None,
            )

        # Pre-check for fixture/dependency flags before policy block
        has_extraction_errors = bool(case.extraction_errors)
        has_unresolved_variable = (
            "missing variable" in combined_text
            or "unresolved variable" in combined_text
            or "missing fixture" in combined_text
            or "{{" in case.url
            or ("{" in case.url and "}" in case.url)
            or bool(missing_fixture_name)
        )
        is_404_synthetic = case.status_code == 404 and (
            "synthetic" in case.test_name.lower()
            or "chain_" in case.test_name.lower()
            or "fixture" in case.test_name.lower()
        )

        # 2. Access or Policy Block
        is_policy_block = (
            case.method.upper() == "POLICY"
            or is_unapproved_scenario
            or any(p in combined_text for p in _POLICY_BLOCK_PATTERNS)
            or (
                case.status == CaseStatus.BLOCKED
                and not has_unresolved_variable
                and not has_extraction_errors
            )
        ) and case.status_code is None

        if (
            is_policy_block
            and not has_unresolved_variable
            and not has_extraction_errors
        ):
            policy_causes = [
                "Mutating operation attempted without required --allow-mutation or --confirm-isolated-env authorization",
                "Target origin is not permitted under TargetAccessPolicy allowlist",
                "Protected production environment fence prevented test execution",
            ]
            if is_unapproved_scenario:
                policy_causes.insert(
                    0,
                    "Test scenario was generated with status 'NEEDS_REVIEW' and requires explicit human approval.",
                )
            return InvestigationFinding(
                finding_id=finding_id,
                run_id=run_id,
                workflow_id=workflow_id,
                test_name=case.test_name,
                method=case.method,
                url=case.url,
                execution_phase=phase,
                is_primary_failure=True,
                observed_symptom=f"Execution blocked by ApiScope safety policy: {err_msg or 'Unauthorized mutation or target'}",
                category=DiagnosticCategory.ACCESS_OR_POLICY_BLOCK,
                confidence=ConfidenceLevel.VERIFIED_OBSERVATION,
                severity=FindingSeverity.HIGH,
                disposition=DiagnosticDisposition.POLICY_AUTHORIZATION_REQUIRED,
                candidate_causes=policy_causes,
                alternative_explanations=[
                    "Test environment configuration erroneously labeled target as production"
                ],
                supporting_evidence=[
                    f"Safety policy check aborted before dispatch: {err_msg}",
                    f"Target URL: {case.url}",
                    *(
                        [f"Scenario origin: {scenario_origin}"]
                        if scenario_origin
                        else []
                    ),
                    "Zero HTTP calls dispatched across network",
                ],
                recommended_next_step=(
                    "Verify target environment isolation. Supply '--allow-target <origin>', "
                    "'--allow-mutation', and '--confirm-isolated-env' flags if authorized."
                ),
                status_code=None,
                elapsed_ms=case.elapsed_ms,
                error_message=err_msg or None,
            )

        # 3. Network or Transport Issue
        if case.status_code is None and any(
            p in combined_text for p in _TRANSPORT_ERROR_PATTERNS
        ):
            is_timeout = "timeout" in combined_text
            is_conn_refused = any(
                p in combined_text
                for p in ("connection refused", "connecterror", "failed to establish")
            )

            if is_timeout:
                symptom = f"Request timed out awaiting server response: {err_msg}"
                causes = [
                    "Target server took longer than client timeout threshold to process request",
                    "Server-side request processing state is uncertain (may have succeeded, failed, or deadlocked)",
                    "Downstream database query or third-party service latency",
                ]
                recom = (
                    "Inspect target application logs around timestamp to verify if the request "
                    "was received and processed. Consider increasing client timeout if endpoint is legitimately slow."
                )
            elif is_conn_refused:
                symptom = f"Network connection refused: {err_msg}"
                causes = [
                    "Target application server is not running or not listening on specified port",
                    "Firewall or network routing policy rejected connection to target host",
                ]
                recom = "Ensure the target application server is running and accessible at the specified origin."
            else:
                symptom = f"Network or transport failure: {err_msg}"
                causes = [
                    "DNS name resolution failure",
                    "TLS/SSL handshake or certificate verification error",
                    "SSRF redirect security policy violation",
                ]
                recom = "Check network DNS settings, SSL certificate validity, and redirect paths."

            return InvestigationFinding(
                finding_id=finding_id,
                run_id=run_id,
                workflow_id=workflow_id,
                test_name=case.test_name,
                method=case.method,
                url=case.url,
                execution_phase=phase,
                is_primary_failure=True,
                observed_symptom=symptom,
                category=DiagnosticCategory.NETWORK_OR_TRANSPORT_ISSUE,
                confidence=ConfidenceLevel.VERIFIED_OBSERVATION,
                severity=FindingSeverity.HIGH,
                disposition=DiagnosticDisposition.NETWORK_OR_INFRASTRUCTURE_ISSUE,
                candidate_causes=causes,
                alternative_explanations=[
                    "Transient network partition or router overload"
                ],
                supporting_evidence=[
                    f"Transport error recorded: {err_msg}",
                    "HTTP response status code: None (no response received)",
                ],
                recommended_next_step=recom,
                status_code=None,
                elapsed_ms=case.elapsed_ms,
                error_message=err_msg or None,
            )

        # 4. Fixture or Dependency Issue
        if has_extraction_errors or has_unresolved_variable or is_404_synthetic:
            extraction_summary = "; ".join(case.extraction_errors)
            symptom = f"Variable extraction or fixture resolution failure: {extraction_summary or err_msg or case.url}"
            candidate_fixture_causes = [
                "Producer endpoint response omitted the expected entity ID field",
                "Response schema drifted from JSONPath extraction rule",
                "Required fixture variable was not declared or seeded before test execution",
            ]
            if missing_fixture_name:
                candidate_fixture_causes.insert(
                    0,
                    f"Fixture '{missing_fixture_name}' was unresolved or missing in fixture preparation report.",
                )
            return InvestigationFinding(
                finding_id=finding_id,
                run_id=run_id,
                workflow_id=workflow_id,
                test_name=case.test_name,
                method=case.method,
                url=case.url,
                execution_phase=phase,
                is_primary_failure=True,
                observed_symptom=symptom,
                category=DiagnosticCategory.FIXTURE_OR_DEPENDENCY_ISSUE,
                confidence=ConfidenceLevel.VERIFIED_OBSERVATION,
                severity=FindingSeverity.MEDIUM,
                disposition=DiagnosticDisposition.FIXTURE_RESOLUTION_REQUIRED,
                candidate_causes=candidate_fixture_causes,
                alternative_explanations=[
                    "Entity was created but immediately deleted or rolled back by server transaction"
                ],
                supporting_evidence=[
                    *(f"Extraction error: {e}" for e in case.extraction_errors),
                    *(
                        [f"Unresolved placeholder in URL: {case.url}"]
                        if "{" in case.url
                        else []
                    ),
                    *(
                        [f"Missing fixture detected: {missing_fixture_name}"]
                        if missing_fixture_name
                        else []
                    ),
                    f"HTTP status code: {case.status_code}",
                ],
                recommended_next_step=(
                    "Inspect producer endpoint response body and verify that JSONPath "
                    "or header extraction path matches the actual payload structure."
                ),
                status_code=case.status_code,
                elapsed_ms=case.elapsed_ms,
                error_message=err_msg or None,
            )

        # 5. Cleanup Issue
        if phase == ExecutionStage.CLEANUP or case.status == CaseStatus.CLEANUP_FAILED:
            return InvestigationFinding(
                finding_id=finding_id,
                run_id=run_id,
                workflow_id=workflow_id,
                test_name=case.test_name,
                method=case.method,
                url=case.url,
                execution_phase=ExecutionStage.CLEANUP,
                is_primary_failure=True,
                observed_symptom=(
                    f"Cleanup operation failed: {case.method} {case.url} returned {case.status_code or 'error'}"
                ),
                category=DiagnosticCategory.CLEANUP_ISSUE,
                confidence=ConfidenceLevel.VERIFIED_OBSERVATION,
                severity=FindingSeverity.MEDIUM,
                disposition=DiagnosticDisposition.CLEANUP_FAILURE_DETECTED,
                candidate_causes=[
                    "Cleanup endpoint returned 4xx/5xx or timed out during resource teardown",
                    "Target entity was already deleted by cascading foreign key rules",
                    "Cleanup mutation was not authorized or endpoint route was invalid",
                ],
                alternative_explanations=[
                    "Target resource locked by another concurrent process"
                ],
                supporting_evidence=[
                    f"Cleanup action: {case.method} {case.url}",
                    f"HTTP status code: {case.status_code}",
                    f"Error details: {err_msg}",
                ],
                recommended_next_step=(
                    "Verify cleanup deletion URL and permissions. Inspect target database to "
                    "manually verify whether residual test data remains."
                ),
                status_code=case.status_code,
                elapsed_ms=case.elapsed_ms,
                error_message=err_msg or None,
            )

        # 6. Test Definition Issue (Syntax, unsupported assertions, or speculative oracles)
        is_test_def_defect = any(
            p in combined_text
            for p in (
                "unsupported rule type",
                "invalid operator",
                "invalid jsonpath",
                "syntax error in expression",
                "unsupported assertion",
            )
        )
        if is_test_def_defect:
            return InvestigationFinding(
                finding_id=finding_id,
                run_id=run_id,
                workflow_id=workflow_id,
                test_name=case.test_name,
                method=case.method,
                url=case.url,
                execution_phase=phase,
                is_primary_failure=True,
                observed_symptom=f"Test definition syntax or assertion defect: {err_msg}",
                category=DiagnosticCategory.TEST_DEFINITION_ISSUE,
                confidence=ConfidenceLevel.VERIFIED_OBSERVATION,
                severity=FindingSeverity.MEDIUM,
                disposition=DiagnosticDisposition.TEST_DEFINITION_FIX_NEEDED,
                candidate_causes=[
                    "Test specification YAML contains malformed assertion syntax or unsupported rule type",
                    "Invalid JSONPath expression specified in test definition",
                ],
                alternative_explanations=[],
                supporting_evidence=[
                    f"Validation message: {err_msg}",
                    f"Test name: {case.test_name}",
                ],
                recommended_next_step=(
                    "Correct test assertion definition YAML to use supported ApiScope operators and valid JSONPath syntax."
                ),
                status_code=case.status_code,
                elapsed_ms=case.elapsed_ms,
                error_message=err_msg or None,
            )

        # 7. Check for Speculative / Unsupported Oracle
        # If test asserts immediate consistency or undocumented header without contract backing
        is_speculative_oracle = (
            "consistency" in case.test_name.lower() and "undocumented" in combined_text
        ) or any(
            "speculative" in str(a.get("message", "")).lower()
            for a in case.assertion_results
        )
        if is_speculative_oracle:
            return InvestigationFinding(
                finding_id=finding_id,
                run_id=run_id,
                workflow_id=workflow_id,
                test_name=case.test_name,
                method=case.method,
                url=case.url,
                execution_phase=phase,
                is_primary_failure=True,
                observed_symptom=f"Test oracle assumption unconfirmed by API contract: {err_msg}",
                category=DiagnosticCategory.TEST_DEFINITION_ISSUE,
                confidence=ConfidenceLevel.SUSPECTED_CAUSE,
                severity=FindingSeverity.LOW,
                disposition=DiagnosticDisposition.TEST_DEFINITION_FIX_NEEDED,
                candidate_causes=[
                    "Test expects undocumented immediate consistency or unsupported business invariant",
                    "Test oracle was generated heuristically without primary contract backing",
                ],
                alternative_explanations=[
                    "Application does not guarantee real-time synchronization across endpoints"
                ],
                supporting_evidence=[
                    f"Test expectation: {case.test_name}",
                    f"Assertion details: {err_msg}",
                ],
                recommended_next_step=(
                    "Review contract specification to determine if eventual consistency or "
                    "asynchronous processing is expected. Adjust test oracle accordingly."
                ),
                status_code=case.status_code,
                elapsed_ms=case.elapsed_ms,
                error_message=err_msg or None,
            )

        # 8. Server 500 Internal Server Error (Suspected Application Failure)
        if case.status_code in (500, 502, 503):
            failed_asserts = [
                a.get("message", "")
                for a in case.assertion_results
                if not a.get("passed", True)
            ]
            return InvestigationFinding(
                finding_id=finding_id,
                run_id=run_id,
                workflow_id=workflow_id,
                test_name=case.test_name,
                method=case.method,
                url=case.url,
                execution_phase=phase,
                is_primary_failure=True,
                observed_symptom=f"HTTP {case.status_code} Internal Server Error returned by {case.method} {case.url}",
                category=DiagnosticCategory.SUSPECTED_APPLICATION_FAILURE,
                confidence=ConfidenceLevel.SUSPECTED_CAUSE,
                severity=FindingSeverity.HIGH,
                disposition=DiagnosticDisposition.ACTIONABLE_DEFECT_SUSPECTED,
                candidate_causes=[
                    "Unhandled runtime exception or crash in target endpoint handler",
                    "Database constraint violation, transaction rollback, or query execution failure",
                    "Uncaught null pointer, type error, or missing internal service dependency",
                ],
                alternative_explanations=[
                    "Malformed payload triggered an unhandled 500 instead of a clean 400 Bad Request",
                    "Target environment database was left in an inconsistent state",
                ],
                supporting_evidence=[
                    f"HTTP response status code: {case.status_code}",
                    *(f"Failed assertion: {m}" for m in failed_asserts),
                    *(
                        [f"Response snippet: {case.response_body[:200]}"]
                        if case.response_body
                        else []
                    ),
                ],
                recommended_next_step=(
                    "Inspect target application server logs around the test timestamp to locate "
                    "the unhandled stack trace and failing source file/line."
                ),
                status_code=case.status_code,
                elapsed_ms=case.elapsed_ms,
                error_message=err_msg or None,
            )

        # 9. Contract or Response Mismatch (Documented response code or schema contradiction)
        failed_asserts = [
            str(a.get("message", ""))
            for a in case.assertion_results
            if not a.get("passed", True)
        ]
        assert_summary = "; ".join(failed_asserts) if failed_asserts else err_msg

        # Reconcile against canonical contract if available
        is_documented_endpoint = False
        documented_status_codes: list[int] = []
        if contract is not None:
            ops = getattr(contract, "operations", [])
            for op in ops:
                path_pattern = re.sub(r"\{[^}]+\}", r"[^/]+", op.path)
                if op.method.upper() == case.method.upper() and (
                    op.path in case.url
                    or bool(re.search(path_pattern, case.url))
                    or (op.operation_id and op.operation_id in case.test_name)
                    or f"{op.method.upper()} {op.path}" in case.test_name
                ):
                    is_documented_endpoint = True
                    resps = (
                        op.responses.values()
                        if isinstance(op.responses, dict)
                        else op.responses
                    )
                    for resp in resps:
                        code = getattr(resp, "status_code", resp)
                        with contextlib.suppress(ValueError, TypeError):
                            documented_status_codes.append(int(code))

        schema_mismatch = any(
            "schema" in str(a.get("type", "")).lower()
            or "schema" in str(a.get("rule_type", "")).lower()
            or "schema" in str(a.get("message", "")).lower()
            for a in case.assertion_results
            if not a.get("passed", True)
        )
        status_mismatch = any(
            "status" in str(a.get("type", "")).lower()
            or "status" in str(a.get("rule_type", "")).lower()
            or "status" in str(a.get("message", "")).lower()
            for a in case.assertion_results
            if not a.get("passed", True)
        )

        causes = []
        if schema_mismatch:
            causes.append(
                "Application response body violated documented JSON schema constraints"
            )
        if status_mismatch:
            causes.append(
                f"Application returned HTTP {case.status_code}, contradicting expected contract"
            )
        if not causes:
            causes.append(
                "Application response failed one or more deterministic contract assertions"
            )

        return InvestigationFinding(
            finding_id=finding_id,
            run_id=run_id,
            workflow_id=workflow_id,
            test_name=case.test_name,
            method=case.method,
            url=case.url,
            execution_phase=phase,
            is_primary_failure=True,
            observed_symptom=f"Contract or assertion mismatch: {assert_summary or 'Assertions failed'}",
            category=DiagnosticCategory.CONTRACT_OR_RESPONSE_MISMATCH,
            confidence=ConfidenceLevel.VERIFIED_OBSERVATION,
            severity=FindingSeverity.HIGH
            if is_documented_endpoint
            else FindingSeverity.MEDIUM,
            disposition=DiagnosticDisposition.CONTRACT_MISMATCH_DETECTED,
            candidate_causes=causes,
            alternative_explanations=[
                "Test assertion expected a field that was recently renamed or deprecated",
                "Contract specification is outdated compared to deployed backend version",
            ],
            supporting_evidence=[
                f"HTTP status received: {case.status_code}",
                *(f"Assertion failure: {m}" for m in failed_asserts),
                *(
                    [f"Documented contract statuses: {documented_status_codes}"]
                    if documented_status_codes
                    else []
                ),
            ],
            recommended_next_step=(
                "Compare endpoint response schema with OpenAPI specification. Update contract "
                "if schema change is intentional, or fix backend serializer if field is missing."
            ),
            status_code=case.status_code,
            elapsed_ms=case.elapsed_ms,
            error_message=err_msg or None,
        )

    def _identify_evidence_gaps(
        self,
        _run_result: RunResult,
        contract: CanonicalContract | None,
        findings: list[InvestigationFinding],
    ) -> list[str]:
        """Identify missing context or evidence that prevents definitive root-cause confirmation."""
        gaps: list[str] = []

        if contract is None:
            gaps.append(
                "No CanonicalContract or OpenAPI specification provided; contract reconciliation was heuristic."
            )

        has_500 = any(f.status_code == 500 for f in findings)
        if has_500:
            gaps.append(
                "Target application server logs and stack traces are absent; server 500 defects remain unconfirmed."
            )

        has_timeouts = any(
            f.category == DiagnosticCategory.NETWORK_OR_TRANSPORT_ISSUE
            for f in findings
        )
        if has_timeouts:
            gaps.append(
                "Network timeout occurred without server-side receipt confirmation; backend processing state is uncertain."
            )

        has_synthetic_404 = any(
            f.category == DiagnosticCategory.FIXTURE_OR_DEPENDENCY_ISSUE
            and f.status_code == 404
            for f in findings
        )
        if has_synthetic_404:
            gaps.append(
                "Endpoint returned 404 for fixture entity; database snapshot prior to test run was not verified."
            )

        return gaps

    def _build_executive_summary(
        self,
        total_cases: int,
        total_failures: int,
        primary_count: int,
        downstream_count: int,
        has_app_defects: bool,
        has_test_or_fixture: bool,
        has_env_or_policy: bool,
    ) -> str:
        """Compose clear, concise executive diagnostic summary."""
        if total_failures == 0:
            return f"All {total_cases} test cases executed cleanly. No failures or policy blocks detected."

        parts = [
            f"Evaluated {total_cases} cases and identified {total_failures} failure(s) "
            f"comprising {primary_count} primary failure(s) and {downstream_count} downstream consequence(s)."
        ]

        if has_app_defects:
            parts.append(
                "Potential application defects or contract mismatches were detected."
            )
        if has_test_or_fixture:
            parts.append(
                "Test definition or fixture resolution issues require adjustment."
            )
        if has_env_or_policy:
            parts.append(
                "Execution policy fences or transport network issues prevented step completion."
            )

        return " ".join(parts)

    def _generate_ai_hypotheses(
        self,
        findings: list[InvestigationFinding],
        _run_result: RunResult,
        _contract: CanonicalContract | None,
        llm_config: LLMConfig | None,
        llm_provider: LLMProviderProtocol | None,
    ) -> dict[str, Any] | None:
        """Generate budget-aware Gemini hypotheses for primary failures (opt-in only)."""
        # Pick primary failures (max 5)
        primary_findings = [f for f in findings if f.is_primary_failure][:5]
        if not primary_findings:
            return None

        # Build provider
        provider = llm_provider
        if provider is None:
            cfg = llm_config or LLMConfig(provider="gemini")
            if not cfg.api_key:
                logger.warning(
                    "AI investigation requested but no API key configured; skipping AI."
                )
                return None
            try:
                provider = LiveHttpLLMProvider(cfg)
            except Exception as err:
                logger.warning("Failed to initialize AI provider: %s", err)
                return None

        # Prepare compact, redacted prompt
        failures_summary = []
        for f in primary_findings:
            failures_summary.append(
                f"- [{f.finding_id}] {f.method} {redact_sensitive_text(f.url)} "
                f"(Status: {f.status_code}, Phase: {f.execution_phase.value}): "
                f"{redact_sensitive_text(f.observed_symptom)}"
            )

        prompt = (
            "You are an API diagnostic assistant. Based on the recorded test failures below, "
            "provide 2-3 concise hypotheses explaining possible root causes in the backend application, "
            "and recommend the most important debugging check. "
            "IMPORTANT: Label all outputs as hypotheses. Never invent stack traces or assume unstated code.\n\n"
            "Primary Failures:\n" + "\n".join(failures_summary) + "\n\n"
            "Format your response as a JSON array of hypothesis strings corresponding to the findings."
        )

        try:
            raw_response = provider.generate(prompt, max_tokens=600)
            clean_text = raw_response.strip()
            if clean_text.startswith("```json"):
                clean_text = clean_text[7:]
            if clean_text.startswith("```"):
                clean_text = clean_text[3:]
            if clean_text.endswith("```"):
                clean_text = clean_text[:-3]
            clean_text = clean_text.strip()

            parsed = json.loads(clean_text)
            if isinstance(parsed, list):
                return {
                    "provider": "gemini",
                    "hypotheses": [f"[HYPOTHESIS] {str(item)}" for item in parsed],
                }
            if isinstance(parsed, dict) and "hypotheses" in parsed:
                return {
                    "provider": "gemini",
                    "hypotheses": [
                        f"[HYPOTHESIS] {str(item)}" for item in parsed["hypotheses"]
                    ],
                }
            return {
                "provider": "gemini",
                "hypotheses": [f"[HYPOTHESIS] {clean_text}"],
            }
        except Exception as err:
            logger.warning("AI hypothesis generation failed gracefully: %s", err)
            return None
