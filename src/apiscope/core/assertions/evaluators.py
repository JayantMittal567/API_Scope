"""Strategy pattern evaluators for each supported assertion type.

Provides specialized evaluators for HTTP status codes, response elapsed times,
headers, JSONPath extractions, and JSON Schema validation.
"""

import re
from abc import ABC, abstractmethod
from typing import Any

import jsonpath_ng
import jsonschema

from apiscope.core.assertions.models import AssertionResult
from apiscope.core.models import HttpResponse


class BaseAssertionEvaluator(ABC):
    """Abstract strategy for evaluating a specific assertion type."""

    @abstractmethod
    def evaluate(
        self,
        rule: dict[str, Any],
        response: HttpResponse,
    ) -> AssertionResult:
        """Evaluate a single rule against an observed HTTP response.

        Args:
            rule: Declarative assertion configuration dictionary.
            response: Observed HTTP response domain model.

        Returns:
            AssertionResult capturing outcome, expected/actual values, and diagnostics.
        """
        ...


class StatusAssertionEvaluator(BaseAssertionEvaluator):
    """Evaluates HTTP response status code equality or inclusive range bounds."""

    def evaluate(
        self,
        rule: dict[str, Any],
        response: HttpResponse,
    ) -> AssertionResult:
        """Evaluate status code assertion rule."""
        # 1. Exact equality check
        if "equals" in rule:
            expected = rule["equals"]
            if not isinstance(expected, int) or isinstance(expected, bool):
                return AssertionResult(
                    assertion_type="status",
                    passed=False,
                    expected=expected,
                    actual=response.status_code,
                    message=(
                        f"Invalid status equality condition '{expected}': "
                        f"must be an integer."
                    ),
                )
            passed = response.status_code == expected
            return AssertionResult(
                assertion_type="status",
                passed=passed,
                expected=expected,
                actual=response.status_code,
                message=f"Expected status {expected}, received {response.status_code}",
            )

        # 2. Inclusive range check
        if "range" in rule:
            r = rule["range"]
            if not isinstance(r, (list, tuple)) or len(r) != 2:
                return AssertionResult(
                    assertion_type="status",
                    passed=False,
                    expected="[min, max]",
                    actual=r,
                    message=(
                        f"Invalid status range '{r}': "
                        f"must be a 2-element list [min, max]."
                    ),
                )
            min_val, max_val = r[0], r[1]
            if (
                not isinstance(min_val, int)
                or isinstance(min_val, bool)
                or not isinstance(max_val, int)
                or isinstance(max_val, bool)
            ):
                return AssertionResult(
                    assertion_type="status",
                    passed=False,
                    expected="[min, max] integers",
                    actual=r,
                    message=f"Invalid status range '{r}': bounds must be integers.",
                )
            if min_val > max_val:
                return AssertionResult(
                    assertion_type="status",
                    passed=False,
                    expected=f"[{min_val}, {max_val}]",
                    actual=r,
                    message=(
                        f"Invalid inverted status range [{min_val}, {max_val}]: "
                        f"lower bound cannot exceed upper bound."
                    ),
                )
            passed = min_val <= response.status_code <= max_val
            return AssertionResult(
                assertion_type="status",
                passed=passed,
                expected=f"[{min_val}, {max_val}]",
                actual=response.status_code,
                message=(
                    f"Expected status in range [{min_val}, {max_val}], "
                    f"received {response.status_code}"
                ),
            )

        # 3. Missing condition
        return AssertionResult(
            assertion_type="status",
            passed=False,
            expected="'equals' or 'range'",
            actual=None,
            message=(
                "Status assertion missing required condition ('equals' or 'range')."
            ),
        )


class ResponseTimeAssertionEvaluator(BaseAssertionEvaluator):
    """Evaluates HTTP response execution elapsed time against an upper threshold."""

    def evaluate(
        self,
        rule: dict[str, Any],
        response: HttpResponse,
    ) -> AssertionResult:
        """Evaluate maximum elapsed time limit."""
        if "value" not in rule:
            return AssertionResult(
                assertion_type="max_time_ms",
                passed=False,
                expected="non-negative number",
                actual=None,
                message="Response time assertion missing required 'value' limit.",
            )

        limit = rule["value"]
        if not isinstance(limit, (int, float)) or isinstance(limit, bool) or limit < 0:
            return AssertionResult(
                assertion_type="max_time_ms",
                passed=False,
                expected="non-negative number",
                actual=limit,
                message=(
                    f"Invalid response time limit '{limit}': "
                    f"must be a non-negative number."
                ),
            )

        actual = response.elapsed_ms
        passed = actual <= limit
        return AssertionResult(
            assertion_type="max_time_ms",
            passed=passed,
            expected=limit,
            actual=actual,
            message=f"Expected response time <= {limit}ms, received {actual}ms",
        )


