"""Domain models, diagnostic verdict taxonomy, and trust assessment reports (Task 25).

Provides structured records for unified multi-source evidence, deduplicated trust
findings, transparent evidence-strength assessments, and JSON/YAML serializable reports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import yaml

from apiscope.analysis.endpoint_inventory import OperationCoverageMetrics
from apiscope.core.logging_config import redact_sensitive_text


class DiagnosticVerdict(StrEnum):
    """Conservative, evidence-gated diagnostic verdict taxonomy."""

    VERIFIED_CONTRACT_MISMATCH = "VERIFIED_CONTRACT_MISMATCH"
    VERIFIED_SEMANTIC_MISMATCH = "VERIFIED_SEMANTIC_MISMATCH"
    SUSPECTED_APPLICATION_DEFECT = "SUSPECTED_APPLICATION_DEFECT"
    TEST_OR_ORACLE_ISSUE = "TEST_OR_ORACLE_ISSUE"
    FIXTURE_OR_DEPENDENCY_ISSUE = "FIXTURE_OR_DEPENDENCY_ISSUE"
    ENVIRONMENT_OR_TRANSPORT_ISSUE = "ENVIRONMENT_OR_TRANSPORT_ISSUE"
    ACCESS_OR_POLICY_BLOCK = "ACCESS_OR_POLICY_BLOCK"
    CLEANUP_ISSUE = "CLEANUP_ISSUE"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_TESTED = "NOT_TESTED"
    PASS_WITHIN_VERIFIED_COVERAGE = "PASS_WITHIN_VERIFIED_COVERAGE"


class VerdictConfidence(StrEnum):
    """Epistemic certainty level of diagnostic findings based on recorded facts."""

    VERIFIED = (
        "VERIFIED"  # Directly corroborated by immutable facts & approved contracts
    )
    SUSPECTED = "SUSPECTED"  # Plausible inference, alternatives considered
    INCONCLUSIVE = "INCONCLUSIVE"  # Conflicting, incomplete, or uncorroborated evidence


class VerdictSeverity(StrEnum):
    """Severity rating of a diagnostic finding."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class OracleProvenance(StrEnum):
    """Source of truth backing expected scenario outcome."""

    EXPLICIT_CONTRACT = "EXPLICIT_CONTRACT"
    INDEPENDENT_REQUIREMENT = "INDEPENDENT_REQUIREMENT"
    SOURCE_CODE_INFERENCE = "SOURCE_CODE_INFERENCE"
    PROPERTY_CONSISTENCY = "PROPERTY_CONSISTENCY"
    AI_HYPOTHESIS = "AI_HYPOTHESIS"
    RUNTIME_DEFAULT = "RUNTIME_DEFAULT"


class EvidenceSubsystem(StrEnum):
    """Originating subsystem providing diagnostic evidence."""

    FAILURE_INVESTIGATION = "FAILURE_INVESTIGATION"
    SILENT_FAILURE_DETECTOR = "SILENT_FAILURE_DETECTOR"
    ENDPOINT_INVENTORY = "ENDPOINT_INVENTORY"
    WORKFLOW_EXECUTION = "WORKFLOW_EXECUTION"
    RUN_RESULT = "RUN_RESULT"
    FIXTURE_MANAGER = "FIXTURE_MANAGER"
    COMPILER = "COMPILER"
    CONTRACT_ANALYZER = "CONTRACT_ANALYZER"
    LATENCY_ANALYZER = "LATENCY_ANALYZER"
    ACCESS_LOG = "ACCESS_LOG"


