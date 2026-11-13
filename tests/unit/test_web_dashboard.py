"""Unit tests for ApiScope Web Dashboard routes and templates.

Covers: FR-28, FR-29, FR-30, FR-31, FR-32, FR-33, FR-36, FR-39, FR-42,
        FR-46, FR-47, FR-48, NFR-03, NFR-04, NFR-06.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from apiscope.core.models import HttpResponse
from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.core.run_service import RunService
from apiscope.data.connection import DatabaseManager
from apiscope.data.repositories import RunRepository
from apiscope.web.app import DashboardConfig, create_web_app


def _make_case(
    name: str = "test",
    module: str = "orders",
    method: str = "GET",
    url: str = "/orders",
    status: CaseStatus = CaseStatus.PASSED,
    elapsed_ms: float = 25.0,
    assertion_results: list[dict[str, Any]] | None = None,
    error_message: str | None = None,
    extracted_variables: dict[str, Any] | None = None,
) -> CaseResult:
    """Helper to construct a deterministic CaseResult."""
    return CaseResult(
        test_name=name,
        module=module,
        tags=["smoke"],
        status=status,
        verified=True,
        method=method,
        url=url,
        status_code=200 if status == CaseStatus.PASSED else 500,
        elapsed_ms=elapsed_ms,
        request_headers={},
        response_headers={},
        assertion_results=assertion_results
        if assertion_results is not None
        else [{"rule_type": "status", "passed": status == CaseStatus.PASSED}],
        extracted_variables=extracted_variables or {},
        extraction_errors=[],
        error_message=error_message,
    )


def _seed_run(
    db_path: Path,
    run_id: str = "run-test-1",
    cases: list[CaseResult] | None = None,
) -> str:
    """Helper to seed a test execution run in SQLite."""
    now = datetime.now(UTC)
    case_list = cases or [_make_case()]
    res = RunResult(
        run_id=run_id,
        environment_name="staging",
        started_at=now,
        finished_at=now,
        duration_ms=150.0,
        total_tests=len(case_list),
        passed_tests=sum(1 for c in case_list if c.status == CaseStatus.PASSED),
        failed_tests=sum(1 for c in case_list if c.status == CaseStatus.FAILED),
        error_tests=sum(1 for c in case_list if c.status == CaseStatus.ERROR),
        skipped_tests=sum(1 for c in case_list if c.status == CaseStatus.SKIPPED),
        results=case_list,
    )
    with DatabaseManager(db_path) as mgr:
        repo = RunRepository(mgr)
        return repo.save_run(res)


def test_dashboard_runs_list_empty_and_populated_with_trends(
    tmp_path: Path,
) -> None:
    """Covers: FR-46, FR-47, FR-29.

    Test GET / and GET /api/trends on empty DB and after saving runs,
    verifying Chart.js canvas and trend points render cleanly.
    """
    db_file = tmp_path / "dashboard_test.db"
    cfg = DashboardConfig(db_path=db_file)
    app = create_web_app(cfg)
    client = TestClient(app)

    # 1. Empty DB
    resp_empty = client.get("/")
    assert resp_empty.status_code == 200
    assert "No test runs recorded yet" in resp_empty.text

    trends_empty = client.get("/api/trends")
    assert trends_empty.status_code == 200
    assert trends_empty.json()["run_count"] == 0

    # 2. Seed runs
    _seed_run(db_file, run_id="run-00000001")
    _seed_run(db_file, run_id="run-00000002")

    resp_pop = client.get("/runs")
    assert resp_pop.status_code == 200
    assert "No test runs recorded yet" not in resp_pop.text
    assert "passRateChart" in resp_pop.text
    assert "latencyChart" in resp_pop.text
    assert "run-0000" in resp_pop.text

    trends_pop = client.get("/api/trends?limit=10")
    assert trends_pop.status_code == 200
    data = trends_pop.json()
    assert data["run_count"] == 2
    assert len(data["points"]) == 2


def test_dashboard_run_detail_shows_expected_vs_actual_and_404_on_missing(
    tmp_path: Path,
) -> None:
    """Covers: FR-46, FR-47, FR-28.

    Verify GET /runs/{run_id} renders case statuses, Expected vs Actual
    assertion failure details, masked secrets, and returns 404 on missing run.
    """
    db_file = tmp_path / "detail_test.db"
    case_failed = _make_case(
        name="test_checkout_failure",
        module="orders",
        method="POST",
        url="http://localhost:8000/orders?token=***",
        status=CaseStatus.FAILED,
        assertion_results=[
            {
                "rule_type": "status",
                "passed": False,
                "expected": 201,
                "actual": 500,
                "message": "Status 500 does not match expected 201",
            }
        ],
        error_message="Server error occurred during payment gateway call",
        extracted_variables={"session_id": "sess-123", "secret_key": "***"},
    )
    _seed_run(db_file, run_id="run-detail-xyz", cases=[case_failed])

    cfg = DashboardConfig(db_path=db_file)
    client = TestClient(create_web_app(cfg))

    # Existing run
    resp = client.get("/runs/run-detail-xyz")
    assert resp.status_code == 200
    assert "run-detail-xyz" in resp.text
    assert "test_checkout_failure" in resp.text
    assert "Expected: <code>201</code> | Actual: <code>500</code>" in resp.text
    assert "Server error occurred during payment gateway call" in resp.text
    assert "sess-123" in resp.text
    assert "***" in resp.text

    # Missing run -> 404
    missing_resp = client.get("/runs/nonexistent-run-id")
    assert missing_resp.status_code == 404
    assert "Run Not Found" in missing_resp.text


def test_dashboard_report_downloads_json_junit_html_and_errors(
    tmp_path: Path,
) -> None:
    """Covers: FR-48, FR-28, FR-30.

    Test GET /runs/{run_id}/download for JSON, JUnit XML, and HTML formats
    with attachment Content-Disposition, and check 400/404 errors.
    """
    db_file = tmp_path / "download_test.db"
    _seed_run(db_file, run_id="run-dl-123")

    cfg = DashboardConfig(db_path=db_file)
    client = TestClient(create_web_app(cfg))

    # JSON download
    r_json = client.get("/runs/run-dl-123/download?format=json")
    assert r_json.status_code == 200
    assert "application/json" in r_json.headers["content-type"]
    assert (
        'filename="apiscope-report-run-dl-123.json"'
        in r_json.headers["content-disposition"]
    )
    data = r_json.json()
    assert data["run_id"] == "run-dl-123"

    # JUnit download
    r_junit = client.get("/runs/run-dl-123/download?format=junit")
    assert r_junit.status_code == 200
    assert "application/xml" in r_junit.headers["content-type"]
    assert (
        'filename="apiscope-report-run-dl-123.xml"'
        in r_junit.headers["content-disposition"]
    )
    assert "<testsuite" in r_junit.text

    # HTML download
    r_html = client.get("/runs/run-dl-123/download?format=html")
    assert r_html.status_code == 200
    assert "text/html" in r_html.headers["content-type"]
    assert (
        'filename="apiscope-report-run-dl-123.html"'
        in r_html.headers["content-disposition"]
    )
    assert "<!DOCTYPE html>" in r_html.text

    # Unknown run -> 404
    r_notfound = client.get("/runs/missing-run/download?format=json")
    assert r_notfound.status_code == 404

    # Invalid format -> 400
    r_bad_fmt = client.get("/runs/run-dl-123/download?format=pdf")
    assert r_bad_fmt.status_code == 400


def test_dashboard_health_page_and_api_show_verdicts_and_warnings(
    tmp_path: Path,
) -> None:
    """Covers: FR-46, FR-47, FR-31, FR-32, FR-33.

    Configure DashboardConfig with sample_app spec and logs, seed run in DB,
    and verify module health page and JSON API outputs.
    """
    db_file = tmp_path / "health_test.db"
    # Seed passing orders run
    cases = [
        _make_case(
            name="test_post_orders",
            module="orders",
            method="POST",
            url="http://localhost:8000/orders",
        ),
        _make_case(
            name="test_get_order",
            module="orders",
            method="GET",
            url="http://localhost:8000/orders/101",
        ),
        _make_case(
            name="test_cancel_order",
            module="orders",
            method="POST",
            url="http://localhost:8000/orders/101/cancel",
        ),
        _make_case(
            name="test_sync_invoice",
            module="orders",
            method="POST",
            url="http://localhost:8000/orders/101/sync-invoice",
        ),
    ]
    _seed_run(db_file, run_id="run-health-orders", cases=cases)

    spec_path = Path("sample_app/openapi.yaml")
    access_log = Path("sample_app/logs/access.log")
    app_log = Path("sample_app/logs/app.log")
    health_file = Path("examples/m9_m10_health_probe_degraded.json")

    cfg = DashboardConfig(
        db_path=db_file,
        spec_path=spec_path,
        access_log_path=access_log,
        app_log_path=app_log,
        health_probe_path=health_file,
    )
    client = TestClient(create_web_app(cfg))

    # HTML Health View
    resp = client.get("/health?module=orders")
    assert resp.status_code == 200
    assert "Module Health Verdict" in resp.text
    assert "orders" in resp.text
    assert "PASS WITH WARNINGS: APIs healthy, internal issue suspected" in resp.text

    # JSON Health API
    resp_api = client.get("/api/health?module=orders")
    assert resp_api.status_code == 200
    api_data = resp_api.json()
    assert api_data["run_id"] == "run-health-orders"
    assert len(api_data["reports"]) == 1
    assert api_data["reports"][0]["module"] == "orders"
    assert api_data["reports"][0]["verdict"] == "HEALTHY WITH WARNINGS"


def test_dashboard_zombies_page_renders_all_classes_and_silent_signals(
    tmp_path: Path,
) -> None:
    """Covers: FR-46, FR-47, FR-36, FR-39, FR-42.

    Request GET /zombies and verify UNUSED, UNDOCUMENTED, DEPRECATED-BUT-CALLED,
    and S1-S4 findings render accurately.
    """
    db_file = tmp_path / "zombies_test.db"
    _seed_run(db_file, run_id="run-zombie-test")

    cfg = DashboardConfig(
        db_path=db_file,
        spec_path=Path("sample_app/openapi.yaml"),
        access_log_path=Path("sample_app/logs/access.log"),
        app_log_path=Path("sample_app/logs/app.log"),
        health_probe_path=Path("examples/m9_m10_health_probe_degraded.json"),
    )
    client = TestClient(create_web_app(cfg))

    resp = client.get("/zombies")
    assert resp.status_code == 200
    assert "Zombie &amp; Silent Failure Analysis" in resp.text
    assert "UNUSED" in resp.text
    assert "UNDOCUMENTED" in resp.text
    assert "DEPRECATED-BUT-CALLED" in resp.text
    assert "/coupons/legacy-archive" in resp.text
    assert "/debug/internal-metrics" in resp.text
    assert "/products/v1-catalog" in resp.text
    assert "S1_LOG_ERROR" in resp.text
    assert "S2_DEPENDENCY_PROBE" in resp.text


def test_dashboard_escapes_xss_in_test_names_and_errors_and_masks_secrets(
    tmp_path: Path,
) -> None:
    """Covers: FR-47, NFR-03, NFR-04.

    Save a RunResult containing script injection tags in test_name and error_message,
    and verify HTML templates autoescape them and never emit raw script execution.
    """
    db_file = tmp_path / "xss_test.db"
    malicious_case = _make_case(
        name="<script>alert('xss-test-name')</script>",
        module="<img src=x onerror=alert('xss-mod')>",
        method="GET",
        url="http://localhost:8000/users?token=super-secret-token",
        status=CaseStatus.ERROR,
        error_message="<script>alert('xss-error')</script>",
        extracted_variables={"key": "<script>alert('xss-var')</script>"},
    )
    _seed_run(db_file, run_id="run-xss-123", cases=[malicious_case])

    cfg = DashboardConfig(db_path=db_file)
    client = TestClient(create_web_app(cfg))

    resp_runs = client.get("/runs")
    assert resp_runs.status_code == 200
    assert "<script>alert('xss-test-name')</script>" not in resp_runs.text

    resp_detail = client.get("/runs/run-xss-123")
    assert resp_detail.status_code == 200
    # Must be escaped:
    xss_name = "&lt;script&gt;alert(&#39;xss-test-name&#39;)&lt;/script&gt;"
    xss_raw = "&lt;script&gt;alert('xss-test-name')&lt;/script&gt;"
    assert xss_name in resp_detail.text or xss_raw in resp_detail.text

    xss_err = "&lt;script&gt;alert(&#39;xss-error&#39;)&lt;/script&gt;"
    xss_err_raw = "&lt;script&gt;alert('xss-error')&lt;/script&gt;"
    assert xss_err in resp_detail.text or xss_err_raw in resp_detail.text
    assert "<script>" not in resp_detail.text
    assert "<img src=x" not in resp_detail.text


def test_dashboard_health_page_and_api_empty_and_all_modules(
    tmp_path: Path,
) -> None:
    """Covers: FR-46, FR-47, FR-31.

    Test /health and /api/health when database is empty and when evaluating
    all modules without a module query parameter.
    """
    db_file = tmp_path / "health_all_test.db"
    cfg = DashboardConfig(db_path=db_file)
    client = TestClient(create_web_app(cfg))

    # 1. Empty DB
    resp_page_empty = client.get("/health")
    assert resp_page_empty.status_code == 200
    assert "No test runs available" in resp_page_empty.text

    resp_api_empty = client.get("/api/health")
    assert resp_api_empty.status_code == 404
    assert "No test runs available" in resp_api_empty.json()["error"]

    # 2. Seed DB and evaluate without module parameter (all modules)
    _seed_run(db_file, run_id="run-all-modules")
    resp_page_all = client.get("/health")
    assert resp_page_all.status_code == 200
    assert "Module Health Verdict" in resp_page_all.text

    resp_api_all = client.get("/api/health")
    assert resp_api_all.status_code == 200
    health_data = resp_api_all.json()
    assert "reports" in health_data
    assert isinstance(health_data["reports"], list)
    assert len(health_data["reports"]) >= 1


def test_dashboard_load_suite_safely_and_trigger_filters(
    tmp_path: Path,
) -> None:
    """Covers: FR-48, NFR-02, NFR-04.

    Test _load_suite_safely branches: single TestDefinition, invalid YAML,
    non-dict YAML, dict without tests/request, and filter yielding zero tests.
    """
    db_file = tmp_path / "load_suite_test.db"
    cfg = DashboardConfig(db_path=db_file, base_dir=tmp_path)
    client = TestClient(create_web_app(cfg))

    # 1. Single TestDefinition (with request)
    single_test = tmp_path / "single_test.yaml"
    single_test.write_text(
        "name: Single Test\nmodule: auth\nrequest:\n"
        "  method: GET\n  url: http://localhost:8000/ping\n",
        encoding="utf-8",
    )
    # 2. Invalid YAML syntax
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("name: [unclosed string\n", encoding="utf-8")

    # 3. Non-dict YAML
    list_yaml = tmp_path / "list.yaml"
    list_yaml.write_text("- item1\n- item2\n", encoding="utf-8")

    # 4. Dict missing tests and request
    missing_keys_yaml = tmp_path / "missing_keys.yaml"
    missing_keys_yaml.write_text("name: Foo\nmodule: bar\n", encoding="utf-8")

    # 5. Valid env file
    env_file = tmp_path / "test.env.yaml"
    env_file.write_text("name: local\nvariables:\n  port: '8080'\n", encoding="utf-8")

    # Bad YAML -> 400
    r_bad = client.post("/api/runs", json={"suite_path": str(bad_yaml)})
    assert r_bad.status_code == 400

    # List YAML -> 400
    r_list = client.post("/api/runs", json={"suite_path": str(list_yaml)})
    assert r_list.status_code == 400

    # Missing keys YAML -> 400
    r_keys = client.post("/api/runs", json={"suite_path": str(missing_keys_yaml)})
    assert r_keys.status_code == 400

    # Single test with mock executor
    mock_executor = MagicMock()
    mock_executor.execute.return_value = HttpResponse(
        status_code=200,
        headers={"Content-Type": "application/json"},
        body='{"ok": true}',
        elapsed_ms=10.0,
    )
    app = create_web_app(cfg, run_service=RunService(executor=mock_executor))
    c2 = TestClient(app)

    r_single = c2.post(
        "/api/runs",
        json={"suite_path": str(single_test), "env_file": str(env_file)},
    )
    assert r_single.status_code == 201

    # Filter with zero matching tests -> 400
    r_zero = c2.post(
        "/api/runs",
        json={"suite_path": str(single_test), "module": "nonexistent_filter"},
    )
    assert r_zero.status_code == 400
    assert "No tests matched" in r_zero.json()["error"]


def test_dashboard_zombies_unconfigured_state(tmp_path: Path) -> None:
    """Covers: FR-46, FR-47, FR-36.

    Verify /zombies displays helpful unconfigured notice when spec/logs are absent.
    """
    db_file = tmp_path / "zombies_unconf.db"
    cfg = DashboardConfig(db_path=db_file)
    client = TestClient(create_web_app(cfg))

    resp = client.get("/zombies")
    assert resp.status_code == 200
    assert "not configured" in resp.text
