"""Execution outcome models capturing per-test case and suite-level results.

Provides immutable, JSON-serializable dataclasses representing test outcomes,
timing metrics, assertion diagnostics, and secret-masked metadata.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class CaseStatus(StrEnum):
    """Execution status for an individual test case or workflow action."""

    __test__ = False

    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"
    CLEANUP_FAILED = "CLEANUP_FAILED"


# Backward-compatibility alias
TestStatus = CaseStatus


@dataclass(frozen=True)
class CaseResult:
    """Immutable record of the execution outcome of a single test case."""

    __test__ = False

    test_name: str
    module: str
    tags: list[str]
    status: CaseStatus
    verified: bool
    method: str
    url: str
    status_code: int | None
    elapsed_ms: float | None
    request_headers: dict[str, str]
    response_headers: dict[str, str]
    assertion_results: list[dict[str, Any]]
    extracted_variables: dict[str, Any]
    extraction_errors: list[str]
    error_message: str | None = None
    skip_reason: str | None = None
    row_index: int | None = None
    response_body: str = ""
    correlation_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert result to a 100% JSON-serializable dictionary."""
        return {
            "test_name": self.test_name,
            "module": self.module,
            "tags": list(self.tags),
            "status": self.status.value,
            "verified": self.verified,
            "method": self.method,
            "url": self.url,
            "status_code": self.status_code,
            "elapsed_ms": self.elapsed_ms,
            "request_headers": dict(self.request_headers),
            "response_headers": dict(self.response_headers),
            "assertion_results": [
                dict(r) if isinstance(r, dict) else r.to_dict()
                for r in self.assertion_results
            ],
            "extracted_variables": dict(self.extracted_variables),
            "extraction_errors": list(self.extraction_errors),
            "error_message": self.error_message,
            "skip_reason": self.skip_reason,
            "row_index": self.row_index,
            "response_body": self.response_body,
            "correlation_id": self.correlation_id,
        }


# Backward-compatibility alias
TestResult = CaseResult


@dataclass(frozen=True)
class RunResult:
    """Aggregated outcome of an entire test collection execution run."""

    __test__ = False

    run_id: str
    environment_name: str | None
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    total_tests: int
    passed_tests: int
    failed_tests: int
    error_tests: int
    skipped_tests: int
    results: list[CaseResult]
    has_warnings: bool = False
    blocked_tests: int = 0

    @property
    def all_passed(self) -> bool:
        """Return True only if at least one test ran and all tests passed."""
        return self.total_tests > 0 and self.passed_tests == self.total_tests

    def to_dict(self) -> dict[str, Any]:
        """Convert run result to a 100% JSON-serializable dictionary."""
        return {
            "run_id": self.run_id,
            "environment_name": self.environment_name,
            "started_at": (
                self.started_at.isoformat()
                if isinstance(self.started_at, datetime)
                else str(self.started_at)
            ),
            "finished_at": (
                self.finished_at.isoformat()
                if isinstance(self.finished_at, datetime)
                else str(self.finished_at)
            ),
            "duration_ms": self.duration_ms,
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "error_tests": self.error_tests,
            "skipped_tests": self.skipped_tests,
            "blocked_tests": self.blocked_tests,
            "all_passed": self.all_passed,
            "has_warnings": self.has_warnings,
            "results": [r.to_dict() for r in self.results],
        }
