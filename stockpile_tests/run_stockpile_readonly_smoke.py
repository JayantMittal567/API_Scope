"""Read-only local StockPile smoke run using ApiScope's core engine.

This temporary diagnostic runner does not change ApiScope's CLI or database.
It is restricted to approved GET paths on localhost:8080 and writes a
minimal result summary without raw response bodies.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from apiscope.core.executor import RequestExecutor, TargetAccessPolicy
from apiscope.core.run_service import RunService
from apiscope.definitions.collection import load_collection

ALLOWED_PATHS = {
    "/api/health",
    "/api/products",
    "/api/suppliers",
    "/api/stock-audits",
    "/api/purchase-orders",
    "/api/sales",
    "/api/surplus-listings",
    "/api/reports/summary",
}


def main() -> None:
    base = Path(__file__).resolve().parent
    suite = load_collection(base / "stockpile_readonly_smoke.yaml")
    for case in suite.tests:
        method = getattr(case.request.method, "value", str(case.request.method))
        url = urlsplit(case.request.url)
        if (
            method != "GET"
            or url.scheme != "http"
            or url.hostname != "localhost"
            or url.port != 8080
            or url.path not in ALLOWED_PATHS
            or url.username is not None
            or url.password is not None
        ):
            raise ValueError("Refusing to run an unapproved or non-read-only test")

    # Explicit exception to the default SSRF policy, scoped to localhost
    # for this one-off, developer-controlled smoke test only.
    with RequestExecutor(
        policy=TargetAccessPolicy(allowed_hosts={"localhost"})
    ) as executor:
        result = RunService(
            executor=executor, max_run_duration_seconds=120.0
        ).run_collection(suite)

    report = {
        "run_id": result.run_id,
        "total_tests": result.total_tests,
        "passed_tests": result.passed_tests,
        "failed_tests": result.failed_tests,
        "error_tests": result.error_tests,
        "skipped_tests": result.skipped_tests,
        "cases": [
            {
                "name": case.test_name,
                "status": case.status.value,
                "verified": case.verified,
                "http_status": case.status_code,
                "elapsed_ms": case.elapsed_ms,
                "assertions": [
                    {
                        "type": a.get("assertion_type"),
                        "passed": a.get("passed"),
                        "message": a.get("message"),
                    }
                    for a in case.assertion_results
                ],
                "error": case.error_message,
            }
            for case in result.results
        ],
    }
    out = base / "stockpile_readonly_results.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"Read-only StockPile smoke: {result.passed_tests} passed, "
        f"{result.failed_tests} failed, {result.error_tests} errors, "
        f"{result.skipped_tests} skipped (total {result.total_tests})."
    )
    print(f"Results written to: {out}")
    if result.failed_tests or result.error_tests or result.skipped_tests:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
