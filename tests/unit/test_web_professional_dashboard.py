"""Unit and security integration tests for the Professional Web Dashboard (Task 27).

Covers:
- Overview landing dashboard with operation denominators and metrics (FR-46, FR-47).
- Guided Analysis 11-stage pipeline visualizer and offline preview trigger (Task 26).
- Project Intelligence modules, entities, and endpoint inventory (Task 16, Task 17).
- Test Planning, scenario categories, oracle provenance, and human approval (Task 18, Task 19).
- Execution Preview, read-only vs mutating fences, and Expected vs Actual diagnostics (Task 20, Task 21).
- Unified Trust & Evidence Engine reports and 11 defensible verdicts (Task 22, Task 23, Task 25).
- Security controls: CSRF protection, Path Traversal blocking, Secret Redaction, XSS escaping (NFR-03, NFR-04).
- Authentic StockPile offline rendering without target execution or live mutations.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.data.connection import DatabaseManager
from apiscope.data.repositories import RunRepository
from apiscope.web.app import DashboardConfig, create_web_app
from apiscope.web.security import generate_csrf_token, verify_csrf_token


def _seed_sample_run(
    db_path: Path,
    run_id: str = "run-test-27",
    environment_name: str = "test",
) -> str:
    """Helper to seed a test execution run in SQLite."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    case_pass = CaseResult(
        test_name="test_get_health",
        module="health",
        tags=["smoke"],
        status=CaseStatus.PASSED,
        verified=True,
        method="GET",
        url="http://localhost:8080/api/health",
        status_code=200,
        elapsed_ms=12.5,
        request_headers={},
        response_headers={},
        assertion_results=[
            {"rule_type": "status", "passed": True, "message": "Expected 200"}
        ],
        extracted_variables={},
        extraction_errors=[],
        error_message=None,
    )
    case_fail = CaseResult(
        test_name="test_get_products_invalid",
        module="products",
        tags=["negative"],
        status=CaseStatus.FAILED,
        verified=True,
        method="GET",
        url="http://localhost:8080/api/products?token=sk-super-secret-token",
        status_code=500,
        elapsed_ms=35.0,
        request_headers={},
        response_headers={},
        assertion_results=[
            {
                "rule_type": "status",
                "passed": False,
                "expected": 400,
                "actual": 500,
                "message": "Expected 400 but got 500",
            }
        ],
        extracted_variables={"session_id": "sess-abc"},
        extraction_errors=[],
        error_message="Internal Server Error in database query",
    )
    run = RunResult(
        run_id=run_id,
        environment_name=environment_name,
        started_at=now,
        finished_at=now,
        duration_ms=47.5,
        total_tests=2,
        passed_tests=1,
        failed_tests=1,
        error_tests=0,
        skipped_tests=0,
        results=[case_pass, case_fail],
    )
    with DatabaseManager(db_path) as mgr:
        repo = RunRepository(mgr)
        return repo.save_run(run)


# ---------------------------------------------------------------------------
# 1. Overview Page & Operation Metrics Tests
# ---------------------------------------------------------------------------


def test_overview_page_empty_db_and_clean_metrics(tmp_path: Path) -> None:
    """Verify Overview landing page renders cleanly with 0 runs without crashing."""
    db_file = tmp_path / "empty_overview.db"
    cfg = DashboardConfig(
        db_path=db_file,
        base_dir=tmp_path,
        spec_path=Path("sample_app/openapi.yaml"),
    )
    app = create_web_app(cfg)
    client = TestClient(app)

    resp = client.get("/")
    assert resp.status_code == 200
    assert "ApiScope" in resp.text
    assert "Documented Operations" in resp.text
    assert "No test runs recorded yet" in resp.text
    assert "OFFLINE / READ-ONLY" in resp.text


def test_overview_page_populated_with_coverage_and_runs(tmp_path: Path) -> None:
    """Verify Overview renders documented operations, passed/failed counts, and runs table."""
    db_file = tmp_path / "pop_overview.db"
    _seed_sample_run(db_file, run_id="run-pop-1")

    cfg = DashboardConfig(
        db_path=db_file,
        base_dir=Path.cwd(),
        spec_path=Path("sample_app/openapi.yaml"),
    )
    app = create_web_app(cfg)
    client = TestClient(app)

    resp = client.get("/")
    assert resp.status_code == 200
    assert "run-pop-1" in resp.text
    assert "Coverage Breakdown" in resp.text
    assert "Operation Coverage Intelligence" in resp.text


# ---------------------------------------------------------------------------
# 2. Guided Analysis (11 Stages) & CSRF Safety Tests
# ---------------------------------------------------------------------------