class HeaderAssertionEvaluator(BaseAssertionEvaluator):
    """Evaluates HTTP response headers with case-insensitive name matching."""

    def evaluate(
        self,
        rule: dict[str, Any],
        response: HttpResponse,
    ) -> AssertionResult:
        """Evaluate header existence, equality, or substring containment."""
        raw_type = rule.get("type", "header")
        header_name = rule.get("name")
        if (
            not header_name
            or not isinstance(header_name, str)
            or not header_name.strip()
        ):
            return AssertionResult(
                assertion_type=raw_type,
                passed=False,
                expected="string header name",
                actual=header_name,
                message="Header assertion missing required string 'name'.",
            )

        clean_name = header_name.strip()
        found_val: str | None = None
        header_found = False

        for k, v in response.headers.items():
            if k.lower() == clean_name.lower():
                found_val = v
                header_found = True
                break

        # 1. Header existence check
        if raw_type == "header_exists" or (
            raw_type == "header" and "equals" not in rule and "contains" not in rule
        ):
            passed = header_found
            msg = (
                f"Header '{clean_name}' exists"
                if passed
                else f"Expected header '{clean_name}' to exist, but it was not found"
            )
            return AssertionResult(
                assertion_type="header_exists",
                passed=passed,
                expected=f"Header '{clean_name}' exists",
                actual=found_val if header_found else None,
                message=msg,
            )

        # 2. Header equality check
        if raw_type == "header_equals" or "equals" in rule:
            expected_val = str(rule.get("equals"))
            if not header_found:
                return AssertionResult(
                    assertion_type="header_equals",
                    passed=False,
                    expected=expected_val,
                    actual=None,
                    message=(
                        f"Expected header '{clean_name}' to equal '{expected_val}', "
                        f"but header was not found"
                    ),
                )
            passed = str(found_val) == expected_val
            msg = (
                f"Header '{clean_name}' matches '{expected_val}'"
                if passed
                else (
                    f"Expected header '{clean_name}' to equal '{expected_val}', "
                    f"received '{found_val}'"
                )
            )
            return AssertionResult(
                assertion_type="header_equals",
                passed=passed,
                expected=expected_val,
                actual=found_val,
                message=msg,
            )

        # 3. Header contains check
        if "contains" in rule:
            expected_sub = str(rule.get("contains"))
            if not header_found:
                return AssertionResult(
                    assertion_type="header",
                    passed=False,
                    expected=expected_sub,
                    actual=None,
                    message=(
                        f"Expected header '{clean_name}' to contain '{expected_sub}', "
                        f"but header was not found"
                    ),
                )
            passed = expected_sub in str(found_val)
            msg = (
                f"Header '{clean_name}' contains '{expected_sub}'"
                if passed
                else (
                    f"Expected header '{clean_name}' to contain '{expected_sub}', "
                    f"received '{found_val}'"
                )
            )
            return AssertionResult(
                assertion_type="header",
                passed=passed,
                expected=expected_sub,
                actual=found_val,
                message=msg,
            )

        return AssertionResult(
            assertion_type=raw_type,
            passed=False,
            expected="'equals', 'contains', or existence",
            actual=None,
            message=(
                f"Header assertion for '{clean_name}' missing evaluation condition."
            ),
        )


