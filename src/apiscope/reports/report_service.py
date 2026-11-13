"""Multi-format report generation and orchestration service for ApiScope.

Provides JSON, JUnit XML, and HTML report rendering with secret masking,
Expected vs. Actual failure details, historical trend embedding, and size validation.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment as JinjaEnvironment
from jinja2 import select_autoescape

from apiscope.core.exceptions import ConfigurationError
from apiscope.core.results import CaseStatus, RunResult
from apiscope.definitions.collection import validate_safe_path
from apiscope.reports.models import TrendSummary

if TYPE_CHECKING:
    from apiscope.data.repositories import RunRepository

MAX_REPORT_SIZE_BYTES: int = 20_971_520  # 20 MB


class ReportFormat(StrEnum):
    """Supported report export formats."""

    JSON = "json"
    JUNIT = "junit"
    HTML = "html"


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ApiScope Test Report - {{ run.run_id }}</title>
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
        Roboto, Helvetica, Arial, sans-serif;
      margin: 0;
      padding: 24px;
      background-color: #f8fafc;
      color: #1e293b;
    }
    .container {
      max-width: 1200px;
      margin: 0 auto;
    }
    .banner {
      background: #ffffff;
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      padding: 20px;
      margin-bottom: 24px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .banner h1 {
      margin: 0 0 12px 0;
      font-size: 24px;
      color: #0f172a;
    }
    .meta-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-top: 12px;
    }
    .meta-card {
      background: #f1f5f9;
      padding: 10px 14px;
      border-radius: 6px;
    }
    .meta-card .label {
      font-size: 12px;
      color: #64748b;
      text-transform: uppercase;
      font-weight: 600;
    }
    .meta-card .value {
      font-size: 18px;
      font-weight: 700;
      color: #0f172a;
      margin-top: 4px;
    }
    .badge {
      display: inline-block;
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 600;
      border-radius: 4px;
      text-transform: uppercase;
    }
    .badge-passed { background: #dcfce7; color: #15803d; }
    .badge-failed { background: #fee2e2; color: #b91c1c; }
    .badge-error { background: #ffedd5; color: #c2410c; }
    .badge-skipped { background: #f1f5f9; color: #475569; }
    table {
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 24px;
      background: #ffffff;
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      overflow: hidden;
    }
    th, td {
      padding: 10px 14px;
      text-align: left;
      border-bottom: 1px solid #e2e8f0;
      font-size: 14px;
    }
    th {
      background: #f8fafc;
      font-weight: 600;
      color: #475569;
    }
    tr:last-child td {
      border-bottom: none;
    }
    .section-title {
      font-size: 18px;
      margin: 24px 0 12px 0;
      color: #0f172a;
      font-weight: 700;
    }
    .failure-card {
      background: #ffffff;
      border: 1px solid #fecaca;
      border-left: 4px solid #ef4444;
      border-radius: 6px;
      padding: 16px;
      margin-bottom: 16px;
    }
    .failure-card h3 {
      margin: 0 0 8px 0;
      font-size: 16px;
      color: #991b1b;
    }
    .pre-box {
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-radius: 4px;
      padding: 8px 12px;
      font-family: monospace;
      font-size: 13px;
      white-space: pre-wrap;
      word-break: break-all;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="banner">
      <h1>ApiScope Test Run Report</h1>
      <div><strong>Run ID:</strong> {{ run.run_id }}</div>
      <div><strong>Environment:</strong> {{ run.environment_name or "default" }}</div>
      <div>
        <strong>Started:</strong> {{ run.started_at }} |
        <strong>Duration:</strong> {{ "%.1f"|format(run.duration_ms) }} ms
      </div>
      <div class="meta-grid">
        <div class="meta-card">
          <div class="label">Total Tests</div>
          <div class="value">{{ run.total_tests }}</div>
        </div>
        <div class="meta-card">
          <div class="label">Passed</div>
          <div class="value" style="color: #15803d;">{{ run.passed_tests }}</div>
        </div>
        <div class="meta-card">
          <div class="label">Failed</div>
          <div class="value" style="color: #b91c1c;">{{ run.failed_tests }}</div>
        </div>
        <div class="meta-card">
          <div class="label">Errors</div>
          <div class="value" style="color: #c2410c;">{{ run.error_tests }}</div>
        </div>
        <div class="meta-card">
          <div class="label">Skipped</div>
          <div class="value" style="color: #475569;">{{ run.skipped_tests }}</div>
        </div>
        <div class="meta-card">
          <div class="label">Pass Rate</div>
          <div class="value">
            {% if run.total_tests > 0 %}
              {{ "%.1f"|format((run.passed_tests / run.total_tests) * 100) }}%
            {% else %}
              0.0%
            {% endif %}
          </div>
        </div>
      </div>
    </div>

    <div class="section-title">Test Results</div>
    <table>
      <thead>
        <tr>
          <th>Module</th>
          <th>Test Name</th>
          <th>Method</th>
          <th>URL</th>
          <th>Status</th>
          <th>Status Code</th>
          <th>Elapsed (ms)</th>
        </tr>
      </thead>
      <tbody>
        {% for case in run.results %}
        <tr>
          <td>{{ case.module }}</td>
          <td>
            {{ case.test_name }}
            {% if case.row_index is not none %} [row {{ case.row_index }}]{% endif %}
          </td>
          <td><code>{{ case.method }}</code></td>
          <td><code>{{ case.url }}</code></td>
          <td>
            {% if case.status == "PASSED" or case.status.value == "PASSED" %}
              <span class="badge badge-passed">PASSED</span>
            {% elif case.status == "FAILED" or case.status.value == "FAILED" %}
              <span class="badge badge-failed">FAILED</span>
            {% elif case.status == "ERROR" or case.status.value == "ERROR" %}
              <span class="badge badge-error">ERROR</span>
            {% else %}
              <span class="badge badge-skipped">SKIPPED</span>
            {% endif %}
          </td>
          <td>{{ case.status_code if case.status_code is not none else "-" }}</td>
          <td>
            {{ "%.1f"|format(case.elapsed_ms) if case.elapsed_ms is not none else "-" }}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>

    {% if has_failures %}
    <div class="section-title">Failure & Diagnostic Details</div>
    {% for case in run.results %}
      {% if case.status != "PASSED" and case.status.value != "PASSED" %}
      <div class="failure-card">
        <h3>
          [{{ case.module }}] {{ case.test_name }}
          {% if case.row_index is not none %}(row {{ case.row_index }}){% endif %}
          &mdash; Status: {{ case.status.value if case.status.value else case.status }}
        </h3>
        {% if case.error_message %}
          <div><strong>Error Message:</strong></div>
          <div class="pre-box">{{ case.error_message }}</div>
        {% endif %}
        {% if case.skip_reason %}
          <div><strong>Skip Reason:</strong></div>
          <div class="pre-box">{{ case.skip_reason }}</div>
        {% endif %}
        {% if case.extraction_errors %}
          <div><strong>Extraction Errors:</strong></div>
          <ul class="pre-box">
            {% for err in case.extraction_errors %}
              <li>{{ err }}</li>
            {% endfor %}
          </ul>
        {% endif %}
        {% if case.assertion_results %}
          <div style="margin-top: 10px;">
            <strong>Assertion Results (Expected vs. Actual):</strong>
          </div>
          <table>
            <thead>
              <tr>
                <th>Rule Type</th>
                <th>Passed</th>
                <th>Expected</th>
                <th>Actual</th>
                <th>Message</th>
              </tr>
            </thead>
            <tbody>
              {% for a in case.assertion_results %}
              <tr style="background-color: {{ '#f0fdf4' if a.passed else '#fef2f2' }};">
                <td><code>{{ a.rule_type or a.assertion_type }}</code></td>
                <td>{{ "YES" if a.passed else "NO" }}</td>
                <td><code>{{ a.expected }}</code></td>
                <td><code>{{ a.actual }}</code></td>
                <td>{{ a.message }}</td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        {% endif %}
      </div>
      {% endif %}
    {% endfor %}
    {% endif %}

    {% if trends and trends.points %}
    <div class="section-title">Historical Trends</div>
    <div style="margin-bottom: 8px;">
      <strong>Runs Evaluated:</strong> {{ trends.run_count }} |
      <strong>Overall Pass Rate:</strong> {{ trends.overall_pass_rate_pct }}% |
      <strong>Overall Avg Latency:</strong> {{ trends.overall_avg_elapsed_ms }} ms
    </div>
    <table>
      <thead>
        <tr>
          <th>Run ID</th>
          <th>Started At</th>
          <th>Total Tests</th>
          <th>Passed Tests</th>
          <th>Pass Rate</th>
          <th>Avg Latency (ms)</th>
          <th>P95 Latency (ms)</th>
        </tr>
      </thead>
      <tbody>
        {% for pt in trends.points %}
        <tr>
          <td><code>{{ pt.run_id }}</code></td>
          <td>{{ pt.started_at }}</td>
          <td>{{ pt.total_tests }}</td>
          <td>{{ pt.passed_tests }}</td>
          <td>{{ pt.pass_rate_pct }}%</td>
          <td>{{ pt.avg_elapsed_ms }}</td>
          <td>{{ pt.p95_elapsed_ms }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% endif %}
  </div>
</body>
</html>
"""