@dataclass
class TrustEvidenceRecord:
    """Normalized, versioned representation of raw diagnostic evidence."""

    __test__ = False

    evidence_id: str
    source_subsystem: EvidenceSubsystem
    source_report_or_run_id: str | None = None
    scenario_id: str | None = None
    workflow_id: str | None = None
    step_id: str | None = None
    endpoint_identity: str = ""
    method: str = "GET"
    route_template: str = "/"
    target_service: str = "primary"
    target_origin: str | None = None
    observation_timestamp: str | None = None
    observed_outcome: str = ""
    expected_outcome: str | None = None
    oracle_provenance: OracleProvenance = OracleProvenance.RUNTIME_DEFAULT
    oracle_approval_state: str = "APPROVED"
    fixture_prerequisites_ready: bool | None = None
    assertion_reference: str | None = None
    data_completeness: str = "COMPLETE"
    confidence: VerdictConfidence = VerdictConfidence.VERIFIED
    supporting_details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert evidence record to a sanitized serializable dictionary."""
        return {
            "evidence_id": self.evidence_id,
            "source_subsystem": self.source_subsystem.value,
            "source_report_or_run_id": self.source_report_or_run_id,
            "scenario_id": self.scenario_id,
            "workflow_id": self.workflow_id,
            "step_id": self.step_id,
            "endpoint_identity": self.endpoint_identity,
            "method": self.method,
            "route_template": self.route_template,
            "target_service": self.target_service,
            "target_origin": self.target_origin,
            "observation_timestamp": self.observation_timestamp,
            "observed_outcome": redact_sensitive_text(self.observed_outcome),
            "expected_outcome": (
                redact_sensitive_text(self.expected_outcome)
                if self.expected_outcome
                else None
            ),
            "oracle_provenance": self.oracle_provenance.value,
            "oracle_approval_state": self.oracle_approval_state,
            "fixture_prerequisites_ready": self.fixture_prerequisites_ready,
            "assertion_reference": self.assertion_reference,
            "data_completeness": self.data_completeness,
            "confidence": self.confidence.value,
            "supporting_details": {
                k: redact_sensitive_text(str(v))
                for k, v in self.supporting_details.items()
            },
        }


@dataclass
class TrustFinding:
    """Defensible, deduplicated primary diagnostic finding."""

    __test__ = False

    finding_id: str
    verdict: DiagnosticVerdict
    confidence: VerdictConfidence
    severity: VerdictSeverity
    endpoint: str
    method: str
    route_template: str
    module: str
    service_context: str
    what_happened: str
    why_reported: str
    supporting_evidence: list[str]
    assumptions_used: list[str] = field(default_factory=list)
    contradictory_evidence: list[str] = field(default_factory=list)
    is_verified_observation: bool = True
    what_remains_untested: list[str] = field(default_factory=list)
    recommended_action: str = ""
    downstream_symptoms: list[str] = field(default_factory=list)
    correlated_source_finding_ids: list[str] = field(default_factory=list)
    evidence_references: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert trust finding to JSON-serializable dictionary with sanitized details."""
        return {
            "finding_id": self.finding_id,
            "verdict": self.verdict.value,
            "confidence": self.confidence.value,
            "severity": self.severity.value,
            "endpoint": self.endpoint,
            "method": self.method,
            "route_template": self.route_template,
            "module": self.module,
            "service_context": self.service_context,
            "what_happened": redact_sensitive_text(self.what_happened),
            "why_reported": redact_sensitive_text(self.why_reported),
            "supporting_evidence": [
                redact_sensitive_text(e) for e in self.supporting_evidence
            ],
            "assumptions_used": [
                redact_sensitive_text(a) for a in self.assumptions_used
            ],
            "contradictory_evidence": [
                redact_sensitive_text(c) for c in self.contradictory_evidence
            ],
            "is_verified_observation": self.is_verified_observation,
            "what_remains_untested": [
                redact_sensitive_text(u) for u in self.what_remains_untested
            ],
            "recommended_action": redact_sensitive_text(self.recommended_action),
            "downstream_symptoms": [
                redact_sensitive_text(s) for s in self.downstream_symptoms
            ],
            "correlated_source_finding_ids": list(self.correlated_source_finding_ids),
            "evidence_references": list(self.evidence_references),
        }


@dataclass
class TrustAssessmentReport:
    """Unified Trust & Evidence evaluation report across diagnostic systems."""

    __test__ = False

    schema_version: str = "1.0.0"
    evaluated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    target_context: str = "primary"
    assessment_id: str = ""
    run_id: str | None = None
    observation_window: str | None = None
    coverage: OperationCoverageMetrics = field(default_factory=OperationCoverageMetrics)
    verdict_counts: dict[str, int] = field(default_factory=dict)
    findings: list[TrustFinding] = field(default_factory=list)
    total_evidence_records: int = 0
    deduplicated_findings_count: int = 0
    grouped_symptoms_count: int = 0
    evidence_sources_evaluated: list[str] = field(default_factory=list)
    evidence_limitations: list[str] = field(default_factory=list)
    remaining_verification_gaps: list[str] = field(default_factory=list)
    ai_consultation: dict[str, Any] | None = None

    def filter_by_module(self, module: str) -> list[TrustFinding]:
        """Return findings matching specified module name (case-insensitive)."""
        target = module.strip().lower()
        return [f for f in self.findings if f.module.lower() == target]

    def filter_by_verdict(self, verdict: DiagnosticVerdict) -> list[TrustFinding]:
        """Return findings matching specified diagnostic verdict."""
        return [f for f in self.findings if f.verdict == verdict]

    def to_dict(self) -> dict[str, Any]:
        """Convert report to JSON-serializable dictionary."""
        return {
            "schema_version": self.schema_version,
            "evaluated_at": self.evaluated_at,
            "target_context": self.target_context,
            "assessment_id": self.assessment_id,
            "run_id": self.run_id,
            "observation_window": self.observation_window,
            "coverage": self.coverage.to_dict(),
            "verdict_counts": dict(self.verdict_counts),
            "total_evidence_records": self.total_evidence_records,
            "deduplicated_findings_count": self.deduplicated_findings_count,
            "grouped_symptoms_count": self.grouped_symptoms_count,
            "evidence_sources_evaluated": list(self.evidence_sources_evaluated),
            "evidence_limitations": list(self.evidence_limitations),
            "remaining_verification_gaps": list(self.remaining_verification_gaps),
            "findings": [f.to_dict() for f in self.findings],
            "ai_consultation": self.ai_consultation,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize report to structured JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def to_yaml(self) -> str:
        """Serialize report to structured YAML string."""
        return yaml.dump(self.to_dict(), sort_keys=False)
