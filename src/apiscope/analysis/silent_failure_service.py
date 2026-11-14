"""Silent Failure Detector and Diagnostic Signal Analysis Service (Task 23).

Implements Module M10: detects silent failures across Signals S1 (app log errors),
S2 (failed dependency health probes), S3 (latency drift vs. saved baseline),
S4 (2xx responses with error-like bodies or warning headers), S5 (unverified or
violated side effects), S6 (response-contract contradictions), S7 (business invariant
violations), and S8 (inconsistent repeated observations), redacting credentials.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from apiscope.analysis.latency_drift import (
    DriftVerdict,
    LatencyDriftAnalyzer,
)
from apiscope.core.exceptions import ConfigurationError
from apiscope.core.logging_config import redact_sensitive_text
from apiscope.core.models import HttpResponse
from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.definitions.collection import validate_safe_path

MAX_LOG_FILE_BYTES: int = 52_428_800  # 50 MB
MAX_LOG_LINES: int = 100_000

# App log line format: <timestamp> <LEVEL> [<module>] <message>
_APP_LOG_REGEX = re.compile(
    r"""^(\S+)\s+(INFO|WARNING|WARN|ERROR|CRITICAL|EXCEPTION)\s+(?:\[([a-zA-Z0-9_-]+)\]\s+)?(.*)$"""
)

_WARNING_HEADERS: frozenset[str] = frozenset(
    {
        "warning",
        "deprecation",
        "sunset",
        "x-apiscope-warning",
        "x-deprecated",
        "x-error",
    }
)


class VerificationState(StrEnum):
    """Classification of diagnostic certainty for silent failure findings."""

    VERIFIED_OBSERVATION = "VERIFIED_OBSERVATION"
    SUSPECTED_BEHAVIOUR = "SUSPECTED_BEHAVIOUR"
    UNVERIFIED_EFFECT = "UNVERIFIED_EFFECT"
    NOT_TESTED = "NOT_TESTED"
    INCONCLUSIVE = "INCONCLUSIVE"


class SilentSignalType(StrEnum):
    """Enumeration of recognized silent failure signal categories (S1-S8)."""

    __test__ = False

    S1_LOG_ERROR = "S1_LOG_ERROR"
    S2_DEPENDENCY_PROBE = "S2_DEPENDENCY_PROBE"
    S3_LATENCY_DRIFT = "S3_LATENCY_DRIFT"
    S4_SUSPICIOUS_2XX = "S4_SUSPICIOUS_2XX"
    S5_UNVERIFIED_EFFECT = "S5_UNVERIFIED_EFFECT"
    S6_CONTRACT_CONTRADICTION = "S6_CONTRACT_CONTRADICTION"
    S7_INVARIANT_VIOLATION = "S7_INVARIANT_VIOLATION"
    S8_INCONSISTENT_OBSERVATION = "S8_INCONSISTENT_OBSERVATION"


@dataclass(frozen=True)
class AppLogEntry:
    """Normalized application log line record with redacted message content."""

    __test__ = False

    timestamp: datetime | None
    raw_timestamp: str | None
    level: str
    module: str | None
    message: str


@dataclass
class SilentFailureFinding:
    """Structured evidence-backed finding for silent failure or unverified outcome."""

    finding_id: str
    signal_type: SilentSignalType
    verification_state: VerificationState
    module: str
    endpoint_or_source: str
    description: str
    evidence: str
    suggested_action: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert finding into a serializable dictionary representation."""
        return {
            "finding_id": self.finding_id,
            "signal_type": self.signal_type.value,
            "verification_state": self.verification_state.value,
            "module": self.module,
            "endpoint_or_source": self.endpoint_or_source,
            "description": self.description,
            "evidence": self.evidence,
            "suggested_action": self.suggested_action,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class SilentFailureSignal:
    """Diagnostic signal indicating internal application anomaly despite 2xx status."""

    __test__ = False

    signal_type: SilentSignalType
    module: str
    endpoint_or_source: str
    description: str
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        """Convert signal into a serializable dictionary representation."""
        return {
            "signal_type": self.signal_type.value,
            "module": self.module,
            "endpoint_or_source": self.endpoint_or_source,
            "description": self.description,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class SilentFailureReport:
    """Comprehensive silent failure evaluation report across diagnostic signals."""

    __test__ = False

    evaluated_at: datetime
    has_silent_failures: bool
    verdict_banner: str
    total_signals: int
    counts_by_type: dict[str, int]
    signals: list[SilentFailureSignal]
    findings: list[SilentFailureFinding] = field(default_factory=list)
    untested_effects_count: int = 0
    verified_mismatches_count: int = 0
    suspicious_count: int = 0
    inconclusive_count: int = 0
    latency_drift_report: dict[str, Any] | None = None

    def filter_by_module(self, module: str) -> list[SilentFailureSignal]:
        """Return signals matching a specific module name (case-insensitive)."""
        target = module.strip().lower()
        return [s for s in self.signals if s.module.lower() == target]

    def filter_findings_by_module(self, module: str) -> list[SilentFailureFinding]:
        """Return findings matching a specific module name (case-insensitive)."""
        target = module.strip().lower()
        return [f for f in self.findings if f.module.lower() == target]

    def to_dict(self) -> dict[str, Any]:
        """Convert silent failure report to a 100% JSON-serializable dictionary."""
        return {
            "evaluated_at": self.evaluated_at.isoformat(),
            "has_silent_failures": self.has_silent_failures,
            "verdict_banner": self.verdict_banner,
            "total_signals": self.total_signals,
            "counts_by_type": dict(self.counts_by_type),
            "signals": [s.to_dict() for s in self.signals],
            "findings": [f.to_dict() for f in self.findings],
            "summary_counts": {
                "verified_mismatches": self.verified_mismatches_count,
                "suspicious": self.suspicious_count,
                "untested_effects": self.untested_effects_count,
                "inconclusive": self.inconclusive_count,
            },
            "latency_drift_report": self.latency_drift_report,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize report to formatted JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def to_yaml(self) -> str:
        """Serialize report to YAML string."""
        return yaml.safe_dump(self.to_dict(), sort_keys=False)


def _parse_iso_timestamp(ts_str: str) -> datetime | None:
    """Attempt to parse an ISO-8601 or common timestamp string to UTC datetime."""
    clean = ts_str.strip().rstrip("Z")
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S.%f",
    ):
        try:
            dt = datetime.strptime(clean, fmt)
            return dt.replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(ts_str)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    except Exception:
        return None


def parse_app_log(content: str) -> list[AppLogEntry]:
    """Parse raw application log content into normalized, secret-redacted entries.

    Args:
        content: Application log text content.

    Returns:
        List of parsed AppLogEntry records.

    Raises:
        ConfigurationError: If line limit is exceeded or content contains no entries.
    """
    raw_lines = content.splitlines()
    non_empty_lines = [line.strip() for line in raw_lines if line.strip()]

    if len(non_empty_lines) > MAX_LOG_LINES:
        raise ConfigurationError(
            f"Application log exceeds maximum limit of {MAX_LOG_LINES} lines."
        )

    entries: list[AppLogEntry] = []
    has_candidate_text = False

    for line in non_empty_lines:
        if line.startswith("#"):
            continue

        has_candidate_text = True

        # 1. Attempt JSON Lines parsing
        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    ts_val = data.get("timestamp") or data.get("time")
                    lvl_val = data.get("level") or data.get("levelname") or "INFO"
                    msg_val = data.get("message") or data.get("msg") or ""
                    mod_val = data.get("module") or data.get("name")

                    parsed_ts = _parse_iso_timestamp(str(ts_val)) if ts_val else None
                    redacted_msg = redact_sensitive_text(str(msg_val))

                    entries.append(
                        AppLogEntry(
                            timestamp=parsed_ts,
                            raw_timestamp=str(ts_val) if ts_val else None,
                            level=str(lvl_val).strip().upper(),
                            module=str(mod_val).strip().lower() if mod_val else None,
                            message=redacted_msg,
                        )
                    )
                    continue
            except Exception:
                pass

        # 2. Standard log line parsing: <timestamp> <LEVEL> [<module>] <message>
        match = _APP_LOG_REGEX.match(line)
        if match:
            raw_ts = match.group(1).strip()
            level = match.group(2).strip().upper()
            if level == "WARN":
                level = "WARNING"
            mod_tag = match.group(3)
            raw_msg = match.group(4).strip()

            parsed_ts = _parse_iso_timestamp(raw_ts)
            redacted_msg = redact_sensitive_text(raw_msg)
            clean_module = mod_tag.strip().lower() if mod_tag else None

            entries.append(
                AppLogEntry(
                    timestamp=parsed_ts,
                    raw_timestamp=raw_ts,
                    level=level,
                    module=clean_module,
                    message=redacted_msg,
                )
            )
            continue

        # 3. Fallback for unformatted tracebacks or exception dumps
        if any(
            err_marker in line
            for err_marker in (
                "Traceback (most recent call last):",
                "RuntimeError:",
                "Exception:",
                "Error:",
            )
        ):
            entries.append(
                AppLogEntry(
                    timestamp=None,
                    raw_timestamp=None,
                    level="ERROR",
                    module=None,
                    message=redact_sensitive_text(line),
                )
            )

    if has_candidate_text and not entries:
        raise ConfigurationError("Application log contains no valid log entries")

    return entries


def load_app_log(
    file_path: str | Path, base_dir: Path | None = None
) -> list[AppLogEntry]:
    """Safely load and parse an application log file from disk.

    Args:
        file_path: Path to the log file.
        base_dir: Optional root directory for directory traversal validation.

    Returns:
        List of parsed AppLogEntry records.

    Raises:
        ConfigurationError: If the file is missing, exceeds size limits, or is invalid.
        PathTraversalError: If file_path escapes the allowed directory boundary.
    """
    p = Path(file_path)
    effective_base = (
        base_dir
        if base_dir is not None
        else (p.parent if p.is_absolute() else Path.cwd())
    )
    safe_path = validate_safe_path(base_dir=effective_base, ref_path=file_path)

    if not safe_path.is_file():
        raise ConfigurationError(f"Application log file not found: {file_path}")

    file_size = safe_path.stat().st_size
    if file_size > MAX_LOG_FILE_BYTES:
        raise ConfigurationError(
            f"Application log file exceeds maximum size of {MAX_LOG_FILE_BYTES} bytes."
        )

    try:
        content = safe_path.read_text(encoding="utf-8-sig")
    except Exception as err:
        raise ConfigurationError(
            f"Error reading application log file '{file_path}': {err}"
        ) from err

    return parse_app_log(content)


def load_run_file(file_path: str | Path, base_dir: Path | None = None) -> RunResult:
    """Safely load and normalize a RunResult from a JSON report file on disk.

    Supports:
      1. Native ApiScope RunResult format ('results' array)
      2. WorkflowExecutionResult format ('setup_results', 'test_results', etc.)
      3. StockPile-style report format ('cases' array)
    """
    p = Path(file_path)
    effective_base = (
        base_dir
        if base_dir is not None
        else (p.parent if p.is_absolute() else Path.cwd())
    )
    safe_path = validate_safe_path(base_dir=effective_base, ref_path=file_path)

    if not safe_path.is_file():
        raise ConfigurationError(f"Test run file not found: {file_path}")

    try:
        content = safe_path.read_text(encoding="utf-8-sig")
        data = json.loads(content)
    except Exception as err:
        raise ConfigurationError(
            f"Error reading/parsing test run file '{file_path}': {err}"
        ) from err

    return parse_run_data(data)


def parse_run_data(data: dict[str, Any]) -> RunResult:
    """Convert arbitrary test run JSON structure into a normalized RunResult."""
    # 1. Direct ApiScope RunResult format
    if "results" in data and "total_tests" in data and "run_id" in data:
        case_results: list[CaseResult] = []
        for c in data.get("results", []):
            case_results.append(
                CaseResult(
                    test_name=str(c.get("test_name", "Unnamed Case")),
                    module=str(c.get("module", "default")),
                    tags=list(c.get("tags", [])),
                    status=CaseStatus(c.get("status", CaseStatus.PASSED.value)),
                    verified=bool(c.get("verified", False)),
                    method=str(c.get("method", "GET")),
                    url=str(c.get("url", "")),
                    status_code=c.get("status_code"),
                    elapsed_ms=c.get("elapsed_ms"),
                    request_headers=dict(c.get("request_headers", {})),
                    response_headers=dict(c.get("response_headers", {})),
                    assertion_results=list(c.get("assertion_results", [])),
                    extracted_variables=dict(c.get("extracted_variables", {})),
                    extraction_errors=list(c.get("extraction_errors", [])),
                    error_message=c.get("error_message"),
                    skip_reason=c.get("skip_reason"),
                    row_index=c.get("row_index"),
                    response_body=str(c.get("response_body", "")),
                    correlation_id=str(c.get("correlation_id", "")),
                )
            )
        return RunResult(
            run_id=str(data.get("run_id", "imported-run")),
            environment_name=data.get("environment_name"),
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            duration_ms=float(data.get("duration_ms", 0.0)),
            total_tests=int(data.get("total_tests", len(case_results))),
            passed_tests=int(data.get("passed_tests", 0)),
            failed_tests=int(data.get("failed_tests", 0)),
            error_tests=int(data.get("error_tests", 0)),
            skipped_tests=int(data.get("skipped_tests", 0)),
            results=case_results,
            has_warnings=bool(data.get("has_warnings", False)),
            blocked_tests=int(data.get("blocked_tests", 0)),
        )

    # 2. WorkflowExecutionResult format
    if "workflow_id" in data and ("setup_results" in data or "test_results" in data):
        wf_cases = []
        for step_key in ("setup_results", "test_results", "cleanup_results"):
            for s in data.get(step_key, []):
                wf_cases.append(
                    CaseResult(
                        test_name=f"[{data.get('workflow_id')}] [{s.get('step_type', 'STEP')}:{s.get('step_number', 1)}] {s.get('name', 'Step')}",
                        module="workflow",
                        tags=[
                            f"workflow:{str(data.get('workflow_id')).lower()}",
                            f"step-type:{str(s.get('step_type', 'test')).lower()}",
                        ],
                        status=CaseStatus(s.get("status", CaseStatus.PASSED.value)),
                        verified=(s.get("status") == CaseStatus.PASSED.value),
                        method=str(s.get("method", "GET")),
                        url=str(s.get("url", "")),
                        status_code=s.get("status_code"),
                        elapsed_ms=s.get("elapsed_ms", 0.0),
                        request_headers=dict(s.get("request_headers", {})),
                        response_headers=dict(s.get("response_headers", {})),
                        assertion_results=list(s.get("assertions", [])),
                        extracted_variables=dict(s.get("extracted_variables", {})),
                        extraction_errors=list(s.get("extraction_errors", [])),
                        error_message=s.get("error_message"),
                        skip_reason=s.get("skip_reason"),
                        correlation_id=str(s.get("correlation_id", "")),
                    )
                )
        return RunResult(
            run_id=f"wf-run-{data.get('workflow_id', 'imported')}",
            environment_name=data.get("environment_name"),
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            duration_ms=float(data.get("duration_ms", 0.0)),
            total_tests=len(wf_cases),
            passed_tests=sum(1 for c in wf_cases if c.status == CaseStatus.PASSED),
            failed_tests=sum(1 for c in wf_cases if c.status == CaseStatus.FAILED),
            error_tests=sum(1 for c in wf_cases if c.status == CaseStatus.ERROR),
            skipped_tests=sum(1 for c in wf_cases if c.status == CaseStatus.SKIPPED),
            blocked_tests=sum(1 for c in wf_cases if c.status == CaseStatus.BLOCKED),
            results=wf_cases,
        )

    # 3. StockPile-style report JSON format ('cases' array)
    if "cases" in data:
        stock_cases = []
        for c in data.get("cases", []):
            status_str = str(c.get("status", "PASSED")).upper()
            try:
                case_status = CaseStatus(status_str)
            except ValueError:
                case_status = CaseStatus.FAILED

            c_name = str(c.get("name", "Test Case"))
            c_method = c.get("method")
            if not c_method:
                m_match = re.search(
                    r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b", c_name
                )
                c_method = m_match.group(1) if m_match else "GET"

            c_url = c.get("url")
            if not c_url:
                p_match = re.search(r"(/[a-zA-Z0-9_/-]+)", c_name)
                c_url = p_match.group(1) if p_match else ""

            stock_cases.append(
                CaseResult(
                    test_name=c_name,
                    module=str(c.get("module", "default")),
                    tags=list(c.get("tags", [])),
                    status=case_status,
                    verified=bool(c.get("verified", False)),
                    method=str(c_method),
                    url=str(c_url),
                    status_code=c.get("http_status", c.get("status_code")),
                    elapsed_ms=c.get("elapsed_ms"),
                    request_headers={},
                    response_headers={},
                    assertion_results=list(c.get("assertions", [])),
                    extracted_variables={},
                    extraction_errors=[],
                    error_message=c.get("error"),
                )
            )
        return RunResult(
            run_id=str(data.get("run_id", "imported-cases-run")),
            environment_name=data.get("environment_name"),
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            duration_ms=float(data.get("duration_ms", 0.0)),
            total_tests=len(stock_cases),
            passed_tests=sum(1 for c in stock_cases if c.status == CaseStatus.PASSED),
            failed_tests=sum(1 for c in stock_cases if c.status == CaseStatus.FAILED),
            error_tests=sum(1 for c in stock_cases if c.status == CaseStatus.ERROR),
            skipped_tests=sum(1 for c in stock_cases if c.status == CaseStatus.SKIPPED),
            blocked_tests=sum(1 for c in stock_cases if c.status == CaseStatus.BLOCKED),
            results=stock_cases,
        )

    raise ConfigurationError(
        "Unrecognized run data structure: missing 'results' or 'cases'"
    )


class SilentFailureService:
    """Application service for detecting silent failures and latency regressions."""

    def __init__(
        self,
        clock: Callable[[], datetime] | None = None,
        latency_tolerance_factor: float = 1.5,
        min_samples_threshold: int = 5,
        min_abs_drift_ms: float = 20.0,
        strict_environment_match: bool = True,
    ) -> None:
        """Initialize SilentFailureService.

        Args:
            clock: Optional clock callable returning timezone-aware UTC datetime.
            latency_tolerance_factor: Tolerance multiplier for latency drift.
            min_samples_threshold: Minimum sample size before evaluating latency drift.
            min_abs_drift_ms: Minimum absolute latency increase in ms to flag regression.
            strict_environment_match: Whether cross-environment comparisons are rejected.
        """
        self._clock = clock or (lambda: datetime.now(UTC))
        self._latency_tolerance_factor = latency_tolerance_factor
        self._min_samples_threshold = min_samples_threshold
        self._min_abs_drift_ms = min_abs_drift_ms
        self._strict_environment_match = strict_environment_match

    def analyze(
        self,
        run_result: RunResult | None = None,
        app_log_entries: Sequence[AppLogEntry] | None = None,
        health_probe_payload: dict[str, Any] | HttpResponse | None = None,
        baseline_p95_by_module: Mapping[str, float] | float | None = None,
        raw_responses: Sequence[tuple[str, str, HttpResponse]] | None = None,
        module: str | None = None,
        workflow_result: Any | None = None,
        baseline_run: RunResult | None = None,
        contract: Any | None = None,
        approved_invariants: list[dict[str, Any]] | None = None,
        latency_drift_analyzer: LatencyDriftAnalyzer | None = None,
    ) -> SilentFailureReport:
        """Analyze test results, logs, and health probes to detect silent failures.

        Args:
            run_result: Optional executed test run result.
            app_log_entries: Optional application log entries for Signal S1.
            health_probe_payload: Optional health check JSON or HttpResponse for S2.
            baseline_p95_by_module: Optional baseline p95 ms for legacy S3.
            raw_responses: Optional raw HTTP responses (module, name, HttpResponse).
            module: Optional module filter.
            workflow_result: Optional WorkflowExecutionResult for stateful checks.
            baseline_run: Optional baseline RunResult for statistical latency comparison.
            contract: Optional CanonicalContract for schema/contract contradiction checks.
            approved_invariants: Optional approved business invariant rules.
            latency_drift_analyzer: Optional configured LatencyDriftAnalyzer.

        Returns:
            SilentFailureReport containing diagnosed findings, signals, and verdict banner.
        """
        signals: list[SilentFailureSignal] = []
        findings: list[SilentFailureFinding] = []
        latency_drift_report_dict: dict[str, Any] | None = None

        # Harmonize workflow_result with run_result if only workflow_result is supplied
        effective_run = run_result
        if (
            effective_run is None
            and workflow_result is not None
            and hasattr(workflow_result, "to_run_result")
        ):
            effective_run = workflow_result.to_run_result()

        def _add(
            finding_id: str,
            sig_type: SilentSignalType,
            v_state: VerificationState,
            mod: str,
            endpoint: str,
            desc: str,
            ev: str,
            action: str = "",
            details: dict[str, Any] | None = None,
        ) -> None:
            findings.append(
                SilentFailureFinding(
                    finding_id=finding_id,
                    signal_type=sig_type,
                    verification_state=v_state,
                    module=mod,
                    endpoint_or_source=endpoint,
                    description=desc,
                    evidence=ev,
                    suggested_action=action,
                    details=details or {},
                )
            )
            # Only verified defects and suspicious behaviors constitute active anomaly signals
            if v_state in {
                VerificationState.VERIFIED_OBSERVATION,
                VerificationState.SUSPECTED_BEHAVIOUR,
            }:
                signals.append(
                    SilentFailureSignal(
                        signal_type=sig_type,
                        module=mod,
                        endpoint_or_source=endpoint,
                        description=desc,
                        evidence=ev,
                    )
                )

        # =====================================================================
        # Signal S1: Application Log Error Correlation (FR-39)
        # =====================================================================
        if app_log_entries:
            error_entries = [
                e
                for e in app_log_entries
                if e.level in {"ERROR", "CRITICAL", "EXCEPTION"}
            ]

            corr_lookup: dict[str, CaseResult] = {}
            if effective_run is not None:
                corr_lookup = {
                    c.correlation_id: c
                    for c in effective_run.results
                    if c.correlation_id
                }

            correlated_entries: list[AppLogEntry] = []
            if effective_run is not None:
                win_start = effective_run.started_at - timedelta(seconds=60)
                win_end = effective_run.finished_at + timedelta(seconds=60)
                matched = [
                    e
                    for e in error_entries
                    if (corr_lookup and any(cid in e.message for cid in corr_lookup))
                    or (e.timestamp is not None and win_start <= e.timestamp <= win_end)
                ]
                correlated_entries = matched or list(error_entries)
            else:
                correlated_entries = list(error_entries)

            for idx, entry in enumerate(correlated_entries, start=1):
                matched_case: CaseResult | None = None
                if corr_lookup:
                    for cid, case in corr_lookup.items():
                        if cid in entry.message:
                            matched_case = case
                            break

                inferred_mod = (
                    entry.module
                    or (matched_case.module if matched_case else None)
                    or module
                    or "default"
                )

                if matched_case is not None:
                    evidence_str = (
                        f"{entry.message} | Correlated directly with test "
                        f"'{matched_case.test_name}' "
                        f"via X-Correlation-ID={matched_case.correlation_id}"
                    )
                else:
                    evidence_str = entry.message

                _add(
                    finding_id=f"S1-LOG-ERR-{idx}",
                    sig_type=SilentSignalType.S1_LOG_ERROR,
                    v_state=VerificationState.SUSPECTED_BEHAVIOUR,
                    mod=inferred_mod,
                    endpoint="app.log",
                    desc="Application log recorded ERROR/EXCEPTION during 2xx API responses",
                    ev=evidence_str,
                    action="Inspect server logs around transaction timestamp for swallowed exceptions.",
                    details={
                        "log_level": entry.level,
                        "timestamp": entry.raw_timestamp,
                    },
                )

        # =====================================================================
        # Signal S2: Failed Dependency Health Probe (FR-40)
        # =====================================================================
        if health_probe_payload is not None:
            s2_module = module or "health"
            payload_dict: dict[str, Any] | None = None
            if isinstance(health_probe_payload, HttpResponse):
                if health_probe_payload.status_code >= 400:
                    code = health_probe_payload.status_code
                    _add(
                        finding_id="S2-HEALTH-HTTP-ERR",
                        sig_type=SilentSignalType.S2_DEPENDENCY_PROBE,
                        v_state=VerificationState.SUSPECTED_BEHAVIOUR,
                        mod=s2_module,
                        endpoint="/health",
                        desc=f"Health probe returned HTTP {code}",
                        ev=f"HTTP status {code}: {health_probe_payload.body}",
                        action="Check target application availability and infrastructure logs.",
                    )
                try:
                    parsed_json = health_probe_payload.json()
                    if isinstance(parsed_json, dict):
                        payload_dict = parsed_json
                except Exception:
                    pass
            elif isinstance(health_probe_payload, dict):
                payload_dict = health_probe_payload

            if payload_dict is not None:
                top_status = str(payload_dict.get("status", "")).strip().upper()
                if top_status in {"DOWN", "DEGRADED", "UNHEALTHY", "ERROR", "FAIL"}:
                    _add(
                        finding_id="S2-HEALTH-DEGRADED",
                        sig_type=SilentSignalType.S2_DEPENDENCY_PROBE,
                        v_state=VerificationState.SUSPECTED_BEHAVIOUR,
                        mod=s2_module,
                        endpoint="/health",
                        desc=f"Health probe /health reported degraded '{top_status}'",
                        ev=f"status: '{top_status}'",
                        action="Verify backing dependencies before executing dependent tests.",
                    )

                deps = (
                    payload_dict.get("dependencies")
                    or payload_dict.get("services")
                    or payload_dict.get("checks")
                )
                if isinstance(deps, dict):
                    for dep_name, dep_status in deps.items():
                        dep_str = str(dep_status).strip().upper()
                        is_degraded = (
                            dep_str
                            in {"DOWN", "DEGRADED", "UNHEALTHY", "FAIL", "FALSE"}
                            or dep_status is False
                        )
                        if is_degraded:
                            _add(
                                finding_id=f"S2-DEP-{dep_name}",
                                sig_type=SilentSignalType.S2_DEPENDENCY_PROBE,
                                v_state=VerificationState.SUSPECTED_BEHAVIOUR,
                                mod=s2_module,
                                endpoint=f"/health:{dep_name}",
                                desc=f"Health probe reported '{dep_name}' is {dep_status}",
                                ev=f"Dependency '{dep_name}': {dep_status}",
                                action=f"Investigate connectivity or credentials for '{dep_name}'.",
                            )

        # =====================================================================
        # Signal S3: Latency Drift vs. Baseline (FR-41 / Task 23)
        # =====================================================================
        if effective_run is not None and baseline_run is not None:
            analyzer = latency_drift_analyzer or LatencyDriftAnalyzer(
                tolerance_factor=self._latency_tolerance_factor,
                min_samples_threshold=self._min_samples_threshold,
                min_abs_drift_ms=self._min_abs_drift_ms,
                strict_environment_match=self._strict_environment_match,
            )
            drift_report = analyzer.compare_runs(
                baseline_run=baseline_run, current_run=effective_run
            )
            latency_drift_report_dict = drift_report.to_dict()

            for comp in drift_report.comparisons:
                if comp.verdict == DriftVerdict.REGRESSION_DETECTED:
                    v_state = (
                        VerificationState.VERIFIED_OBSERVATION
                        if comp.confidence == "HIGH"
                        else VerificationState.SUSPECTED_BEHAVIOUR
                    )
                    _add(
                        finding_id=f"S3-REGRESSION-{comp.operation}",
                        sig_type=SilentSignalType.S3_LATENCY_DRIFT,
                        v_state=v_state,
                        mod=module or comp.operation,
                        endpoint=comp.route_template or comp.operation,
                        desc=comp.details,
                        ev=(
                            f"Baseline median: {comp.baseline_median_ms} ms, "
                            f"Current median: {comp.current_median_ms} ms "
                            f"(+{comp.rel_change_pct}%, +{comp.abs_change_ms} ms)"
                        ),
                        action="Investigate database indexing, backend compute latency, or network saturation.",
                        details=comp.to_dict(),
                    )
                elif comp.verdict == DriftVerdict.INSUFFICIENT_DATA:
                    _add(
                        finding_id=f"S3-INSUFFICIENT-{comp.operation}",
                        sig_type=SilentSignalType.S3_LATENCY_DRIFT,
                        v_state=VerificationState.INCONCLUSIVE,
                        mod=module or comp.operation,
                        endpoint=comp.route_template or comp.operation,
                        desc=comp.details,
                        ev=f"Sample counts: baseline={comp.baseline_samples}, current={comp.current_samples}",
                        action="Gather at least 5 comparable requests before evaluating latency drift.",
                        details=comp.to_dict(),
                    )
                elif comp.verdict == DriftVerdict.INCOMPATIBLE_CONTEXT:
                    _add(
                        finding_id=f"S3-INCOMPATIBLE-{comp.operation}",
                        sig_type=SilentSignalType.S3_LATENCY_DRIFT,
                        v_state=VerificationState.INCONCLUSIVE,
                        mod=module or comp.operation,
                        endpoint=comp.route_template or comp.operation,
                        desc=comp.details,
                        ev=f"Baseline env: {comp.baseline_env}, Current env: {comp.current_env}",
                        action="Compare performance only across identical deployment environments.",
                        details=comp.to_dict(),
                    )

        elif effective_run is not None and baseline_p95_by_module is not None:
            # Legacy S3 calculation path
            cases_by_module: dict[str, list[float]] = {}
            for case in effective_run.results:
                if (
                    case.status_code is not None
                    and 200 <= case.status_code < 300
                    and case.elapsed_ms is not None
                ):
                    cases_by_module.setdefault(case.module, []).append(case.elapsed_ms)

            for mod_name, lats in cases_by_module.items():
                base_val: float | None = None
                if isinstance(baseline_p95_by_module, (int, float)):
                    base_val = float(baseline_p95_by_module)
                elif isinstance(baseline_p95_by_module, Mapping):
                    base_val = baseline_p95_by_module.get(
                        mod_name
                    ) or baseline_p95_by_module.get(mod_name.lower())

                if base_val is not None and base_val > 0 and lats:
                    sorted_lats = sorted(lats)
                    p95_idx = int(math.ceil(0.95 * len(sorted_lats))) - 1
                    p95_idx = max(0, min(p95_idx, len(sorted_lats) - 1))
                    p95_ms = round(sorted_lats[p95_idx], 2)
                    tolerated_limit = base_val * self._latency_tolerance_factor

                    if p95_ms > tolerated_limit:
                        _add(
                            finding_id=f"S3-LEGACY-{mod_name}",
                            sig_type=SilentSignalType.S3_LATENCY_DRIFT,
                            v_state=VerificationState.SUSPECTED_BEHAVIOUR,
                            mod=mod_name,
                            endpoint=mod_name,
                            desc=(
                                f"Module '{mod_name}' p95 latency {p95_ms:.1f} ms "
                                f"exceeded limit ({tolerated_limit:.1f} ms)"
                            ),
                            ev=(
                                f"Module '{mod_name}' p95 latency {p95_ms:.1f} ms "
                                f"exceeded baseline {base_val:.1f} ms "
                                f"(limit: {tolerated_limit:.1f} ms)"
                            ),
                            action="Optimize backend query patterns or caching for this module.",
                        )

        # =====================================================================
        # Signal S4: Suspicious 2xx Responses (Headers & Bodies) (FR-42)
        # =====================================================================
        if effective_run is not None:
            for case in effective_run.results:
                if case.status_code is not None and 200 <= case.status_code < 300:
                    # Respect HTTP 204 No Content (explicitly legal empty body)
                    if case.status_code == 204:
                        continue

                    # 1. Warning / Deprecation Headers
                    for h_name, h_val in case.response_headers.items():
                        if h_name.lower() in _WARNING_HEADERS:
                            redacted_h = redact_sensitive_text(str(h_val))
                            _add(
                                finding_id=f"S4-HDR-{case.test_name}-{h_name}",
                                sig_type=SilentSignalType.S4_SUSPICIOUS_2XX,
                                v_state=VerificationState.SUSPECTED_BEHAVIOUR,
                                mod=case.module,
                                endpoint=f"{case.method} {case.url}",
                                desc=f"2xx response returned suspicious/warning header '{h_name}'",
                                ev=f"{h_name}: {redacted_h}",
                                action="Check deprecation notices or fallback mode activation.",
                            )

                    # 2. Error-Like Body in 2xx
                    body_text = case.response_body
                    if body_text:
                        try:
                            body_dict = json.loads(body_text)
                            if isinstance(body_dict, dict):
                                self._inspect_2xx_body(
                                    body_dict=body_dict,
                                    module=case.module,
                                    endpoint=f"{case.method} {case.url}",
                                    test_name=case.test_name,
                                    add_fn=_add,
                                )
                        except Exception:
                            pass

        if raw_responses:
            for mod_resp, ep_name, resp_obj in raw_responses:
                if 200 <= resp_obj.status_code < 300:
                    if resp_obj.status_code == 204:
                        continue
                    for h_name, h_val in resp_obj.headers.items():
                        if h_name.lower() in _WARNING_HEADERS:
                            redacted_h = redact_sensitive_text(str(h_val))
                            _add(
                                finding_id=f"S4-RAW-HDR-{ep_name}-{h_name}",
                                sig_type=SilentSignalType.S4_SUSPICIOUS_2XX,
                                v_state=VerificationState.SUSPECTED_BEHAVIOUR,
                                mod=mod_resp,
                                endpoint=ep_name,
                                desc=f"2xx response returned suspicious/warning header '{h_name}'",
                                ev=f"{h_name}: {redacted_h}",
                                action="Check deprecation notices or fallback mode activation.",
                            )
                    try:
                        raw_json = resp_obj.json()
                        if isinstance(raw_json, dict):
                            self._inspect_2xx_body(
                                body_dict=raw_json,
                                module=mod_resp,
                                endpoint=ep_name,
                                test_name=ep_name,
                                add_fn=_add,
                            )
                    except Exception:
                        pass

        # =====================================================================
        # Signal S5: Side-Effect Mismatch & Unverified Writes (Task 23)
        # =====================================================================
        if effective_run is not None:
            self._evaluate_side_effects(
                effective_run=effective_run,
                workflow_result=workflow_result,
                add_fn=_add,
            )

        # =====================================================================
        # Signal S6 & S7: Contract Contradiction & Invariant Violations
        # =====================================================================
        if effective_run is not None:
            self._evaluate_contract_and_invariants(
                effective_run=effective_run,
                contract=contract,
                approved_invariants=approved_invariants,
                add_fn=_add,
            )

        # =====================================================================
        # Signal S8: Inconsistent Repeated Observations (Task 23)
        # =====================================================================
        if effective_run is not None:
            self._evaluate_repeated_observations(
                effective_run=effective_run,
                add_fn=_add,
            )

        # Module filtering
        if module:
            target_mod = module.strip().lower()
            filtered_signals = [s for s in signals if s.module.lower() == target_mod]
            filtered_findings = [f for f in findings if f.module.lower() == target_mod]
        else:
            filtered_signals = list(signals)
            filtered_findings = list(findings)

        # Count categorizations
        verified_mismatches_count = sum(
            1
            for f in filtered_findings
            if f.verification_state == VerificationState.VERIFIED_OBSERVATION
        )
        suspicious_count = sum(
            1
            for f in filtered_findings
            if f.verification_state == VerificationState.SUSPECTED_BEHAVIOUR
        )
        untested_effects_count = sum(
            1
            for f in filtered_findings
            if f.verification_state
            in {VerificationState.UNVERIFIED_EFFECT, VerificationState.NOT_TESTED}
        )
        inconclusive_count = sum(
            1
            for f in filtered_findings
            if f.verification_state == VerificationState.INCONCLUSIVE
        )

        # True silent failures require verified defects or evidence of suspicious behavior
        has_silent_failures = verified_mismatches_count > 0 or suspicious_count > 0

        if verified_mismatches_count > 0:
            verdict_banner = (
                f"SILENT FAILURE DETECTED: {verified_mismatches_count} "
                "verified defect/contradiction(s) found"
            )
        elif suspicious_count > 0:
            verdict_banner = (
                "PASS WITH WARNINGS: APIs healthy, internal issue suspected"
            )
        elif untested_effects_count > 0:
            verdict_banner = (
                "PASS: All tests succeeded (side-effects unverified / not tested)"
            )
        else:
            verdict_banner = "NO SILENT FAILURES DETECTED"

        counts_by_type: dict[str, int] = {t.value: 0 for t in SilentSignalType}
        for s in filtered_signals:
            if s.signal_type.value in counts_by_type:
                counts_by_type[s.signal_type.value] += 1

        return SilentFailureReport(
            evaluated_at=self._clock(),
            has_silent_failures=has_silent_failures,
            verdict_banner=verdict_banner,
            total_signals=len(filtered_signals),
            counts_by_type=counts_by_type,
            signals=filtered_signals,
            findings=filtered_findings,
            untested_effects_count=untested_effects_count,
            verified_mismatches_count=verified_mismatches_count,
            suspicious_count=suspicious_count,
            inconclusive_count=inconclusive_count,
            latency_drift_report=latency_drift_report_dict,
        )

    @staticmethod
    def _inspect_2xx_body(
        body_dict: dict[str, Any],
        module: str,
        endpoint: str,
        test_name: str,
        add_fn: Callable[..., None],
    ) -> None:
        """Inspect a 2xx JSON response body for error-like payload attributes."""
        # Top-level 'error' non-null / non-false / non-empty
        err_val = body_dict.get("error")
        is_err = (
            err_val is not None
            and err_val is not False
            and err_val != ""
            and err_val != []
        )
        if is_err:
            add_fn(
                finding_id=f"S4-BODY-ERR-{test_name}",
                sig_type=SilentSignalType.S4_SUSPICIOUS_2XX,
                v_state=VerificationState.SUSPECTED_BEHAVIOUR,
                mod=module,
                endpoint=endpoint,
                desc="2xx response contains non-empty 'error' field in JSON body",
                ev=redact_sensitive_text(json.dumps(body_dict)),
                action="Verify why error was swallowed into a 2xx status code.",
            )
            return

        if body_dict.get("exception"):
            add_fn(
                finding_id=f"S4-BODY-EXC-{test_name}",
                sig_type=SilentSignalType.S4_SUSPICIOUS_2XX,
                v_state=VerificationState.SUSPECTED_BEHAVIOUR,
                mod=module,
                endpoint=endpoint,
                desc="2xx response contains 'exception' field in JSON body",
                ev=redact_sensitive_text(json.dumps(body_dict)),
                action="Inspect application exception handlers swallowing fatal errors.",
            )
            return

        status_field = str(body_dict.get("status", "")).strip().lower()
        if status_field in {"error", "failed", "failure"}:
            add_fn(
                finding_id=f"S4-BODY-STATUS-{test_name}",
                sig_type=SilentSignalType.S4_SUSPICIOUS_2XX,
                v_state=VerificationState.SUSPECTED_BEHAVIOUR,
                mod=module,
                endpoint=endpoint,
                desc=f"2xx response contains status '{status_field}' in body",
                ev=redact_sensitive_text(json.dumps(body_dict)),
                action="Align HTTP response status code with payload body status.",
            )
            return

        if body_dict.get("synced") is False:
            add_fn(
                finding_id=f"S4-BODY-SYNCED-{test_name}",
                sig_type=SilentSignalType.S4_SUSPICIOUS_2XX,
                v_state=VerificationState.SUSPECTED_BEHAVIOUR,
                mod=module,
                endpoint=endpoint,
                desc="2xx response contains 'synced: false' in body indicating swallowed sync failure",
                ev=redact_sensitive_text(json.dumps(body_dict)),
                action="Check synchronization retry workers or asynchronous billing queue.",
            )

    @staticmethod
    def _evaluate_side_effects(
        effective_run: RunResult,
        workflow_result: Any | None,
        add_fn: Callable[..., None],
    ) -> None:
        """Evaluate whether claimed 2xx mutations actually changed target state."""
        _ = workflow_result
        mutating_methods = {"POST", "PUT", "PATCH", "DELETE"}
        cases = effective_run.results

        # Check if the entire test run is read-only (e.g. StockPile baseline)
        executed_mutations = [c for c in cases if c.method.upper() in mutating_methods]
        if not executed_mutations:
            add_fn(
                finding_id="S5-READONLY-SUITE",
                sig_type=SilentSignalType.S5_UNVERIFIED_EFFECT,
                v_state=VerificationState.NOT_TESTED,
                mod="suite",
                endpoint="read-only suite",
                desc="Side-effect persistence verification was not tested (read-only execution).",
                ev="All executed requests were read-only; no database mutations were performed.",
                action="Execute stateful mutation workflows (POST/PUT/DELETE followed by verification GET).",
            )
            return

        # Map cases to search for follow-up verification read requests
        for idx, mut_case in enumerate(cases):
            m_method = mut_case.method.upper()
            if m_method not in mutating_methods:
                continue

            # If mutation failed, was skipped, or blocked, skip S5
            if mut_case.status_code is None or not (200 <= mut_case.status_code < 300):
                continue

            # 202 Accepted represents asynchronous processing (eventual consistency)
            is_async = mut_case.status_code == 202

            # Find subsequent cases in sequence
            subsequent_cases = cases[idx + 1 :]

            # Extract resource path base (e.g., /api/products/101 -> /api/products)
            mut_path = ""
            try:
                mut_path = urlparse(mut_case.url).path
            except Exception:
                mut_path = mut_case.url

            # Search for a subsequent GET operation targeting this resource or collection
            matching_followup: CaseResult | None = None
            for sub in subsequent_cases:
                if sub.method.upper() != "GET":
                    continue
                sub_path = ""
                try:
                    sub_path = urlparse(sub.url).path
                except Exception:
                    sub_path = sub.url

                # Check path containment or ID matching
                if (
                    sub_path == mut_path
                    or (
                        mut_path
                        and sub_path
                        and (mut_path in sub_path or sub_path in mut_path)
                    )
                    or (
                        mut_case.module
                        and sub.module
                        and mut_case.module.lower() == sub.module.lower()
                    )
                ):
                    matching_followup = sub
                    break

            if matching_followup is None:
                # No verification step was executed downstream
                add_fn(
                    finding_id=f"S5-UNVERIFIED-{mut_case.test_name}",
                    sig_type=SilentSignalType.S5_UNVERIFIED_EFFECT,
                    v_state=VerificationState.UNVERIFIED_EFFECT,
                    mod=mut_case.module,
                    endpoint=f"{mut_case.method} {mut_case.url}",
                    desc=(
                        "Mutating request returned 2xx but side effect was not "
                        "verified by any downstream read operation."
                    ),
                    ev=(
                        f"{mut_case.method} {mut_case.url} returned HTTP {mut_case.status_code}; "
                        "test sequence completed without a subsequent GET verification step."
                    ),
                    action="Add a follow-up GET request to verify persistence in storage.",
                    details={"mutating_status": mut_case.status_code},
                )
                continue

            # If follow-up read exists:
            if is_async:
                # 202 Accepted: asynchronous processing in progress; not a defect
                add_fn(
                    finding_id=f"S5-ASYNC-{mut_case.test_name}",
                    sig_type=SilentSignalType.S5_UNVERIFIED_EFFECT,
                    v_state=VerificationState.INCONCLUSIVE,
                    mod=mut_case.module,
                    endpoint=f"{mut_case.method} {mut_case.url}",
                    desc="Asynchronous 202 Accepted request outcome is pending or eventually consistent.",
                    ev=(
                        f"Mutating request returned HTTP 202 Accepted; follow-up GET "
                        f"returned HTTP {matching_followup.status_code}."
                    ),
                    action="Allow event polling or delay for asynchronous eventual consistency.",
                    details={
                        "mutating_status": 202,
                        "followup_status": matching_followup.status_code,
                    },
                )
            elif m_method == "DELETE":
                # DELETE: Expect 404 or empty on follow-up GET
                if matching_followup.status_code == 200:
                    add_fn(
                        finding_id=f"S5-DELETE-MISMATCH-{mut_case.test_name}",
                        sig_type=SilentSignalType.S5_UNVERIFIED_EFFECT,
                        v_state=VerificationState.VERIFIED_OBSERVATION,
                        mod=mut_case.module,
                        endpoint=f"{mut_case.method} {mut_case.url}",
                        desc="Resource still exists after claimed DELETE success (side-effect failure)",
                        ev=(
                            f"DELETE returned HTTP {mut_case.status_code} but follow-up "
                            f"GET {matching_followup.url} returned HTTP 200 OK."
                        ),
                        action="Investigate delete handler transaction commit and cache invalidation.",
                        details={
                            "mutating_status": mut_case.status_code,
                            "followup_status": matching_followup.status_code,
                        },
                    )
            else:
                # POST, PUT, PATCH: Expect 200 on follow-up GET
                if matching_followup.status_code in {404, 410}:
                    add_fn(
                        finding_id=f"S5-WRITE-MISMATCH-{mut_case.test_name}",
                        sig_type=SilentSignalType.S5_UNVERIFIED_EFFECT,
                        v_state=VerificationState.VERIFIED_OBSERVATION,
                        mod=mut_case.module,
                        endpoint=f"{mut_case.method} {mut_case.url}",
                        desc="Mutating request returned 2xx but subsequent read returned 404 / entity missing",
                        ev=(
                            f"{mut_case.method} {mut_case.url} returned HTTP {mut_case.status_code}, "
                            f"but subsequent {matching_followup.method} {matching_followup.url} "
                            f"returned HTTP {matching_followup.status_code}."
                        ),
                        action="Verify database write transaction commit and replication lag.",
                        details={
                            "mutating_status": mut_case.status_code,
                            "followup_status": matching_followup.status_code,
                        },
                    )

    @staticmethod
    def _evaluate_contract_and_invariants(
        effective_run: RunResult,
        contract: Any | None,
        approved_invariants: list[dict[str, Any]] | None,
        add_fn: Callable[..., None],
    ) -> None:
        """Evaluate contract contradictions (S6) and business invariant violations (S7)."""
        _ = contract
        for case in effective_run.results:
            if case.status_code is None or not (200 <= case.status_code < 300):
                continue

            # S6: 2xx with failed assertions in case.assertion_results
            failed_assertions = [
                a for a in case.assertion_results if a.get("passed") is False
            ]
            if failed_assertions:
                failed_msgs = [
                    str(a.get("message") or a.get("rule_type") or "Assertion failed")
                    for a in failed_assertions
                ]
                add_fn(
                    finding_id=f"S6-ASSERT-FAIL-{case.test_name}",
                    sig_type=SilentSignalType.S6_CONTRACT_CONTRADICTION,
                    v_state=VerificationState.VERIFIED_OBSERVATION,
                    mod=case.module,
                    endpoint=f"{case.method} {case.url}",
                    desc="2xx response contradicts contract assertions or schema requirements",
                    ev=redact_sensitive_text(
                        f"Status {case.status_code} received but assertions failed: {'; '.join(failed_msgs)}"
                    ),
                    action="Reconcile OpenAPI response schema or fix application serialization.",
                    details={"failed_assertions": failed_assertions},
                )

            # S7: Business invariant checks
            if approved_invariants and case.response_body:
                try:
                    data = json.loads(case.response_body)
                    if isinstance(data, dict):
                        for inv in approved_invariants:
                            f_name = inv.get("field")
                            op = inv.get("operator", "eq")
                            target_val = inv.get("value")
                            inv_name = inv.get("name", f"Invariant {f_name}")

                            if f_name in data:
                                actual_val = data[f_name]
                                violated = False
                                if op == "gte" and isinstance(actual_val, (int, float)):
                                    violated = actual_val < target_val
                                elif op == "gt" and isinstance(
                                    actual_val, (int, float)
                                ):
                                    violated = actual_val <= target_val
                                elif op == "neq":
                                    violated = actual_val == target_val
                                elif op == "eq":
                                    violated = actual_val != target_val

                                if violated:
                                    add_fn(
                                        finding_id=f"S7-INVARIANT-{case.test_name}-{f_name}",
                                        sig_type=SilentSignalType.S7_INVARIANT_VIOLATION,
                                        v_state=VerificationState.VERIFIED_OBSERVATION,
                                        mod=case.module,
                                        endpoint=f"{case.method} {case.url}",
                                        desc=f"2xx response violated business invariant: {inv_name}",
                                        ev=(
                                            f"Field '{f_name}' value '{actual_val}' violated "
                                            f"rule '{op} {target_val}'"
                                        ),
                                        action=f"Verify domain integrity logic for invariant '{inv_name}'.",
                                        details={
                                            "invariant": inv,
                                            "actual_value": actual_val,
                                        },
                                    )
                except Exception:
                    pass

    @staticmethod
    def _evaluate_repeated_observations(
        effective_run: RunResult,
        add_fn: Callable[..., None],
    ) -> None:
        """Detect inconsistent responses across identical idempotent requests (S8)."""
        get_cases = [
            c
            for c in effective_run.results
            if c.method.upper() == "GET"
            and c.status_code is not None
            and 200 <= c.status_code < 300
            and c.response_body
        ]

        by_url: dict[str, list[CaseResult]] = defaultdict(list)
        for c in get_cases:
            by_url[c.url].append(c)

        for url, items in by_url.items():
            if len(items) >= 2:
                # Compare bodies of identical calls
                first_body = items[0].response_body.strip()
                inconsistent = [
                    item
                    for item in items[1:]
                    if item.response_body.strip() != first_body
                ]
                if inconsistent:
                    add_fn(
                        finding_id=f"S8-INCONSISTENT-{url}",
                        sig_type=SilentSignalType.S8_INCONSISTENT_OBSERVATION,
                        v_state=VerificationState.SUSPECTED_BEHAVIOUR,
                        mod=items[0].module,
                        endpoint=f"GET {url}",
                        desc="Identical idempotent GET requests returned inconsistent responses without intervening mutations",
                        ev=(
                            f"GET {url} was called {len(items)} times with contradictory "
                            f"response bodies."
                        ),
                        action="Inspect caching tiers, read-replica replication lag, or nondeterministic sorting.",
                        details={"sample_count": len(items)},
                    )


__all__ = [
    "MAX_LOG_FILE_BYTES",
    "MAX_LOG_LINES",
    "AppLogEntry",
    "SilentFailureFinding",
    "SilentFailureReport",
    "SilentFailureService",
    "SilentFailureSignal",
    "SilentSignalType",
    "VerificationState",
    "load_app_log",
    "load_run_file",
    "parse_app_log",
    "parse_run_data",
]
