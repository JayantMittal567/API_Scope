"""Unit tests for rule-based test generator.

Covers: FR-20, FR-21, FR-22, NFR-02, NFR-03, NFR-06.
"""

from __future__ import annotations

import pytest

from apiscope.core.exceptions import DefinitionError
from apiscope.definitions.models import (
    DefinitionSource,
    DefinitionStatus,
    TestDefinition,
)
from apiscope.generators.openapi_importer import (
    load_openapi_spec,
    parse_openapi_spec,
)
from apiscope.generators.rule_generator import (
    RuleBasedGenerator,
    synthesize_valid_value,
)


def _load_sample_spec():
    return load_openapi_spec("examples/m5_sample_openapi.yaml")


def test_generate_positive_tests_with_status_and_json_schema_assertions() -> None:
    """Covers: FR-20, NFR-06
    Verify positive test generation contains status code and json_schema assertions,
    with approved status and rule-generated source.
    """
    spec = _load_sample_spec()
    gen = RuleBasedGenerator()
    tests = gen.generate_tests(spec)

    positive_tests = [t for t in tests if "positive" in t.tags]
    assert (
        len(positive_tests) == 3
    )  # POST /orders, GET /orders/{order_id}, GET /products

    for pt in positive_tests:
        assert isinstance(pt, TestDefinition)
        assert pt.source == DefinitionSource.RULE_GENERATED
        assert pt.status == DefinitionStatus.APPROVED
        assert "positive" in pt.tags
        assert "rule-generated" in pt.tags
        assert pt.request.url.startswith("{{base_url}}")

        # Verify status assertion exists
        status_assertions = [a for a in pt.assertions if a.get("type") == "status"]
        assert len(status_assertions) == 1
        assert "equals" in status_assertions[0]

        # Verify json_schema assertion exists where response schema was defined
        if pt.name == "POST /orders - positive":
            assert status_assertions[0]["equals"] == 201
            schema_assertions = [
                a for a in pt.assertions if a.get("type") == "json_schema"
            ]
            assert len(schema_assertions) == 1
            assert "schema" in schema_assertions[0]
            assert pt.request.headers.get("Authorization") == "Bearer {{auth_token}}"
            assert pt.request.body is not None
            assert "product_id" in pt.request.body

        elif pt.name == "GET /orders/{order_id} - positive":
            assert status_assertions[0]["equals"] == 200
            schema_assertions = [
                a for a in pt.assertions if a.get("type") == "json_schema"
            ]
            assert len(schema_assertions) == 1
            # Check path param was substituted
            assert "/orders/1" in pt.request.url

        elif pt.name == "GET /products - positive":
            assert status_assertions[0]["equals"] == 200
            # Public endpoint should NOT have authorization header
            assert "Authorization" not in pt.request.headers


def test_generate_negative_tests_all_five_categories() -> None:
    """Covers: FR-21
    Verify generation of negative tests across all five required categories:
    1) Missing required body fields (range [400, 422])
    2) Wrong field types (range [400, 422])
    3) Invalid path parameter / resource not found (equals 404)
    4) Missing auth (range [401, 403]) on secured endpoints only
    5) Unsupported HTTP method (equals 405)
    """
    spec = _load_sample_spec()
    gen = RuleBasedGenerator()
    tests = gen.generate_tests(spec)

    neg_tests = [t for t in tests if "negative" in t.tags]
    assert len(neg_tests) > 0

    # 1. Missing required field tests
    missing_req = [t for t in neg_tests if "missing required field" in t.name]
    assert len(missing_req) >= 2  # product_id and quantity
    for t in missing_req:
        range_assertions = [a for a in t.assertions if a.get("range") == [400, 422]]
        assert len(range_assertions) == 1

    # 2. Wrong field type tests
    wrong_types = [t for t in neg_tests if "invalid type for" in t.name]
    assert len(wrong_types) >= 4  # product_id, quantity, coupon_code, priority
    for t in wrong_types:
        range_assertions = [a for a in t.assertions if a.get("range") == [400, 422]]
        assert len(range_assertions) == 1

    # 3. Invalid resource ID (404)
    not_found_tests = [
        t for t in neg_tests if "resource not found (invalid path param)" in t.name
    ]
    assert len(not_found_tests) >= 1
    assert not_found_tests[0].assertions[0] == {"type": "status", "equals": 404}
    assert "999999999" in not_found_tests[0].request.url

    # 4. Missing auth (401/403)
    auth_tests = [t for t in neg_tests if "missing authentication" in t.name]
    # Generated for POST /orders and GET /orders/{order_id}, but NOT GET /products
    assert len(auth_tests) == 2
    for t in auth_tests:
        assert "Authorization" not in t.request.headers
        assert t.request.auth is None
        assert t.assertions[0] == {"type": "status", "range": [401, 403]}
        assert "auth" in t.tags

    # Confirm GET /products (public) has no missing auth test
    products_auth = [t for t in auth_tests if "GET /products" in t.name]
    assert len(products_auth) == 0

    # 5. Unsupported method (405)
    unsupported_tests = [t for t in neg_tests if "unsupported method" in t.name]
    assert len(unsupported_tests) >= 2
    for t in unsupported_tests:
        assert t.assertions[0] == {"type": "status", "equals": 405}