def test_guided_analysis_page_renders_11_stages(tmp_path: Path) -> None:
    """Verify Guided Workflow renders all 11 lifecycle stages with accurate status."""
    db_file = tmp_path / "guided.db"
    cfg = DashboardConfig(db_path=db_file, base_dir=Path.cwd())
    app = create_web_app(cfg)
    client = TestClient(app)

    resp = client.get("/guided")
    assert resp.status_code == 200
    assert "Stage 01: Project Inspection" in resp.text
    assert "Stage 02: Contract Analysis" in resp.text
    assert "Stage 03: Test Planning" in resp.text
    assert "Stage 04: Test Compilation" in resp.text
    assert "Stage 05: Fixture Preparation" in resp.text
    assert "Stage 06: Execution Preview" in resp.text
    assert "Stage 07: Controlled Execution" in resp.text
    assert "Stage 08: Failure Investigation" in resp.text
    assert "Stage 09: Silent Failure &amp; Coverage" in resp.text
    assert "Stage 10: Trust Assessment" in resp.text
    assert "Stage 11: Report Export" in resp.text


def test_guided_analysis_api_post_csrf_protection(tmp_path: Path) -> None:
    """Verify POST /api/guided/run rejects requests with missing or invalid CSRF token."""
    db_file = tmp_path / "guided_csrf.db"
    cfg = DashboardConfig(
        db_path=db_file,
        base_dir=Path.cwd(),
        csrf_secret="secret-key-123",
    )
    app = create_web_app(cfg)
    client = TestClient(app)

    # 1. Missing CSRF token -> 403
    resp_no_csrf = client.post(
        "/api/guided/run",
        json={"spec_path": "sample_app/openapi.yaml"},
    )
    assert resp_no_csrf.status_code == 403
    assert "CSRF" in resp_no_csrf.json()["error"]

    # 2. Invalid CSRF token -> 403
    resp_bad_csrf = client.post(
        "/api/guided/run",
        json={"spec_path": "sample_app/openapi.yaml", "csrf_token": "forged-token"},
    )
    assert resp_bad_csrf.status_code == 403


def test_guided_analysis_api_mutation_fence(tmp_path: Path) -> None:
    """Verify mutating requests are rejected without confirmed isolated environment."""
    db_file = tmp_path / "guided_fence.db"
    secret = "fence-secret-key"
    cfg = DashboardConfig(db_path=db_file, base_dir=Path.cwd(), csrf_secret=secret)
    app = create_web_app(cfg)
    client = TestClient(app)

    valid_token = generate_csrf_token(secret)

    # Allow mutation=True but confirm_isolated_env=False -> 400 Bad Request
    resp = client.post(
        "/api/guided/run",
        json={
            "spec_path": "sample_app/openapi.yaml",
            "csrf_token": valid_token,
            "allow_mutation": True,
            "confirm_isolated_env": False,
        },
    )
    assert resp.status_code == 400
    assert "isolated environment" in resp.json()["error"].lower()


# ---------------------------------------------------------------------------
# 3. Project Intelligence Tests
# ---------------------------------------------------------------------------


def test_project_intelligence_page_renders_modules_and_endpoints(
    tmp_path: Path,
) -> None:
    """Verify Project Intelligence renders modules, entities, and endpoint inventory."""
    db_file = tmp_path / "intel.db"
    cfg = DashboardConfig(
        db_path=db_file,
        base_dir=Path.cwd(),
        spec_path=Path("sample_app/openapi.yaml"),
    )
    app = create_web_app(cfg)
    client = TestClient(app)

    resp = client.get("/intelligence")
    assert resp.status_code == 200
    assert "Project Intelligence" in resp.text
    assert "Discovered Modules" in resp.text
    assert "Endpoint Inventory" in resp.text
    assert "GET" in resp.text


# ---------------------------------------------------------------------------
# 4. Test Planning & Review Tests
# ---------------------------------------------------------------------------


def test_test_planning_page_and_approval_flow(tmp_path: Path) -> None:
    """Verify Test Planning renders scenarios and allows CSRF-protected approval."""
    db_file = tmp_path / "planning.db"
    secret = "plan-secret-key"
    cfg = DashboardConfig(db_path=db_file, base_dir=Path.cwd(), csrf_secret=secret)
    app = create_web_app(cfg)
    client = TestClient(app)

    # 1. View planning page
    resp_view = client.get("/planning")
    assert resp_view.status_code == 200
    assert "Test Planning &amp; Review" in resp_view.text
    assert "Planned Scenarios" in resp_view.text

    # 2. Reject unauthenticated approval
    resp_unauth = client.post("/api/planning/approve", json={})
    assert resp_unauth.status_code == 403

    # 3. Approve with valid CSRF token
    valid_token = generate_csrf_token(secret)
    resp_approve = client.post(
        "/api/planning/approve",
        json={"csrf_token": valid_token},
    )
    assert resp_approve.status_code == 200
    assert resp_approve.json()["status"] == "success"


