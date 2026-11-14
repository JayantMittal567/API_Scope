"""Domain models for ApiScope Test Planning Engine (Task 18).

Defines structured, versioned test plans combining deterministic baseline scenarios
and optional budget-aware Gemini reasoning proposals with human governance,
explicit oracle provenance, and prerequisite tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from apiscope.definitions.models import DefinitionStatus
from apiscope.intelligence.models import (
    EvidenceReference,
    EvidenceSourceType,
    VerificationStatus,
)


class ScenarioCategory(StrEnum):
    """Categorization of test scenario objectives."""

    CONTRACT_POSITIVE = "CONTRACT_POSITIVE"
    INPUT_VALIDATION = "INPUT_VALIDATION"
    BOUNDARY = "BOUNDARY"
    RESPONSE_SCHEMA = "RESPONSE_SCHEMA"
    AUTH_SECURITY = "AUTH_SECURITY"
    RESOURCE_EXISTENCE = "RESOURCE_EXISTENCE"
    ERROR_HANDLING = "ERROR_HANDLING"
    WORKFLOW_STATE_TRANSITION = "WORKFLOW_STATE_TRANSITION"
    DOMAIN_INVARIANT = "DOMAIN_INVARIANT"
    CONSISTENCY_CHECK = "CONSISTENCY_CHECK"


class ScenarioPriority(StrEnum):
    """Prioritization level for test scenario execution."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ScenarioOrigin(StrEnum):
    """Origin generator of the test scenario."""

    DETERMINISTIC = "DETERMINISTIC"
    AI_PROPOSED = "AI_PROPOSED"


class OracleType(StrEnum):
    """Source of truth backing expected scenario outcome."""

    EXPLICIT_CONTRACT = "EXPLICIT_CONTRACT"
    INDEPENDENT_REQUIREMENT = "INDEPENDENT_REQUIREMENT"
    SOURCE_CODE_INFERENCE = "SOURCE_CODE_INFERENCE"
    PROPERTY_CONSISTENCY = "PROPERTY_CONSISTENCY"
    AI_HYPOTHESIS = "AI_HYPOTHESIS"


