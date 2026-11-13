"""Silent Failure Detector and Diagnostic Signal Analysis Service.

Implements Module M10: detects silent failures across Signals S1 (app log errors),
S2 (failed dependency health probes), S3 (latency drift vs. saved baseline), and
S4 (2xx responses with error-like bodies or warning headers), redacting credentials.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from apiscope.core.exceptions import ConfigurationError
from apiscope.core.logging_config import redact_sensitive_text
from apiscope.core.models import HttpResponse
from apiscope.core.results import CaseResult, RunResult
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


class SilentSignalType(StrEnum):
    """Enumeration of recognized silent failure signal categories (S1-S4)."""

    __test__ = False

    S1_LOG_ERROR = "S1_LOG_ERROR"
    S2_DEPENDENCY_PROBE = "S2_DEPENDENCY_PROBE"
    S3_LATENCY_DRIFT = "S3_LATENCY_DRIFT"
    S4_SUSPICIOUS_2XX = "S4_SUSPICIOUS_2XX"


@dataclass(frozen=True)
class AppLogEntry:
    """Normalized application log line record with redacted message content."""

    __test__ = False

    timestamp: datetime | None
    raw_timestamp: str | None
    level: str
    module: str | None
    message: str


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

    def filter_by_module(self, module: str) -> list[SilentFailureSignal]:
        """Return signals matching a specific module name (case-insensitive)."""
        target = module.strip().lower()
        return [s for s in self.signals if s.module.lower() == target]

    def to_dict(self) -> dict[str, Any]:
        """Convert silent failure report to a 100% JSON-serializable dictionary."""
        return {
            "evaluated_at": self.evaluated_at.isoformat(),
            "has_silent_failures": self.has_silent_failures,
            "verdict_banner": self.verdict_banner,
            "total_signals": self.total_signals,
            "counts_by_type": dict(self.counts_by_type),
            "signals": [s.to_dict() for s in self.signals],
        }


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


class SilentFailureService:
    """Application service for detecting silent failures across Signals S1-S4."""

    def __init__(
        self,
        clock: Callable[[], datetime] | None = None,
        latency_tolerance_factor: float = 1.5,
    ) -> None:
        """Initialize SilentFailureService.

        Args:
            clock: Optional clock callable returning timezone-aware UTC datetime.
            latency_tolerance_factor: Tolerance multiplier for p95 latency drift.
        """
        self._clock = clock or (lambda: datetime.now(UTC))
        self._latency_tolerance_factor = latency_tolerance_factor

    def analyze(
        self,
        run_result: RunResult | None = None,
        app_log_entries: Sequence[AppLogEntry] | None = None,
        health_probe_payload: dict[str, Any] | HttpResponse | None = None,
        baseline_p95_by_module: Mapping[str, float] | float | None = None,
        raw_responses: Sequence[tuple[str, str, HttpResponse]] | None = None,
        module: str | None = None,
    ) -> SilentFailureReport:
        """Analyze test results, logs, and health probes to detect silent failures.

        Args:
            run_result: Optional executed test run result.
            app_log_entries: Optional application log entries for Signal S1.
            health_probe_payload: Optional health check JSON or HttpResponse for S2.
            baseline_p95_by_module: Optional baseline p95 ms for Signal S3.
            raw_responses: Optional raw HTTP responses (module, name, HttpResponse).
            module: Optional module filter.

        Returns:
            SilentFailureReport containing diagnosed signals and banner verdict.
        """
        signals: list[SilentFailureSignal] = []

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
            if run_result is not None:
                corr_lookup = {
                    c.correlation_id: c for c in run_result.results if c.correlation_id
                }

            correlated_entries: list[AppLogEntry] = []
            if run_result is not None:
                win_start = run_result.started_at - timedelta(seconds=60)
                win_end = run_result.finished_at + timedelta(seconds=60)
                matched = [
                    e
                    for e in error_entries
                    if (corr_lookup and any(cid in e.message for cid in corr_lookup))
                    or (e.timestamp is not None and win_start <= e.timestamp <= win_end)
                ]
                correlated_entries = matched or list(error_entries)
            else:
                correlated_entries = list(error_entries)

            for entry in correlated_entries:
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

                signals.append(
                    SilentFailureSignal(
                        signal_type=SilentSignalType.S1_LOG_ERROR,
                        module=inferred_mod,
                        endpoint_or_source="app.log",
                        description=(
                            "Application log recorded ERROR/EXCEPTION during "
                            "2xx API responses"
                        ),
                        evidence=evidence_str,
                    )
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
                    signals.append(
                        SilentFailureSignal(
                            signal_type=SilentSignalType.S2_DEPENDENCY_PROBE,
                            module=s2_module,
                            endpoint_or_source="/health",
                            description=f"Health probe returned HTTP {code}",
                            evidence=(
                                f"HTTP status {code}: {health_probe_payload.body}"
                            ),
                        )
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
                    signals.append(
                        SilentFailureSignal(
                            signal_type=SilentSignalType.S2_DEPENDENCY_PROBE,
                            module=s2_module,
                            endpoint_or_source="/health",
                            description=(
                                f"Health probe /health reported degraded '{top_status}'"
                            ),
                            evidence=f"status: '{top_status}'",
                        )
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
                            signals.append(
                                SilentFailureSignal(
                                    signal_type=SilentSignalType.S2_DEPENDENCY_PROBE,
                                    module=s2_module,
                                    endpoint_or_source=f"/health:{dep_name}",
                                    description=(
                                        f"Health probe reported '{dep_name}' is "
                                        f"{dep_status}"
                                    ),
                                    evidence=f"Dependency '{dep_name}': {dep_status}",
                                )
                            )

        # =====================================================================
        # Signal S3: Latency Drift vs. Saved Baseline (FR-41)
        # =====================================================================
        if run_result is not None and baseline_p95_by_module is not None:
            cases_by_module: dict[str, list[float]] = {}
            for case in run_result.results:
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
                        signals.append(
                            SilentFailureSignal(
                                signal_type=SilentSignalType.S3_LATENCY_DRIFT,
                                module=mod_name,
                                endpoint_or_source=mod_name,
                                description=(
                                    f"Module '{mod_name}' p95 latency {p95_ms:.1f} ms "
                                    f"exceeded limit ({tolerated_limit:.1f} ms)"
                                ),
                                evidence=(
                                    f"Module '{mod_name}' p95 latency {p95_ms:.1f} ms "
                                    f"exceeded baseline {base_val:.1f} ms "
                                    f"(limit: {tolerated_limit:.1f} ms)"
                                ),
                            )
                        )

        # =====================================================================
        # Signal S4: Suspicious 2xx Responses (Headers & Bodies) (FR-42)
        # =====================================================================
        if run_result is not None:
            for case in run_result.results:
                if case.status_code is not None and 200 <= case.status_code < 300:
                    # 1. Warning / Deprecation Headers
                    for h_name, h_val in case.response_headers.items():
                        if h_name.lower() in _WARNING_HEADERS:
                            redacted_h = redact_sensitive_text(str(h_val))
                            signals.append(
                                SilentFailureSignal(
                                    signal_type=SilentSignalType.S4_SUSPICIOUS_2XX,
                                    module=case.module,
                                    endpoint_or_source=f"{case.method} {case.url}",
                                    description=(
                                        f"2xx response returned suspicious/warning "
                                        f"header '{h_name}'"
                                    ),
                                    evidence=f"{h_name}: {redacted_h}",
                                )
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
                                    signals=signals,
                                )
                        except Exception:
                            pass

        if raw_responses:
            for mod_resp, ep_name, resp_obj in raw_responses:
                if 200 <= resp_obj.status_code < 300:
                    for h_name, h_val in resp_obj.headers.items():
                        if h_name.lower() in _WARNING_HEADERS:
                            redacted_h = redact_sensitive_text(str(h_val))
                            signals.append(
                                SilentFailureSignal(
                                    signal_type=SilentSignalType.S4_SUSPICIOUS_2XX,
                                    module=mod_resp,
                                    endpoint_or_source=ep_name,
                                    description=(
                                        f"2xx response returned suspicious/warning "
                                        f"header '{h_name}'"
                                    ),
                                    evidence=f"{h_name}: {redacted_h}",
                                )
                            )
                    try:
                        raw_json = resp_obj.json()
                        if isinstance(raw_json, dict):
                            self._inspect_2xx_body(
                                body_dict=raw_json,
                                module=mod_resp,
                                endpoint=ep_name,
                                signals=signals,
                            )
                    except Exception:
                        pass

        # Module filtering
        if module:
            target_mod = module.strip().lower()
            filtered_signals = [s for s in signals if s.module.lower() == target_mod]
        else:
            filtered_signals = list(signals)

        has_silent_failures = len(filtered_signals) > 0
        verdict_banner = (
            "PASS WITH WARNINGS: APIs healthy, internal issue suspected"
            if has_silent_failures
            else "NO SILENT FAILURES DETECTED"
        )

        counts_by_type: dict[str, int] = {t.value: 0 for t in SilentSignalType}
        for s in filtered_signals:
            counts_by_type[s.signal_type.value] += 1

        return SilentFailureReport(
            evaluated_at=self._clock(),
            has_silent_failures=has_silent_failures,
            verdict_banner=verdict_banner,
            total_signals=len(filtered_signals),
            counts_by_type=counts_by_type,
            signals=filtered_signals,
        )

    @staticmethod
    def _inspect_2xx_body(
        body_dict: dict[str, Any],
        module: str,
        endpoint: str,
        signals: list[SilentFailureSignal],
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
            signals.append(
                SilentFailureSignal(
                    signal_type=SilentSignalType.S4_SUSPICIOUS_2XX,
                    module=module,
                    endpoint_or_source=endpoint,
                    description=(
                        "2xx response contains non-empty 'error' field in JSON body"
                    ),
                    evidence=redact_sensitive_text(json.dumps(body_dict)),
                )
            )
            return

        if body_dict.get("exception"):
            signals.append(
                SilentFailureSignal(
                    signal_type=SilentSignalType.S4_SUSPICIOUS_2XX,
                    module=module,
                    endpoint_or_source=endpoint,
                    description="2xx response contains 'exception' field in JSON body",
                    evidence=redact_sensitive_text(json.dumps(body_dict)),
                )
            )
            return

        status_field = str(body_dict.get("status", "")).strip().lower()
        if status_field in {"error", "failed", "failure"}:
            signals.append(
                SilentFailureSignal(
                    signal_type=SilentSignalType.S4_SUSPICIOUS_2XX,
                    module=module,
                    endpoint_or_source=endpoint,
                    description=(
                        f"2xx response contains status '{status_field}' in body"
                    ),
                    evidence=redact_sensitive_text(json.dumps(body_dict)),
                )
            )
            return

        if body_dict.get("synced") is False:
            signals.append(
                SilentFailureSignal(
                    signal_type=SilentSignalType.S4_SUSPICIOUS_2XX,
                    module=module,
                    endpoint_or_source=endpoint,
                    description=(
                        "2xx response contains 'synced: false' in body indicating "
                        "swallowed sync failure"
                    ),
                    evidence=redact_sensitive_text(json.dumps(body_dict)),
                )
            )
