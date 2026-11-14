"""Contract Quality Analyzer evaluating specifications for defects and omissions.

Detects missing success responses, missing schemas, contradictory constraints,
unresolved references, path parameter mismatches, undocumented security,
and ambiguous tags with actionable severities and evidence.
"""

from __future__ import annotations

import re
from typing import Any

from apiscope.contract.models import (
    CanonicalContract,
    CanonicalOperation,
    ContractFinding,
    FindingSeverity,
    QualityCategory,
)
from apiscope.intelligence.models import (
    EvidenceReference,
    EvidenceSourceType,
    VerificationStatus,
)


def _fid(prefix: str, text: str) -> str:
    """Generate a compact deterministic finding ID."""
    return f"{prefix}_{abs(hash(text)) % 100000}"


def _check_schema_contradictions(
    schema: dict[str, Any],
    location_desc: str,
    target_op: str,
) -> list[ContractFinding]:
    """Scan a schema dictionary for contradictory or invalid constraints."""
    findings: list[ContractFinding] = []

    # Check unresolved references
    if "_unresolved_ref" in schema:
        ref_val = schema["_unresolved_ref"]
        findings.append(
            ContractFinding(
                finding_id=_fid("unresolved_ref", location_desc + ref_val),
                category=QualityCategory.SCHEMA_DEFECT,
                severity=FindingSeverity.HIGH,
                description=f"Unresolved reference '{ref_val}' in {location_desc}",
                target_operation=target_op,
                recommendation="Verify component definition exists in OpenAPI document",
                evidence=[
                    EvidenceReference(
                        source_type=EvidenceSourceType.OPENAPI,
                        source_ref=location_desc,
                        snippet=f"$ref: '{ref_val}'",
                        confidence=1.0,
                        explanation="Failed pointer resolution during normalization",
                    )
                ],
            )
        )

    # Minimum > Maximum
    min_v = schema.get("minimum")
    max_v = schema.get("maximum")
    if (
        isinstance(min_v, (int, float))
        and isinstance(max_v, (int, float))
        and min_v > max_v
    ):
        findings.append(
            ContractFinding(
                finding_id=_fid("contradictory_min_max", location_desc),
                category=QualityCategory.SCHEMA_DEFECT,
                severity=FindingSeverity.HIGH,
                description=(
                    f"Contradictory numeric constraints in {location_desc}: "
                    f"minimum ({min_v}) exceeds maximum ({max_v})"
                ),
                target_operation=target_op,
                recommendation="Adjust schema boundaries so minimum <= maximum",
                evidence=[
                    EvidenceReference(
                        source_type=EvidenceSourceType.OPENAPI,
                        source_ref=location_desc,
                        snippet=f"minimum: {min_v}, maximum: {max_v}",
                        confidence=1.0,
                        explanation="Invalid numeric boundary assertion",
                    )
                ],
            )
        )

    # minLength > maxLength
    min_l = schema.get("minLength")
    max_l = schema.get("maxLength")
    if isinstance(min_l, int) and isinstance(max_l, int) and min_l > max_l:
        findings.append(
            ContractFinding(
                finding_id=_fid("contradictory_len", location_desc),
                category=QualityCategory.SCHEMA_DEFECT,
                severity=FindingSeverity.HIGH,
                description=(
                    f"Contradictory string length constraints in {location_desc}: "
                    f"minLength ({min_l}) exceeds maxLength ({max_l})"
                ),
                target_operation=target_op,
                recommendation="Adjust string constraints so minLength <= maxLength",
                evidence=[
                    EvidenceReference(
                        source_type=EvidenceSourceType.OPENAPI,
                        source_ref=location_desc,
                        snippet=f"minLength: {min_l}, maxLength: {max_l}",
                        confidence=1.0,
                        explanation="Invalid length boundary assertion",
                    )
                ],
            )
        )

    # minItems > maxItems
    min_i = schema.get("minItems")
    max_i = schema.get("maxItems")
    if isinstance(min_i, int) and isinstance(max_i, int) and min_i > max_i:
        findings.append(
            ContractFinding(
                finding_id=_fid("contradictory_items", location_desc),
                category=QualityCategory.SCHEMA_DEFECT,
                severity=FindingSeverity.HIGH,
                description=(
                    f"Contradictory array item constraints in {location_desc}: "
                    f"minItems ({min_i}) exceeds maxItems ({max_i})"
                ),
                target_operation=target_op,
                recommendation="Adjust array constraints so minItems <= maxItems",
                evidence=[
                    EvidenceReference(
                        source_type=EvidenceSourceType.OPENAPI,
                        source_ref=location_desc,
                        snippet=f"minItems: {min_i}, maxItems: {max_i}",
                        confidence=1.0,
                        explanation="Invalid item count boundary assertion",
                    )
                ],
            )
        )

    # Check nested properties
    props = schema.get("properties")
    if isinstance(props, dict):
        for prop_name, prop_def in props.items():
            if isinstance(prop_def, dict):
                findings.extend(
                    _check_schema_contradictions(
                        prop_def,
                        f"{location_desc}.properties.{prop_name}",
                        target_op,
                    )
                )

    # Check array items
    items_schema = schema.get("items")
    if isinstance(items_schema, dict):
        findings.extend(
            _check_schema_contradictions(
                items_schema,
                f"{location_desc}.items",
                target_op,
            )
        )

    return findings


