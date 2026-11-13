"""Integration tests for web dashboard suite trigger and CLI serve command.

Covers: FR-18, FR-24, FR-25, FR-26, FR-27, FR-48, NFR-02, NFR-04.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from apiscope.cli.main import app as cli_app
from apiscope.core.models import HttpResponse
from apiscope.core.run_service import RunService
from apiscope.web import DashboardConfig, create_web_app

runner = CliRunner()


def test_post_api_runs_triggers_suite_and_persists_to_sqlite(
    tmp_path: Path,
) -> None:
    """Covers: FR-48, FR-18, FR-27.

    Trigger suite execution via POST /api/runs using a mocked HTTP transport,
    verify 201 Created response, and confirm run persistence via GET /runs/{run_id}.
    """
    db_file = tmp_path / "trigger_test.db"
    cfg = DashboardConfig(db_path=db_file)

    mock_executor = MagicMock()
    mock_executor.execute.side_effect = [
        HttpResponse(
            status_code=201,
            headers={"Content-Type": "application/json"},
            body='{"id": "u-123", "token": "secret-tok", "status": "ACTIVE"}',
            elapsed_ms=15.0,
        ),
        HttpResponse(
            status_code=201,
            headers={"Content-Type": "application/json"},
            body='{"order_id": "ord-999", "confirmed": true}',
            elapsed_ms=25.0,
        ),
        HttpResponse(
            status_code=200,
            headers={"Content-Type": "application/json"},
            body='{"order_id": "ord-999", "item": "laptop", "status": "CONFIRMED"}',
            elapsed_ms=10.0,
        ),
    ]

    run_service = RunService(executor=mock_executor)
    app = create_web_app(config=cfg, run_service=run_service)
    client = TestClient(app)

    # Trigger run with valid suite path
    resp = client.post(
        "/api/runs",
        json={"suite_path": "examples/m4_chaining_suite.yaml"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "run_id" in data
    assert data["total_tests"] == 3
    assert data["passed_tests"] == 3
    assert data["failed_tests"] == 0

    run_id = data["run_id"]
    assert isinstance(run_id, str) and len(run_id) > 0

    # Confirm run is persisted and retrievable via GET /runs/{run_id}
    detail_resp = client.get(f"/runs/{run_id}")
    assert detail_resp.status_code == 200
    assert run_id in detail_resp.text
    assert "Create User" in detail_resp.text
    assert "Create Order" in detail_resp.text
    assert "Get Order" in detail_resp.text


def test_post_api_runs_blocks_path_traversal_and_missing_suite(
    tmp_path: Path,
) -> None:
    """Covers: FR-48, NFR-02, NFR-04.

    Verify POST /api/runs rejects paths outside the repository and nonexistent files
    with 400 Bad Request.
    """
    db_file = tmp_path / "security_test.db"
    cfg = DashboardConfig(db_path=db_file)
    client = TestClient(create_web_app(cfg))

    # Path traversal in suite_path
    resp_trav = client.post(
        "/api/runs",
        json={"suite_path": "../../secret/outside.yaml"},
    )
    assert resp_trav.status_code == 400
    assert "traversal" in resp_trav.json()["error"].lower()

    # Nonexistent suite
    resp_missing = client.post(
        "/api/runs",
        json={"suite_path": "does_not_exist_file.yaml"},
    )
    assert resp_missing.status_code == 400
    assert (
        "not found" in resp_missing.json()["error"].lower()
        or "does not exist" in resp_missing.json()["error"].lower()
    )

    # Path traversal in env_file
    resp_env_trav = client.post(
        "/api/runs",
        json={
            "suite_path": "examples/m4_chaining_suite.yaml",
            "env_file": "../../evil.yaml",
        },
    )
    assert resp_env_trav.status_code == 400
    assert "traversal" in resp_env_trav.json()["error"].lower()

    # Path traversal in data_file
    resp_data_trav = client.post(
        "/api/runs",
        json={
            "suite_path": "examples/m4_chaining_suite.yaml",
            "data_file": "../../evil.csv",
        },
    )
    assert resp_data_trav.status_code == 400
    assert "traversal" in resp_data_trav.json()["error"].lower()


def test_cli_serve_check_only_validates_config_and_returns_0_or_3() -> None:
    """Covers: FR-24, FR-25, FR-26.

    Verify `apiscope serve --check-only` succeeds with exit code 0 when valid,
    and fails with exit code 3 on port or file errors.
    """
    # 1. Valid configuration
    result_ok = runner.invoke(
        cli_app,
        [
            "serve",
            "--check-only",
            "--port",
            "8080",
            "--spec",
            "sample_app/openapi.yaml",
        ],
    )
    assert result_ok.exit_code == 0
    assert "ready at" in result_ok.stdout

    # 2. Invalid port (too high)
    result_bad_port = runner.invoke(
        cli_app,
        ["serve", "--check-only", "--port", "99999"],
    )
    assert result_bad_port.exit_code == 3
    port_err = result_bad_port.stderr or result_bad_port.stdout
    assert "Port must be between 1 and 65535" in port_err

    # 3. Invalid port (zero)
    result_zero_port = runner.invoke(
        cli_app,
        ["serve", "--check-only", "--port", "0"],
    )
    assert result_zero_port.exit_code == 3
    zero_err = result_zero_port.stderr or result_zero_port.stdout
    assert "Port must be between 1 and 65535" in zero_err

    # 4. Nonexistent OpenAPI spec
    result_missing_spec = runner.invoke(
        cli_app,
        ["serve", "--check-only", "--spec", "nonexistent_spec.yaml"],
    )
    assert result_missing_spec.exit_code == 3
    spec_err = result_missing_spec.stderr or result_missing_spec.stdout
    assert "not found" in spec_err or "does not exist" in spec_err

    # 5. Nonexistent access log
    result_missing_log = runner.invoke(
        cli_app,
        ["serve", "--check-only", "--access-log", "nonexistent_access.log"],
    )
    assert result_missing_log.exit_code == 3
    log_err = result_missing_log.stderr or result_missing_log.stdout
    assert "not found" in log_err or "does not exist" in log_err

    # 6. Nonexistent app log
    result_missing_app = runner.invoke(
        cli_app,
        ["serve", "--check-only", "--app-log", "nonexistent_app.log"],
    )
    assert result_missing_app.exit_code == 3
    app_err = result_missing_app.stderr or result_missing_app.stdout
    assert "not found" in app_err or "does not exist" in app_err

    # 7. Nonexistent health probe file
    result_missing_health = runner.invoke(
        cli_app,
        ["serve", "--check-only", "--health-file", "nonexistent_probe.json"],
    )
    assert result_missing_health.exit_code == 3
    h_err = result_missing_health.stderr or result_missing_health.stdout
    assert "not found" in h_err or "does not exist" in h_err
