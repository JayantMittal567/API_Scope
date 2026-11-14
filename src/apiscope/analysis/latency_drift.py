"""Latency drift and statistical regression analysis service (Task 23).

Compares historical baseline test execution timing against current runs across
comparable operations, target environments, and sample sizes with conservative
statistical thresholds to prevent false performance regression claims.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

import yaml

from apiscope.core.results import CaseResult, RunResult


class DriftVerdict(StrEnum):
    """Categorization of latency drift outcome for an operation."""

    STABLE = "STABLE"
    REGRESSION_DETECTED = "REGRESSION_DETECTED"
    IMPROVED = "IMPROVED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    INCOMPATIBLE_CONTEXT = "INCOMPATIBLE_CONTEXT"


@dataclass
class LatencyComparison:
    """Statistical latency comparison record for a specific operation."""

    operation: str
    method: str
    route_template: str
    baseline_env: str | None
    current_env: str | None
    baseline_samples: int
    current_samples: int
    baseline_median_ms: float
    current_median_ms: float
    baseline_p95_ms: float | None
    current_p95_ms: float | None
    abs_change_ms: float
    rel_change_pct: float
    verdict: DriftVerdict
    confidence: str
    details: str

    def to_dict(self) -> dict[str, Any]:
        """Convert comparison to dictionary."""
        return {
            "operation": self.operation,
            "method": self.method,
            "route_template": self.route_template,
            "baseline_env": self.baseline_env,
            "current_env": self.current_env,
            "sample_counts": {
                "baseline": self.baseline_samples,
                "current": self.current_samples,
            },
            "metrics": {
                "baseline_median_ms": self.baseline_median_ms,
                "current_median_ms": self.current_median_ms,
                "baseline_p95_ms": self.baseline_p95_ms,
                "current_p95_ms": self.current_p95_ms,
                "abs_change_ms": self.abs_change_ms,
                "rel_change_pct": self.rel_change_pct,
            },
            "verdict": self.verdict.value,
            "confidence": self.confidence,
            "details": self.details,
        }


@dataclass
class LatencyDriftReport:
    """Comprehensive latency drift diagnostic report across evaluated operations."""

    evaluated_at: str
    baseline_run_id: str | None
    current_run_id: str | None
    baseline_env: str | None
    current_env: str | None
    total_operations_compared: int
    regressions_count: int
    stable_count: int
    improved_count: int
    insufficient_data_count: int
    incompatible_context_count: int
    has_regressions: bool
    comparisons: list[LatencyComparison] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert report to JSON-serializable dictionary."""
        return {
            "evaluated_at": self.evaluated_at,
            "baseline_run_id": self.baseline_run_id,
            "current_run_id": self.current_run_id,
            "baseline_env": self.baseline_env,
            "current_env": self.current_env,
            "summary_metrics": {
                "total_operations_compared": self.total_operations_compared,
                "regressions_count": self.regressions_count,
                "stable_count": self.stable_count,
                "improved_count": self.improved_count,
                "insufficient_data_count": self.insufficient_data_count,
                "incompatible_context_count": self.incompatible_context_count,
                "has_regressions": self.has_regressions,
            },
            "summary": self.summary,
            "comparisons": [c.to_dict() for c in self.comparisons],
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize report to formatted JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def to_yaml(self) -> str:
        """Serialize report to YAML string."""
        return yaml.safe_dump(self.to_dict(), sort_keys=False)


def _calculate_median(values: list[float]) -> float:
    """Compute 50th percentile (median) of sorted float values."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return round(s[mid], 2)
    return round((s[mid - 1] + s[mid]) / 2.0, 2)


def _calculate_p95(values: list[float]) -> float | None:
    """Compute 95th percentile of float values."""
    if not values:
        return None
    s = sorted(values)
    idx = int(math.ceil(0.95 * len(s))) - 1
    idx = max(0, min(idx, len(s) - 1))
    return round(s[idx], 2)


def _extract_origin(url: str) -> str:
    """Extract scheme + netloc from a URL."""
    try:
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass
    return ""


class LatencyDriftAnalyzer:
    """Defensible latency regression detector enforcing statistical comparability."""

    def __init__(
        self,
        min_samples_threshold: int = 5,
        min_p95_samples: int = 10,
        tolerance_factor: float = 1.4,
        min_abs_drift_ms: float = 20.0,
        strict_environment_match: bool = True,
    ) -> None:
        """Initialize latency drift analyzer.

        Args:
            min_samples_threshold: Minimum sample count required for any verdict.
            min_p95_samples: Minimum sample count required to compute p95.
            tolerance_factor: Relative threshold factor (1.4 = +40% increase).
            min_abs_drift_ms: Minimum absolute latency increase in ms to flag regression.
            strict_environment_match: Whether to reject cross-environment comparisons.
        """
        self.min_samples = min_samples_threshold
        self.min_p95_samples = min_p95_samples
        self.tolerance_factor = tolerance_factor
        self.min_abs_drift_ms = min_abs_drift_ms
        self.strict_env = strict_environment_match

    def compare_runs(
        self,
        baseline_run: RunResult,
        current_run: RunResult,
    ) -> LatencyDriftReport:
        """Compare latency measurements between two executed RunResults.

        Args:
            baseline_run: Historical baseline test run.
            current_run: Recent test run to compare against baseline.

        Returns:
            Structured LatencyDriftReport.
        """
        return self.compare_case_samples(
            baseline_cases=baseline_run.results,
            current_cases=current_run.results,
            baseline_run_id=baseline_run.run_id,
            current_run_id=current_run.run_id,
            baseline_env=baseline_run.environment_name,
            current_env=current_run.environment_name,
        )

    def compare_case_samples(
        self,
        baseline_cases: list[CaseResult],
        current_cases: list[CaseResult],
        baseline_run_id: str | None = None,
        current_run_id: str | None = None,
        baseline_env: str | None = None,
        current_env: str | None = None,
    ) -> LatencyDriftReport:
        """Compare latency across matching operation buckets with statistical safeguards."""
        # 1. Filter completed successful/response-bearing cases (exclude transport timeouts)
        base_by_op = self._bucket_cases(baseline_cases)
        curr_by_op = self._bucket_cases(current_cases)

        # Collect distinct operations
        all_ops = sorted(set(base_by_op.keys()) | set(curr_by_op.keys()))

        comparisons: list[LatencyComparison] = []
        regressions_count = 0
        stable_count = 0
        improved_count = 0
        insufficient_count = 0
        incompatible_count = 0

        for op in all_ops:
            base_items = base_by_op.get(op, [])
            curr_items = curr_by_op.get(op, [])

            method = ""
            route = ""
            if base_items:
                method = base_items[0].method
                route = base_items[0].url
            elif curr_items:
                method = curr_items[0].method
                route = curr_items[0].url

            # Extract origins
            base_origin = _extract_origin(base_items[0].url) if base_items else ""
            curr_origin = _extract_origin(curr_items[0].url) if curr_items else ""

            # Check environment comparability
            is_env_incompatible = False
            if (
                self.strict_env
                and base_origin
                and curr_origin
                and base_origin != curr_origin
                or (
                    self.strict_env
                    and baseline_env
                    and current_env
                    and baseline_env.strip().lower() != current_env.strip().lower()
                )
            ):
                is_env_incompatible = True

            base_lats = [c.elapsed_ms for c in base_items if c.elapsed_ms is not None]
            curr_lats = [c.elapsed_ms for c in curr_items if c.elapsed_ms is not None]

            base_n = len(base_lats)
            curr_n = len(curr_lats)

            base_med = _calculate_median(base_lats)
            curr_med = _calculate_median(curr_lats)

            base_p95 = (
                _calculate_p95(base_lats) if base_n >= self.min_p95_samples else None
            )
            curr_p95 = (
                _calculate_p95(curr_lats) if curr_n >= self.min_p95_samples else None
            )

            abs_change = round(curr_med - base_med, 2)
            rel_change = (
                round((abs_change / base_med) * 100.0, 2) if base_med > 0 else 0.0
            )

            if is_env_incompatible:
                verdict = DriftVerdict.INCOMPATIBLE_CONTEXT
                confidence = "INCOMPATIBLE_CONTEXT"
                details = (
                    f"Target environment mismatch ('{base_origin or baseline_env}' vs "
                    f"'{curr_origin or current_env}'); cross-environment comparison is invalid."
                )
                incompatible_count += 1
            elif base_n < self.min_samples or curr_n < self.min_samples:
                verdict = DriftVerdict.INSUFFICIENT_DATA
                confidence = "INSUFFICIENT_DATA"
                details = (
                    f"Insufficient sampling (baseline: {base_n}, current: {curr_n}; "
                    f"required: {self.min_samples}). Regression cannot be confirmed."
                )
                insufficient_count += 1
            else:
                # Statistical regression check
                # Requires both relative increase exceeding tolerance factor AND absolute drift >= min_abs_drift_ms
                tolerated_median = base_med * self.tolerance_factor
                if curr_med > tolerated_median and abs_change >= self.min_abs_drift_ms:
                    verdict = DriftVerdict.REGRESSION_DETECTED
                    confidence = "HIGH" if (base_n >= 10 and curr_n >= 10) else "MEDIUM"
                    details = (
                        f"Median latency regressed by +{abs_change} ms (+{rel_change}%); "
                        f"current {curr_med} ms exceeded tolerated limit {tolerated_median:.1f} ms."
                    )
                    regressions_count += 1
                elif abs_change < -self.min_abs_drift_ms and rel_change <= -20.0:
                    verdict = DriftVerdict.IMPROVED
                    confidence = "MEDIUM"
                    details = (
                        f"Median latency improved by {abs_change} ms ({rel_change}%)."
                    )
                    improved_count += 1
                else:
                    verdict = DriftVerdict.STABLE
                    confidence = "HIGH"
                    details = f"Latency remains within expected tolerance bounds ({abs_change:+} ms)."
                    stable_count += 1

            comparisons.append(
                LatencyComparison(
                    operation=op,
                    method=method,
                    route_template=route,
                    baseline_env=baseline_env,
                    current_env=current_env,
                    baseline_samples=base_n,
                    current_samples=curr_n,
                    baseline_median_ms=base_med,
                    current_median_ms=curr_med,
                    baseline_p95_ms=base_p95,
                    current_p95_ms=curr_p95,
                    abs_change_ms=abs_change,
                    rel_change_pct=rel_change,
                    verdict=verdict,
                    confidence=confidence,
                    details=details,
                )
            )

        has_reg = regressions_count > 0

        # Compose summary
        summary = (
            f"Evaluated {len(comparisons)} operations: {regressions_count} regression(s), "
            f"{stable_count} stable, {improved_count} improved, "
            f"{insufficient_count} with insufficient data, {incompatible_count} incompatible context."
        )

        return LatencyDriftReport(
            evaluated_at=datetime.now(UTC).isoformat(),
            baseline_run_id=baseline_run_id,
            current_run_id=current_run_id,
            baseline_env=baseline_env,
            current_env=current_env,
            total_operations_compared=len(comparisons),
            regressions_count=regressions_count,
            stable_count=stable_count,
            improved_count=improved_count,
            insufficient_data_count=insufficient_count,
            incompatible_context_count=incompatible_count,
            has_regressions=has_reg,
            comparisons=comparisons,
            summary=summary,
        )

    @staticmethod
    def _bucket_cases(cases: list[CaseResult]) -> dict[str, list[CaseResult]]:
        """Group cases by normalized operation bucket, excluding timeouts/failed requests."""
        buckets: dict[str, list[CaseResult]] = {}
        for c in cases:
            # Must have received an HTTP response (timeouts have status_code is None)
            if c.status_code is None or c.elapsed_ms is None:
                continue

            # Normalized op key: prefer module + method, or parsed path
            parsed_path = ""
            try:
                parsed_path = urlparse(c.url).path
            except Exception:
                parsed_path = c.url

            op_key = f"{c.method.upper()} {parsed_path or c.module}"
            buckets.setdefault(op_key, []).append(c)
        return buckets
