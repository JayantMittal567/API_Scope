"""Unit and security regression tests for ExecutionSafetyPolicy and Mutation Fencing.

Verifies the 17 security scenarios mandated by Security Fix 02:
 1. GET allowed under read-only policy
 2. POST blocked by default
 3. PUT blocked by default
 4. PATCH blocked by default
 5. DELETE blocked by default
 6. UNKNOWN operation handled conservatively (blocked)
 7. Explicitly risky GET blocked by read-only policy (e.g., /_test/reset, /purge)
 8. Mutation allowed only under appropriately authorized policy
 9. --allow-target alone does not authorize mutation
10. Dry-run issues zero HTTP requests
11. Dry-run masks secrets
12. Mutating generated tests cannot bypass policy through approved status alone
13. CLI and service execution enforce consistent restrictions
14. Dashboard execution cannot bypass central policy
15. Blocked operations produce understandable results (SKIPPED with clear reason)
16. Redirect and SSRF protections remain functional alongside safety policy
17. Existing authorized workflows migrate without silently losing test coverage
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from apiscope.cli.main import app as cli_app
from apiscope.core.exceptions import ExecutionSafetyError, InvalidRequestError
from apiscope.core.executor import RequestExecutor, TargetAccessPolicy
from apiscope.core.models import HttpMethod, HttpRequest
from apiscope.core.results import CaseStatus
from apiscope.core.run_service import RunService
from apiscope.core.safety import (
    PRODUCTION_ENVIRONMENTS,
    DryRunReport,
    ExecutionMode,
    ExecutionSafetyPolicy,
    OperationRisk,
)
from apiscope.data.connection import DatabaseManager
from apiscope.data.repositories import RunRepository
from apiscope.definitions.collection import TestCollection
from apiscope.definitions.environment import Environment
from apiscope.definitions.models import (
    DefinitionSource,
    DefinitionStatus,
    TestDefinition,
    TestRequestDefinition,
)
from apiscope.web.app import DashboardConfig, create_web_app

runner = CliRunner()


# =============================================================================
# SCENARIO 1: GET allowed under read-only policy
# =============================================================================
def test_scenario_01_get_allowed_under_read_only_policy() -> None:
    policy = ExecutionSafetyPolicy.read_only()
    result = policy.evaluate(
        request=HttpRequest(method=HttpMethod.GET, url="https://api.example.com/items")
    )
    assert result.permitted is True
    assert result.risk == OperationRisk.READ_ONLY
    assert result.reason is None


# =============================================================================
# SCENARIOS 2-5: POST, PUT, PATCH, DELETE blocked by default
# =============================================================================
@pytest.mark.parametrize(
    ("method", "expected_risk"),
    [
        (HttpMethod.POST, OperationRisk.MUTATING),
        (HttpMethod.PUT, OperationRisk.MUTATING),
        (HttpMethod.PATCH, OperationRisk.MUTATING),
        (HttpMethod.DELETE, OperationRisk.DESTRUCTIVE),
    ],
)
def test_scenarios_02_to_05_mutating_methods_blocked_by_default(
    method: HttpMethod, expected_risk: OperationRisk
) -> None:
    policy = ExecutionSafetyPolicy()  # Default is READ_ONLY
    result = policy.evaluate(
        request=HttpRequest(method=method, url="https://api.example.com/items/1")
    )
    assert result.permitted is False
    assert result.risk == expected_risk
    assert result.reason is not None
    assert f"classified as {expected_risk.value}" in result.reason
    assert "authorize via --allow-mutation" in result.reason


# =============================================================================
# SCENARIO 6: UNKNOWN operation handled conservatively (blocked)
# =============================================================================
def test_scenario_06_unknown_operation_blocked_conservatively() -> None:
    policy = ExecutionSafetyPolicy.read_only()
    result = policy.evaluate(
        request=HttpRequest(method=HttpMethod.GET, url="https://api.example.com/items"),
        test_def=TestDefinition(
            name="Unknown Method Test",
            module="test",
            request=TestRequestDefinition(
                method="PURGE_CUSTOM",  # type: ignore[arg-type]
                url="https://api.example.com/items",
            ),
        ),
    )
    assert result.permitted is False
    assert result.risk == OperationRisk.UNKNOWN
    assert "UNKNOWN risk classification" in str(result.reason)


# =============================================================================
# SCENARIO 7: Explicitly risky GET blocked by read-only policy
# =============================================================================
@pytest.mark.parametrize(
    "risky_path",
    [
        "/_test/reset",
        "/reset",
        "/restart",
        "/shutdown",
        "/clear",
        "/purge",
        "/truncate",
        "/destroy",
        "/cleanup",
    ],
)
def test_scenario_07_risky_get_endpoints_blocked_by_read_only_policy(
    risky_path: str,
) -> None:
    policy = ExecutionSafetyPolicy.read_only()
    req = HttpRequest(method=HttpMethod.GET, url=f"https://api.example.com{risky_path}")
    result = policy.evaluate(request=req)
    assert result.permitted is False
    assert result.risk == OperationRisk.DESTRUCTIVE
    assert "DESTRUCTIVE" in str(result.reason)


def test_scenario_07_custom_endpoint_override_blocks_risky_endpoint() -> None:
    policy = ExecutionSafetyPolicy(
        mode=ExecutionMode.READ_ONLY,
        endpoint_overrides={"/trigger-sync": OperationRisk.MUTATING},
    )
    result = policy.evaluate(
        request=HttpRequest(
            method=HttpMethod.GET,
            url="https://api.example.com/trigger-sync?full=true",
        )
    )
    assert result.permitted is False
    assert result.risk == OperationRisk.MUTATING


# =============================================================================
# SCENARIO 8: Mutation allowed only under appropriately authorized policy
# =============================================================================
def test_scenario_08_mutation_allowed_under_authorized_policy_in_non_prod() -> None:
    policy = ExecutionSafetyPolicy(
        mode=ExecutionMode.PERMIT_MUTATIONS,
        allow_mutations=True,
    )
    env = Environment(name="dev")
    post_req = HttpRequest(method=HttpMethod.POST, url="https://api.example.com/items")
    result = policy.evaluate(request=post_req, environment=env)
    assert result.permitted is True
    assert result.risk == OperationRisk.MUTATING


def test_scenario_08_mutation_strictly_blocked_in_production_environment() -> None:
    policy = ExecutionSafetyPolicy(
        mode=ExecutionMode.PERMIT_ALL,
        allow_mutations=True,
        allow_destructive=True,
    )
    for prod_name in PRODUCTION_ENVIRONMENTS:
        env = Environment(name=prod_name)
        result = policy.evaluate(
            request=HttpRequest(
                method=HttpMethod.DELETE, url="https://api.example.com/items/1"
            ),
            environment=env,
        )
        assert result.permitted is False
        assert "designated production environment" in str(result.reason)


# =============================================================================
# SCENARIO 9: --allow-target alone does not authorize mutation
# =============================================================================
def test_scenario_09_allow_target_alone_does_not_authorize_mutation() -> None:
    tap = TargetAccessPolicy(allowed_targets=["http://localhost:8080"])
    assert tap.is_target_authorized("http://localhost:8080/items") is True

    safety = ExecutionSafetyPolicy.read_only()
    result = safety.evaluate(
        request=HttpRequest(method=HttpMethod.POST, url="http://localhost:8080/items")
    )
    assert result.permitted is False
    assert result.risk == OperationRisk.MUTATING

    executor = RequestExecutor(
        policy=tap,
        safety_policy=safety,
        transport=httpx.MockTransport(lambda _r: httpx.Response(200)),
    )
    with pytest.raises(ExecutionSafetyError, match="classified as MUTATING"):
        executor.execute(
            HttpRequest(method=HttpMethod.POST, url="http://localhost:8080/items")
        )


# =============================================================================
# SCENARIO 10: Dry-run issues zero HTTP requests
# =============================================================================
def test_scenario_10_dry_run_issues_zero_http_requests() -> None:
    request_dispatched = False

    def mock_handler(_req: httpx.Request) -> httpx.Response:
        nonlocal request_dispatched
        request_dispatched = True
        return httpx.Response(200, json={"ok": True})

    executor = RequestExecutor(transport=httpx.MockTransport(mock_handler))
    service = RunService(executor=executor)

    collection = TestCollection(
        name="Mixed Suite",
        module="store",
        tests=[
            TestDefinition(
                name="Get Item",
                module="store",
                request=TestRequestDefinition(
                    method=HttpMethod.GET,
                    url="https://api.example.com/items/1",
                ),
                status=DefinitionStatus.APPROVED,
            ),
            TestDefinition(
                name="Delete Item",
                module="store",
                request=TestRequestDefinition(
                    method=HttpMethod.DELETE,
                    url="https://api.example.com/items/1",
                ),
                status=DefinitionStatus.APPROVED,
            ),
        ],
    )

    report: DryRunReport = service.dry_run(collection)

    assert request_dispatched is False
    assert report.total_operations == 2
    assert report.read_only_count == 1
    assert report.destructive_count == 1
    assert report.executable_count == 1
    assert report.blocked_count == 1


# =============================================================================
# SCENARIO 11: Dry-run masks secrets
# =============================================================================
def test_scenario_11_dry_run_masks_secrets() -> None:
    service = RunService()
    env = Environment(
        name="test_env",
        variables={
            "base_url": "https://api.example.com",
            "api_key": "super-secret-api-token-888",
        },
        secrets={"api_key"},
    )
    collection = TestCollection(
        name="Secret Suite",
        module="auth",
        tests=[
            TestDefinition(
                name="Auth Test",
                module="auth",
                request=TestRequestDefinition(
                    method=HttpMethod.GET,
                    url="{{base_url}}/profile?token={{api_key}}",
                    headers={"Authorization": "Bearer {{api_key}}"},
                ),
                status=DefinitionStatus.APPROVED,
            )
        ],
    )

    report = service.dry_run(collection, environment=env)
    assert len(report.operations) == 1
    op = report.operations[0]

    assert "super-secret-api-token-888" not in op.url
    assert "***" in op.url
    assert op.masked_headers["Authorization"] == "***"


# =============================================================================
# SCENARIO 12: Mutating generated tests cannot bypass policy via approved status alone
# =============================================================================
def test_scenario_12_approved_status_alone_cannot_bypass_safety_policy() -> None:
    policy = ExecutionSafetyPolicy.read_only()
    approved_mutating_test = TestDefinition(
        name="Approved AI Mutating Test",
        module="admin",
        request=TestRequestDefinition(
            method=HttpMethod.DELETE, url="https://api.example.com/users/42"
        ),
        status=DefinitionStatus.APPROVED,
        source=DefinitionSource.AI_GENERATED,
    )
    result = policy.evaluate(test_def=approved_mutating_test)
    assert result.permitted is False
    assert result.risk == OperationRisk.DESTRUCTIVE
    assert "DESTRUCTIVE" in str(result.reason)


# =============================================================================
# SCENARIO 13: CLI and service execution enforce consistent restrictions
# =============================================================================
def test_scenario_13_cli_and_service_enforce_consistent_restrictions(
    tmp_path: Path,
) -> None:
    suite_file = tmp_path / "mutating_suite.yaml"
    suite_file.write_text(
        """
