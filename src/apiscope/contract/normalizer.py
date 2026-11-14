"""Contract Normalizer converting raw OpenAPI specifications into canonical contracts.

Resolves local references with cycle bounds, normalizes parameters, content types,
response status codes, request bodies, and incorporates verified Project Intelligence
findings into a canonical model without inventing artificial constraints.
"""

from __future__ import annotations

import re
from typing import Any

from apiscope.contract.models import (
    CanonicalContract,
    CanonicalOperation,
    CanonicalParameter,
    CanonicalRequestBody,
    CanonicalResponse,
)
from apiscope.core.exceptions import DefinitionError
from apiscope.intelligence.models import (
    EvidenceReference,
    EvidenceSourceType,
    ProjectIntelligenceReport,
    VerificationStatus,
)

MAX_NORMALIZATION_DEPTH: int = 15
HTTP_METHODS: frozenset[str] = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options"}
)
GENERIC_TAG_PATTERNS: tuple[str, ...] = (
    "controller",
    "controllers",
    "api",
    "apis",
    "default",
    "service",
    "services",
    "handler",
    "handlers",
)
SKIP_PATH_SEGMENTS: frozenset[str] = frozenset(
    {"api", "v1", "v2", "v3", "v4", "v0", "rest", "app"}
)


def _infer_module_from_path(path: str) -> str:
    """Infer functional module name from URL path structure."""
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
        cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", filtered[0].strip().lower()).strip("_")
        return cleaned or "default"
    return "default"


def _clean_ref_name(ref: str) -> str:
    """Extract clean component schema name from $ref path."""
    return ref.lstrip("#").strip("/").split("/")[-1]


def _extract_path_param_names(path: str) -> list[str]:
    """Extract parameter variable names from a URL path template (e.g. '{id}')."""
    return re.findall(r"\{([^}]+)\}", path)


def _safe_resolve_pointer(root: dict[str, Any], ref: str) -> Any:
    """Resolve JSON Pointer within root dictionary safely."""
    if not ref.startswith("#"):
        return None
    parts = ref.lstrip("#").strip("/").split("/")
    current: Any = root
    for part in parts:
        if not part:
            continue
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def safe_resolve_schema(
    schema: Any,
    root: dict[str, Any],
    visited: set[str] | None = None,
    depth: int = 0,
) -> dict[str, Any]:
    """Recursively resolve schema references with cycle bounds."""
    if not isinstance(schema, dict):
        return {}

    if depth > MAX_NORMALIZATION_DEPTH:
        return {"_bounded_depth": True, **schema}

    curr_visited = set(visited or set())

    if "$ref" in schema:
        ref = str(schema["$ref"]).strip()
        if ref in curr_visited:
            # Self-reference or circular reference encountered; bound recursion safely
            return {
                "_circular_ref": True,
                "$ref": ref,
                "name": _clean_ref_name(ref),
            }

        target = _safe_resolve_pointer(root, ref)
        if target is None or not isinstance(target, dict):
            # Target cannot be resolved; return stub marking unresolvable ref
            return {"_unresolved_ref": ref, "$ref": ref}

        next_visited = curr_visited | {ref}
        resolved = safe_resolve_schema(
            target, root, visited=next_visited, depth=depth + 1
        )
        # Merge sibling properties over target if present
        res = dict(resolved)
        for k, v in schema.items():
            if k != "$ref":
                res[k] = v
        res["_original_ref"] = ref
        return res

    result: dict[str, Any] = {}
    for k, v in schema.items():
        if k in ("properties", "patternProperties") and isinstance(v, dict):
            result[k] = {
                pk: safe_resolve_schema(pv, root, visited=curr_visited, depth=depth + 1)
                for pk, pv in v.items()
            }
        elif k == "items" and isinstance(v, dict):
            result[k] = safe_resolve_schema(
                v, root, visited=curr_visited, depth=depth + 1
            )
        elif k in ("allOf", "anyOf", "oneOf") and isinstance(v, list):
            result[k] = [
                safe_resolve_schema(item, root, visited=curr_visited, depth=depth + 1)
                for item in v
                if isinstance(item, dict)
            ]
        else:
            result[k] = v

    return result