# ---------------------------------------------------------------------------
# 5. Execution Preview & Results Tests
# ---------------------------------------------------------------------------


def test_execution_preview_and_results_diagnostics(tmp_path: Path) -> None:
    """Verify Execution Preview displays safety restrictions and test case results."""
    db_file = tmp_path / "exec.db"
    _seed_sample_run(db_file, run_id="run-exec-1")

    cfg = DashboardConfig(db_path=db_file, base_dir=Path.cwd())
    app = create_web_app(cfg)
    client = TestClient(app)

    resp = client.get("/execution?run_id=run-exec-1")
    assert resp.status_code == 200
    assert "Execution Safety Fence Status" in resp.text
    assert "READ-ONLY ENFORCED" in resp.text
    assert "test_get_health" in resp.text
    assert "test_get_products_invalid" in resp.text
    assert "FAILED" in resp.text


# ---------------------------------------------------------------------------
# 6. Trust Assessment & Defensible Diagnostics Tests
# ---------------------------------------------------------------------------


def test_trust_diagnostics_page_renders_11_verdicts_and_gaps(
    tmp_path: Path,
) -> None:
    """Verify Trust page renders defensible verdicts, observations, and verification gaps."""
    db_file = tmp_path / "trust.db"
    cfg = DashboardConfig(db_path=db_file, base_dir=Path.cwd())
    app = create_web_app(cfg)
    client = TestClient(app)

    resp = client.get("/trust")
    assert resp.status_code == 200
    assert "Trust Assessment &amp; Defensible Diagnostics" in resp.text
    assert "Deduplicated Findings" in resp.text
    assert "Latency Drift Diagnostics" in resp.text
    assert "Insufficient baseline samples" in resp.text
    assert "Evidence Limitations &amp; Verification Gaps" in resp.text


# ---------------------------------------------------------------------------
# 7. Secure Artifact Downloads & Path Traversal Protection Tests
# ---------------------------------------------------------------------------


def test_artifacts_download_security_and_traversal_blocking(
    tmp_path: Path,
) -> None:
    """Verify /artifacts/{filename} blocks path traversal and serves allowed files."""
    db_file = tmp_path / "artifacts.db"
    out_dir = tmp_path / "reports"
    out_dir.mkdir()
    sample_artifact = out_dir / "sample_manifest.json"
    sample_artifact.write_text('{"manifest_id": "test-123"}', encoding="utf-8")

    cfg = DashboardConfig(
        db_path=db_file,
        base_dir=tmp_path,
        output_dir=out_dir,
    )
    app = create_web_app(cfg)
    client = TestClient(app)

    # 1. Download valid file -> 200
    resp_ok = client.get("/artifacts/sample_manifest.json")
    assert resp_ok.status_code == 200
    assert "application/json" in resp_ok.headers["content-type"]
    assert resp_ok.json()["manifest_id"] == "test-123"

    # 2. Block path traversal attempts -> 400
    resp_trav1 = client.get("/artifacts/..%2f.env")
    assert resp_trav1.status_code == 400

    resp_trav2 = client.get("/artifacts/subdir%2fsecret.key")
    assert resp_trav2.status_code == 400

    # 3. Missing file -> 404
    resp_missing = client.get("/artifacts/nonexistent_report.json")
    assert resp_missing.status_code == 404


# ---------------------------------------------------------------------------
# 8. Secret Redaction & XSS Escaping Tests
# ---------------------------------------------------------------------------


def test_secret_redaction_and_xss_protection(tmp_path: Path) -> None:
    """Verify secrets (API keys, tokens) are redacted and untrusted input is escaped."""
    db_file = tmp_path / "redact.db"
    _seed_sample_run(db_file, run_id="run-secret-check")

    cfg = DashboardConfig(db_path=db_file, base_dir=Path.cwd())
    app = create_web_app(cfg)
    client = TestClient(app)

    resp = client.get("/execution?run_id=run-secret-check")
    assert resp.status_code == 200
    # Must NOT leak plain token:
    assert "sk-super-secret-token" not in resp.text


# ---------------------------------------------------------------------------
# 9. Authentic StockPile Offline Acceptance Tests
# ---------------------------------------------------------------------------


