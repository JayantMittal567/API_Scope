"""Unit tests for LatencyDriftAnalyzer and statistical timing regression detection (Task 23).

Covers:
- Median and p95 calculations with various sample distributions.
- Adequate samples regression detection (Case 6).
- Insufficient data rejection below minimum samples threshold (Case 7).
- Incompatible environment detection across origins / environment tags (Case 8).
- Transport error / timeout filtering (None status codes).
- Stable and improved timing classification.
- Serialization to JSON and YAML.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import yaml

from apiscope.analysis.latency_drift import (
    DriftVerdict,
    LatencyDriftAnalyzer,
    _calculate_median,
    _calculate_p95,
)
from apiscope.core.results import CaseResult, CaseStatus, RunResult


def _make_case(
    test_name: str,
    method: str = "GET",
    url: str = "http://localhost:8080/api/items",
    elapsed_ms: float | None = 50.0,
    status_code: int | None = 200,
    module: str = "items",
) -> CaseResult:
    """Helper to build CaseResult with specific timing and status."""
    return CaseResult(
        test_name=test_name,
        module=module,
        tags=["timing"],
        status=CaseStatus.PASSED if status_code == 200 else CaseStatus.FAILED,
        verified=True,
        method=method,
        url=url,
        status_code=status_code,
        elapsed_ms=elapsed_ms,
        request_headers={},
        response_headers={},
        assertion_results=[],
        extracted_variables={},
        extraction_errors=[],
    )


def _make_run(
    run_id: str,
    cases: list[CaseResult],
    env: str | None = "staging",
) -> RunResult:
    """Helper to build RunResult."""
    now = datetime.now(UTC)
    return RunResult(
        run_id=run_id,
        environment_name=env,
        started_at=now,
        finished_at=now,
        duration_ms=sum((c.elapsed_ms or 0.0) for c in cases),
        total_tests=len(cases),
        passed_tests=sum(1 for c in cases if c.status == CaseStatus.PASSED),
        failed_tests=sum(1 for c in cases if c.status == CaseStatus.FAILED),
        error_tests=0,
        skipped_tests=0,
        results=cases,
    )


def test_statistical_helpers_median_and_p95() -> None:
    """Test median and p95 calculations across various distributions."""
    # Empty
    assert _calculate_median([]) == 0.0
    assert _calculate_p95([]) is None

    # Odd count
    odd_values = [10.0, 50.0, 20.0]
    assert _calculate_median(odd_values) == 20.0

    # Even count
    even_values = [10.0, 40.0, 20.0, 30.0]
    assert _calculate_median(even_values) == 25.0

    # p95 calculation
    ten_values = [float(i * 10) for i in range(1, 11)]  # 10..100
    p95 = _calculate_p95(ten_values)
    assert p95 is not None
    assert p95 >= 90.0


def test_latency_drift_detects_regression_with_adequate_samples() -> None:
    """Case 6: Baseline N=10 at 50ms, Current N=10 at 120ms -> REGRESSION_DETECTED."""
    base_cases = [_make_case(f"base_{i}", elapsed_ms=50.0 + (i % 3)) for i in range(10)]
    curr_cases = [
        _make_case(f"curr_{i}", elapsed_ms=120.0 + (i % 5)) for i in range(10)
    ]

    base_run = _make_run("run-base", base_cases, env="prod")
    curr_run = _make_run("run-curr", curr_cases, env="prod")

    analyzer = LatencyDriftAnalyzer(
        min_samples_threshold=5,
        min_p95_samples=10,
        tolerance_factor=1.4,
        min_abs_drift_ms=20.0,
    )
    report = analyzer.compare_runs(base_run, curr_run)

    assert report.has_regressions is True
    assert report.regressions_count == 1
    assert len(report.comparisons) == 1

    comp = report.comparisons[0]
    assert comp.verdict == DriftVerdict.REGRESSION_DETECTED
    assert comp.confidence == "HIGH"
    assert comp.baseline_samples == 10
    assert comp.current_samples == 10
    assert comp.baseline_p95_ms is not None
    assert comp.current_p95_ms is not None
    assert comp.abs_change_ms > 20.0
    assert comp.rel_change_pct > 40.0


def test_latency_drift_insufficient_data_rejection() -> None:
    """Case 7: Insufficient samples (N=3 < min_samples=5) -> INSUFFICIENT_DATA."""
    base_cases = [_make_case(f"b_{i}", elapsed_ms=50.0) for i in range(3)]
    curr_cases = [_make_case(f"c_{i}", elapsed_ms=150.0) for i in range(3)]

    base_run = _make_run("run-b", base_cases)
    curr_run = _make_run("run-c", curr_cases)

    analyzer = LatencyDriftAnalyzer(min_samples_threshold=5)
    report = analyzer.compare_runs(base_run, curr_run)

    assert report.has_regressions is False
    assert report.insufficient_data_count == 1
    comp = report.comparisons[0]
    assert comp.verdict == DriftVerdict.INSUFFICIENT_DATA
    assert comp.confidence == "INSUFFICIENT_DATA"
    assert comp.baseline_p95_ms is None
    assert comp.current_p95_ms is None


def test_latency_drift_incompatible_context_detection() -> None:
    """Case 8: Cross-environment comparison -> INCOMPATIBLE_CONTEXT."""
    base_cases = [
        _make_case(
            f"b_{i}",
            url="http://staging.internal:8080/api/items",
            elapsed_ms=50.0,
        )
        for i in range(10)
    ]
    curr_cases = [
        _make_case(
            f"c_{i}",
            url="http://prod.internal:8080/api/items",
            elapsed_ms=120.0,
        )
        for i in range(10)
    ]

    base_run = _make_run("run-b", base_cases, env="staging")
    curr_run = _make_run("run-c", curr_cases, env="production")

    analyzer = LatencyDriftAnalyzer(strict_environment_match=True)
    report = analyzer.compare_runs(base_run, curr_run)

    assert report.has_regressions is False
    assert report.incompatible_context_count == 1
    comp = report.comparisons[0]
    assert comp.verdict == DriftVerdict.INCOMPATIBLE_CONTEXT
    assert "Target environment mismatch" in comp.details


def test_latency_drift_filters_transport_errors_and_timeouts() -> None:
    """Timeouts and transport errors (status_code is None) must be excluded from latency calculation."""
    base_cases = [
        _make_case(f"b_{i}", elapsed_ms=50.0, status_code=200) for i in range(5)
    ]
    # Add 2 timeout cases
    base_cases.append(_make_case("b_timeout", elapsed_ms=None, status_code=None))
    base_cases.append(_make_case("b_conn_err", elapsed_ms=5000.0, status_code=None))

    curr_cases = [
        _make_case(f"c_{i}", elapsed_ms=52.0, status_code=200) for i in range(5)
    ]
    curr_cases.append(_make_case("c_timeout", elapsed_ms=None, status_code=None))

    base_run = _make_run("run-b", base_cases)
    curr_run = _make_run("run-c", curr_cases)

    analyzer = LatencyDriftAnalyzer(min_samples_threshold=5)
    report = analyzer.compare_runs(base_run, curr_run)

    comp = report.comparisons[0]
    assert comp.baseline_samples == 5
    assert comp.current_samples == 5
    assert comp.verdict == DriftVerdict.STABLE


def test_latency_drift_stable_and_improved_classification() -> None:
    """Verify STABLE within tolerance bounds and IMPROVED on significant latency drop."""
    # Stable test
    base_cases = [_make_case(f"b_{i}", elapsed_ms=100.0) for i in range(6)]
    curr_cases_stable = [_make_case(f"c_{i}", elapsed_ms=105.0) for i in range(6)]

    base_run = _make_run("run-b", base_cases)
    curr_run_stable = _make_run("run-cs", curr_cases_stable)

    analyzer = LatencyDriftAnalyzer(min_samples_threshold=5)
    rep_stable = analyzer.compare_runs(base_run, curr_run_stable)
    assert rep_stable.comparisons[0].verdict == DriftVerdict.STABLE

    # Improved test: 100ms -> 40ms (-60ms, -60%)
    curr_cases_improved = [_make_case(f"ci_{i}", elapsed_ms=40.0) for i in range(6)]
    curr_run_improved = _make_run("run-ci", curr_cases_improved)

    rep_improved = analyzer.compare_runs(base_run, curr_run_improved)
    assert rep_improved.comparisons[0].verdict == DriftVerdict.IMPROVED
    assert rep_improved.improved_count == 1


def test_latency_drift_report_serialization() -> None:
    """Test report to_dict, to_json, and to_yaml serialization round-trips."""
    base_cases = [_make_case(f"b_{i}", elapsed_ms=50.0) for i in range(5)]
    curr_cases = [_make_case(f"c_{i}", elapsed_ms=50.0) for i in range(5)]
    base_run = _make_run("run-b", base_cases)
    curr_run = _make_run("run-c", curr_cases)

    report = LatencyDriftAnalyzer().compare_runs(base_run, curr_run)

    rep_dict = report.to_dict()
    assert "summary_metrics" in rep_dict
    assert "comparisons" in rep_dict

    json_str = report.to_json()
    parsed_json = json.loads(json_str)
    assert parsed_json["summary_metrics"]["total_operations_compared"] == 1

    yaml_str = report.to_yaml()
    parsed_yaml = yaml.safe_load(yaml_str)
    assert parsed_yaml["summary_metrics"]["total_operations_compared"] == 1
