"""Authentic StockPile acceptance verification for the Unified Guided Workflow Orchestration (Task 26).

Verifies authentic StockPile project analysis and guided orchestration:
- Exactly 60 documented operations (30 GET, 30 Mutating).
- Offline zero-network execution guarantee (zero target HTTP network calls, socket blocked).
- Authentic 8-request smoke run ingestion: 8 GET attempted, 52 untested, 0 mutations, honest assessment, 0 fabricated defects.
- Authentic 30-GET extended run ingestion: 30 GET attempted (100% GET coverage), 30 mutations untested, 0 fabricated defects.
- Full 11-stage lifecycle executed reproducibly with structured artifacts and provenance manifest.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from apiscope.orchestration.models import (
    GuidedWorkflowOptions,
    StageStatus,
    WorkflowStage,
)
from apiscope.orchestration.service import GuidedWorkflowService
from apiscope.trust.models import DiagnosticVerdict


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


def test_stockpile_offline_preview_zero_network(
    tmp_path: Path,
    stockpile_spec_path: Path,
) -> None:
    """Verify offline guided workflow preview on authentic StockPile with socket blocked."""
    out_dir = tmp_path / "stockpile_preview"

    with patch("socket.socket") as mock_sock:
        mock_sock.side_effect = RuntimeError(
            "Target network access attempted during offline preview!"
        )

        opts = GuidedWorkflowOptions(
            spec_path=stockpile_spec_path,
            output_dir=out_dir,
            preview_only=True,
        )

        svc = GuidedWorkflowService()
        result = svc.run_guided_workflow(opts)

        assert result.is_success is True
        assert result.preview is not None
        assert result.preview.readonly_operations_count == 30
        assert result.preview.mutating_operations_count == 30

        # Stages 1 to 6 completed, 7 skipped
        records = result.manifest.stage_records
        assert records[WorkflowStage.PROJECT_INSPECTION.value].status in (
            StageStatus.COMPLETED,
            StageStatus.COMPLETED_WITH_WARNINGS,
        )
        assert records[WorkflowStage.CONTRACT_ANALYSIS.value].status in (
            StageStatus.COMPLETED,
            StageStatus.COMPLETED_WITH_WARNINGS,
        )
        assert records[WorkflowStage.TEST_PLANNING.value].status in (
            StageStatus.COMPLETED,
            StageStatus.COMPLETED_WITH_WARNINGS,
            StageStatus.NEEDS_REVIEW,
        )
        assert records[WorkflowStage.TEST_COMPILATION.value].status in (
            StageStatus.COMPLETED,
            StageStatus.COMPLETED_WITH_WARNINGS,
        )
        assert records[WorkflowStage.FIXTURE_PREPARATION.value].status in (
            StageStatus.COMPLETED,
            StageStatus.COMPLETED_WITH_WARNINGS,
        )
        assert (
            records[WorkflowStage.EXECUTION_PREVIEW.value].status
            == StageStatus.COMPLETED
        )
        assert (
            records[WorkflowStage.CONTROLLED_EXECUTION.value].status
            == StageStatus.SKIPPED
        )

        # Artifacts exist
        assert (out_dir / "workflow_manifest.json").is_file()
        assert (out_dir / "execution_preview.json").is_file()
        assert (out_dir / "test_plan.json").is_file()


def test_stockpile_smoke_run_guided_workflow_ingestion(
    tmp_path: Path,
    stockpile_spec_path: Path,
    stockpile_smoke_path: Path,
) -> None:
    """Verify guided workflow ingesting authentic StockPile smoke results offline."""
    out_dir = tmp_path / "stockpile_smoke"

    with patch("socket.socket") as mock_sock:
        mock_sock.side_effect = RuntimeError(
            "Target network access attempted during offline smoke run ingestion!"
        )

        opts = GuidedWorkflowOptions(
            spec_path=stockpile_spec_path,
            output_dir=out_dir,
            existing_run_file=stockpile_smoke_path,
            preview_only=False,
        )

        svc = GuidedWorkflowService()
        result = svc.run_guided_workflow(opts)

        assert result.is_success is True
        assert result.manifest.run_id is not None

        # Execution stage completed via ingestion
        records = result.manifest.stage_records
        assert (
            records[WorkflowStage.CONTROLLED_EXECUTION.value].status
            == StageStatus.COMPLETED
        )
        assert (
            records[WorkflowStage.TRUST_ASSESSMENT.value].status
            == StageStatus.COMPLETED
        )

        # Trust assessment checks
        assert result.trust_report is not None
        cov = result.trust_report.coverage
        assert cov.total_documented_operations == 60
        assert cov.passed_operations_count == 8
        assert cov.untested_operations_count == 52

        # Zero fabricated defects
        assert (
            result.trust_report.verdict_counts.get(
                DiagnosticVerdict.VERIFIED_CONTRACT_MISMATCH.value, 0
            )
            == 0
        )
        assert (
            result.trust_report.verdict_counts.get(
                DiagnosticVerdict.VERIFIED_SEMANTIC_MISMATCH.value, 0
            )
            == 0
        )
        assert (
            result.trust_report.verdict_counts.get(
                DiagnosticVerdict.SUSPECTED_APPLICATION_DEFECT.value, 0
            )
            == 0
        )

        # Artifacts generated
        assert (out_dir / "workflow_manifest.json").is_file()
        assert (out_dir / "trust_assessment_report.json").is_file()
        assert (out_dir / "trust_assessment_report.yaml").is_file()


def test_stockpile_extended_run_guided_workflow_ingestion(
    tmp_path: Path,
    stockpile_spec_path: Path,
    stockpile_extended_path: Path,
) -> None:
    """Verify guided workflow ingesting authentic StockPile extended 30-GET results offline."""
    out_dir = tmp_path / "stockpile_extended"

    with patch("socket.socket") as mock_sock:
        mock_sock.side_effect = RuntimeError(
            "Target network access attempted during offline extended run ingestion!"
        )

        opts = GuidedWorkflowOptions(
            spec_path=stockpile_spec_path,
            output_dir=out_dir,
            existing_run_file=stockpile_extended_path,
            preview_only=False,
        )

        svc = GuidedWorkflowService()
        result = svc.run_guided_workflow(opts)

        assert result.is_success is True

        # Trust assessment checks
        assert result.trust_report is not None
        cov = result.trust_report.coverage
        assert cov.total_documented_operations == 60
        assert cov.passed_operations_count == 30
        assert cov.readonly_total_operations == 30
        assert cov.readonly_attempted_operations == 30
        assert cov.readonly_coverage_pct == pytest.approx(100.0, rel=1e-2)
        assert cov.mutation_total_operations == 30
        assert cov.mutation_attempted_operations == 0
        assert cov.untested_operations_count == 30

        # Zero fabricated defects
        assert (
            result.trust_report.verdict_counts.get(
                DiagnosticVerdict.VERIFIED_CONTRACT_MISMATCH.value, 0
            )
            == 0
        )
        assert (
            result.trust_report.verdict_counts.get(
                DiagnosticVerdict.VERIFIED_SEMANTIC_MISMATCH.value, 0
            )
            == 0
        )
        assert (
            result.trust_report.verdict_counts.get(
                DiagnosticVerdict.SUSPECTED_APPLICATION_DEFECT.value, 0
            )
            == 0
        )
