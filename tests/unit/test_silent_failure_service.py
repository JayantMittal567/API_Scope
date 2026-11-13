"""Unit tests for SilentFailureService and silent failure signals S1-S4.

Covers: FR-39, FR-40, FR-41, FR-42, FR-43, FR-44, NFR-02, NFR-03, NFR-04, NFR-06.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from apiscope.analysis.silent_failure_service import (
    MAX_LOG_FILE_BYTES,
    MAX_LOG_LINES,
    SilentFailureService,
    SilentSignalType,
    load_app_log,
    parse_app_log,
)
from apiscope.core.exceptions import ConfigurationError, PathTraversalError
from apiscope.core.models import HttpResponse
from apiscope.core.results import CaseResult, CaseStatus, RunResult


def _make_case(
    name: str = "test",
    module: str = "orders",
    method: str = "POST",
    url: str = "http://localhost:8000/orders/101/sync-invoice",
    status: CaseStatus = CaseStatus.PASSED,
    status_code: int = 200,
    elapsed_ms: float = 20.0,
    response_headers: dict[str, str] | None = None,
    response_body: str = "",
) -> CaseResult:
    """Helper to construct a deterministic CaseResult."""
    return CaseResult(
        test_name=name,
        module=module,
        tags=["order-sync"],
        status=status,
        verified=True,
        method=method,
        url=url,
        status_code=status_code,
        elapsed_ms=elapsed_ms,
        request_headers={},
        response_headers=response_headers or {},
        response_body=response_body,
        assertion_results=[{"rule_type": "status", "passed": True}],
        extracted_variables={},
        extraction_errors=[],
    )


def test_signal_s1_correlates_app_log_errors_and_redacts_secrets() -> None:
    """Covers: FR-39, NFR-03.

    Parse sample_app/logs/app.log plus a log line with a secret Bearer token,
    verifying S1_LOG_ERROR is detected for [orders] InvoiceGatewayTimeout and
    secrets are redacted in evidence.
    """
    app_log_path = Path("sample_app/logs/app.log")
    assert app_log_path.exists(), "sample_app/logs/app.log must exist"

    sample_content = app_log_path.read_text(encoding="utf-8")
    loaded_direct = load_app_log(app_log_path)
    assert len(loaded_direct) == 8

    extra_line = (
        "2026-11-12T10:00:25Z ERROR [auth] Failed auth with Bearer super-secret-token\n"
    )
    combined = sample_content + "\n" + extra_line

    entries = parse_app_log(combined)
    assert len(entries) >= 3  # at least the 2 error lines from app.log + extra

    # Create run_result with matching time window to test time correlation
    fixed_start = datetime(2026, 11, 12, 10, 0, 0, tzinfo=UTC)
    fixed_end = datetime(2026, 11, 12, 10, 0, 30, tzinfo=UTC)
    timed_run = RunResult(
        run_id="run-timed-1",
        environment_name="test",
        started_at=fixed_start,
        finished_at=fixed_end,
        duration_ms=30000.0,
        total_tests=1,
        passed_tests=1,
        failed_tests=0,
        error_tests=0,
        skipped_tests=0,
        results=[],
    )

    service = SilentFailureService()
    report = service.analyze(app_log_entries=entries, run_result=timed_run)

    assert report.has_silent_failures is True
    assert report.counts_by_type[SilentSignalType.S1_LOG_ERROR.value] >= 3

    # Check that [orders] InvoiceGatewayTimeout (BUG-10) was caught
    orders_signals = [
        s
        for s in report.signals
        if s.module == "orders" and s.signal_type == SilentSignalType.S1_LOG_ERROR
    ]
    assert len(orders_signals) >= 1
    assert any("InvoiceGatewayTimeout" in s.evidence for s in orders_signals)

    # Check secret redaction
    auth_signals = [
        s
        for s in report.signals
        if s.module == "auth" and s.signal_type == SilentSignalType.S1_LOG_ERROR
    ]
    assert len(auth_signals) == 1
    assert "super-secret-token" not in auth_signals[0].evidence
    assert "***" in auth_signals[0].evidence

    # JSON lines and raw traceback parsing
    jsonl_text = (
        '{"timestamp": "2026-11-12T10:00:00Z", "level": "ERROR", '
        '"module": "orders", "message": "JSON error"}\n'
        "Traceback (most recent call last):\n"
        '  File "main.py", line 42, in <module>\n'
        "RuntimeError: Crash\n"
    )
    jsonl_entries = parse_app_log(jsonl_text)
    assert len(jsonl_entries) >= 2
    assert any(e.level == "ERROR" and e.module == "orders" for e in jsonl_entries)
    assert any(
        e.level == "ERROR" and "RuntimeError" in e.message for e in jsonl_entries
    )

    # Empty entries from candidate text raises ConfigurationError
    with pytest.raises(ConfigurationError, match="no valid log entries"):
        parse_app_log("Just some unparseable random text\nAnother invalid text line")


def test_signal_s2_detects_degraded_dependency_health_probe() -> None:
    """Covers: FR-40.

    Verify S2_DEPENDENCY_PROBE is emitted when a health probe returns
    a degraded status or degraded dependencies.
    """
    service = SilentFailureService()

    # 1. Dict payload matching sample_app / examples
    degraded_payload = {
        "status": "DEGRADED",
        "dependencies": {
            "database": "UP",
            "payment_gateway": "DOWN",
        },
    }
    report = service.analyze(health_probe_payload=degraded_payload)
    assert report.has_silent_failures is True
    assert report.counts_by_type[SilentSignalType.S2_DEPENDENCY_PROBE.value] >= 2
    s2_signals = [
        s
        for s in report.signals
        if s.signal_type == SilentSignalType.S2_DEPENDENCY_PROBE
    ]
    assert any("payment_gateway" in s.evidence for s in s2_signals)

    # 2. HttpResponse with 503 status code
    http_resp = HttpResponse(
        status_code=503,
        headers={"content-type": "application/json"},
        body='{"status": "DOWN", "error": "Service unavailable"}',
        elapsed_ms=10.0,
    )
    http_report = service.analyze(health_probe_payload=http_resp)
    assert http_report.has_silent_failures is True
    assert http_report.counts_by_type[SilentSignalType.S2_DEPENDENCY_PROBE.value] >= 1


def test_signal_s3_detects_latency_drift_against_baseline() -> None:
    """Covers: FR-41, BUG-08.

    Pass a RunResult where products p95 is 160.0 ms against baseline 50.0 ms,
    verifying S3_LATENCY_DRIFT is emitted.
    """
    fixed_time = datetime(2026, 11, 12, 12, 0, 0, tzinfo=UTC)
    # Generate cases with latencies around 150-160 ms
    cases = [
        _make_case(
            name=f"test_product_{i}",
            module="products",
            method="GET",
            url=f"/products/{i}",
            elapsed_ms=160.0,
        )
        for i in range(10)
    ]
    run_result = RunResult(
        run_id="run-latency-test",
        environment_name="test",
        started_at=fixed_time,
        finished_at=fixed_time,
        duration_ms=1600.0,
        total_tests=10,
        passed_tests=10,
        failed_tests=0,
        error_tests=0,
        skipped_tests=0,
        results=cases,
    )

    service = SilentFailureService(latency_tolerance_factor=1.5)
    # Baseline is 50.0 ms; 1.5x limit is 75.0 ms; 160.0 ms > 75.0 ms -> S3
    report = service.analyze(
        run_result=run_result,
        baseline_p95_by_module={"products": 50.0},
    )

    assert report.has_silent_failures is True
    assert report.counts_by_type[SilentSignalType.S3_LATENCY_DRIFT.value] == 1
    s3_signal = report.signals[0]
    assert s3_signal.signal_type == SilentSignalType.S3_LATENCY_DRIFT
    assert s3_signal.module == "products"
    assert "160.0 ms" in s3_signal.evidence
    assert "baseline 50.0 ms" in s3_signal.evidence


def test_signal_s4_detects_warning_headers_and_error_like_2xx_body() -> None:
    """Covers: FR-42, BUG-10.

    Pass a 200 OK response with X-ApiScope-Warning header and an error-like
    JSON body, verifying S4_SUSPICIOUS_2XX is emitted.
    """
    body_json = json.dumps(
        {
            "order_id": 101,
            "synced": False,
            "error": "Internal billing sync failed silently",
        }
    )
    case = _make_case(
        name="test_order_sync",
        module="orders",
        method="POST",
        url="http://localhost:8000/orders/101/sync-invoice",
        status_code=200,
        response_headers={
            "Content-Type": "application/json",
            "X-ApiScope-Warning": "Deprecated sync fallback triggered",
        },
        response_body=body_json,
    )

    fixed_time = datetime(2026, 11, 12, 12, 0, 0, tzinfo=UTC)
    run_result = RunResult(
        run_id="run-s4-test",
        environment_name="test",
        started_at=fixed_time,
        finished_at=fixed_time,
        duration_ms=50.0,
        total_tests=1,
        passed_tests=1,
        failed_tests=0,
        error_tests=0,
        skipped_tests=0,
        results=[case],
    )

    service = SilentFailureService()
    report = service.analyze(run_result=run_result)

    assert report.has_silent_failures is True
    assert report.counts_by_type[SilentSignalType.S4_SUSPICIOUS_2XX.value] >= 2

    s4_signals = [
        s for s in report.signals if s.signal_type == SilentSignalType.S4_SUSPICIOUS_2XX
    ]
    # Header signal
    assert any("X-ApiScope-Warning" in s.description for s in s4_signals)
    # Body signal
    assert any("error" in s.evidence or "synced" in s.evidence for s in s4_signals)

    # Test exception field, status: error, synced: False, and raw_responses
    resp_exception = HttpResponse(
        status_code=200,
        headers={"warning": "299 - Deprecated API"},
        body='{"exception": "PaymentTimeout", "status": "failed"}',
        elapsed_ms=15.0,
    )
    resp_synced_false = HttpResponse(
        status_code=200,
        headers={},
        body='{"synced": false}',
        elapsed_ms=10.0,
    )
    resp_status_error = HttpResponse(
        status_code=200,
        headers={},
        body='{"status": "error", "message": "Failed silently"}',
        elapsed_ms=10.0,
    )
    report_raw = service.analyze(
        raw_responses=[
            ("orders", "POST /orders/sync", resp_exception),
            ("orders", "POST /orders/flag", resp_synced_false),
            ("orders", "POST /orders/status", resp_status_error),
        ]
    )
    assert report_raw.has_silent_failures is True
    assert report_raw.counts_by_type[SilentSignalType.S4_SUSPICIOUS_2XX.value] >= 4


def test_silent_failure_report_verdict_banner_and_to_dict() -> None:
    """Covers: FR-43, FR-44, NFR-06.

    Verify verdict_banner matches canonical specification and to_dict() contract.
    """
    service = SilentFailureService()

    # Empty report -> no silent failures
    clean_report = service.analyze()
    assert clean_report.has_silent_failures is False
    assert clean_report.verdict_banner == "NO SILENT FAILURES DETECTED"
    assert clean_report.total_signals == 0

    clean_dict = clean_report.to_dict()
    assert clean_dict["has_silent_failures"] is False
    assert set(clean_dict["counts_by_type"].keys()) == {
        "S1_LOG_ERROR",
        "S2_DEPENDENCY_PROBE",
        "S3_LATENCY_DRIFT",
        "S4_SUSPICIOUS_2XX",
    }

    # Report with signal
    degraded_payload = {"status": "DEGRADED"}
    flagged_report = service.analyze(health_probe_payload=degraded_payload)
    assert flagged_report.has_silent_failures is True
    assert (
        flagged_report.verdict_banner
        == "PASS WITH WARNINGS: APIs healthy, internal issue suspected"
    )

    flagged_dict = flagged_report.to_dict()
    assert flagged_dict["has_silent_failures"] is True
    assert len(flagged_dict["signals"]) > 0
    serialized = json.dumps(flagged_dict)
    assert len(serialized) > 0

    # filter_by_module
    filtered_signals = flagged_report.filter_by_module("health")
    assert len(filtered_signals) > 0
    empty_filtered = flagged_report.filter_by_module("nonexistent")
    assert len(empty_filtered) == 0


def test_load_app_log_enforces_safe_path_and_size_limits(tmp_path: Path) -> None:
    """Covers: FR-39, NFR-02, NFR-04.

    Verify PathTraversalError, missing file, and file size / line count limits.
    """
    base_dir = tmp_path / "safe_dir"
    base_dir.mkdir()

    # 1. Path traversal
    with pytest.raises(PathTraversalError):
        load_app_log("../unsafe.log", base_dir=base_dir)

    # 2. Missing file
    missing = base_dir / "missing.log"
    with pytest.raises(ConfigurationError, match="not found"):
        load_app_log(missing, base_dir=base_dir)

    # 3. File size exceeds MAX_LOG_FILE_BYTES
    oversized = base_dir / "large.log"
    oversized.write_text("dummy", encoding="utf-8")
    real_stat = oversized.stat()
    with patch("pathlib.Path.stat") as mock_stat:
        mock_result = MagicMock()
        mock_result.st_size = MAX_LOG_FILE_BYTES + 1
        mock_result.st_mode = real_stat.st_mode
        mock_stat.return_value = mock_result
        with pytest.raises(ConfigurationError, match="maximum size"):
            load_app_log(oversized, base_dir=base_dir)

    # 4. Line count exceeds MAX_LOG_LINES
    huge_lines = "\n".join(
        ["2026-11-12T10:00:00Z INFO [server] log message"] * (MAX_LOG_LINES + 5)
    )
    with pytest.raises(ConfigurationError, match="exceeds maximum"):
        parse_app_log(huge_lines)
