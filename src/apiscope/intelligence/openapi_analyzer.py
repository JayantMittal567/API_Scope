"""Deterministic OpenAPI Analyzer for Project Intelligence Engine.

Discovers project identity, endpoint operations, functional modules, entity candidates,
schema relationships, authentication models, candidate workflows, risk surfaces, and
missing specification documentation directly from OpenAPI 3.x specifications.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from apiscope.generators.openapi_importer import (
    OpenAPISpec,
    load_openapi_spec,
    parse_openapi_spec,
)
from apiscope.intelligence.models import (
    DiscoveredAuthModel,
    DiscoveredEndpoint,
    DiscoveredEntity,
    DiscoveredModule,
    DiscoveredRelationship,
    DiscoveredWorkflow,
    EvidenceReference,
    EvidenceSourceType,
    MissingInformationItem,
    ProjectIdentity,
    ProjectIntelligenceReport,
    RiskArea,
    TestDataRequirement,
    VerificationStatus,
)

GENERIC_TAG_PATTERNS: tuple[str, ...] = (
    "controller",
    "controllers",
    "api",
    "apis",
    "endpoint",
    "endpoints",
    "handler",
    "handlers",
    "default",
    "root",
    "main",
    "rest",
    "service",
    "services",
    "v1",
    "v2",
    "v0",
    "stockpile-controller",
    "sample-app",
)

SKIP_PATH_SEGMENTS: frozenset[str] = frozenset(
    {"api", "v1", "v2", "v3", "v4", "v0", "rest", "app"}
)


def _slugify(text: str) -> str:
    """Normalize text into a lowercase underscore-separated identifier."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", text.strip().lower()).strip("_")
    return cleaned or "default"


def _clean_schema_name(name: str) -> str:
    """Normalize a schema name by stripping package or wrapper prefixes."""
    parts = name.split(".")
    return parts[-1]


