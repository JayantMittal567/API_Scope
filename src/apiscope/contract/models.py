"""Domain models and canonical representations for API contract quality.

Defines canonical operation contracts, parameters, request/response structures,
contract quality findings with evidence provenance, test-generation readiness,
and comprehensive contract quality reports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import yaml

from apiscope.intelligence.models import (
    EvidenceReference,
    VerificationStatus,
)


class OperationReadiness(StrEnum):
    """Test-generation readiness state for an operation or contract."""

    READY = "READY"  # Sufficient evidence for deterministic test synthesis
    PARTIAL = "PARTIAL"  # Minor omissions exist, but basic tests can be synthesized
    BLOCKED = "BLOCKED"  # Critical defects or contradictions prevent valid testing


class FindingSeverity(StrEnum):
    """Impact severity of a contract quality finding."""

    HIGH = "HIGH"  # Critical defect or security mismatch
    MEDIUM = "MEDIUM"  # Significant specification omission or ambiguity
    LOW = "LOW"  # Minor documentation inconsistency or deprecation
    INFO = "INFO"  # Informational observation (e.g. optional metadata omitted)


class QualityCategory(StrEnum):
    """Taxonomy of contract quality findings."""

    SCHEMA_DEFECT = "SCHEMA_DEFECT"  # Contradictory constraints, unresolvable $ref
    SPEC_OMISSION = "SPEC_OMISSION"  # Missing 2xx responses, path param mismatches
    SECURITY_GAP = "SECURITY_GAP"  # Undocumented auth, insecure mutations
    DOCUMENTATION_GAP = "DOCUMENTATION_GAP"  # Missing response bodies, generic tags
    SOURCE_SPEC_DRIFT = "SOURCE_SPEC_DRIFT"  # Source route not in spec or vice versa
    UNCERTAINTY = "UNCERTAINTY"  # Ambiguous types, unverifiable requirements


@dataclass(frozen=True)
class ContractFinding:
    """Actionable contract defect, omission, or drift finding with evidence."""

    finding_id: str
    category: QualityCategory
    severity: FindingSeverity
    description: str
    target_operation: str = ""  # e.g. "GET /api/products/{id}" or "schemas.Product"
    recommendation: str = ""
    status: VerificationStatus = VerificationStatus.CONFIRMED
    evidence: list[EvidenceReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert finding to a serializable dictionary."""
        return {
            "finding_id": self.finding_id,
            "category": self.category.value,
            "severity": self.severity.value,
            "description": self.description,
            "target_operation": self.target_operation,
            "recommendation": self.recommendation,
            "status": self.status.value,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass(frozen=True)
class CanonicalParameter:
    """Normalized specification for a path, query, header, or cookie parameter."""

    name: str
    location: str  # "path", "query", "header", "cookie"
    required: bool = False
    schema: dict[str, Any] = field(default_factory=dict)
    schema_ref: str | None = None
    description: str = ""
    deprecated: bool = False
    constraints: dict[str, Any] = field(default_factory=dict)
    evidence: list[EvidenceReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert parameter to serializable dictionary."""
        return {
            "name": self.name,
            "location": self.location,
            "required": self.required,
            "schema": dict(self.schema),
            "schema_ref": self.schema_ref,
            "description": self.description,
            "deprecated": self.deprecated,
            "constraints": dict(self.constraints),
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass(frozen=True)
class CanonicalRequestBody:
    """Normalized request body with MIME content types and schema definitions."""

    content_types: list[str] = field(default_factory=list)
    required: bool = False
    schema: dict[str, Any] = field(default_factory=dict)
    schema_ref: str | None = None
    description: str = ""
    evidence: list[EvidenceReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert request body specification to serializable dictionary."""
        return {
            "content_types": list(self.content_types),
            "required": self.required,
            "schema": dict(self.schema),
            "schema_ref": self.schema_ref,
            "description": self.description,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass(frozen=True)
class CanonicalResponse:
    """Normalized HTTP response definition for a status code."""

    status_code: str  # e.g. "200", "201", "204", "400", "default"
    description: str = ""
    content_types: list[str] = field(default_factory=list)
    schema: dict[str, Any] = field(default_factory=dict)
    schema_ref: str | None = None
    is_array: bool = False
    item_schema_ref: str | None = None
    is_empty_body: bool = False
    evidence: list[EvidenceReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert response definition to serializable dictionary."""
        return {
            "status_code": self.status_code,
            "description": self.description,
            "content_types": list(self.content_types),
            "schema": dict(self.schema),
            "schema_ref": self.schema_ref,
            "is_array": self.is_array,
            "item_schema_ref": self.item_schema_ref,
            "is_empty_body": self.is_empty_body,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass
class CanonicalOperation:
    """Canonical contract representation for a single HTTP operation on a path."""

    method: str  # Uppercase: "GET", "POST", "PUT", "DELETE", etc.
    path: str  # Path template, e.g. "/api/products/{id}"
    operation_id: str = ""
    summary: str = ""
    description: str = ""
    declared_tags: list[str] = field(default_factory=list)
    module: str = "default"
    module_status: VerificationStatus = VerificationStatus.INFERRED
    deprecated: bool = False
    requires_auth: bool = False
    security_schemes: list[dict[str, Any]] = field(default_factory=list)
    parameters: list[CanonicalParameter] = field(default_factory=list)
    request_body: CanonicalRequestBody | None = None
    responses: dict[str, CanonicalResponse] = field(default_factory=dict)
    readiness: OperationReadiness = OperationReadiness.READY
    readiness_reasons: list[str] = field(default_factory=list)
    findings: list[ContractFinding] = field(default_factory=list)
    evidence: list[EvidenceReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert canonical operation to serializable dictionary."""
        return {
            "method": self.method,
            "path": self.path,
            "operation_id": self.operation_id,
            "summary": self.summary,
            "description": self.description,
            "declared_tags": list(self.declared_tags),
            "module": self.module,
            "module_status": self.module_status.value,
            "deprecated": self.deprecated,
            "requires_auth": self.requires_auth,
            "security_schemes": list(self.security_schemes),
            "parameters": [p.to_dict() for p in self.parameters],
            "request_body": (
                self.request_body.to_dict() if self.request_body else None
            ),
            "responses": {k: v.to_dict() for k, v in self.responses.items()},
            "readiness": self.readiness.value,
            "readiness_reasons": list(self.readiness_reasons),
            "findings": [f.to_dict() for f in self.findings],
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass
class CanonicalContract:
    """Comprehensive canonical contract for target API operations and schemas."""

    title: str
    version: str
    openapi_version: str
    servers: list[str] = field(default_factory=list)
    operations: list[CanonicalOperation] = field(default_factory=list)
    modules: dict[str, list[str]] = field(default_factory=dict)
    schemas: dict[str, Any] = field(default_factory=dict)
    security_schemes: dict[str, Any] = field(default_factory=dict)
    global_security: list[dict[str, Any]] = field(default_factory=list)
    findings: list[ContractFinding] = field(default_factory=list)
    evidence: list[EvidenceReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert canonical contract to serializable dictionary."""
        return {
            "title": self.title,
            "version": self.version,
            "openapi_version": self.openapi_version,
            "servers": list(self.servers),
            "operations": [op.to_dict() for op in self.operations],
            "modules": {k: list(v) for k, v in self.modules.items()},
            "schemas": dict(self.schemas),
            "security_schemes": dict(self.security_schemes),
            "global_security": list(self.global_security),
            "findings": [f.to_dict() for f in self.findings],
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass
class ContractQualityReport:
    """Versioned quality report containing contract analysis and readiness."""

    schema_version: str = "1.0.0"
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    title: str = "API Contract"
    version: str = "1.0.0"
    total_operations: int = 0
    operations_by_method: dict[str, int] = field(default_factory=dict)
    modules_count: int = 0
    component_schemas_count: int = 0
    readiness_summary: dict[str, int] = field(default_factory=dict)
    overall_readiness: OperationReadiness = OperationReadiness.READY
    findings_summary: dict[str, int] = field(default_factory=dict)
    findings: list[ContractFinding] = field(default_factory=list)
    operations: list[CanonicalOperation] = field(default_factory=list)
    source_reconciliation: dict[str, Any] = field(default_factory=dict)
    schema_reconciliation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert contract quality report to serializable dictionary."""
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "title": self.title,
            "version": self.version,
            "total_operations": self.total_operations,
            "operations_by_method": dict(self.operations_by_method),
            "modules_count": self.modules_count,
            "component_schemas_count": self.component_schemas_count,
            "readiness_summary": dict(self.readiness_summary),
            "overall_readiness": self.overall_readiness.value,
            "findings_summary": dict(self.findings_summary),
            "findings": [f.to_dict() for f in self.findings],
            "operations": [op.to_dict() for op in self.operations],
            "source_reconciliation": dict(self.source_reconciliation),
            "schema_reconciliation": dict(self.schema_reconciliation),
        }

    def to_yaml(self) -> str:
        """Serialize report to YAML string."""
        return yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True)

    def to_json(self) -> str:
        """Serialize report to formatted JSON string."""
        return json.dumps(self.to_dict(), indent=2)
