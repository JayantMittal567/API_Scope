"""Advanced Endpoint Inventory, Source/Spec Reconciliation, and Coverage Intelligence (Task 24).

Implements reliable canonical endpoint identification, strict operational coverage
metrics (with distinct operation denominators, separating planned from executed),
evidence-based endpoint classification (avoiding false-positive zombie claims for
untested endpoints), and multi-source reconciliation across OpenAPI specs, source
repositories, test plans, test collections, and runtime execution runs.
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from apiscope.analysis.path_normalizer import (
    extract_url_path,
    match_path_to_spec,
    normalize_unmatched_path,
)
from apiscope.analysis.zombie_service import (
    AccessLogEntry,
    load_access_log,
)
from apiscope.contract.models import CanonicalContract
from apiscope.core.logging_config import redact_sensitive_text
from apiscope.core.results import CaseStatus, RunResult
from apiscope.definitions.collection import TestCollection, validate_safe_path
from apiscope.generators.openapi_importer import (
    OpenAPISpec,
    load_openapi_spec,
)
from apiscope.intelligence.source_scanner import (
    DiscoveredSourceFindings,
    SourceCodeScanner,
)
from apiscope.planning.models import TestPlan


class EndpointClassification(StrEnum):
    """Evidence-based classification states for API endpoints."""

    __test__ = False

    DOCUMENTED_AND_OBSERVED = "DOCUMENTED_AND_OBSERVED"
    DOCUMENTED_NOT_OBSERVED = "DOCUMENTED_NOT_OBSERVED"
    SOURCE_ONLY_CANDIDATE = "SOURCE_ONLY_CANDIDATE"
    SPEC_ONLY_CANDIDATE = "SPEC_ONLY_CANDIDATE"
    RUNTIME_MISMATCH_CANDIDATE = "RUNTIME_MISMATCH_CANDIDATE"
    DEPRECATED_OR_OBSOLETE_CANDIDATE = "DEPRECATED_OR_OBSOLETE_CANDIDATE"
    ZOMBIE_CANDIDATE = "ZOMBIE_CANDIDATE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

    # Backward compatibility aliases with earlier M9/M10 classes
    UNUSED = "DOCUMENTED_NOT_OBSERVED"
    UNDOCUMENTED = "RUNTIME_MISMATCH_CANDIDATE"
    UNTESTED = "DOCUMENTED_NOT_OBSERVED"
    DEPRECATED_BUT_CALLED = "DEPRECATED_OR_OBSOLETE_CANDIDATE"


class InventoryConfidence(StrEnum):
    """Epistemic certainty status of endpoint inventory conclusions."""

    VERIFIED_OBSERVATION = "VERIFIED_OBSERVATION"
    SUSPECTED_CAUSE = "SUSPECTED_CAUSE"
    INFERRED = "INFERRED"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class CanonicalEndpointIdentity:
    """Unique canonical identity for an HTTP endpoint operation."""

    __test__ = False

    method: str
    route_template: str
    service_context: str = "primary"
    module: str = "default"
    origin: str = ""

    @property
    def canonical_key(self) -> str:
        """Normalized string key combining service context, HTTP method, and path template."""
        return f"{self.service_context}:{self.method.upper()} {self.route_template}"

    def to_dict(self) -> dict[str, Any]:
        """Convert endpoint identity to serializable dictionary."""
        return {
            "method": self.method.upper(),
            "route_template": self.route_template,
            "service_context": self.service_context,
            "module": self.module,
            "origin": self.origin,
            "canonical_key": self.canonical_key,
        }


@dataclass
class OperationCoverageMetrics:
    """Accurate API test coverage metrics using distinct canonical operations as denominators.

    Denominators are always the total number of documented operations.
    Repeated test executions of the same endpoint do not inflate coverage percentages.
    Planned and compiled test cases are never counted as executed.
    """

    __test__ = False

    total_documented_operations: int = 0
    planned_operations_count: int = 0
    planned_coverage_pct: float = 0.0
    generated_operations_count: int = 0
    generated_coverage_pct: float = 0.0
    attempted_operations_count: int = 0
    attempted_coverage_pct: float = 0.0
    completed_operations_count: int = 0
    completed_coverage_pct: float = 0.0
    passed_operations_count: int = 0
    passed_coverage_pct: float = 0.0
    failed_operations_count: int = 0
    failed_coverage_pct: float = 0.0
    blocked_or_skipped_operations_count: int = 0
    blocked_or_skipped_coverage_pct: float = 0.0
    untested_operations_count: int = 0
    untested_coverage_pct: float = 0.0
    readonly_total_operations: int = 0
    readonly_attempted_operations: int = 0
    readonly_coverage_pct: float = 0.0
    mutation_total_operations: int = 0
    mutation_attempted_operations: int = 0
    mutation_coverage_pct: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert coverage metrics to serializable dictionary."""
        return {
            "total_documented_operations": self.total_documented_operations,
            "planned_operations_count": self.planned_operations_count,
            "planned_coverage_pct": round(self.planned_coverage_pct, 2),
            "generated_operations_count": self.generated_operations_count,
            "generated_coverage_pct": round(self.generated_coverage_pct, 2),
            "attempted_operations_count": self.attempted_operations_count,
            "attempted_coverage_pct": round(self.attempted_coverage_pct, 2),
            "completed_operations_count": self.completed_operations_count,
            "completed_coverage_pct": round(self.completed_coverage_pct, 2),
            "passed_operations_count": self.passed_operations_count,
            "passed_coverage_pct": round(self.passed_coverage_pct, 2),
            "failed_operations_count": self.failed_operations_count,
            "failed_coverage_pct": round(self.failed_coverage_pct, 2),
            "blocked_or_skipped_operations_count": (
                self.blocked_or_skipped_operations_count
            ),
            "blocked_or_skipped_coverage_pct": round(
                self.blocked_or_skipped_coverage_pct, 2
            ),
            "untested_operations_count": self.untested_operations_count,
            "untested_coverage_pct": round(self.untested_coverage_pct, 2),
            "readonly_total_operations": self.readonly_total_operations,
            "readonly_attempted_operations": self.readonly_attempted_operations,
            "readonly_coverage_pct": round(self.readonly_coverage_pct, 2),
            "mutation_total_operations": self.mutation_total_operations,
            "mutation_attempted_operations": self.mutation_attempted_operations,
            "mutation_coverage_pct": round(self.mutation_coverage_pct, 2),
        }