def test_generate_boundary_tests_min_max_length_and_enum() -> None:
    """Covers: FR-22
    Verify generation of boundary tests:
    - Numeric minimum (at minimum = 2xx, below minimum = [400, 422])
    - Numeric maximum (at maximum = 2xx, above maximum = [400, 422])
    - String minLength (at minLength = 2xx, below minLength = [400, 422])
    - String maxLength (at maxLength = 2xx, above maxLength = [400, 422])
    - Enum constraint (invalid enum value = [400, 422])
    """
    spec = _load_sample_spec()
    gen = RuleBasedGenerator()
    tests = gen.generate_tests(spec)

    boundary_tests = [t for t in tests if "boundary" in t.tags]
    assert len(boundary_tests) > 0

    # Minimum boundary tests (body & query)
    at_min = [t for t in boundary_tests if "at minimum" in t.name]
    below_min = [t for t in boundary_tests if "below minimum" in t.name]
    assert len(at_min) >= 3  # product_id, quantity, query limit
    assert len(below_min) >= 3
    for t in at_min:
        assert "equals" in t.assertions[0]
    for t in below_min:
        assert t.assertions[0].get("range") == [400, 422]

    # Maximum boundary tests
    at_max = [t for t in boundary_tests if "at maximum" in t.name]
    above_max = [t for t in boundary_tests if "above maximum" in t.name]
    assert len(at_max) >= 3
    assert len(above_max) >= 3
    for t in at_max:
        assert "equals" in t.assertions[0]
    for t in above_max:
        assert t.assertions[0].get("range") == [400, 422]

    # String length tests
    at_min_len = [t for t in boundary_tests if "at minLength" in t.name]
    below_min_len = [t for t in boundary_tests if "below minLength" in t.name]
    assert len(at_min_len) >= 1  # coupon_code
    assert len(below_min_len) >= 1
    assert "equals" in at_min_len[0].assertions[0]
    assert below_min_len[0].assertions[0].get("range") == [400, 422]

    # Enum tests
    enum_tests = [t for t in boundary_tests if "invalid enum value" in t.name]
    assert len(enum_tests) >= 1  # priority
    assert enum_tests[0].assertions[0].get("range") == [400, 422]
    assert enum_tests[0].request.body["priority"] == "__INVALID_ENUM_VALUE__"


def test_generated_tests_use_placeholders_and_contain_no_raw_secrets() -> None:
    """Covers: FR-20, NFR-03
    Verify generated tests use variable templates and do not leak hardcoded secrets.
    """
    spec = _load_sample_spec()
    gen = RuleBasedGenerator()
    tests = gen.generate_tests(spec)

    raw_forbidden = ["secret", "password", "my-real-token", "12345678"]

    for t in tests:
        assert "{{base_url}}" in t.request.url
        auth_hdr = t.request.headers.get("Authorization", "")
        if auth_hdr:
            assert auth_hdr == "Bearer {{auth_token}}"
        for forbidden in raw_forbidden:
            assert forbidden not in t.request.url.lower()
            if t.request.body and isinstance(t.request.body, dict):
                for val in t.request.body.values():
                    assert forbidden not in str(val).lower()