class ContractNormalizer:
    """Normalizes raw OpenAPI specifications into canonical, verifiable contracts."""

    def __init__(
        self,
        raw_spec: dict[str, Any],
        intelligence_report: ProjectIntelligenceReport | None = None,
    ) -> None:
        """Initialize normalizer with raw spec dict and optional intelligence."""
        if not isinstance(raw_spec, dict):
            raise DefinitionError("OpenAPI specification must be a dictionary")

        self.root = raw_spec
        self.intelligence = intelligence_report
        self.components = raw_spec.get("components", {}) or {}
        self.raw_schemas = self.components.get("schemas", {}) or {}
        self.raw_parameters = self.components.get("parameters", {}) or {}
        self.raw_security_schemes = self.components.get("securitySchemes", {}) or {}
        self.global_security = raw_spec.get("security", []) or []

        # Index intelligence report endpoints if available
        self._intel_endpoints: dict[str, Any] = {}
        if intelligence_report is not None:
            for ep in intelligence_report.endpoints:
                sig = f"{ep.method.upper()} {ep.path}"
                self._intel_endpoints[sig] = ep

    def normalize(self) -> CanonicalContract:
        """Produce a complete CanonicalContract from the specification."""
        info = self.root.get("info", {}) or {}
        title = str(info.get("title") or "API Contract").strip()
        version = str(info.get("version") or "1.0.0").strip()
        openapi_version = str(self.root.get("openapi") or "3.0.0").strip()

        servers = [
            str(s.get("url", ""))
            for s in self.root.get("servers", [])
            if isinstance(s, dict) and s.get("url")
        ]

        normalized_schemas: dict[str, Any] = {}
        for s_name, s_def in self.raw_schemas.items():
            if isinstance(s_def, dict):
                normalized_schemas[s_name] = safe_resolve_schema(s_def, self.root)

        operations: list[CanonicalOperation] = []
        modules: dict[str, list[str]] = {}

        raw_paths = self.root.get("paths", {}) or {}
        for path_str, path_item in raw_paths.items():
            if not isinstance(path_item, dict):
                continue

            path_params = path_item.get("parameters", []) or []

            for method_str, op_data in path_item.items():
                m_low = method_str.lower()
                if m_low not in HTTP_METHODS or not isinstance(op_data, dict):
                    continue

                op = self._normalize_operation(
                    method=m_low.upper(),
                    path=path_str,
                    op_data=op_data,
                    path_level_params=path_params,
                )
                operations.append(op)
                modules.setdefault(op.module, []).append(f"{op.method} {op.path}")

        evidence = [
            EvidenceReference(
                source_type=EvidenceSourceType.OPENAPI,
                source_ref="spec:root",
                snippet=f"{title} v{version} ({len(operations)} operations)",
                confidence=1.0,
                explanation="Normalized directly from OpenAPI specification",
            )
        ]

        return CanonicalContract(
            title=title,
            version=version,
            openapi_version=openapi_version,
            servers=servers,
            operations=operations,
            modules=modules,
            schemas=normalized_schemas,
            security_schemes=dict(self.raw_security_schemes),
            global_security=list(self.global_security),
            evidence=evidence,
        )

    def _normalize_operation(
        self,
        method: str,
        path: str,
        op_data: dict[str, Any],
        path_level_params: list[Any],
    ) -> CanonicalOperation:
        """Normalize a single endpoint operation."""
        op_id = str(op_data.get("operationId") or "").strip()
        summary = str(op_data.get("summary") or "").strip()
        desc = str(op_data.get("description") or "").strip()
        deprecated = bool(op_data.get("deprecated", False))
        tags = [str(t).strip() for t in op_data.get("tags", []) if str(t).strip()]

        # Module resolution: intelligence report -> confirmed tags -> path inference
        sig = f"{method} {path}"
        intel_ep = self._intel_endpoints.get(sig)
        is_generic = not tags or any(
            any(g in t.lower() for g in GENERIC_TAG_PATTERNS) for t in tags
        )

        if intel_ep is not None:
            module = intel_ep.inferred_module
            module_status = intel_ep.module_status
        elif not is_generic and tags:
            clean_tag = re.sub(r"[^a-zA-Z0-9_]+", "_", tags[0].strip().lower()).strip(
                "_"
            )
            module = clean_tag or "default"
            module_status = VerificationStatus.CONFIRMED
        else:
            module = _infer_module_from_path(path)
            module_status = VerificationStatus.INFERRED

        # Authentication evaluation
        op_security = op_data.get("security")
        if op_security is not None:
            # Operation explicitly overrides security
            requires_auth = bool(op_security)
            sec_schemes = list(op_security)
        elif self.global_security:
            # Inherits global security
            requires_auth = True
            sec_schemes = list(self.global_security)
        else:
            requires_auth = False
            sec_schemes = []

        # Parameter normalization
        parameters: list[CanonicalParameter] = []
        combined_raw_params = list(path_level_params) + list(
            op_data.get("parameters", []) or []
        )
        seen_params: set[tuple[str, str]] = set()

        for p_raw in combined_raw_params:
            if not isinstance(p_raw, dict):
                continue

            # Resolve parameter-level $ref if present
            if "$ref" in p_raw:
                p_ref = str(p_raw["$ref"]).strip()
                resolved_p = _safe_resolve_pointer(self.root, p_ref)
                if isinstance(resolved_p, dict):
                    p_raw = resolved_p
                else:
                    # Unresolvable parameter reference
                    parameters.append(
                        CanonicalParameter(
                            name=_clean_ref_name(p_ref),
                            location="query",
                            schema={"_unresolved_ref": p_ref},
                            schema_ref=p_ref,
                        )
                    )
                    continue

            p_name = str(p_raw.get("name") or "").strip()
            p_in = str(p_raw.get("in") or "query").strip().lower()
            if not p_name or (p_name, p_in) in seen_params:
                continue
            seen_params.add((p_name, p_in))

            p_req = bool(p_raw.get("required", p_in == "path"))
            p_desc = str(p_raw.get("description") or "").strip()
            p_dep = bool(p_raw.get("deprecated", False))

            raw_schema = p_raw.get("schema", {}) or {}
            resolved_s = safe_resolve_schema(raw_schema, self.root)
            schema_ref = raw_schema.get("$ref")

            # Extract constraints
            constraints: dict[str, Any] = {}
            for c_field in (
                "minimum",
                "maximum",
                "exclusiveMinimum",
                "exclusiveMaximum",
                "minLength",
                "maxLength",
                "pattern",
                "enum",
                "minItems",
                "maxItems",
                "uniqueItems",
                "format",
            ):
                if c_field in resolved_s:
                    constraints[c_field] = resolved_s[c_field]

            parameters.append(
                CanonicalParameter(
                    name=p_name,
                    location=p_in,
                    required=p_req,
                    schema=resolved_s,
                    schema_ref=str(schema_ref) if schema_ref else None,
                    description=p_desc,
                    deprecated=p_dep,
                    constraints=constraints,
                    evidence=[
                        EvidenceReference(
                            source_type=EvidenceSourceType.OPENAPI,
                            source_ref=f"paths.{path}.{method.lower()}.parameters.{p_name}",
                            snippet=f"{p_in} param '{p_name}' (req: {p_req})",
                            confidence=1.0,
                            explanation="Normalized parameter declaration",
                        )
                    ],
                )
            )

        # Request Body normalization
        request_body: CanonicalRequestBody | None = None
        req_raw = op_data.get("requestBody")
        if isinstance(req_raw, dict):
            if "$ref" in req_raw:
                rb_ref = str(req_raw["$ref"]).strip()
                resolved_rb = _safe_resolve_pointer(self.root, rb_ref)
                if isinstance(resolved_rb, dict):
                    req_raw = resolved_rb
                else:
                    req_raw = {"_unresolved_ref": rb_ref}

            rb_req = bool(req_raw.get("required", False))
            rb_desc = str(req_raw.get("description") or "").strip()
            content = req_raw.get("content", {}) or {}
            content_types = list(content.keys())

            body_schema: dict[str, Any] = {}
            body_ref: str | None = None
            if content:
                first_media = next(iter(content.values()), {})
                if isinstance(first_media, dict):
                    raw_bs = first_media.get("schema", {}) or {}
                    body_schema = safe_resolve_schema(raw_bs, self.root)
                    if "$ref" in raw_bs:
                        body_ref = str(raw_bs["$ref"])

            request_body = CanonicalRequestBody(
                content_types=content_types,
                required=rb_req,
                schema=body_schema,
                schema_ref=body_ref,
                description=rb_desc,
                evidence=[
                    EvidenceReference(
                        source_type=EvidenceSourceType.OPENAPI,
                        source_ref=f"paths.{path}.{method.lower()}.requestBody",
                        snippet=f"content_types={content_types}, required={rb_req}",
                        confidence=1.0,
                        explanation="Normalized request body definition",
                    )
                ],
            )

        # Response normalization
        responses: dict[str, CanonicalResponse] = {}
        raw_responses = op_data.get("responses", {}) or {}
        for code_str, r_def in raw_responses.items():
            if not isinstance(r_def, dict):
                continue

            if "$ref" in r_def:
                r_ref = str(r_def["$ref"]).strip()
                resolved_r = _safe_resolve_pointer(self.root, r_ref)
                if isinstance(resolved_r, dict):
                    r_def = resolved_r
                else:
                    responses[str(code_str)] = CanonicalResponse(
                        status_code=str(code_str),
                        schema={"_unresolved_ref": r_ref},
                        schema_ref=r_ref,
                    )
                    continue

            r_desc = str(r_def.get("description") or "").strip()
            r_content = r_def.get("content", {}) or {}
            r_content_types = (
                list(r_content.keys()) if isinstance(r_content, dict) else []
            )

            is_empty = code_str in ("204", "304") or (
                not r_content_types and code_str.startswith("2")
            )

            r_schema: dict[str, Any] = {}
            r_schema_ref: str | None = None
            is_array = False
            item_schema_ref: str | None = None

            if r_content and isinstance(r_content, dict):
                first_media = next(iter(r_content.values()), {})
                if isinstance(first_media, dict):
                    raw_rs = first_media.get("schema", {}) or {}
                    r_schema = safe_resolve_schema(raw_rs, self.root)
                    if "$ref" in raw_rs:
                        r_schema_ref = str(raw_rs["$ref"])
                    elif raw_rs.get("type") == "array":
                        is_array = True
                        items_def = raw_rs.get("items", {}) or {}
                        if "$ref" in items_def:
                            item_schema_ref = str(items_def["$ref"])

            responses[str(code_str)] = CanonicalResponse(
                status_code=str(code_str),
                description=r_desc,
                content_types=r_content_types,
                schema=r_schema,
                schema_ref=r_schema_ref,
                is_array=is_array,
                item_schema_ref=item_schema_ref,
                is_empty_body=is_empty,
                evidence=[
                    EvidenceReference(
                        source_type=EvidenceSourceType.OPENAPI,
                        source_ref=f"paths.{path}.{method.lower()}.responses.{code_str}",
                        snippet=f"HTTP {code_str} ({r_desc[:50]})",
                        confidence=1.0,
                        explanation="Normalized response definition",
                    )
                ],
            )

        evidence = [
            EvidenceReference(
                source_type=EvidenceSourceType.OPENAPI,
                source_ref=f"paths.{path}.{method.lower()}",
                snippet=f"{method} {path} (operationId: '{op_id}')",
                confidence=1.0,
                explanation="Normalized canonical operation",
            )
        ]

        return CanonicalOperation(
            method=method,
            path=path,
            operation_id=op_id,
            summary=summary,
            description=desc,
            declared_tags=tags,
            module=module,
            module_status=module_status,
            deprecated=deprecated,
            requires_auth=requires_auth,
            security_schemes=sec_schemes,
            parameters=parameters,
            request_body=request_body,
            responses=responses,
            evidence=evidence,
        )
