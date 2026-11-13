"""End-to-end system tests verifying full pipeline integration and evaluation.

Covers: FR-16, FR-20, FR-21, FR-22, FR-24, FR-27, FR-28, FR-31, FR-34, FR-36,
        FR-38, FR-39, FR-42, FR-46, FR-50, FR-53, M13, NFR-02, NFR-03, NFR-04, NFR-06.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sample_app.app import create_sample_app, reset_state
from typer.testing import CliRunner

from apiscope.analysis.evaluator_experiment import run_planted_bug_experiment
from apiscope.analysis.silent_failure_service import (
    SilentFailureService,
    load_app_log,
)
from apiscope.cli.main import app as cli_app
from apiscope.core.executor import RequestExecutor, TargetAccessPolicy
from apiscope.core.models import HttpMethod
from apiscope.core.results import CaseStatus
from apiscope.core.run_service import RunService
from apiscope.definitions.collection import TestCollection
from apiscope.definitions.environment import Environment
from apiscope.definitions.models import (
    DefinitionSource,
    DefinitionStatus,
    TestDefinition,
    TestRequestDefinition,
)
from apiscope.web import DashboardConfig, create_web_app

runner = CliRunner()


class _SystemASGITransport(httpx.BaseTransport):
    """Synchronous transport adapter bridging RequestExecutor to FastAPI ASGI."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        async def _call() -> httpx.Response:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                base_url="http://127.0.0.1:8000",
            ) as client:
                resp = await client.request(
                    method=request.method,
                    url=str(request.url),
                    headers=request.headers,
                    content=request.content,
                )
                return httpx.Response(
                    status_code=resp.status_code,
                    headers=resp.headers,
                    content=resp.content,
                    request=request,
                )

        return asyncio.run(_call())


def test_four_way_planted_bug_evaluation_experiment() -> None:
    """Covers: FR-16, FR-20, FR-21, FR-22, FR-31, FR-36, FR-39, FR-42, FR-50,
            FR-53, M13.

    Runs the 4-way evaluation experiment across all 12 planted bugs in sample_app
    and verifies the clean target baseline on StockPile:
      - 0 false positives across all 4 strategies,
      - Rule-based generation detects >= 5 bugs,
      - Rule-based + AI detects >= 8 bugs,
      - Combined pipeline achieves 12/12 (100.0%) detection rate,
      - StockPile clean baseline achieves 0 false positives and HEALTHY verdicts.
    """
    report = run_planted_bug_experiment()

    # Strategy 1 (Handwritten probes)
    assert report.handwritten_metrics.total_tests_executed > 0
    assert report.handwritten_metrics.false_positives == 0
    assert report.handwritten_metrics.bugs_detected_count == 10
    assert report.handwritten_metrics.detection_rate_pct > 80.0

    # Strategy 2 (Rule-Based only)
    assert report.rule_generated_metrics.total_tests_executed >= 70
    assert report.rule_generated_metrics.false_positives == 0
    assert report.rule_generated_metrics.bugs_detected_count >= 5
    for expected_bug in ["BUG-04", "BUG-06", "BUG-07"]:
        assert expected_bug in report.rule_generated_metrics.bugs_detected

    # Strategy 3 (Rule-Based + AI)
    assert report.rule_plus_ai_metrics.total_tests_executed >= 74
    assert report.rule_plus_ai_metrics.false_positives == 0
    assert report.rule_plus_ai_metrics.bugs_detected_count >= 8
    for expected_ai_bug in ["BUG-02", "BUG-03", "BUG-05"]:
        assert expected_ai_bug in report.rule_plus_ai_metrics.bugs_detected

    # Strategy 4 (Full ApiScope Combined Pipeline)
    assert report.combined_apiscope_metrics.total_tests_executed >= 90
    assert report.combined_apiscope_metrics.false_positives == 0
    assert report.combined_apiscope_metrics.bugs_detected_count == 12
    assert report.combined_apiscope_metrics.detection_rate_pct == 100.0

    all_12 = [f"BUG-{i:02d}" for i in range(1, 13)]
    assert report.combined_apiscope_metrics.bugs_detected == all_12

    # StockPile Clean Target Specificity
    clean_summary = report.clean_target_summary
    assert clean_summary["false_positives"] == 0
    assert clean_summary["failed_tests"] == 0
    assert clean_summary["status"] == "ALL_HEALTHY_ZERO_FALSE_POSITIVES"
    for _mod, verdict in clean_summary["module_verdicts"].items():
        assert verdict == "HEALTHY"