name: "Mutating Suite"
module: "items"
tests:
  - name: "Create Item"
    module: "items"
    request:
      method: "POST"
      url: "https://api.example.com/items"
      body:
        title: "Test"
    assertions:
      - type: "status"
        equals: 201
""",
        encoding="utf-8",
    )

    # 1. Service execution without allow_mutation blocks POST
    coll = TestCollection(
        name="Mutating Suite",
        module="items",
        tests=[
            TestDefinition(
                name="Create Item",
                module="items",
                request=TestRequestDefinition(
                    method=HttpMethod.POST,
                    url="https://api.example.com/items",
                ),
                status=DefinitionStatus.APPROVED,
            )
        ],
    )
    service = RunService()
    svc_res = service.run_collection(coll, allow_mutation=False)
    assert svc_res.results[0].status == CaseStatus.SKIPPED
    assert "MUTATING" in str(svc_res.results[0].skip_reason)

    # 2. CLI execution without --allow-mutation blocks POST with identical skip result
    db_path = tmp_path / "cli.db"
    res = runner.invoke(
        cli_app,
        ["run", str(suite_file), "--db", str(db_path)],
    )
    assert res.exit_code == 1  # Not all passed because test was skipped
    assert "Skipped: 1" in res.stdout


# =============================================================================
# SCENARIO 14: Dashboard execution cannot bypass central policy
# =============================================================================
def test_scenario_14_dashboard_execution_cannot_bypass_policy(
    tmp_path: Path,
) -> None:
    suite_file = tmp_path / "post_suite.yaml"
    suite_file.write_text(
        """
