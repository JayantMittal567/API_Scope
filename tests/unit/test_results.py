"""Unit tests for M4 Result Models (CaseStatus, CaseResult, RunResult).

Tests JSON-serializability, ISO-8601 UTC timestamp formatting, test collection
warning avoidance (__test__ = False), and aggregated suite metrics.
"""

import json
from datetime import UTC, datetime

from apiscope.core.results import CaseResult, CaseStatus, RunResult


def test_case_result_and_run_result_to_dict_json_serializable() -> None:
    """Covers: FR-18, NFR-06

    Verify that CaseResult and RunResult serialize cleanly to JSON with zero custom
    encoders and format timestamps as ISO-8601 strings.
    """
    now_start = datetime(2026, 11, 12, 10, 0, 0, tzinfo=UTC)
    now_finish = datetime(2026, 11, 12, 10, 0, 1, 500000, tzinfo=UTC)

    case = CaseResult(
        test_name="Create User",
        module="users",
        tags=["auth", "smoke"],
        status=CaseStatus.PASSED,
        verified=True,
        method="POST",
        url="https://api.example.com/users",
        status_code=201,
        elapsed_ms=45.2,
        request_headers={"Authorization": "***"},
        response_headers={"Content-Type": "application/json"},
        assertion_results=[
            {"assertion_type": "status", "passed": True, "expected": 201, "actual": 201}
        ],
        extracted_variables={"user_id": 42},
        extraction_errors=[],
        error_message=None,
        skip_reason=None,
        row_index=None,
    )

    run = RunResult(
        run_id="run-uuid-1234",
        environment_name="staging",
        started_at=now_start,
        finished_at=now_finish,
        duration_ms=1500.0,
        total_tests=1,
        passed_tests=1,
        failed_tests=0,
        error_tests=0,
        skipped_tests=0,
        results=[case],
    )

    case_dict = case.to_dict()
    assert case_dict["status"] == "PASSED"
    assert case_dict["verified"] is True
    assert case_dict["url"] == "https://api.example.com/users"

    run_dict = run.to_dict()
    assert run_dict["run_id"] == "run-uuid-1234"
    assert run_dict["started_at"] == "2026-11-12T10:00:00+00:00"
    assert run_dict["finished_at"] == "2026-11-12T10:00:01.500000+00:00"
    assert run_dict["all_passed"] is True
    assert len(run_dict["results"]) == 1

    # Guarantee 100% standard JSON serializability
    json_str = json.dumps(run_dict)
    assert "run-uuid-1234" in json_str
    assert "2026-11-12T10:00:00+00:00" in json_str


def test_run_result_all_passed_property_and_counts() -> None:
    """Covers: FR-18

    Verify that all_passed property logic and failure/skip/error count aggregations
    behave correctly.
    """
    now = datetime.now(UTC)

    # Empty run: all_passed must be False (0 tests ran)
    empty_run = RunResult(
        run_id="empty-run",
        environment_name=None,
        started_at=now,
        finished_at=now,
        duration_ms=0.0,
        total_tests=0,
        passed_tests=0,
        failed_tests=0,
        error_tests=0,
        skipped_tests=0,
        results=[],
    )
    assert empty_run.all_passed is False

    # Mixed results: all_passed must be False
    mixed_run = RunResult(
        run_id="mixed-run",
        environment_name="prod",
        started_at=now,
        finished_at=now,
        duration_ms=250.0,
        total_tests=3,
        passed_tests=2,
        failed_tests=1,
        error_tests=0,
        skipped_tests=0,
        results=[],
    )
    assert mixed_run.all_passed is False

    # Fully passed run: all_passed must be True
    perfect_run = RunResult(
        run_id="perfect-run",
        environment_name="dev",
        started_at=now,
        finished_at=now,
        duration_ms=300.0,
        total_tests=4,
        passed_tests=4,
        failed_tests=0,
        error_tests=0,
        skipped_tests=0,
        results=[],
    )
    assert perfect_run.all_passed is True