class RiskClassification(StrEnum):
    """Risk classification associated with scenario failure or operation."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class ScenarioStep:
    """Individual HTTP interaction step within a test scenario."""

    step_number: int
    method: str
    path: str
    description: str
    request_template: dict[str, Any] = field(default_factory=dict)
    expected_status: list[str] = field(default_factory=lambda: ["200"])
    oracle_type: OracleType = OracleType.EXPLICIT_CONTRACT
    expected_outcome: str = ""
    oracle_evidence: EvidenceReference | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert step to dictionary."""
        return {
            "step_number": self.step_number,
            "method": self.method,
            "path": self.path,
            "description": self.description,
            "request_template": self.request_template,
            "expected_status": self.expected_status,
            "oracle_type": self.oracle_type.value,
            "expected_outcome": self.expected_outcome,
            "oracle_evidence": (
                {
                    "source_type": self.oracle_evidence.source_type.value,
                    "source_ref": self.oracle_evidence.source_ref,
                    "snippet": self.oracle_evidence.snippet,
                    "confidence": self.oracle_evidence.confidence,
                    "explanation": self.oracle_evidence.explanation,
                }
                if self.oracle_evidence is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScenarioStep:
        """Construct step from dictionary."""
        ev_data = data.get("oracle_evidence")
        ev_obj = None
        if ev_data and isinstance(ev_data, dict):
            ev_obj = EvidenceReference(
                source_type=EvidenceSourceType(ev_data.get("source_type", "OPENAPI")),
                source_ref=ev_data.get("source_ref", ""),
                snippet=ev_data.get("snippet", ""),
                confidence=float(ev_data.get("confidence", 1.0)),
                explanation=ev_data.get("explanation", ""),
            )

        raw_ot = data.get("oracle_type", "EXPLICIT_CONTRACT")
        try:
            ot = OracleType(raw_ot)
        except ValueError:
            ot = OracleType.EXPLICIT_CONTRACT

        return cls(
            step_number=int(data.get("step_number", 1)),
            method=str(data.get("method", "GET")),
            path=str(data.get("path", "/")),
            description=str(data.get("description", "")),
            request_template=dict(data.get("request_template", {})),
            expected_status=list(data.get("expected_status", ["200"])),
            oracle_type=ot,
            expected_outcome=str(data.get("expected_outcome", "")),
            oracle_evidence=ev_obj,
        )


@dataclass
class TestScenario:
    """Comprehensive, evidence-backed test scenario proposal."""

    __test__ = False

    scenario_id: str
    title: str
    description: str
    category: ScenarioCategory
    priority: ScenarioPriority = ScenarioPriority.MEDIUM
    origin: ScenarioOrigin = ScenarioOrigin.DETERMINISTIC
    target_module: str = "default"
    target_endpoints: list[str] = field(default_factory=list)
    related_rule: str | None = None
    preconditions: list[str] = field(default_factory=list)
    test_data_requirements: list[str] = field(default_factory=list)
    steps: list[ScenarioStep] = field(default_factory=list)
    risk_classification: RiskClassification = RiskClassification.LOW
    confidence: VerificationStatus = VerificationStatus.CONFIRMED
    missing_dependencies: list[str] = field(default_factory=list)
    approval_status: DefinitionStatus = DefinitionStatus.APPROVED
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert test scenario to dictionary."""
        return {
            "scenario_id": self.scenario_id,
            "title": self.title,
            "description": self.description,
            "category": self.category.value,
            "priority": self.priority.value,
            "origin": self.origin.value,
            "target_module": self.target_module,
            "target_endpoints": self.target_endpoints,
            "related_rule": self.related_rule,
            "preconditions": self.preconditions,
            "test_data_requirements": self.test_data_requirements,
            "steps": [s.to_dict() for s in self.steps],
            "risk_classification": self.risk_classification.value,
            "confidence": self.confidence.value,
            "missing_dependencies": self.missing_dependencies,
            "approval_status": self.approval_status.value,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestScenario:
        """Construct test scenario from dictionary."""
        raw_cat = data.get("category", "CONTRACT_POSITIVE")
        try:
            cat = ScenarioCategory(raw_cat)
        except ValueError:
            cat = ScenarioCategory.CONTRACT_POSITIVE

        raw_prio = data.get("priority", "MEDIUM")
        try:
            prio = ScenarioPriority(raw_prio)
        except ValueError:
            prio = ScenarioPriority.MEDIUM

        raw_orig = data.get("origin", "DETERMINISTIC")
        try:
            orig = ScenarioOrigin(raw_orig)
        except ValueError:
            orig = ScenarioOrigin.DETERMINISTIC

        raw_risk = data.get("risk_classification", "LOW")
        try:
            risk = RiskClassification(raw_risk)
        except ValueError:
            risk = RiskClassification.LOW

        raw_conf = data.get("confidence", "CONFIRMED")
        try:
            conf = VerificationStatus(raw_conf)
        except ValueError:
            conf = VerificationStatus.CONFIRMED

        raw_appr = data.get("approval_status", "approved")
        try:
            appr = DefinitionStatus(raw_appr)
        except ValueError:
            appr = DefinitionStatus.PROPOSED

        steps = [
            ScenarioStep.from_dict(s)
            for s in data.get("steps", [])
            if isinstance(s, dict)
        ]

        return cls(
            scenario_id=str(data.get("scenario_id", "")),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            category=cat,
            priority=prio,
            origin=orig,
            target_module=str(data.get("target_module", "default")),
            target_endpoints=list(data.get("target_endpoints", [])),
            related_rule=data.get("related_rule"),
            preconditions=list(data.get("preconditions", [])),
            test_data_requirements=list(data.get("test_data_requirements", [])),
            steps=steps,
            risk_classification=risk,
            confidence=conf,
            missing_dependencies=list(data.get("missing_dependencies", [])),
            approval_status=appr,
            rationale=str(data.get("rationale", "")),
        )


@dataclass
class AIBudgetConfig:
    """Configurable budget guardrails for free-tier Gemini reasoning calls."""

    max_calls: int = 3
    max_input_tokens_per_call: int = 2500
    max_output_tokens_per_call: int = 2000
    max_total_estimated_tokens: int = 8000
    request_timeout_sec: float = 15.0
    max_retries: int = 1


@dataclass
class AIUsageRecord:
    """Record of AI calls, estimated or actual token usage, and completion status."""

    status: str = "SKIPPED"
    calls_made: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    is_estimated: bool = True
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert AI usage record to dictionary."""
        return {
            "status": self.status,
            "calls_made": self.calls_made,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "is_estimated": self.is_estimated,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AIUsageRecord:
        """Construct AI usage record from dictionary."""
        return cls(
            status=str(data.get("status", "SKIPPED")),
            calls_made=int(data.get("calls_made", 0)),
            prompt_tokens=int(data.get("prompt_tokens", 0)),
            completion_tokens=int(data.get("completion_tokens", 0)),
            total_tokens=int(data.get("total_tokens", 0)),
            is_estimated=bool(data.get("is_estimated", True)),
            details=str(data.get("details", "")),
        )


@dataclass
class TestPlan:
    """Versioned test plan specifying comprehensive testing proposals."""

    __test__ = False

    plan_id: str
    title: str
    version: str
    schema_version: str = "1.0.0"
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    contract_ref: str = ""
    intelligence_ref: str = ""
    summary: dict[str, Any] = field(default_factory=dict)
    scenarios: list[TestScenario] = field(default_factory=list)
    missing_prerequisites: list[str] = field(default_factory=list)
    untestable_operations: list[dict[str, Any]] = field(default_factory=list)
    ai_usage: AIUsageRecord = field(default_factory=AIUsageRecord)

    def to_dict(self) -> dict[str, Any]:
        """Convert test plan to dictionary."""
        return {
            "plan_id": self.plan_id,
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "title": self.title,
            "version": self.version,
            "contract_ref": self.contract_ref,
            "intelligence_ref": self.intelligence_ref,
            "summary": self.summary,
            "missing_prerequisites": self.missing_prerequisites,
            "untestable_operations": self.untestable_operations,
            "ai_usage": self.ai_usage.to_dict(),
            "scenarios": [s.to_dict() for s in self.scenarios],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestPlan:
        """Construct test plan from dictionary."""
        scenarios = [
            TestScenario.from_dict(s)
            for s in data.get("scenarios", [])
            if isinstance(s, dict)
        ]
        ai_u_data = data.get("ai_usage", {})
        ai_record = (
            AIUsageRecord.from_dict(ai_u_data)
            if isinstance(ai_u_data, dict)
            else AIUsageRecord()
        )

        return cls(
            plan_id=str(data.get("plan_id", "")),
            title=str(data.get("title", "")),
            version=str(data.get("version", "")),
            schema_version=str(data.get("schema_version", "1.0.0")),
            generated_at=str(data.get("generated_at", datetime.now(UTC).isoformat())),
            contract_ref=str(data.get("contract_ref", "")),
            intelligence_ref=str(data.get("intelligence_ref", "")),
            summary=dict(data.get("summary", {})),
            scenarios=scenarios,
            missing_prerequisites=list(data.get("missing_prerequisites", [])),
            untestable_operations=list(data.get("untestable_operations", [])),
            ai_usage=ai_record,
        )