@dataclass
class ModuleCoverageMetrics:
    """Coverage metrics aggregated per functional module."""

    __test__ = False

    module: str
    total_documented: int = 0
    attempted: int = 0
    passed: int = 0
    untested: int = 0
    coverage_pct: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert module coverage metrics to serializable dictionary."""
        return {
            "module": self.module,
            "total_documented": self.total_documented,
            "attempted": self.attempted,
            "passed": self.passed,
            "untested": self.untested,
            "coverage_pct": round(self.coverage_pct, 2),
        }


@dataclass
class EvidenceBasedEndpointFinding:
    """Detailed evidence-backed finding explaining endpoint status and anomalies."""

    __test__ = False

    finding_id: str
    method: str
    canonical_endpoint: str
    module: str
    service_context: str
    classification: EndpointClassification
    confidence: InventoryConfidence
    supporting_evidence: list[str] = field(default_factory=list)
    relevant_run_ids: list[str] = field(default_factory=list)
    alternative_explanations: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    recommended_action: str = ""
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert finding to serializable dictionary."""
        return {
            "finding_id": self.finding_id,
            "method": self.method,
            "canonical_endpoint": self.canonical_endpoint,
            "module": self.module,
            "service_context": self.service_context,
            "classification": self.classification.value,
            "confidence": self.confidence.value,
            "supporting_evidence": list(self.supporting_evidence),
            "relevant_run_ids": list(self.relevant_run_ids),
            "alternative_explanations": list(self.alternative_explanations),
            "missing_evidence": list(self.missing_evidence),
            "recommended_action": self.recommended_action,
            "details": self.details,
        }