def test_max_generated_tests_limit_enforced() -> None:
    """Covers: FR-20, NFR-02
    Verify DefinitionError is raised if generated test count exceeds max limit.
    """
    spec = _load_sample_spec()
    gen = RuleBasedGenerator(max_generated_tests=5)
    with pytest.raises(DefinitionError) as exc_info:
        gen.generate_tests(spec)
    assert "exceeds maximum limit of 5" in str(exc_info.value)


def test_synthesize_valid_value_branches() -> None:
    """Covers: FR-20
    Verify synthesize_valid_value handles various types, examples, and defaults.
    """
    assert synthesize_valid_value(None) == "sample_value"
    assert synthesize_valid_value({"default": "default_val"}) == "default_val"
    assert synthesize_valid_value({"example": 42}) == 42
    assert synthesize_valid_value({"examples": ["ex1", "ex2"]}) == "ex1"
    assert synthesize_valid_value({"enum": ["alpha", "beta"]}) == "alpha"
    assert synthesize_valid_value({"type": "boolean"}) is True
    assert synthesize_valid_value({"type": "number", "minimum": 3.5}) == 3.5
    assert synthesize_valid_value({"type": "string", "minLength": 15}) == (
        "test_valueaaaaa"
    )
    arr = synthesize_valid_value({"type": "array", "items": {"type": "integer"}})
    assert isinstance(arr, list)
    assert len(arr) == 1
    assert arr[0] == 1


def test_api_key_auth_header_and_query_generation() -> None:
    """Covers: FR-20, NFR-03
    Verify ApiKeyAuth generation for header and query schemes.
    """
    raw_spec = {
        "openapi": "3.0.0",
        "info": {"title": "ApiKey API", "version": "1.0"},
        "paths": {
            "/header-key": {
                "get": {
                    "security": [{"headerAuth": []}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/query-key": {
                "get": {
                    "security": [{"queryAuth": []}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
        "components": {
            "securitySchemes": {
                "headerAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-Custom-Key",
                },
                "queryAuth": {
                    "type": "apiKey",
                    "in": "query",
                    "name": "api_key",
                },
            }
        },
    }
    spec = parse_openapi_spec(raw_spec)
    gen = RuleBasedGenerator()
    tests = gen.generate_tests(spec)

    header_test = next(t for t in tests if "GET /header-key - positive" in t.name)
    assert "X-Custom-Key" in header_test.request.headers

    query_test = next(t for t in tests if "GET /query-key - positive" in t.name)
    assert query_test.request.auth is not None


def test_definition_to_dict_all_optional_fields() -> None:
    """Covers: FR-23
    Verify test_definition_to_dict serializes all optional request fields.
    """
    from apiscope.core.models import BodyType, HttpMethod
    from apiscope.definitions.models import TestRequestDefinition
    from apiscope.generators.service import test_definition_to_dict

    t_def = TestDefinition(
        name="Full Test",
        module="full",
        request=TestRequestDefinition(
            method=HttpMethod.POST,
            url="http://test.com",
            headers={"H": "1"},
            params={"P": "2"},
            body={"B": 3},
            body_type=BodyType.JSON,
            timeout=12.5,
            max_retries=3,
            follow_redirects=False,
            retry_non_idempotent=True,
        ),
        tags=["t1"],
        extract={"var": "body.id"},
        assertions=[{"type": "status", "equals": 200}],
        source=DefinitionSource.RULE_GENERATED,
        status=DefinitionStatus.APPROVED,
    )
    d = test_definition_to_dict(t_def)
    assert d["request"]["timeout"] == 12.5
    assert d["request"]["max_retries"] == 3
    assert d["request"]["follow_redirects"] is False
    assert d["request"]["retry_non_idempotent"] is True
    assert d["extract"] == {"var": "body.id"}
