"""Unit test suite for M3 — Assertion Engine (FR-10 to FR-15).

Covers:
- Status assertions: exact match, inclusive range, boundaries (1-8).
- Response time assertions: below, at, above limit, negative limits (9-12).
- Header assertions: existence, case-insensitivity, equality (13-17).
- JSONPath assertions: equality, contains, numeric > (excludes bool),
  regex, type checks (int/num vs bool), missing paths, multiple matches (18-36).
- JSON Schema assertions: valid, missing required, wrong type, constraints (37-41).
- Engine behavior: dispatch, isolation, ordering, aggregate counts,
  unverified state, unknown types, malformed rules, immutability (42-52).
"""

from typing import Any

from apiscope.core.assertions import (
    AssertionEngine,
    AssertionResult,
    AssertionStatus,
    BaseAssertionEvaluator,
)
from apiscope.core.models import HttpResponse
from apiscope.definitions.models import TestDefinition, TestRequestDefinition


def _make_response(
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    body: str = "",
    elapsed_ms: float = 50.0,
) -> HttpResponse:
    """Helper to construct an isolated HttpResponse domain model for testing."""
    return HttpResponse(
        status_code=status_code,
        headers=headers or {},
        body=body,
        elapsed_ms=elapsed_ms,
        url="https://api.example.com/test",
    )


# ============================================================================
# 1. HTTP Status Assertions (Cases 1 - 8)
# ============================================================================