@dataclass
class EndpointInventoryRecord:
    """Unified inventory entry tracking an endpoint across specification, source, and runtime."""

    __test__ = False

    identity: CanonicalEndpointIdentity
    is_in_spec: bool = False
    is_in_source: bool = False
    is_deprecated: bool = False
    source_file: str | None = None
    source_line: int | None = None
    runtime_call_count: int = 0
    runtime_status_codes: list[int] = field(default_factory=list)
    test_executions_count: int = 0
    test_passed_count: int = 0
    test_failed_count: int = 0
    test_skipped_count: int = 0
    classification: EndpointClassification = (
        EndpointClassification.INSUFFICIENT_EVIDENCE
    )
    primary_finding_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert inventory record to serializable dictionary."""
        return {
            "identity": self.identity.to_dict(),
            "is_in_spec": self.is_in_spec,
            "is_in_source": self.is_in_source,
            "is_deprecated": self.is_deprecated,
            "source_file": self.source_file,
            "source_line": self.source_line,
            "runtime_call_count": self.runtime_call_count,
            "runtime_status_codes": list(self.runtime_status_codes),
            "test_executions_count": self.test_executions_count,
            "test_passed_count": self.test_passed_count,
            "test_failed_count": self.test_failed_count,
            "test_skipped_count": self.test_skipped_count,
            "classification": self.classification.value,
            "primary_finding_id": self.primary_finding_id,
        }


@dataclass
class EndpointInventoryReport:
    """Complete diagnostic report with endpoint inventory, coverage intelligence, and findings."""

    __test__ = False

    schema_version: str = "1.0.0"
    evaluated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    target_context: str = "primary"
    observation_window: str | None = None
    evidence_sources_evaluated: list[str] = field(default_factory=list)
    evidence_limitations: list[str] = field(default_factory=list)
    total_canonical_operations: int = 0
    coverage_summary: OperationCoverageMetrics = field(
        default_factory=OperationCoverageMetrics
    )
    module_breakdown: dict[str, ModuleCoverageMetrics] = field(default_factory=dict)
    classification_counts: dict[str, int] = field(default_factory=dict)
    inventory: list[EndpointInventoryRecord] = field(default_factory=list)
    findings: list[EvidenceBasedEndpointFinding] = field(default_factory=list)

    def filter_by_module(self, module: str) -> list[EvidenceBasedEndpointFinding]:
        """Filter report findings by module name (case-insensitive)."""
        target = module.strip().lower()
        return [f for f in self.findings if f.module.lower() == target]

    def to_dict(self) -> dict[str, Any]:
        """Convert report to serializable dictionary."""
        return {
            "schema_version": self.schema_version,
            "evaluated_at": self.evaluated_at,
            "target_context": self.target_context,
            "observation_window": self.observation_window,
            "evidence_sources_evaluated": list(self.evidence_sources_evaluated),
            "evidence_limitations": list(self.evidence_limitations),
            "total_canonical_operations": self.total_canonical_operations,
            "coverage_summary": self.coverage_summary.to_dict(),
            "module_breakdown": {
                k: v.to_dict() for k, v in self.module_breakdown.items()
            },
            "classification_counts": dict(self.classification_counts),
            "inventory": [rec.to_dict() for rec in self.inventory],
            "findings": [f.to_dict() for f in self.findings],
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize report to formatted JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def to_yaml(self) -> str:
        """Serialize report to formatted YAML string."""
        return yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True)


def _infer_module_from_template(path: str) -> str:
    """Infer functional module name from URL path segments."""
    clean = path.strip("/")
    segments = clean.split("/") if clean else []
    for seg in segments:
        seg_lower = seg.lower()
        if seg_lower in ("api", "v1", "v2", "v3", "v4") or (
            seg_lower.startswith("v") and seg_lower[1:].isdigit()
        ):
            continue
        cleaned_seg = seg_lower.replace("{", "").replace("}", "").replace("-", "_")
        return cleaned_seg or "default"
    return "default"


def _extract_origin_from_url(url: str) -> str:
    """Safely extract origin (scheme + netloc) from a URL without leaking credentials."""
    try:
        parts = urllib.parse.urlsplit(str(url).strip())
        if parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
        return ""
    except Exception:
        return ""


def _is_auxiliary_service(file_path: str, primary_language: str = "") -> bool:
    """Determine if a source file belongs to an auxiliary microservice rather than target API."""
    path_low = file_path.lower().replace("\\", "/")
    auxiliary_markers = (
        "/analytics/",
        "/web/",
        "/client/",
        "/frontend/",
        "/admin-portal/",
        "/mock/",
        "/harness/",
    )
    if any(marker in path_low for marker in auxiliary_markers):
        return True

    # Language boundary check
    if "java" in primary_language.lower() and path_low.endswith(".py"):
        return True
    return bool("python" in primary_language.lower() and path_low.endswith(".java"))


class EndpointInventoryService:
    """Comprehensive service for endpoint inventory, source reconciliation, and coverage."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        """Initialize service with optional custom clock."""
        self._clock = clock or (lambda: datetime.now(UTC))

    def analyze(
        self,
        spec: OpenAPISpec | CanonicalContract | Path | str | None = None,
        repo_path: Path | str | None = None,
        source_findings: DiscoveredSourceFindings | None = None,
        run_result: RunResult | None = None,
        runs: list[RunResult] | None = None,
        test_plan: TestPlan | dict[str, Any] | None = None,
        test_collection: TestCollection | None = None,
        access_entries: Sequence[AccessLogEntry] | None = None,
        access_log_path: Path | str | None = None,
        target_service: str = "primary",
        module_filter: str | None = None,
    ) -> EndpointInventoryReport:
        """Execute complete offline endpoint inventory, reconciliation, and coverage analysis."""
        evaluated_sources: list[str] = []
        limitations: list[str] = []

        # 1. Resolve OpenAPI specification
        openapi_spec: OpenAPISpec | None = None
        canonical_contract: CanonicalContract | None = None

        if spec is not None:
            if isinstance(spec, OpenAPISpec):
                openapi_spec = spec
                evaluated_sources.append("OPENAPI_SPECIFICATION")
            elif isinstance(spec, CanonicalContract):
                canonical_contract = spec
                evaluated_sources.append("CANONICAL_CONTRACT")
            else:
                p_spec = Path(spec)
                eff_base = p_spec.parent if p_spec.is_absolute() else Path.cwd()
                safe_spec = validate_safe_path(base_dir=eff_base, ref_path=p_spec)
                openapi_spec = load_openapi_spec(safe_spec, base_dir=eff_base)
                evaluated_sources.append("OPENAPI_SPECIFICATION")
        else:
            limitations.append(
                "No OpenAPI specification provided; specification-level inventory cannot be determined"
            )

        # 2. Resolve source repository findings
        active_source_findings: DiscoveredSourceFindings | None = source_findings
        if active_source_findings is None and repo_path is not None:
            p_repo = Path(repo_path)
            eff_base = p_repo if p_repo.is_absolute() else Path.cwd()
            safe_repo = validate_safe_path(base_dir=eff_base, ref_path=p_repo)
            if safe_repo.is_dir():
                scanner = SourceCodeScanner(safe_repo)
                active_source_findings = scanner.scan()
                evaluated_sources.append("SOURCE_CODE_REPOSITORY")
            else:
                limitations.append(
                    f"Provided repository directory '{repo_path}' is not accessible"
                )

        if active_source_findings is None:
            limitations.append(
                "No source code repository provided; source-to-spec reconciliation was not performed"
            )
        elif "SOURCE_CODE_REPOSITORY" not in evaluated_sources:
            evaluated_sources.append("SOURCE_CODE_FINDINGS")

        # 3. Resolve execution runs
        all_runs: list[RunResult] = []
        if runs is not None:
            all_runs.extend(runs)
        if run_result is not None:
            all_runs.append(run_result)

        if all_runs:
            evaluated_sources.append("TEST_EXECUTION_RUNS")
        else:
            limitations.append(
                "No test execution runs provided; attempted execution coverage is 0.0%"
            )

        # 4. Resolve access log entries
        all_access_entries: list[AccessLogEntry] = []
        if access_entries is not None:
            all_access_entries.extend(access_entries)
        if access_log_path is not None:
            p_log = Path(access_log_path)
            safe_log = validate_safe_path(base_dir=Path.cwd(), ref_path=p_log)
            all_access_entries.extend(load_access_log(safe_log))
            evaluated_sources.append("ACCESS_LOGS")
        elif access_entries:
            evaluated_sources.append("ACCESS_LOG_ENTRIES")

        # 5. Resolve test plans and collections
        if test_plan is not None:
            evaluated_sources.append("TEST_PLAN_SCENARIOS")
        if test_collection is not None:
            evaluated_sources.append("TEST_COLLECTION_TESTS")

        # Build documented operations map
        # Key: (service_context, method, path_template)
        inventory_records: dict[str, EndpointInventoryRecord] = {}
        spec_paths: set[str] = set()

        if openapi_spec is not None:
            for ep in openapi_spec.endpoints:
                mth = ep.method.upper()
                norm_p = ep.path
                spec_paths.add(norm_p)
                mod = ep.module or _infer_module_from_template(norm_p)
                ident = CanonicalEndpointIdentity(
                    method=mth,
                    route_template=norm_p,
                    service_context=target_service,
                    module=mod,
                )
                inventory_records[ident.canonical_key] = EndpointInventoryRecord(
                    identity=ident,
                    is_in_spec=True,
                    is_in_source=False,
                    is_deprecated=ep.deprecated,
                )
        elif canonical_contract is not None:
            for op in canonical_contract.operations:
                mth = op.method.upper()
                norm_p = op.path
                spec_paths.add(norm_p)
                mod = op.module or _infer_module_from_template(norm_p)
                ident = CanonicalEndpointIdentity(
                    method=mth,
                    route_template=norm_p,
                    service_context=target_service,
                    module=mod,
                )
                inventory_records[ident.canonical_key] = EndpointInventoryRecord(
                    identity=ident,
                    is_in_spec=True,
                    is_in_source=False,
                    is_deprecated=op.deprecated,
                )

        sorted_spec_paths = sorted(spec_paths)

        # Reconcile Source Code findings
        isolated_auxiliary_records: list[EndpointInventoryRecord] = []
        if active_source_findings is not None:
            src_lang = active_source_findings.source_language
            for src_ep in active_source_findings.endpoints:
                mth = src_ep.method.upper()
                raw_path = src_ep.path
                clean_path = extract_url_path(raw_path)

                if _is_auxiliary_service(src_ep.file_path, src_lang):
                    # Auxiliary service: isolate and do not flag against target spec
                    p_parts = Path(src_ep.file_path).parts
                    aux_service = p_parts[0] if p_parts else "auxiliary"
                    mod = _infer_module_from_template(clean_path)
                    ident = CanonicalEndpointIdentity(
                        method=mth,
                        route_template=clean_path,
                        service_context=aux_service,
                        module=mod,
                    )
                    isolated_auxiliary_records.append(
                        EndpointInventoryRecord(
                            identity=ident,
                            is_in_spec=False,
                            is_in_source=True,
                            source_file=src_ep.file_path,
                            source_line=src_ep.line_number,
                            classification=EndpointClassification.SOURCE_ONLY_CANDIDATE,
                        )
                    )
                    continue

                # Target service controller: attempt match to spec
                matched_template = match_path_to_spec(clean_path, sorted_spec_paths)
                target_key = (
                    f"{target_service}:{mth} {matched_template}"
                    if matched_template is not None
                    else None
                )
                if target_key and target_key in inventory_records:
                    rec = inventory_records[target_key]
                    rec.is_in_source = True
                    rec.source_file = src_ep.file_path
                    rec.source_line = src_ep.line_number
                else:
                    # Target service source route absent from spec
                    norm_unmatched = normalize_unmatched_path(clean_path)
                    mod = _infer_module_from_template(norm_unmatched)
                    ident = CanonicalEndpointIdentity(
                        method=mth,
                        route_template=norm_unmatched,
                        service_context=target_service,
                        module=mod,
                    )
                    if ident.canonical_key not in inventory_records:
                        inventory_records[ident.canonical_key] = (
                            EndpointInventoryRecord(
                                identity=ident,
                                is_in_spec=False,
                                is_in_source=True,
                                source_file=src_ep.file_path,
                                source_line=src_ep.line_number,
                                classification=EndpointClassification.SOURCE_ONLY_CANDIDATE,
                            )
                        )
                    else:
                        inventory_records[ident.canonical_key].is_in_source = True

        # Process Planned Scenarios
        planned_operations: set[str] = set()
        if test_plan is not None:
            scenarios = []
            if isinstance(test_plan, TestPlan):
                scenarios = test_plan.scenarios
            elif isinstance(test_plan, dict):
                scenarios = test_plan.get("scenarios", [])

            for scn in scenarios:
                op_str = getattr(scn, "operation", None) or (
                    scn.get("operation") if isinstance(scn, dict) else None
                )
                if op_str and " " in op_str:
                    mth_part, path_part = op_str.split(" ", 1)
                    matched = match_path_to_spec(path_part, sorted_spec_paths)
                    if matched is not None:
                        planned_operations.add(
                            f"{target_service}:{mth_part.upper()} {matched}"
                        )
                else:
                    # Inspect target_endpoints if present
                    target_eps = getattr(scn, "target_endpoints", None) or (
                        scn.get("target_endpoints", []) if isinstance(scn, dict) else []
                    )
                    for t_ep in target_eps:
                        if t_ep and " " in t_ep:
                            mth_part, path_part = t_ep.split(" ", 1)
                            matched = match_path_to_spec(path_part, sorted_spec_paths)
                            if matched is not None:
                                planned_operations.add(
                                    f"{target_service}:{mth_part.upper()} {matched}"
                                )

                    # Inspect steps if present
                    steps = getattr(scn, "steps", None) or (
                        scn.get("steps", []) if isinstance(scn, dict) else []
                    )
                    for st in steps:
                        s_mth = getattr(st, "method", None) or (
                            st.get("method") if isinstance(st, dict) else None
                        )
                        s_path = getattr(st, "path", None) or (
                            st.get("path") if isinstance(st, dict) else None
                        )
                        if s_mth and s_path:
                            matched = match_path_to_spec(s_path, sorted_spec_paths)
                            if matched is not None:
                                planned_operations.add(
                                    f"{target_service}:{s_mth.upper()} {matched}"
                                )

        # Process Generated/Compiled Test Collections
        generated_operations: set[str] = set()
        if test_collection is not None:
            for t_def in test_collection.tests:
                t_mth = (
                    t_def.request.method.value
                    if hasattr(t_def.request.method, "value")
                    else str(t_def.request.method)
                ).upper()
                c_p = extract_url_path(t_def.request.url)
                matched = match_path_to_spec(c_p, sorted_spec_paths)
                if matched is not None:
                    generated_operations.add(f"{target_service}:{t_mth} {matched}")

        # Process Test Execution Runs
        attempted_operations: set[str] = set()
        completed_operations: set[str] = set()
        passed_operations: set[str] = set()
        failed_operations: set[str] = set()
        blocked_operations: set[str] = set()
        all_seen_run_ids: set[str] = set()

        for r_res in all_runs:
            if r_res.run_id:
                all_seen_run_ids.add(r_res.run_id)

            for case in r_res.results:
                clean_url = redact_sensitive_text(str(case.url))
                c_path = extract_url_path(clean_url)
                c_mth = case.method.upper()
                c_origin = _extract_origin_from_url(case.url)

                matched = match_path_to_spec(c_path, sorted_spec_paths)
                c_key = (
                    f"{target_service}:{c_mth} {matched}"
                    if matched is not None
                    else None
                )
                if c_key and c_key in inventory_records:
                    rec = inventory_records[c_key]
                    op_key = c_key
                else:
                    norm_unmatched = (
                        matched
                        if matched is not None
                        else normalize_unmatched_path(c_path)
                    )
                    u_key = f"{target_service}:{c_mth} {norm_unmatched}"
                    op_key = u_key
                    if u_key not in inventory_records:
                        mod = case.module or _infer_module_from_template(norm_unmatched)
                        rec = EndpointInventoryRecord(
                            identity=CanonicalEndpointIdentity(
                                method=c_mth,
                                route_template=norm_unmatched,
                                service_context=target_service,
                                module=mod,
                                origin=c_origin,
                            ),
                            is_in_spec=False,
                            classification=EndpointClassification.RUNTIME_MISMATCH_CANDIDATE,
                        )
                        inventory_records[u_key] = rec
                    else:
                        rec = inventory_records[u_key]

                if case.status == CaseStatus.SKIPPED:
                    rec.test_skipped_count += 1
                    blocked_operations.add(op_key)
                else:
                    rec.runtime_call_count += 1
                    rec.test_executions_count += 1
                    if case.status_code is not None:
                        rec.runtime_status_codes.append(case.status_code)

                    attempted_operations.add(op_key)
                    if case.status_code is not None and case.status_code > 0:
                        completed_operations.add(op_key)

                    if case.status == CaseStatus.PASSED:
                        rec.test_passed_count += 1
                        passed_operations.add(op_key)
                    elif case.status in (CaseStatus.FAILED, CaseStatus.ERROR):
                        rec.test_failed_count += 1
                        failed_operations.add(op_key)

        # Process Access Log Traffic
        for entry in all_access_entries:
            e_mth = entry.method.upper()
            e_path = extract_url_path(entry.raw_path)
            matched = match_path_to_spec(e_path, sorted_spec_paths)
            e_key = (
                f"{target_service}:{e_mth} {matched}" if matched is not None else None
            )

            if e_key and e_key in inventory_records:
                rec = inventory_records[e_key]
                rec.runtime_call_count += 1
                rec.runtime_status_codes.append(entry.status_code)
            else:
                norm_unmatched = (
                    matched if matched is not None else normalize_unmatched_path(e_path)
                )
                u_key = f"{target_service}:{e_mth} {norm_unmatched}"
                if u_key not in inventory_records:
                    mod = _infer_module_from_template(norm_unmatched)
                    rec = EndpointInventoryRecord(
                        identity=CanonicalEndpointIdentity(
                            method=e_mth,
                            route_template=norm_unmatched,
                            service_context=target_service,
                            module=mod,
                        ),
                        is_in_spec=False,
                        classification=EndpointClassification.RUNTIME_MISMATCH_CANDIDATE,
                    )
                    inventory_records[u_key] = rec
                else:
                    rec = inventory_records[u_key]
                rec.runtime_call_count += 1
                rec.runtime_status_codes.append(entry.status_code)

        # Apply Evidence-Based Classifications and Generate Findings
        findings: list[EvidenceBasedEndpointFinding] = []
        finding_seq = 1

        for _key, rec in inventory_records.items():
            ident = rec.identity
            f_id = f"EP-{finding_seq:04d}"
            finding_seq += 1

            # 1. Source-Only Candidate
            if rec.is_in_source and not rec.is_in_spec:
                rec.classification = EndpointClassification.SOURCE_ONLY_CANDIDATE
                finding = EvidenceBasedEndpointFinding(
                    finding_id=f_id,
                    method=ident.method,
                    canonical_endpoint=ident.route_template,
                    module=ident.module,
                    service_context=ident.service_context,
                    classification=EndpointClassification.SOURCE_ONLY_CANDIDATE,
                    confidence=InventoryConfidence.SUSPECTED_CAUSE,
                    supporting_evidence=[
                        f"Found controller mapping in '{rec.source_file}:{rec.source_line}'",
                        "Absent from OpenAPI specification",
                    ],
                    relevant_run_ids=list(all_seen_run_ids),
                    alternative_explanations=[
                        "Endpoint may be internal, dev-only, or intentionally unexposed",
                        "May be a newly developed route pending OpenAPI spec documentation",
                    ],
                    missing_evidence=[
                        "No public contract documentation found in OpenAPI specification"
                    ],
                    recommended_action=(
                        f"Document '{ident.method} {ident.route_template}' in OpenAPI spec "
                        "if client-facing, or annotate as private internal"
                    ),
                    details=(
                        f"Endpoint '{ident.method} {ident.route_template}' was discovered "
                        f"in source code ({rec.source_file}) but is absent from OpenAPI specification"
                    ),
                )
                rec.primary_finding_id = f_id
                findings.append(finding)
                continue

            # 2. Runtime Mismatch Candidate (Undocumented in spec)
            if not rec.is_in_spec and (
                rec.runtime_call_count > 0 or rec.test_executions_count > 0
            ):
                rec.classification = EndpointClassification.RUNTIME_MISMATCH_CANDIDATE
                finding = EvidenceBasedEndpointFinding(
                    finding_id=f_id,
                    method=ident.method,
                    canonical_endpoint=ident.route_template,
                    module=ident.module,
                    service_context=ident.service_context,
                    classification=EndpointClassification.RUNTIME_MISMATCH_CANDIDATE,
                    confidence=InventoryConfidence.VERIFIED_OBSERVATION,
                    supporting_evidence=[
                        f"Observed {rec.runtime_call_count} call(s) with status codes {rec.runtime_status_codes[:5]}",
                        "Path template absent from OpenAPI specification",
                    ],
                    relevant_run_ids=list(all_seen_run_ids),
                    alternative_explanations=[
                        "Legacy endpoint still receiving client traffic",
                        "Internal health, debugging, or metrics probe route",
                    ],
                    missing_evidence=[
                        "OpenAPI documentation for this active runtime route"
                    ],
                    recommended_action=(
                        f"Update OpenAPI specification to document active route '{ident.method} {ident.route_template}'"
                    ),
                    details=(
                        f"Endpoint '{ident.method} {ident.route_template}' received active traffic "
                        f"({rec.runtime_call_count} call(s)) but is absent from OpenAPI specification"
                    ),
                )
                rec.primary_finding_id = f_id
                findings.append(finding)
                continue

            # 3. Documented in Spec
            if rec.is_in_spec:
                # Check for Spec-Only Candidate (when source was scanned)
                if (
                    active_source_findings is not None
                    and not rec.is_in_source
                    and not rec.is_deprecated
                ):
                    rec.classification = EndpointClassification.SPEC_ONLY_CANDIDATE
                    finding = EvidenceBasedEndpointFinding(
                        finding_id=f_id,
                        method=ident.method,
                        canonical_endpoint=ident.route_template,
                        module=ident.module,
                        service_context=ident.service_context,
                        classification=EndpointClassification.SPEC_ONLY_CANDIDATE,
                        confidence=InventoryConfidence.SUSPECTED_CAUSE,
                        supporting_evidence=[
                            "Documented in OpenAPI specification",
                            "Absent from scanned target service source controller mappings",
                        ],
                        relevant_run_ids=list(all_seen_run_ids),
                        alternative_explanations=[
                            "Route may be dynamically mounted, proxied, or defined in an unindexed library",
                            "Specification may describe planned but unreleased capability",
                        ],
                        missing_evidence=[
                            "Static AST controller mapping in target repository"
                        ],
                        recommended_action=(
                            f"Verify implementation for '{ident.method} {ident.route_template}' or retire from specification"
                        ),
                        details=(
                            f"Endpoint '{ident.method} {ident.route_template}' is documented in OpenAPI spec "
                            "but was not located in inspected target service source controllers"
                        ),
                    )
                    rec.primary_finding_id = f_id
                    findings.append(finding)
                    continue

                # Check Deprecation
                if rec.is_deprecated:
                    # Check multi-evidence Zombie Candidate rule
                    # Condition: marked deprecated + consistent 404/410 across calls + (not in source or removed)
                    is_repeated_404_or_410 = len(rec.runtime_status_codes) >= 2 and all(
                        code in (404, 410) for code in rec.runtime_status_codes
                    )
                    if is_repeated_404_or_410 and (
                        not rec.is_in_source
                        if active_source_findings is not None
                        else True
                    ):
                        rec.classification = EndpointClassification.ZOMBIE_CANDIDATE
                        finding = EvidenceBasedEndpointFinding(
                            finding_id=f_id,
                            method=ident.method,
                            canonical_endpoint=ident.route_template,
                            module=ident.module,
                            service_context=ident.service_context,
                            classification=EndpointClassification.ZOMBIE_CANDIDATE,
                            confidence=InventoryConfidence.VERIFIED_OBSERVATION,
                            supporting_evidence=[
                                "Marked deprecated in OpenAPI specification",
                                f"Consistently returned HTTP {rec.runtime_status_codes[0]} across {len(rec.runtime_status_codes)} calls",
                                (
                                    "Confirmed absent from source controllers"
                                    if active_source_findings is not None
                                    else "No active route implementation observed"
                                ),
                            ],
                            relevant_run_ids=list(all_seen_run_ids),
                            alternative_explanations=[],
                            missing_evidence=[],
                            recommended_action=(
                                f"Decommission and remove zombie route '{ident.method} {ident.route_template}' "
                                "from specification and client callers"
                            ),
                            details=(
                                f"Zombie candidate: '{ident.method} {ident.route_template}' is deprecated, "
                                f"unimplemented, and returned HTTP {rec.runtime_status_codes[0]}"
                            ),
                        )
                        rec.primary_finding_id = f_id
                        findings.append(finding)
                        continue

                    # Deprecated candidate (still called or unobserved)
                    rec.classification = (
                        EndpointClassification.DEPRECATED_OR_OBSOLETE_CANDIDATE
                    )
                    evidence_list = ["Marked deprecated in OpenAPI specification"]
                    if rec.runtime_call_count > 0:
                        evidence_list.append(
                            f"Received {rec.runtime_call_count} call(s) in observed traffic"
                        )
                    finding = EvidenceBasedEndpointFinding(
                        finding_id=f_id,
                        method=ident.method,
                        canonical_endpoint=ident.route_template,
                        module=ident.module,
                        service_context=ident.service_context,
                        classification=EndpointClassification.DEPRECATED_OR_OBSOLETE_CANDIDATE,
                        confidence=InventoryConfidence.VERIFIED_OBSERVATION,
                        supporting_evidence=evidence_list,
                        relevant_run_ids=list(all_seen_run_ids),
                        alternative_explanations=[
                            "Deprecated endpoint remains available for backwards compatibility during migration"
                        ],
                        missing_evidence=[],
                        recommended_action=(
                            f"Plan retirement migration for deprecated route '{ident.method} {ident.route_template}'"
                        ),
                        details=(
                            f"Endpoint '{ident.method} {ident.route_template}' is marked deprecated in OpenAPI spec"
                        ),
                    )
                    rec.primary_finding_id = f_id
                    findings.append(finding)
                    continue

                # Standard documented endpoint: check observed vs unobserved
                if rec.runtime_call_count > 0 or rec.test_executions_count > 0:
                    rec.classification = EndpointClassification.DOCUMENTED_AND_OBSERVED
                    ev_notes = [
                        f"Exercised by {rec.test_executions_count} test execution(s) "
                        f"({rec.test_passed_count} passed, {rec.test_failed_count} failed)"
                    ]
                    # Check for 401/403 or dynamic resource 404
                    alt_exp = []
                    if any(code in (401, 403) for code in rec.runtime_status_codes):
                        alt_exp.append(
                            "HTTP 401/403 indicates authentication requirement, not endpoint failure"
                        )
                    if (
                        any(code == 404 for code in rec.runtime_status_codes)
                        and "{" in ident.route_template
                    ):
                        alt_exp.append(
                            "HTTP 404 on parameterized route suggests requested resource ID was absent, not endpoint death"
                        )

                    finding = EvidenceBasedEndpointFinding(
                        finding_id=f_id,
                        method=ident.method,
                        canonical_endpoint=ident.route_template,
                        module=ident.module,
                        service_context=ident.service_context,
                        classification=EndpointClassification.DOCUMENTED_AND_OBSERVED,
                        confidence=InventoryConfidence.VERIFIED_OBSERVATION,
                        supporting_evidence=ev_notes,
                        relevant_run_ids=list(all_seen_run_ids),
                        alternative_explanations=alt_exp,
                        missing_evidence=[],
                        recommended_action="Maintain regression test coverage",
                        details=(
                            f"Endpoint '{ident.method} {ident.route_template}' is documented and active "
                            f"({rec.test_passed_count} passed tests)"
                        ),
                    )
                    rec.primary_finding_id = f_id
                    findings.append(finding)
                else:
                    # RULE 1: Untested is NOT dead. Must be DOCUMENTED_NOT_OBSERVED.
                    rec.classification = EndpointClassification.DOCUMENTED_NOT_OBSERVED
                    finding = EvidenceBasedEndpointFinding(
                        finding_id=f_id,
                        method=ident.method,
                        canonical_endpoint=ident.route_template,
                        module=ident.module,
                        service_context=ident.service_context,
                        classification=EndpointClassification.DOCUMENTED_NOT_OBSERVED,
                        confidence=InventoryConfidence.INFERRED,
                        supporting_evidence=[
                            "Documented in OpenAPI specification",
                            "0 test executions or access log requests recorded in observed scope",
                        ],
                        relevant_run_ids=list(all_seen_run_ids),
                        alternative_explanations=[
                            "Endpoint was not exercised by the executed test plan or recorded log window",
                            "Endpoint may be fully operational but awaits synthetic test coverage",
                        ],
                        missing_evidence=[
                            "No test executions or access log traffic captured for this endpoint"
                        ],
                        recommended_action=(
                            f"Synthesize test cases for documented endpoint '{ident.method} {ident.route_template}'"
                        ),
                        details=(
                            f"Endpoint '{ident.method} {ident.route_template}' is documented in OpenAPI spec "
                            "but received 0 calls in observed runs/logs (unobserved, not dead)"
                        ),
                    )
                    rec.primary_finding_id = f_id
                    findings.append(finding)

        # Calculate API Coverage Metrics using distinct canonical operations as denominators
        documented_keys = [k for k, r in inventory_records.items() if r.is_in_spec]
        total_doc = len(documented_keys)

        planned_in_doc = {
            k
            for k in planned_operations
            if k in inventory_records and inventory_records[k].is_in_spec
        }
        generated_in_doc = {
            k
            for k in generated_operations
            if k in inventory_records and inventory_records[k].is_in_spec
        }
        attempted_in_doc = {
            k
            for k in attempted_operations
            if k in inventory_records and inventory_records[k].is_in_spec
        }
        completed_in_doc = {
            k
            for k in completed_operations
            if k in inventory_records and inventory_records[k].is_in_spec
        }
        passed_in_doc = {
            k
            for k in passed_operations
            if k in inventory_records and inventory_records[k].is_in_spec
        }
        failed_in_doc = {
            k
            for k in failed_operations
            if k in inventory_records and inventory_records[k].is_in_spec
        }
        blocked_in_doc = {
            k
            for k in blocked_operations
            if k in inventory_records
            and inventory_records[k].is_in_spec
            and k not in attempted_in_doc
        }
        untested_in_doc = set(documented_keys) - attempted_in_doc

        readonly_keys = [
            k
            for k in documented_keys
            if inventory_records[k].identity.method.upper()
            in ("GET", "HEAD", "OPTIONS")
        ]
        mutation_keys = [
            k
            for k in documented_keys
            if inventory_records[k].identity.method.upper()
            in ("POST", "PUT", "DELETE", "PATCH")
        ]

        ro_attempted = [k for k in readonly_keys if k in attempted_in_doc]
        mut_attempted = [k for k in mutation_keys if k in attempted_in_doc]

        total_ro = len(readonly_keys)
        total_mut = len(mutation_keys)

        coverage_summary = OperationCoverageMetrics(
            total_documented_operations=total_doc,
            planned_operations_count=len(planned_in_doc),
            planned_coverage_pct=(
                (len(planned_in_doc) / total_doc * 100.0) if total_doc else 0.0
            ),
            generated_operations_count=len(generated_in_doc),
            generated_coverage_pct=(
                (len(generated_in_doc) / total_doc * 100.0) if total_doc else 0.0
            ),
            attempted_operations_count=len(attempted_in_doc),
            attempted_coverage_pct=(
                (len(attempted_in_doc) / total_doc * 100.0) if total_doc else 0.0
            ),
            completed_operations_count=len(completed_in_doc),
            completed_coverage_pct=(
                (len(completed_in_doc) / total_doc * 100.0) if total_doc else 0.0
            ),
            passed_operations_count=len(passed_in_doc),
            passed_coverage_pct=(
                (len(passed_in_doc) / total_doc * 100.0) if total_doc else 0.0
            ),
            failed_operations_count=len(failed_in_doc),
            failed_coverage_pct=(
                (len(failed_in_doc) / total_doc * 100.0) if total_doc else 0.0
            ),
            blocked_or_skipped_operations_count=len(blocked_in_doc),
            blocked_or_skipped_coverage_pct=(
                (len(blocked_in_doc) / total_doc * 100.0) if total_doc else 0.0
            ),
            untested_operations_count=len(untested_in_doc),
            untested_coverage_pct=(
                (len(untested_in_doc) / total_doc * 100.0) if total_doc else 0.0
            ),
            readonly_total_operations=total_ro,
            readonly_attempted_operations=len(ro_attempted),
            readonly_coverage_pct=(
                (len(ro_attempted) / total_ro * 100.0) if total_ro else 0.0
            ),
            mutation_total_operations=total_mut,
            mutation_attempted_operations=len(mut_attempted),
            mutation_coverage_pct=(
                (len(mut_attempted) / total_mut * 100.0) if total_mut else 0.0
            ),
        )

        # Module-level breakdowns
        module_breakdown: dict[str, ModuleCoverageMetrics] = {}
        for k in documented_keys:
            rec = inventory_records[k]
            mod = rec.identity.module
            if mod not in module_breakdown:
                module_breakdown[mod] = ModuleCoverageMetrics(module=mod)
            m_stat = module_breakdown[mod]
            m_stat.total_documented += 1
            if k in attempted_in_doc:
                m_stat.attempted += 1
            if k in passed_in_doc:
                m_stat.passed += 1
            if k in untested_in_doc:
                m_stat.untested += 1

        for m_stat in module_breakdown.values():
            m_stat.coverage_pct = (
                (m_stat.attempted / m_stat.total_documented * 100.0)
                if m_stat.total_documented
                else 0.0
            )

        # Classification counts
        classification_counts: dict[str, int] = {
            c.value: 0 for c in EndpointClassification
        }
        for rec in inventory_records.values():
            classification_counts[rec.classification.value] += 1

        # Apply module filter if specified
        final_findings = findings
        if module_filter:
            m_target = module_filter.strip().lower()
            final_findings = [f for f in findings if f.module.lower() == m_target]

        # Deterministic sorting
        final_findings.sort(
            key=lambda f: (
                f.module.lower(),
                f.classification.value,
                f.method,
                f.canonical_endpoint,
            )
        )
        sorted_inventory = sorted(
            inventory_records.values(),
            key=lambda r: (
                r.identity.module.lower(),
                r.identity.method,
                r.identity.route_template,
            ),
        )

        return EndpointInventoryReport(
            schema_version="1.0.0",
            evaluated_at=self._clock().isoformat(),
            target_context=target_service,
            observation_window=(
                f"{len(all_runs)} run(s), {len(all_access_entries)} access log call(s)"
            ),
            evidence_sources_evaluated=evaluated_sources,
            evidence_limitations=limitations,
            total_canonical_operations=len(inventory_records),
            coverage_summary=coverage_summary,
            module_breakdown=module_breakdown,
            classification_counts=classification_counts,
            inventory=sorted_inventory,
            findings=final_findings,
        )
