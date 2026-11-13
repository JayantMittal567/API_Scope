"""Zombie API Endpoint Detector and Access Log Analysis Service.

Implements Module M9: parses Common/Combined and JSON-lines access logs,
normalizes dynamic concrete paths, and identifies UNUSED, UNDOCUMENTED,
UNTESTED, and DEPRECATED-BUT-CALLED zombie endpoints.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from apiscope.analysis.path_normalizer import (
    extract_url_path,
    match_path_to_spec,
    normalize_unmatched_path,
)
from apiscope.core.exceptions import ConfigurationError
from apiscope.core.results import RunResult
from apiscope.definitions.collection import TestCollection, validate_safe_path
from apiscope.generators.openapi_importer import OpenAPISpec

MAX_LOG_FILE_BYTES: int = 52_428_800  # 50 MB
MAX_LOG_LINES: int = 100_000

# Combined and Common Log Format: IP ident auth [ts] "METHOD /path..." status bytes
_COMBINED_LOG_REGEX = re.compile(
    r"""^\S+\s+\S+\s+\S+\s+\[([^\]]+)\]\s+"([A-Za-z]+)\s+([^"\s]+)[^"]*"\s+(\d{3})"""
)


class ZombieClass(StrEnum):
    """Canonical classification for zombie and anomalous API endpoints."""

    __test__ = False

    UNUSED = "UNUSED"
    UNDOCUMENTED = "UNDOCUMENTED"
    UNTESTED = "UNTESTED"
    DEPRECATED_BUT_CALLED = "DEPRECATED-BUT-CALLED"


@dataclass(frozen=True)
class AccessLogEntry:
    """Normalized record of a single HTTP request parsed from access logs."""

    __test__ = False

    method: str
    raw_path: str
    status_code: int
    timestamp: str | None = None


@dataclass(frozen=True)
class ZombieFinding:
    """Specific zombie endpoint finding diagnosed during log and spec analysis."""

    __test__ = False

    classification: ZombieClass
    method: str
    normalized_path: str
    module: str
    call_count: int
    last_seen: str | None
    details: str

    def to_dict(self) -> dict[str, Any]:
        """Convert finding into a serializable dictionary."""
        return {
            "classification": self.classification.value,
            "method": self.method,
            "normalized_path": self.normalized_path,
            "module": self.module,
            "call_count": self.call_count,
            "last_seen": self.last_seen,
            "details": self.details,
        }


@dataclass(frozen=True)
class ZombieReport:
    """Aggregated analysis report of zombie endpoints across a specification."""

    __test__ = False

    evaluated_at: datetime
    total_findings: int
    counts_by_class: dict[str, int]
    counts_by_module: dict[str, dict[str, int]]
    findings: list[ZombieFinding]

    def filter_by_module(self, module: str) -> list[ZombieFinding]:
        """Return findings matching a specific module name (case-insensitive)."""
        target = module.strip().lower()
        return [f for f in self.findings if f.module.lower() == target]

    def to_dict(self) -> dict[str, Any]:
        """Convert report to a 100% JSON-serializable dictionary."""
        return {
            "evaluated_at": self.evaluated_at.isoformat(),
            "total_findings": self.total_findings,
            "counts_by_class": dict(self.counts_by_class),
            "counts_by_module": {k: dict(v) for k, v in self.counts_by_module.items()},
            "findings": [f.to_dict() for f in self.findings],
        }


def parse_access_log(content: str) -> list[AccessLogEntry]:
    """Parse raw access log content into normalized AccessLogEntry objects.

    Supports Apache/Nginx Combined/Common Log Format and JSON Lines (.jsonl).
    Ignores test harness resets (/_test/reset) and empty/comment lines.

    Args:
        content: String content of the access log.

    Returns:
        List of parsed AccessLogEntry instances.

    Raises:
        ConfigurationError: If line limit is exceeded or no valid entries exist in
            non-empty log content.
    """
    raw_lines = content.splitlines()
    non_empty_lines = [line.strip() for line in raw_lines if line.strip()]

    if len(non_empty_lines) > MAX_LOG_LINES:
        raise ConfigurationError(
            f"Access log exceeds maximum limit of {MAX_LOG_LINES} lines."
        )

    entries: list[AccessLogEntry] = []
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
                    m_val = data.get("method") or data.get("http_method")
                    p_val = data.get("path") or data.get("url") or data.get("uri")
                    s_val = data.get("status") or data.get("status_code")
                    ts_val = data.get("timestamp") or data.get("time")

                    if m_val and p_val and s_val is not None:
                        clean_path = extract_url_path(str(p_val))
                        if clean_path == "/_test/reset":
                            continue
                        entries.append(
                            AccessLogEntry(
                                method=str(m_val).strip().upper(),
                                raw_path=clean_path,
                                status_code=int(s_val),
                                timestamp=str(ts_val).strip() if ts_val else None,
                            )
                        )
                        continue
            except Exception:
                pass

        # 2. Attempt Combined/Common Log Format regex
        match = _COMBINED_LOG_REGEX.match(line)
        if match:
            raw_ts = match.group(1).strip()
            method = match.group(2).strip().upper()
            raw_req_path = match.group(3).strip()
            status_code = int(match.group(4))

            clean_path = extract_url_path(raw_req_path)
            if clean_path == "/_test/reset":
                continue

            entries.append(
                AccessLogEntry(
                    method=method,
                    raw_path=clean_path,
                    status_code=status_code,
                    timestamp=raw_ts,
                )
            )

    if has_candidate_text and not entries:
        raise ConfigurationError("Access log contains no valid log entries")

    return entries


def load_access_log(
    file_path: str | Path, base_dir: Path | None = None
) -> list[AccessLogEntry]:
    """Safely load and parse an access log file from disk.

    Args:
        file_path: Path to the log file.
        base_dir: Optional root directory for directory traversal validation.

    Returns:
        List of parsed AccessLogEntry instances.

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
        raise ConfigurationError(f"Access log file not found: {file_path}")

    file_size = safe_path.stat().st_size
    if file_size > MAX_LOG_FILE_BYTES:
        raise ConfigurationError(
            f"Access log file exceeds maximum size of {MAX_LOG_FILE_BYTES} bytes."
        )

    try:
        content = safe_path.read_text(encoding="utf-8-sig")
    except Exception as err:
        raise ConfigurationError(
            f"Error reading access log file '{file_path}': {err}"
        ) from err

    return parse_access_log(content)


def _infer_module_from_path(normalized_path: str) -> str:
    """Infer module identifier from the first non-empty segment of a normalized path."""
    clean = normalized_path.strip("/")
    segments = clean.split("/") if clean else []
    for seg in segments:
        seg_lower = seg.lower()
        if seg_lower in ("api", "v1", "v2", "v3", "v4") or re.match(
            r"^v\d+$", seg_lower
        ):
            continue
        return seg_lower.replace("{", "").replace("}", "") or "default"
    return "default"


class ZombieService:
    """Application service for detecting unused, undocumented, and zombie endpoints."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        """Initialize ZombieService with an optional clock callable."""
        self._clock = clock or (lambda: datetime.now(UTC))

    def analyze(
        self,
        openapi_spec: OpenAPISpec,
        access_entries: Sequence[AccessLogEntry],
        run_result: RunResult | None = None,
        test_collection: TestCollection | None = None,
        module: str | None = None,
    ) -> ZombieReport:
        """Analyze access log entries and test results against an OpenAPI spec.

        Args:
            openapi_spec: Imported OpenAPI 3 specification.
            access_entries: Sequence of parsed AccessLogEntry items.
            run_result: Optional executed test RunResult for UNTESTED detection.
            test_collection: Optional TestCollection for UNTESTED detection.
            module: Optional module name filter.

        Returns:
            ZombieReport containing categorized findings and aggregate summaries.
        """
        spec_paths = sorted(
            set(openapi_spec.path_methods.keys())
            | {ep.path for ep in openapi_spec.endpoints}
        )

        call_counts: dict[tuple[str, str], int] = {}
        last_seen_ts: dict[tuple[str, str], str | None] = {}

        undocumented_counts: dict[tuple[str, str], int] = {}
        undocumented_last_seen: dict[tuple[str, str], str | None] = {}

        # 1. Match log calls to spec templates or classify as undocumented
        for entry in access_entries:
            clean_path = extract_url_path(entry.raw_path)
            matched_template = match_path_to_spec(clean_path, spec_paths)
            method = entry.method.upper()

            spec_methods = [
                m.upper() for m in openapi_spec.path_methods.get(matched_template, [])
            ]
            if matched_template is not None and method in spec_methods:
                key = (method, matched_template)
                call_counts[key] = call_counts.get(key, 0) + 1
                if entry.timestamp:
                    last_seen_ts[key] = entry.timestamp
            else:
                norm_unmatched = normalize_unmatched_path(clean_path)
                u_key = (method, norm_unmatched)
                undocumented_counts[u_key] = undocumented_counts.get(u_key, 0) + 1
                if entry.timestamp:
                    undocumented_last_seen[u_key] = entry.timestamp

        raw_findings: list[ZombieFinding] = []

        # 2. Emit UNDOCUMENTED findings
        for (m_method, m_path), count in undocumented_counts.items():
            inferred_module = _infer_module_from_path(m_path)
            raw_findings.append(
                ZombieFinding(
                    classification=ZombieClass.UNDOCUMENTED,
                    method=m_method,
                    normalized_path=m_path,
                    module=inferred_module,
                    call_count=count,
                    last_seen=undocumented_last_seen.get((m_method, m_path)),
                    details=(
                        f"Endpoint '{m_method} {m_path}' received {count} call(s) "
                        "in access logs but is absent from OpenAPI spec"
                    ),
                )
            )

        # 3. Determine tested endpoints from run_result / test_collection
        tested_operations: set[tuple[str, str]] = set()
        if run_result is not None:
            for case in run_result.results:
                c_path = extract_url_path(case.url)
                matched = match_path_to_spec(c_path, spec_paths)
                if matched is not None:
                    tested_operations.add((case.method.upper(), matched))

        if test_collection is not None:
            for test_def in test_collection.tests:
                t_path = extract_url_path(test_def.request.url)
                matched = match_path_to_spec(t_path, spec_paths)
                if matched is not None:
                    m_val = (
                        test_def.request.method.value
                        if hasattr(test_def.request.method, "value")
                        else str(test_def.request.method)
                    )
                    tested_operations.add((m_val.upper(), matched))

        # 4. Evaluate documented OpenAPI endpoints
        for ep in openapi_spec.endpoints:
            ep_key = (ep.method.upper(), ep.path)
            calls = call_counts.get(ep_key, 0)
            last_ts = last_seen_ts.get(ep_key)

            # UNUSED: in spec, 0 calls in access logs
            if calls == 0:
                raw_findings.append(
                    ZombieFinding(
                        classification=ZombieClass.UNUSED,
                        method=ep.method.upper(),
                        normalized_path=ep.path,
                        module=ep.module,
                        call_count=0,
                        last_seen=None,
                        details=(
                            f"Endpoint '{ep.method.upper()} {ep.path}' is documented "
                            "in OpenAPI spec but received 0 calls in access logs"
                        ),
                    )
                )

            # DEPRECATED-BUT-CALLED: marked deprecated in spec, called >= 1 times
            if ep.deprecated and calls > 0:
                raw_findings.append(
                    ZombieFinding(
                        classification=ZombieClass.DEPRECATED_BUT_CALLED,
                        method=ep.method.upper(),
                        normalized_path=ep.path,
                        module=ep.module,
                        call_count=calls,
                        last_seen=last_ts,
                        details=(
                            f"Endpoint '{ep.method.upper()} {ep.path}' is marked "
                            f"deprecated in OpenAPI spec but received {calls} call(s) "
                            "in access logs"
                        ),
                    )
                )

            # UNTESTED: in spec, not exercised by executed tests
            if (
                run_result is not None or test_collection is not None
            ) and ep_key not in tested_operations:
                raw_findings.append(
                    ZombieFinding(
                        classification=ZombieClass.UNTESTED,
                        method=ep.method.upper(),
                        normalized_path=ep.path,
                        module=ep.module,
                        call_count=calls,
                        last_seen=last_ts,
                        details=(
                            f"Endpoint '{ep.method.upper()} {ep.path}' is documented "
                            "in OpenAPI spec but was not exercised by any test case"
                        ),
                    )
                )

        # 5. Filter by module if specified
        if module:
            target_mod = module.strip().lower()
            filtered_findings = [
                f for f in raw_findings if f.module.lower() == target_mod
            ]
        else:
            filtered_findings = list(raw_findings)

        # Sort deterministically by (module, classification, method, normalized_path)
        filtered_findings.sort(
            key=lambda f: (
                f.module.lower(),
                f.classification.value,
                f.method,
                f.normalized_path,
            )
        )

        # 6. Aggregate counts
        counts_by_class: dict[str, int] = {c.value: 0 for c in ZombieClass}
        counts_by_module: dict[str, dict[str, int]] = {}

        for f in filtered_findings:
            counts_by_class[f.classification.value] += 1
            mod_dict = counts_by_module.setdefault(
                f.module, {c.value: 0 for c in ZombieClass}
            )
            mod_dict[f.classification.value] += 1

        return ZombieReport(
            evaluated_at=self._clock(),
            total_findings=len(filtered_findings),
            counts_by_class=counts_by_class,
            counts_by_module=counts_by_module,
            findings=filtered_findings,
        )
