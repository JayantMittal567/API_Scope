"""Rule-based API test generator for ApiScope.

Synthesizes positive, negative (missing required fields, invalid types, 404,
missing auth, 405), and boundary tests directly from OpenAPI 3 specifications.
"""

from __future__ import annotations

import re
from typing import Any

from apiscope.core.exceptions import DefinitionError
from apiscope.core.models import (
    ApiKeyAuth,
    ApiKeyLocation,
    AuthConfig,
    BearerAuth,
    BodyType,
    HttpMethod,
)
from apiscope.definitions.models import (
    DefinitionSource,
    DefinitionStatus,
    TestDefinition,
    TestRequestDefinition,
)
from apiscope.generators.openapi_importer import (
    MAX_GENERATED_TESTS,
    EndpointSpec,
    OpenAPISpec,
)

_UNSUPPORTED_METHOD_CANDIDATES: list[str] = [
    "DELETE",
    "POST",
    "PUT",
    "PATCH",
    "GET",
]


def synthesize_valid_value(
    schema: dict[str, Any] | None, field_name: str = "value"
) -> Any:
    """Synthesize a valid sample value adhering to a JSON schema specification.

    Args:
        schema: Resolved JSON schema dictionary.
        field_name: Contextual field name used for string placeholders.

    Returns:
        Synthesized Python primitive, list, or dictionary conforming to schema.
    """
    if not schema or not isinstance(schema, dict):
        return "sample_value"

    if "allOf" in schema and isinstance(schema["allOf"], list) and schema["allOf"]:
        merged_schema: dict[str, Any] = {
            k: v for k, v in schema.items() if k != "allOf"
        }
        merged_props: dict[str, Any] = dict(merged_schema.get("properties", {}))
        merged_required: list[str] = list(merged_schema.get("required", []))
        for sub in schema["allOf"]:
            if isinstance(sub, dict):
                if "properties" in sub and isinstance(sub["properties"], dict):
                    merged_props.update(sub["properties"])
                if "required" in sub and isinstance(sub["required"], list):
                    for req in sub["required"]:
                        if req not in merged_required:
                            merged_required.append(req)
                for sk, sv in sub.items():
                    if (
                        sk not in ("properties", "required", "allOf")
                        and sk not in merged_schema
                    ):
                        merged_schema[sk] = sv
        if merged_props:
            merged_schema["type"] = "object"
            merged_schema["properties"] = merged_props
            merged_schema["required"] = merged_required
        return synthesize_valid_value(merged_schema, field_name=field_name)

    if "oneOf" in schema and isinstance(schema["oneOf"], list) and schema["oneOf"]:
        return synthesize_valid_value(schema["oneOf"][0], field_name=field_name)

    if "anyOf" in schema and isinstance(schema["anyOf"], list) and schema["anyOf"]:
        return synthesize_valid_value(schema["anyOf"][0], field_name=field_name)

    if "default" in schema:
        return schema["default"]

    if "example" in schema:
        return schema["example"]

    if (
        "examples" in schema
        and isinstance(schema["examples"], list)
        and schema["examples"]
    ):
        return schema["examples"][0]

    if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
        return schema["enum"][0]

    schema_type = schema.get("type")

    if schema_type == "integer":
        min_val = schema.get("minimum")
        max_val = schema.get("maximum")
        if min_val is not None:
            val = int(min_val)
            if max_val is not None and val > int(max_val):
                val = int(max_val)
            return val
        if max_val is not None:
            return min(1, int(max_val))
        return 1

    if schema_type == "number":
        min_val = schema.get("minimum")
        if min_val is not None:
            return float(min_val)
        max_val = schema.get("maximum")
        if max_val is not None:
            return min(1.0, float(max_val))
        return 1.0

    if schema_type == "boolean":
        return True

    if schema_type == "string":
        min_len = int(schema.get("minLength", 0))
        max_len = schema.get("maxLength")
        candidate = f"test_{field_name}"
        if min_len > len(candidate):
            candidate = candidate + ("a" * (min_len - len(candidate)))
        if max_len is not None and len(candidate) > int(max_len):
            candidate = candidate[: int(max_len)]
            if len(candidate) < min_len:
                candidate = "a" * min_len
        return candidate

    if schema_type == "array":
        item_schema = schema.get("items", {"type": "string"})
        return [synthesize_valid_value(item_schema, "item")]

    if schema_type == "object" or (schema_type is None and "properties" in schema):
        props = schema.get("properties", {})
        result: dict[str, Any] = {}
        if isinstance(props, dict):
            for prop_k, prop_s in props.items():
                result[prop_k] = synthesize_valid_value(prop_s, prop_k)
        return result

    return "test_value"


