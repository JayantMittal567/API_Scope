"""Integration tests for Module M12 AI workflow, governance, CLI, and Web Dashboard.

Covers: FR-24, FR-26, FR-52, FR-53, FR-54, FR-55, NFR-02, NFR-03, NFR-04, NFR-06.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sample_app.app import create_sample_app, reset_state
from typer.testing import CliRunner

from apiscope.cli.main import app as cli_app
from apiscope.core.executor import RequestExecutor, TargetAccessPolicy
from apiscope.core.results import CaseStatus
from apiscope.core.run_service import RunService
from apiscope.definitions.collection import load_collection
from apiscope.definitions.environment import Environment
from apiscope.definitions.models import DefinitionStatus
from apiscope.generators.ai_generator import (
    AiGeneratorService,
    ProfileDrivenSynthesisProvider,
)
from apiscope.web import DashboardConfig, create_web_app

runner = CliRunner()


class SynchronousASGITransport(httpx.BaseTransport):
    """Synchronous transport adapter that bridges RequestExecutor to FastAPI ASGI."""

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


@pytest.fixture(autouse=True)
def _reset_sample_app_state() -> None:
    """Ensure sample application in-memory state is clean before each test."""
    reset_state()


def test_proposed_ai_tests_are_skipped_by_run_service_until_approved(
    tmp_path: Path,
) -> None:
    """Covers: FR-52, FR-53, FR-54, NFR-06.

    Enforces governance: proposed AI tests are strictly skipped by default.
    Once approved via AiGeneratorService, the tests execute against target
    and deterministically detect planted bugs: BUG-02, BUG-03, and BUG-05.
    """
    app = create_sample_app()
    transport = SynchronousASGITransport(app=app)
    executor = RequestExecutor(
        transport=transport,
        policy=TargetAccessPolicy(allow_private_networks=True),
    )
    service = RunService(executor=executor)

    proposed_suite_path = Path("examples/m12_ai_proposed_suite.yaml")
    collection = load_collection(proposed_suite_path)
    env = Environment(
        name="test_env",
        variables={
            "base_url": "http://127.0.0.1:8000",
            "user_1_token": "token-user-1",
        },
    )

    # 1. Run without include_proposed (Default Governance)
    run_result = service.run_collection(
        collection=collection,
        environment=env,
        include_proposed=False,
    )
    assert run_result.total_tests == 4
    assert run_result.skipped_tests == 4
    for result in run_result.results:
        assert result.status == CaseStatus.SKIPPED
        assert result.skip_reason is not None
        assert "proposed" in result.skip_reason.lower()

    # 2. Approve the proposed suite via AiGeneratorService
    ai_service = AiGeneratorService(provider=ProfileDrivenSynthesisProvider())
    approved_file = tmp_path / "approved_suite.yaml"
    approved_collection = ai_service.approve_suite(
        suite_path=proposed_suite_path,
        output_path=approved_file,
    )
    assert len(approved_collection.tests) == 4
    for test in approved_collection.tests:
        assert test.status == DefinitionStatus.APPROVED

    # 3. Execute approved suite against target application
    reset_state()
    approved_run_result = service.run_collection(
        collection=approved_collection,
        environment=env,
        include_proposed=False,
    )
    assert approved_run_result.total_tests == 4
    assert approved_run_result.skipped_tests == 0

    results_by_name = {r.test_name: r for r in approved_run_result.results}

    # Verify BUG-02 (IDOR) was caught: expected status 403, but received 200
    idor_res = results_by_name["AI-GEN BUG-02 IDOR Customer Profile Access"]
    assert idor_res.status == CaseStatus.FAILED
    assert idor_res.status_code == 200

    # Verify BUG-03 initial redeem passes
    coupon_step1 = results_by_name["AI-GEN BUG-03 Single-Use Coupon Redeem Step 1"]
    assert coupon_step1.status == CaseStatus.PASSED
    assert coupon_step1.status_code == 200

    # Verify BUG-03 (Coupon repeat) was caught: expected [400, 409], received 200
    coupon_step2 = results_by_name["AI-GEN BUG-03 Single-Use Coupon Repeat Step 2"]
    assert coupon_step2.status == CaseStatus.FAILED
    assert coupon_step2.status_code == 200

    # Verify BUG-05 (Cancel shipped order) was caught: expected [400, 409], received 200
    cancel_res = results_by_name["AI-GEN BUG-05 Cancel Shipped Order Invalid State"]
    assert cancel_res.status == CaseStatus.FAILED
    assert cancel_res.status_code == 200


def test_cli_ai_generate_and_approve_and_web_approve_endpoint(
    tmp_path: Path,
) -> None:
    """Covers: FR-24, FR-26, FR-53, FR-55, NFR-02, NFR-04.

    Verifies CLI workflow (`apiscope ai-generate`, `apiscope approve`),
    and Web Dashboard REST API endpoints (`/api/approve`, `/api/ai-generate`),
    including directory traversal protection.
    """
    # 1. CLI ai-generate
    generated_file = tmp_path / "cli_generated.yaml"
    res_gen = runner.invoke(
        cli_app,
        [
            "ai-generate",
            "sample_app/openapi.yaml",
            "--profile",
            "examples/m12_sample_app_profile.yaml",
            "--output",
            str(generated_file),
            "--provider",
            "profile-fallback",
        ],
    )
    assert res_gen.exit_code == 0
    assert "Generated 4 AI test cases" in res_gen.stdout
    assert generated_file.is_file()

    # 2. CLI approve
    approved_file = tmp_path / "cli_approved.yaml"
    res_appr = runner.invoke(
        cli_app,
        [
            "approve",
            str(generated_file),
            "--output",
            str(approved_file),
        ],
    )
    assert res_appr.exit_code == 0
    assert "Approved 4 tests" in res_appr.stdout
    assert approved_file.is_file()

    # 3. Web Dashboard REST API
    web_base = tmp_path / "web_workspace"
    web_base.mkdir(parents=True, exist_ok=True)
    db_file = web_base / "dashboard.db"

    cfg = DashboardConfig(db_path=db_file, base_dir=web_base)
    web_app = create_web_app(cfg)
    client = TestClient(web_app)

    # 3a. Security: Path traversal on POST /api/approve rejected
    res_evil_appr = client.post("/api/approve", json={"suite_path": "../../evil.yaml"})
    assert res_evil_appr.status_code == 400

    # 3b. Security: Path traversal on POST /api/ai-generate rejected
    res_evil_gen = client.post(
        "/api/ai-generate",
        json={
            "spec_path": "../../evil.yaml",
            "profile_path": "profile.yaml",
            "output_path": "out.yaml",
        },
    )
    assert res_evil_gen.status_code == 400

    # 3c. Valid POST /api/ai-generate in web_base
    shutil.copy("sample_app/openapi.yaml", web_base / "spec.yaml")
    shutil.copy("examples/m12_sample_app_profile.yaml", web_base / "prof.yaml")

    res_web_gen = client.post(
        "/api/ai-generate",
        json={
            "spec_path": "spec.yaml",
            "profile_path": "prof.yaml",
            "output_path": "web_gen.yaml",
            "provider": "profile-fallback",
        },
    )
    assert res_web_gen.status_code == 201
    gen_data = res_web_gen.json()
    assert gen_data["status"] == "success"
    assert gen_data["generated_count"] == 4

    # 3d. Valid POST /api/approve in web_base
    res_web_appr = client.post(
        "/api/approve",
        json={"suite_path": "web_gen.yaml"},
    )
    assert res_web_appr.status_code == 200
    appr_data = res_web_appr.json()
    assert appr_data["status"] == "success"
    assert appr_data["approved_count"] == 4


def test_cli_run_skips_proposed_tests_unless_flag_is_passed(
    tmp_path: Path,
) -> None:
    """Covers: FR-54, FR-55.

    Verify that `apiscope run` skips proposed tests unless `--include-proposed` is set.
    """
    suite_copy = tmp_path / "proposed_suite.yaml"
    shutil.copy("examples/m12_ai_proposed_suite.yaml", suite_copy)

    # 1. Run without --include-proposed
    res_default = runner.invoke(
        cli_app,
        ["run", str(suite_copy)],
    )
    # Total tests = 4, skipped = 4, passed = 0. all_passed is False so exit code is 1
    assert res_default.exit_code == 1
    assert "Skipped: 4" in res_default.stdout
    assert "Passed: 0" in res_default.stdout
    assert "Failed: 0" in res_default.stdout

    # 2. Run with --include-proposed
    # Tests execute (not skipped) and fail due to unresolved base_url or assertions
    res_include = runner.invoke(
        cli_app,
        ["run", str(suite_copy), "--include-proposed"],
    )
    assert res_include.exit_code == 1
    assert "Skipped: 0" in res_include.stdout