class OpenApiAnalyzer:
    """Analyzes OpenAPI specifications to produce deterministic project intelligence."""

    def __init__(self, raw_spec: dict[str, Any], openapi_spec: OpenAPISpec) -> None:
        """Initialize OpenApiAnalyzer.

        Args:
            raw_spec: Unresolved raw dictionary mapping of the OpenAPI specification.
            openapi_spec: Parsed and normalized OpenAPISpec instance.
        """
        self.raw_spec = raw_spec
        self.spec = openapi_spec
        self.raw_components = raw_spec.get("components", {}) or {}
        self.raw_schemas = self.raw_components.get("schemas", {}) or {}
        self.raw_security_schemes = self.raw_components.get("securitySchemes", {}) or {}

    @classmethod
    def from_file(cls, spec_path: str) -> OpenApiAnalyzer:
        """Construct analyzer from OpenAPI file path."""
        import yaml

        with open(spec_path, encoding="utf-8-sig") as f:
            raw_data = yaml.safe_load(f.read())
        parsed = load_openapi_spec(spec_path)
        return cls(raw_spec=raw_data, openapi_spec=parsed)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OpenApiAnalyzer:
        """Construct analyzer from parsed OpenAPI dictionary."""
        parsed = parse_openapi_spec(data)
        return cls(raw_spec=data, openapi_spec=parsed)

    def extract_project_identity(self) -> ProjectIdentity:
        """Extract project identity, platform version, and server configuration."""
        info = self.raw_spec.get("info", {}) or {}
        title = str(info.get("title") or self.spec.title or "Unnamed API").strip()
        version = str(info.get("version") or self.spec.version or "0.1.0").strip()
        desc = str(info.get("description") or "").strip()
        servers = list(self.spec.servers)

        evidence = [
            EvidenceReference(
                source_type=EvidenceSourceType.OPENAPI,
                source_ref="info",
                snippet=f"title: '{title}', version: '{version}'",
                confidence=1.0,
                explanation="Extracted directly from OpenAPI info block",
            )
        ]

        return ProjectIdentity(
            name=title,
            version=version,
            description=desc,
            servers=servers,
            source_language="unknown",
            evidence=evidence,
        )

    def _is_tag_set_generic(self, all_tags: list[str]) -> bool:
        """Determine whether the specification's tags are generic or uninformative."""
        if not all_tags:
            return True
        unique_tags = {t.strip().lower() for t in all_tags if t.strip()}
        if len(unique_tags) <= 1:
            return True
        return any(any(gen in t for gen in GENERIC_TAG_PATTERNS) for t in unique_tags)

    def _infer_module_from_path(self, path: str) -> str:
        """Infer functional domain module name from path structure."""
        clean = path.strip("/")
        segments = clean.split("/") if clean else []
        filtered: list[str] = []
        for s in segments:
            s_low = s.lower()
            if s_low in SKIP_PATH_SEGMENTS or re.match(r"^v\d+$", s_low):
                continue
            clean_s = re.sub(r"[{}]", "", s)
            if clean_s:
                filtered.append(clean_s)

        if filtered:
            return _slugify(filtered[0])
        return "default"

    def analyze_endpoints_and_modules(
        self,
    ) -> tuple[
        list[DiscoveredEndpoint],
        list[DiscoveredModule],
        list[MissingInformationItem],
    ]:
        """Discover endpoints, apply deterministic module inference, and note gaps."""
        raw_paths = self.raw_spec.get("paths", {}) or {}
        collected_tags: list[str] = []
        for p_dict in raw_paths.values():
            if isinstance(p_dict, dict):
                for op_dict in p_dict.values():
                    if isinstance(op_dict, dict):
                        collected_tags.extend(op_dict.get("tags", []))

        generic_tags = self._is_tag_set_generic(collected_tags)
        missing_info: list[MissingInformationItem] = []
        if generic_tags and collected_tags:
            missing_info.append(
                MissingInformationItem(
                    category="generic_tags",
                    description=(
                        f"OpenAPI operations share generic or monolithic tags "
                        f"({sorted(set(collected_tags))}). Functional modules inferred "
                        "from URL path segments."
                    ),
                    recommendation=(
                        "Define distinct, domain-specific tags per resource "
                        "controller (e.g., 'products', 'suppliers', 'sales')."
                    ),
                )
            )

        endpoints: list[DiscoveredEndpoint] = []
        module_endpoint_map: dict[str, list[str]] = {}
        module_status_map: dict[str, VerificationStatus] = {}

        for ep in self.spec.endpoints:
            orig_tags: list[str] = []
            op_data = raw_paths.get(ep.path, {}).get(ep.method.lower(), {})
            if isinstance(op_data, dict):
                orig_tags = op_data.get("tags", [])

            if generic_tags or not orig_tags:
                mod_name = self._infer_module_from_path(ep.path)
                mod_status = VerificationStatus.INFERRED
                evidence_desc = (
                    f"Inferred module '{mod_name}' from path structure '{ep.path}' "
                    f"(generic or absent tags: {orig_tags})"
                )
            else:
                mod_name = _slugify(str(orig_tags[0]))
                mod_status = VerificationStatus.CONFIRMED
                evidence_desc = (
                    f"Confirmed module from declared operation tag: '{orig_tags[0]}'"
                )

            resp_refs: dict[str, str] = {}
            if isinstance(op_data, dict):
                responses = op_data.get("responses", {})
                if isinstance(responses, dict):
                    for status_code, resp_dict in responses.items():
                        if isinstance(resp_dict, dict):
                            content = resp_dict.get("content", {})
                            if isinstance(content, dict):
                                for media_obj in content.values():
                                    schema_obj = (
                                        media_obj.get("schema", {})
                                        if isinstance(media_obj, dict)
                                        else {}
                                    )
                                    ref = schema_obj.get("$ref")
                                    if ref:
                                        resp_refs[str(status_code)] = str(ref)

            req_ref: str | None = None
            if isinstance(op_data, dict):
                req_body = op_data.get("requestBody", {})
                if isinstance(req_body, dict):
                    req_content = req_body.get("content", {})
                    if isinstance(req_content, dict):
                        for media_obj in req_content.values():
                            if isinstance(media_obj, dict):
                                s_ref = media_obj.get("schema", {}).get("$ref")
                                if s_ref:
                                    req_ref = str(s_ref)

            params: list[dict[str, Any]] = [p.to_dict() for p in ep.parameters]

            ep_evidence = [
                EvidenceReference(
                    source_type=EvidenceSourceType.OPENAPI,
                    source_ref=f"paths.{ep.path}.{ep.method.lower()}",
                    snippet=f"{ep.method} {ep.path} (tags: {orig_tags})",
                    confidence=(
                        1.0 if mod_status == VerificationStatus.CONFIRMED else 0.85
                    ),
                    explanation=evidence_desc,
                )
            ]

            discovered_ep = DiscoveredEndpoint(
                path=ep.path,
                method=ep.method,
                operation_id=ep.operation_id,
                summary=ep.summary,
                declared_tags=orig_tags,
                inferred_module=mod_name,
                module_status=mod_status,
                requires_auth=ep.requires_auth,
                auth_schemes=[str(s) for s in ep.security_schemes],
                parameters=params,
                request_schema_ref=req_ref,
                response_schema_refs=resp_refs,
                evidence=ep_evidence,
            )
            endpoints.append(discovered_ep)

            ep_sig = f"{ep.method} {ep.path}"
            module_endpoint_map.setdefault(mod_name, []).append(ep_sig)
            if mod_name not in module_status_map:
                module_status_map[mod_name] = mod_status
            elif mod_status == VerificationStatus.CONFIRMED:
                module_status_map[mod_name] = VerificationStatus.CONFIRMED

        modules: list[DiscoveredModule] = []
        for mod_name, ep_list in sorted(module_endpoint_map.items()):
            m_status = module_status_map.get(mod_name, VerificationStatus.INFERRED)
            m_evidence = [
                EvidenceReference(
                    source_type=EvidenceSourceType.OPENAPI,
                    source_ref=f"module.{mod_name}",
                    snippet=f"Module '{mod_name}' has {len(ep_list)} endpoints",
                    confidence=(
                        1.0 if m_status == VerificationStatus.CONFIRMED else 0.85
                    ),
                    explanation=(
                        f"Grouped {len(ep_list)} operations into '{mod_name}' "
                        f"via {m_status.value} heuristic"
                    ),
                )
            ]
            modules.append(
                DiscoveredModule(
                    name=mod_name,
                    endpoints_count=len(ep_list),
                    endpoints=sorted(ep_list),
                    status=m_status,
                    evidence=m_evidence,
                )
            )

        return endpoints, modules, missing_info

    def analyze_entities_and_relationships(
        self, endpoints: list[DiscoveredEndpoint]
    ) -> tuple[list[DiscoveredEntity], list[DiscoveredRelationship]]:
        """Extract domain entity candidates and schema/path relationships."""
        entities: list[DiscoveredEntity] = []
        entity_name_set: set[str] = set()

        # 1. Discover entities from OpenAPI components/schemas
        for s_name, s_def in self.raw_schemas.items():
            if not isinstance(s_def, dict):
                continue

            clean_name = _clean_schema_name(s_name)
            if clean_name.lower() in (
                "apiresponse",
                "errorresponse",
                "pageable",
            ):
                continue

            props = list(s_def.get("properties", {}).keys())
            pk = next(
                (
                    p
                    for p in props
                    if p.lower()
                    in (
                        "id",
                        f"{clean_name.lower()}id",
                        f"{clean_name.lower()}_id",
                    )
                ),
                None,
            )

            managing: list[str] = []
            for ep in endpoints:
                if clean_name.lower() in ep.path.lower():
                    managing.append(f"{ep.method} {ep.path}")

            evidence = [
                EvidenceReference(
                    source_type=EvidenceSourceType.OPENAPI,
                    source_ref=f"#/components/schemas/{s_name}",
                    snippet=f"Schema '{clean_name}' properties: {props[:5]}",
                    confidence=1.0,
                    explanation="Discovered in components/schemas declaration",
                )
            ]

            entities.append(
                DiscoveredEntity(
                    name=clean_name,
                    schema_name=s_name,
                    properties=props,
                    primary_key=pk,
                    managing_endpoints=managing,
                    status=VerificationStatus.CONFIRMED,
                    evidence=evidence,
                )
            )
            entity_name_set.add(clean_name.lower())

        # 2. Extract path-inferred entities for paths without explicit schema matches
        path_entity_names: set[str] = set()
        for ep in endpoints:
            mod = ep.inferred_module
            if (
                mod != "default"
                and mod.lower() not in entity_name_set
                and mod.lower() not in path_entity_names
            ):
                ent_name = mod.rstrip("s").replace("_", " ").title().replace(" ", "")
                if (
                    ent_name.lower() not in entity_name_set
                    and ent_name.lower() not in path_entity_names
                ):
                    path_entity_names.add(ent_name.lower())
                    managing_eps = [
                        f"{e.method} {e.path}"
                        for e in endpoints
                        if e.inferred_module == mod
                    ]
                    entities.append(
                        DiscoveredEntity(
                            name=ent_name,
                            schema_name=None,
                            properties=[],
                            primary_key="id",
                            managing_endpoints=managing_eps,
                            status=VerificationStatus.INFERRED,
                            evidence=[
                                EvidenceReference(
                                    source_type=EvidenceSourceType.OPENAPI,
                                    source_ref=f"paths.{ep.path}",
                                    snippet=f"Path '{ep.path}' -> '{ent_name}'",
                                    confidence=0.8,
                                    explanation=(
                                        "Inferred from URL path structure "
                                        "without explicit schema"
                                    ),
                                )
                            ],
                        )
                    )

        # 3. Discover Relationships between entities
        relationships: list[DiscoveredRelationship] = []
        for entity in entities:
            if not entity.schema_name or entity.schema_name not in self.raw_schemas:
                continue
            s_dict = self.raw_schemas[entity.schema_name]
            properties = s_dict.get("properties", {})
            if not isinstance(properties, dict):
                continue

            for prop_name, prop_dict in properties.items():
                if not isinstance(prop_dict, dict):
                    continue

                ref = prop_dict.get("$ref")
                if ref and isinstance(ref, str):
                    target_name = _clean_schema_name(ref.split("/")[-1])
                    if target_name.lower() != entity.name.lower():
                        relationships.append(
                            DiscoveredRelationship(
                                source_entity=entity.name,
                                target_entity=target_name,
                                relationship_type="references_schema",
                                foreign_key_field=prop_name,
                                status=VerificationStatus.CONFIRMED,
                                evidence=[
                                    EvidenceReference(
                                        source_type=EvidenceSourceType.OPENAPI,
                                        source_ref=(
                                            f"#/components/schemas/{entity.schema_name}"
                                            f"/properties/{prop_name}"
                                        ),
                                        snippet=f"Property '{prop_name}' -> '{ref}'",
                                        confidence=1.0,
                                        explanation="Direct $ref schema pointer",
                                    )
                                ],
                            )
                        )

                p_lower = prop_name.lower()
                for other in entities:
                    if other.name.lower() == entity.name.lower():
                        continue
                    if p_lower in (
                        f"{other.name.lower()}id",
                        f"{other.name.lower()}_id",
                    ):
                        relationships.append(
                            DiscoveredRelationship(
                                source_entity=entity.name,
                                target_entity=other.name,
                                relationship_type="foreign_key_reference",
                                foreign_key_field=prop_name,
                                status=VerificationStatus.INFERRED,
                                evidence=[
                                    EvidenceReference(
                                        source_type=EvidenceSourceType.OPENAPI,
                                        source_ref=(
                                            f"#/components/schemas/{entity.schema_name}"
                                            f"/properties/{prop_name}"
                                        ),
                                        snippet=(
                                            f"Field '{prop_name}' matches candidate "
                                            f"key of '{other.name}'"
                                        ),
                                        confidence=0.85,
                                        explanation="Foreign key naming match",
                                    )
                                ],
                            )
                        )

        # 4. Check nested path relationships (e.g. /products/{productId}/supplier)
        for ep in endpoints:
            m = re.match(
                r"^/api/([a-zA-Z0-9_-]+)/\{[a-zA-Z0-9_]+\}/([a-zA-Z0-9_-]+)$",
                ep.path,
            )
            if m:
                parent_seg = m.group(1).rstrip("s").title()
                child_seg = m.group(2).rstrip("s").title()
                relationships.append(
                    DiscoveredRelationship(
                        source_entity=parent_seg,
                        target_entity=child_seg,
                        relationship_type="nested_sub_resource",
                        foreign_key_field=None,
                        status=VerificationStatus.INFERRED,
                        evidence=[
                            EvidenceReference(
                                source_type=EvidenceSourceType.OPENAPI,
                                source_ref=f"paths.{ep.path}",
                                snippet=f"Path nesting: {ep.path}",
                                confidence=0.85,
                                explanation=(
                                    "URL path nesting indicates sub-resource "
                                    "association"
                                ),
                            )
                        ],
                    )
                )

        return entities, relationships

    def analyze_authentication(
        self, endpoints: list[DiscoveredEndpoint]
    ) -> DiscoveredAuthModel:
        """Analyze authentication schemes and infer role boundaries."""
        auth_type = "none"
        schemes = list(self.raw_security_schemes.keys())
        if schemes:
            first_scheme = self.raw_security_schemes[schemes[0]]
            sec_type = first_scheme.get("type", "").lower()
            scheme_name = first_scheme.get("scheme", "").lower()
            if sec_type == "http" and scheme_name == "bearer":
                auth_type = "bearer_jwt"
            elif sec_type == "apiKey":
                auth_type = f"api_key_{first_scheme.get('in', 'header')}"
            elif sec_type == "http" and scheme_name == "basic":
                auth_type = "basic"
            else:
                auth_type = sec_type or "custom"

        discovered_roles: set[str] = set()
        role_pattern = re.compile(
            r"\b(admin|administrator|manager|viewer|editor|customer|user)\b",
            re.IGNORECASE,
        )

        role_allowed_map: dict[str, list[str]] = {}
        for ep in endpoints:
            for text_source in (ep.summary, ep.operation_id):
                matches = role_pattern.findall(text_source)
                for r in matches:
                    r_clean = r.title()
                    discovered_roles.add(r_clean)
                    role_allowed_map.setdefault(r_clean, []).append(
                        f"{ep.method} {ep.path}"
                    )

        if not discovered_roles:
            if auth_type != "none":
                discovered_roles = {"Admin", "Viewer"}
            else:
                discovered_roles = {"Anonymous"}

        role_rules: list[dict[str, Any]] = []
        for role in sorted(discovered_roles):
            allowed = role_allowed_map.get(role, [])
            role_rules.append(
                {
                    "role_name": role,
                    "token_variable": f"{{{role.lower()}_token}}",
                    "allowed_endpoints": sorted(allowed),
                    "forbidden_endpoints": [],
                }
            )

        evidence = [
            EvidenceReference(
                source_type=EvidenceSourceType.OPENAPI,
                source_ref="components.securitySchemes",
                snippet=f"Declared schemes: {schemes}",
                confidence=1.0 if schemes else 0.7,
                explanation="Security schemes declared in OpenAPI specification",
            )
        ]

        return DiscoveredAuthModel(
            auth_type=auth_type,
            roles=sorted(discovered_roles),
            role_rules=role_rules,
            status=(
                VerificationStatus.CONFIRMED if schemes else VerificationStatus.INFERRED
            ),
            evidence=evidence,
        )

    def analyze_workflows(
        self,
        modules: list[DiscoveredModule],
        endpoints: list[DiscoveredEndpoint],
    ) -> list[DiscoveredWorkflow]:
        """Synthesize candidate multi-step workflows based on CRUD patterns."""
        workflows: list[DiscoveredWorkflow] = []

        for mod in modules:
            mod_eps = [e for e in endpoints if e.inferred_module == mod.name]
            has_post = next((e for e in mod_eps if e.method == "POST"), None)
            has_get_item = next(
                (e for e in mod_eps if e.method == "GET" and "{" in e.path),
                None,
            )
            has_put = next(
                (e for e in mod_eps if e.method in ("PUT", "PATCH") and "{" in e.path),
                None,
            )
            has_delete = next(
                (e for e in mod_eps if e.method == "DELETE" and "{" in e.path),
                None,
            )

            if has_post and has_get_item:
                steps: list[dict[str, Any]] = [
                    {
                        "step": 1,
                        "action": "create",
                        "method": "POST",
                        "path": has_post.path,
                        "extract": "id",
                    },
                    {
                        "step": 2,
                        "action": "read_and_verify",
                        "method": "GET",
                        "path": has_get_item.path,
                        "requires_var": "id",
                    },
                ]
                if has_put:
                    steps.append(
                        {
                            "step": len(steps) + 1,
                            "action": "update",
                            "method": has_put.method,
                            "path": has_put.path,
                            "requires_var": "id",
                        }
                    )
                if has_delete:
                    steps.append(
                        {
                            "step": len(steps) + 1,
                            "action": "delete",
                            "method": "DELETE",
                            "path": has_delete.path,
                            "requires_var": "id",
                        }
                    )

                w_id = f"workflow_{mod.name}_crud"
                workflows.append(
                    DiscoveredWorkflow(
                        workflow_id=w_id,
                        name=f"{mod.name.title()} Lifecycle Workflow",
                        description=(
                            f"Sequential lifecycle chain for {mod.name}: "
                            "create, retrieve, update, and delete"
                        ),
                        steps=steps,
                        status=VerificationStatus.INFERRED,
                        evidence=[
                            EvidenceReference(
                                source_type=EvidenceSourceType.OPENAPI,
                                source_ref=f"module.{mod.name}",
                                snippet=f"CRUD operations on module '{mod.name}'",
                                confidence=0.9,
                                explanation=(
                                    "Inferred from matching POST, GET, "
                                    "and DELETE operations"
                                ),
                            )
                        ],
                    )
                )

        return workflows

    def analyze_risk_areas(self, endpoints: list[DiscoveredEndpoint]) -> list[RiskArea]:
        """Identify potential security and API quality risk surfaces."""
        risks: list[RiskArea] = []

        # 1. Unauthenticated Mutations
        unauth_mutations = [
            f"{e.method} {e.path}"
            for e in endpoints
            if not e.requires_auth and e.method in ("POST", "PUT", "PATCH", "DELETE")
        ]
        if unauth_mutations:
            risks.append(
                RiskArea(
                    area_id="risk_unauthenticated_mutations",
                    category="security_authentication",
                    description=(
                        f"Found {len(unauth_mutations)} mutating operations "
                        "that do not require authentication."
                    ),
                    affected_endpoints=unauth_mutations[:10],
                    severity="HIGH",
                    evidence=[
                        EvidenceReference(
                            source_type=EvidenceSourceType.OPENAPI,
                            source_ref="security",
                            snippet=f"{len(unauth_mutations)} unauth endpoints",
                            confidence=1.0,
                            explanation=(
                                "OpenAPI operations have empty or missing "
                                "security requirement"
                            ),
                        )
                    ],
                )
            )

        # 2. Endpoints without Response Schemas (Documentation gap)
        no_schema_eps = [
            f"{e.method} {e.path}" for e in endpoints if not e.response_schema_refs
        ]
        if no_schema_eps:
            risks.append(
                RiskArea(
                    area_id="risk_undocumented_response_schemas",
                    category="documentation_gap",
                    description=(
                        f"{len(no_schema_eps)} operations lack documented 2xx schemas, "
                        "preventing deterministic schema assertions."
                    ),
                    affected_endpoints=no_schema_eps[:10],
                    severity="MEDIUM",
                    evidence=[
                        EvidenceReference(
                            source_type=EvidenceSourceType.OPENAPI,
                            source_ref="responses",
                            snippet=f"{len(no_schema_eps)} operations lack schema",
                            confidence=0.95,
                            explanation="Missing schema in OpenAPI responses",
                        )
                    ],
                )
            )

        # 3. Dynamic path ID with GET (potential IDOR candidate)
        idor_candidates = [
            f"{e.method} {e.path}"
            for e in endpoints
            if "{" in e.path
            and e.method == "GET"
            and any(
                p.get("name") in ("id", "userId", "customerId") for p in e.parameters
            )
        ]
        if idor_candidates:
            risks.append(
                RiskArea(
                    area_id="risk_idor_candidate_endpoints",
                    category="security_idor",
                    description=(
                        f"Found {len(idor_candidates)} GET endpoints accepting "
                        "resource ID parameters to verify for horizontal "
                        "authorization boundaries."
                    ),
                    affected_endpoints=idor_candidates[:10],
                    severity="HIGH",
                    evidence=[
                        EvidenceReference(
                            source_type=EvidenceSourceType.OPENAPI,
                            source_ref="parameters",
                            snippet=f"{len(idor_candidates)} ID-based GET endpoints",
                            confidence=0.85,
                            explanation="Path parameter implies entity lookup",
                        )
                    ],
                )
            )

        return risks

    def analyze_test_data_requirements(
        self, endpoints: list[DiscoveredEndpoint]
    ) -> list[TestDataRequirement]:
        """Extract input parameters and body schemas for valid test synthesis."""
        requirements: list[TestDataRequirement] = []
        seen_fields: set[str] = set()

        for ep in endpoints:
            for param in ep.parameters:
                p_name = param.get("name", "")
                if p_name and p_name not in seen_fields:
                    seen_fields.add(p_name)
                    p_schema = param.get("schema", {}) or {}
                    p_type = p_schema.get("type", "string")
                    example = (
                        1
                        if p_type == "integer"
                        else ("sample_param" if p_type == "string" else True)
                    )
                    requirements.append(
                        TestDataRequirement(
                            field_name=p_name,
                            entity=ep.inferred_module,
                            data_type=p_type,
                            example_value=example,
                            constraints={
                                "location": param.get("location", "query"),
                                "required": param.get("required", False),
                            },
                            evidence=[
                                EvidenceReference(
                                    source_type=EvidenceSourceType.OPENAPI,
                                    source_ref=f"paths.{ep.path}.parameters.{p_name}",
                                    snippet=f"Parameter '{p_name}' ({p_type})",
                                    confidence=1.0,
                                    explanation="Extracted from parameter declaration",
                                )
                            ],
                        )
                    )

        return requirements

    @classmethod
    def analyze_file(cls, spec_path: str | Path) -> ProjectIntelligenceReport:
        """Construct analyzer from OpenAPI file path and run complete analysis."""
        return cls.from_file(str(spec_path)).analyze()

    def analyze(self) -> ProjectIntelligenceReport:
        """Execute full deterministic OpenAPI analysis and return report."""
        project = self.extract_project_identity()
        endpoints, modules, missing = self.analyze_endpoints_and_modules()
        entities, relationships = self.analyze_entities_and_relationships(endpoints)
        auth = self.analyze_authentication(endpoints)
        workflows = self.analyze_workflows(modules, endpoints)
        risk_areas = self.analyze_risk_areas(endpoints)
        test_data_reqs = self.analyze_test_data_requirements(endpoints)

        return ProjectIntelligenceReport(
            project=project,
            endpoints=endpoints,
            modules=modules,
            entities=entities,
            relationships=relationships,
            auth=auth,
            workflows=workflows,
            test_data_requirements=test_data_reqs,
            risk_areas=risk_areas,
            missing_information=missing,
        )
