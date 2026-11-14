"""Integration tests for ApiScope CLI commands (init, run, report).

Verifies non-interactive execution, deterministic exit codes (0, 1, 2, 3),
SQLite persistence, multi-format reporting, data-driven runs, and secret protection.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from apiscope.cli.main import app
from apiscope.core.executor import RequestExecutor
from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.core.run_service import RunService
from apiscope.data.connection import DatabaseManager
from apiscope.data.repositories import RunRepository

runner = CliRunner()


def test_cli_init_creates_starter_files_and_is_idempotent(tmp_path: Path) -> None:
    """Covers: FR-24, FR-26

    Verify that apiscope init creates starter files non-interactively and is idempotent.
    """
    target_dir = tmp_path / "starter_project"
    res = runner.invoke(app, ["init", "--dir", str(target_dir)])
    assert res.exit_code == 0
    assert "Initialized ApiScope project" in res.stdout

    env_path = target_dir / "apiscope.env.yaml"
    suite_path = target_dir / "sample_suite.yaml"
    assert env_path.is_file()
    assert suite_path.is_file()

    # Re-run without force should succeed and not overwrite
    res2 = runner.invoke(app, ["init", "--dir", str(target_dir)])
    assert res2.exit_code == 0
    assert "Starter files already exist" in res2.stdout

    # Re-run with force
    res3 = runner.invoke(app, ["init", "--dir", str(target_dir), "--force"])
    assert res3.exit_code == 0
    assert "Initialized ApiScope project" in res3.stdout


def test_cli_run_all_passed_returns_exit_code_0_and_persists_to_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Covers: FR-24, FR-25, FR-26, FR-27

    Verify that running a test suite where all tests pass exits with code 0
    and persists execution telemetry in SQLite.
    """
    suite_file = tmp_path / "passing_suite.yaml"
    suite_file.write_text(
        """
name: "Passing Suite"
module: "health"
tags:
  - "smoke"
tests:
  - name: "Health Ping"
    module: "health"
    tags:
      - "smoke"
    request:
      method: "GET"
      url: "https://api.example.com/health"
    assertions:
      - type: "status"
        equals: 200
""",
        encoding="utf-8",
    )

    def mock_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "healthy"})

    original_init = RunService.__init__

    def custom_init(self, *args, **kwargs):
        kwargs["executor"] = RequestExecutor(
            transport=httpx.MockTransport(mock_handler)
        )
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(RunService, "__init__", custom_init)

    db_path = tmp_path / "history.db"
    result = runner.invoke(
        app,
        [
            "run",
            str(suite_file),
            "--db",
            str(db_path),
        ],
    )

    assert result.exit_code == 0
    assert "Total: 1" in result.stdout
    assert "Passed: 1" in result.stdout

    # Verify run persisted to DB
    db_mgr = DatabaseManager(db_path)
    repo = RunRepository(db_mgr)
    latest = repo.get_latest_run()
    assert latest is not None
    assert latest.total_tests == 1
    assert latest.passed_tests == 1
    db_mgr.close()


def test_cli_run_with_failed_assertion_returns_exit_code_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Covers: FR-24, FR-26

    Verify that a failed assertion results in CLI exit code 1.
    """
    suite_file = tmp_path / "failing_suite.yaml"
    suite_file.write_text(
        """
name: "Failing Suite"
module: "users"
tests:
  - name: "Get User"
    module: "users"
    request:
      method: "GET"
      url: "https://api.example.com/users/999"
    assertions:
      - type: "status"
        equals: 200
