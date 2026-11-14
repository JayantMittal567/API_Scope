"""SQLite repositories and trend analysis providers for ApiScope.

Persists test run outcomes, per-case execution telemetry, and assertion details
using raw SQLite transactions, deterministic connection management, and Windows
filesystem-safe connection closure.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import UTC, datetime
from typing import Any

from apiscope.core.exceptions import ConfigurationError
from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.data.connection import DatabaseManager
from apiscope.reports.models import TrendPoint, TrendSummary

MAX_HISTORY_LIMIT: int = 500


def initialize_schema(conn: sqlite3.Connection) -> None:
    """Create ApiScope database tables and indexes if they do not exist.

    Args:
        conn: Open SQLite connection.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            environment_name TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            duration_ms REAL NOT NULL,
            total_tests INTEGER NOT NULL,
            passed_tests INTEGER NOT NULL,
            failed_tests INTEGER NOT NULL,
            error_tests INTEGER NOT NULL,
            skipped_tests INTEGER NOT NULL,
            has_warnings INTEGER NOT NULL DEFAULT 0
        );
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS case_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            case_order INTEGER NOT NULL,
            test_name TEXT NOT NULL,
            module TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            status TEXT NOT NULL,
            verified INTEGER NOT NULL,
            method TEXT NOT NULL,
            url TEXT NOT NULL,
            status_code INTEGER,
            elapsed_ms REAL,
            request_headers_json TEXT NOT NULL,
            response_headers_json TEXT NOT NULL,
            extracted_variables_json TEXT NOT NULL,
            extraction_errors_json TEXT NOT NULL,
            error_message TEXT,
            skip_reason TEXT,
            row_index INTEGER,
            response_body TEXT NOT NULL DEFAULT '',
            correlation_id TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (run_id) REFERENCES runs (run_id) ON DELETE CASCADE
        );
        """
    )

    try:
        col_rows = conn.execute("PRAGMA table_info(case_results)").fetchall()
        col_names = [r[1] for r in col_rows]
        if "response_body" not in col_names:
            conn.execute(
                "ALTER TABLE case_results ADD COLUMN "
                "response_body TEXT NOT NULL DEFAULT ''"
            )
        if "correlation_id" not in col_names:
            conn.execute(
                "ALTER TABLE case_results ADD COLUMN "
                "correlation_id TEXT NOT NULL DEFAULT ''"
            )
    except Exception:
        pass

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assertion_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_result_id INTEGER NOT NULL,
            assertion_order INTEGER NOT NULL,
            rule_type TEXT NOT NULL,
            passed INTEGER NOT NULL,
            expected_json TEXT,
            actual_json TEXT,
            message TEXT NOT NULL,
            FOREIGN KEY (case_result_id) REFERENCES case_results (id) ON DELETE CASCADE
        );
        """
    )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_case_results_run_id ON case_results (run_id);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_case_results_module ON case_results (module);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assertion_results_case_id "
        "ON assertion_results (case_result_id);"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs (started_at);")