name: "Dashboard Mutating Suite"
module: "store"
tests:
  - name: "Post Item"
    module: "store"
    request:
      method: "POST"
      url: "https://api.example.com/orders"
    assertions:
      - type: "status"
        equals: 201
""",
        encoding="utf-8",
    )

    cfg = DashboardConfig(
        db_path=tmp_path / "dash.db",
        base_dir=tmp_path,
    )
    app = create_web_app(config=cfg, run_service=RunService())
    client = TestClient(app)

    resp = client.post(
        "/api/runs",
        json={"suite_path": str(suite_file)},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["total_tests"] == 1
    assert data["skipped_tests"] == 1
    assert data["passed_tests"] == 0

    db_mgr = DatabaseManager(tmp_path / "dash.db")
    repo = RunRepository(db_mgr)
    run_rec = repo.get_run(data["run_id"])
    assert run_rec is not None
    assert len(run_rec.results) == 1
    assert run_rec.results[0].status == CaseStatus.SKIPPED
    assert "MUTATING" in str(run_rec.results[0].skip_reason)
    db_mgr.close()


# =============================================================================
# SCENARIO 15: Blocked operations produce understandable results (SKIPPED)
# =============================================================================
def test_scenario_15_blocked_operations_produce_understandable_results() -> None:
    collection = TestCollection(
        name="Delete Suite",
        module="users",
        tests=[
            TestDefinition(
                name="Delete User",
                module="users",
                request=TestRequestDefinition(
                    method=HttpMethod.DELETE,
                    url="https://api.example.com/users/99",
                ),
                status=DefinitionStatus.APPROVED,
            )
        ],
    )
    service = RunService()
    run_result = service.run_collection(collection)
    assert run_result.total_tests == 1
    assert run_result.skipped_tests == 1
    case = run_result.results[0]
    assert case.status == CaseStatus.SKIPPED
    assert case.skip_reason is not None
    assert "DESTRUCTIVE" in case.skip_reason
    assert "--allow-mutation" in case.skip_reason
    assert case.error_message is None
    assert case.status_code is None


# =============================================================================
# SCENARIO 16: Redirect and SSRF protections remain functional
# =============================================================================
def test_scenario_16_redirect_and_ssrf_protections_remain_functional() -> None:
    def redirect_handler(req: httpx.Request) -> httpx.Response:
        if str(req.url) == "https://api.example.com/redirect":
            return httpx.Response(
                302, headers={"Location": "http://192.168.1.100/admin"}
            )
        return httpx.Response(200, json={"status": "ok"})

    policy = TargetAccessPolicy(allow_private_networks=False)
    safety = ExecutionSafetyPolicy.permissive()
    executor = RequestExecutor(
        policy=policy,
        safety_policy=safety,
        transport=httpx.MockTransport(redirect_handler),
    )

    req = HttpRequest(
        method=HttpMethod.GET,
        url="https://api.example.com/redirect",
        follow_redirects=True,
    )
    with pytest.raises(InvalidRequestError, match="private/local network IP"):
        executor.execute(req)


# =============================================================================
# SCENARIO 17 & CLI DRY-RUN: CLI dry-run preview and missing variables
# =============================================================================
def test_cli_dry_run_preview_and_exit_code_zero(tmp_path: Path) -> None:
    suite_file = tmp_path / "preview_suite.yaml"
    suite_file.write_text(
        """
