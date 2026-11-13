"""Domain models and data structures for module health evaluation.

Provides immutable, JSON-serializable dataclasses and enums representing
verdicts, category check statistics, and comprehensive module health reports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class HealthVerdict(StrEnum):
    """Deterministic verdict state for a software module's API health."""

    __test__ = False

    HEALTHY = "HEALTHY"
    HEALTHY_WITH_WARNINGS = "HEALTHY WITH WARNINGS"
    UNHEALTHY = "UNHEALTHY"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True)
class CategoryCheckStats:
    """Statistical summary for a specific category of test checks."""

    __test__ = False

    category: str
    total: int
    passed: int
    failed: int
    pass_rate_pct: float

    def to_dict(self) -> dict[str, Any]:
        """Convert category check statistics to a dictionary."""
        return {
            "category": self.category,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate_pct": self.pass_rate_pct,
        }


@dataclass(frozen=True)
class ModuleHealthReport:
    """Evidence-based health evaluation report for a software module."""

    __test__ = False

    module: str
    verdict: HealthVerdict
    run_id: str
    evaluated_at: datetime
    total_tests: int
    passed_tests: int
    failed_tests: int
    error_tests: int
    skipped_tests: int
    total_assertions: int
    passed_assertions: int
    pass_rate_pct: float
    spec_endpoints_total: int
    spec_endpoints_tested: int
    endpoint_coverage_pct: float
    untested_endpoints: list[str]
    coverage_threshold_pct: float
    functional_stats: CategoryCheckStats
    contract_stats: CategoryCheckStats
    negative_boundary_stats: CategoryCheckStats
    auth_stats: CategoryCheckStats
    avg_latency_ms: float
    p95_latency_ms: float
    baseline_p95_ms: float | None
    latency_regression: bool
    zombie_count: int
    silent_failure_count: int
    summary: str
    evidence: list[str]
    warnings: list[str]
    failures: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Convert module health report to a 100% JSON-serializable dictionary."""
        return {
            "module": self.module,
            "verdict": self.verdict.value,
            "run_id": self.run_id,
            "evaluated_at": self.evaluated_at.isoformat(),
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "error_tests": self.error_tests,
            "skipped_tests": self.skipped_tests,
            "total_assertions": self.total_assertions,
            "passed_assertions": self.passed_assertions,
            "pass_rate_pct": self.pass_rate_pct,
            "spec_endpoints_total": self.spec_endpoints_total,
            "spec_endpoints_tested": self.spec_endpoints_tested,
            "endpoint_coverage_pct": self.endpoint_coverage_pct,
            "untested_endpoints": list(self.untested_endpoints),
            "coverage_threshold_pct": self.coverage_threshold_pct,
            "functional_stats": self.functional_stats.to_dict(),
            "contract_stats": self.contract_stats.to_dict(),
            "negative_boundary_stats": self.negative_boundary_stats.to_dict(),
            "auth_stats": self.auth_stats.to_dict(),
            "avg_latency_ms": self.avg_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "baseline_p95_ms": self.baseline_p95_ms,
            "latency_regression": self.latency_regression,
            "zombie_count": self.zombie_count,
            "silent_failure_count": self.silent_failure_count,
            "summary": self.summary,
            "evidence": list(self.evidence),
            "warnings": list(self.warnings),
            "failures": list(self.failures),
        }
