"""Unit tests for ZombieService and access log parsing.

Covers: FR-35, FR-36, FR-37, NFR-02, NFR-04, NFR-06.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from apiscope.analysis.zombie_service import (
    MAX_LOG_FILE_BYTES,
    MAX_LOG_LINES,
    ZombieClass,
    ZombieService,
    load_access_log,
    parse_access_log,
)
from apiscope.core.exceptions import ConfigurationError, PathTraversalError
from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.generators.openapi_importer import load_openapi_spec


def _make_case(
    name: str = "test",
    module: str = "products",
    method: str = "GET",
    url: str = "/products",
    status: CaseStatus = CaseStatus.PASSED,
) -> CaseResult:
    """Helper to construct a deterministic CaseResult."""
    return CaseResult(
        test_name=name,
        module=module,
        tags=["smoke"],
        status=status,
        verified=True,
        method=method,
        url=url,
        status_code=200,
        elapsed_ms=15.0,
        request_headers={},
        response_headers={},
        assertion_results=[{"rule_type": "status", "passed": True}],
        extracted_variables={},
        extraction_errors=[],
    )


def test_parse_access_log_combined_and_jsonl_formats_with_utf8_bom(
    tmp_path: Path,
) -> None:
    """Covers: FR-35.

    Verify parsing of Combined Log Format, JSON lines, UTF-8-BOM,
    ignoring /_test/reset, comments, and empty lines.
    """
    combined_log_content = (
        "# This is a comment\n"
        '127.0.0.1 - - [12/Nov/2026:10:00:01 +0000] "GET /products/1 HTTP/1.1" '
        "200 123\n\n"
        '127.0.0.1 - - [12/Nov/2026:10:00:02 +0000] "POST /_test/reset HTTP/1.1" '
        "200 45\n"
        '10.0.0.1 - user [2026-11-12T10:05:00Z] "DELETE /orders/42 HTTP/1.1" 204 0\n'
    )
    entries = parse_access_log(combined_log_content)
    assert len(entries) == 2
    assert entries[0].method == "GET"
    assert entries[0].raw_path == "/products/1"
    assert entries[0].status_code == 200
    assert entries[0].timestamp == "12/Nov/2026:10:00:01 +0000"

    assert entries[1].method == "DELETE"
    assert entries[1].raw_path == "/orders/42"
    assert entries[1].status_code == 204
    assert entries[1].timestamp == "2026-11-12T10:05:00Z"

    # JSON lines with UTF-8 BOM
    jsonl_content = (
        '{"method": "GET", "path": "/products?page=2", "status": 200, '
        '"time": "2026-11-12T11:00:00Z"}\n'
        '{"http_method": "POST", "url": "/orders", "status_code": 201, '
        '"timestamp": "2026-11-12T11:05:00Z"}\n'
        '{"method": "POST", "uri": "/_test/reset", "status": 200}\n'
    )
    bom_file = tmp_path / "access.jsonl"
    bom_file.write_text(jsonl_content, encoding="utf-8-sig")

    loaded_entries = load_access_log(bom_file)
    assert len(loaded_entries) == 2
    assert loaded_entries[0].method == "GET"
    assert loaded_entries[0].raw_path == "/products"
    assert loaded_entries[0].status_code == 200
    assert loaded_entries[0].timestamp == "2026-11-12T11:00:00Z"

    assert loaded_entries[1].method == "POST"
    assert loaded_entries[1].raw_path == "/orders"
    assert loaded_entries[1].status_code == 201
    assert loaded_entries[1].timestamp == "2026-11-12T11:05:00Z"

    # Invalid non-empty content with no valid entries
    with pytest.raises(ConfigurationError, match="no valid log entries"):
        parse_access_log("Not a valid log line at all\nAnother invalid line")


def test_zombie_service_detects_all_four_classes_on_sample_app_fixtures() -> None:
    """Covers: FR-35, FR-36, FR-37, M13, BUG-11, BUG-12.

    Load sample_app/openapi.yaml and sample_app/logs/access.log,
    and assert UNUSED, UNDOCUMENTED, DEPRECATED_BUT_CALLED, and UNTESTED
    findings are correctly detected.
    """
    spec_path = Path("sample_app/openapi.yaml")
    log_path = Path("sample_app/logs/access.log")
    assert spec_path.exists(), "sample_app/openapi.yaml must exist"
    assert log_path.exists(), "sample_app/logs/access.log must exist"

    spec = load_openapi_spec(spec_path)
    entries = load_access_log(log_path)
    assert len(entries) == 15

    # Partial run result exercising GET /products and POST /orders
    fixed_time = datetime(2026, 11, 12, 12, 0, 0, tzinfo=UTC)
    partial_run = RunResult(
        run_id="run-partial-1",
        environment_name="test",
        started_at=fixed_time,
        finished_at=fixed_time,
        duration_ms=100.0,
        total_tests=2,
        passed_tests=2,
        failed_tests=0,
        error_tests=0,
        skipped_tests=0,
        results=[
            _make_case(
                name="test_get_products",
                module="products",
                method="GET",
                url="http://localhost:8000/products",
            ),
            _make_case(
                name="test_post_orders",
                module="orders",
                method="POST",
                url="http://localhost:8000/orders",
            ),
        ],
    )

    service = ZombieService(clock=lambda: fixed_time)
    report = service.analyze(
        openapi_spec=spec,
        access_entries=entries,
        run_result=partial_run,
    )

    assert report.total_findings > 0
    class_map = {f.classification: [] for f in report.findings}
    for f in report.findings:
        class_map[f.classification].append(f)

    # 1. UNUSED: GET /coupons/legacy-archive (in spec, 0 calls in access.log)
    unused_paths = [
        (f.method, f.normalized_path) for f in class_map.get(ZombieClass.UNUSED, [])
    ]
    assert ("GET", "/coupons/legacy-archive") in unused_paths

    # 2. UNDOCUMENTED: GET /debug/internal-metrics (BUG-11: in log, absent from spec)
    undoc_paths = [
        (f.method, f.normalized_path)
        for f in class_map.get(ZombieClass.UNDOCUMENTED, [])
    ]
    assert ("GET", "/debug/internal-metrics") in undoc_paths

    # 3. DEPRECATED_BUT_CALLED: GET /products/v1-catalog (BUG-12: deprecated)
    dep_paths = [
        (f.method, f.normalized_path)
        for f in class_map.get(ZombieClass.DEPRECATED_BUT_CALLED, [])
    ]
    assert ("GET", "/products/v1-catalog") in dep_paths
    dep_finding = next(
        f
        for f in class_map[ZombieClass.DEPRECATED_BUT_CALLED]
        if f.normalized_path == "/products/v1-catalog"
    )
    assert dep_finding.call_count == 2

    # 4. UNTESTED: endpoints in spec not in partial_run
    untested_paths = [
        (f.method, f.normalized_path) for f in class_map.get(ZombieClass.UNTESTED, [])
    ]
    assert len(untested_paths) > 0
    assert ("GET", "/products") not in untested_paths
    assert ("POST", "/orders") not in untested_paths

    # Dynamic path normalization: /products/1 and /products/2 -> /products/{product_id}
    # In access.log, line 2 and 3 call /products/1 and /products/2 (call count >= 2)
    empty_report = service.analyze(
        openapi_spec=spec,
        access_entries=entries,
        run_result=None,
    )
    # With run_result=None, UNTESTED should be 0
    assert empty_report.counts_by_class[ZombieClass.UNTESTED.value] == 0


def test_zombie_service_module_filter_and_to_dict_serialization() -> None:
    """Covers: FR-37, NFR-06.

    Verify module filtering and JSON-serializable to_dict() contract.
    """
    spec_path = Path("sample_app/openapi.yaml")
    log_path = Path("sample_app/logs/access.log")
    spec = load_openapi_spec(spec_path)
    entries = load_access_log(log_path)

    service = ZombieService()
    # Module filter on analyze
    products_report = service.analyze(
        openapi_spec=spec,
        access_entries=entries,
        module="products",
    )
    for f in products_report.findings:
        assert f.module.lower() == "products"

    # Module filter on report
    all_report = service.analyze(
        openapi_spec=spec,
        access_entries=entries,
    )
    filtered = all_report.filter_by_module("debug")
    assert len(filtered) >= 1
    assert all(f.module.lower() == "debug" for f in filtered)

    # to_dict serialization
    rep_dict = all_report.to_dict()
    assert isinstance(rep_dict, dict)
    assert "evaluated_at" in rep_dict
    assert "total_findings" in rep_dict
    assert "counts_by_class" in rep_dict
    assert set(rep_dict["counts_by_class"].keys()) == {
        "UNUSED",
        "UNDOCUMENTED",
        "UNTESTED",
        "DEPRECATED-BUT-CALLED",
    }
    assert "findings" in rep_dict
    # Must be valid json
    serialized = json.dumps(rep_dict)
    assert len(serialized) > 0


def test_load_access_log_enforces_safe_path_and_size_limits(
    tmp_path: Path,
) -> None:
    """Covers: FR-35, NFR-02, NFR-04.

    Verify PathTraversalError, missing file, and file size / line count limits.
    """
    base_dir = tmp_path / "safe_dir"
    base_dir.mkdir()

    # 1. Path traversal
    with pytest.raises(PathTraversalError):
        load_access_log("../unsafe.log", base_dir=base_dir)

    # 2. Missing file
    missing = base_dir / "missing.log"
    with pytest.raises(ConfigurationError, match="not found"):
        load_access_log(missing, base_dir=base_dir)

    # 3. File size exceeds MAX_LOG_FILE_BYTES
    oversized = base_dir / "large.log"
    oversized.write_text("dummy", encoding="utf-8")
    real_stat = oversized.stat()
    with patch("pathlib.Path.stat") as mock_stat:
        from unittest.mock import MagicMock

        mock_result = MagicMock()
        mock_result.st_size = MAX_LOG_FILE_BYTES + 1
        mock_result.st_mode = real_stat.st_mode
        mock_stat.return_value = mock_result
        with pytest.raises(ConfigurationError, match="maximum size"):
            load_access_log(oversized, base_dir=base_dir)

    # 4. Line count exceeds MAX_LOG_LINES
    sample_line = '127.0.0.1 - - [timestamp] "GET / HTTP/1.1" 200 0'
    huge_lines = "\n".join([sample_line] * (MAX_LOG_LINES + 5))
    with pytest.raises(ConfigurationError, match="exceeds maximum"):
        parse_access_log(huge_lines)
