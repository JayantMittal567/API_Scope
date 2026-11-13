"""Reports Layer for ApiScope.

Handles generation and export of test run summaries, health status reports,
and analytical findings into formats such as HTML, JSON, and JUnit XML.
"""

from apiscope.reports.models import TrendPoint, TrendSummary
from apiscope.reports.report_service import (
    MAX_REPORT_SIZE_BYTES,
    ReportFormat,
    ReportService,
    render_html_report,
    render_json_report,
    render_junit_report,
)

__all__ = [
    "MAX_REPORT_SIZE_BYTES",
    "ReportFormat",
    "ReportService",
    "TrendPoint",
    "TrendSummary",
    "render_html_report",
    "render_json_report",
    "render_junit_report",
]