class TestStatusAssertions:
    """Evaluates HTTP response status code exact equality and ranges."""

    def test_01_exact_matching_status_passes(self) -> None:
        """Case 1: Exact matching status code satisfies assertion."""
        engine = AssertionEngine()
        resp = _make_response(status_code=200)
        rules = [{"type": "status", "equals": 200}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is True
        assert summary.total == 1
        assert summary.passed == 1
        res = summary[0]
        assert res.passed is True
        assert res.status == AssertionStatus.PASS
        assert res.expected == 200
        assert res.actual == 200
        assert "Expected status 200, received 200" in res.message

    def test_02_exact_matching_status_fails(self) -> None:
        """Case 2: Mismatched status code fails assertion."""
        engine = AssertionEngine()
        resp = _make_response(status_code=404)
        rules = [{"type": "status", "equals": 200}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary.failed == 1
        res = summary[0]
        assert res.passed is False
        assert res.status == AssertionStatus.FAIL
        assert res.actual == 404
        assert "Expected status 200, received 404" in res.message

    def test_03_inclusive_range_passes(self) -> None:
        """Case 3: Status code within inclusive range passes."""
        engine = AssertionEngine()
        resp = _make_response(status_code=204)
        rules = [{"type": "status", "range": [200, 299]}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is True
        assert summary[0].passed is True
        assert "range [200, 299]" in summary[0].message

    def test_04_lower_range_boundary_passes(self) -> None:
        """Case 4: Status code exactly at lower bound of range passes."""
        engine = AssertionEngine()
        resp = _make_response(status_code=200)
        rules = [{"type": "status", "range": [200, 299]}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is True
        assert summary[0].passed is True
        assert summary[0].actual == 200

    def test_05_upper_range_boundary_passes(self) -> None:
        """Case 5: Status code exactly at upper bound of range passes."""
        engine = AssertionEngine()
        resp = _make_response(status_code=299)
        rules = [{"type": "status", "range": [200, 299]}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is True
        assert summary[0].passed is True
        assert summary[0].actual == 299

    def test_06_status_outside_range_fails(self) -> None:
        """Case 6: Status code outside configured range fails."""
        engine = AssertionEngine()
        resp = _make_response(status_code=500)
        rules = [{"type": "status", "range": [200, 299]}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary[0].passed is False
        assert summary[0].actual == 500

    def test_07_inverted_range_is_rejected(self) -> None:
        """Case 7: Inverted range [299, 200] is rejected as invalid configuration."""
        engine = AssertionEngine()
        resp = _make_response(status_code=200)
        rules = [{"type": "status", "range": [299, 200]}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary[0].passed is False
        assert "lower bound cannot exceed upper bound" in summary[0].message

    def test_08_missing_expected_condition_is_handled_correctly(self) -> None:
        """Case 8: Status assertion missing both equals and range fails gracefully."""
        engine = AssertionEngine()
        resp = _make_response(status_code=200)
        rules = [{"type": "status"}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary[0].passed is False
        assert "missing required condition" in summary[0].message


# ============================================================================
# 2. Response Time Assertions (Cases 9 - 12)
# ============================================================================


class TestResponseTimeAssertions:
    """Evaluates HTTP response execution elapsed time against max limits."""

    def test_09_response_time_below_limit_passes(self) -> None:
        """Case 9: Elapsed time strictly below limit passes."""
        engine = AssertionEngine()
        resp = _make_response(elapsed_ms=150.0)
        rules = [{"type": "max_time_ms", "value": 500}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is True
        assert summary[0].passed is True
        assert summary[0].actual == 150.0

    def test_10_response_time_exactly_at_limit_passes(self) -> None:
        """Case 10: Elapsed time exactly at limit passes (inclusive <= check)."""
        engine = AssertionEngine()
        resp = _make_response(elapsed_ms=500.0)
        rules = [{"type": "max_time_ms", "value": 500}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is True
        assert summary[0].passed is True
        assert summary[0].actual == 500.0

    def test_11_response_time_above_limit_fails(self) -> None:
        """Case 11: Elapsed time exceeding limit fails."""
        engine = AssertionEngine()
        resp = _make_response(elapsed_ms=501.5)
        rules = [{"type": "max_time_ms", "value": 500}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary[0].passed is False
        assert summary[0].actual == 501.5
        assert "Expected response time <= 500ms" in summary[0].message

    def test_12_invalid_negative_limits_are_rejected(self) -> None:
        """Case 12: Negative response time limits fail configuration check."""
        engine = AssertionEngine()
        resp = _make_response(elapsed_ms=10.0)
        rules = [{"type": "max_time_ms", "value": -50}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary[0].passed is False
        assert "must be a non-negative number" in summary[0].message


# ============================================================================
# 3. Header Assertions (Cases 13 - 17)
# ============================================================================


class TestHeaderAssertions:
    """Evaluates HTTP response headers with case-insensitive name matching."""

    def test_13_existing_header_passes(self) -> None:
        """Case 13: Header existence assertion passes when header is present."""
        engine = AssertionEngine()
        resp = _make_response(headers={"Content-Type": "application/json"})
        rules = [{"type": "header_exists", "name": "Content-Type"}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is True
        assert summary[0].passed is True
        assert "Header 'Content-Type' exists" in summary[0].message

    def test_14_missing_header_fails(self) -> None:
        """Case 14: Header existence assertion fails when header is missing."""
        engine = AssertionEngine()
        resp = _make_response(headers={"Content-Type": "application/json"})
        rules = [{"type": "header_exists", "name": "X-Auth-Token"}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary[0].passed is False
        assert "Expected header 'X-Auth-Token' to exist" in summary[0].message

    def test_15_header_name_matching_is_case_insensitive(self) -> None:
        """Case 15: Header name lookup is case-insensitive across cases."""
        engine = AssertionEngine()
        resp = _make_response(headers={"Content-Type": "application/json"})

        # Lowercase request, Mixed case response
        rules = [
            {"type": "header_exists", "name": "content-type"},
            {
                "type": "header_equals",
                "name": "CONTENT-TYPE",
                "equals": "application/json",
            },
        ]
        summary = engine.evaluate(resp, rules)

        assert summary.success is True
        assert summary.passed == 2
        assert summary[0].passed is True
        assert summary[1].passed is True

    def test_16_correct_header_value_passes(self) -> None:
        """Case 16: Header equality passes when value matches exactly."""
        engine = AssertionEngine()
        resp = _make_response(headers={"X-Version": "v1.2.3"})
        rules = [{"type": "header_equals", "name": "X-Version", "equals": "v1.2.3"}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is True
        assert summary[0].passed is True
        assert summary[0].actual == "v1.2.3"

    def test_17_incorrect_header_value_fails(self) -> None:
        """Case 17: Header equality fails when value differs."""
        engine = AssertionEngine()
        resp = _make_response(headers={"X-Version": "v1.0.0"})
        rules = [{"type": "header_equals", "name": "X-Version", "equals": "v2.0.0"}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary[0].passed is False
        assert summary[0].actual == "v1.0.0"
        assert "Expected header 'X-Version' to equal 'v2.0.0'" in summary[0].message


# ============================================================================
# 4. JSONPath Assertions (Cases 18 - 36)
# ============================================================================


class TestJsonPathAssertions:
    """Evaluates JSONPath expressions against deserialized response bodies."""

    def test_18_simple_path_equality(self) -> None:
        """Case 18: Simple top-level path equality check passes."""
        engine = AssertionEngine()
        resp = _make_response(body='{"status": "ok", "code": 200}')
        rules = [{"type": "jsonpath", "path": "$.status", "equals": "ok"}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is True
        assert summary[0].passed is True
        assert summary[0].actual == "ok"

    def test_19_nested_path_equality(self) -> None:
        """Case 19: Deeply nested object property equality check passes."""
        engine = AssertionEngine()
        body = '{"user": {"profile": {"contact": {"email": "alex@example.com"}}}}'
        resp = _make_response(body=body)
        rules = [
            {
                "type": "jsonpath",
                "path": "$.user.profile.contact.email",
                "equals": "alex@example.com",
            }
        ]

        summary = engine.evaluate(resp, rules)

        assert summary.success is True
        assert summary[0].passed is True
        assert summary[0].actual == "alex@example.com"

    def test_20_string_contains(self) -> None:
        """Case 20: Substring containment check on string property passes."""
        engine = AssertionEngine()
        resp = _make_response(
            body='{"message": "Order created successfully with ID 45"}'
        )
        rules = [
            {
                "type": "jsonpath",
                "path": "$.message",
                "contains": "created successfully",
            }
        ]

        summary = engine.evaluate(resp, rules)

        assert summary.success is True
        assert summary[0].passed is True

    def test_21_list_contains(self) -> None:
        """Case 21: Element membership check in list passes."""
        engine = AssertionEngine()
        resp = _make_response(body='{"roles": ["viewer", "editor", "admin"]}')
        rules = [{"type": "jsonpath", "path": "$.roles", "contains": "editor"}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is True
        assert summary[0].passed is True

    def test_22_dictionary_key_contains(self) -> None:
        """Case 22: Key existence check in dictionary passes."""
        engine = AssertionEngine()
        resp = _make_response(body='{"config": {"debug": true, "timeout": 30}}')
        rules = [{"type": "jsonpath", "path": "$.config", "contains": "debug"}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is True
        assert summary[0].passed is True

    def test_23_numeric_greater_than_passes(self) -> None:
        """Case 23: Numeric greater-than comparison passes when exceeding threshold."""
        engine = AssertionEngine()
        resp = _make_response(body='{"account": {"balance": 1500.50, "age": 28}}')
        rules = [
            {"type": "jsonpath", "path": "$.account.balance", "greater_than": 1000},
            {"type": "jsonpath", "path": "$.account.age", "greater_than": 18},
        ]

        summary = engine.evaluate(resp, rules)

        assert summary.success is True
        assert summary.passed == 2

    def test_24_numeric_greater_than_fails(self) -> None:
        """Case 24: Numeric greater-than comparison fails when value is <= threshold."""
        engine = AssertionEngine()
        resp = _make_response(body='{"count": 10}')
        rules = [{"type": "jsonpath", "path": "$.count", "greater_than": 10}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary[0].passed is False
        assert "Expected > 10, received 10" in summary[0].message

    def test_25_incorrect_type_for_greater_than_is_rejected(self) -> None:
        """Case 25: Non-numeric and boolean types are rejected for greater_than."""
        engine = AssertionEngine()
        resp = _make_response(body='{"text": "high", "flag": true}')

        # String actual
        r1 = engine.evaluate(
            resp, [{"type": "jsonpath", "path": "$.text", "greater_than": 10}]
        )
        assert r1.success is False
        assert "requires numeric value" in r1[0].message

        # Boolean actual (True must not satisfy greater_than numeric check)
        r2 = engine.evaluate(
            resp, [{"type": "jsonpath", "path": "$.flag", "greater_than": 0}]
        )
        assert r2.success is False
        assert "excluding bool" in r2[0].message

    def test_26_regex_match_passes(self) -> None:
        """Case 26: Regular expression assertion passes when pattern matches."""
        engine = AssertionEngine()
        resp = _make_response(body='{"email": "support@apiscope.org"}')
        rules = [
            {
                "type": "jsonpath",
                "path": "$.email",
                "regex": r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",
            }
        ]

        summary = engine.evaluate(resp, rules)

        assert summary.success is True
        assert summary[0].passed is True

    def test_27_regex_mismatch_fails(self) -> None:
        """Case 27: Regular expression assertion fails when pattern does not match."""
        engine = AssertionEngine()
        resp = _make_response(body='{"email": "not-an-email-address"}')
        rules = [
            {
                "type": "jsonpath",
                "path": "$.email",
                "regex": r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",
            }
        ]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary[0].passed is False
        assert "does not match regex" in summary[0].message

    def test_28_valid_string_type_passes(self) -> None:
        """Case 28: type_check 'string' passes for string properties."""
        engine = AssertionEngine()
        resp = _make_response(body='{"name": "ApiScope"}')
        rules = [{"type": "jsonpath", "path": "$.name", "type_check": "string"}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is True
        assert summary[0].passed is True

    def test_29_valid_integer_type_passes(self) -> None:
        """Case 29: type_check 'integer' passes for native integer properties."""
        engine = AssertionEngine()
        resp = _make_response(body='{"id": 1024}')
        rules = [{"type": "jsonpath", "path": "$.id", "type_check": "integer"}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is True
        assert summary[0].passed is True

    def test_30_integer_is_not_confused_with_boolean(self) -> None:
        """Case 30: Python True/False does not satisfy 'integer' type check."""
        engine = AssertionEngine()
        resp = _make_response(body='{"active": true}')
        rules = [{"type": "jsonpath", "path": "$.active", "type_check": "integer"}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary[0].passed is False
        assert "Expected type 'integer', received bool" in summary[0].message

    def test_31_number_is_not_confused_with_boolean(self) -> None:
        """Case 31: Python True/False does not satisfy 'number' type check."""
        engine = AssertionEngine()
        resp = _make_response(body='{"flag": false}')
        rules = [{"type": "jsonpath", "path": "$.flag", "type_check": "number"}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary[0].passed is False
        assert "Expected type 'number', received bool" in summary[0].message

    def test_32_missing_jsonpath_result_fails(self) -> None:
        """Case 32: Non-existent path returns clean failure with diagnostic message."""
        engine = AssertionEngine()
        resp = _make_response(body='{"user": {"name": "Bob"}}')
        rules = [{"type": "jsonpath", "path": "$.user.missing_key", "equals": "test"}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary[0].passed is False
        assert "found no matching elements" in summary[0].message

    def test_33_invalid_jsonpath_fails_gracefully(self) -> None:
        """Case 33: Syntactically invalid JSONPath fails without crashing the engine."""
        engine = AssertionEngine()
        resp = _make_response(body='{"data": [1, 2, 3]}')
        rules = [{"type": "jsonpath", "path": "$[invalid JSONPath", "equals": 1}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary[0].passed is False
        assert "Invalid JSONPath expression" in summary[0].message

    def test_34_multiple_jsonpath_matches_all_satisfy_condition(self) -> None:
        """Case 34: Multiple matches all meeting condition result in PASS."""
        engine = AssertionEngine()
        body = '{"items": [{"price": 15}, {"price": 25}, {"price": 40}]}'
        resp = _make_response(body=body)
        rules = [{"type": "jsonpath", "path": "$.items[*].price", "greater_than": 10}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is True
        assert summary[0].passed is True
        assert "All 3 elements matched" in summary[0].message

    def test_35_one_failing_multiple_match_value_makes_assertion_fail(self) -> None:
        """Case 35: A single failing element among multiple matches produces a FAIL."""
        engine = AssertionEngine()
        body = '{"items": [{"price": 15}, {"price": 5}, {"price": 40}]}'
        resp = _make_response(body=body)
        rules = [{"type": "jsonpath", "path": "$.items[*].price", "greater_than": 10}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary[0].passed is False
        assert "matched 3 elements but 1 failed condition" in summary[0].message
        assert "Element [1] (5) failed" in summary[0].message

    def test_36_malformed_json_response_is_handled_gracefully(self) -> None:
        """Case 36: HTML or non-JSON body is reported without unhandled exceptions."""
        engine = AssertionEngine()
        resp = _make_response(body="<html><body>502 Bad Gateway</body></html>")
        rules = [{"type": "jsonpath", "path": "$.id", "equals": 1}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary[0].passed is False
        assert "Response body is not valid JSON" in summary[0].message


# ============================================================================
# 5. JSON Schema Assertions (Cases 37 - 41)
# ============================================================================


class TestJsonSchemaAssertions:
    """Evaluates response bodies against declarative JSON Schema definitions."""

    def test_37_valid_schema_and_response_pass(self) -> None:
        """Case 37: Body strictly conforming to schema passes validation."""
        engine = AssertionEngine()
        body = '{"id": 42, "name": "Notebook", "price": 12.99}'
        resp = _make_response(body=body)
        schema = {
            "type": "object",
            "required": ["id", "name", "price"],
            "properties": {
                "id": {"type": "integer"},
                "name": {"type": "string"},
                "price": {"type": "number"},
            },
        }
        rules = [{"type": "json_schema", "schema": schema}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is True
        assert summary[0].passed is True
        assert "conforms to JSON Schema" in summary[0].message

    def test_38_missing_required_field_fails(self) -> None:
        """Case 38: Body missing required schema property fails."""
        engine = AssertionEngine()
        body = '{"id": 42}'
        resp = _make_response(body=body)
        schema = {
            "type": "object",
            "required": ["id", "quantity"],
            "properties": {
                "id": {"type": "integer"},
                "quantity": {"type": "integer"},
            },
        }
        rules = [{"type": "json_schema", "schema": schema}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary[0].passed is False
        assert "required property" in summary[0].message

    def test_39_incorrect_response_type_fails(self) -> None:
        """Case 39: Property violating defined data type fails."""
        engine = AssertionEngine()
        body = '{"id": "not-an-integer", "quantity": 10}'
        resp = _make_response(body=body)
        schema = {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "quantity": {"type": "integer"},
            },
        }
        rules = [{"type": "json_schema", "schema": schema}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary[0].passed is False
        assert "is not of type 'integer'" in summary[0].message

    def test_40_minimum_constraint_violation_fails(self) -> None:
        """Case 40: Numeric value violating minimum constraint fails."""
        engine = AssertionEngine()
        body = '{"id": 1, "quantity": 0}'
        resp = _make_response(body=body)
        schema = {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "quantity": {"type": "integer", "minimum": 1},
            },
        }
        rules = [{"type": "json_schema", "schema": schema}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary[0].passed is False
        assert "is less than the minimum of 1" in summary[0].message

    def test_41_invalid_schema_is_reported_properly(self) -> None:
        """Case 41: Malformed schema definition fails gracefully without crashing."""
        engine = AssertionEngine()
        body = '{"id": 1}'
        resp = _make_response(body=body)
        schema = {"type": "unrecognized_unknown_json_type"}
        rules = [{"type": "json_schema", "schema": schema}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary[0].passed is False
        assert "Invalid JSON Schema definition" in summary[0].message


# ============================================================================
# 6. Engine Behavior & Aggregate Summary (Cases 42 - 52)
# ============================================================================


class TestAssertionEngineBehavior:
    """Verifies orchestration, error isolation, ordering, and immutability."""

    def test_42_multiple_assertions_execute_together(self) -> None:
        """Case 42: Multiple assertions evaluate together in a single pass."""
        engine = AssertionEngine()
        resp = _make_response(
            status_code=201,
            headers={"Content-Type": "application/json"},
            body='{"id": 100, "status": "created"}',
            elapsed_ms=85.0,
        )
        rules = [
            {"type": "status", "equals": 201},
            {"type": "max_time_ms", "value": 200},
            {"type": "header_exists", "name": "Content-Type"},
            {"type": "jsonpath", "path": "$.id", "equals": 100},
        ]

        summary = engine.evaluate(resp, rules)

        assert summary.total == 4
        assert summary.passed == 4
        assert summary.failed == 0
        assert summary.success is True
        assert summary.verified is True

    def test_43_one_failure_does_not_stop_later_assertions(self) -> None:
        """Case 43: A failed assertion does not interrupt subsequent evaluations."""
        engine = AssertionEngine()
        resp = _make_response(
            status_code=200,
            headers={"X-Server": "gateway"},
            body='{"count": 5}',
        )
        rules = [
            {"type": "status", "equals": 200},  # PASS
            {"type": "jsonpath", "path": "$.missing", "equals": 1},  # FAIL
            {"type": "header_exists", "name": "X-Server"},  # PASS
        ]

        summary = engine.evaluate(resp, rules)

        assert summary.total == 3
        assert summary.passed == 2
        assert summary.failed == 1
        assert summary.success is False
        assert summary[0].passed is True
        assert summary[1].passed is False
        assert summary[2].passed is True

    def test_44_all_results_are_returned_in_original_assertion_order(self) -> None:
        """Case 44: Result list strictly mirrors original assertion rule order."""
        engine = AssertionEngine()
        resp = _make_response(
            status_code=200,
            headers={"X-One": "1", "X-Two": "2"},
            body='{"val": 99}',
        )
        rules = [
            {"type": "header_exists", "name": "X-One"},
            {"type": "status", "equals": 200},
            {"type": "jsonpath", "path": "$.val", "equals": 99},
            {"type": "header_exists", "name": "X-Two"},
        ]

        summary = engine.evaluate(resp, rules)

        assert [r.assertion_type for r in summary] == [
            "header_exists",
            "status",
            "jsonpath",
            "header_exists",
        ]

    def test_45_correct_aggregate_counts(self) -> None:
        """Case 45: Summary reports exact total, passed, and failed counts."""
        engine = AssertionEngine()
        resp = _make_response(status_code=200)
        rules = [
            {"type": "status", "equals": 200},
            {"type": "status", "equals": 200},
            {"type": "status", "equals": 500},
            {"type": "status", "equals": 200},
        ]

        summary = engine.evaluate(resp, rules)

        assert summary.total == 4
        assert summary.passed == 3
        assert summary.failed == 1
        assert summary.total == summary.passed + summary.failed

    def test_46_mixed_pass_fail_summary(self) -> None:
        """Case 46: Mixed pass/fail correctly reports failure and description."""
        engine = AssertionEngine()
        resp = _make_response(status_code=200)
        rules = [
            {"type": "status", "equals": 200},
            {"type": "status", "equals": 404},
        ]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary.verified is True
        assert "1 of 2 assertions failed" in summary.message
        assert summary["success"] is False
        assert summary["total"] == 2

    def test_47_empty_assertion_list_is_not_considered_verified(self) -> None:
        """Case 47: Zero assertions configured returns unverified status."""
        engine = AssertionEngine()
        resp = _make_response(status_code=200)

        summary = engine.evaluate(resp, [])

        assert summary.total == 0
        assert summary.passed == 0
        assert summary.failed == 0
        assert summary.success is False
        assert summary.verified is False
        assert "No assertions configured" in summary.message

    def test_48_unknown_assertion_type_is_handled_clearly(self) -> None:
        """Case 48: Unregistered assertion type produces structured failure message."""
        engine = AssertionEngine()
        resp = _make_response(status_code=200)
        rules = [{"type": "future_grpc_check", "rpc": "GetStatus"}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is False
        assert summary[0].passed is False
        assert "Unknown assertion type 'future_grpc_check'" in summary[0].message
        assert "Supported types:" in summary[0].message

    def test_49_invalid_assertion_configuration_does_not_crash_engine(self) -> None:
        """Case 49: Malformed rule object or invalid fields fail cleanly."""
        engine = AssertionEngine()
        resp = _make_response(status_code=200)
        rules: list[Any] = [
            "not a dict",
            {"missing_type": True},
            {"type": "status", "equals": "not-an-integer"},
        ]

        summary = engine.evaluate(resp, rules)

        assert summary.total == 3
        assert summary.failed == 3
        assert "must be a dictionary" in summary[0].message
        assert "missing required 'type'" in summary[1].message
        assert "must be an integer" in summary[2].message

    def test_50_response_without_a_body_is_handled_correctly(self) -> None:
        """Case 50: Response with empty body fails body checks gracefully."""
        engine = AssertionEngine()
        resp = _make_response(status_code=204, body="", headers={"X-Done": "true"})
        rules = [
            {"type": "status", "equals": 204},
            {"type": "header_exists", "name": "X-Done"},
            {"type": "jsonpath", "path": "$.id", "equals": 1},
            {"type": "json_schema", "schema": {"type": "object"}},
        ]

        summary = engine.evaluate(resp, rules)

        assert summary.total == 4
        assert summary.passed == 2
        assert summary.failed == 2
        assert summary[0].passed is True
        assert summary[1].passed is True
        assert summary[2].passed is False
        assert "Response body is empty" in summary[2].message
        assert summary[3].passed is False
        assert "Response body is empty" in summary[3].message

    def test_51_existing_response_model_is_not_mutated(self) -> None:
        """Case 51: Evaluating assertions does not modify response attributes."""
        engine = AssertionEngine()
        resp = _make_response(
            status_code=200,
            headers={"Content-Type": "application/json"},
            body='{"user": "alice"}',
            elapsed_ms=45.0,
        )
        original_status = resp.status_code
        original_headers = dict(resp.headers)
        original_body = resp.body
        original_elapsed = resp.elapsed_ms

        rules = [
            {"type": "status", "equals": 200},
            {"type": "header_exists", "name": "Content-Type"},
            {"type": "jsonpath", "path": "$.user", "equals": "alice"},
        ]
        engine.evaluate(resp, rules)

        assert resp.status_code == original_status
        assert resp.headers == original_headers
        assert resp.body == original_body
        assert resp.elapsed_ms == original_elapsed

    def test_52_existing_yaml_test_definition_is_not_mutated(self) -> None:
        """Case 52: Evaluating assertions does not alter TestDefinition rules."""
        engine = AssertionEngine()
        test_def = TestDefinition(
            name="Immutability assertion test",
            module="testing",
            request=TestRequestDefinition(method="GET", url="https://api.example.com"),
            assertions=[
                {"type": "status", "equals": 200},
                {"type": "jsonpath", "path": "$.count", "equals": 5},
            ],
        )
        resp = _make_response(status_code=200, body='{"count": 5}')

        engine.evaluate(resp, test_def.assertions)

        assert len(test_def.assertions) == 2
        assert test_def.assertions[0] == {"type": "status", "equals": 200}
        assert test_def.assertions[1] == {
            "type": "jsonpath",
            "path": "$.count",
            "equals": 5,
        }


class TestCustomEvaluatorRegistration:
    """Verifies extensibility via custom evaluator strategy registration."""

    def test_custom_evaluator_registration(self) -> None:
        """Verify custom evaluator can be registered and dispatched."""

        class CustomEvaluator(BaseAssertionEvaluator):
            def evaluate(
                self, rule: dict[str, Any], response: HttpResponse
            ) -> AssertionResult:
                val = rule.get("expected_prefix", "")
                passed = response.url.startswith(val)
                return AssertionResult(
                    assertion_type="url_prefix",
                    passed=passed,
                    expected=val,
                    actual=response.url,
                    message=f"URL prefix matched '{val}'"
                    if passed
                    else "Prefix mismatch",
                )

        engine = AssertionEngine()
        engine.register_evaluator("url_prefix", CustomEvaluator())

        resp = _make_response()
        rules = [{"type": "url_prefix", "expected_prefix": "https://api.example.com"}]

        summary = engine.evaluate(resp, rules)

        assert summary.success is True
        assert summary[0].passed is True
        assert summary[0].assertion_type == "url_prefix"