class RunRepository:
    """Repository managing persistence and retrieval of test runs in SQLite."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        """Initialize RunRepository and verify schema existence.

        Args:
            db_manager: DatabaseManager handling SQLite connections.
        """
        self.db_manager = db_manager
        with self.db_manager.connection() as conn:
            initialize_schema(conn)

    def save_run(self, run_result: RunResult, has_warnings: bool = False) -> str:
        """Persist a RunResult, its cases, and assertion results atomically.

        Args:
            run_result: The aggregated test run execution result.
            has_warnings: Whether the run produced non-fatal warnings.

        Returns:
            The persisted run_id string.
        """
        started_at_str = (
            run_result.started_at.isoformat()
            if isinstance(run_result.started_at, datetime)
            else str(run_result.started_at)
        )
        finished_at_str = (
            run_result.finished_at.isoformat()
            if isinstance(run_result.finished_at, datetime)
            else str(run_result.finished_at)
        )

        with self.db_manager.connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs (
                    run_id, environment_name, started_at, finished_at,
                    duration_ms, total_tests, passed_tests, failed_tests,
                    error_tests, skipped_tests, has_warnings
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_result.run_id,
                    run_result.environment_name,
                    started_at_str,
                    finished_at_str,
                    run_result.duration_ms,
                    run_result.total_tests,
                    run_result.passed_tests,
                    run_result.failed_tests,
                    run_result.error_tests,
                    run_result.skipped_tests,
                    1 if has_warnings else 0,
                ),
            )

            for case_order, case in enumerate(run_result.results):
                cursor = conn.execute(
                    """
                    INSERT INTO case_results (
                        run_id, case_order, test_name, module, tags_json,
                        status, verified, method, url, status_code,
                        elapsed_ms, request_headers_json, response_headers_json,
                        extracted_variables_json, extraction_errors_json,
                        error_message, skip_reason, row_index, response_body,
                        correlation_id
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        run_result.run_id,
                        case_order,
                        case.test_name,
                        case.module,
                        json.dumps(list(case.tags)),
                        (
                            case.status.value
                            if hasattr(case.status, "value")
                            else str(case.status)
                        ),
                        1 if case.verified else 0,
                        case.method,
                        case.url,
                        case.status_code,
                        case.elapsed_ms,
                        json.dumps(dict(case.request_headers)),
                        json.dumps(dict(case.response_headers)),
                        json.dumps(dict(case.extracted_variables)),
                        json.dumps(list(case.extraction_errors)),
                        case.error_message,
                        case.skip_reason,
                        case.row_index,
                        getattr(case, "response_body", "") or "",
                        getattr(case, "correlation_id", "") or "",
                    ),
                )
                case_id = cursor.lastrowid

                for assertion_order, assertion in enumerate(case.assertion_results):
                    rule_type = assertion.get("rule_type") or assertion.get(
                        "assertion_type", "unknown"
                    )
                    passed = 1 if assertion.get("passed") else 0
                    expected_json = json.dumps(assertion.get("expected"))
                    actual_json = json.dumps(assertion.get("actual"))
                    message = str(assertion.get("message", ""))
                    conn.execute(
                        """
                        INSERT INTO assertion_results (
                            case_result_id, assertion_order, rule_type,
                            passed, expected_json, actual_json, message
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            case_id,
                            assertion_order,
                            rule_type,
                            passed,
                            expected_json,
                            actual_json,
                            message,
                        ),
                    )

        return run_result.run_id

    def get_run(self, run_id: str) -> RunResult | None:
        """Retrieve a complete RunResult by unique run_id.

        Args:
            run_id: Unique test run identifier.

        Returns:
            Reconstructed RunResult if found, None otherwise.
        """
        with self.db_manager.connection() as conn:
            run_row = conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if not run_row:
                return None

            case_rows = conn.execute(
                "SELECT * FROM case_results WHERE run_id = ? ORDER BY case_order ASC",
                (run_id,),
            ).fetchall()

            case_results: list[CaseResult] = []
            for c_row in case_rows:
                case_id = c_row["id"]
                a_rows = conn.execute(
                    "SELECT * FROM assertion_results WHERE case_result_id = ? "
                    "ORDER BY assertion_order ASC",
                    (case_id,),
                ).fetchall()

                assertion_dicts: list[dict[str, Any]] = []
                for a_row in a_rows:
                    try:
                        expected_val = json.loads(a_row["expected_json"])
                    except Exception:
                        expected_val = a_row["expected_json"]
                    try:
                        actual_val = json.loads(a_row["actual_json"])
                    except Exception:
                        actual_val = a_row["actual_json"]

                    assertion_dicts.append(
                        {
                            "rule_type": a_row["rule_type"],
                            "assertion_type": a_row["rule_type"],
                            "passed": bool(a_row["passed"]),
                            "expected": expected_val,
                            "actual": actual_val,
                            "message": a_row["message"],
                        }
                    )

                case_results.append(
                    CaseResult(
                        test_name=c_row["test_name"],
                        module=c_row["module"],
                        tags=json.loads(c_row["tags_json"]),
                        status=CaseStatus(c_row["status"]),
                        verified=bool(c_row["verified"]),
                        method=c_row["method"],
                        url=c_row["url"],
                        status_code=c_row["status_code"],
                        elapsed_ms=c_row["elapsed_ms"],
                        request_headers=json.loads(c_row["request_headers_json"]),
                        response_headers=json.loads(c_row["response_headers_json"]),
                        assertion_results=assertion_dicts,
                        extracted_variables=json.loads(
                            c_row["extracted_variables_json"]
                        ),
                        extraction_errors=json.loads(c_row["extraction_errors_json"]),
                        error_message=c_row["error_message"],
                        skip_reason=c_row["skip_reason"],
                        row_index=c_row["row_index"],
                        response_body=dict(c_row).get("response_body", ""),
                        correlation_id=dict(c_row).get("correlation_id", ""),
                    )
                )

        started_at = datetime.fromisoformat(run_row["started_at"])
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)

        finished_at = datetime.fromisoformat(run_row["finished_at"])
        if finished_at.tzinfo is None:
            finished_at = finished_at.replace(tzinfo=UTC)

        blocked_count = sum(1 for c in case_results if c.status == CaseStatus.BLOCKED)
        has_warnings = bool(dict(run_row).get("has_warnings", 0))

        return RunResult(
            run_id=run_row["run_id"],
            environment_name=run_row["environment_name"],
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=run_row["duration_ms"],
            total_tests=run_row["total_tests"],
            passed_tests=run_row["passed_tests"],
            failed_tests=run_row["failed_tests"],
            error_tests=run_row["error_tests"],
            skipped_tests=run_row["skipped_tests"],
            has_warnings=has_warnings,
            blocked_tests=blocked_count,
            results=case_results,
        )

    def get_latest_run(self) -> RunResult | None:
        """Retrieve the most recent RunResult.

        Returns:
            The most recent RunResult if any exists, None otherwise.
        """
        with self.db_manager.connection() as conn:
            row = conn.execute(
                "SELECT run_id FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            run_id = row["run_id"]
        return self.get_run(run_id)

    def list_runs(
        self, limit: int = 50, module: str | None = None
    ) -> list[dict[str, Any]]:
        """List summary dictionaries of historical test runs.

        Args:
            limit: Maximum number of runs to return (1 to 500).
            module: Optional module name filter.

        Returns:
            List of run summary dictionaries ordered newest first.

        Raises:
            ConfigurationError: If limit is less than 1 or exceeds MAX_HISTORY_LIMIT.
        """
        if limit <= 0 or limit > MAX_HISTORY_LIMIT:
            raise ConfigurationError(
                f"History limit must be between 1 and {MAX_HISTORY_LIMIT}"
            )

        with self.db_manager.connection() as conn:
            if module is not None and module.strip():
                rows = conn.execute(
                    """
                    SELECT DISTINCT r.run_id, r.environment_name, r.started_at,
                           r.finished_at, r.duration_ms, r.total_tests,
                           r.passed_tests, r.failed_tests, r.error_tests,
                           r.skipped_tests, r.has_warnings
                    FROM runs r
                    JOIN case_results c ON r.run_id = c.run_id
                    WHERE LOWER(c.module) = LOWER(?)
                    ORDER BY r.started_at DESC, r.rowid DESC
                    LIMIT ?
                    """,
                    (module.strip(), limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT run_id, environment_name, started_at, finished_at,
                           duration_ms, total_tests, passed_tests, failed_tests,
                           error_tests, skipped_tests, has_warnings
                    FROM runs
                    ORDER BY started_at DESC, rowid DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

        return [
            {
                "run_id": r["run_id"],
                "environment_name": r["environment_name"],
                "started_at": r["started_at"],
                "finished_at": r["finished_at"],
                "duration_ms": r["duration_ms"],
                "total_tests": r["total_tests"],
                "passed_tests": r["passed_tests"],
                "failed_tests": r["failed_tests"],
                "error_tests": r["error_tests"],
                "skipped_tests": r["skipped_tests"],
                "has_warnings": bool(r["has_warnings"]),
            }
            for r in rows
        ]

    def get_trends(self, limit: int = 20, module: str | None = None) -> TrendSummary:
        """Calculate historical trend metrics ordered chronologically.

        Args:
            limit: Maximum number of recent runs to evaluate (1 to 500).
            module: Optional module name to filter metrics.

        Returns:
            TrendSummary with chronological TrendPoints.
        """
        if limit <= 0 or limit > MAX_HISTORY_LIMIT:
            raise ConfigurationError(
                f"History limit must be between 1 and {MAX_HISTORY_LIMIT}"
            )

        recent_runs = self.list_runs(limit=limit, module=module)
        # Sort chronologically (oldest to newest)
        chrono_runs = list(reversed(recent_runs))

        points: list[TrendPoint] = []
        all_latencies: list[float] = []

        with self.db_manager.connection() as conn:
            for r in chrono_runs:
                run_id = r["run_id"]
                if module is not None and module.strip():
                    case_rows = conn.execute(
                        "SELECT status, elapsed_ms FROM case_results "
                        "WHERE run_id = ? AND LOWER(module) = LOWER(?)",
                        (run_id, module.strip()),
                    ).fetchall()
                else:
                    case_rows = conn.execute(
                        "SELECT status, elapsed_ms FROM case_results WHERE run_id = ?",
                        (run_id,),
                    ).fetchall()

                total = len(case_rows)
                passed = sum(1 for c in case_rows if c["status"] == "PASSED")
                latencies = [
                    float(c["elapsed_ms"])
                    for c in case_rows
                    if c["elapsed_ms"] is not None
                ]

                pass_rate = round((passed / total) * 100.0, 2) if total > 0 else 0.0

                if latencies:
                    avg_lat = round(sum(latencies) / len(latencies), 2)
                    sorted_lat = sorted(latencies)
                    p95_idx = int(math.ceil(0.95 * len(sorted_lat))) - 1
                    p95_idx = max(0, min(p95_idx, len(sorted_lat) - 1))
                    p95_lat = round(sorted_lat[p95_idx], 2)
                    all_latencies.extend(latencies)
                else:
                    avg_lat = 0.0
                    p95_lat = 0.0

                points.append(
                    TrendPoint(
                        run_id=run_id,
                        started_at=r["started_at"],
                        total_tests=total,
                        passed_tests=passed,
                        pass_rate_pct=pass_rate,
                        avg_elapsed_ms=avg_lat,
                        p95_elapsed_ms=p95_lat,
                    )
                )

        total_tests_sum = sum(p.total_tests for p in points)
        passed_tests_sum = sum(p.passed_tests for p in points)
        overall_pass_rate = (
            round((passed_tests_sum / total_tests_sum) * 100.0, 2)
            if total_tests_sum > 0
            else 0.0
        )
        overall_avg_lat = (
            round(sum(all_latencies) / len(all_latencies), 2) if all_latencies else 0.0
        )

        return TrendSummary(
            module=module.strip() if module and module.strip() else None,
            run_count=len(points),
            points=points,
            overall_pass_rate_pct=overall_pass_rate,
            overall_avg_elapsed_ms=overall_avg_lat,
        )