class ContractQualityAnalyzer:
    """Evaluates CanonicalContract for defects, omissions, and ambiguities."""

    def analyze(self, contract: CanonicalContract) -> list[ContractFinding]:
        """Analyze full contract and populate findings per operation and contract."""
        all_findings: list[ContractFinding] = []
        seen_op_ids: dict[str, str] = {}  # op_id -> "METHOD PATH"

        # Check global security declaration
        if not contract.security_schemes:
            sec_finding = ContractFinding(
                finding_id="contract_no_security_schemes",
                category=QualityCategory.SECURITY_GAP,
                severity=FindingSeverity.LOW,
                description=(
                    "OpenAPI document declares no securitySchemes. Authentication "
                    "expectations are undocumented in specification (backend may still "
                    "enforce authentication outside spec)."
                ),
                target_operation="components.securitySchemes",
                recommendation=(
                    "Document security schemes under components/securitySchemes"
                ),
                status=VerificationStatus.INFERRED,
                evidence=[
                    EvidenceReference(
                        source_type=EvidenceSourceType.OPENAPI,
                        source_ref="components.securitySchemes",
                        snippet="securitySchemes: none declared",
                        confidence=1.0,
                        explanation="Missing security schemes block in OpenAPI",
                    )
                ],
            )
            all_findings.append(sec_finding)
            contract.findings.append(sec_finding)

        # Analyze each operation
        for op in contract.operations:
            op_findings = self._analyze_operation(op, seen_op_ids)
            op.findings.extend(op_findings)
            contract.findings.extend(op_findings)
            all_findings.extend(op_findings)

        # Analyze component schemas
        for s_name, s_def in contract.schemas.items():
            if isinstance(s_def, dict):
                s_findings = self._analyze_component_schema(s_name, s_def)
                all_findings.extend(s_findings)
                contract.findings.extend(s_findings)

        return all_findings

    def _analyze_operation(
        self,
        op: CanonicalOperation,
        seen_op_ids: dict[str, str],
    ) -> list[ContractFinding]:
        """Analyze an individual endpoint operation for quality issues."""
        findings: list[ContractFinding] = []
        sig = f"{op.method} {op.path}"

        # 1. Operation ID checks
        if not op.operation_id:
            findings.append(
                ContractFinding(
                    finding_id=_fid("missing_op_id", sig),
                    category=QualityCategory.SPEC_OMISSION,
                    severity=FindingSeverity.LOW,
                    description=f"Operation '{sig}' lacks operationId identifier",
                    target_operation=sig,
                    recommendation="Provide a unique camelCase operationId",
                )
            )
        elif op.operation_id in seen_op_ids:
            findings.append(
                ContractFinding(
                    finding_id=_fid("duplicate_op_id", sig),
                    category=QualityCategory.SPEC_OMISSION,
                    severity=FindingSeverity.LOW,
                    description=(
                        f"Duplicate operationId '{op.operation_id}' on '{sig}'; "
                        f"previously defined on '{seen_op_ids[op.operation_id]}'"
                    ),
                    target_operation=sig,
                    recommendation="Assign distinct operationId values per operation",
                )
            )
        else:
            seen_op_ids[op.operation_id] = sig

        # 2. Path parameter consistency
        template_params = set(re.findall(r"\{([^}]+)\}", op.path))
        declared_path_params = {
            p.name: p for p in op.parameters if p.location == "path"
        }

        # Check template param not declared
        for t_param in template_params:
            if t_param not in declared_path_params:
                findings.append(
                    ContractFinding(
                        finding_id=_fid("missing_path_param", sig + t_param),
                        category=QualityCategory.SPEC_OMISSION,
                        severity=FindingSeverity.HIGH,
                        description=(
                            f"Path parameter '{{{t_param}}}' in URL template "
                            f"'{op.path}' is not declared in parameters list."
                        ),
                        target_operation=sig,
                        recommendation=(
                            f"Add parameter '{t_param}' with location 'path'"
                        ),
                        evidence=[
                            EvidenceReference(
                                source_type=EvidenceSourceType.OPENAPI,
                                source_ref=f"paths.{op.path}",
                                snippet=f"Path: {op.path}",
                                confidence=1.0,
                                explanation=(
                                    "Path template variable without parameter "
                                    "declaration"
                                ),
                            )
                        ],
                    )
                )

        # Check declared path param not in template
        for d_name in declared_path_params:
            if d_name not in template_params:
                findings.append(
                    ContractFinding(
                        finding_id=_fid("orphan_path_param", sig + d_name),
                        category=QualityCategory.SPEC_OMISSION,
                        severity=FindingSeverity.HIGH,
                        description=(
                            f"Declared path parameter '{d_name}' does not appear "
                            f"in URL template '{op.path}'."
                        ),
                        target_operation=sig,
                        recommendation=(
                            f"Remove parameter '{d_name}' or add '{{{d_name}}}' to path"
                        ),
                    )
                )

        # 3. Success response definition
        success_responses = [
            (code, resp)
            for code, resp in op.responses.items()
            if code.startswith("2") or code == "default"
        ]
        if not success_responses:
            findings.append(
                ContractFinding(
                    finding_id=_fid("no_2xx_response", sig),
                    category=QualityCategory.SPEC_OMISSION,
                    severity=FindingSeverity.HIGH,
                    description=(
                        f"Operation '{sig}' defines no 2xx success response status code"
                    ),
                    target_operation=sig,
                    recommendation=(
                        "Document expected 2xx response (e.g. 200, 201, 204)"
                    ),
                    evidence=[
                        EvidenceReference(
                            source_type=EvidenceSourceType.OPENAPI,
                            source_ref=(
                                f"paths.{op.path}.{op.method.lower()}.responses"
                            ),
                            snippet=f"Responses: {list(op.responses.keys())}",
                            confidence=1.0,
                            explanation="Absence of 2xx status code in responses map",
                        )
                    ],
                )
            )
        else:
            # Check 2xx schema presence (excluding 204/304 empty responses)
            for code, resp in success_responses:
                if (
                    code not in ("204", "304")
                    and not resp.is_empty_body
                    and not resp.schema
                    and not resp.schema_ref
                    and not resp.item_schema_ref
                ):
                    findings.append(
                        ContractFinding(
                            finding_id=_fid("missing_response_schema", sig + code),
                            category=QualityCategory.DOCUMENTATION_GAP,
                            severity=FindingSeverity.MEDIUM,
                            description=(
                                f"HTTP {code} response on '{sig}' lacks a "
                                f"documented response body schema definition."
                            ),
                            target_operation=sig,
                            recommendation=(
                                "Define response schema under "
                                "content['application/json'].schema"
                            ),
                            evidence=[
                                EvidenceReference(
                                    source_type=EvidenceSourceType.OPENAPI,
                                    source_ref=(
                                        f"paths.{op.path}.{op.method.lower()}"
                                        f".responses.{code}"
                                    ),
                                    snippet=f"Content types: {resp.content_types}",
                                    confidence=1.0,
                                    explanation=(
                                        "Response lacks body schema definition"
                                    ),
                                )
                            ],
                        )
                    )

        # 4. Request body quality
        if op.request_body:
            if not op.request_body.content_types:
                findings.append(
                    ContractFinding(
                        finding_id=_fid("missing_rb_content_type", sig),
                        category=QualityCategory.SPEC_OMISSION,
                        severity=FindingSeverity.MEDIUM,
                        description=(
                            f"Request body on '{sig}' declared without MIME "
                            f"content types"
                        ),
                        target_operation=sig,
                        recommendation="Specify content type (e.g. application/json)",
                    )
                )
            if (
                not op.request_body.schema
                and not op.request_body.schema_ref
                and op.request_body.content_types
            ):
                findings.append(
                    ContractFinding(
                        finding_id=_fid("missing_rb_schema", sig),
                        category=QualityCategory.SPEC_OMISSION,
                        severity=FindingSeverity.MEDIUM,
                        description=(
                            f"Request body on '{sig}' specifies content type "
                            f"but lacks schema"
                        ),
                        target_operation=sig,
                        recommendation="Define request body schema definition",
                    )
                )

        # 5. Security documentation gap
        if not op.requires_auth and op.method in ("POST", "PUT", "PATCH", "DELETE"):
            findings.append(
                ContractFinding(
                    finding_id=_fid("undocumented_auth", sig),
                    category=QualityCategory.SECURITY_GAP,
                    severity=FindingSeverity.MEDIUM,
                    description=(
                        f"Mutating operation '{sig}' has no documented security scheme "
                        f"in OpenAPI (backend may enforce authentication outside spec)."
                    ),
                    target_operation=sig,
                    recommendation=(
                        "Declare security requirements if operation is protected, "
                        "or document explicitly as public endpoint."
                    ),
                    status=VerificationStatus.INFERRED,
                    evidence=[
                        EvidenceReference(
                            source_type=EvidenceSourceType.OPENAPI,
                            source_ref=f"paths.{op.path}.{op.method.lower()}.security",
                            snippet="security: empty or missing",
                            confidence=1.0,
                            explanation=(
                                "Missing security requirement on mutating operation"
                            ),
                        )
                    ],
                )
            )

        # 6. Deprecated operations
        if op.deprecated:
            findings.append(
                ContractFinding(
                    finding_id=_fid("deprecated_op", sig),
                    category=QualityCategory.DOCUMENTATION_GAP,
                    severity=FindingSeverity.LOW,
                    description=f"Operation '{sig}' is marked as deprecated",
                    target_operation=sig,
                    recommendation="Plan test migration for deprecated endpoint",
                )
            )

        # 7. Generic or ambiguous module tags
        if op.module_status == VerificationStatus.INFERRED:
            findings.append(
                ContractFinding(
                    finding_id=_fid("generic_tags", sig),
                    category=QualityCategory.DOCUMENTATION_GAP,
                    severity=FindingSeverity.INFO,
                    description=(
                        f"Operation '{sig}' tags ({op.declared_tags}) are generic "
                        f"or absent; module '{op.module}' inferred from path structure."
                    ),
                    target_operation=sig,
                    recommendation=(
                        "Assign domain-specific tags in OpenAPI specification"
                    ),
                )
            )

        # 8. Check schema contradictions in parameters and bodies
        for p in op.parameters:
            findings.extend(
                _check_schema_contradictions(
                    p.schema, f"parameter '{p.name}' on '{sig}'", sig
                )
            )

        if op.request_body and op.request_body.schema:
            findings.extend(
                _check_schema_contradictions(
                    op.request_body.schema, f"requestBody on '{sig}'", sig
                )
            )

        for code, resp in op.responses.items():
            if resp.schema:
                findings.extend(
                    _check_schema_contradictions(
                        resp.schema, f"response {code} on '{sig}'", sig
                    )
                )

        return findings

    def _analyze_component_schema(
        self,
        schema_name: str,
        schema: dict[str, Any],
    ) -> list[ContractFinding]:
        """Analyze component schema definition for defects or omitted required lists."""
        findings = _check_schema_contradictions(
            schema, f"components.schemas.{schema_name}", f"schemas.{schema_name}"
        )

        # Missing required field declarations in object schemas
        # (Explicitly categorized as INFO/DOCUMENTATION_GAP, not a schema defect!)
        s_type = schema.get("type")
        props = schema.get("properties")
        if (s_type == "object" or props) and "required" not in schema:
            findings.append(
                ContractFinding(
                    finding_id=_fid("missing_required_list", schema_name),
                    category=QualityCategory.DOCUMENTATION_GAP,
                    severity=FindingSeverity.INFO,
                    description=(
                        f"Schema '{schema_name}' defines properties but omits an "
                        f"explicit 'required' fields list (properties treated as "
                        f"optional)."
                    ),
                    target_operation=f"schemas.{schema_name}",
                    recommendation=(
                        "Declare required fields if any fields are mandatory for "
                        "valid requests"
                    ),
                    status=VerificationStatus.INFERRED,
                )
            )

        return findings