""",
        encoding="utf-8",
    )

    def mock_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "Not Found"})

    original_init = RunService.__init__

    def custom_init(self, *args, **kwargs):
        kwargs["executor"] = RequestExecutor(
            transport=httpx.MockTransport(mock_handler)
        )
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(RunService, "__init__", custom_init)

    db_path = tmp_path / "fail.db"
    result = runner.invoke(
        app,
        [
            "run",
            str(suite_file),
            "--db",
            str(db_path),
        ],
    )

    assert result.exit_code == 1
    assert "Passed: 0" in result.stdout
    assert "Failed: 1" in result.stdout


def test_cli_run_with_warnings_returns_0_normally_and_2_with_strict_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Covers: FR-26

    Verify that runs passing with warnings exit with 0 normally, but exit with 2
    when --strict is enabled.
    """
    suite_file = tmp_path / "dummy_suite.yaml"
    suite_file.write_text(
        """
name: "Dummy Suite"
module: "test"
tests:
  - name: "Dummy"
    module: "test"
    request:
      method: "GET"
      url: "https://api.example.com/dummy"
""",
        encoding="utf-8",
    )

    t = datetime.now(UTC)
    case_passed = CaseResult(
        test_name="Dummy",
        module="test",
        tags=[],
        status=CaseStatus.PASSED,
        verified=True,
        method="GET",
        url="https://api.example.com/dummy",
        status_code=200,
        elapsed_ms=10.0,
        request_headers={},
        response_headers={},
        assertion_results=[],
        extracted_variables={},
        extraction_errors=[],
    )
    warn_run = RunResult(
        run_id="warn-run-123",
        environment_name="test",
        started_at=t,
        finished_at=t,
        duration_ms=10.0,
        total_tests=1,
        passed_tests=1,
        failed_tests=0,
        error_tests=0,
        skipped_tests=0,
        results=[case_passed],
        has_warnings=True,
    )

    monkeypatch.setattr(RunService, "run_collection", lambda *_a, **_kw: warn_run)

    db_path = tmp_path / "warn.db"

    # Run without --strict -> exit code 0
    res_normal = runner.invoke(
        app,
        [
            "run",
            str(suite_file),
            "--db",
            str(db_path),
        ],
    )
    assert res_normal.exit_code == 0

    # Run with --strict -> exit code 2
    res_strict = runner.invoke(
        app,
        [
            "run",
            str(suite_file),
            "--db",
            str(db_path),
            "--strict",
        ],
    )
    assert res_strict.exit_code == 2


def test_cli_run_invalid_file_or_zero_matching_tests_returns_exit_code_3(
    tmp_path: Path,
) -> None:
    """Covers: FR-25, FR-26, NFR-02

    Verify that missing files, path traversal attempts, or filters matching zero tests
    return exit code 3.
    """
    # 1. Non-existent file
    res1 = runner.invoke(app, ["run", str(tmp_path / "nonexistent.yaml")])
    assert res1.exit_code == 3
    assert "Error:" in res1.stderr or "Error:" in res1.stdout

    # 2. Path traversal sequence
    res2 = runner.invoke(app, ["run", "../../outside.yaml"])
    assert res2.exit_code == 3

    # 3. Valid suite but zero matching tests under filter
    suite_file = tmp_path / "suite.yaml"
    suite_file.write_text(
        """
name: "Suite"
module: "orders"
tests:
  - name: "Test 1"
    module: "orders"
    request:
      method: "GET"
      url: "https://api.example.com/test"
""",
        encoding="utf-8",
    )
    res3 = runner.invoke(
        app,
        ["run", str(suite_file), "--module", "nonexistent_module"],
    )
    assert res3.exit_code == 3
    assert "No tests matched the specified filters" in (res3.stdout + res3.stderr)


def test_cli_run_with_data_file_and_env_file_and_output_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Covers: FR-24, FR-25, FR-28, NFR-03

    Verify end-to-end execution with data-file, env-file, and output report,
    ensuring runtime HTTP execution receives raw secret while reports and
    stdout mask it.
    """
    env_file = tmp_path / "demo.env.yaml"
    env_file.write_text(
        """
name: "demo"
variables:
  base_url: "https://api.example.com"
  api_key: "raw-secret-token-777"
secrets:
  - "api_key"
""",
        encoding="utf-8",
    )

    data_file = tmp_path / "items.csv"
    data_file.write_text(
        "sku,quantity\nABC,2\nXYZ,5\n",
        encoding="utf-8",
    )

    suite_file = tmp_path / "orders.yaml"
    suite_file.write_text(
        """
