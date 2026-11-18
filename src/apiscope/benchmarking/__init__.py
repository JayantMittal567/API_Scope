"""ApiScope Benchmarking & Defect-Detection Accuracy Evaluation Package (Task 28).

Provides independent ground-truth manifests, controlled reference applications with seeded
defects, post-hoc accuracy evaluation, and authentic StockPile artifact verification.
"""

from __future__ import annotations

from apiscope.benchmarking.evaluator import BenchmarkEvaluator
from apiscope.benchmarking.manifest import (
    BENCHMARK_MANIFEST_VERSION,
    build_default_ground_truth_cases,
    create_default_benchmark_manifest,
    export_benchmark_manifest,
    load_benchmark_manifest,
)
from apiscope.benchmarking.models import (
    BenchmarkCase,
    BenchmarkManifest,
    BenchmarkMetrics,
    BenchmarkReport,
    CaseEvaluationResult,
    CategoryAccuracyMetrics,
    CleanControlCategory,
    EvaluationClassification,
    PlantedDefectCategory,
    StockPileValidationSummary,
)
from apiscope.benchmarking.reference_app import (
    BenchmarkAppConfig,
    create_benchmark_app,
    get_benchmark_openapi_spec,
)
from apiscope.benchmarking.service import BenchmarkService
from apiscope.benchmarking.stockpile_evaluator import StockPileEvidenceEvaluator

__all__ = [
    "BENCHMARK_MANIFEST_VERSION",
    "BenchmarkAppConfig",
    "BenchmarkCase",
    "BenchmarkEvaluator",
    "BenchmarkManifest",
    "BenchmarkMetrics",
    "BenchmarkReport",
    "BenchmarkService",
    "CaseEvaluationResult",
    "CategoryAccuracyMetrics",
    "CleanControlCategory",
    "EvaluationClassification",
    "PlantedDefectCategory",
    "StockPileEvidenceEvaluator",
    "StockPileValidationSummary",
    "build_default_ground_truth_cases",
    "create_benchmark_app",
    "create_default_benchmark_manifest",
    "export_benchmark_manifest",
    "get_benchmark_openapi_spec",
    "load_benchmark_manifest",
]
