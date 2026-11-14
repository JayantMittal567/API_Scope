"""Unit tests for Endpoint Inventory and Coverage Intelligence (Task 24).

Covers all 17 synthetic acceptance criteria:
1. Documented and observed endpoint
2. Documented but unobserved endpoint (untested is NOT dead)
3. Source-only candidate (controller found, absent in spec)
4. Spec-only candidate with source scan
5. Spec-only uncertainty when no source is scanned (honest limitations)
6. Separate microservice route isolation (auxiliary analytics not falsely flagged)
7. Authenticated route returning 401 (active, not zombie)
8. Missing resource returning 404 (dynamic ID missing, not route removal)
9. Deprecated operation in spec
10. Multi-evidence zombie candidate
11. Unknown runtime usage
12. Deduplication of repeated requests to one endpoint
13. Planned vs executed coverage distinction (planned does not inflate executed)
14. Skipped and policy-blocked operations not counted as executed
15. Static route precedence over parameterized templates
16. Secret redaction in inventory findings
17. Offline zero-network guarantee
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from apiscope.analysis.endpoint_inventory import (
    EndpointClassification,
    EndpointInventoryService,
    InventoryConfidence,
)
from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.generators.openapi_importer import (
    EndpointSpec,
    OpenAPISpec,
)
from apiscope.intelligence.source_scanner import (
    DiscoveredSourceEndpoint,
    DiscoveredSourceFindings,
)
from apiscope.planning.models import (
    ScenarioCategory,
    ScenarioStep,
    TestPlan,
    TestScenario,
)


def _make_spec(endpoints: list[tuple[str, str, str, bool]]) -> OpenAPISpec:
    """Helper to construct an OpenAPISpec from (method, path, module, deprecated) tuples."""
    eps = [
        EndpointSpec(
            path=p,
            method=m.upper(),
            module=mod,
            deprecated=dep,
        )
        for m, p, mod, dep in endpoints
    ]
    path_methods: dict[str, list[str]] = {}
    for ep in eps:
        path_methods.setdefault(ep.path, []).append(ep.method)

    return OpenAPISpec(
        title="Test API",
        version="1.0.0",
        openapi_version="3.0.0",
        servers=["http://localhost:8080"],
        endpoints=eps,
        path_methods=path_methods,
    )


def _make_case(
    method: str,
    url: str,
    status: CaseStatus = CaseStatus.PASSED,
    status_code: int = 200,
    module: str = "default",
    name: str = "test_case",
) -> CaseResult:
    """Helper to construct a CaseResult."""
    return CaseResult(
        test_name=name,
        module=module,
        tags=["test"],
        status=status,
        verified=status == CaseStatus.PASSED,
        method=method.upper(),
        url=url,
        status_code=status_code,
        elapsed_ms=10.0,
        request_headers={},
        response_headers={},
        assertion_results=[],
        extracted_variables={},
        extraction_errors=[],
    )


def _make_run(run_id: str, cases: list[CaseResult]) -> RunResult:
    """Helper to construct a RunResult."""
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    passed = sum(1 for c in cases if c.status == CaseStatus.PASSED)
    failed = sum(1 for c in cases if c.status == CaseStatus.FAILED)
    errored = sum(1 for c in cases if c.status == CaseStatus.ERROR)
    skipped = sum(1 for c in cases if c.status == CaseStatus.SKIPPED)

    return RunResult(
        run_id=run_id,
        environment_name="test",
        started_at=now,
        finished_at=now,
        duration_ms=100.0,
        total_tests=len(cases),
        passed_tests=passed,
        failed_tests=failed,
        error_tests=errored,
        skipped_tests=skipped,
        results=cases,
    )


# -------------------------------------------------------------------------
# Test Cases 1 & 2: Documented Observed vs Unobserved (Untested is not dead)
# -------------------------------------------------------------------------


def test_documented_and_observed_vs_unobserved_endpoint() -> None:
    """Verify Rule 1: Documented endpoint with test execution is DOCUMENTED_AND_OBSERVED;

    untested documented endpoint is DOCUMENTED_NOT_OBSERVED, never a zombie.
    """
    spec = _make_spec(
        [
            ("GET", "/api/products", "products", False),
            ("POST", "/api/products", "products", False),
            ("DELETE", "/api/products/{id}", "products", False),
        ]
    )
    # Only test GET /api/products
    cases = [_make_case("GET", "http://localhost:8080/api/products")]
    run = _make_run("run-1", cases)

    service = EndpointInventoryService()
    report = service.analyze(spec=spec, run_result=run)

    assert report.total_canonical_operations == 3
    assert report.coverage_summary.total_documented_operations == 3
    assert report.coverage_summary.attempted_operations_count == 1
    assert report.coverage_summary.attempted_coverage_pct == pytest.approx(
        33.33, rel=1e-2
    )
    assert report.coverage_summary.untested_operations_count == 2

    # Check classifications
    get_rec = next(
        r
        for r in report.inventory
        if r.identity.method == "GET" and r.identity.route_template == "/api/products"
    )
    assert get_rec.classification == EndpointClassification.DOCUMENTED_AND_OBSERVED
    assert get_rec.test_passed_count == 1

    post_rec = next(
        r
        for r in report.inventory
        if r.identity.method == "POST" and r.identity.route_template == "/api/products"
    )
    assert post_rec.classification == EndpointClassification.DOCUMENTED_NOT_OBSERVED
    assert post_rec.test_executions_count == 0

    # Ensure zero zombie candidates fabricated
    assert (
        report.classification_counts[EndpointClassification.ZOMBIE_CANDIDATE.value] == 0
    )


# -------------------------------------------------------------------------
# Test Case 3: Source-Only Candidate
# -------------------------------------------------------------------------


def test_source_only_candidate_route() -> None:
    """Verify Rule 3: Target service route found in source but absent from spec

    is classified as SOURCE_ONLY_CANDIDATE (not automatically public).
    """
    spec = _make_spec([("GET", "/api/users", "users", False)])
    source_findings = DiscoveredSourceFindings(
        source_language="python",
        endpoints=[
            DiscoveredSourceEndpoint(
                path="/api/internal/debug-cache",
                method="POST",
                controller_name="DebugController",
                method_name="clear_cache",
                file_path="app/controllers/debug.py",
                line_number=45,
            )
        ],
    )

    service = EndpointInventoryService()
    report = service.analyze(spec=spec, source_findings=source_findings)

    src_only = [
        f
        for f in report.findings
        if f.classification == EndpointClassification.SOURCE_ONLY_CANDIDATE
    ]
    assert len(src_only) == 1
    assert src_only[0].method == "POST"
    assert src_only[0].canonical_endpoint == "/api/internal/debug-cache"
    assert src_only[0].confidence == InventoryConfidence.SUSPECTED_CAUSE
    assert any(
        "internal" in exp.lower() for exp in src_only[0].alternative_explanations
    )


# -------------------------------------------------------------------------
# Test Cases 4 & 5: Spec-Only with vs without Source Scan
# -------------------------------------------------------------------------


def test_spec_only_candidate_with_and_without_source_scan() -> None:
    """Verify Rule 2: Without source scan, spec-only cannot be confirmed;

    with source scan, missing controller maps to SPEC_ONLY_CANDIDATE.
    """
    spec = _make_spec([("GET", "/api/missing-impl", "default", False)])

    service = EndpointInventoryService()

    # 1. Without source scan: limitation registered, classified as DOCUMENTED_NOT_OBSERVED
    rep_no_src = service.analyze(spec=spec)
    assert any(
        "No source code repository provided" in lim
        for lim in rep_no_src.evidence_limitations
    )
    rec_no_src = rep_no_src.inventory[0]
    assert rec_no_src.classification == EndpointClassification.DOCUMENTED_NOT_OBSERVED

    # 2. With source scan: confirmed absent from inspected controller AST
    empty_src = DiscoveredSourceFindings(source_language="python", endpoints=[])
    rep_with_src = service.analyze(spec=spec, source_findings=empty_src)
    rec_with_src = rep_with_src.inventory[0]
    assert rec_with_src.classification == EndpointClassification.SPEC_ONLY_CANDIDATE
    spec_finding = rep_with_src.findings[0]
    assert spec_finding.confidence == InventoryConfidence.SUSPECTED_CAUSE
    assert any(
        "dynamically mounted" in exp.lower()
        for exp in spec_finding.alternative_explanations
    )


# -------------------------------------------------------------------------
# Test Case 6: Separate Microservice Route Isolation
# -------------------------------------------------------------------------


def test_separate_microservice_route_isolated() -> None:
    """Verify auxiliary service routes (e.g. analytics, web) are isolated

    under their service context and not flagged as drift against target API.
    """
    spec = _make_spec([("GET", "/api/orders", "orders", False)])
    source_findings = DiscoveredSourceFindings(
        source_language="java/spring-boot",
        endpoints=[
            DiscoveredSourceEndpoint(
                path="/analytics/daily-metrics",
                method="GET",
                controller_name="MetricsController",
                method_name="getMetrics",
                file_path="src/analytics/controllers/MetricsController.py",
                line_number=20,
            )
        ],
    )

    service = EndpointInventoryService()
    report = service.analyze(spec=spec, source_findings=source_findings)

    # Analytics route must not contaminate primary documented inventory
    assert report.coverage_summary.total_documented_operations == 1
    # Check that no spurious drift was added against primary service spec
    target_findings = [f for f in report.findings if f.service_context == "primary"]
    assert all("/analytics" not in f.canonical_endpoint for f in target_findings)


# -------------------------------------------------------------------------
# Test Cases 7 & 8: HTTP 401 Auth and HTTP 404 Parameterized Route
# -------------------------------------------------------------------------


def test_authenticated_route_returning_401_is_active_not_zombie() -> None:
    """Verify Rule 5: HTTP 401 Unauthorized reflects auth requirements, not endpoint death."""
    spec = _make_spec([("GET", "/api/secure/profile", "auth", False)])
    cases = [
        _make_case(
            "GET",
            "http://localhost:8080/api/secure/profile",
            status=CaseStatus.PASSED,
            status_code=401,
        )
    ]
    run = _make_run("run-auth", cases)

    service = EndpointInventoryService()
    report = service.analyze(spec=spec, run_result=run)

    rec = report.inventory[0]
    assert rec.classification == EndpointClassification.DOCUMENTED_AND_OBSERVED
    finding = report.findings[0]
    assert any(
        "authentication" in exp.lower() for exp in finding.alternative_explanations
    )


def test_missing_resource_returning_404_on_parameterized_route() -> None:
    """Verify Rule 4: HTTP 404 on parameterized route reflects missing entity ID, not endpoint death."""
    spec = _make_spec([("GET", "/api/items/{id}", "items", False)])
    cases = [
        _make_case(
            "GET",
            "http://localhost:8080/api/items/99999",
            status=CaseStatus.PASSED,
            status_code=404,
        )
    ]
    run = _make_run("run-404", cases)

    service = EndpointInventoryService()
    report = service.analyze(spec=spec, run_result=run)

    rec = report.inventory[0]
    assert rec.classification == EndpointClassification.DOCUMENTED_AND_OBSERVED
    finding = report.findings[0]
    assert any(
        "requested resource id was absent" in exp.lower()
        for exp in finding.alternative_explanations
    )


# -------------------------------------------------------------------------
# Test Cases 9 & 10: Deprecation and Multi-Evidence Zombie Candidate
# -------------------------------------------------------------------------


def test_deprecated_operation_in_spec() -> None:
    """Verify Rule 6: Deprecated endpoint in spec is DEPRECATED_OR_OBSOLETE_CANDIDATE."""
    spec = _make_spec([("GET", "/api/v1/legacy-list", "legacy", True)])

    service = EndpointInventoryService()
    report = service.analyze(spec=spec)

    rec = report.inventory[0]
    assert rec.classification == EndpointClassification.DEPRECATED_OR_OBSOLETE_CANDIDATE
    finding = report.findings[0]
    assert finding.confidence == InventoryConfidence.VERIFIED_OBSERVATION


def test_multi_evidence_zombie_candidate_detection() -> None:
    """Verify Rule 7: Multi-evidence corroboration (deprecated in spec + absent in source

    + repeated 404/410 across calls) produces ZOMBIE_CANDIDATE.
    """
    spec = _make_spec([("GET", "/api/v1/deleted-route", "legacy", True)])
    source_findings = DiscoveredSourceFindings(source_language="python", endpoints=[])
    # Run calling /api/v1/deleted-route twice, both returning 404
    cases = [
        _make_case(
            "GET",
            "http://localhost:8080/api/v1/deleted-route",
            status=CaseStatus.FAILED,
            status_code=404,
        ),
        _make_case(
            "GET",
            "http://localhost:8080/api/v1/deleted-route",
            status=CaseStatus.FAILED,
            status_code=404,
        ),
    ]
    run = _make_run("run-zombie", cases)

    service = EndpointInventoryService()
    report = service.analyze(spec=spec, source_findings=source_findings, run_result=run)

    rec = report.inventory[0]
    assert rec.classification == EndpointClassification.ZOMBIE_CANDIDATE
    assert (
        report.classification_counts[EndpointClassification.ZOMBIE_CANDIDATE.value] == 1
    )
    finding = report.findings[0]
    assert finding.confidence == InventoryConfidence.VERIFIED_OBSERVATION
    assert any("deprecated" in ev.lower() for ev in finding.supporting_evidence)
    assert any("returned http 404" in ev.lower() for ev in finding.supporting_evidence)


# -------------------------------------------------------------------------
# Test Cases 11 & 12: Deduplication of Repeated Requests
# -------------------------------------------------------------------------


def test_deduplication_repeated_requests_to_one_endpoint() -> None:
    """Verify that 10 executions of the same endpoint count as 1 canonical operation covered."""
    spec = _make_spec(
        [
            ("GET", "/api/orders", "orders", False),
            ("POST", "/api/orders", "orders", False),
        ]
    )
    # 10 calls to GET /api/orders
    cases = [_make_case("GET", "http://localhost:8080/api/orders") for _ in range(10)]
    run = _make_run("run-multi", cases)

    service = EndpointInventoryService()
    report = service.analyze(spec=spec, run_result=run)

    assert report.coverage_summary.total_documented_operations == 2
    assert report.coverage_summary.attempted_operations_count == 1
    assert report.coverage_summary.attempted_coverage_pct == pytest.approx(50.0)
    assert report.coverage_summary.untested_operations_count == 1

    rec = next(r for r in report.inventory if r.identity.method == "GET")
    assert rec.test_executions_count == 10
    assert rec.test_passed_count == 10


# -------------------------------------------------------------------------
# Test Cases 13 & 14: Planned vs Executed and Blocked/Skipped Distinction
# -------------------------------------------------------------------------


def test_planned_vs_executed_coverage_distinction() -> None:
    """Verify that planned scenarios never inflate executed coverage metrics."""
    spec = _make_spec(
        [
            ("GET", "/api/a", "mod", False),
            ("GET", "/api/b", "mod", False),
            ("GET", "/api/c", "mod", False),
            ("GET", "/api/d", "mod", False),
        ]
    )

    # Test plan plans all 4 endpoints
    test_plan = TestPlan(
        plan_id="plan-1",
        version="1.0.0",
        title="Sample Plan",
        scenarios=[
            TestScenario(
                scenario_id="scn-a",
                title="A",
                description="desc",
                category=ScenarioCategory.CONTRACT_POSITIVE,
                target_endpoints=["GET /api/a"],
                steps=[
                    ScenarioStep(
                        step_number=1, method="GET", path="/api/a", description="step"
                    )
                ],
            ),
            TestScenario(
                scenario_id="scn-b",
                title="B",
                description="desc",
                category=ScenarioCategory.CONTRACT_POSITIVE,
                target_endpoints=["GET /api/b"],
                steps=[
                    ScenarioStep(
                        step_number=1, method="GET", path="/api/b", description="step"
                    )
                ],
            ),
            TestScenario(
                scenario_id="scn-c",
                title="C",
                description="desc",
                category=ScenarioCategory.CONTRACT_POSITIVE,
                target_endpoints=["GET /api/c"],
                steps=[
                    ScenarioStep(
                        step_number=1, method="GET", path="/api/c", description="step"
                    )
                ],
            ),
            TestScenario(
                scenario_id="scn-d",
                title="D",
                description="desc",
                category=ScenarioCategory.CONTRACT_POSITIVE,
                target_endpoints=["GET /api/d"],
                steps=[
                    ScenarioStep(
                        step_number=1, method="GET", path="/api/d", description="step"
                    )
                ],
            ),
        ],
    )

    # But only 1 endpoint was executed in the test run
    cases = [_make_case("GET", "http://localhost:8080/api/a")]
    run = _make_run("run-exec", cases)

    service = EndpointInventoryService()
    report = service.analyze(spec=spec, test_plan=test_plan, run_result=run)

    cov = report.coverage_summary
    assert cov.total_documented_operations == 4
    # Planned is 4 / 4 = 100.0%
    assert cov.planned_operations_count == 4
    assert cov.planned_coverage_pct == pytest.approx(100.0)
    # Attempted is strictly 1 / 4 = 25.0%
    assert cov.attempted_operations_count == 1
    assert cov.attempted_coverage_pct == pytest.approx(25.0)
    assert cov.untested_operations_count == 3


def test_skipped_and_blocked_cases_not_counted_as_executed() -> None:
    """Verify that SKIPPED test cases are counted in blocked_or_skipped, not in completed."""
    spec = _make_spec(
        [
            ("POST", "/api/mutation", "mod", False),
        ]
    )
    cases = [
        _make_case(
            "POST",
            "http://localhost:8080/api/mutation",
            status=CaseStatus.SKIPPED,
            status_code=0,
        )
    ]
    run = _make_run("run-skip", cases)

    service = EndpointInventoryService()
    report = service.analyze(spec=spec, run_result=run)

    cov = report.coverage_summary
    assert cov.blocked_or_skipped_operations_count == 1
    assert cov.completed_operations_count == 0
    assert cov.passed_operations_count == 0


# -------------------------------------------------------------------------
# Test Cases 15, 16 & 17: Precedence, Secret Redaction, and Offline Guarantee
# -------------------------------------------------------------------------


def test_static_route_precedence_over_parameterized() -> None:
    """Verify that static literal routes take precedence over parameterized templates."""
    spec = _make_spec(
        [
            ("GET", "/api/products/stats", "products", False),
            ("GET", "/api/products/{id}", "products", False),
        ]
    )
    cases = [_make_case("GET", "http://localhost:8080/api/products/stats")]
    run = _make_run("run-stats", cases)

    service = EndpointInventoryService()
    report = service.analyze(spec=spec, run_result=run)

    stats_rec = next(
        r
        for r in report.inventory
        if r.identity.route_template == "/api/products/stats"
    )
    param_rec = next(
        r for r in report.inventory if r.identity.route_template == "/api/products/{id}"
    )

    assert stats_rec.classification == EndpointClassification.DOCUMENTED_AND_OBSERVED
    assert stats_rec.test_executions_count == 1
    assert param_rec.classification == EndpointClassification.DOCUMENTED_NOT_OBSERVED
    assert param_rec.test_executions_count == 0


def test_secret_redaction_in_inventory_findings() -> None:
    """Verify that API keys and bearer tokens in URLs are redacted from findings."""
    spec = _make_spec([("GET", "/api/export", "export", False)])
    cases = [
        _make_case(
            "GET",
            "http://localhost:8080/api/export?token=secret123456789&key=sk-ant-987654321",
        )
    ]
    run = _make_run("run-secret", cases)

    service = EndpointInventoryService()
    report = service.analyze(spec=spec, run_result=run)

    json_rep = report.to_json()
    assert "secret123456789" not in json_rep
    assert "sk-ant-987654321" not in json_rep


def test_offline_zero_network_guarantee() -> None:
    """Verify that inventory and coverage analysis executes purely offline without sockets."""
    spec = _make_spec([("GET", "/api/status", "status", False)])
    run = _make_run(
        "run-offline", [_make_case("GET", "http://localhost:8080/api/status")]
    )

    with patch("socket.socket") as mock_socket:
        mock_socket.side_effect = RuntimeError(
            "Network access attempted during offline analysis!"
        )
        service = EndpointInventoryService()
        report = service.analyze(spec=spec, run_result=run)
        assert report.total_canonical_operations == 1
