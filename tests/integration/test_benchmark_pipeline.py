"""Integration tests for the complete benchmark orchestration service (Task 28)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from apiscope.benchmarking.service import BenchmarkService


def test_benchmark_service_run_all(tmp_path: Path) -> None:
    """Verify BenchmarkService runs all 32 cases and produces all 5 report artifacts."""
    svc = BenchmarkService(base_dir=Path.cwd())
    report = svc.run_benchmark(mode="all", output_dir=tmp_path)

    # Check metrics
    m = report.metrics
    assert m.total_cases == 32
    assert m.tp == 16
    assert m.tn == 16
    assert m.fp == 0
    assert m.fn == 0
    assert m.precision == 1.0
    assert m.recall == 1.0
    assert m.f1_score == 1.0
    assert m.false_positive_rate == 0.0

    # Check exported artifacts on disk
    assert (tmp_path / "ground_truth_manifest.json").is_file()
    assert (tmp_path / "ground_truth_manifest.yaml").is_file()
    assert (tmp_path / "benchmark_report.json").is_file()
    assert (tmp_path / "benchmark_report.yaml").is_file()
    assert (tmp_path / "BENCHMARK_SUMMARY.md").is_file()

    # Verify JSON content
    raw_json = json.loads(
        (tmp_path / "benchmark_report.json").read_text(encoding="utf-8")
    )
    assert raw_json["manifest_version"] == "1.0.0"
    assert raw_json["metrics"]["tp"] == 16

    # Verify YAML content
    raw_yaml = yaml.safe_load(
        (tmp_path / "benchmark_report.yaml").read_text(encoding="utf-8")
    )
    assert raw_yaml["metrics"]["recall"] == 1.0

    # Verify Markdown summary
    summary_md = (tmp_path / "BENCHMARK_SUMMARY.md").read_text(encoding="utf-8")
    assert "APISCOPE 1.0 — BENCHMARK EVALUATION & ACCURACY REPORT" in summary_md
    assert "Authentic StockPile Evidence Verification" in summary_md


def test_benchmark_service_modes(tmp_path: Path) -> None:
    """Verify BenchmarkService supports 'defects', 'clean', and 'stockpile' modes."""
    svc = BenchmarkService(base_dir=Path.cwd())

    # Defects mode
    rep_defects = svc.run_benchmark(mode="defects", output_dir=tmp_path / "defects")
    assert rep_defects.metrics.total_cases == 16
    assert rep_defects.metrics.defect_cases_count == 16
    assert rep_defects.metrics.clean_cases_count == 0

    # Clean mode
    rep_clean = svc.run_benchmark(mode="clean", output_dir=tmp_path / "clean")
    assert rep_clean.metrics.total_cases == 16
    assert rep_clean.metrics.defect_cases_count == 0
    assert rep_clean.metrics.clean_cases_count == 16
    assert rep_clean.metrics.fp == 0
    assert rep_clean.metrics.tn == 16

    # StockPile mode
    rep_st = svc.run_benchmark(mode="stockpile", output_dir=tmp_path / "stockpile")
    assert rep_st.metrics.total_cases == 0
    assert rep_st.stockpile_validation.honest_reporting_verified is True