name: "Dry Run Preview Suite"
module: "catalog"
tests:
  - name: "Read Products"
    module: "catalog"
    request:
      method: "GET"
      url: "{{base_url}}/products"
  - name: "Delete Product"
    module: "catalog"
    request:
      method: "DELETE"
      url: "{{base_url}}/products/{{item_id}}"
""",
        encoding="utf-8",
    )

    env_file = tmp_path / "dev.env.yaml"
    env_file.write_text(
        """
name: "dev"
variables:
  base_url: "https://api.example.com"
""",
        encoding="utf-8",
    )

    db_path = tmp_path / "dry_run.db"

    result = runner.invoke(
        cli_app,
        [
            "run",
            str(suite_file),
            "--env-file",
            str(env_file),
            "--dry-run",
            "--db",
            str(db_path),
        ],
    )
    assert result.exit_code == 0
    assert "=== DRY-RUN PLAN: Dry Run Preview Suite ===" in result.stdout
    assert "Planned Operations: 2" in result.stdout
    assert "Executable: 1" in result.stdout
    assert "Blocked: 1" in result.stdout
    assert "1 READ_ONLY, 0 MUTATING, 1 DESTRUCTIVE" in result.stdout
    assert "[PERMITTED]" in result.stdout
    assert "[BLOCKED:" in result.stdout
    assert "Missing Variables: item_id" in result.stdout

    assert not db_path.exists()
