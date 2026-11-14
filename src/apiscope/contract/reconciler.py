"""Contract Reconciler aligning OpenAPI specifications with source code findings.

Distinguishes multi-service monorepo contexts, reconciles controller routes with
OpenAPI operations, detects security/parameter drift, and identifies authorization
test candidates without premature vulnerability declarations.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from apiscope.contract.models import (
    CanonicalContract,
    ContractFinding,
    FindingSeverity,
    QualityCategory,
)
from apiscope.intelligence.models import (
    EvidenceReference,
    EvidenceSourceType,
    VerificationStatus,
)
from apiscope.intelligence.source_scanner import (
    DiscoveredSourceEndpoint,
    DiscoveredSourceFindings,
)


def _normalize_path_pattern(p: str) -> str:
    """Normalize path parameter syntax to generic placeholder '{}' for comparison."""
    clean = re.sub(r"\{[^}]+\}", "{}", p.rstrip("/").lower())
    return clean or "/"


def _is_same_service_context(
    endpoint: DiscoveredSourceEndpoint,
    primary_language: str,
) -> bool:
    """Check if discovered source endpoint belongs to target service."""
    path_low = endpoint.file_path.lower().replace("\\", "/")

    if "java" in primary_language.lower():
        # Target service is Java; exclude Python, Node, or frontend paths
        return not (
            path_low.endswith(".py")
            or "/analytics/" in path_low
            or "/web/" in path_low
            or "/client/" in path_low
        )

    if "python" in primary_language.lower():
        # Target service is Python; exclude Java or frontend paths
        return not (
            path_low.endswith(".java")
            or "/java/" in path_low
            or "/web/" in path_low
            or "/client/" in path_low
        )

    return True


class ContractReconciler:
    """Reconciles canonical OpenAPI operations against source code discoveries."""

    def reconcile(
        self,
        contract: CanonicalContract,
        findings: DiscoveredSourceFindings,
    ) -> dict[str, Any]:
        """Perform bi-directional reconciliation and attach drift findings."""
        matched_operations: list[dict[str, str]] = []
        spec_only_operations: list[str] = []
        target_source_only_routes: list[dict[str, Any]] = []
        isolated_other_service_routes: list[dict[str, Any]] = []

        # 1. Partition discovered source endpoints by service boundary
        target_service_endpoints: list[DiscoveredSourceEndpoint] = []
        for src_ep in findings.endpoints:
            if _is_same_service_context(src_ep, findings.source_language):
                target_service_endpoints.append(src_ep)
            else:
                p_parts = Path(src_ep.file_path).parts
                isolated_other_service_routes.append(
                    {
                        "method": src_ep.method,
                        "path": src_ep.path,
                        "file_path": src_ep.file_path,
                        "service_context": p_parts[0] if p_parts else "external",
                    }
                )

        # 2. Correlate target service endpoints with canonical contract operations
        matched_src_sigs: set[str] = set()

        for op in contract.operations:
            op_norm = _normalize_path_pattern(op.path)
            op_matched = False

            for src_ep in target_service_endpoints:
                src_norm = _normalize_path_pattern(src_ep.path)
                if op.method.upper() == src_ep.method.upper() and op_norm == src_norm:
                    op_matched = True
                    matched_src_sigs.add(f"{src_ep.method} {src_ep.path}")

                    # Upgrade module verification to CONFIRMED
                    op.module_status = VerificationStatus.CONFIRMED

                    # Link source code evidence
                    src_ref = f"{src_ep.file_path}:{src_ep.line_number}"
                    op.evidence.append(
                        EvidenceReference(
                            source_type=EvidenceSourceType.SOURCE_CODE,
                            source_ref=src_ref,
                            snippet=f"{src_ep.controller_name}.{src_ep.method_name}()",
                            confidence=1.0,
                            explanation=(
                                "Confirmed route implementation in target controller"
                            ),
                        )
                    )

                    # Security drift check
                    if src_ep.required_roles and not op.requires_auth:
                        op_hash = abs(hash(op.method + op.path)) % 100000
                        drift_finding = ContractFinding(
                            finding_id=f"security_drift_{op_hash}",
                            category=QualityCategory.SOURCE_SPEC_DRIFT,
                            severity=FindingSeverity.HIGH,
                            description=(
                                f"Security drift on '{op.method} {op.path}': Source "
                                f"controller enforces {src_ep.required_roles} via "
                                f"annotations, but OpenAPI spec marks operation public."
                            ),
                            target_operation=f"{op.method} {op.path}",
                            recommendation=(
                                "Update OpenAPI security requirement to match source "
                                "access control rules"
                            ),
                            status=VerificationStatus.CONFLICTING,
                            evidence=[
                                EvidenceReference(
                                    source_type=EvidenceSourceType.SOURCE_CODE,
                                    source_ref=src_ref,
                                    snippet=f"Roles: {src_ep.required_roles}",
                                    confidence=1.0,
                                    explanation="Source role authorization constraint",
                                )
                            ],
                        )
                        op.findings.append(drift_finding)
                        contract.findings.append(drift_finding)

                    matched_operations.append(
                        {
                            "operation": f"{op.method} {op.path}",
                            "controller": src_ep.controller_name,
                            "source_file": src_ref,
                        }
                    )
                    break

            if not op_matched:
                spec_only_operations.append(f"{op.method} {op.path}")
                op_hash = abs(hash(op.method + op.path)) % 100000
                drift_f = ContractFinding(
                    finding_id=f"spec_only_route_{op_hash}",
                    category=QualityCategory.SOURCE_SPEC_DRIFT,
                    severity=FindingSeverity.INFO,
                    description=(
                        f"Specification-only operation '{op.method} {op.path}': No "
                        f"matching controller method located in scanned source tree."
                    ),
                    target_operation=f"{op.method} {op.path}",
                    recommendation=(
                        "Verify operation implementation or check if route is served "
                        "by external delegate or generated proxy"
                    ),
                    status=VerificationStatus.INFERRED,
                )
                op.findings.append(drift_f)
                contract.findings.append(drift_f)

        # 3. Check target-source endpoints missing from OpenAPI
        for src_ep in target_service_endpoints:
            sig = f"{src_ep.method} {src_ep.path}"
            if sig not in matched_src_sigs:
                target_source_only_routes.append(
                    {
                        "method": src_ep.method,
                        "path": src_ep.path,
                        "controller": src_ep.controller_name,
                        "file_path": f"{src_ep.file_path}:{src_ep.line_number}",
                    }
                )
                sig_hash = abs(hash(sig)) % 100000
                src_drift_f = ContractFinding(
                    finding_id=f"source_only_route_{sig_hash}",
                    category=QualityCategory.SOURCE_SPEC_DRIFT,
                    severity=FindingSeverity.MEDIUM,
                    description=(
                        f"Source-only operation '{sig}' implemented in "
                        f"{src_ep.controller_name} ({src_ep.file_path}:"
                        f"{src_ep.line_number}) is missing from OpenAPI specification."
                    ),
                    target_operation=sig,
                    recommendation=(
                        "Document route in OpenAPI specification or verify if intended "
                        "as internal operational endpoint"
                    ),
                    status=VerificationStatus.CONFIRMED,
                    evidence=[
                        EvidenceReference(
                            source_type=EvidenceSourceType.SOURCE_CODE,
                            source_ref=f"{src_ep.file_path}:{src_ep.line_number}",
                            snippet=f"{src_ep.controller_name}.{src_ep.method_name}",
                            confidence=1.0,
                            explanation=(
                                "Source controller route missing from specification"
                            ),
                        )
                    ],
                )
                contract.findings.append(src_drift_f)

        # 4. Identify potential authorization boundary test candidates
        for op in contract.operations:
            if op.method == "GET" and "{" in op.path:
                id_params = [
                    p.name
                    for p in op.parameters
                    if p.location == "path"
                    and any(id_kw in p.name.lower() for id_kw in ("id", "code", "num"))
                ]
                if id_params:
                    auth_cand = ContractFinding(
                        finding_id=f"auth_candidate_{abs(hash(op.path)) % 100000}",
                        category=QualityCategory.SECURITY_GAP,
                        severity=FindingSeverity.INFO,
                        description=(
                            f"Potential authorization boundary candidate on "
                            f"'{op.method} {op.path}': GET accepts resource "
                            f"identifier {id_params}. Suitable candidate for "
                            f"isolation testing (not a confirmed vulnerability)."
                        ),
                        target_operation=f"{op.method} {op.path}",
                        recommendation=(
                            "Design authorization test probes with varied identity "
                            "contexts"
                        ),
                        status=VerificationStatus.INFERRED,
                    )
                    op.findings.append(auth_cand)
                    contract.findings.append(auth_cand)

        return {
            "primary_language": findings.source_language,
            "scanned_files_count": findings.scanned_files_count,
            "target_service_routes_count": len(target_service_endpoints),
            "matched_operations_count": len(matched_operations),
            "spec_only_operations_count": len(spec_only_operations),
            "source_only_routes_count": len(target_source_only_routes),
            "isolated_microservice_routes_count": len(isolated_other_service_routes),
            "matched_operations": matched_operations,
            "spec_only_operations": spec_only_operations,
            "source_only_routes": target_source_only_routes,
            "isolated_microservice_routes": isolated_other_service_routes,
        }
