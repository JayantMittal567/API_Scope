"""StockPile acceptance verification for Endpoint Inventory and Coverage Intelligence (Task 24).

Verifies authentic StockPile read-only results against stockpile_openapi.yaml:
- Exactly 60 documented operations (30 GET, 30 Mutating).
- Authentic 8-request smoke run: 8 GET attempted, 52 untested, 0 mutations, 0 fabricated zombies.
- Authentic 30-GET extended run: 30 GET attempted (100% GET coverage), 30 mutations untested, 0 fabricated zombies.
- Proper normalization and matching of parameterized vs static routes.
- Offline execution guarantee (zero target HTTP network calls).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from apiscope.analysis.endpoint_inventory import (
    EndpointClassification,
    EndpointInventoryService,
)
from apiscope.analysis.silent_failure_service import load_run_file
from apiscope.definitions.collection import load_collection
from apiscope.generators.openapi_importer import load_openapi_spec


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


@pytest.fixture
def stockpile_plan_path() -> Path:
    p = Path("stockpile_tests/stockpile_test_plan.json")
    assert p.is_file(), "stockpile_test_plan.json must exist"
    return p


@pytest.fixture
def stockpile_suite_path() -> Path:
    p = Path("stockpile_tests/stockpile_compiled_suite.yaml")
    assert p.is_file(), "stockpile_compiled_suite.yaml must exist"
    return p


def test_stockpile_spec_structure_verification(stockpile_spec_path: Path) -> None:
    """Verify that StockPile OpenAPI specification defines exactly 60 operations (30 GET, 30 Mutating)."""
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


def test_stockpile_smoke_run_inventory_and_coverage(
    stockpile_spec_path: Path,
    stockpile_smoke_path: Path,
    stockpile_plan_path: Path,
    stockpile_suite_path: Path,
) -> None:
    """Evaluate authentic StockPile smoke run (8 GET calls) offline:

    - Total documented: 60
    - Attempted operations: 8 (13.3%)
    - Read-only operations: 8 / 30 (26.7%)
    - Mutation operations: 0 / 30 (0.0%)
    - Untested operations: 52 (86.7%)
    - Zombie candidates: EXACTLY 0 (no false-positive zombie claims for untested endpoints!)
    """
    with patch("socket.socket") as mock_sock:
        mock_sock.side_effect = RuntimeError(
            "Network call attempted during offline test!"
        )

        run_res = load_run_file(stockpile_smoke_path)
        assert run_res.total_tests == 8

        plan_data = json.loads(stockpile_plan_path.read_text(encoding="utf-8"))
        suite = load_collection(stockpile_suite_path)

        service = EndpointInventoryService()
        report = service.analyze(
            spec=stockpile_spec_path,
            run_result=run_res,
            test_plan=plan_data,
            test_collection=suite,
        )

        cov = report.coverage_summary
        assert cov.total_documented_operations == 60
        assert cov.attempted_operations_count == 8
        assert cov.attempted_coverage_pct == pytest.approx(13.33, rel=1e-2)
        assert cov.passed_operations_count == 8
        assert cov.failed_operations_count == 0
        assert cov.untested_operations_count == 52

        # Read-only vs Mutation
        assert cov.readonly_total_operations == 30
        assert cov.readonly_attempted_operations == 8
        assert cov.readonly_coverage_pct == pytest.approx(26.67, rel=1e-2)
        assert cov.mutation_total_operations == 30
        assert cov.mutation_attempted_operations == 0
        assert cov.mutation_coverage_pct == 0.0

        # Planned and Generated coverage
        assert cov.planned_operations_count == 60
        assert cov.planned_coverage_pct == pytest.approx(100.0)
        assert cov.generated_operations_count == 33
        assert cov.generated_coverage_pct == pytest.approx(55.0)

        # Zero zombie candidates fabricated
        assert (
            report.classification_counts[EndpointClassification.ZOMBIE_CANDIDATE.value]
            == 0
        )
        assert (
            report.classification_counts[
                EndpointClassification.RUNTIME_MISMATCH_CANDIDATE.value
            ]
            == 0
        )
        assert (
            report.classification_counts[
                EndpointClassification.DOCUMENTED_AND_OBSERVED.value
            ]
            == 8
        )
        assert (
            report.classification_counts[
                EndpointClassification.DOCUMENTED_NOT_OBSERVED.value
            ]
            == 52
        )


def test_stockpile_extended_run_inventory_and_coverage(
    stockpile_spec_path: Path,
    stockpile_extended_path: Path,
) -> None:
    """Evaluate authentic StockPile extended run (30 GET calls) offline:

    - Total documented: 60
    - Attempted operations: 30 (50.0%)
    - Read-only operations: 30 / 30 (100.0%)
    - Mutation operations: 0 / 30 (0.0%)
    - Untested operations: 30 (50.0%)
    - Zombie candidates: EXACTLY 0 (100% GET coverage does not prove mutation health)
    """
    with patch("socket.socket") as mock_sock:
        mock_sock.side_effect = RuntimeError(
            "Network call attempted during offline test!"
        )

        run_res = load_run_file(stockpile_extended_path)
        assert run_res.total_tests == 30

        service = EndpointInventoryService()
        report = service.analyze(
            spec=stockpile_spec_path,
            run_result=run_res,
        )

        cov = report.coverage_summary
        assert cov.total_documented_operations == 60
        assert cov.attempted_operations_count == 30
        assert cov.attempted_coverage_pct == pytest.approx(50.0)
        assert cov.passed_operations_count == 30
        assert cov.untested_operations_count == 30

        # Read-only vs Mutation
        assert cov.readonly_total_operations == 30
        assert cov.readonly_attempted_operations == 30
        assert cov.readonly_coverage_pct == pytest.approx(100.0)
        assert cov.mutation_total_operations == 30
        assert cov.mutation_attempted_operations == 0
        assert cov.mutation_coverage_pct == 0.0

        # Zero zombie candidates fabricated
        assert (
            report.classification_counts[EndpointClassification.ZOMBIE_CANDIDATE.value]
            == 0
        )
        assert (
            report.classification_counts[
                EndpointClassification.DOCUMENTED_AND_OBSERVED.value
            ]
            == 30
        )
        assert (
            report.classification_counts[
                EndpointClassification.DOCUMENTED_NOT_OBSERVED.value
            ]
            == 30
        )


def test_stockpile_route_template_matching_and_precedence(
    stockpile_spec_path: Path,
    stockpile_extended_path: Path,
) -> None:
    """Verify that concrete parameterized routes are properly matched to spec templates

    and do not collide with static routes (e.g. /api/products vs /api/products/{id}).
    """
    run_res = load_run_file(stockpile_extended_path)
    service = EndpointInventoryService()
    report = service.analyze(spec=stockpile_spec_path, run_result=run_res)

    inv_map = {
        f"{r.identity.method} {r.identity.route_template}": r for r in report.inventory
    }

    # Static /api/products and parameterized /api/products/{id} must both be observed
    assert "GET /api/products" in inv_map
    assert "GET /api/products/{id}" in inv_map
    assert (
        inv_map["GET /api/products"].classification
        == EndpointClassification.DOCUMENTED_AND_OBSERVED
    )
    assert (
        inv_map["GET /api/products/{id}"].classification
        == EndpointClassification.DOCUMENTED_AND_OBSERVED
    )

    # Static /api/sales/summary vs parameterized /api/sales/{id}
    assert "GET /api/sales/summary" in inv_map
    assert "GET /api/sales/{id}" in inv_map
    assert (
        inv_map["GET /api/sales/summary"].classification
        == EndpointClassification.DOCUMENTED_AND_OBSERVED
    )
    assert (
        inv_map["GET /api/sales/{id}"].classification
        == EndpointClassification.DOCUMENTED_AND_OBSERVED
    )

    # Static /api/surplus-listings/summary vs parameterized /api/surplus-listings/{id}
    assert "GET /api/surplus-listings/summary" in inv_map
    assert "GET /api/surplus-listings/{id}" in inv_map
    assert (
        inv_map["GET /api/surplus-listings/summary"].classification
        == EndpointClassification.DOCUMENTED_AND_OBSERVED
    )
    assert (
        inv_map["GET /api/surplus-listings/{id}"].classification
        == EndpointClassification.DOCUMENTED_AND_OBSERVED
    )
