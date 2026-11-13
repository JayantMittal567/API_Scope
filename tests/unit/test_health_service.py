"""Unit tests for HealthService module health verdict calculation and coverage.

Covers: FR-31, FR-32, FR-33, NFR-02, NFR-06.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from apiscope.analysis.health_models import HealthVerdict
from apiscope.analysis.health_service import HealthService
from apiscope.core.exceptions import ConfigurationError
from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.generators.openapi_importer import EndpointSpec, OpenAPISpec


def _make_case(
    name: str = "test",
    module: str = "orders",
    method: str = "GET",
    url: str = "/orders",
    status: CaseStatus = CaseStatus.PASSED,
    verified: bool = True,
    elapsed_ms: float = 25.0,
    tags: list[str] | None = None,
    assertion_results: list[dict[str, Any]] | None = None,
) -> CaseResult:
    """Helper to construct a deterministic CaseResult."""
    return CaseResult(
        test_name=name,
        module=module,
        tags=tags or [],
        status=status,
        verified=verified,
        method=method,
        url=url,
        status_code=200 if status == CaseStatus.PASSED else 500,
        elapsed_ms=elapsed_ms,
        request_headers={},
        response_headers={},
        assertion_results=assertion_results
        if assertion_results is not None
        else [
            {
                "rule_type": "status",
                "passed": status == CaseStatus.PASSED,
                "expected": 200,
                "actual": 200 if status == CaseStatus.PASSED else 500,
                "message": "Status code check",
            }
        ],
        extracted_variables={},
        extraction_errors=[],
        error_message="Test failed" if status == CaseStatus.FAILED else None,
        skip_reason="Skipped by user" if status == CaseStatus.SKIPPED else None,
    )


def _make_run(cases: list[CaseResult]) -> RunResult:
    """Helper to aggregate CaseResults into a RunResult."""
    now = datetime.now(UTC)
    return RunResult(
        run_id="run-test-uuid",
        environment_name="test-env",
        started_at=now,
        finished_at=now,
        duration_ms=150.0,
        total_tests=len(cases),
        passed_tests=sum(1 for c in cases if c.status == CaseStatus.PASSED),
        failed_tests=sum(1 for c in cases if c.status == CaseStatus.FAILED),
        error_tests=sum(1 for c in cases if c.status == CaseStatus.ERROR),
        skipped_tests=sum(1 for c in cases if c.status == CaseStatus.SKIPPED),
        results=cases,
    )


def _make_spec(endpoints: list[EndpointSpec]) -> OpenAPISpec:
    """Helper to construct an OpenAPISpec."""
    path_methods: dict[str, list[str]] = {}
    for ep in endpoints:
        path_methods.setdefault(ep.path, []).append(ep.method)
    return OpenAPISpec(
        title="Test API",
        version="1.0.0",
        openapi_version="3.0.3",
        endpoints=endpoints,
        path_methods=path_methods,
    )


def test_verdict_healthy_when_all_pass_and_coverage_above_threshold() -> None:
    """Covers: FR-31, FR-32, FR-33, NFR-06.

    Verifies HealthVerdict.HEALTHY when all tests pass and coverage is 100%.
    Ensures evidence/summary include 'verified against' and NEVER 'zero bugs'.
    """
    spec = _make_spec(
        [
            EndpointSpec(path="/orders", method="GET", module="orders"),
            EndpointSpec(path="/orders/{order_id}", method="GET", module="orders"),
        ]
    )
    cases = [
        _make_case(
            name="get_orders",
            module="orders",
            method="GET",
            url="http://localhost:8000/orders",
            tags=["positive"],
        ),
        _make_case(
            name="get_single_order",
            module="orders",
            method="GET",
            url="http://localhost:8000/orders/101",
            tags=["positive"],
        ),
    ]
    service = HealthService()
    report = service.evaluate_module("orders", _make_run(cases), openapi_spec=spec)

    assert report.verdict == HealthVerdict.HEALTHY
    assert report.spec_endpoints_total == 2
    assert report.spec_endpoints_tested == 2
    assert report.endpoint_coverage_pct == 100.0
    assert report.untested_endpoints == []
    assert report.passed_tests == 2
    assert report.failed_tests == 0

    # Strict wording checks (FR-33)
    assert "verified against" in report.summary.lower()
    assert "zero bugs" not in report.summary.lower()
    assert "bug-free" not in report.summary.lower()
    for ev in report.evidence:
        assert "zero bugs" not in ev.lower()
        assert "bug-free" not in ev.lower()


def test_verdict_incomplete_when_endpoint_coverage_below_threshold() -> None:
    """Covers: FR-31, FR-32.

    Verifies HealthVerdict.INCOMPLETE when 1 of 3 endpoints is tested (33.3% < 80.0%).
    Verifies untested_endpoints lists the 2 remaining endpoints.
    """
    spec = _make_spec(
        [
            EndpointSpec(path="/orders", method="GET", module="orders"),
            EndpointSpec(path="/orders", method="POST", module="orders"),
            EndpointSpec(path="/orders/{order_id}", method="DELETE", module="orders"),
        ]
    )
    cases = [
        _make_case(
            name="get_orders",
            module="orders",
            method="GET",
            url="/orders",
            status=CaseStatus.PASSED,
        )
    ]
    service = HealthService()
    report = service.evaluate_module(
        "orders",
        _make_run(cases),
        openapi_spec=spec,
        coverage_threshold_pct=80.0,
    )

    assert report.verdict == HealthVerdict.INCOMPLETE
    assert report.spec_endpoints_total == 3
    assert report.spec_endpoints_tested == 1
    assert report.endpoint_coverage_pct == pytest.approx(33.33, rel=1e-2)
    assert report.untested_endpoints == ["DELETE /orders/{order_id}", "POST /orders"]
    assert "below threshold 80.0%" in report.summary


def test_verdict_incomplete_when_no_tests_or_unverified_test() -> None:
    """Covers: FR-32.

    Verifies HealthVerdict.INCOMPLETE when total_tests == 0 or any test
    has verified == False.
    """
    service = HealthService()

    # Case A: total_tests == 0
    empty_run = _make_run([])
    report_empty = service.evaluate_module("orders", empty_run)
    assert report_empty.verdict == HealthVerdict.INCOMPLETE
    assert "No test cases executed" in report_empty.summary

    # Case B: verified == False (no assertions evaluated)
    cases_unverified = [
        _make_case(
            name="blind_test",
            module="orders",
            verified=False,
            assertion_results=[],
        )
    ]
    report_unverified = service.evaluate_module("orders", _make_run(cases_unverified))
    assert report_unverified.verdict == HealthVerdict.INCOMPLETE
    assert "unverified" in report_unverified.summary.lower()


def test_verdict_unhealthy_when_functional_contract_negative_or_auth_test_fails() -> (
    None
):
    """Covers: FR-31, FR-32.

    Verifies any FAILED, ERROR, or SKIPPED case yields HealthVerdict.UNHEALTHY
    even if coverage is 100%.
    """
    service = HealthService()

    # Subcase A: Failed test case
    cases_failed = [
        _make_case(
            name="test_order_create",
            module="orders",
            status=CaseStatus.FAILED,
        )
    ]
    report_failed = service.evaluate_module("orders", _make_run(cases_failed))
    assert report_failed.verdict == HealthVerdict.UNHEALTHY
    assert len(report_failed.failures) == 1
    assert "test_order_create: FAILED" in report_failed.failures[0]

    # Subcase B: Errored test case
    cases_error = [
        _make_case(
            name="test_order_error",
            module="orders",
            status=CaseStatus.ERROR,
        )
    ]
    report_error = service.evaluate_module("orders", _make_run(cases_error))
    assert report_error.verdict == HealthVerdict.UNHEALTHY

    # Subcase C: Skipped test case
    cases_skipped = [
        _make_case(
            name="test_order_skipped",
            module="orders",
            status=CaseStatus.SKIPPED,
        )
    ]
    report_skipped = service.evaluate_module("orders", _make_run(cases_skipped))
    assert report_skipped.verdict == HealthVerdict.UNHEALTHY


def test_verdict_healthy_with_warnings_on_zombie_or_silent_failure_or_latency_drift():
    """Covers: FR-31, FR-32, FR-33.

    Verifies HealthVerdict.HEALTHY_WITH_WARNINGS when all tests pass but warnings exist.
    Verifies that 'PASS WITH WARNINGS: APIs healthy, internal issue suspected' appears
    specifically when silent_failure_count > 0.
    """
    service = HealthService(latency_tolerance_factor=1.5)
    cases = [
        _make_case(
            name="test_order",
            module="orders",
            status=CaseStatus.PASSED,
            elapsed_ms=80.0,
        )
    ]
    run = _make_run(cases)

    # Condition 1: silent_failure_count > 0
    report_sf = service.evaluate_module(
        module="orders",
        run_result=run,
        silent_failure_count=2,
        silent_failure_evidence=["Endpoint returned 200 with DB error message"],
    )
    assert report_sf.verdict == HealthVerdict.HEALTHY_WITH_WARNINGS
    assert (
        "PASS WITH WARNINGS: APIs healthy, internal issue suspected"
        in report_sf.summary
    )
    assert any("2 silent failure(s)" in w for w in report_sf.warnings)

    # Condition 2: zombie_count > 0
    report_zombie = service.evaluate_module(
        module="orders",
        run_result=run,
        zombie_count=1,
        zombie_evidence=["Endpoint /orders/hidden active in logs but not in spec"],
    )
    assert report_zombie.verdict == HealthVerdict.HEALTHY_WITH_WARNINGS
    assert any("1 zombie endpoint(s)" in w for w in report_zombie.warnings)

    # Condition 3: Latency regression (p95 80ms > baseline 40ms * 1.5 = 60ms)
    report_latency = service.evaluate_module(
        module="orders",
        run_result=run,
        baseline_p95_ms=40.0,
    )
    assert report_latency.verdict == HealthVerdict.HEALTHY_WITH_WARNINGS
    assert report_latency.latency_regression is True
    assert any("Latency regression detected" in w for w in report_latency.warnings)


def test_invalid_coverage_threshold_raises_configuration_error() -> None:
    """Covers: FR-31, NFR-02.

    Verifies that negative or > 100 coverage thresholds raise ConfigurationError.
    """
    service = HealthService()
    run = _make_run([])

    with pytest.raises(ConfigurationError, match="between 0.0 and 100.0"):
        service.evaluate_module("orders", run, coverage_threshold_pct=-5.0)

    with pytest.raises(ConfigurationError, match="between 0.0 and 100.0"):
        service.evaluate_module("orders", run, coverage_threshold_pct=105.0)


def test_module_health_report_to_dict_json_serializable() -> None:
    """Covers: FR-33, NFR-06.

    Verifies that report.to_dict() is 100% JSON-serializable.
    """
    spec = _make_spec([EndpointSpec(path="/orders", method="GET", module="orders")])
    cases = [
        _make_case(
            name="test_order",
            module="orders",
            tags=["auth", "boundary"],
            assertion_results=[
                {
                    "rule_type": "status",
                    "passed": True,
                    "expected": 200,
                    "actual": 200,
                    "message": "Status OK",
                },
                {
                    "rule_type": "json_schema",
                    "passed": True,
                    "expected": {},
                    "actual": {},
                    "message": "Schema valid",
                },
            ],
        )
    ]
    service = HealthService()
    report = service.evaluate_module("orders", _make_run(cases), openapi_spec=spec)

    report_dict = report.to_dict()
    assert isinstance(report_dict, dict)
    assert report_dict["module"] == "orders"
    assert report_dict["verdict"] == "HEALTHY"
    assert report_dict["contract_stats"]["passed"] == 2
    assert report_dict["auth_stats"]["passed"] == 1
    assert report_dict["negative_boundary_stats"]["passed"] == 1

    # Verify JSON serialization
    serialized = json.dumps(report_dict)
    deserialized = json.loads(serialized)
    assert deserialized["module"] == "orders"
    assert deserialized["verdict"] == "HEALTHY"


def test_evaluate_all_modules() -> None:
    """Covers: FR-31, FR-34.

    Verifies evaluating multiple modules across test cases and spec.
    """
    spec = _make_spec(
        [
            EndpointSpec(path="/orders", method="GET", module="orders"),
            EndpointSpec(path="/users", method="GET", module="users"),
        ]
    )
    cases = [
        _make_case(name="get_orders", module="orders", method="GET", url="/orders"),
        _make_case(name="get_users", module="users", method="GET", url="/users"),
    ]
    service = HealthService()
    reports = service.evaluate_all_modules(_make_run(cases), openapi_spec=spec)

    assert len(reports) == 2
    assert [r.module for r in reports] == ["orders", "users"]
    assert all(r.verdict == HealthVerdict.HEALTHY for r in reports)
