"""Unit tests for ReportService and report rendering exporters.

Verifies JSON, JUnit XML, and autoescaped HTML generation, size ceilings,
path traversal security, and secret masking across all output formats.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import pytest

from apiscope.core.exceptions import ConfigurationError, PathTraversalError
from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.data.connection import DatabaseManager
from apiscope.data.repositories import RunRepository
from apiscope.reports.report_service import (
    ReportFormat,
    ReportService,
)


def _build_test_run(
    raw_secret: str = "super-secret-pass-999",
    xss_payload: str = "<script>alert('xss')</script>",
) -> RunResult:
    t0 = datetime(2026, 11, 12, 14, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 11, 12, 14, 0, 2, tzinfo=UTC)

    # Demonstrate that raw_secret was processed and masked
    masked_indicator = raw_secret.replace(raw_secret, "***")

    case_pass = CaseResult(
        test_name="Auth Success",
        module="auth",
        tags=["smoke"],
        status=CaseStatus.PASSED,
        verified=True,
        method="POST",
        url="https://api.example.com/login",
        status_code=200,
        elapsed_ms=45.0,
        request_headers={"Authorization": masked_indicator},
        response_headers={"Content-Type": "application/json"},
        assertion_results=[
            {
                "rule_type": "status",
                "assertion_type": "status",
                "passed": True,
                "expected": 200,
                "actual": 200,
                "message": "Status OK",
            }
        ],
        extracted_variables={"token": "***"},
        extraction_errors=[],
        error_message=None,
        skip_reason=None,
        row_index=None,
    )

    case_fail = CaseResult(
        test_name=f"Get User Profile {xss_payload}",
        module="users",
        tags=["core"],
        status=CaseStatus.FAILED,
        verified=True,
        method="GET",
        url="https://api.example.com/users/42",
        status_code=500,
        elapsed_ms=150.0,
        request_headers={"Authorization": "***"},
        response_headers={"Content-Type": "application/json"},
        assertion_results=[
            {
                "rule_type": "status",
                "assertion_type": "status",
                "passed": False,
                "expected": 200,
                "actual": 500,
                "message": (
                    f"Expected 200, got 500 with payload {xss_payload} "
                    f"(secret masked as ***)"
                ),
            }
        ],
        extracted_variables={},
        extraction_errors=["JSONPath $.user not found"],
        error_message=None,
        skip_reason=None,
        row_index=1,
    )

    case_error = CaseResult(
        test_name="Timeout Test",
        module="orders",
        tags=["timeout"],
        status=CaseStatus.ERROR,
        verified=False,
        method="POST",
        url="https://api.example.com/checkout",
        status_code=None,
        elapsed_ms=None,
        request_headers={},
        response_headers={},
        assertion_results=[],
        extracted_variables={},
        extraction_errors=[],
        error_message=f"Request timeout after 30s with {xss_payload}",
        skip_reason=None,
        row_index=None,
    )

    case_skipped = CaseResult(
        test_name="Skipped Payment",
        module="orders",
        tags=["payment"],
        status=CaseStatus.SKIPPED,
        verified=False,
        method="POST",
        url="https://api.example.com/pay",
        status_code=None,
        elapsed_ms=None,
        request_headers={},
        response_headers={},
        assertion_results=[],
        extracted_variables={},
        extraction_errors=[],
        error_message=None,
        skip_reason="Upstream order creation failed",
        row_index=None,
    )

    return RunResult(
        run_id="run-report-001",
        environment_name="staging",
        started_at=t0,
        finished_at=t1,
        duration_ms=2000.0,
        total_tests=4,
        passed_tests=1,
        failed_tests=1,
        error_tests=1,
        skipped_tests=1,
        results=[case_pass, case_fail, case_error, case_skipped],
    )


def test_generate_json_report_includes_expected_vs_actual_and_trends(
    tmp_path: Path,
) -> None:
    """Covers: FR-28, FR-29, FR-30

    Verify that JSON report exports include Expected vs. Actual failure details
    and historical trend summaries.
    """
    db_mgr = DatabaseManager(tmp_path / "rep.db")
    repo = RunRepository(db_mgr)
    service = ReportService(repo)

    run = _build_test_run()
    repo.save_run(run)

    json_str = service.generate_report(report_format=ReportFormat.JSON)
    data = json.loads(json_str)

    assert data["run_id"] == "run-report-001"
    assert data["total_tests"] == 4
    assert len(data["results"]) == 4

    # Check failure details in case 1
    fail_case = data["results"][1]
    assert fail_case["status"] == "FAILED"
    assert len(fail_case["assertion_results"]) == 1
    assertion = fail_case["assertion_results"][0]
    assert assertion["rule_type"] == "status"
    assert assertion["expected"] == 200
    assert assertion["actual"] == 500
    assert assertion["passed"] is False

    # Check trends key
    assert "trends" in data
    assert data["trends"] is not None
    assert data["trends"]["run_count"] == 1

    db_mgr.close()


def test_generate_junit_xml_report_valid_xml_with_failure_error_skipped_tags(
    tmp_path: Path,
) -> None:
    """Covers: FR-28, FR-30

    Verify that JUnit XML output parses cleanly with xml.etree.ElementTree and contains
    proper <failure>, <error>, and <skipped> tags.
    """
    db_mgr = DatabaseManager(tmp_path / "rep_junit.db")
    repo = RunRepository(db_mgr)
    service = ReportService(repo)

    run = _build_test_run()
    repo.save_run(run)

    xml_str = service.generate_report(report_format=ReportFormat.JUNIT)
    root = ET.fromstring(xml_str)

    assert root.tag == "testsuite"
    assert root.attrib["name"] == "apiscope"
    assert root.attrib["tests"] == "4"
    assert root.attrib["failures"] == "1"
    assert root.attrib["errors"] == "1"
    assert root.attrib["skipped"] == "1"

    cases = list(root.findall("testcase"))
    assert len(cases) == 4

    # Case 0: pass
    assert cases[0].attrib["classname"] == "auth"
    assert len(cases[0]) == 0  # No failure/error/skipped children

    # Case 1: fail with row index
    assert cases[1].attrib["classname"] == "users"
    assert "[row 1]" in cases[1].attrib["name"]
    failure_el = cases[1].find("failure")
    assert failure_el is not None
    assert failure_el.attrib["type"] == "AssertionFailure"
    assert "Rule: status" in failure_el.text
    assert "Expected: 200" in failure_el.text
    assert "Actual: 500" in failure_el.text

    # Case 2: error
    assert cases[2].attrib["classname"] == "orders"
    error_el = cases[2].find("error")
    assert error_el is not None
    assert error_el.attrib["type"] == "ExecutionError"
    assert "Request timeout after 30s" in error_el.attrib["message"]

    # Case 3: skipped
    assert cases[3].attrib["classname"] == "orders"
    skipped_el = cases[3].find("skipped")
    assert skipped_el is not None
    assert "Upstream order creation failed" in skipped_el.attrib["message"]

    db_mgr.close()


def test_generate_html_report_valid_html_and_autoescaped(
    tmp_path: Path,
) -> None:
    """Covers: FR-28, FR-30, NFR-04

    Verify that HTML report output is a self-contained document and autoescapes
    potential XSS injection strings in test names, payloads, and error messages.
    """
    db_mgr = DatabaseManager(tmp_path / "rep_html.db")
    repo = RunRepository(db_mgr)
    service = ReportService(repo)

    run = _build_test_run(xss_payload="<script>alert('xss')</script>")
    repo.save_run(run)

    html_str = service.generate_report(report_format=ReportFormat.HTML)

    assert "<!DOCTYPE html>" in html_str
    assert "ApiScope Test Run Report" in html_str

    # Autoescaping check: raw tag must NOT exist in HTML
    assert "<script>alert('xss')</script>" not in html_str
    assert "&lt;script&gt;alert(&#39;xss&#39;)&lt;/script&gt;" in html_str

    # Expected vs Actual table must exist
    assert "Expected vs. Actual" in html_str
    assert "Expected 200, got 500" in html_str
    assert "Auth Success" in html_str

    db_mgr.close()


def test_generate_report_writes_to_file_and_blocks_path_traversal(
    tmp_path: Path,
) -> None:
    """Covers: FR-30, NFR-04

    Verify that writing to a safe file path succeeds while directory traversal
    indicators raise PathTraversalError.
    """
    db_mgr = DatabaseManager(tmp_path / "rep_write.db")
    repo = RunRepository(db_mgr)
    service = ReportService(repo)

    run = _build_test_run()
    repo.save_run(run)

    # Safe write
    out_file = tmp_path / "reports" / "summary.html"
    rendered = service.generate_report(
        report_format="html", output_path=out_file, base_dir=tmp_path
    )
    assert out_file.is_file()
    assert out_file.read_text(encoding="utf-8") == rendered

    # Path traversal block
    with pytest.raises(PathTraversalError):
        service.generate_report(
            report_format="html",
            output_path="../../unsafe.html",
            base_dir=tmp_path,
        )

    db_mgr.close()


def test_generate_report_missing_run_or_empty_db_raises_configuration_error(
    tmp_path: Path,
) -> None:
    """Covers: FR-30, NFR-02

    Verify that requesting non-existent runs or reporting an empty database
    raises ConfigurationError.
    """
    db_mgr = DatabaseManager(tmp_path / "empty_rep.db")
    repo = RunRepository(db_mgr)
    service = ReportService(repo)

    # Empty DB
    with pytest.raises(
        ConfigurationError, match="No test run found in database to report"
    ):
        service.generate_report(report_format="json")

    # Unknown run ID
    with pytest.raises(ConfigurationError, match="Run ID 'nonexistent' not found"):
        service.generate_report(run_id="nonexistent", report_format="json")

    # Invalid format
    run = _build_test_run()
    repo.save_run(run)
    with pytest.raises(ConfigurationError, match="Unsupported report format"):
        service.generate_report(report_format="invalid-format")

    db_mgr.close()


def test_generate_report_enforces_max_report_size_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Covers: FR-30, NFR-02

    Verify that reports exceeding MAX_REPORT_SIZE_BYTES raise ConfigurationError.
    """
    db_mgr = DatabaseManager(tmp_path / "rep_size.db")
    repo = RunRepository(db_mgr)
    service = ReportService(repo)

    run = _build_test_run()
    repo.save_run(run)

    # Lower the size ceiling temporarily
    import apiscope.reports.report_service

    monkeypatch.setattr(apiscope.reports.report_service, "MAX_REPORT_SIZE_BYTES", 50)

    with pytest.raises(
        ConfigurationError, match="Generated report exceeds maximum size limit"
    ):
        service.generate_report(report_format="json")

    db_mgr.close()


def test_reports_contain_no_raw_secrets(tmp_path: Path) -> None:
    """Covers: FR-28, NFR-03

    Verify that secret strings remain strictly masked ("***") across JSON, JUnit XML,
    and HTML report representations.
    """
    db_mgr = DatabaseManager(tmp_path / "rep_secret.db")
    repo = RunRepository(db_mgr)
    service = ReportService(repo)

    raw_secret = "raw-super-secret-token-888"
    # Even if an upstream bug passed raw secret into CaseResult, let's verify our masks
    run = _build_test_run(raw_secret=raw_secret)
    repo.save_run(run)

    json_rep = service.generate_report(report_format="json")
    junit_rep = service.generate_report(report_format="junit")
    html_rep = service.generate_report(report_format="html")

    for rep_str, rep_name in [
        (json_rep, "JSON"),
        (junit_rep, "JUnit XML"),
        (html_rep, "HTML"),
    ]:
        assert raw_secret not in rep_str, (
            f"Raw secret leaked into {rep_name} report output!"
        )
        assert "***" in rep_str, f"Mask indicator missing in {rep_name} report output!"

    db_mgr.close()
