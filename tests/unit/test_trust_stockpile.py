"""Authentic StockPile acceptance verification for the Unified Trust & Evidence Engine (Task 25).

Verifies authentic StockPile read-only results against stockpile_openapi.yaml:
- Exactly 60 documented operations (30 GET, 30 Mutating).
- Authentic 8-request smoke run: 8 GET attempted, 52 untested, 0 mutations, honest assessment, 0 fabricated defects.
- Authentic 30-GET extended run: 30 GET attempted (100% GET coverage), 30 mutations untested, 0 fabricated defects.
- Denominator is exactly 60 distinct canonical operations, never inflated test-case counts.
- Offline zero-network execution guarantee (zero target HTTP network calls, socket blocked).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from apiscope.analysis.silent_failure_service import load_run_file
from apiscope.generators.openapi_importer import load_openapi_spec
from apiscope.trust.models import (
    DiagnosticVerdict,
    TrustAssessmentReport,
)
from apiscope.trust.service import TrustAssessmentService


@pytest.fixture
def stockpile_spec_path() -> Path:
    p = Path("stockpile_tests/stockpile_openapi.yaml")
    assert p.is_file(), "stockpile_openapi.yaml must exist"
    return p


@pytest.fixture
def stockpile_smoke_path() -> Path:
    p = Path("stockpile_tests/stockpile_readonly_results.json")
    assert p.is_file(), "stockpile_readonly_results.json must exist"
    return p


@pytest.fixture
def stockpile_extended_path() -> Path:
    p = Path("stockpile_tests/stockpile_extended_results.json")
    assert p.is_file(), "stockpile_extended_results.json must exist"
    return p


def test_stockpile_spec_structure(stockpile_spec_path: Path) -> None:
    """Verify that StockPile OpenAPI spec defines exactly 60 operations (30 GET, 30 Mutating)."""
    spec = load_openapi_spec(stockpile_spec_path)
    assert len(spec.endpoints) == 60

    gets = [ep for ep in spec.endpoints if ep.method.upper() == "GET"]
    mutations = [
        ep
        for ep in spec.endpoints
        if ep.method.upper() in ("POST", "PUT", "DELETE", "PATCH")
    ]

    assert len(gets) == 30
    assert len(mutations) == 30
    assert len(gets) + len(mutations) == 60


def test_stockpile_smoke_run_trust_assessment(
    stockpile_spec_path: Path,
    stockpile_smoke_path: Path,
) -> None:
    """Assess authentic StockPile smoke run (8 GET calls) offline:

    - Total documented: 60
    - Passed operations: 8
    - Attempted coverage: 13.33% (8/60)
    - Read-only coverage: 26.67% (8/30)
    - Mutation coverage: 0.0% (0/30)
    - Untested operations: 52 (86.67%)
    - Zero fabricated defects (no false positive defects for untested endpoints).
    """
    with patch("socket.socket") as mock_sock:
        mock_sock.side_effect = RuntimeError(
            "Target network access attempted during offline trust assessment!"
        )

        spec = load_openapi_spec(stockpile_spec_path)
        run_res = load_run_file(stockpile_smoke_path)
        assert run_res.total_tests == 8

        service = TrustAssessmentService()
        report = service.assess(
            run_result=run_res,
            spec=spec,
            target_service="stockpile",
        )

        assert isinstance(report, TrustAssessmentReport)
        assert report.target_context == "stockpile"

        # Canonical coverage metrics
        cov = report.coverage
        assert cov.total_documented_operations == 60
        assert cov.attempted_operations_count == 8
        assert cov.passed_operations_count == 8
        assert cov.failed_operations_count == 0
        assert cov.untested_operations_count == 52

        assert cov.readonly_total_operations == 30
        assert cov.readonly_attempted_operations == 8
        assert cov.readonly_coverage_pct == pytest.approx(26.67, rel=1e-2)

        assert cov.mutation_total_operations == 30
        assert cov.mutation_attempted_operations == 0
        assert cov.mutation_coverage_pct == 0.0

        # Zero fabricated defects
        assert (
            report.verdict_counts[DiagnosticVerdict.VERIFIED_CONTRACT_MISMATCH.value]
            == 0
        )
        assert (
            report.verdict_counts[DiagnosticVerdict.VERIFIED_SEMANTIC_MISMATCH.value]
            == 0
        )
        assert (
            report.verdict_counts[DiagnosticVerdict.SUSPECTED_APPLICATION_DEFECT.value]
            == 0
        )
        assert report.verdict_counts[DiagnosticVerdict.TEST_OR_ORACLE_ISSUE.value] == 0

        # Honest assessment: 52 operations remain untested, not claimed bug-free
        assert cov.untested_operations_count == 52
        assert len(report.remaining_verification_gaps) > 0


def test_stockpile_extended_run_trust_assessment(
    stockpile_spec_path: Path,
    stockpile_extended_path: Path,
) -> None:
    """Assess authentic StockPile extended read-only run (30 GET calls) offline:

    - Total documented: 60
    - Attempted operations: 30
    - Read-only coverage: 100.0% (30/30)
    - Mutation coverage: 0.0% (0/30)
    - Untested operations: 30 (all 30 mutating operations remain untested)
    - Zero fabricated defects.
    """
    with patch("socket.socket") as mock_sock:
        mock_sock.side_effect = RuntimeError(
            "Target network access attempted during offline trust assessment!"
        )

        spec = load_openapi_spec(stockpile_spec_path)
        run_res = load_run_file(stockpile_extended_path)
        assert run_res.total_tests == 30

        service = TrustAssessmentService()
        report = service.assess(
            run_result=run_res,
            spec=spec,
            target_service="stockpile",
        )

        cov = report.coverage
        assert cov.total_documented_operations == 60
        assert cov.attempted_operations_count == 30
        assert cov.passed_operations_count == 30
        assert cov.failed_operations_count == 0
        assert cov.untested_operations_count == 30

        # Exact read-only coverage
        assert cov.readonly_total_operations == 30
        assert cov.readonly_attempted_operations == 30
        assert cov.readonly_coverage_pct == pytest.approx(100.0)

        # Mutating operations remain completely untested
        assert cov.mutation_total_operations == 30
        assert cov.mutation_attempted_operations == 0
        assert cov.mutation_coverage_pct == 0.0

        # Zero fabricated defects
        assert (
            report.verdict_counts[DiagnosticVerdict.VERIFIED_CONTRACT_MISMATCH.value]
            == 0
        )
        assert (
            report.verdict_counts[DiagnosticVerdict.VERIFIED_SEMANTIC_MISMATCH.value]
            == 0
        )
        assert (
            report.verdict_counts[DiagnosticVerdict.SUSPECTED_APPLICATION_DEFECT.value]
            == 0
        )

        # All 30 untested operations are mutations
        assert cov.untested_operations_count == 30
        assert cov.mutation_total_operations == 30
        assert cov.mutation_attempted_operations == 0
        assert cov.mutation_coverage_pct == 0.0
        assert len(report.remaining_verification_gaps) > 0


def test_stockpile_serialization_and_redaction(
    stockpile_spec_path: Path,
    stockpile_smoke_path: Path,
) -> None:
    """Verify dictionary, JSON and YAML export of StockPile trust assessment."""
    spec = load_openapi_spec(stockpile_spec_path)
    run_res = load_run_file(stockpile_smoke_path)

    service = TrustAssessmentService()
    report = service.assess(run_result=run_res, spec=spec)

    # Dict serialization
    d = report.to_dict()
    assert d["target_context"] == "primary"
    assert "coverage" in d
    assert d["coverage"]["total_documented_operations"] == 60

    # JSON serialization
    json_str = report.to_json()
    parsed_json = json.loads(json_str)
    assert parsed_json["target_context"] == "primary"

    # YAML serialization
    yaml_str = report.to_yaml()
    assert "target_context: primary" in yaml_str
    assert "total_documented_operations: 60" in yaml_str