class JsonPathAssertionEvaluator(BaseAssertionEvaluator):
    """Evaluates JSONPath expressions against deserialized JSON response bodies."""

    def evaluate(
        self,
        rule: dict[str, Any],
        response: HttpResponse,
    ) -> AssertionResult:
        """Evaluate JSONPath condition across single or multiple matches."""
        path_expr = rule.get("path")
        if not path_expr or not isinstance(path_expr, str) or not path_expr.strip():
            return AssertionResult(
                assertion_type="jsonpath",
                passed=False,
                expected="valid JSONPath string",
                actual=path_expr,
                message="JSONPath assertion missing required string 'path'.",
            )
        path = path_expr.strip()

        # 1. Parse response body as JSON
        if not response.body or not response.body.strip():
            return AssertionResult(
                assertion_type="jsonpath",
                passed=False,
                expected=self._describe_expected(rule),
                actual="<empty body>",
                message="Response body is empty; cannot evaluate JSONPath.",
            )

        try:
            data = response.json()
        except Exception as err:
            return AssertionResult(
                assertion_type="jsonpath",
                passed=False,
                expected=self._describe_expected(rule),
                actual="<non-JSON body>",
                message=f"Response body is not valid JSON: {err}",
            )

        # 2. Parse JSONPath expression
        try:
            parsed_expr = jsonpath_ng.parse(path)
        except Exception as err:
            return AssertionResult(
                assertion_type="jsonpath",
                passed=False,
                expected="valid JSONPath expression",
                actual=path,
                message=f"Invalid JSONPath expression '{path}': {err}",
            )

        # 3. Find matches
        matches = [m.value for m in parsed_expr.find(data)]
        if not matches:
            return AssertionResult(
                assertion_type="jsonpath",
                passed=False,
                expected=self._describe_expected(rule),
                actual=None,
                message=(
                    f"JSONPath '{path}' found no matching elements in response body."
                ),
            )

        # 4. Evaluate condition across all matches
        failed_items: list[tuple[int, Any, str]] = []
        for idx, item in enumerate(matches):
            item_passed, err_desc = self._evaluate_single(item, rule)
            if not item_passed:
                failed_items.append((idx, item, err_desc))

        expected_desc = self._describe_expected(rule)
        if len(matches) == 1:
            actual_val = matches[0]
            if not failed_items:
                return AssertionResult(
                    assertion_type="jsonpath",
                    passed=True,
                    expected=expected_desc,
                    actual=actual_val,
                    message=f"JSONPath '{path}' matched: {actual_val!r}",
                )
            return AssertionResult(
                assertion_type="jsonpath",
                passed=False,
                expected=expected_desc,
                actual=actual_val,
                message=f"JSONPath '{path}' condition failed: {failed_items[0][2]}",
            )

        # Multiple matches: every match must satisfy the condition
        actual_val = matches
        if not failed_items:
            return AssertionResult(
                assertion_type="jsonpath",
                passed=True,
                expected=expected_desc,
                actual=actual_val,
                message=(
                    f"All {len(matches)} elements matched by '{path}' "
                    f"satisfied condition."
                ),
            )

        first_idx, first_val, first_err = failed_items[0]
        return AssertionResult(
            assertion_type="jsonpath",
            passed=False,
            expected=expected_desc,
            actual=actual_val,
            message=(
                f"JSONPath '{path}' matched {len(matches)} elements but "
                f"{len(failed_items)} failed condition. Element [{first_idx}] "
                f"({first_val!r}) failed: {first_err}"
            ),
        )

    def _describe_expected(self, rule: dict[str, Any]) -> Any:
        """Derive expected value description from rule keys."""
        for key in ("equals", "contains", "greater_than", "regex", "type_check"):
            if key in rule:
                return rule[key]
        return "valid condition"

    def _evaluate_single(
        self,
        item: Any,
        rule: dict[str, Any],
    ) -> tuple[bool, str]:
        """Evaluate a single extracted match against condition rules."""
        # 1. Equality check
        if "equals" in rule:
            expected = rule["equals"]
            if item == expected:
                return True, ""
            return False, f"Expected {expected!r}, received {item!r}"

        # 2. Contains check
        if "contains" in rule:
            expected = rule["contains"]
            if isinstance(item, str):
                if str(expected) in item:
                    return True, ""
                return False, f"Substring {expected!r} not found in {item!r}"
            if isinstance(item, (list, tuple)):
                if expected in item:
                    return True, ""
                return False, f"Element {expected!r} not found in list {item!r}"
            if isinstance(item, dict):
                if expected in item or str(expected) in item:
                    return True, ""
                keys_list = list(item.keys())
                return (
                    False,
                    f"Key {expected!r} not found in dictionary keys {keys_list!r}",
                )
            return (
                False,
                f"Type {type(item).__name__} does not support 'contains' operation",
            )

        # 3. Numeric Greater-Than check
        if "greater_than" in rule:
            threshold = rule["greater_than"]
            if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
                return (
                    False,
                    f"greater_than requires numeric threshold (excluding bool), "
                    f"got {threshold!r}",
                )
            if not isinstance(item, (int, float)) or isinstance(item, bool):
                return (
                    False,
                    f"greater_than requires numeric value (excluding bool), "
                    f"got {type(item).__name__}: {item!r}",
                )
            if item > threshold:
                return True, ""
            return False, f"Expected > {threshold}, received {item}"

        # 4. Regex check
        if "regex" in rule:
            pattern_str = rule["regex"]
            if not isinstance(item, str):
                return (
                    False,
                    f"regex requires string value, got {type(item).__name__}: {item!r}",
                )
            try:
                pattern = re.compile(pattern_str)
            except re.error as err:
                return False, f"Invalid regex pattern '{pattern_str}': {err}"
            if pattern.search(item):
                return True, ""
            return False, f"Value {item!r} does not match regex '{pattern_str}'"

        # 5. Type Check
        if "type_check" in rule:
            expected_type = str(rule["type_check"]).strip().lower()
            return self._check_type(item, expected_type)

        return (
            False,
            "JSONPath assertion missing condition ('equals', 'contains', "
            "'greater_than', 'regex', 'type_check')",
        )

    def _check_type(self, val: Any, expected_type: str) -> tuple[bool, str]:
        """Validate value against expected JSON type specification."""
        valid_types = {
            "string": isinstance(val, str),
            "integer": isinstance(val, int) and not isinstance(val, bool),
            "number": isinstance(val, (int, float)) and not isinstance(val, bool),
            "boolean": isinstance(val, bool),
            "object": isinstance(val, dict),
            "array": isinstance(val, list),
            "null": val is None,
        }
        if expected_type not in valid_types:
            options = ", ".join(sorted(valid_types.keys()))
            return (
                False,
                f"Unsupported type_check '{expected_type}'. Allowed: {options}",
            )

        if valid_types[expected_type]:
            return True, ""
        return (
            False,
            f"Expected type '{expected_type}', received {type(val).__name__} ({val!r})",
        )


