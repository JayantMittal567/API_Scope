"""Historical trend models capturing pass rates and latency metrics over time.

Provides dataclasses representing individual run trend points and aggregate trend
summaries across multiple historical test execution runs.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TrendPoint:
    """Historical metric snapshot for a single test run."""

    __test__ = False

    run_id: str
    started_at: str
    total_tests: int
    passed_tests: int
    pass_rate_pct: float
    avg_elapsed_ms: float
    p95_elapsed_ms: float

    def to_dict(self) -> dict[str, Any]:
        """Convert trend point to a serializable dictionary."""
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "pass_rate_pct": self.pass_rate_pct,
            "avg_elapsed_ms": self.avg_elapsed_ms,
            "p95_elapsed_ms": self.p95_elapsed_ms,
        }


@dataclass(frozen=True)
class TrendSummary:
    """Aggregated historical trend analysis across multiple past test runs."""

    __test__ = False

    module: str | None
    run_count: int
    points: list[TrendPoint]
    overall_pass_rate_pct: float
    overall_avg_elapsed_ms: float

    def to_dict(self) -> dict[str, Any]:
        """Convert trend summary to a serializable dictionary."""
        return {
            "module": self.module,
            "run_count": self.run_count,
            "points": [p.to_dict() for p in self.points],
            "overall_pass_rate_pct": self.overall_pass_rate_pct,
            "overall_avg_elapsed_ms": self.overall_avg_elapsed_ms,
        }
