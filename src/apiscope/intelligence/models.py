"""Domain models, evidence provenance, and report structures for Project Intelligence.

Defines evidence source types, verification statuses, discovered endpoint/entity/module
models, risk areas, and the versioned ProjectIntelligenceReport with ProjectProfile
export compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import yaml

from apiscope.generators.project_profile import (
    BusinessInvariantRule,
    ProjectProfile,
    RoleRule,
    StateTransitionRule,
)


class EvidenceSourceType(StrEnum):
    """Origin of a discovered fact or claim."""

    OPENAPI = "OPENAPI"
    SOURCE_CODE = "SOURCE_CODE"
    DOCUMENTATION = "DOCUMENTATION"
    USER_INPUT = "USER_INPUT"
    LLM = "LLM"


class VerificationStatus(StrEnum):
    """Epistemic certainty status of a discovered fact."""

    CONFIRMED = "CONFIRMED"  # Direct primary evidence supports the claim
    INFERRED = "INFERRED"  # Deterministic heuristic derived from evidence
    PROPOSED = "PROPOSED"  # Suggested by AI or heuristic; requires human review
    UNKNOWN = "UNKNOWN"  # Insufficient information available
    CONFLICTING = "CONFLICTING"  # Observed evidence from sources disagrees


@dataclass(frozen=True)
class EvidenceReference:
    """Provenance pointer tying a discovered fact to concrete source evidence."""

    source_type: EvidenceSourceType
    source_ref: str  # e.g. "openapi:paths./products.get", "ProductController.java:45"
    snippet: str = ""  # Sanitized, minimal snippet of evidence
    confidence: float = 1.0  # 0.0 to 1.0 confidence score
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert evidence reference to serializable dictionary."""
        return {
            "source_type": self.source_type.value,
            "source_ref": self.source_ref,
            "snippet": self.snippet,
            "confidence": round(self.confidence, 2),
            "explanation": self.explanation,
        }


