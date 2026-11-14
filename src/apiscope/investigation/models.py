"""Domain models, diagnostic taxonomy, and investigation reports (Task 22).

Provides structured records for failure triage findings, causal workflow analysis,
evidence references, epistemic confidence levels, and JSON/YAML serializable reports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import yaml

from apiscope.core.logging_config import redact_sensitive_text


class DiagnosticCategory(StrEnum):
    """Taxonomy of API test and workflow failure categories."""

    TEST_DEFINITION_ISSUE = "TEST_DEFINITION_ISSUE"
    FIXTURE_OR_DEPENDENCY_ISSUE = "FIXTURE_OR_DEPENDENCY_ISSUE"
    ENVIRONMENT_OR_CONFIGURATION_ISSUE = "ENVIRONMENT_OR_CONFIGURATION_ISSUE"
    NETWORK_OR_TRANSPORT_ISSUE = "NETWORK_OR_TRANSPORT_ISSUE"
    ACCESS_OR_POLICY_BLOCK = "ACCESS_OR_POLICY_BLOCK"
    CONTRACT_OR_RESPONSE_MISMATCH = "CONTRACT_OR_RESPONSE_MISMATCH"
    SUSPECTED_APPLICATION_FAILURE = "SUSPECTED_APPLICATION_FAILURE"
    CLEANUP_ISSUE = "CLEANUP_ISSUE"
    INCONCLUSIVE = "INCONCLUSIVE"


class ConfidenceLevel(StrEnum):
    """Epistemic confidence of diagnostic conclusions based on recorded evidence."""

    VERIFIED_OBSERVATION = (
        "VERIFIED_OBSERVATION"  # Directly supported by recorded facts
    )
    SUSPECTED_CAUSE = "SUSPECTED_CAUSE"  # Plausible inference, alternatives considered
    INCONCLUSIVE = "INCONCLUSIVE"  # Insufficient or conflicting evidence


class DiagnosticDisposition(StrEnum):
    """Actionable verdict summarizing the next required engineering direction."""

    ACTIONABLE_DEFECT_SUSPECTED = "ACTIONABLE_DEFECT_SUSPECTED"
    CONTRACT_MISMATCH_DETECTED = "CONTRACT_MISMATCH_DETECTED"
    FIXTURE_RESOLUTION_REQUIRED = "FIXTURE_RESOLUTION_REQUIRED"
    TEST_DEFINITION_FIX_NEEDED = "TEST_DEFINITION_FIX_NEEDED"
    ENVIRONMENT_OR_CONFIG_FIX_NEEDED = "ENVIRONMENT_OR_CONFIG_FIX_NEEDED"
    NETWORK_OR_INFRASTRUCTURE_ISSUE = "NETWORK_OR_INFRASTRUCTURE_ISSUE"
    POLICY_AUTHORIZATION_REQUIRED = "POLICY_AUTHORIZATION_REQUIRED"
    CLEANUP_FAILURE_DETECTED = "CLEANUP_FAILURE_DETECTED"
    INVESTIGATION_INCONCLUSIVE = "INVESTIGATION_INCONCLUSIVE"


class ExecutionStage(StrEnum):
    """Lifecycle phase at which an execution failure occurred."""

    GATE = "GATE"
    SETUP = "SETUP"
    TEST = "TEST"
    CLEANUP = "CLEANUP"
    UNKNOWN = "UNKNOWN"


class FindingSeverity(StrEnum):
    """Severity rating of an investigated finding."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