class RuleBasedGenerator:
    """Generates structured, executable TestDefinition suites from OpenAPISpec."""

    __test__ = False

    def __init__(self, max_generated_tests: int = MAX_GENERATED_TESTS) -> None:
        self.max_generated_tests = max_generated_tests

    def generate_tests(
        self,
        spec: OpenAPISpec,
        module: str | None = None,
        base_url_var: str = "{{base_url}}",
        include_crud_chains: bool = False,
    ) -> list[TestDefinition]:
        """Generate positive, negative, and boundary test definitions.

        Args:
            spec: Normalized OpenAPISpec instance.
            module: Optional module filter. If provided, tests are only generated
                for matching endpoints.
            base_url_var: Base URL variable placeholder (default '{{base_url}}').
            include_crud_chains: Whether to include automatic Producer-Consumer
                CRUD chaining tests.

        Returns:
            List of generated TestDefinition instances.

        Raises:
            DefinitionError: If generated test count exceeds MAX_GENERATED_TESTS.
        """
        endpoints = spec.filter_by_module(module) if module else list(spec.endpoints)

        tests: list[TestDefinition] = []

        # 1. Positive Tests (FR-20)
        for ep in endpoints:
            tests.append(self._generate_positive_test(ep, base_url_var))

        # 2. Negative Tests (FR-21)
        for ep in endpoints:
            tests.extend(self._generate_negative_tests(ep, base_url_var))

        # 2e. Unsupported HTTP Method (FR-21 Category 2e) per path
        tests.extend(
            self._generate_unsupported_method_tests(spec, endpoints, base_url_var)
        )

        # 3. Boundary Tests (FR-22)
        for ep in endpoints:
            tests.extend(self._generate_boundary_tests(ep, base_url_var))

        # 4. CRUD Chaining Tests (FR-16, FR-20 Upgrade)
        if include_crud_chains:
            tests.extend(
                self.generate_crud_chains(
                    spec=spec, module=module, base_url_var=base_url_var
                )
            )

        if len(tests) > self.max_generated_tests:
            raise DefinitionError(
                f"Generated test count ({len(tests)}) exceeds maximum limit "
                f"of {self.max_generated_tests}."
            )

        return tests

    def generate_crud_chains(
        self,
        spec: OpenAPISpec,
        module: str | None = None,
        base_url_var: str = "{{base_url}}",
    ) -> list[TestDefinition]:
        """Automatically synthesize Producer-Consumer 2-step CRUD chaining tests.

        Detects pairs where a POST endpoint creates a resource and returns
        an identifier, and a GET endpoint consumes that identifier as a path parameter.

        Args:
            spec: Normalized OpenAPISpec instance.
            module: Optional module filter.
            base_url_var: Base URL variable placeholder (default '{{base_url}}').

        Returns:
            List of paired chained TestDefinition instances.
        """
        import re

        endpoints = spec.filter_by_module(module) if module else list(spec.endpoints)
        producers = [
            ep for ep in endpoints if ep.method.upper() == "POST" and not ep.deprecated
        ]
        consumers = [
            ep for ep in endpoints if ep.method.upper() == "GET" and not ep.deprecated
        ]

        chained_tests: list[TestDefinition] = []

        for p in producers:
            p_path = p.path.rstrip("/")
            for c in consumers:
                if c.module.lower() != p.module.lower():
                    continue

                # Match consumer path pattern: p_path/{param_name}
                match = re.match(
                    r"^" + re.escape(p_path) + r"/\{([a-zA-Z0-9_]+)\}$",
                    c.path.rstrip("/"),
                )
                if not match:
                    continue

                param_name = match.group(1)

                # Determine identifier field in producer response schema
                id_field = "id"
                if p.response_schema and isinstance(p.response_schema, dict):
                    props = p.response_schema.get("properties", {})
                    if isinstance(props, dict):
                        if param_name in props:
                            id_field = param_name
                        elif "id" in props:
                            id_field = "id"
                        else:
                            matched_prop = next(
                                (k for k in props if k.endswith("_id") or k == "id"),
                                "id",
                            )
                            id_field = matched_prop

                # 1. Producer TestDefinition
                producer_base = self._generate_positive_test(p, base_url_var)
                step1_name = f"CHAIN {p.method} {p.path} -> extract {param_name}"
                step1 = TestDefinition(
                    name=step1_name,
                    module=p.module,
                    request=producer_base.request,
                    tags=["positive", "chain", "rule-generated"],
                    extract={f"chain_{param_name}": f"$.{id_field}"},
                    assertions=[{"type": "status", "equals": p.success_status}],
                    source=DefinitionSource.RULE_GENERATED,
                    status=DefinitionStatus.APPROVED,
                )

                # 2. Consumer TestDefinition
                consumer_path_token = f"{{{param_name}}}"
                chained_token = f"{{{{chain_{param_name}}}}}"
                chained_consumer_path = c.path.replace(
                    consumer_path_token, chained_token
                )
                consumer_url = f"{base_url_var}{chained_consumer_path}"
                auth_cfg, headers = self._resolve_auth(c)
                consumer_req = TestRequestDefinition(
                    method=HttpMethod.GET,
                    url=consumer_url,
                    headers=headers,
                    params={},
                    body=None,
                    auth=auth_cfg,
                )
                step2_name = f"CHAIN {c.method} {c.path} -> verify created {param_name}"
                step2 = TestDefinition(
                    name=step2_name,
                    module=c.module,
                    request=consumer_req,
                    tags=["positive", "chain", "rule-generated"],
                    extract={},
                    assertions=[{"type": "status", "equals": c.success_status}],
                    source=DefinitionSource.RULE_GENERATED,
                    status=DefinitionStatus.APPROVED,
                )

                chained_tests.extend([step1, step2])

        return chained_tests

    def _build_url_and_params(
        self,
        endpoint: EndpointSpec,
        base_url_var: str,
        path_override: dict[str, Any] | None = None,
        query_override: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Resolve endpoint URL path and query parameters."""
        resolved_path = endpoint.path
        query_params: dict[str, Any] = {}

        path_overrides = path_override or {}
        query_overrides = query_override or {}

        for param in endpoint.parameters:
            if param.location == "path":
                if param.name in path_overrides:
                    val = path_overrides[param.name]
                else:
                    val = synthesize_valid_value(param.schema, param.name)
                pattern = r"\{" + re.escape(param.name) + r"\}"
                resolved_path = re.sub(pattern, str(val), resolved_path)
            elif param.location == "query":
                if param.name in query_overrides:
                    query_params[param.name] = query_overrides[param.name]
                elif param.required:
                    query_params[param.name] = synthesize_valid_value(
                        param.schema, param.name
                    )

        for k, v in query_overrides.items():
            if k not in query_params:
                query_params[k] = v

        url = f"{base_url_var}{resolved_path}"
        return url, query_params

    def _resolve_auth(
        self, endpoint: EndpointSpec
    ) -> tuple[AuthConfig | None, dict[str, str]]:
        """Determine authentication headers and model for secured endpoints."""
        if not endpoint.requires_auth:
            return None, {}

        for scheme in endpoint.security_schemes:
            if scheme.get("type") == "apiKey":
                loc = scheme.get("in", "header").lower()
                name = scheme.get("name", "X-API-Key")
                if loc == "header":
                    return (
                        ApiKeyAuth(
                            name=name,
                            value="{{api_key}}",
                            location=ApiKeyLocation.HEADER,
                        ),
                        {name: "{{api_key}}"},
                    )
                elif loc == "query":
                    return (
                        ApiKeyAuth(
                            name=name,
                            value="{{api_key}}",
                            location=ApiKeyLocation.QUERY,
                        ),
                        {},
                    )

        # Default Bearer Auth
        return (
            BearerAuth(token="{{auth_token}}"),
            {"Authorization": "Bearer {{auth_token}}"},
        )

    def _generate_positive_test(
        self, endpoint: EndpointSpec, base_url_var: str
    ) -> TestDefinition:
        """Generate a positive test definition for an endpoint (FR-20)."""
        url, params = self._build_url_and_params(endpoint, base_url_var)
        auth_config, headers = self._resolve_auth(endpoint)

        body: Any = None
        if endpoint.request_body_schema:
            headers["Content-Type"] = "application/json"
            body = synthesize_valid_value(endpoint.request_body_schema)

        assertions: list[dict[str, Any]] = [
            {"type": "status", "equals": endpoint.success_status}
        ]

        if endpoint.response_schema is not None and endpoint.success_status != 204:
            assertions.append(
                {"type": "json_schema", "schema": endpoint.response_schema}
            )

        req_def = TestRequestDefinition(
            method=HttpMethod.from_string(endpoint.method),
            url=url,
            headers=headers,
            params=params,
            body=body,
            body_type=BodyType.JSON if body is not None else BodyType.JSON,
            auth=auth_config,
        )

        return TestDefinition(
            name=f"{endpoint.method} {endpoint.path} - positive",
            module=endpoint.module,
            request=req_def,
            tags=["positive", "rule-generated"],
            assertions=assertions,
            source=DefinitionSource.RULE_GENERATED,
            status=DefinitionStatus.APPROVED,
        )

    def _generate_negative_tests(
        self, endpoint: EndpointSpec, base_url_var: str
    ) -> list[TestDefinition]:
        """Generate negative tests covering categories 2a, 2b, 2c, 2d (FR-21)."""
        tests: list[TestDefinition] = []
        url, params = self._build_url_and_params(endpoint, base_url_var)
        auth_config, headers = self._resolve_auth(endpoint)

        # 2a. Missing Required Fields
        if endpoint.request_body_schema and isinstance(
            endpoint.request_body_schema, dict
        ):
            base_body = synthesize_valid_value(endpoint.request_body_schema)
            required_props = endpoint.request_body_schema.get("required", [])
            if isinstance(required_props, list) and isinstance(base_body, dict):
                for req_field in required_props:
                    if req_field in base_body:
                        invalid_body = dict(base_body)
                        del invalid_body[req_field]

                        missing_hdrs = dict(headers)
                        missing_hdrs["Content-Type"] = "application/json"

                        req_def = TestRequestDefinition(
                            method=HttpMethod.from_string(endpoint.method),
                            url=url,
                            headers=missing_hdrs,
                            params=dict(params),
                            body=invalid_body,
                            body_type=BodyType.JSON,
                            auth=auth_config,
                        )
                        tests.append(
                            TestDefinition(
                                name=(
                                    f"{endpoint.method} {endpoint.path} - "
                                    f"missing required field '{req_field}'"
                                ),
                                module=endpoint.module,
                                request=req_def,
                                tags=["negative", "rule-generated"],
                                assertions=[{"type": "status", "range": [400, 422]}],
                                source=DefinitionSource.RULE_GENERATED,
                                status=DefinitionStatus.APPROVED,
                            )
                        )

        # 2b. Wrong Field Types
        if endpoint.request_body_schema and isinstance(
            endpoint.request_body_schema, dict
        ):
            base_body = synthesize_valid_value(endpoint.request_body_schema)
            props = endpoint.request_body_schema.get("properties", {})
            if isinstance(props, dict) and isinstance(base_body, dict):
                for prop_name, prop_schema in props.items():
                    if not isinstance(prop_schema, dict):
                        continue
                    p_type = prop_schema.get("type")
                    if p_type in ("integer", "number", "boolean", "array", "object"):
                        invalid_val: Any = "INVALID_TYPE_STRING"
                    elif p_type == "string":
                        invalid_val = 12345
                    else:
                        continue

                    invalid_body = dict(base_body)
                    invalid_body[prop_name] = invalid_val

                    wrong_hdrs = dict(headers)
                    wrong_hdrs["Content-Type"] = "application/json"

                    req_def = TestRequestDefinition(
                        method=HttpMethod.from_string(endpoint.method),
                        url=url,
                        headers=wrong_hdrs,
                        params=dict(params),
                        body=invalid_body,
                        body_type=BodyType.JSON,
                        auth=auth_config,
                    )
                    tests.append(
                        TestDefinition(
                            name=(
                                f"{endpoint.method} {endpoint.path} - "
                                f"invalid type for '{prop_name}'"
                            ),
                            module=endpoint.module,
                            request=req_def,
                            tags=["negative", "rule-generated"],
                            assertions=[{"type": "status", "range": [400, 422]}],
                            source=DefinitionSource.RULE_GENERATED,
                            status=DefinitionStatus.APPROVED,
                        )
                    )

        # 2c. Invalid Resource ID (404)
        path_params = [p for p in endpoint.parameters if p.location == "path"]
        if path_params:
            first_path_param = path_params[0]
            param_type = first_path_param.schema.get("type")
            sentinel_val: Any = (
                999999999 if param_type == "integer" else "nonexistent-id-999999"
            )

            not_found_url, _ = self._build_url_and_params(
                endpoint,
                base_url_var,
                path_override={first_path_param.name: sentinel_val},
            )

            req_def = TestRequestDefinition(
                method=HttpMethod.from_string(endpoint.method),
                url=not_found_url,
                headers=dict(headers),
                params=dict(params),
                body=None,
                body_type=BodyType.JSON,
                auth=auth_config,
            )
            tests.append(
                TestDefinition(
                    name=(
                        f"{endpoint.method} {endpoint.path} - "
                        "resource not found (invalid path param)"
                    ),
                    module=endpoint.module,
                    request=req_def,
                    tags=["negative", "rule-generated"],
                    assertions=[{"type": "status", "equals": 404}],
                    source=DefinitionSource.RULE_GENERATED,
                    status=DefinitionStatus.APPROVED,
                )
            )

        # 2d. Missing Authentication (401/403)
        if endpoint.requires_auth:
            unauth_headers = {
                k: v
                for k, v in headers.items()
                if k.lower() not in ("authorization", "x-api-key")
            }
            body = (
                synthesize_valid_value(endpoint.request_body_schema)
                if endpoint.request_body_schema
                else None
            )
            if body is not None:
                unauth_headers["Content-Type"] = "application/json"

            req_def = TestRequestDefinition(
                method=HttpMethod.from_string(endpoint.method),
                url=url,
                headers=unauth_headers,
                params=dict(params),
                body=body,
                body_type=BodyType.JSON if body is not None else BodyType.JSON,
                auth=None,
            )
            tests.append(
                TestDefinition(
                    name=f"{endpoint.method} {endpoint.path} - missing authentication",
                    module=endpoint.module,
                    request=req_def,
                    tags=["negative", "auth", "rule-generated"],
                    assertions=[{"type": "status", "range": [401, 403]}],
                    source=DefinitionSource.RULE_GENERATED,
                    status=DefinitionStatus.APPROVED,
                )
            )

        return tests

    def _generate_unsupported_method_tests(
        self,
        spec: OpenAPISpec,
        endpoints: list[EndpointSpec],
        base_url_var: str,
    ) -> list[TestDefinition]:
        """Generate 405 unsupported method tests per unique path (FR-21 Category 2e)."""
        tests: list[TestDefinition] = []
        seen_paths: set[str] = set()

        for ep in endpoints:
            if ep.path in seen_paths:
                continue
            seen_paths.add(ep.path)

            supported_methods = spec.path_methods.get(ep.path, [ep.method])
            unsupported_method: str | None = None
            for candidate in _UNSUPPORTED_METHOD_CANDIDATES:
                if candidate not in supported_methods:
                    unsupported_method = candidate
                    break

            if unsupported_method is None:
                continue

            url, params = self._build_url_and_params(ep, base_url_var)

            req_def = TestRequestDefinition(
                method=HttpMethod.from_string(unsupported_method),
                url=url,
                headers={},
                params=params,
                body=None,
                body_type=BodyType.JSON,
            )
            tests.append(
                TestDefinition(
                    name=f"{unsupported_method} {ep.path} - unsupported method",
                    module=ep.module,
                    request=req_def,
                    tags=["negative", "rule-generated"],
                    assertions=[{"type": "status", "equals": 405}],
                    source=DefinitionSource.RULE_GENERATED,
                    status=DefinitionStatus.APPROVED,
                )
            )

        return tests

    def _generate_boundary_tests(
        self, endpoint: EndpointSpec, base_url_var: str
    ) -> list[TestDefinition]:
        """Generate boundary tests for min/max, string length, and enums (FR-22)."""
        tests: list[TestDefinition] = []
        url, params = self._build_url_and_params(endpoint, base_url_var)
        auth_config, headers = self._resolve_auth(endpoint)

        # Body properties boundary checks
        if endpoint.request_body_schema and isinstance(
            endpoint.request_body_schema, dict
        ):
            base_body = synthesize_valid_value(endpoint.request_body_schema)
            props = endpoint.request_body_schema.get("properties", {})
            if isinstance(props, dict) and isinstance(base_body, dict):
                for prop_name, prop_schema in props.items():
                    if not isinstance(prop_schema, dict):
                        continue
                    p_type = prop_schema.get("type")

                    # Numeric minimum
                    if "minimum" in prop_schema and p_type in ("integer", "number"):
                        min_val = prop_schema["minimum"]
                        step = 1 if p_type == "integer" else 1.0

                        # At minimum -> 2xx
                        b_body = dict(base_body)
                        b_body[prop_name] = min_val
                        tests.append(
                            self._create_boundary_test(
                                endpoint=endpoint,
                                test_name=(
                                    f"{endpoint.method} {endpoint.path} - "
                                    f"boundary '{prop_name}' at minimum {min_val}"
                                ),
                                url=url,
                                headers=headers,
                                params=params,
                                body=b_body,
                                auth=auth_config,
                                expected_status={"equals": endpoint.success_status},
                            )
                        )

                        # Below minimum -> [400, 422]
                        b_body_below = dict(base_body)
                        b_body_below[prop_name] = min_val - step
                        tests.append(
                            self._create_boundary_test(
                                endpoint=endpoint,
                                test_name=(
                                    f"{endpoint.method} {endpoint.path} - "
                                    f"boundary '{prop_name}' below minimum"
                                ),
                                url=url,
                                headers=headers,
                                params=params,
                                body=b_body_below,
                                auth=auth_config,
                                expected_status={"range": [400, 422]},
                            )
                        )

                    # Numeric maximum
                    if "maximum" in prop_schema and p_type in ("integer", "number"):
                        max_val = prop_schema["maximum"]
                        step = 1 if p_type == "integer" else 1.0

                        # At maximum -> 2xx
                        b_body = dict(base_body)
                        b_body[prop_name] = max_val
                        tests.append(
                            self._create_boundary_test(
                                endpoint=endpoint,
                                test_name=(
                                    f"{endpoint.method} {endpoint.path} - "
                                    f"boundary '{prop_name}' at maximum {max_val}"
                                ),
                                url=url,
                                headers=headers,
                                params=params,
                                body=b_body,
                                auth=auth_config,
                                expected_status={"equals": endpoint.success_status},
                            )
                        )

                        # Above maximum -> [400, 422]
                        b_body_above = dict(base_body)
                        b_body_above[prop_name] = max_val + step
                        tests.append(
                            self._create_boundary_test(
                                endpoint=endpoint,
                                test_name=(
                                    f"{endpoint.method} {endpoint.path} - "
                                    f"boundary '{prop_name}' above maximum"
                                ),
                                url=url,
                                headers=headers,
                                params=params,
                                body=b_body_above,
                                auth=auth_config,
                                expected_status={"range": [400, 422]},
                            )
                        )

                    # String minLength
                    if (
                        "minLength" in prop_schema
                        and p_type == "string"
                        and int(prop_schema["minLength"]) > 0
                    ):
                        min_len = int(prop_schema["minLength"])

                        # At minLength -> 2xx
                        b_body = dict(base_body)
                        b_body[prop_name] = "a" * min_len
                        tests.append(
                            self._create_boundary_test(
                                endpoint=endpoint,
                                test_name=(
                                    f"{endpoint.method} {endpoint.path} - "
                                    f"boundary '{prop_name}' at minLength {min_len}"
                                ),
                                url=url,
                                headers=headers,
                                params=params,
                                body=b_body,
                                auth=auth_config,
                                expected_status={"equals": endpoint.success_status},
                            )
                        )

                        # Below minLength -> [400, 422]
                        b_body_below = dict(base_body)
                        b_body_below[prop_name] = "a" * (min_len - 1)
                        tests.append(
                            self._create_boundary_test(
                                endpoint=endpoint,
                                test_name=(
                                    f"{endpoint.method} {endpoint.path} - "
                                    f"boundary '{prop_name}' below minLength"
                                ),
                                url=url,
                                headers=headers,
                                params=params,
                                body=b_body_below,
                                auth=auth_config,
                                expected_status={"range": [400, 422]},
                            )
                        )

                    # String maxLength
                    if "maxLength" in prop_schema and p_type == "string":
                        max_len = int(prop_schema["maxLength"])

                        # At maxLength -> 2xx
                        b_body = dict(base_body)
                        b_body[prop_name] = "a" * max_len
                        tests.append(
                            self._create_boundary_test(
                                endpoint=endpoint,
                                test_name=(
                                    f"{endpoint.method} {endpoint.path} - "
                                    f"boundary '{prop_name}' at maxLength {max_len}"
                                ),
                                url=url,
                                headers=headers,
                                params=params,
                                body=b_body,
                                auth=auth_config,
                                expected_status={"equals": endpoint.success_status},
                            )
                        )

                        # Above maxLength -> [400, 422]
                        b_body_above = dict(base_body)
                        b_body_above[prop_name] = "a" * (max_len + 1)
                        tests.append(
                            self._create_boundary_test(
                                endpoint=endpoint,
                                test_name=(
                                    f"{endpoint.method} {endpoint.path} - "
                                    f"boundary '{prop_name}' above maxLength"
                                ),
                                url=url,
                                headers=headers,
                                params=params,
                                body=b_body_above,
                                auth=auth_config,
                                expected_status={"range": [400, 422]},
                            )
                        )

                    # Enum constraint
                    if (
                        "enum" in prop_schema
                        and isinstance(prop_schema["enum"], list)
                        and prop_schema["enum"]
                    ):
                        b_body_invalid_enum = dict(base_body)
                        b_body_invalid_enum[prop_name] = "__INVALID_ENUM_VALUE__"
                        tests.append(
                            self._create_boundary_test(
                                endpoint=endpoint,
                                test_name=(
                                    f"{endpoint.method} {endpoint.path} - "
                                    f"boundary '{prop_name}' invalid enum value"
                                ),
                                url=url,
                                headers=headers,
                                params=params,
                                body=b_body_invalid_enum,
                                auth=auth_config,
                                expected_status={"range": [400, 422]},
                            )
                        )

        # Query parameters boundary checks
        query_params_specs = [p for p in endpoint.parameters if p.location == "query"]
        for qp in query_params_specs:
            q_schema = qp.schema
            if not isinstance(q_schema, dict):
                continue
            qp_type = q_schema.get("type")

            # Numeric query minimum
            if "minimum" in q_schema and qp_type in ("integer", "number"):
                min_val = q_schema["minimum"]
                step = 1 if qp_type == "integer" else 1.0

                # At minimum -> 2xx
                _, q_params_at = self._build_url_and_params(
                    endpoint, base_url_var, query_override={qp.name: min_val}
                )
                tests.append(
                    self._create_boundary_test(
                        endpoint=endpoint,
                        test_name=(
                            f"{endpoint.method} {endpoint.path} - "
                            f"boundary query '{qp.name}' at minimum {min_val}"
                        ),
                        url=url,
                        headers=headers,
                        params=q_params_at,
                        body=None,
                        auth=auth_config,
                        expected_status={"equals": endpoint.success_status},
                    )
                )

                # Below minimum -> [400, 422]
                _, q_params_below = self._build_url_and_params(
                    endpoint, base_url_var, query_override={qp.name: min_val - step}
                )
                tests.append(
                    self._create_boundary_test(
                        endpoint=endpoint,
                        test_name=(
                            f"{endpoint.method} {endpoint.path} - "
                            f"boundary query '{qp.name}' below minimum"
                        ),
                        url=url,
                        headers=headers,
                        params=q_params_below,
                        body=None,
                        auth=auth_config,
                        expected_status={"range": [400, 422]},
                    )
                )

            # Numeric query maximum
            if "maximum" in q_schema and qp_type in ("integer", "number"):
                max_val = q_schema["maximum"]
                step = 1 if qp_type == "integer" else 1.0

                # At maximum -> 2xx
                _, q_params_at = self._build_url_and_params(
                    endpoint, base_url_var, query_override={qp.name: max_val}
                )
                tests.append(
                    self._create_boundary_test(
                        endpoint=endpoint,
                        test_name=(
                            f"{endpoint.method} {endpoint.path} - "
                            f"boundary query '{qp.name}' at maximum {max_val}"
                        ),
                        url=url,
                        headers=headers,
                        params=q_params_at,
                        body=None,
                        auth=auth_config,
                        expected_status={"equals": endpoint.success_status},
                    )
                )

                # Above maximum -> [400, 422]
                _, q_params_above = self._build_url_and_params(
                    endpoint, base_url_var, query_override={qp.name: max_val + step}
                )
                tests.append(
                    self._create_boundary_test(
                        endpoint=endpoint,
                        test_name=(
                            f"{endpoint.method} {endpoint.path} - "
                            f"boundary query '{qp.name}' above maximum"
                        ),
                        url=url,
                        headers=headers,
                        params=q_params_above,
                        body=None,
                        auth=auth_config,
                        expected_status={"range": [400, 422]},
                    )
                )

        return tests

    def _create_boundary_test(
        self,
        endpoint: EndpointSpec,
        test_name: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any],
        body: Any,
        auth: AuthConfig | None,
        expected_status: dict[str, Any],
    ) -> TestDefinition:
        """Helper to create a boundary TestDefinition."""
        hdrs = dict(headers)
        if body is not None:
            hdrs["Content-Type"] = "application/json"

        req_def = TestRequestDefinition(
            method=HttpMethod.from_string(endpoint.method),
            url=url,
            headers=hdrs,
            params=dict(params),
            body=body,
            body_type=BodyType.JSON if body is not None else BodyType.JSON,
            auth=auth,
        )

        assertion_spec: dict[str, Any] = {"type": "status"}
        assertion_spec.update(expected_status)

        return TestDefinition(
            name=test_name,
            module=endpoint.module,
            request=req_def,
            tags=["boundary", "rule-generated"],
            assertions=[assertion_spec],
            source=DefinitionSource.RULE_GENERATED,
            status=DefinitionStatus.APPROVED,
        )
