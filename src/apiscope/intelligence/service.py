"""Project Intelligence Service orchestrating target discovery and analysis.

Coordinates OpenAPI analysis, confined source code inspection, and optional
AI enrichment to produce an evidence-backed ProjectIntelligenceReport.
"""

from __future__ import annotations

import json
from pathlib import Path

from apiscope.intelligence.ai_enricher import AiProjectEnricher
from apiscope.intelligence.models import (
    DiscoveredEndpoint,
    DiscoveredEntity,
    DiscoveredModule,
    EvidenceReference,
    EvidenceSourceType,
    MissingInformationItem,
    ProjectIdentity,
    ProjectIntelligenceReport,
    RiskArea,
    VerificationStatus,
)
from apiscope.intelligence.openapi_analyzer import OpenApiAnalyzer
from apiscope.intelligence.source_scanner import (
    DiscoveredSourceFindings,
    SourceCodeScanner,
)


def _normalize_path(p: str) -> str:
    """Normalize path pattern for matching (e.g. '{productId}' -> '{}')."""
    import re

    # Replace parameter names with generic placeholder for fuzzy path matching
    return re.sub(r"\{[^}]+\}", "{}", p.rstrip("/").lower())


class ProjectIntelligenceService:
    """Orchestrates comprehensive target project intelligence gathering."""

    def __init__(
        self,
        openapi_analyzer: OpenApiAnalyzer | None = None,
        source_scanner: SourceCodeScanner | None = None,
        ai_enricher: AiProjectEnricher | None = None,
    ) -> None:
        """Initialize service with optional pre-configured analyzers."""
        self.openapi_analyzer = openapi_analyzer
        self._source_scanner = source_scanner
        self.ai_enricher = ai_enricher or AiProjectEnricher()

    def analyze_target(
        self,
        spec_path: str | Path,
        repo_path: str | Path | None = None,
        enable_ai: bool = False,
        provider_type: str = "gemini",
        api_key: str | None = None,
        model_name: str | None = None,
    ) -> ProjectIntelligenceReport:
        """Execute project intelligence pipeline against OpenAPI spec and optional repo.

        Args:
            spec_path: Path to OpenAPI specification (YAML or JSON).
            repo_path: Optional local source repository root directory.
            enable_ai: If True, invoke LLM enrichment for hypotheses.
            provider_type: LLM provider ("gemini" or "openai").
            api_key: Optional provider API key.
            model_name: Optional custom model name.

        Returns:
            Structured, evidence-backed ProjectIntelligenceReport.
        """
        # Step 1: Deterministic OpenAPI analysis
        if self.openapi_analyzer is not None:
            report = self.openapi_analyzer.analyze()
        else:
            report = OpenApiAnalyzer.analyze_file(spec_path)

        # Step 2: Optional source code scanning
        if repo_path is not None:
            scanner = self._source_scanner or SourceCodeScanner(repo_path)
            findings = scanner.scan()
            report = self._merge_source_findings(report, findings, repo_path)

        # Step 3: Optional AI enrichment (strictly opt-in)
        if enable_ai:
            report = self.ai_enricher.enrich_report(
                report=report,
                provider_type=provider_type,
                api_key=api_key,
                model_name=model_name,
            )

        return report

    def _merge_source_findings(
        self,
        report: ProjectIntelligenceReport,
        findings: DiscoveredSourceFindings,
        repo_path: str | Path,
    ) -> ProjectIntelligenceReport:
        """Merge discovered source code findings into baseline OpenAPI report."""
        repo_p = Path(repo_path)

        # 1. Update project identity with language and source evidence
        new_identity = ProjectIdentity(
            name=report.project.name,
            version=report.project.version,
            description=report.project.description,
            servers=list(report.project.servers),
            source_language=findings.source_language,
            evidence=[
                *report.project.evidence,
                EvidenceReference(
                    source_type=EvidenceSourceType.SOURCE_CODE,
                    source_ref=f"repo:{repo_p.name}",
                    snippet=(
                        f"Scanned {findings.scanned_files_count} files, "
                        f"detected {findings.source_language}"
                    ),
                    confidence=1.0,
                    explanation="Static source code repository scan",
                ),
            ],
        )

        # 2. Correlate endpoints and verify modules
        merged_endpoints = list(report.endpoints)
        confirmed_controllers: dict[str, str] = {}  # module_name -> controller_name
        source_matched_endpoints: set[str] = set()

        for src_ep in findings.endpoints:
            for idx, ep in enumerate(merged_endpoints):
                # Match path and method
                if ep.method.upper() == src_ep.method.upper() and _normalize_path(
                    ep.path
                ) == _normalize_path(src_ep.path):
                    source_matched_endpoints.add(f"{src_ep.method} {src_ep.path}")

                    # Upgrade module verification to CONFIRMED
                    confirmed_controllers[ep.inferred_module] = src_ep.controller_name

                    # Add source evidence to endpoint
                    new_evidence = list(ep.evidence)
                    if src_ep.evidence:
                        new_evidence.append(src_ep.evidence)

                    # Check for auth mismatches / conflicts
                    if src_ep.required_roles and not ep.requires_auth:
                        report.risk_areas.append(
                            RiskArea(
                                area_id=f"auth_conflict_{ep.method}_{ep.path}",
                                category="SECURITY_MISMATCH",
                                description=(
                                    f"Endpoint '{ep.method} {ep.path}' has no auth in"
                                    f" OpenAPI spec but source enforces roles"
                                    f" {src_ep.required_roles}"
                                ),
                                affected_endpoints=[f"{ep.method} {ep.path}"],
                                severity="HIGH",
                                evidence=[
                                    EvidenceReference(
                                        source_type=EvidenceSourceType.SOURCE_CODE,
                                        source_ref=(
                                            f"{src_ep.file_path}:{src_ep.line_number}"
                                        ),
                                        snippet=f"Roles: {src_ep.required_roles}",
                                        confidence=1.0,
                                        explanation=("Source controller role mismatch"),
                                    )
                                ],
                            )
                        )

                    # Replace endpoint with updated source info
                    merged_endpoints[idx] = DiscoveredEndpoint(
                        path=ep.path,
                        method=ep.method,
                        operation_id=ep.operation_id,
                        summary=ep.summary or src_ep.docstring,
                        declared_tags=ep.declared_tags,
                        inferred_module=ep.inferred_module,
                        module_status=VerificationStatus.CONFIRMED,
                        requires_auth=ep.requires_auth or bool(src_ep.required_roles),
                        auth_schemes=ep.auth_schemes,
                        parameters=ep.parameters,
                        request_schema_ref=ep.request_schema_ref,
                        response_schema_refs=ep.response_schema_refs,
                        evidence=new_evidence,
                    )
                    break

        # Check for shadow endpoints present in source code but missing from OpenAPI
        for src_ep in findings.endpoints:
            ep_key = f"{src_ep.method} {src_ep.path}"
            if ep_key not in source_matched_endpoints:
                report.missing_information.append(
                    MissingInformationItem(
                        category="SHADOW_ENDPOINT",
                        description=(
                            f"Endpoint '{src_ep.method} {src_ep.path}' implemented in "
                            f"{src_ep.controller_name} ({src_ep.file_path}:"
                            f"{src_ep.line_number}) is not documented in OpenAPI."
                        ),
                        affected_endpoints=[ep_key],
                        recommendation=(
                            "Review source controller to document or decommission"
                            " route."
                        ),
                    )
                )

        # 3. Upgrade matching modules to CONFIRMED with source controller provenance
        merged_modules: list[DiscoveredModule] = []
        for mod in report.modules:
            if mod.name in confirmed_controllers:
                c_name = confirmed_controllers[mod.name]
                merged_modules.append(
                    DiscoveredModule(
                        name=mod.name,
                        endpoints_count=mod.endpoints_count,
                        endpoints=mod.endpoints,
                        status=VerificationStatus.CONFIRMED,
                        source_controller=c_name,
                        evidence=[
                            *mod.evidence,
                            EvidenceReference(
                                source_type=EvidenceSourceType.SOURCE_CODE,
                                source_ref=c_name,
                                snippet=f"Matched controller: {c_name}",
                                confidence=1.0,
                                explanation=(
                                    "Confirmed module mapping from source controller"
                                ),
                            ),
                        ],
                    )
                )
            else:
                merged_modules.append(mod)

        # 4. Merge Entities and JPA/Pydantic models
        merged_entities = list(report.entities)
        existing_entity_names = {
            e.name.lower(): idx for idx, e in enumerate(merged_entities)
        }

        for src_model in findings.models:
            model_key = src_model.name.lower()
            if model_key in existing_entity_names:
                idx = existing_entity_names[model_key]
                existing = merged_entities[idx]
                combined_props = sorted(
                    set(existing.properties) | set(src_model.fields)
                )
                new_evidence = list(existing.evidence)
                if src_model.evidence:
                    new_evidence.append(src_model.evidence)

                merged_entities[idx] = DiscoveredEntity(
                    name=existing.name,
                    schema_name=existing.schema_name,
                    properties=combined_props,
                    primary_key=existing.primary_key,
                    managing_endpoints=existing.managing_endpoints,
                    status=VerificationStatus.CONFIRMED,
                    evidence=new_evidence,
                )
            elif src_model.is_entity:
                # Add newly discovered source entity
                merged_entities.append(
                    DiscoveredEntity(
                        name=src_model.name,
                        schema_name=src_model.name,
                        properties=list(src_model.fields),
                        status=VerificationStatus.CONFIRMED,
                        evidence=[src_model.evidence] if src_model.evidence else [],
                    )
                )

        # 5. Merge Roles and Role Rules
        combined_roles = sorted(set(report.auth.roles) | set(findings.roles))
        from apiscope.intelligence.models import DiscoveredAuthModel

        merged_auth = DiscoveredAuthModel(
            auth_type=report.auth.auth_type,
            roles=combined_roles,
            role_rules=report.auth.role_rules,
            status=(
                VerificationStatus.CONFIRMED if findings.roles else report.auth.status
            ),
            evidence=[
                *report.auth.evidence,
                EvidenceReference(
                    source_type=EvidenceSourceType.SOURCE_CODE,
                    source_ref=f"repo:{repo_p.name}",
                    snippet=f"Discovered roles: {findings.roles}",
                    confidence=1.0,
                    explanation=(
                        "Roles discovered from source code security annotations"
                    ),
                ),
            ],
        )

        # 6. Merge discovered business rules
        merged_rules = list(report.business_rules)
        merged_rules.extend(findings.business_rules)

        # Construct new combined report
        return ProjectIntelligenceReport(
            schema_version=report.schema_version,
            generated_at=report.generated_at,
            project=new_identity,
            endpoints=merged_endpoints,
            modules=merged_modules,
            entities=merged_entities,
            relationships=report.relationships,
            auth=merged_auth,
            workflows=report.workflows,
            business_rules=merged_rules,
            test_data_requirements=report.test_data_requirements,
            risk_areas=report.risk_areas,
            missing_information=report.missing_information,
            warnings=report.warnings,
        )

    @staticmethod
    def save_report(
        report: ProjectIntelligenceReport,
        output_path: str | Path,
    ) -> None:
        """Serialize and save intelligence report to JSON or YAML file.

        Args:
            report: Populated ProjectIntelligenceReport.
            output_path: Destination file path (.json or .yaml/.yml).
        """
        out_p = Path(output_path).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)

        if out_p.suffix.lower() == ".json":
            with open(out_p, "w", encoding="utf-8") as f:
                json.dump(report.to_dict(), f, indent=2)
        else:
            with open(out_p, "w", encoding="utf-8") as f:
                f.write(report.to_yaml())