name: "Parameterized Orders"
module: "orders"
tests:
  - name: "Create Order Item"
    module: "orders"
    request:
      method: "POST"
      url: "{{base_url}}/items"
      headers:
        Authorization: "Bearer {{api_key}}"
      body:
        sku: "{{sku}}"
        quantity: "{{quantity}}"
    assertions:
      - type: "status"
        equals: 201
""",
        encoding="utf-8",
    )

    captured_auth_headers: list[str] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        auth_hdr = request.headers.get("authorization", "")
        captured_auth_headers.append(auth_hdr)
        return httpx.Response(201, json={"order_id": 9999})

    original_init = RunService.__init__

    def custom_init(self, *args, **kwargs):
        kwargs["executor"] = RequestExecutor(
            transport=httpx.MockTransport(mock_handler)
        )
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(RunService, "__init__", custom_init)

    db_path = tmp_path / "orders.db"
    out_report = tmp_path / "reports" / "run_report.json"

    res = runner.invoke(
        app,
        [
            "run",
            str(suite_file),
            "--env-file",
            str(env_file),
            "--env",
            "demo",
            "--data-file",
            str(data_file),
            "--db",
            str(db_path),
            "--output",
            str(out_report),
            "--format",
            "json",
            "--allow-mutation",
        ],
    )

    assert res.exit_code == 0
    # Runtime execution received RAW secret
    assert len(captured_auth_headers) == 2
    assert all(h == "Bearer raw-secret-token-777" for h in captured_auth_headers)

    # Generated report and stdout must NOT leak raw secret
    report_content = out_report.read_text(encoding="utf-8")
    assert "raw-secret-token-777" not in report_content
    assert "raw-secret-token-777" not in res.stdout
    assert "***" in report_content


def test_cli_report_outputs_json_junit_html_and_returns_exit_code_0_or_3(
    tmp_path: Path,
) -> None:
    """Covers: FR-24, FR-26, FR-28, FR-30

    Verify that apiscope report exports JSON, JUnit, and HTML correctly and
    exits with 0 on success or 3 on missing runs.
    """
    db_path = tmp_path / "rep_cli.db"
    db_mgr = DatabaseManager(db_path)
    repo = RunRepository(db_mgr)

    t = datetime.now(UTC)
    run = RunResult(
        run_id="run-cli-001",
        environment_name="test",
        started_at=t,
        finished_at=t,
        duration_ms=50.0,
        total_tests=1,
        passed_tests=1,
        failed_tests=0,
        error_tests=0,
        skipped_tests=0,
        results=[
            CaseResult(
                test_name="Test One",
                module="mod",
                tags=[],
                status=CaseStatus.PASSED,
                verified=True,
                method="GET",
                url="https://api.example.com",
                status_code=200,
                elapsed_ms=50.0,
                request_headers={},
                response_headers={},
                assertion_results=[],
                extracted_variables={},
                extraction_errors=[],
            )
        ],
    )
    repo.save_run(run)
    db_mgr.close()

    # 1. Output JSON to stdout
    res_json = runner.invoke(
        app,
        ["report", "--db", str(db_path), "--format", "json"],
    )
    assert res_json.exit_code == 0
    parsed = json.loads(res_json.stdout)
    assert parsed["run_id"] == "run-cli-001"

    # 2. Output HTML to file
    out_html = tmp_path / "out.html"
    res_html = runner.invoke(
        app,
        [
            "report",
            "--db",
            str(db_path),
            "--format",
            "html",
            "--output",
            str(out_html),
        ],
    )
    assert res_html.exit_code == 0
    assert out_html.is_file()
    assert "<!DOCTYPE html>" in out_html.read_text(encoding="utf-8")

    # 3. Output JUnit XML to stdout
    res_junit = runner.invoke(
        app,
        ["report", "--db", str(db_path), "--format", "junit"],
    )
    assert res_junit.exit_code == 0
    assert "<testsuite" in res_junit.stdout

    # 4. Unknown run-id -> exit code 3
    res_err = runner.invoke(
        app,
        [
            "report",
            "--db",
            str(db_path),
            "--run-id",
            "unknown-run-id",
        ],
    )
    assert res_err.exit_code == 3


def test_cli_run_single_test_definition_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Covers: FR-24, FR-25

    Verify that apiscope run accepts single TestDefinition files with request field.
    """
    single_test = tmp_path / "single.yaml"
    single_test.write_text(
        """
name: "Single Test Case"
module: "single"
request:
  method: "GET"
  url: "https://api.example.com/item"
assertions:
  - type: "status"
    equals: 200
""",
        encoding="utf-8",
    )

    def mock_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    original_init = RunService.__init__

    def custom_init(self, *args, **kwargs):
        kwargs["executor"] = RequestExecutor(
            transport=httpx.MockTransport(mock_handler)
        )
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(RunService, "__init__", custom_init)

    res = runner.invoke(app, ["run", str(single_test), "--db", str(tmp_path / "s.db")])
    assert res.exit_code == 0
    assert "Passed: 1" in res.stdout


