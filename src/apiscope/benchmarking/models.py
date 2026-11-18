"""Domain models and data structures for ApiScope defect-detection benchmarking (Task 28).

Defines versioned ground-truth manifests, evaluation outcomes, confusion matrix metrics,
and cross-diagnostic correlation records with strict ground-truth independence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import yaml

from apiscope.core.logging_config import redact_sensitive_text


class PlantedDefectCategory(StrEnum):
    """Canonical categories of deliberately planted API defects (A through P)."""

    A_RESPONSE_SCHEMA_VIOLATION = "A_RESPONSE_SCHEMA_VIOLATION"
    B_INCORRECT_HTTP_STATUS = "B_INCORRECT_HTTP_STATUS"
    C_200_ERROR_ENVELOPE = "C_200_ERROR_ENVELOPE"
    D_MISSING_REQUIRED_DATA = "D_MISSING_REQUIRED_DATA"
    E_BROKEN_CREATION_SIDE_EFFECT = "E_BROKEN_CREATION_SIDE_EFFECT"
    F_DELETE_WITHOUT_REMOVAL = "F_DELETE_WITHOUT_REMOVAL"
    G_BUSINESS_INVARIANT_VIOLATION = "G_BUSINESS_INVARIANT_VIOLATION"
    H_BROKEN_STATE_TRANSITION = "H_BROKEN_STATE_TRANSITION"
    I_ACCESS_CONTROL_CONTRADICTION = "I_ACCESS_CONTROL_CONTRADICTION"
    J_CONTRACT_HEADER_CONTRADICTION = "J_CONTRACT_HEADER_CONTRADICTION"
    K_REPEATABLE_LATENCY_REGRESSION = "K_REPEATABLE_LATENCY_REGRESSION"
    L_FLAKY_BEHAVIOUR = "L_FLAKY_BEHAVIOUR"
    M_SOURCE_SPEC_RUNTIME_INCONSISTENCY = "M_SOURCE_SPEC_RUNTIME_INCONSISTENCY"
    N_OBSOLETE_ENDPOINT = "N_OBSOLETE_ENDPOINT"
    O_MULTIPLE_SIGNALS_SINGLE_DEFECT = "O_MULTIPLE_SIGNALS_SINGLE_DEFECT"
    P_FIXTURE_PRODUCER_FAILURE = "P_FIXTURE_PRODUCER_FAILURE"


class CleanControlCategory(StrEnum):
    """Canonical categories of known-clean negative control scenarios."""

    CLEAN_COMPLIANT_RESPONSE = "CLEAN_COMPLIANT_RESPONSE"
    CLEAN_EMPTY_COLLECTION = "CLEAN_EMPTY_COLLECTION"
    CLEAN_204_NO_CONTENT = "CLEAN_204_NO_CONTENT"
    CLEAN_HANDLED_404_NOT_FOUND = "CLEAN_HANDLED_404_NOT_FOUND"
    CLEAN_EXPECTED_401_UNAUTHORIZED = "CLEAN_EXPECTED_401_UNAUTHORIZED"
    CLEAN_EXPECTED_403_FORBIDDEN = "CLEAN_EXPECTED_403_FORBIDDEN"
    CLEAN_UNTESTED_ENDPOINT = "CLEAN_UNTESTED_ENDPOINT"
    CLEAN_AUTHORIZED_MUTATION = "CLEAN_AUTHORIZED_MUTATION"
    CLEAN_VERIFIED_SIDE_EFFECT = "CLEAN_VERIFIED_SIDE_EFFECT"
    CLEAN_PAGINATION_AND_OPTIONALS = "CLEAN_PAGINATION_AND_OPTIONALS"
    CLEAN_NORMAL_TIMING_VARIATION = "CLEAN_NORMAL_TIMING_VARIATION"
    CLEAN_INSUFFICIENT_LATENCY_SAMPLES = "CLEAN_INSUFFICIENT_LATENCY_SAMPLES"
    CLEAN_DRY_RUN_POLICY_BLOCK = "CLEAN_DRY_RUN_POLICY_BLOCK"
    CLEAN_MISSING_TELEMETRY = "CLEAN_MISSING_TELEMETRY"
    CLEAN_CONSISTENT_IDEMPOTENT_GET = "CLEAN_CONSISTENT_IDEMPOTENT_GET"
    CLEAN_SOURCE_SPEC_MATCH = "CLEAN_SOURCE_SPEC_MATCH"


class EvaluationClassification(StrEnum):
    """Standard evaluation outcome classification for a benchmark case."""

    TRUE_POSITIVE = "TRUE_POSITIVE"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    FALSE_NEGATIVE = "FALSE_NEGATIVE"
    TRUE_NEGATIVE = "TRUE_NEGATIVE"
    UNSUPPORTED = "UNSUPPORTED"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class BenchmarkCase:
    """Independent ground-truth specification for a single controlled benchmark case."""

    case_id: str
    title: str
    target_operation: str  # e.g. "POST /api/products"
    method: str  # e.g. "POST"
    path: str  # e.g. "/api/products"
    service_context: str  # e.g. "products"
    is_defect: bool
    category: str  # PlantedDefectCategory or CleanControlCategory value
    intended_behaviour: str
    planted_behaviour: str
    expected_verdict: str  # Expected ApiScope DiagnosticVerdict value
    expected_confidence: str  # "VERIFIED", "SUSPECTED", or "INCONCLUSIVE"
    evidence_necessary: str
    prerequisite_state: str
    expected_observable_symptoms: str
    required_execution_permissions: str  # "READ_ONLY" or "MUTATION"
    expected_detectable: bool = True
    known_limitations: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert benchmark case to JSON-serializable dictionary."""
        return {
            "case_id": self.case_id,
            "title": self.title,
            "target_operation": self.target_operation,
            "method": self.method,
            "path": self.path,
            "service_context": self.service_context,
            "is_defect": self.is_defect,
            "category": self.category,
            "intended_behaviour": self.intended_behaviour,
            "planted_behaviour": self.planted_behaviour,
            "expected_verdict": self.expected_verdict,
            "expected_confidence": self.expected_confidence,
            "evidence_necessary": self.evidence_necessary,
            "prerequisite_state": self.prerequisite_state,
            "expected_observable_symptoms": self.expected_observable_symptoms,
            "required_execution_permissions": self.required_execution_permissions,
            "expected_detectable": self.expected_detectable,
            "known_limitations": self.known_limitations,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BenchmarkCase:
        """Construct BenchmarkCase from dictionary."""
        return cls(
            case_id=data["case_id"],
            title=data["title"],
            target_operation=data["target_operation"],
            method=data.get("method", data["target_operation"].split()[0]),
            path=data.get("path", data["target_operation"].split()[-1]),
            service_context=data.get("service_context", "default"),
            is_defect=bool(data["is_defect"]),
            category=data["category"],
            intended_behaviour=data.get("intended_behaviour", ""),
            planted_behaviour=data.get("planted_behaviour", ""),
            expected_verdict=data.get("expected_verdict", "VERIFIED_CONTRACT_MISMATCH"),
            expected_confidence=data.get("expected_confidence", "VERIFIED"),
            evidence_necessary=data.get("evidence_necessary", ""),
            prerequisite_state=data.get("prerequisite_state", "None"),
            expected_observable_symptoms=data.get("expected_observable_symptoms", ""),
            required_execution_permissions=data.get(
                "required_execution_permissions", "READ_ONLY"
            ),
            expected_detectable=bool(data.get("expected_detectable", True)),
            known_limitations=data.get("known_limitations", ""),
        )


@dataclass
class BenchmarkManifest:
    """Versioned collection of ground-truth benchmark cases."""

    manifest_version: str = "1.0.0"
    title: str = "ApiScope Defect-Detection Ground-Truth Benchmark Manifest"
    description: str = (
        "Independent ground-truth specification defining 16 planted defects "
        "and 16 clean controls for ApiScope 1.0 accuracy evaluation."
    )
    generated_at: str = field(
        default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    cases: list[BenchmarkCase] = field(default_factory=list)

    @property
    def total_cases(self) -> int:
        """Total number of benchmark cases."""
        return len(self.cases)

    @property
    def total_cases_count(self) -> int:
        """Total number of benchmark cases (alias)."""
        return len(self.cases)

    @property
    def defect_cases(self) -> list[BenchmarkCase]:
        """All cases representing deliberate planted defects."""
        return [c for c in self.cases if c.is_defect]

    @property
    def clean_cases(self) -> list[BenchmarkCase]:
        """All cases representing clean negative controls."""
        return [c for c in self.cases if not c.is_defect]

    def get_case(self, case_id: str) -> BenchmarkCase | None:
        """Lookup case by stable ID."""
        for c in self.cases:
            if c.case_id == case_id:
                return c
        return None

    def to_dict(self) -> dict[str, Any]:
        """Convert manifest to JSON-serializable dictionary."""
        return {
            "manifest_version": self.manifest_version,
            "title": self.title,
            "description": self.description,
            "generated_at": self.generated_at,
            "summary": {
                "total_cases": self.total_cases,
                "defect_cases_count": len(self.defect_cases),
                "clean_controls_count": len(self.clean_cases),
            },
            "cases": [c.to_dict() for c in self.cases],
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize manifest to formatted JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def to_yaml(self) -> str:
        """Serialize manifest to YAML string."""
        return yaml.dump(self.to_dict(), sort_keys=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BenchmarkManifest:
        """Construct BenchmarkManifest from dictionary."""
        cases = [BenchmarkCase.from_dict(c) for c in data.get("cases", [])]
        return cls(
            manifest_version=data.get("manifest_version", "1.0.0"),
            title=data.get("title", ""),
            description=data.get("description", ""),
            generated_at=data.get("generated_at", ""),
            cases=cases,
        )


@dataclass
class CaseEvaluationResult:
    """Individual scored evaluation result for a benchmark case."""

    case_id: str
    title: str
    is_defect: bool
    category: str
    expected_verdict: str
    classification: EvaluationClassification
    detected_verdict: str | None = None
    matched_finding_ids: list[str] = field(default_factory=list)
    findings_count: int = 0
    duplicate_count: int = 0
    is_misclassified: bool = False
    evidence_adequate: bool = True
    evidence_notes: str = ""
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert evaluation result to JSON-serializable dictionary."""
        return {
            "case_id": self.case_id,
            "title": self.title,
            "is_defect": self.is_defect,
            "category": self.category,
            "expected_verdict": self.expected_verdict,
            "classification": self.classification.value,
            "detected_verdict": self.detected_verdict,
            "matched_finding_ids": self.matched_finding_ids,
            "findings_count": self.findings_count,
            "duplicate_count": self.duplicate_count,
            "is_misclassified": self.is_misclassified,
            "evidence_adequate": self.evidence_adequate,
            "evidence_notes": redact_sensitive_text(self.evidence_notes),
            "explanation": redact_sensitive_text(self.explanation),
        }


@dataclass
class CategoryAccuracyMetrics:
    """Accuracy metrics computed for a specific defect or control category."""

    category: str
    total_cases: int = 0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    unsupported: int = 0
    inconclusive: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert category metrics to dictionary."""
        return {
            "category": self.category,
            "total_cases": self.total_cases,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "unsupported": self.unsupported,
            "inconclusive": self.inconclusive,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1_score": round(self.f1_score, 4),
        }


@dataclass
class BenchmarkMetrics:
    """Overall confusion matrix and accuracy metrics for a benchmark evaluation run."""

    total_cases: int = 0
    defect_cases_count: int = 0
    clean_cases_count: int = 0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    unsupported: int = 0
    inconclusive: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1_score: float = 0.0
    false_positive_rate: float = 0.0
    total_raw_findings: int = 0
    deduplicated_findings: int = 0
    duplicate_findings_count: int = 0
    deduplication_rate: float = 0.0
    misclassifications_count: int = 0
    misclassification_rate: float = 0.0
    per_category: dict[str, CategoryAccuracyMetrics] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            "total_cases": self.total_cases,
            "defect_cases_count": self.defect_cases_count,
            "clean_cases_count": self.clean_cases_count,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1_score": round(self.f1_score, 4),
            "false_positive_rate": round(self.false_positive_rate, 4),
            "confusion_matrix": {
                "true_positives": self.tp,
                "false_positives": self.fp,
                "false_negatives": self.fn,
                "true_negatives": self.tn,
                "unsupported": self.unsupported,
                "inconclusive": self.inconclusive,
            },
            "scores": {
                "precision": round(self.precision, 4),
                "recall": round(self.recall, 4),
                "f1_score": round(self.f1_score, 4),
                "false_positive_rate": round(self.false_positive_rate, 4),
            },
            "deduplication": {
                "total_raw_findings": self.total_raw_findings,
                "deduplicated_findings": self.deduplicated_findings,
                "duplicate_findings_count": self.duplicate_findings_count,
                "deduplication_rate": round(self.deduplication_rate, 4),
            },
            "classification_accuracy": {
                "misclassifications_count": self.misclassifications_count,
                "misclassification_rate": round(self.misclassification_rate, 4),
            },
            "per_category": {k: v.to_dict() for k, v in self.per_category.items()},
        }


@dataclass
class StockPileValidationSummary:
    """Validation report analyzing authentic recorded StockPile artifacts."""

    total_documented_operations: int = 60
    total_get_operations: int = 30
    total_mutating_operations: int = 30
    smoke_tests_passed: int = 8
    extended_get_tests_passed: int = 30
    mutating_tests_executed: int = 0
    unverified_mutating_operations_count: int = 30
    read_only_coverage_pct: float = 50.0
    honest_reporting_verified: bool = True
    fabricated_mutations_count: int = 0
    fabricated_application_defects_count: int = 0
    evidence_artifacts_present: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def readonly_operations_count(self) -> int:
        """Alias for total GET operations count."""
        return self.total_get_operations

    @property
    def mutating_operations_count(self) -> int:
        """Alias for total mutating operations count."""
        return self.total_mutating_operations

    def to_markdown(self) -> str:
        """Render StockPile validation summary to markdown table/section."""
        return (
            "### StockPile Evidence Verification\n\n"
            f"- **Total Documented Operations**: {self.total_documented_operations} "
            f"({self.total_get_operations} GET, {self.total_mutating_operations} Mutating)\n"
            f"- **Smoke Tests Passed**: {self.smoke_tests_passed}\n"
            f"- **Extended GET Tests Passed**: {self.extended_get_tests_passed}\n"
            f"- **Mutating Tests Executed**: {self.mutating_tests_executed}\n"
            f"- **Unverified Mutations**: {self.unverified_mutating_operations_count} mutating operations (honestly preserved without fabricated live execution)\n"
            f"- **Read-Only Coverage**: {self.read_only_coverage_pct:.1f}%\n"
            f"- **Honest Reporting Verified**: {self.honest_reporting_verified}\n"
            f"- **Fabricated Mutations Count**: {self.fabricated_mutations_count}\n"
            f"- **Fabricated Defects Count**: {self.fabricated_application_defects_count}\n"
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert StockPile validation summary to dictionary."""
        return {
            "total_documented_operations": self.total_documented_operations,
            "readonly_operations_count": self.total_get_operations,
            "mutating_operations_count": self.total_mutating_operations,
            "smoke_tests_passed": self.smoke_tests_passed,
            "extended_get_tests_passed": self.extended_get_tests_passed,
            "mutating_tests_executed": self.mutating_tests_executed,
            "unverified_mutating_operations_count": (
                self.unverified_mutating_operations_count
            ),
            "read_only_coverage_pct": round(self.read_only_coverage_pct, 1),
            "honest_reporting_verified": self.honest_reporting_verified,
            "fabricated_mutations_count": self.fabricated_mutations_count,
            "fabricated_application_defects_count": (
                self.fabricated_application_defects_count
            ),
            "operations_breakdown": {
                "get_operations": self.total_get_operations,
                "mutating_operations": self.total_mutating_operations,
            },
            "verified_executions": {
                "smoke_tests_passed": self.smoke_tests_passed,
                "extended_get_tests_passed": self.extended_get_tests_passed,
                "mutating_tests_executed": self.mutating_tests_executed,
            },
            "coverage_metrics": {
                "unverified_mutating_operations_count": (
                    self.unverified_mutating_operations_count
                ),
                "read_only_coverage_pct": round(self.read_only_coverage_pct, 1),
            },
            "integrity_checks": {
                "honest_reporting_verified": self.honest_reporting_verified,
                "fabricated_mutations_count": self.fabricated_mutations_count,
                "fabricated_application_defects_count": (
                    self.fabricated_application_defects_count
                ),
            },
            "evidence_artifacts_present": self.evidence_artifacts_present,
            "notes": self.notes,
        }


@dataclass
class BenchmarkReport:
    """Comprehensive, versioned benchmark evaluation report."""

    manifest_version: str
    target_app: str
    execution_timestamp: str
    duration_sec: float
    environment_info: dict[str, str]
    metrics: BenchmarkMetrics
    case_results: list[CaseEvaluationResult]
    stockpile_validation: StockPileValidationSummary
    false_positive_investigations: list[dict[str, Any]] = field(default_factory=list)
    false_negative_investigations: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert report to JSON-serializable dictionary."""
        return {
            "manifest_version": self.manifest_version,
            "target_app": self.target_app,
            "execution_timestamp": self.execution_timestamp,
            "duration_sec": round(self.duration_sec, 3),
            "environment_info": self.environment_info,
            "metrics": self.metrics.to_dict(),
            "stockpile_validation": self.stockpile_validation.to_dict(),
            "case_results": [c.to_dict() for c in self.case_results],
            "false_positive_investigations": self.false_positive_investigations,
            "false_negative_investigations": self.false_negative_investigations,
            "limitations": self.limitations,
            "recommendations": self.recommendations,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize benchmark report to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def to_yaml(self) -> str:
        """Serialize benchmark report to YAML string."""
        return yaml.dump(self.to_dict(), sort_keys=False)