def render_json_report(
    run_result: RunResult, trends: TrendSummary | None = None
) -> str:
    """Render test run results as formatted JSON with secret masking.

    Args:
        run_result: The executed test run outcome.
        trends: Optional historical TrendSummary.

    Returns:
        Indented JSON string representation.
    """
    data = run_result.to_dict()
    for case in data.get("results", []):
        for assertion in case.get("assertion_results", []):
            if "rule_type" not in assertion:
                assertion["rule_type"] = assertion.get("assertion_type", "unknown")

    data["trends"] = trends.to_dict() if trends else None
    return json.dumps(data, indent=2)


def render_junit_report(run_result: RunResult) -> str:
    """Render test run results as standardized JUnit XML.

    Args:
        run_result: The executed test run outcome.

    Returns:
        Valid JUnit XML string.
    """
    total = run_result.total_tests
    failed = run_result.failed_tests
    errors = run_result.error_tests
    skipped = run_result.skipped_tests
    duration_sec = run_result.duration_ms / 1000.0

    started_iso = (
        run_result.started_at.isoformat()
        if isinstance(run_result.started_at, datetime)
        else str(run_result.started_at)
    )

    root = ET.Element(
        "testsuite",
        attrib={
            "name": "apiscope",
            "tests": str(total),
            "failures": str(failed),
            "errors": str(errors),
            "skipped": str(skipped),
            "time": f"{duration_sec:.3f}",
            "timestamp": started_iso,
        },
    )

    for case in run_result.results:
        case_name = case.test_name
        if case.row_index is not None:
            case_name = f"{case.test_name} [row {case.row_index}]"

        elapsed_sec = (case.elapsed_ms / 1000.0) if case.elapsed_ms is not None else 0.0

        tc = ET.SubElement(
            root,
            "testcase",
            attrib={
                "classname": case.module,
                "name": case_name,
                "time": f"{elapsed_sec:.3f}",
            },
        )

        status_str = (
            case.status.value if hasattr(case.status, "value") else str(case.status)
        )

        if status_str == CaseStatus.FAILED.value:
            failed_assertions = [
                a for a in case.assertion_results if not a.get("passed", False)
            ]
            detail_lines: list[str] = []
            for a in failed_assertions:
                r_type = a.get("rule_type") or a.get("assertion_type", "unknown")
                exp = a.get("expected")
                act = a.get("actual")
                msg = a.get("message", "")
                detail_lines.append(
                    f"Rule: {r_type} | Expected: {exp} | Actual: {act} | Message: {msg}"
                )

            if case.extraction_errors:
                for err in case.extraction_errors:
                    detail_lines.append(f"ExtractionError: {err}")

            summary_msg = (
                f"{len(failed_assertions)} assertion(s) failed"
                if failed_assertions
                else "Test failed verification"
            )
            body_text = (
                "\n".join(detail_lines)
                if detail_lines
                else case.error_message or "Assertion failure"
            )

            failure_el = ET.SubElement(
                tc,
                "failure",
                attrib={
                    "type": "AssertionFailure",
                    "message": summary_msg,
                },
            )
            failure_el.text = body_text

        elif status_str == CaseStatus.ERROR.value:
            err_msg = case.error_message or "Execution Error"
            error_el = ET.SubElement(
                tc,
                "error",
                attrib={
                    "type": "ExecutionError",
                    "message": err_msg,
                },
            )
            error_el.text = err_msg

        elif status_str == CaseStatus.SKIPPED.value:
            skip_msg = case.skip_reason or "Skipped"
            ET.SubElement(
                tc,
                "skipped",
                attrib={
                    "message": skip_msg,
                },
            )

    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return xml_bytes.decode("utf-8")


