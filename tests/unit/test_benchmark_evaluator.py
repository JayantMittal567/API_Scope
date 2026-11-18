"""Unit tests for independent benchmark scoring and metrics calculation (Task 28)."""

from __future__ import annotations

from apiscope.benchmarking.evaluator import BenchmarkEvaluator
from apiscope.benchmarking.manifest import create_default_benchmark_manifest
from apiscope.benchmarking.models import (
    CaseEvaluationResult,
    EvaluationClassification,
)


def test_metrics_calculation_standard() -> None:
    """Verify Precision, Recall, F1, and FPR formulas with standard confusion values."""
    manifest = create_default_benchmark_manifest()
    evaluator = BenchmarkEvaluator(manifest)

    # 10 TP, 2 FP, 1 FN, 7 TN
    results = (
        [
            CaseEvaluationResult(
                case_id=f"TP-{i}",
                title="TP",
                is_defect=True,
                category="A",
                expected_verdict="DEFECT",
                classification=EvaluationClassification.TRUE_POSITIVE,
            )
            for i in range(10)
        ]
        + [
            CaseEvaluationResult(
                case_id=f"FP-{i}",
                title="FP",
                is_defect=False,
                category="CLN",
                expected_verdict="CLEAN",
                classification=EvaluationClassification.FALSE_POSITIVE,
            )
            for i in range(2)
        ]
        + [
            CaseEvaluationResult(
                case_id="FN-0",
                title="FN",
                is_defect=True,
                category="B",
                expected_verdict="DEFECT",
                classification=EvaluationClassification.FALSE_NEGATIVE,
            )
        ]
        + [
            CaseEvaluationResult(
                case_id=f"TN-{i}",
                title="TN",
                is_defect=False,
                category="CLN",
                expected_verdict="CLEAN",
                classification=EvaluationClassification.TRUE_NEGATIVE,
            )
            for i in range(7)
        ]
    )

    metrics = evaluator.compute_metrics(results)
    assert metrics.tp == 10
    assert metrics.fp == 2
    assert metrics.fn == 1
    assert metrics.tn == 7
    assert metrics.total_cases == 20

    # Precision = 10 / (10 + 2) = 10/12 ≈ 0.8333
    assert abs(metrics.precision - (10 / 12)) < 1e-4

    # Recall = 10 / (10 + 1) = 10/11 ≈ 0.9091
    assert abs(metrics.recall - (10 / 11)) < 1e-4

    # FPR = 2 / (2 + 7) = 2/9 ≈ 0.2222
    assert abs(metrics.false_positive_rate - (2 / 9)) < 1e-4


def test_metrics_calculation_zero_denominators() -> None:
    """Verify zero-denominator safeguard protects against ZeroDivisionError."""
    manifest = create_default_benchmark_manifest()
    evaluator = BenchmarkEvaluator(manifest)

    # 0 TP, 0 FP, 0 FN, 0 TN
    empty_metrics = evaluator.compute_metrics([])
    assert empty_metrics.precision == 0.0
    assert empty_metrics.recall == 0.0
    assert empty_metrics.f1_score == 0.0
    assert empty_metrics.false_positive_rate == 0.0

    # Clean only: 0 TP, 0 FP, 0 FN, 5 TN
    clean_results = [
        CaseEvaluationResult(
            case_id=f"TN-{i}",
            title="TN",
            is_defect=False,
            category="CLN",
            expected_verdict="CLEAN",
            classification=EvaluationClassification.TRUE_NEGATIVE,
        )
        for i in range(5)
    ]
    clean_metrics = evaluator.compute_metrics(clean_results)
    assert clean_metrics.false_positive_rate == 0.0
    assert clean_metrics.precision == 1.0  # Zero false alarms on clean suite


def test_metrics_deduplication_and_misclassifications() -> None:
    """Verify deduplication rate and misclassification rate computations."""
    manifest = create_default_benchmark_manifest()
    evaluator = BenchmarkEvaluator(manifest)

    results = [
        CaseEvaluationResult(
            case_id="TP-0",
            title="TP",
            is_defect=True,
            category="A",
            expected_verdict="DEFECT",
            classification=EvaluationClassification.TRUE_POSITIVE,
            findings_count=3,
            duplicate_count=2,
        ),
        CaseEvaluationResult(
            case_id="TP-1",
            title="TP",
            is_defect=True,
            category="P_FIXTURE_PRODUCER_FAILURE",
            expected_verdict="FIXTURE",
            classification=EvaluationClassification.TRUE_POSITIVE,
            findings_count=2,
            duplicate_count=1,
            is_misclassified=False,
        ),
    ]

    metrics = evaluator.compute_metrics(results)
    assert metrics.total_raw_findings == 5
    assert metrics.deduplicated_findings == 3
    assert metrics.deduplication_rate == 3 / 5
    assert metrics.misclassifications_count == 0
    assert metrics.misclassification_rate == 0.0
