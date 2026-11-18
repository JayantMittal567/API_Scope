"""Unit tests for authentic StockPile evidence evaluation (Task 28)."""

from __future__ import annotations

from pathlib import Path

from apiscope.benchmarking.stockpile_evaluator import StockPileEvidenceEvaluator


def test_stockpile_evidence_evaluator_live_repo() -> None:
    """Verify StockPile evaluator accurately reads local recorded artifacts."""
    evaluator = StockPileEvidenceEvaluator(base_dir=Path.cwd())
    summary = evaluator.evaluate()

    # Total documented operations in canonical contract
    assert summary.total_documented_operations == 60
    assert summary.readonly_operations_count == 30
    assert summary.mutating_operations_count == 30

    # Authentic test run outcomes
    assert summary.smoke_tests_passed == 8
    assert summary.extended_get_tests_passed == 30

    # Unverified mutations
    assert summary.unverified_mutating_operations_count == 30

    # Zero fabrications
    assert summary.fabricated_mutations_count == 0
    assert summary.fabricated_application_defects_count == 0
    assert summary.honest_reporting_verified is True


def test_stockpile_summary_to_dict_and_markdown() -> None:
    """Verify StockPileValidationSummary serialization and markdown section formatting."""
    evaluator = StockPileEvidenceEvaluator(base_dir=Path.cwd())
    summary = evaluator.evaluate()

    d = summary.to_dict()
    assert d["total_documented_operations"] == 60
    assert d["honest_reporting_verified"] is True

    md = summary.to_markdown()
    assert "StockPile Evidence Verification" in md
    assert "60 (30 GET, 30 Mutating)" in md
    assert "30 mutating operations" in md
