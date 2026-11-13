"""Unit tests for SQLite RunRepository and historical trend calculations.

Verifies persistence roundtrip, schema creation, pagination, module filtering,
percentile latency computations, and Windows filesystem connection closing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from apiscope.core.exceptions import ConfigurationError
from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.data.connection import DatabaseManager
from apiscope.data.repositories import MAX_HISTORY_LIMIT, RunRepository


def _make_dummy_case(
    name: str,
    module: str,
    status: CaseStatus,
    status_code: int | None = 200,
    elapsed_ms: float | None = 50.0,
    assertions: list[dict] | None = None,
    error_message: str | None = None,
    skip_reason: str | None = None,
) -> CaseResult:
    return CaseResult(
        test_name=name,
        module=module,
        tags=["unit", "test"],
        status=status,
        verified=status == CaseStatus.PASSED,
        method="GET",
        url="https://api.example.com/test",
        status_code=status_code,
        elapsed_ms=elapsed_ms,
        request_headers={"Accept": "application/json"},
        response_headers={"Content-Type": "application/json"},
        assertion_results=assertions or [],
        extracted_variables={},
        extraction_errors=[],
        error_message=error_message,
        skip_reason=skip_reason,
        row_index=None,
    )


def test_save_and_get_run_roundtrip_preserves_cases_and_assertions(
    tmp_path: Path,
) -> None:
    """Covers: FR-27, NFR-06

    Verify that a complete RunResult with passed, failed, errored, and skipped
    cases roundtrips through SQLite without data loss or ordering alteration.
    """
    db_path = tmp_path / "history.db"
    db_mgr = DatabaseManager(db_path)
    repo = RunRepository(db_mgr)

    t0 = datetime(2026, 11, 12, 10, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 11, 12, 10, 0, 5, tzinfo=UTC)

    case1 = _make_dummy_case(
        name="Case 1 Pass",
        module="auth",
        status=CaseStatus.PASSED,
        status_code=200,
        elapsed_ms=45.2,
        assertions=[
            {
                "rule_type": "status",
                "assertion_type": "status",
                "passed": True,
                "expected": 200,
                "actual": 200,
                "message": "Status OK",
            }
        ],
    )
    case2 = _make_dummy_case(
        name="Case 2 Fail",
        module="auth",
        status=CaseStatus.FAILED,
        status_code=500,
        elapsed_ms=120.5,
        assertions=[
            {
                "rule_type": "status",
                "assertion_type": "status",
                "passed": False,
                "expected": 200,
                "actual": 500,
                "message": "Expected 200 got 500",
            }
        ],
    )
    case3 = _make_dummy_case(
        name="Case 3 Error",
        module="users",
        status=CaseStatus.ERROR,
        status_code=None,
        elapsed_ms=None,
        error_message="Network transport timeout",
    )
    case4 = _make_dummy_case(
        name="Case 4 Skip",
        module="users",
        status=CaseStatus.SKIPPED,
        status_code=None,
        elapsed_ms=None,
        skip_reason="Upstream auth token missing",
    )

    run = RunResult(
        run_id="run-uuid-001",
        environment_name="staging",
        started_at=t0,
        finished_at=t1,
        duration_ms=5000.0,
        total_tests=4,
        passed_tests=1,
        failed_tests=1,
        error_tests=1,
        skipped_tests=1,
        results=[case1, case2, case3, case4],
    )

    saved_id = repo.save_run(run, has_warnings=False)
    assert saved_id == "run-uuid-001"

    # Retrieve by explicit ID
    fetched = repo.get_run("run-uuid-001")
    assert fetched is not None
    assert fetched.run_id == "run-uuid-001"
    assert fetched.environment_name == "staging"
    assert fetched.started_at == t0
    assert fetched.finished_at == t1
    assert fetched.total_tests == 4
    assert fetched.passed_tests == 1
    assert fetched.failed_tests == 1
    assert fetched.error_tests == 1
    assert fetched.skipped_tests == 1
    assert len(fetched.results) == 4

    # Verify case order and contents
    assert fetched.results[0].test_name == "Case 1 Pass"
    assert fetched.results[0].status == CaseStatus.PASSED
    assert len(fetched.results[0].assertion_results) == 1
    assert fetched.results[0].assertion_results[0]["passed"] is True

    assert fetched.results[1].test_name == "Case 2 Fail"
    assert fetched.results[1].status == CaseStatus.FAILED
    assert fetched.results[1].assertion_results[0]["actual"] == 500

    assert fetched.results[2].test_name == "Case 3 Error"
    assert fetched.results[2].status == CaseStatus.ERROR
    assert fetched.results[2].error_message == "Network transport timeout"

    assert fetched.results[3].test_name == "Case 4 Skip"
    assert fetched.results[3].status == CaseStatus.SKIPPED
    assert fetched.results[3].skip_reason == "Upstream auth token missing"

    # Retrieve latest
    latest = repo.get_latest_run()
    assert latest is not None
    assert latest.run_id == "run-uuid-001"

    db_mgr.close()


def test_get_run_unknown_id_returns_none(tmp_path: Path) -> None:
    """Covers: FR-27

    Verify that querying a non-existent run ID or empty repository returns None.
    """
    db_mgr = DatabaseManager(tmp_path / "empty.db")
    repo = RunRepository(db_mgr)

    assert repo.get_run("does-not-exist") is None
    assert repo.get_latest_run() is None

    db_mgr.close()


def test_list_runs_pagination_and_module_filter(tmp_path: Path) -> None:
    """Covers: FR-27, FR-29

    Verify listing runs with pagination and module filtering.
    """
    db_mgr = DatabaseManager(tmp_path / "list.db")
    repo = RunRepository(db_mgr)

    t0 = datetime(2026, 11, 12, 11, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 11, 12, 12, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 11, 12, 13, 0, 0, tzinfo=UTC)

    run1 = RunResult(
        run_id="run-1",
        environment_name="dev",
        started_at=t0,
        finished_at=t0,
        duration_ms=100.0,
        total_tests=1,
        passed_tests=1,
        failed_tests=0,
        error_tests=0,
        skipped_tests=0,
        results=[_make_dummy_case("c1", "auth", CaseStatus.PASSED)],
    )
    run2 = RunResult(
        run_id="run-2",
        environment_name="dev",
        started_at=t1,
        finished_at=t1,
        duration_ms=200.0,
        total_tests=1,
        passed_tests=1,
        failed_tests=0,
        error_tests=0,
        skipped_tests=0,
        results=[_make_dummy_case("c2", "billing", CaseStatus.PASSED)],
    )
    run3 = RunResult(
        run_id="run-3",
        environment_name="dev",
        started_at=t2,
        finished_at=t2,
        duration_ms=300.0,
        total_tests=1,
        passed_tests=1,
        failed_tests=0,
        error_tests=0,
        skipped_tests=0,
        results=[_make_dummy_case("c3", "auth", CaseStatus.PASSED)],
    )

    repo.save_run(run1)
    repo.save_run(run2)
    repo.save_run(run3)

    # All runs limited to 2 (newest first)
    paged = repo.list_runs(limit=2)
    assert len(paged) == 2
    assert paged[0]["run_id"] == "run-3"
    assert paged[1]["run_id"] == "run-2"

    # Module filtered to auth
    auth_runs = repo.list_runs(limit=50, module="auth")
    assert len(auth_runs) == 2
    assert {r["run_id"] for r in auth_runs} == {"run-3", "run-1"}

    # Module filtered to billing
    billing_runs = repo.list_runs(limit=50, module="billing")
    assert len(billing_runs) == 1
    assert billing_runs[0]["run_id"] == "run-2"

    # Non-existent module
    empty_runs = repo.list_runs(limit=50, module="nonexistent")
    assert len(empty_runs) == 0

    db_mgr.close()


def test_list_runs_invalid_limit_raises_configuration_error(
    tmp_path: Path,
) -> None:
    """Covers: FR-27, NFR-02

    Verify that non-positive limits or limits exceeding MAX_HISTORY_LIMIT
    raise ConfigurationError.
    """
    db_mgr = DatabaseManager(tmp_path / "limits.db")
    repo = RunRepository(db_mgr)

    with pytest.raises(ConfigurationError, match="History limit must be between 1 and"):
        repo.list_runs(limit=0)

    with pytest.raises(ConfigurationError, match="History limit must be between 1 and"):
        repo.list_runs(limit=-5)

    with pytest.raises(ConfigurationError, match="History limit must be between 1 and"):
        repo.list_runs(limit=MAX_HISTORY_LIMIT + 1)

    db_mgr.close()


def test_get_trends_computes_pass_rate_avg_and_p95_latency(
    tmp_path: Path,
) -> None:
    """Covers: FR-29, NFR-06

    Verify that historical trends correctly calculate pass rates and average/p95
    latencies in chronological order.
    """
    db_mgr = DatabaseManager(tmp_path / "trends.db")
    repo = RunRepository(db_mgr)

    t0 = datetime(2026, 11, 12, 10, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 11, 12, 11, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 11, 12, 12, 0, 0, tzinfo=UTC)

    # Run 1: 1 passed (10ms), 1 failed (20ms) -> 50% pass rate, avg 15ms, p95 20ms
    r1 = RunResult(
        run_id="run-1",
        environment_name="dev",
        started_at=t0,
        finished_at=t0,
        duration_ms=30.0,
        total_tests=2,
        passed_tests=1,
        failed_tests=1,
        error_tests=0,
        skipped_tests=0,
        results=[
            _make_dummy_case("c1", "orders", CaseStatus.PASSED, elapsed_ms=10.0),
            _make_dummy_case("c2", "orders", CaseStatus.FAILED, elapsed_ms=20.0),
        ],
    )

    # Run 2: 2 passed (30ms, 40ms) -> 100% pass rate, avg 35ms, p95 40ms
    r2 = RunResult(
        run_id="run-2",
        environment_name="dev",
        started_at=t1,
        finished_at=t1,
        duration_ms=70.0,
        total_tests=2,
        passed_tests=2,
        failed_tests=0,
        error_tests=0,
        skipped_tests=0,
        results=[
            _make_dummy_case("c3", "orders", CaseStatus.PASSED, elapsed_ms=30.0),
            _make_dummy_case("c4", "orders", CaseStatus.PASSED, elapsed_ms=40.0),
        ],
    )

    # Run 3: 1 passed (50ms) -> 100% pass rate
    r3 = RunResult(
        run_id="run-3",
        environment_name="dev",
        started_at=t2,
        finished_at=t2,
        duration_ms=50.0,
        total_tests=1,
        passed_tests=1,
        failed_tests=0,
        error_tests=0,
        skipped_tests=0,
        results=[
            _make_dummy_case("c5", "orders", CaseStatus.PASSED, elapsed_ms=50.0),
        ],
    )

    repo.save_run(r1)
    repo.save_run(r2)
    repo.save_run(r3)

    trends = repo.get_trends(limit=10)
    assert trends.run_count == 3
    assert len(trends.points) == 3

    # Verify chronological ordering (run-1 -> run-2 -> run-3)
    assert trends.points[0].run_id == "run-1"
    assert trends.points[0].pass_rate_pct == 50.0
    assert trends.points[0].avg_elapsed_ms == 15.0
    assert trends.points[0].p95_elapsed_ms == 20.0

    assert trends.points[1].run_id == "run-2"
    assert trends.points[1].pass_rate_pct == 100.0
    assert trends.points[1].avg_elapsed_ms == 35.0
    assert trends.points[1].p95_elapsed_ms == 40.0

    assert trends.points[2].run_id == "run-3"
    assert trends.points[2].pass_rate_pct == 100.0
    assert trends.points[2].avg_elapsed_ms == 50.0

    # Overall metrics: total=5, passed=4 -> 80% pass rate
    assert trends.overall_pass_rate_pct == 80.0
    # Latencies: 10, 20, 30, 40, 50 -> avg 30.0
    assert trends.overall_avg_elapsed_ms == 30.0

    db_mgr.close()


def test_sqlite_connection_closes_cleanly_on_windows_tmp_path(
    tmp_path: Path,
) -> None:
    """Covers: FR-27, NFR-02

    Verify that closing the DatabaseManager permits immediate file deletion on
    Windows without PermissionError or file lock violations.
    """
    db_file = tmp_path / "closable.db"
    db_mgr = DatabaseManager(db_file)
    repo = RunRepository(db_mgr)

    t = datetime.now(UTC)
    run = RunResult(
        run_id="run-lock-test",
        environment_name="test",
        started_at=t,
        finished_at=t,
        duration_ms=10.0,
        total_tests=1,
        passed_tests=1,
        failed_tests=0,
        error_tests=0,
        skipped_tests=0,
        results=[_make_dummy_case("c", "mod", CaseStatus.PASSED)],
    )
    repo.save_run(run)

    # Explicitly close database manager
    db_mgr.close()

    # Deleting db_file on Windows must succeed without WinError 32
    db_file.unlink()
    assert not db_file.exists()


def test_database_manager_context_and_transaction_rollback(
    tmp_path: Path,
) -> None:
    """Covers: FR-27, NFR-02

    Verify that DatabaseManager functions as a context manager, tracks connections,
    and rolls back uncommitted changes upon exceptions.
    """
    db_file = tmp_path / "rollback.db"
    with DatabaseManager(db_file) as db_mgr:
        conn = db_mgr.get_connection()
        conn.execute("CREATE TABLE test_data (id INT PRIMARY KEY, name TEXT);")
        conn.execute("INSERT INTO test_data VALUES (1, 'alice');")
        conn.commit()

        # Transaction rollback on exception
        with pytest.raises(RuntimeError), db_mgr.connection() as tx_conn:
            tx_conn.execute("INSERT INTO test_data VALUES (2, 'bob');")
            raise RuntimeError("Forced abort")

        rows = conn.execute("SELECT * FROM test_data;").fetchall()
        assert len(rows) == 1
        assert rows[0]["name"] == "alice"