@dataclass(frozen=True)
class ProjectIdentity:
    """Identity and high-level platform metadata of the inspected project."""

    name: str
    version: str
    description: str = ""
    servers: list[str] = field(default_factory=list)
    source_language: str = "unknown"  # e.g. "java/spring-boot", "python/fastapi"
    evidence: list[EvidenceReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert project identity to serializable dictionary."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "servers": list(self.servers),
            "source_language": self.source_language,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass(frozen=True)
class DiscoveredEndpoint:
    """Detailed structural specification of a discovered API endpoint operation."""

    path: str
    method: str
    operation_id: str = ""
    summary: str = ""
    declared_tags: list[str] = field(default_factory=list)
    inferred_module: str = "default"
    module_status: VerificationStatus = VerificationStatus.INFERRED
    requires_auth: bool = False
    auth_schemes: list[str] = field(default_factory=list)
    parameters: list[dict[str, Any]] = field(default_factory=list)
    request_schema_ref: str | None = None
    response_schema_refs: dict[str, str] = field(default_factory=dict)
    evidence: list[EvidenceReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert discovered endpoint to serializable dictionary."""
        return {
            "path": self.path,
            "method": self.method,
            "operation_id": self.operation_id,
            "summary": self.summary,
            "declared_tags": list(self.declared_tags),
            "inferred_module": self.inferred_module,
            "module_status": self.module_status.value,
            "requires_auth": self.requires_auth,
            "auth_schemes": list(self.auth_schemes),
            "parameters": list(self.parameters),
            "request_schema_ref": self.request_schema_ref,
            "response_schema_refs": dict(self.response_schema_refs),
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass(frozen=True)
class DiscoveredModule:
    """Cohesive functional module grouping related endpoints and workflows."""

    name: str
    endpoints_count: int
    endpoints: list[str]  # e.g. ["GET /api/products", "POST /api/products"]
    status: VerificationStatus
    source_controller: str | None = None
    evidence: list[EvidenceReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert discovered module to serializable dictionary."""
        res: dict[str, Any] = {
            "name": self.name,
            "endpoints_count": self.endpoints_count,
            "endpoints": list(self.endpoints),
            "status": self.status.value,
            "evidence": [e.to_dict() for e in self.evidence],
        }
        if self.source_controller is not None:
            res["source_controller"] = self.source_controller
        return res


@dataclass(frozen=True)
class DiscoveredEntity:
    """Domain entity identified through schemas, path parameters, or source models."""

    name: str
    schema_name: str | None = None
    properties: list[str] = field(default_factory=list)
    primary_key: str | None = None
    managing_endpoints: list[str] = field(default_factory=list)
    status: VerificationStatus = VerificationStatus.INFERRED
    evidence: list[EvidenceReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert discovered entity to serializable dictionary."""
        return {
            "name": self.name,
            "schema_name": self.schema_name,
            "properties": list(self.properties),
            "primary_key": self.primary_key,
            "managing_endpoints": list(self.managing_endpoints),
            "status": self.status.value,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass(frozen=True)
class DiscoveredRelationship:
    """Inferred or declared relationship connecting two domain entities."""

    source_entity: str
    target_entity: str
    relationship_type: str  # e.g. "references", "contains", "sub_resource_of"
    foreign_key_field: str | None = None
    status: VerificationStatus = VerificationStatus.INFERRED
    evidence: list[EvidenceReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert discovered relationship to serializable dictionary."""
        return {
            "source_entity": self.source_entity,
            "target_entity": self.target_entity,
            "relationship_type": self.relationship_type,
            "foreign_key_field": self.foreign_key_field,
            "status": self.status.value,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass(frozen=True)
class DiscoveredAuthModel:
    """Discovered security, role, and token configuration of the target API."""

    auth_type: str  # e.g. "bearer_jwt", "api_key", "basic", "none"
    roles: list[str] = field(default_factory=list)
    role_rules: list[dict[str, Any]] = field(default_factory=list)
    status: VerificationStatus = VerificationStatus.INFERRED
    evidence: list[EvidenceReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert authentication model to serializable dictionary."""
        return {
            "auth_type": self.auth_type,
            "roles": list(self.roles),
            "role_rules": list(self.role_rules),
            "status": self.status.value,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass(frozen=True)
class DiscoveredWorkflow:
    """Multi-step workflow connecting producer and consumer endpoints."""

    workflow_id: str
    name: str
    description: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    status: VerificationStatus = VerificationStatus.INFERRED
    evidence: list[EvidenceReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert discovered workflow to serializable dictionary."""
        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "description": self.description,
            "steps": list(self.steps),
            "status": self.status.value,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass(frozen=True)
class DiscoveredBusinessRule:
    """Discovered domain invariant, state-transition rule, or validation constraint."""

    rule_id: str
    module: str
    description: str
    endpoint: str
    rule_type: str  # "state_transition", "invariant", "role_boundary"
    details: dict[str, Any] = field(default_factory=dict)
    status: VerificationStatus = VerificationStatus.INFERRED
    evidence: list[EvidenceReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert discovered business rule to serializable dictionary."""
        return {
            "rule_id": self.rule_id,
            "module": self.module,
            "description": self.description,
            "endpoint": self.endpoint,
            "rule_type": self.rule_type,
            "details": dict(self.details),
            "status": self.status.value,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass(frozen=True)
class TestDataRequirement:
    """Input parameter or payload dependency required to execute valid tests."""

    field_name: str
    entity: str
    data_type: str
    example_value: Any
    constraints: dict[str, Any] = field(default_factory=dict)
    evidence: list[EvidenceReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert test data requirement to serializable dictionary."""
        return {
            "field_name": self.field_name,
            "entity": self.entity,
            "data_type": self.data_type,
            "example_value": self.example_value,
            "constraints": dict(self.constraints),
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass(frozen=True)
class RiskArea:
    """Identified high-risk surface (unauthenticated mutation, IDOR, schema leakage)."""

    area_id: str
    category: str
    description: str
    affected_endpoints: list[str]
    severity: str  # "HIGH", "MEDIUM", "LOW"
    evidence: list[EvidenceReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert risk area to serializable dictionary."""
        return {
            "area_id": self.area_id,
            "category": self.category,
            "description": self.description,
            "affected_endpoints": list(self.affected_endpoints),
            "severity": self.severity,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass(frozen=True)
class MissingInformationItem:
    """Documented gap, ambiguity, or missing specification in target project."""

    category: str
    description: str
    affected_endpoints: list[str] = field(default_factory=list)
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert missing information item to serializable dictionary."""
        return {
            "category": self.category,
            "description": self.description,
            "affected_endpoints": list(self.affected_endpoints),
            "recommendation": self.recommendation,
        }


@dataclass
class ProjectIntelligenceReport:
    """Versioned project intelligence report capturing target project context."""

    schema_version: str = "1.0.0"
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    project: ProjectIdentity = field(
        default_factory=lambda: ProjectIdentity(name="Unnamed", version="0.1.0")
    )
    endpoints: list[DiscoveredEndpoint] = field(default_factory=list)
    modules: list[DiscoveredModule] = field(default_factory=list)
    entities: list[DiscoveredEntity] = field(default_factory=list)
    relationships: list[DiscoveredRelationship] = field(default_factory=list)
    auth: DiscoveredAuthModel = field(
        default_factory=lambda: DiscoveredAuthModel(auth_type="none")
    )
    workflows: list[DiscoveredWorkflow] = field(default_factory=list)
    business_rules: list[DiscoveredBusinessRule] = field(default_factory=list)
    test_data_requirements: list[TestDataRequirement] = field(default_factory=list)
    risk_areas: list[RiskArea] = field(default_factory=list)
    missing_information: list[MissingInformationItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert full intelligence report to a serializable dictionary."""
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "project": self.project.to_dict(),
            "endpoints": [ep.to_dict() for ep in self.endpoints],
            "modules": [m.to_dict() for m in self.modules],
            "entities": [e.to_dict() for e in self.entities],
            "relationships": [r.to_dict() for r in self.relationships],
            "auth": self.auth.to_dict(),
            "workflows": [w.to_dict() for w in self.workflows],
            "business_rules": [b.to_dict() for b in self.business_rules],
            "test_data_requirements": [
                t.to_dict() for t in self.test_data_requirements
            ],
            "risk_areas": [r.to_dict() for r in self.risk_areas],
            "missing_information": [m.to_dict() for m in self.missing_information],
            "warnings": list(self.warnings),
        }

    def to_yaml(self) -> str:
        """Serialize intelligence report to formatted YAML string."""
        return yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectIntelligenceReport:
        """Reconstruct ProjectIntelligenceReport from dictionary representation."""
        p_data = data.get("project", {})
        project = ProjectIdentity(
            name=p_data.get("name", "Unnamed"),
            version=p_data.get("version", "0.1.0"),
            description=p_data.get("description", ""),
            servers=list(p_data.get("servers", [])),
            source_language=p_data.get("source_language", "unknown"),
        )
        endpoints = [
            DiscoveredEndpoint(
                path=ep["path"],
                method=ep["method"],
                operation_id=ep.get("operation_id", ""),
                summary=ep.get("summary", ""),
                declared_tags=list(ep.get("declared_tags", [])),
                inferred_module=ep.get("inferred_module", "default"),
                module_status=VerificationStatus(
                    ep.get("module_status", VerificationStatus.INFERRED.value)
                ),
                requires_auth=bool(ep.get("requires_auth", False)),
                auth_schemes=list(ep.get("auth_schemes", [])),
                parameters=list(ep.get("parameters", [])),
                request_schema_ref=ep.get("request_schema_ref"),
                response_schema_refs=dict(ep.get("response_schema_refs", {})),
            )
            for ep in data.get("endpoints", [])
            if isinstance(ep, dict) and "path" in ep and "method" in ep
        ]
        modules = [
            DiscoveredModule(
                name=m["name"],
                endpoints_count=int(
                    m.get("endpoints_count", len(m.get("endpoints", [])))
                ),
                endpoints=list(m.get("endpoints", [])),
                status=VerificationStatus(
                    m.get("status", VerificationStatus.INFERRED.value)
                ),
                source_controller=m.get("source_controller"),
            )
            for m in data.get("modules", [])
            if isinstance(m, dict) and "name" in m
        ]
        entities = [
            DiscoveredEntity(
                name=e["name"],
                schema_name=e.get("schema_name"),
                properties=list(e.get("properties", [])),
                primary_key=e.get("primary_key"),
                managing_endpoints=list(e.get("managing_endpoints", [])),
                status=VerificationStatus(
                    e.get("status", VerificationStatus.INFERRED.value)
                ),
            )
            for e in data.get("entities", [])
            if isinstance(e, dict) and "name" in e
        ]
        a_data = data.get("auth", {})
        auth = DiscoveredAuthModel(
            auth_type=a_data.get("auth_type", "none"),
            roles=list(a_data.get("roles", [])),
            role_rules=list(a_data.get("role_rules", [])),
            status=VerificationStatus(
                a_data.get("status", VerificationStatus.INFERRED.value)
            ),
        )
        return cls(
            schema_version=data.get("schema_version", "1.0.0"),
            generated_at=data.get("generated_at", ""),
            project=project,
            endpoints=endpoints,
            modules=modules,
            entities=entities,
            auth=auth,
            warnings=list(data.get("warnings", [])),
        )

    def to_project_profile(self) -> ProjectProfile:
        """Convert intelligence report into a standard ProjectProfile.

        Enables direct interoperability with AiGeneratorService.
        """
        # Map discovered roles
        roles: list[RoleRule] = []
        for role_name in self.auth.roles:
            allowed: list[str] = []
            forbidden: list[str] = []
            for rr in self.auth.role_rules:
                if rr.get("role_name", "").lower() == role_name.lower():
                    allowed.extend(rr.get("allowed_endpoints", []))
                    forbidden.extend(rr.get("forbidden_endpoints", []))
            roles.append(
                RoleRule(
                    role_name=role_name,
                    token_variable=f"{{{role_name.lower()}_token}}",
                    allowed_endpoints=sorted(set(allowed)),
                    forbidden_endpoints=sorted(set(forbidden)),
                )
            )

        # Map state transitions
        transitions: list[StateTransitionRule] = []
        for rule in self.business_rules:
            if rule.rule_type == "state_transition":
                transitions.append(
                    StateTransitionRule(
                        entity=rule.details.get("entity", rule.module),
                        description=rule.description,
                        endpoint=rule.endpoint,
                        invalid_state_id=rule.details.get("invalid_state_id", 0),
                        invalid_state_name=rule.details.get(
                            "invalid_state_name", "INVALID_STATE"
                        ),
                        expected_status_range=rule.details.get(
                            "expected_status_range", [400, 409]
                        ),
                    )
                )

        # Map business invariants
        invariants: list[BusinessInvariantRule] = []
        for rule in self.business_rules:
            if rule.rule_type in (
                "invariant",
                "single_use",
                "idempotency",
                "role_boundary",
            ):
                invariants.append(
                    BusinessInvariantRule(
                        rule_id=rule.rule_id,
                        module=rule.module,
                        description=rule.description,
                        endpoint=rule.endpoint,
                        rule_type=rule.details.get("rule_type", "single_use"),
                        payload=rule.details.get("payload", {}),
                        first_expected_status=rule.details.get(
                            "first_expected_status", 200
                        ),
                        repeat_expected_status_range=rule.details.get(
                            "repeat_expected_status_range", [400, 409]
                        ),
                    )
                )

        sensitive_fields = ["password", "token", "secret", "auth_hash"]
        notes = [
            f"Module '{m.name}': {m.endpoints_count} endpoints ({m.status.value})"
            for m in self.modules
        ]
        notes.extend(self.warnings)

        return ProjectProfile(
            project_name=self.project.name,
            domain=self.project.name.lower().replace(" ", "_"),
            roles=roles,
            state_transitions=transitions,
            business_invariants=invariants,
            sensitive_fields_denylist=sensitive_fields,
            notes=notes,
        )