class JsonSchemaAssertionEvaluator(BaseAssertionEvaluator):
    """Evaluates HTTP response body against a declarative JSON Schema definition."""

    def evaluate(
        self,
        rule: dict[str, Any],
        response: HttpResponse,
    ) -> AssertionResult:
        """Validate response JSON against provided JSON Schema."""
        schema = rule.get("schema")
        if not schema or not isinstance(schema, dict):
            return AssertionResult(
                assertion_type="json_schema",
                passed=False,
                expected="schema dictionary",
                actual=schema,
                message="JSON Schema assertion missing required 'schema' dictionary.",
            )

        # 1. Validate the schema structure itself
        try:
            validator_cls = jsonschema.validators.validator_for(schema)
            validator_cls.check_schema(schema)
        except jsonschema.exceptions.SchemaError as err:
            return AssertionResult(
                assertion_type="json_schema",
                passed=False,
                expected="valid JSON Schema",
                actual=schema,
                message=f"Invalid JSON Schema definition: {err.message}",
            )
        except Exception as err:
            return AssertionResult(
                assertion_type="json_schema",
                passed=False,
                expected="valid JSON Schema",
                actual=schema,
                message=f"Invalid JSON Schema definition: {err}",
            )

        # 2. Parse response body as JSON
        if not response.body or not response.body.strip():
            return AssertionResult(
                assertion_type="json_schema",
                passed=False,
                expected="JSON Schema conformance",
                actual="<empty body>",
                message="Response body is empty; cannot validate against JSON Schema.",
            )

        try:
            data = response.json()
        except Exception as err:
            return AssertionResult(
                assertion_type="json_schema",
                passed=False,
                expected="JSON Schema conformance",
                actual="<non-JSON body>",
                message=f"Response body is not valid JSON: {err}",
            )

        # 3. Validate response data against schema
        validator = validator_cls(schema)
        errors = list(validator.iter_errors(data))
        if not errors:
            return AssertionResult(
                assertion_type="json_schema",
                passed=True,
                expected="JSON Schema conformance",
                actual="conforming",
                message="Response body conforms to JSON Schema.",
            )

        first_err = errors[0]
        err_path = "/".join(str(p) for p in first_err.path)
        path_str = f" at '#/{err_path}'" if err_path else ""
        msg = f"JSON Schema validation failed{path_str}: {first_err.message}"

        return AssertionResult(
            assertion_type="json_schema",
            passed=False,
            expected=f"valid schema at {path_str or 'root'}",
            actual=first_err.message,
            message=msg,
            diagnostics={
                "error_count": len(errors),
                "path": list(first_err.path),
                "validator": first_err.validator,
            },
        )
