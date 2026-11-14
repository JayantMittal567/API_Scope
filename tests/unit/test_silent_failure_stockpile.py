"""Unit tests for offline StockPile acceptance verification (Task 23).

Ensures authentic StockPile read-only results (both smoke and extended) are evaluated
purely offline with zero network calls, correctly report side-effect verification as
NOT_TESTED (read-only execution cannot prove DB persistence), and do not fabricate defects.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from apiscope.analysis.silent_failure_service import (
    SilentFailureService,
    SilentSignalType,
    VerificationState,
    load_run_file,
)


def test_stockpile_smoke_readonly_results_offline_evaluation() -> None:
    """Evaluate stockpile_readonly_results.json offline without network calls.

    Must report side-effect verification as NOT_TESTED and has_silent_failures=False.
    """
    path = Path("stockpile_tests/stockpile_readonly_results.json")
    assert path.is_file(), "StockPile readonly smoke results file must exist"

    # Block any socket / network calls to guarantee 100% offline execution
    with patch("socket.socket") as mock_socket:
        mock_socket.side_effect = RuntimeError(
            "Network access attempted during offline test!"
        )

        run_result = load_run_file(path)
        assert run_result.total_tests == 8
        assert all(c.method.upper() == "GET" for c in run_result.results)

        service = SilentFailureService()
        report = service.analyze(run_result=run_result)

        # Zero silent failures or defects fabricated
        assert report.has_silent_failures is False
        assert report.verified_mismatches_count == 0
        assert report.suspicious_count == 0

        # Side-effect persistence must be reported as NOT_TESTED
        s5_findings = [
            f
            for f in report.findings
            if f.signal_type == SilentSignalType.S5_UNVERIFIED_EFFECT
        ]
        assert len(s5_findings) == 1
        assert s5_findings[0].verification_state == VerificationState.NOT_TESTED
        assert "read-only execution" in s5_findings[0].description.lower()
        assert (
            "PASS: All tests succeeded (side-effects unverified / not tested)"
            in report.verdict_banner
        )


def test_stockpile_extended_readonly_results_offline_evaluation() -> None:
    """Evaluate stockpile_extended_results.json offline without network calls.

    Must report side-effect verification as NOT_TESTED and has_silent_failures=False.
    """
    path = Path("stockpile_tests/stockpile_extended_results.json")
    assert path.is_file(), "StockPile extended readonly results file must exist"

    with patch("socket.socket") as mock_socket:
        mock_socket.side_effect = RuntimeError(
            "Network access attempted during offline test!"
        )

        run_result = load_run_file(path)
        assert run_result.total_tests == 30
        assert all(c.method.upper() == "GET" for c in run_result.results)

        service = SilentFailureService()
        report = service.analyze(run_result=run_result)

        assert report.has_silent_failures is False
        assert report.verified_mismatches_count == 0
        assert report.suspicious_count == 0

        s5_findings = [
            f
            for f in report.findings
            if f.signal_type == SilentSignalType.S5_UNVERIFIED_EFFECT
        ]
        assert len(s5_findings) == 1
        assert s5_findings[0].verification_state == VerificationState.NOT_TESTED
        assert "not tested" in s5_findings[0].description.lower()