def render_html_report(
    run_result: RunResult, trends: TrendSummary | None = None
) -> str:
    """Render test run results as a self-contained HTML5 report.

    Args:
        run_result: The executed test run outcome.
        trends: Optional historical TrendSummary.

    Returns:
        Rendered HTML document string with autoescaping enabled.
    """
    has_failures = any(
        (c.status.value if hasattr(c.status, "value") else str(c.status))
        != CaseStatus.PASSED.value
        for c in run_result.results
    )
    env = JinjaEnvironment(
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.from_string(_HTML_TEMPLATE)
    return template.render(run=run_result, trends=trends, has_failures=has_failures)


class ReportService:
    """Orchestration service for generating and exporting test execution reports."""

    def __init__(self, repository: RunRepository) -> None:
        """Initialize ReportService.

        Args:
            repository: RunRepository for reading and persisting test runs.
        """
        self.repository = repository

    def record_run(self, run_result: RunResult, has_warnings: bool = False) -> str:
        """Persist a RunResult to SQLite history.

        Args:
            run_result: Aggregated run outcome to persist.
            has_warnings: Whether the run produced warnings.

        Returns:
            The persisted run_id string.
        """
        return self.repository.save_run(run_result, has_warnings=has_warnings)

    def generate_report(
        self,
        run_id: str | None = None,
        report_format: ReportFormat | str = ReportFormat.JSON,
        output_path: str | Path | None = None,
        base_dir: Path | None = None,
        include_trends: bool = True,
    ) -> str:
        """Generate a formatted report and optionally write to a safe file path.

        Args:
            run_id: Optional run ID to report (defaults to latest run if None).
            report_format: Export format ('json', 'junit', 'html').
            output_path: Optional destination file path.
            base_dir: Optional base directory for directory traversal validation.
            include_trends: Whether to include historical trend data.

        Returns:
            Rendered report string.

        Raises:
            ConfigurationError: If run is not found, format is invalid, or report
                exceeds MAX_REPORT_SIZE_BYTES.
            PathTraversalError: If output_path escapes base_dir.
        """
        fmt_str = (
            report_format.value
            if isinstance(report_format, ReportFormat)
            else str(report_format).strip().lower()
        )

        try:
            fmt = ReportFormat(fmt_str)
        except ValueError:
            raise ConfigurationError(
                f"Unsupported report format: '{report_format}'"
            ) from None

        if run_id is not None:
            run_result = self.repository.get_run(run_id)
            if run_result is None:
                raise ConfigurationError(f"Run ID '{run_id}' not found in database")
        else:
            run_result = self.repository.get_latest_run()
            if run_result is None:
                raise ConfigurationError("No test run found in database to report")

        trends: TrendSummary | None = None
        if include_trends:
            try:
                trends = self.repository.get_trends()
            except Exception:
                trends = None

        if fmt == ReportFormat.JSON:
            rendered = render_json_report(run_result, trends=trends)
        elif fmt == ReportFormat.JUNIT:
            rendered = render_junit_report(run_result)
        elif fmt == ReportFormat.HTML:
            rendered = render_html_report(run_result, trends=trends)
        else:
            raise ConfigurationError(f"Unsupported report format: '{report_format}'")

        encoded_len = len(rendered.encode("utf-8"))
        if encoded_len > MAX_REPORT_SIZE_BYTES:
            raise ConfigurationError(
                f"Generated report exceeds maximum size limit of "
                f"{MAX_REPORT_SIZE_BYTES} bytes"
            )

        if output_path is not None:
            out_p = Path(output_path)
            effective_base = (
                base_dir
                if base_dir is not None
                else (out_p.parent if out_p.is_absolute() else Path.cwd())
            )
            safe_target = validate_safe_path(
                base_dir=effective_base, ref_path=output_path
            )
            safe_target.parent.mkdir(parents=True, exist_ok=True)
            safe_target.write_text(rendered, encoding="utf-8")

        return rendered