def test_correlation_id_end_to_end_from_run_service_to_silent_failure_s1(
    tmp_path: Path,
) -> None:
    """Covers: FR-39, NFR-06.

    Verifies distributed trace correlation:
      1. RunService generates correlation ID 'corr-test-999' and injects
         X-Correlation-ID header into outbound HTTP request.
      2. sample_app BUG-10 endpoint includes [corr_id=corr-test-999] in app.log.
      3. CaseResult records correlation_id='corr-test-999'.
      4. SilentFailureService Signal S1 matches the correlation ID and correlates
         the error directly with the triggering test case.
    """
    tmp_app_log = tmp_path / "app.log"
    tmp_access_log = tmp_path / "access.log"
    tmp_app_log.write_text("", encoding="utf-8")
    tmp_access_log.write_text("", encoding="utf-8")

    reset_state()
    app = create_sample_app(
        app_log_path=tmp_app_log,
        access_log_path=tmp_access_log,
    )
    transport = _SystemASGITransport(app=app)
    executor = RequestExecutor(
        transport=transport,
        policy=TargetAccessPolicy(allow_private_networks=True),
    )

    expected_corr_id = "corr-test-999"
    run_service = RunService(
        executor=executor,
        correlation_id_generator=lambda: expected_corr_id,
    )

    test_def = TestDefinition(
        name="Sync Invoice Test",
        module="orders",
        request=TestRequestDefinition(
            method=HttpMethod.POST,
            url="{{base_url}}/orders/101/sync-invoice",
            headers={"Authorization": "Bearer {{user_1_token}}"},
        ),
        assertions=[{"type": "status", "equals": 200}],
        source=DefinitionSource.HANDWRITTEN,
        status=DefinitionStatus.APPROVED,
    )

    coll = TestCollection(
        name="Sync Suite",
        module="orders",
        tests=[test_def],
    )
    env = Environment(
        name="test_env",
        variables={
            "base_url": "http://127.0.0.1:8000",
            "user_1_token": "token-user-1",
        },
    )

    run_result = run_service.run_collection(coll, environment=env)
    assert run_result.total_tests == 1
    assert len(run_result.results) == 1

    case_res = run_result.results[0]
    assert case_res.status == CaseStatus.PASSED
    assert case_res.correlation_id == expected_corr_id

    # Verify log output in app.log contains the correlation token
    log_content = tmp_app_log.read_text(encoding="utf-8")
    assert f"[corr_id={expected_corr_id}]" in log_content

    # Analyze via SilentFailureService
    app_entries = load_app_log(tmp_app_log)
    silent_service = SilentFailureService()
    report = silent_service.analyze(
        run_result=run_result,
        app_log_entries=app_entries,
    )

    # Must find Signal S1 with evidence citing the exact test and correlation ID
    s1_signals = [s for s in report.signals if s.signal_type.value == "S1_LOG_ERROR"]
    assert len(s1_signals) >= 1
    s1 = s1_signals[0]
    assert expected_corr_id in s1.evidence
    assert "Correlated directly with test 'Sync Invoice Test'" in s1.evidence
    assert s1.module == "orders"


