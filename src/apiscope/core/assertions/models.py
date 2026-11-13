"""Domain models for assertion evaluation outcomes and aggregate summaries.

Provides structured results capturing individual assertion status, expected/actual
values, human-readable explanations, and suite-level verification summaries.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AssertionStatus(StrEnum):
    """Execution status of an individual assertion."""

    PASS = "PASS"
    FAIL = "FAIL"


@dataclass
class AssertionResult:
    """Detailed outcome of a single assertion evaluation.

    Attributes:
        assertion_type: Identifier of the assertion type (e.g., 'status', 'jsonpath').
        passed: Boolean indicating whether the assertion condition was met.
        expected: Expected value, boundary, or condition description.
        actual: Actual value observed from the HTTP response.
        message: Human-readable diagnostic description of the result.
        diagnostics: Optional dictionary of supplementary diagnostic details.
    """

    __test__ = False  # Prevent pytest from treating this as a test class

    assertion_type: str
    passed: bool
    expected: Any
    actual: Any
    message: str
    diagnostics: dict[str, Any] | None = None

    @property
    def status(self) -> AssertionStatus:
        """Return enum status representation."""
        return AssertionStatus.PASS if self.passed else AssertionStatus.FAIL

    def to_dict(self) -> dict[str, Any]:
        """Convert result into a serializable dictionary representation."""
        data: dict[str, Any] = {
            "assertion_type": self.assertion_type,
            "passed": self.passed,
            "status": self.status.value,
            "expected": self.expected,
            "actual": self.actual,
            "message": self.message,
        }
        if self.diagnostics:
            data["diagnostics"] = self.diagnostics
        return data


@dataclass
class AssertionSummary:
    """Aggregate summary of all assertions evaluated against an HTTP response.

    Attributes:
        total: Total number of assertions evaluated.
        passed: Count of assertions that satisfied their conditions.
        failed: Count of assertions that did not satisfy their conditions.
        success: True only if at least one assertion ran and all passed.
        verified: True if assertions were configured and evaluated; False if empty.
        results: Ordered list of individual AssertionResult instances.
        message: Summary message describing the evaluation outcome.
    """

    __test__ = False  # Prevent pytest from treating this as a test class

    total: int
    passed: int
    failed: int
    success: bool
    verified: bool
    results: list[AssertionResult] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert aggregate summary into a serializable dictionary."""
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "success": self.success,
            "verified": self.verified,
            "message": self.message,
            "results": [r.to_dict() for r in self.results],
        }

    def __len__(self) -> int:
        """Return the number of evaluated assertion results."""
        return self.total

    def __iter__(self) -> Any:
        """Iterate over individual AssertionResult instances."""
        return iter(self.results)

    def __getitem__(self, index_or_key: int | str) -> Any:
        """Access results by index or summary properties by key."""
        if isinstance(index_or_key, int):
            return self.results[index_or_key]
        return self.to_dict()[index_or_key]

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve summary property by key with optional default."""
        return self.to_dict().get(key, default)
