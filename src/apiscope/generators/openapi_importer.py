"""OpenAPI 3.0 / 3.1 specification importer and normalized domain models.

Parses OpenAPI JSON and YAML specifications, safely resolves local component
references ($ref), validates constraints, extracts endpoint operations, and
determines authentication and module assignments.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from apiscope.core.exceptions import DefinitionError, PathTraversalError
from apiscope.definitions.collection import validate_safe_path

MAX_OPENAPI_FILE_BYTES: int = 10_485_760  # 10 MB
MAX_REF_DEPTH: int = 25
MAX_GENERATED_TESTS: int = 2000

_VALID_HTTP_METHODS: frozenset[str] = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options"}
)


def _slugify(text: str) -> str:
    """Normalize text into a lowercase URL/file-friendly slug."""
    text_clean = str(text).strip().lower()
    slug = re.sub(r"[^a-z0-9_-]+", "-", text_clean).strip("-")
    return slug or "default"


def _extract_module(path: str, tags: list[str]) -> str:
    """Infer module name from OpenAPI operation tags or path structure.

    Args:
        path: Endpoint path (e.g. '/orders/{order_id}').
        tags: List of operation tags.

    Returns:
        Slugified module identifier.
    """
    if tags:
        first_tag = str(tags[0]).strip()
        if first_tag:
            return _slugify(first_tag)

    clean_path = path.strip("/")
    segments = clean_path.split("/") if clean_path else []
    filtered: list[str] = []
    for seg in segments:
        seg_lower = seg.lower()
        if seg_lower in ("api", "v1", "v2", "v3", "v4") or re.match(
            r"^v\d+$", seg_lower
        ):
            continue
        filtered.append(seg)

    if filtered:
        first_seg = re.sub(r"[{}]", "", filtered[0])
        slug = _slugify(first_seg)
        if slug:
            return slug

    return "default"


def _lookup_json_pointer(root: dict[str, Any], ref: str) -> Any:
    """Resolve an internal JSON Pointer (#/components/schemas/...) in the spec.

    Args:
        root: Root dictionary of the OpenAPI document.
        ref: JSON pointer reference string starting with '#'.

    Returns:
        The referenced dictionary, schema, or object.

    Raises:
        DefinitionError: If the pointer cannot be resolved or is invalid.
    """
    if not ref.startswith("#"):
        raise DefinitionError(f"Unsupported or unresolvable $ref: '{ref}'")

    parts = ref.lstrip("#").strip("/").split("/")
    current: Any = root
    for part in parts:
        if not part:
            continue
        # Unescape RFC 6901 JSON pointer tokens
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            raise DefinitionError(
                f"Unsupported or unresolvable $ref: '{ref}' (segment '{key}' not found)"
            )
        current = current[key]
    return current


def _resolve_refs(
    node: Any,
    root: dict[str, Any],
    visited: set[str] | None = None,
    depth: int = 0,
) -> Any:
    """Recursively resolve internal JSON Pointer references in a schema node.

    Args:
        node: Target node (dict, list, or primitive value).
        root: Root OpenAPI dictionary for reference lookup.
        visited: Set of JSON pointer paths currently in the active recursion chain.
        depth: Current recursion depth.

    Returns:
        A deep-resolved copy of the node with internal references expanded.

    Raises:
        DefinitionError: If depth exceeds MAX_REF_DEPTH, a cyclic reference is
            detected, or an unresolvable reference is encountered.
    """
    if depth > MAX_REF_DEPTH:
        raise DefinitionError(
            "Circular or excessively deep $ref detected in OpenAPI specification"
        )

    if isinstance(node, dict):
        if "$ref" in node:
            ref = str(node["$ref"]).strip()
            if not ref.startswith("#"):
                raise DefinitionError(f"Unsupported or unresolvable $ref: '{ref}'")

            current_visited = visited or set()
            if ref in current_visited:
                raise DefinitionError(
                    "Circular or excessively deep $ref detected in "
                    "OpenAPI specification"
                )

            target = _lookup_json_pointer(root, ref)
            new_visited = current_visited | {ref}
            resolved_target = _resolve_refs(target, root, new_visited, depth=depth + 1)

            if isinstance(resolved_target, dict):
                # Copy target and merge sibling properties defined on node
                result = dict(resolved_target)
                for k, v in node.items():
                    if k != "$ref":
                        result[k] = _resolve_refs(v, root, new_visited, depth=depth + 1)
                return result
            return resolved_target

        return {
            k: _resolve_refs(v, root, visited, depth=depth) for k, v in node.items()
        }

    if isinstance(node, list):
        return [_resolve_refs(item, root, visited, depth=depth) for item in node]

    return node


@dataclass(frozen=True)
class ParameterSpec:
    """Normalized specification for an HTTP parameter (path, query, header, cookie)."""

    __test__ = False

    name: str
    location: str  # "path", "query", "header", "cookie"
    required: bool = False
    schema: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert parameter specification to a serializable dictionary."""
        return {
            "name": self.name,
            "location": self.location,
            "required": self.required,
            "schema": self.schema,
        }


@dataclass(frozen=True)
class EndpointSpec:
    """Normalized specification for a single HTTP operation on a path."""

    __test__ = False

    path: str
    method: str  # Uppercase: "GET", "POST", etc.
    operation_id: str = ""
    summary: str = ""
    module: str = "default"
    deprecated: bool = False
    requires_auth: bool = False
    security_schemes: list[dict[str, Any]] = field(default_factory=list)
    parameters: list[ParameterSpec] = field(default_factory=list)
    request_body_required: bool = False
    request_body_schema: dict[str, Any] | None = None
    success_status: int = 200
    response_schema: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert endpoint specification to a serializable dictionary."""
        return {
            "path": self.path,
            "method": self.method,
            "operation_id": self.operation_id,
            "summary": self.summary,
            "module": self.module,
            "deprecated": self.deprecated,
            "requires_auth": self.requires_auth,
            "security_schemes": self.security_schemes,
            "parameters": [p.to_dict() for p in self.parameters],
            "request_body_required": self.request_body_required,
            "request_body_schema": self.request_body_schema,
            "success_status": self.success_status,
            "response_schema": self.response_schema,
        }


@dataclass(frozen=True)
class OpenAPISpec:
    """Normalized in-memory model of an imported OpenAPI 3 specification."""

    __test__ = False

    title: str
    version: str
    openapi_version: str
    servers: list[str] = field(default_factory=list)
    endpoints: list[EndpointSpec] = field(default_factory=list)
    path_methods: dict[str, list[str]] = field(default_factory=dict)

    def filter_by_module(self, module: str) -> list[EndpointSpec]:
        """Filter endpoints matching a specified module name.

        Args:
            module: Target module name (case-insensitive).

        Returns:
            List of matching EndpointSpec instances.
        """
        target = module.strip().lower()
        return [ep for ep in self.endpoints if ep.module.lower() == target]

    def to_dict(self) -> dict[str, Any]:
        """Convert OpenAPI specification to a serializable dictionary."""
        return {
            "title": self.title,
            "version": self.version,
            "openapi_version": self.openapi_version,
            "servers": list(self.servers),
            "endpoints": [ep.to_dict() for ep in self.endpoints],
            "path_methods": {k: list(v) for k, v in self.path_methods.items()},
        }


def parse_openapi_spec(data: Any) -> OpenAPISpec:
    """Parse and normalize an in-memory OpenAPI 3.0 / 3.1 specification dictionary.

    Args:
        data: Parsed dictionary from an OpenAPI JSON/YAML file.

    Returns:
        Normalized OpenAPISpec domain instance.

    Raises:
        DefinitionError: If the document is malformed, missing required elements,
            contains invalid versions, or has unresolvable references.
    """
    if not isinstance(data, dict):
        raise DefinitionError("OpenAPI specification must be a dictionary mapping.")

    openapi_ver = data.get("openapi")
    if not openapi_ver or not isinstance(openapi_ver, str):
        raise DefinitionError(
            "Unsupported or missing OpenAPI version; expected 3.0.x or 3.1.x"
        )
    clean_ver = openapi_ver.strip()
    if not (clean_ver.startswith("3.0") or clean_ver.startswith("3.1")):
        raise DefinitionError(
            f"Unsupported or missing OpenAPI version '{clean_ver}'; "
            "expected 3.0.x or 3.1.x"
        )

    info = data.get("info")
    if not isinstance(info, dict):
        raise DefinitionError("Missing or invalid 'info' in OpenAPI specification.")
    title = str(info.get("title", "")).strip()
    version = str(info.get("version", "")).strip()
    if not title or not version:
        raise DefinitionError(
            "OpenAPI 'info' must contain non-empty 'title' and 'version'."
        )

    paths_dict = data.get("paths")
    if not isinstance(paths_dict, dict) or len(paths_dict) == 0:
        raise DefinitionError(
            "Missing or invalid 'paths' in OpenAPI specification; "
            "must be a non-empty mapping."
        )

    # Extract servers
    servers_list = data.get("servers", [])
    servers: list[str] = []
    if isinstance(servers_list, list):
        for s in servers_list:
            if isinstance(s, dict) and "url" in s:
                servers.append(str(s["url"]))

    # Global security requirements and security scheme components
    components = data.get("components", {})
    if not isinstance(components, dict):
        components = {}
    raw_security_schemes = components.get("securitySchemes", {})
    if not isinstance(raw_security_schemes, dict):
        raw_security_schemes = {}

    resolved_security_schemes = {
        name: _resolve_refs(scheme, data)
        for name, scheme in raw_security_schemes.items()
    }

    spec_security = data.get("security")

    endpoints: list[EndpointSpec] = []
    path_methods: dict[str, list[str]] = {}

    for path, path_item in paths_dict.items():
        if not isinstance(path_item, dict):
            continue

        # Resolved path-level parameters
        raw_path_params = path_item.get("parameters", [])
        path_level_params: list[ParameterSpec] = []
        if isinstance(raw_path_params, list):
            for p in raw_path_params:
                resolved_p = _resolve_refs(p, data)
                if isinstance(resolved_p, dict) and "name" in resolved_p:
                    path_level_params.append(
                        ParameterSpec(
                            name=str(resolved_p["name"]),
                            location=str(resolved_p.get("in", "query")),
                            required=bool(resolved_p.get("required", False)),
                            schema=resolved_p.get("schema", {}),
                        )
                    )

        supported_methods: list[str] = []

        for key, operation in path_item.items():
            method_lower = key.lower()
            if method_lower not in _VALID_HTTP_METHODS:
                continue
            if not isinstance(operation, dict):
                continue

            method_upper = method_lower.upper()
            supported_methods.append(method_upper)

            operation_id = str(operation.get("operationId", "")).strip()
            summary = str(operation.get("summary", "")).strip()
            deprecated = bool(operation.get("deprecated", False))

            raw_tags = operation.get("tags", [])
            tags_list = [str(t) for t in raw_tags] if isinstance(raw_tags, list) else []
            module = _extract_module(path, tags_list)

            # Security determination
            op_security = operation.get("security")
            if op_security is not None:
                requires_auth = bool(isinstance(op_security, list) and op_security)
                active_security_reqs = (
                    op_security if isinstance(op_security, list) else []
                )
            else:
                requires_auth = bool(isinstance(spec_security, list) and spec_security)
                active_security_reqs = (
                    spec_security if isinstance(spec_security, list) else []
                )

            matched_schemes: list[dict[str, Any]] = []
            for sec_req in active_security_reqs:
                if isinstance(sec_req, dict):
                    for scheme_name in sec_req:
                        if scheme_name in resolved_security_schemes:
                            scheme_def = dict(resolved_security_schemes[scheme_name])
                            scheme_def["_name"] = scheme_name
                            matched_schemes.append(scheme_def)

            # Parameters merging: operation-level overrides path-level
            merged_params_dict: dict[tuple[str, str], ParameterSpec] = {
                (p.name, p.location): p for p in path_level_params
            }

            raw_op_params = operation.get("parameters", [])
            if isinstance(raw_op_params, list):
                for p in raw_op_params:
                    resolved_p = _resolve_refs(p, data)
                    if isinstance(resolved_p, dict) and "name" in resolved_p:
                        spec_p = ParameterSpec(
                            name=str(resolved_p["name"]),
                            location=str(resolved_p.get("in", "query")),
                            required=bool(resolved_p.get("required", False)),
                            schema=resolved_p.get("schema", {}),
                        )
                        merged_params_dict[(spec_p.name, spec_p.location)] = spec_p

            parameters = list(merged_params_dict.values())

            # Request body extraction
            raw_req_body = operation.get("requestBody")
            request_body_required = False
            request_body_schema: dict[str, Any] | None = None
            if raw_req_body is not None:
                resolved_req_body = _resolve_refs(raw_req_body, data)
                if isinstance(resolved_req_body, dict):
                    request_body_required = bool(
                        resolved_req_body.get("required", False)
                    )
                    content = resolved_req_body.get("content", {})
                    if isinstance(content, dict) and "application/json" in content:
                        json_content = content["application/json"]
                        if isinstance(json_content, dict):
                            request_body_schema = json_content.get("schema")

            # Responses and success status extraction
            raw_responses = operation.get("responses", {})
            success_status = 200
            response_schema: dict[str, Any] | None = None

            if isinstance(raw_responses, dict):
                status_candidates: list[int] = []
                for s_key in raw_responses:
                    s_str = str(s_key).strip()
                    if s_str.isdigit():
                        code = int(s_str)
                        if 200 <= code <= 299:
                            status_candidates.append(code)

                if status_candidates:
                    status_candidates.sort()
                    success_status = status_candidates[0]
                elif "default" in raw_responses:
                    success_status = 200

                target_resp = raw_responses.get(
                    str(success_status), raw_responses.get("default")
                )
                if target_resp is not None:
                    resolved_resp = _resolve_refs(target_resp, data)
                    if isinstance(resolved_resp, dict):
                        content = resolved_resp.get("content", {})
                        if isinstance(content, dict) and "application/json" in content:
                            json_resp = content["application/json"]
                            if isinstance(json_resp, dict):
                                response_schema = json_resp.get("schema")

            endpoint = EndpointSpec(
                path=path,
                method=method_upper,
                operation_id=operation_id,
                summary=summary,
                module=module,
                deprecated=deprecated,
                requires_auth=requires_auth,
                security_schemes=matched_schemes,
                parameters=parameters,
                request_body_required=request_body_required,
                request_body_schema=request_body_schema,
                success_status=success_status,
                response_schema=response_schema,
            )
            endpoints.append(endpoint)

        if supported_methods:
            supported_methods.sort()
            path_methods[path] = supported_methods

    # Sort endpoints deterministically by (path, method)
    endpoints.sort(key=lambda ep: (ep.path, ep.method))

    return OpenAPISpec(
        title=title,
        version=version,
        openapi_version=clean_ver,
        servers=servers,
        endpoints=endpoints,
        path_methods=path_methods,
    )


def load_openapi_spec(
    file_path: str | Path,
    base_dir: Path | None = None,
) -> OpenAPISpec:
    """Load and parse an OpenAPI specification file safely.

    Args:
        file_path: Path to the OpenAPI YAML or JSON specification.
        base_dir: Allowed root directory for path isolation. If None, checks
            for directory traversal components against current working directory.

    Returns:
        Validated OpenAPISpec instance.

    Raises:
        DefinitionError: If the file is missing, exceeds size limits, or is malformed.
        PathTraversalError: If the file path attempts directory traversal.
    """
    ref_str = str(file_path).strip()
    normalized = ref_str.replace("\\", "/")

    if (
        "../" in normalized
        or normalized.startswith("../")
        or normalized == ".."
        or "/../" in normalized
        or normalized.endswith("/..")
    ):
        raise PathTraversalError(
            f"Forbidden directory traversal sequence detected in '{ref_str}'."
        )

    p = Path(ref_str)
    if ".." in p.parts:
        raise PathTraversalError(
            f"Forbidden directory traversal sequence detected in '{ref_str}'."
        )

    if base_dir is not None:
        safe_path = validate_safe_path(base_dir=base_dir, ref_path=file_path)
    else:
        effective_base = p.parent if p.is_absolute() else Path.cwd()
        safe_path = validate_safe_path(base_dir=effective_base, ref_path=file_path)

    if not safe_path.is_file():
        raise DefinitionError(f"OpenAPI spec file not found: {file_path}")

    file_size = safe_path.stat().st_size
    if file_size > MAX_OPENAPI_FILE_BYTES:
        raise DefinitionError(
            f"OpenAPI spec file exceeds maximum size of {MAX_OPENAPI_FILE_BYTES} bytes."
        )

    try:
        content = safe_path.read_text(encoding="utf-8-sig")
    except Exception as err:
        raise DefinitionError(
            f"Error reading OpenAPI specification '{file_path}': {err}"
        ) from err

    try:
        data = yaml.safe_load(content)
    except Exception as err:
        raise DefinitionError(
            f"Malformed YAML/JSON in OpenAPI specification '{file_path}': {err}"
        ) from err

    return parse_openapi_spec(data)