def test_cli_run_malformed_yaml_and_non_dict_and_missing_keys(
    tmp_path: Path,
) -> None:
    """Covers: FR-24, FR-26, NFR-02

    Verify that malformed YAML, non-mapping documents, or suites missing required keys
    produce exit code 3.
    """
    # 1. Malformed syntax
    bad_syntax = tmp_path / "syntax_error.yaml"
    bad_syntax.write_text("name: [unclosed list", encoding="utf-8")
    r1 = runner.invoke(app, ["run", str(bad_syntax)])
    assert r1.exit_code == 3

    # 2. Non-dict mapping
    non_dict = tmp_path / "scalar.yaml"
    non_dict.write_text("just a plain string", encoding="utf-8")
    r2 = runner.invoke(app, ["run", str(non_dict)])
    assert r2.exit_code == 3

    # 3. Neither 'tests' nor 'request'
    empty_mapping = tmp_path / "missing_keys.yaml"
    empty_mapping.write_text("name: 'invalid'\nmodule: 'invalid'", encoding="utf-8")
    r3 = runner.invoke(app, ["run", str(empty_mapping)])
    assert r3.exit_code == 3


def test_cli_env_file_mismatched_or_default_discovery(tmp_path: Path) -> None:
    """Covers: FR-24, FR-25, FR-26

    Verify that mismatched environment names or missing environment files
    produce exit code 3.
    """
    env_file = tmp_path / "my.env.yaml"
    env_file.write_text("name: staging\nvariables: {}\nsecrets: []", encoding="utf-8")

    suite_file = tmp_path / "suite.yaml"
    suite_file.write_text(
        "name: S\nmodule: M\ntests:\n"
        "  - name: T\n    module: M\n    request:\n"
        "      method: GET\n      url: 'https://api.example.com'",
        encoding="utf-8",
    )

    # Mismatched name
    res1 = runner.invoke(
        app,
        ["run", str(suite_file), "--env-file", str(env_file), "--env", "production"],
    )
    assert res1.exit_code == 3
    assert "Environment 'production' not found" in (res1.stdout + res1.stderr)

    # --env without --env-file when apiscope.env.yaml is missing
    res2 = runner.invoke(app, ["run", str(suite_file), "--env", "dev"])
    assert res2.exit_code == 3


def test_cli_version_and_main_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Covers: FR-24, NFR-06

    Verify that apiscope version displays version and main() triggers the Typer app.
    """
    res = runner.invoke(app, ["version"])
    assert res.exit_code == 0
    assert "ApiScope version" in res.stdout

    monkeypatch.setattr("sys.argv", ["apiscope", "version"])
    from apiscope.cli.main import main as cli_main

    with pytest.raises(SystemExit) as exc_info:
        cli_main()
    assert exc_info.value.code == 0