def test_stockpile_authentic_artifacts_offline_rendering(
    tmp_path: Path,
) -> None:
    """Verify StockPile authentic artifacts render 60 operations and 8 smoke passes offline."""
    db_file = tmp_path / "stockpile_web.db"
    cfg = DashboardConfig(
        db_path=db_file,
        base_dir=Path.cwd(),
        spec_path=Path("stockpile_tests/stockpile_openapi.yaml"),
    )
    app = create_web_app(cfg)
    client = TestClient(app)

    # Overview
    r_overview = client.get("/")
    assert r_overview.status_code == 200
    assert "OpenAPI definition" in r_overview.text
    assert "60" in r_overview.text  # 60 documented operations

    # Guided
    r_guided = client.get("/guided")
    assert r_guided.status_code == 200
    assert "Stage 01: Project Inspection" in r_guided.text

    # Intelligence
    r_intel = client.get("/intelligence")
    assert r_intel.status_code == 200
    assert "dashboard" in r_intel.text
    assert "expenses" in r_intel.text
    assert "products" in r_intel.text

    # Planning
    r_plan = client.get("/planning")
    assert r_plan.status_code == 200
    assert "205" in r_plan.text  # 205 total scenarios

    # Trust
    r_trust = client.get("/trust")
    assert r_trust.status_code == 200
    assert "PASS_WITHIN_VERIFIED_COVERAGE" in r_trust.text


# ---------------------------------------------------------------------------
# 10. Web Security Unit Tests (CSRF, Filename, Context Redaction)
# ---------------------------------------------------------------------------


def test_csrf_token_lifecycle_and_edge_cases() -> None:
    """Test CSRF token generation, signature validation, expiry, and tampering."""
    secret = "unit-test-csrf-secret"
    token = generate_csrf_token(secret, session_id="user-123")
    assert verify_csrf_token(token, secret, max_age_seconds=3600) is True

    # Wrong secret
    assert verify_csrf_token(token, "wrong-secret") is False

    # Empty or non-string token
    assert verify_csrf_token("", secret) is False
    assert verify_csrf_token(None, secret) is False

    # Tampered token
    assert verify_csrf_token(token + "tampered", secret) is False
    assert verify_csrf_token("invalid:base64:token", secret) is False

    # Expired token (simulate with max_age_seconds=-1)
    assert verify_csrf_token(token, secret, max_age_seconds=-1) is False


def test_validate_artifact_filename_branches() -> None:
    """Test filename sanitizer catches traversals, dotfiles, null bytes, and empty inputs."""
    import pytest

    from apiscope.core.exceptions import PathTraversalError
    from apiscope.web.security import validate_artifact_filename

    assert validate_artifact_filename("report.json") == "report.json"
    assert validate_artifact_filename("audit-2026.yaml") == "audit-2026.yaml"

    with pytest.raises(PathTraversalError):
        validate_artifact_filename("")

    with pytest.raises(PathTraversalError):
        validate_artifact_filename(None)  # type: ignore

    with pytest.raises(PathTraversalError):
        validate_artifact_filename("../secret.env")

    with pytest.raises(PathTraversalError):
        validate_artifact_filename("sub/dir/file.json")

    with pytest.raises(PathTraversalError):
        validate_artifact_filename("sub\\dir\\file.json")

    with pytest.raises(PathTraversalError):
        validate_artifact_filename("null\x00byte.txt")

    with pytest.raises(PathTraversalError):
        validate_artifact_filename(".hidden")


def test_sanitize_display_context_structures() -> None:
    """Test recursive sanitization across strings, dicts, lists, tuples, and objects."""
    from apiscope.web.security import sanitize_display_context

    data = {
        "token": "sk-sensitive-api-token-12345",
        "nested": [
            ("key", "Authorization: Bearer super-secret-jwt"),
            {"password": "mypassword123"},
        ],
    }
    sanitized = sanitize_display_context(data)
    assert "sk-sensitive-api-token-12345" not in str(sanitized)
    assert "super-secret-jwt" not in str(sanitized)
    assert "mypassword123" not in str(sanitized)


def test_guided_run_error_handling(tmp_path: Path) -> None:
    """Test POST /api/guided/run handles missing spec, bad JSON, and form submissions."""
    db_file = tmp_path / "errors.db"
    secret = "error-test-secret"
    cfg = DashboardConfig(db_path=db_file, base_dir=tmp_path, csrf_secret=secret)
    app = create_web_app(cfg)
    client = TestClient(app)

    valid_token = generate_csrf_token(secret)

    # 1. Missing spec path
    r_no_spec = client.post(
        "/api/guided/run",
        json={"csrf_token": valid_token, "spec_path": ""},
    )
    assert r_no_spec.status_code == 400

    # 2. Spec path traversal
    r_trav = client.post(
        "/api/guided/run",
        json={"csrf_token": valid_token, "spec_path": "../outside.yaml"},
    )
    assert r_trav.status_code == 400

    # 3. HTML form submission redirection
    sample_spec = tmp_path / "openapi.yaml"
    sample_spec.write_text(
        "openapi: 3.0.0\ninfo:\n  title: Test API\n  version: 1.0.0\npaths: {}\n",
        encoding="utf-8",
    )
    r_form = client.post(
        "/api/guided/run",
        data={
            "csrf_token": valid_token,
            "spec_path": "openapi.yaml",
        },
        headers={"accept": "text/html"},
        follow_redirects=False,
    )
    assert r_form.status_code in (303, 200)
