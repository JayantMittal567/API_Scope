# ApiScope Module Health Verdict Demonstration (Module M8)

ApiScope evaluates software module health by cross-referencing executed API test cases against OpenAPI 3 specifications, assertion verifications, contract conformance, negative/boundary tests, authentication checks, and latency drift.

---

## 1. Prerequisites & Test Run

Before running module health analysis, ensure your application or sample target is executing, and run test definitions storing the execution telemetry in the SQLite database:

```powershell
# Run the test suite against the target application and record results to history.db
python -m apiscope run tests/sample_suite.yaml --env development --db .apiscope/history.db
```

---

## 2. Basic Health Evaluation

Evaluate the health of the `orders` module cross-referenced against the OpenAPI specification:

```powershell
python -m apiscope health --module orders --spec sample_app/openapi.yaml --db .apiscope/history.db
```

### Sample Output (HEALTHY):

```text
=== Module Health: orders [HEALTHY] ===
Summary: Module 'orders' is HEALTHY: verified against 18 checks with 100.0% endpoint coverage.
Evidence:
  - orders: 4/4 endpoints tested (100.0% coverage)
  - Verified against 18 checks (18/18 assertions passed) with 100.0% endpoint coverage
  - Test cases: 6/6 passed (functional: 4/4, negative/boundary: 2/2, auth: 2/2)
  - Latency: avg 34.2 ms, p95 48.0 ms
  - Signals: 0 zombies, 0 silent failures
```

---

## 3. Strict Mode & Latency Regression Drift

Evaluate module health with a latency baseline threshold (`--baseline-p95`) and strict exit enforcement (`--strict`):

```powershell
python -m apiscope health --module orders --baseline-p95 25.0 --strict --db .apiscope/history.db
```

### Sample Output (HEALTHY WITH WARNINGS):

```text
=== Module Health: orders [HEALTHY WITH WARNINGS] ===
Summary: Module 'orders' is HEALTHY WITH WARNINGS: verified against 18 checks with 100.0% endpoint coverage; 1 warning condition(s) detected.
Evidence:
  - orders: 4/4 endpoints tested (100.0% coverage)
  - Verified against 18 checks (18/18 assertions passed) with 100.0% endpoint coverage
  - Test cases: 6/6 passed (functional: 4/4, negative/boundary: 2/2, auth: 2/2)
  - Latency: avg 34.2 ms, p95 48.0 ms (baseline p95: 25.0 ms)
  - Signals: 0 zombies, 0 silent failures
Warnings:
  ! Latency regression detected: p95 48.0 ms exceeds baseline 25.0 ms * 1.5 (37.5 ms)

# Exit code: 2 (HEALTHY WITH WARNINGS under --strict)
```

---

## 4. Incomplete Coverage Detection

When the OpenAPI specification defines endpoints that have not yet been exercised by tests (below the 80.0% threshold):

```powershell
python -m apiscope health --module products --spec sample_app/openapi.yaml --threshold 80.0
```

### Sample Output (INCOMPLETE):

```text
=== Module Health: products [INCOMPLETE] ===
Summary: Module 'products' evaluation INCOMPLETE: Endpoint coverage 33.3% is below threshold 80.0%; verified against 4 checks with 33.3% endpoint coverage.
Evidence:
  - products: 1/3 endpoints tested (33.3% coverage)
  - Verified against 4 checks (4/4 assertions passed) with 33.3% endpoint coverage
  - Test cases: 1/1 passed (functional: 1/1, negative/boundary: 0/0, auth: 0/0)
  - Latency: avg 22.0 ms, p95 22.0 ms
  - Signals: 0 zombies, 0 silent failures

# Exit code: 1 (INCOMPLETE / UNHEALTHY)
```

---

## 5. Exporting Structured JSON Health Reports

Generate and export structured JSON evidence for CI/CD pipelines or dashboards:

```powershell
python -m apiscope health --module orders --spec sample_app/openapi.yaml --output reports/health_orders.json
```

```json
{
  "module": "orders",
  "verdict": "HEALTHY",
  "run_id": "run-b0f34045-812e-4e67-93ae-c40d99ef87b3",
  "evaluated_at": "2026-11-13T01:50:00+00:00",
  "total_tests": 6,
  "passed_tests": 6,
  "failed_tests": 0,
  "error_tests": 0,
  "skipped_tests": 0,
  "total_assertions": 18,
  "passed_assertions": 18,
  "pass_rate_pct": 100.0,
  "spec_endpoints_total": 4,
  "spec_endpoints_tested": 4,
  "endpoint_coverage_pct": 100.0,
  "untested_endpoints": [],
  "coverage_threshold_pct": 80.0,
  "functional_stats": {
    "category": "functional",
    "total": 4,
    "passed": 4,
    "failed": 0,
    "pass_rate_pct": 100.0
  },
  "contract_stats": {
    "category": "contract",
    "total": 8,
    "passed": 8,
    "failed": 0,
    "pass_rate_pct": 100.0
  },
  "negative_boundary_stats": {
    "category": "negative_boundary",
    "total": 2,
    "passed": 2,
    "failed": 0,
    "pass_rate_pct": 100.0
  },
  "auth_stats": {
    "category": "auth",
    "total": 2,
    "passed": 2,
    "failed": 0,
    "pass_rate_pct": 100.0
  },
  "avg_latency_ms": 34.2,
  "p95_latency_ms": 48.0,
  "baseline_p95_ms": null,
  "latency_regression": false,
  "zombie_count": 0,
  "silent_failure_count": 0,
  "summary": "Module 'orders' is HEALTHY: verified against 18 checks with 100.0% endpoint coverage.",
  "evidence": [
    "orders: 4/4 endpoints tested (100.0% coverage)",
    "Verified against 18 checks (18/18 assertions passed) with 100.0% endpoint coverage",
    "Test cases: 6/6 passed (functional: 4/4, negative/boundary: 2/2, auth: 2/2)",
    "Latency: avg 34.2 ms, p95 48.0 ms",
    "Signals: 0 zombies, 0 silent failures"
  ],
  "warnings": [],
  "failures": []
}
```

---

## 6. CLI Deterministic Exit Code Summary

| Exit Code | Meaning | Conditions |
|:---:|:---|:---|
| `0` | Success | `HEALTHY`, or `HEALTHY WITH WARNINGS` when `--strict` is not passed. |
| `1` | Test/Health Failure | One or more evaluated modules are `UNHEALTHY` or `INCOMPLETE`. |
| `2` | Warnings with Strict | One or more evaluated modules are `HEALTHY WITH WARNINGS` and `--strict` is passed. |
| `3` | Tool / Configuration Error | Missing database/run, invalid OpenAPI spec, bad thresholds, or directory traversal. |