@dataclass
class InvestigationFinding:
    """Detailed diagnostic record for a specific failed or blocked test case."""

    __test__ = False

    finding_id: str
    run_id: str
    test_name: str
    method: str
    url: str
    execution_phase: ExecutionStage
    observed_symptom: str
    category: DiagnosticCategory
    confidence: ConfidenceLevel
    severity: FindingSeverity
    disposition: DiagnosticDisposition
    workflow_id: str | None = None
    is_primary_failure: bool = True
    primary_finding_id: str | None = None
    downstream_impact_count: int = 0
    candidate_causes: list[str] = field(default_factory=list)
    alternative_explanations: list[str] = field(default_factory=list)
    supporting_evidence: list[str] = field(default_factory=list)
    contradictory_evidence: list[str] = field(default_factory=list)
    recommended_next_step: str = ""
    evidence_references: list[dict[str, Any]] = field(default_factory=list)
    status_code: int | None = None
    elapsed_ms: float | None = None
    error_message: str | None = None
    ai_hypothesis: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert finding to a JSON-serializable dictionary with secret redaction."""
        return {
            "finding_id": self.finding_id,
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "test_name": self.test_name,
            "method": self.method,
            "url": redact_sensitive_text(self.url),
            "execution_phase": self.execution_phase.value,
            "is_primary_failure": self.is_primary_failure,
            "primary_finding_id": self.primary_finding_id,
            "downstream_impact_count": self.downstream_impact_count,
            "observed_symptom": redact_sensitive_text(self.observed_symptom),
            "category": self.category.value,
            "confidence": self.confidence.value,
            "severity": self.severity.value,
            "disposition": self.disposition.value,
            "candidate_causes": [
                redact_sensitive_text(c) for c in self.candidate_causes
            ],
            "alternative_explanations": [
                redact_sensitive_text(a) for a in self.alternative_explanations
            ],
            "supporting_evidence": [
                redact_sensitive_text(e) for e in self.supporting_evidence
            ],
            "contradictory_evidence": [
                redact_sensitive_text(c) for c in self.contradictory_evidence
            ],
            "recommended_next_step": redact_sensitive_text(self.recommended_next_step),
            "evidence_references": list(self.evidence_references),
            "status_code": self.status_code,
            "elapsed_ms": self.elapsed_ms,
            "error_message": (
                redact_sensitive_text(self.error_message)
                if self.error_message
                else None
            ),
            "ai_hypothesis": (
                redact_sensitive_text(self.ai_hypothesis)
                if self.ai_hypothesis
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InvestigationFinding:
        """Construct finding instance from dictionary."""
        return cls(
            finding_id=str(data.get("finding_id", "")),
            run_id=str(data.get("run_id", "")),
            workflow_id=data.get("workflow_id"),
            test_name=str(data.get("test_name", "")),
            method=str(data.get("method", "")),
            url=str(data.get("url", "")),
            execution_phase=ExecutionStage(
                data.get("execution_phase", ExecutionStage.UNKNOWN.value)
            ),
            observed_symptom=str(data.get("observed_symptom", "")),
            category=DiagnosticCategory(
                data.get("category", DiagnosticCategory.INCONCLUSIVE.value)
            ),
            confidence=ConfidenceLevel(
                data.get("confidence", ConfidenceLevel.INCONCLUSIVE.value)
            ),
            severity=FindingSeverity(
                data.get("severity", FindingSeverity.MEDIUM.value)
            ),
            disposition=DiagnosticDisposition(
                data.get(
                    "disposition",
                    DiagnosticDisposition.INVESTIGATION_INCONCLUSIVE.value,
                )
            ),
            is_primary_failure=bool(data.get("is_primary_failure", True)),
            primary_finding_id=data.get("primary_finding_id"),
            downstream_impact_count=int(data.get("downstream_impact_count", 0)),
            candidate_causes=list(data.get("candidate_causes", [])),
            alternative_explanations=list(data.get("alternative_explanations", [])),
            supporting_evidence=list(data.get("supporting_evidence", [])),
            contradictory_evidence=list(data.get("contradictory_evidence", [])),
            recommended_next_step=str(data.get("recommended_next_step", "")),
            evidence_references=list(data.get("evidence_references", [])),
            status_code=data.get("status_code"),
            elapsed_ms=data.get("elapsed_ms"),
            error_message=data.get("error_message"),
            ai_hypothesis=data.get("ai_hypothesis"),
        )


@dataclass
class FailureInvestigationReport:
    """Comprehensive failure analysis report summarizing deterministic investigation findings."""

    __test__ = False

    investigation_id: str
    run_id: str
    evaluated_at: str
    total_cases_evaluated: int
    total_failures_detected: int
    primary_failures_count: int
    downstream_consequences_count: int
    category_counts: dict[str, int]
    disposition_counts: dict[str, int]
    findings: list[InvestigationFinding] = field(default_factory=list)
    has_application_defects: bool = False
    has_test_or_fixture_issues: bool = False
    has_environment_or_policy_blocks: bool = False
    summary: str = ""
    missing_evidence_gaps: list[str] = field(default_factory=list)
    ai_analysis: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert report to JSON-serializable dictionary."""
        return {
            "investigation_id": self.investigation_id,
            "run_id": self.run_id,
            "evaluated_at": self.evaluated_at,
            "summary_metrics": {
                "total_cases_evaluated": self.total_cases_evaluated,
                "total_failures_detected": self.total_failures_detected,
                "primary_failures_count": self.primary_failures_count,
                "downstream_consequences_count": self.downstream_consequences_count,
                "has_application_defects": self.has_application_defects,
                "has_test_or_fixture_issues": self.has_test_or_fixture_issues,
                "has_environment_or_policy_blocks": self.has_environment_or_policy_blocks,
            },
            "category_counts": dict(self.category_counts),
            "disposition_counts": dict(self.disposition_counts),
            "summary": self.summary,
            "missing_evidence_gaps": list(self.missing_evidence_gaps),
            "findings": [f.to_dict() for f in self.findings],
            "ai_analysis": self.ai_analysis,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize report to indented JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def to_yaml(self) -> str:
        """Serialize report to YAML string."""
        return yaml.safe_dump(self.to_dict(), sort_keys=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FailureInvestigationReport:
        """Construct investigation report from dictionary."""
        metrics = data.get("summary_metrics", {})
        findings_data = data.get("findings", [])
        return cls(
            investigation_id=str(
                data.get(
                    "investigation_id", f"inv-{int(datetime.now(UTC).timestamp())}"
                )
            ),
            run_id=str(data.get("run_id", "")),
            evaluated_at=str(data.get("evaluated_at", datetime.now(UTC).isoformat())),
            total_cases_evaluated=int(
                metrics.get(
                    "total_cases_evaluated", data.get("total_cases_evaluated", 0)
                )
            ),
            total_failures_detected=int(
                metrics.get(
                    "total_failures_detected", data.get("total_failures_detected", 0)
                )
            ),
            primary_failures_count=int(
                metrics.get(
                    "primary_failures_count", data.get("primary_failures_count", 0)
                )
            ),
            downstream_consequences_count=int(
                metrics.get(
                    "downstream_consequences_count",
                    data.get("downstream_consequences_count", 0),
                )
            ),
            category_counts=dict(data.get("category_counts", {})),
            disposition_counts=dict(data.get("disposition_counts", {})),
            findings=[InvestigationFinding.from_dict(f) for f in findings_data],
            has_application_defects=bool(
                metrics.get(
                    "has_application_defects",
                    data.get("has_application_defects", False),
                )
            ),
            has_test_or_fixture_issues=bool(
                metrics.get(
                    "has_test_or_fixture_issues",
                    data.get("has_test_or_fixture_issues", False),
                )
            ),
            has_environment_or_policy_blocks=bool(
                metrics.get(
                    "has_environment_or_policy_blocks",
                    data.get("has_environment_or_policy_blocks", False),
                )
            ),
            summary=str(data.get("summary", "")),
            missing_evidence_gaps=list(data.get("missing_evidence_gaps", [])),
            ai_analysis=data.get("ai_analysis"),
        )
