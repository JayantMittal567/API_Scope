"""Extended read-only local StockPile verification runner using ApiScope core engine.

Phase 2 Evaluation: Executes 30 strictly read-only GET endpoints against an
explicitly authorized local StockPile instance on localhost:8080.

Security and Governance Controls:
  - Enforces TargetAccessPolicy(allowed_hosts={'localhost'}).
  - Prohibits all mutation methods (POST, PUT, DELETE, PATCH).
  - Validates that target URLs strictly match approved localhost:8080 GET paths.
  - Preserves ApiScope's global SSRF protection.
  - Generates detailed JSON test audit results and console coverage diagnostics.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from apiscope.core.executor import RequestExecutor, TargetAccessPolicy
from apiscope.core.run_service import RunService
from apiscope.definitions.collection import load_collection

ALLOWED_PATHS = {
    "/api/health",
    "/api/dashboard",
    "/api/products",
    "/api/products/1",
    "/api/products/1/movements",
    "/api/suppliers",
    "/api/suppliers/3",
    "/api/suppliers/3/products",
    "/api/stock-audits",
    "/api/stock-audits/summary",
    "/api/stock-audits/5",
    "/api/purchase-orders",
    "/api/purchase-orders/4",
    "/api/sales",
    "/api/sales/summary",
    "/api/sales/trend",
    "/api/sales/8",
    "/api/surplus-listings",
    "/api/surplus-listings/summary",
    "/api/surplus-listings/suggestions",
    "/api/surplus-listings/1",
    "/api/reports/summary",
    "/api/reports/profit-loss",
    "/api/reports/sales",
    "/api/reports/inventory-loss",
    "/api/reports/purchases",
    "/api/expenses",
    "/api/intelligence",
    "/api/intelligence/sales",
    "/api/movements",
}


def main() -> None:
    base = Path(__file__).resolve().parent
    suite_file = base / "stockpile_extended_readonly.yaml"
    suite = load_collection(suite_file)

    # 1. Strict Security & Read-Only Safety Validation
    print("=" * 70)
    print("APISCOPE: STOCKPILE EXTENDED READ-ONLY EVALUATION (PHASE 2)")
    print("=" * 70)
    print(f"Loading suite: {suite_file.name}")
    print(f"Total test cases configured: {len(suite.tests)}")

    for case in suite.tests:
        method = getattr(case.request.method, "value", str(case.request.method)).upper()
        url = urlsplit(case.request.url)

        if method != "GET":
            raise ValueError(
                f"Security Violation: Non-GET mutation method '{method}' "
                f"forbidden in read-only evaluation: {case.test_name}"
            )
        if (
            url.scheme != "http"
            or url.hostname != "localhost"
            or url.port != 8080
            or url.path not in ALLOWED_PATHS
            or url.username is not None
            or url.password is not None
        ):
            raise ValueError(
                f"Security Violation: Target URL '{case.request.url}' is not an "
                f"authorized read-only localhost:8080 StockPile endpoint."
            )

    print("All 30 test cases verified strictly read-only and authorized.")
    print("Executing tests against http://localhost:8080...")
    print("-" * 70)

    # 2. Scoped Request Execution
    with RequestExecutor(
        policy=TargetAccessPolicy(allowed_hosts={"localhost"})
    ) as executor:
        result = RunService(
            executor=executor, max_run_duration_seconds=120.0
        ).run_collection(suite)

    # 3. Result Compilation & Assertion Diagnostics
    total_assertions = 0
    passed_assertions = 0
    failed_assertions = 0

    cases_report = []
    print(f"{'STATUS':<8} {'CODE':<6} {'TIME':<10} {'TEST NAME'}")
    print("-" * 70)

    for case in result.results:
        http_code = case.status_code if case.status_code is not None else "ERR"
        elapsed = f"{case.elapsed_ms:.1f}ms" if case.elapsed_ms is not None else "N/A"
        name = case.test_name
        print(f"[{case.status.value:<6}] {str(http_code):<6} {elapsed:<10} {name}")

        case_asserts = []
        for a in case.assertion_results:
            total_assertions += 1
            passed = a.get("passed", False)
            if passed:
                passed_assertions += 1
            else:
                failed_assertions += 1
            case_asserts.append({
                "type": a.get("assertion_type"),
                "passed": passed,
                "message": a.get("message"),
                "expected": a.get("expected"),
                "actual": a.get("actual"),
            })

        cases_report.append({
            "name": case.test_name,
            "status": case.status.value,
            "verified": case.verified,
            "http_status": case.status_code,
            "elapsed_ms": case.elapsed_ms,
            "assertions_count": len(case.assertion_results),
            "assertions": case_asserts,
            "error": case.error_message,
        })

    print("-" * 70)
    print("SUMMARY OF VERIFICATION OUTCOMES:")
    print(f"  Tests Executed:     {result.total_tests}")
    print(f"  Tests Passed:       {result.passed_tests}")
    print(f"  Tests Failed:       {result.failed_tests}")
    print(f"  Tests Errored:      {result.error_tests}")
    print(f"  Total Assertions:   {total_assertions}")
    print(f"  Assertions Passed:  {passed_assertions}")
    print(f"  Assertions Failed:  {failed_assertions}")
    pass_pct = (
        round(passed_assertions / total_assertions * 100.0, 2)
        if total_assertions
        else 0.0
    )
    print(f"  Assertion Pass Rate: {pass_pct}%")

    out_file = base / "stockpile_extended_results.json"
    report_data = {
        "evaluation_phase": "Phase 2 - Extended Read-Only Verification",
        "run_id": result.run_id,
        "total_tests": result.total_tests,
        "passed_tests": result.passed_tests,
        "failed_tests": result.failed_tests,
        "error_tests": result.error_tests,
        "skipped_tests": result.skipped_tests,
        "total_assertions": total_assertions,
        "passed_assertions": passed_assertions,
        "failed_assertions": failed_assertions,
        "cases": cases_report,
    }
    out_file.write_text(json.dumps(report_data, indent=2), encoding="utf-8")
    print(f"\nAudit results successfully written to: {out_file}")

    if result.failed_tests or result.error_tests or result.skipped_tests:
        sys.exit(1)


if __name__ == "__main__":
    main()