def test_full_system_pipeline_cli_db_reports_ai_health_zombie_and_web(
    tmp_path: Path,
) -> None:
    """Covers: FR-24, FR-27, FR-28, FR-34, FR-38, FR-46, FR-53, NFR-03, NFR-06.

    Verifies the end-to-end user journey across all CLI commands, persistence,
    multi-format report rendering, AI lifecycle, health analysis, and Web Dashboard.
    """
    db_file = tmp_path / "system_telemetry.db"
    out_dir = tmp_path / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. CLI init
    res_init = runner.invoke(
        cli_app,
        ["init", "--dir", str(out_dir)],
    )
    assert res_init.exit_code == 0
    starter_env = out_dir / "apiscope.env.yaml"
    starter_suite = out_dir / "sample_suite.yaml"
    assert starter_env.exists()
    assert starter_suite.exists()

    # 2. CLI import
    imported_spec = out_dir / "imported_spec.yaml"
    res_import = runner.invoke(
        cli_app,
        ["import", "sample_app/openapi.yaml", "--output", str(imported_spec)],
    )
    assert res_import.exit_code == 0
    assert imported_spec.exists()

    # 3. CLI generate (rule-based)
    rule_suite = out_dir / "rule_suite.yaml"
    res_gen = runner.invoke(
        cli_app,
        [
            "generate",
            "sample_app/openapi.yaml",
            "--output",
            str(rule_suite),
            "--module",
            "orders",
        ],
    )
    assert res_gen.exit_code == 0
    assert rule_suite.exists()

    # 4. CLI ai-generate (proposed) & approve
    ai_suite = out_dir / "ai_proposed.yaml"
    res_ai_gen = runner.invoke(
        cli_app,
        [
            "ai-generate",
            "sample_app/openapi.yaml",
            "--profile",
            "examples/m12_sample_app_profile.yaml",
            "--output",
            str(ai_suite),
            "--provider",
            "profile-fallback",
        ],
    )
    assert res_ai_gen.exit_code == 0
    assert ai_suite.exists()

    approved_suite = out_dir / "ai_approved.yaml"
    res_approve = runner.invoke(
        cli_app,
        ["approve", str(ai_suite), "--output", str(approved_suite)],
    )
    assert res_approve.exit_code == 0
    assert approved_suite.exists()

    # 5. CLI run against starter_suite (persisting to SQLite)
    res_run = runner.invoke(
        cli_app,
        [
            "run",
            str(starter_suite),
            "--env-file",
            str(starter_env),
            "--db",
            str(db_file),
        ],
    )
    assert res_run.exit_code in (0, 1)

    # 6. CLI report exports (JSON, JUnit XML, HTML)
    json_rep = out_dir / "report.json"
    res_rep_json = runner.invoke(
        cli_app,
        [
            "report",
            "--db",
            str(db_file),
            "--format",
            "json",
            "--output",
            str(json_rep),
        ],
    )
    assert res_rep_json.exit_code == 0
    assert json_rep.exists()

    junit_rep = out_dir / "report.xml"
    res_rep_junit = runner.invoke(
        cli_app,
        [
            "report",
            "--db",
            str(db_file),
            "--format",
            "junit",
            "--output",
            str(junit_rep),
        ],
    )
    assert res_rep_junit.exit_code == 0
    assert junit_rep.exists()

    html_rep = out_dir / "report.html"
    res_rep_html = runner.invoke(
        cli_app,
        [
            "report",
            "--db",
            str(db_file),
            "--format",
            "html",
            "--output",
            str(html_rep),
        ],
    )
    assert res_rep_html.exit_code == 0
    assert html_rep.exists()

    # 7. CLI zombie analysis
    res_zombie = runner.invoke(
        cli_app,
        [
            "zombie",
            "--spec",
            "sample_app/openapi.yaml",
            "--access-log",
            "sample_app/logs/access.log",
            "--db",
            str(db_file),
        ],
    )
    assert res_zombie.exit_code == 0
    assert "Zombie API Analysis" in res_zombie.output

    # 8. CLI health evaluation
    res_health = runner.invoke(
        cli_app,
        [
            "health",
            "--db",
            str(db_file),
            "--spec",
            "sample_app/openapi.yaml",
            "--access-log",
            "sample_app/logs/access.log",
            "--app-log",
            "sample_app/logs/app.log",
        ],
    )
    assert res_health.exit_code in (0, 1)

    # 9. CLI serve --check-only
    res_serve_check = runner.invoke(
        cli_app,
        ["serve", "--db", str(db_file), "--check-only"],
    )
    assert res_serve_check.exit_code == 0

    # 10. Web Dashboard test client endpoints
    web_config = DashboardConfig(
        db_path=db_file,
        spec_path=Path("sample_app/openapi.yaml"),
        access_log_path=Path("sample_app/logs/access.log"),
        app_log_path=Path("sample_app/logs/app.log"),
    )
    web_app = create_web_app(config=web_config)
    client = TestClient(web_app)

    r_root = client.get("/")
    assert r_root.status_code == 200
    assert "ApiScope" in r_root.text

    r_runs = client.get("/runs")
    assert r_runs.status_code == 200

    r_health = client.get("/health")
    assert r_health.status_code == 200

    r_zombies = client.get("/zombies")
    assert r_zombies.status_code == 200

    r_trends = client.get("/api/trends")
    assert r_trends.status_code == 200
    assert "run_count" in r_trends.json()
