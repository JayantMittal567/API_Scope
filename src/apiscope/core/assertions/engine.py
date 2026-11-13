"""Assertion evaluation engine coordinating dispatch across specialized evaluators.

Implements the Strategy pattern, providing a centralized registry, isolated
error handling per assertion, and aggregate summary generation.
"""

from collections.abc import Sequence
from typing import Any

from apiscope.core.assertions.evaluators import (
    BaseAssertionEvaluator,
    HeaderAssertionEvaluator,
    JsonPathAssertionEvaluator,
    JsonSchemaAssertionEvaluator,
    ResponseTimeAssertionEvaluator,
    StatusAssertionEvaluator,
)
from apiscope.core.assertions.models import AssertionResult, AssertionSummary
from apiscope.core.contracts import AssertionEngineProtocol
from apiscope.core.models import HttpResponse


class AssertionEngine(AssertionEngineProtocol):
    """Core assertion evaluation engine implementing the Strategy design pattern.

    Dispatches declarative assertion definitions to registered evaluators, aggregates
    individual outcomes, and ensures evaluation failures never crash test execution.
    """

    def __init__(
        self,
        registry: dict[str, BaseAssertionEvaluator] | None = None,
    ) -> None:
        """Initialize engine with standard built-in evaluators."""
        self._registry: dict[str, BaseAssertionEvaluator] = {}
        self._register_defaults()
        if registry:
            for k, v in registry.items():
                self.register_evaluator(k, v)

    def _register_defaults(self) -> None:
        """Register default strategies for all supported assertion types."""
        status_evaluator = StatusAssertionEvaluator()
        time_evaluator = ResponseTimeAssertionEvaluator()
        header_evaluator = HeaderAssertionEvaluator()
        jsonpath_evaluator = JsonPathAssertionEvaluator()
        schema_evaluator = JsonSchemaAssertionEvaluator()

        self.register_evaluator("status", status_evaluator)
        self.register_evaluator("max_time_ms", time_evaluator)
        self.register_evaluator("header_exists", header_evaluator)
        self.register_evaluator("header_equals", header_evaluator)
        self.register_evaluator("header", header_evaluator)
        self.register_evaluator("jsonpath", jsonpath_evaluator)
        self.register_evaluator("json_schema", schema_evaluator)
        self.register_evaluator("schema", schema_evaluator)

    def register_evaluator(
        self,
        assertion_type: str,
        evaluator: BaseAssertionEvaluator,
    ) -> None:
        """Register or override an assertion evaluator strategy.

        Args:
            assertion_type: The identifier string (case-insensitive).
            evaluator: BaseAssertionEvaluator strategy instance.
        """
        self._registry[assertion_type.strip().lower()] = evaluator

    def get_evaluator(self, assertion_type: str) -> BaseAssertionEvaluator | None:
        """Retrieve registered evaluator for a given assertion type."""
        return self._registry.get(assertion_type.strip().lower())

    def evaluate(
        self,
        response: HttpResponse,
        rules: Sequence[dict[str, Any]],
    ) -> AssertionSummary:
        """Evaluate declarative assertion rules against an observed HTTP response.

        Args:
            response: Observed HTTP response domain model.
            rules: Sequence of assertion definition dictionaries.

        Returns:
            AssertionSummary capturing aggregate status and individual results.
        """
        if not rules:
            return AssertionSummary(
                total=0,
                passed=0,
                failed=0,
                success=False,
                verified=False,
                message="No assertions configured - verification incomplete",
                results=[],
            )

        results: list[AssertionResult] = []

        for index, rule in enumerate(rules):
            if not isinstance(rule, dict):
                results.append(
                    AssertionResult(
                        assertion_type="unknown",
                        passed=False,
                        expected="dictionary mapping",
                        actual=type(rule).__name__,
                        message=(
                            f"Assertion [{index}] is invalid: "
                            f"must be a dictionary mapping."
                        ),
                    )
                )
                continue

            raw_type = rule.get("type")
            if not raw_type or not isinstance(raw_type, str) or not raw_type.strip():
                results.append(
                    AssertionResult(
                        assertion_type="unknown",
                        passed=False,
                        expected="string assertion type",
                        actual=raw_type,
                        message=(
                            f"Assertion [{index}] is missing required 'type' field."
                        ),
                    )
                )
                continue

            type_key = raw_type.strip().lower()
            evaluator = self._registry.get(type_key)

            if evaluator is None:
                supported = ", ".join(sorted(self._registry.keys()))
                results.append(
                    AssertionResult(
                        assertion_type=raw_type,
                        passed=False,
                        expected="known assertion type",
                        actual=raw_type,
                        message=(
                            f"Unknown assertion type '{raw_type}'. "
                            f"Supported types: {supported}"
                        ),
                    )
                )
                continue

            try:
                result = evaluator.evaluate(rule, response)
                results.append(result)
            except Exception as err:
                results.append(
                    AssertionResult(
                        assertion_type=raw_type,
                        passed=False,
                        expected="successful evaluation",
                        actual=type(err).__name__,
                        message=(
                            f"Unexpected error evaluating '{raw_type}' assertion: {err}"
                        ),
                    )
                )

        total = len(results)
        passed = sum(1 for r in results if r.passed)
        failed = total - passed
        success = failed == 0 and total > 0
        verified = total > 0
        message = (
            f"All {total} assertions passed"
            if success
            else f"{failed} of {total} assertions failed"
        )

        return AssertionSummary(
            total=total,
            passed=passed,
            failed=failed,
            success=success,
            verified=verified,
            results=results,
            message=message,
        )
