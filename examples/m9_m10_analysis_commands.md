# ApiScope M9 & M10 Analysis Commands Guide

This guide demonstrates how to execute Zombie API detection (Module M9) and Silent Failure analysis (Module M10) using the ApiScope CLI on Windows PowerShell.

---

## 1. Zombie API Endpoint Detection (`apiscope zombie`)

The `apiscope zombie` command analyzes OpenAPI 3 specifications alongside access logs (Common/Combined Log Format or JSON Lines) and optional execution run history from SQLite.

### 1.1. Analyze Zombie Endpoints Against Access Logs

Scan `sample_app/openapi.yaml` against `sample_app/logs/access.log`:

```powershell
apiscope zombie --spec sample_app/openapi.yaml --access-log sample_app/logs/access.log
```

Expected output:
- Identifies `UNUSED` endpoints defined in OpenAPI spec that have 0 calls in access logs (e.g. `GET /coupons/legacy-archive`).
- Identifies `UNDOCUMENTED` endpoints called in access logs that are missing from the OpenAPI spec (e.g. `GET /debug/internal-metrics`).
- Identifies `DEPRECATED-BUT-CALLED` endpoints marked `deprecated: true` in OpenAPI spec but actively invoked in access logs (e.g. `GET /products/v1-catalog`).
- Normalizes parameterized URLs (e.g. `/products/1` and `/products/2` -> `/products/{product_id}`).

### 1.2. Correlate with Test Execution History for UNTESTED Detection

```powershell
apiscope zombie --spec sample_app/openapi.yaml --access-log sample_app/logs/access.log --db .apiscope/history.db
```

- Cross-references executed test cases in SQLite to detect `UNTESTED` endpoints (documented in OpenAPI spec but never exercised by test cases).

### 1.3. Enforce CI Gate with Strict Mode and Export JSON Report

```powershell
apiscope zombie `
  --spec sample_app/openapi.yaml `
  --access-log sample_app/logs/access.log `
  --output reports/zombie-report.json `
  --strict
```

Exit codes:
- `0`: No zombie endpoints detected (or strict mode disabled).
- `2`: Zombie endpoints detected when `--strict` is enabled.
- `3`: Configuration, log parsing, or file path error.

---

## 2. Integrated Module Health Verdict (`apiscope health`)

The `apiscope health` command combines test assertion results, OpenAPI endpoint coverage, latency drift benchmarks, zombie API findings, and silent failure signals into an evidence-based verdict.

### 2.1. Basic Health Check

```powershell
apiscope health --db .apiscope/history.db --spec sample_app/openapi.yaml
```

### 2.2. Enhanced Health Check with Log Error & Dependency Health Probes

Correlate application logs (`sample_app/logs/app.log`) for swallowed exceptions (Signal S1) and degraded `/health` probes (Signal S2):

```powershell
apiscope health `
  --db .apiscope/history.db `
  --spec sample_app/openapi.yaml `
  --app-log sample_app/logs/app.log `
  --health-file examples/m9_m10_health_probe_degraded.json `
  --strict
```

Expected verdict banner:
```text
=== Module Health: orders [HEALTHY WITH WARNINGS] ===
Summary: Module 'orders' PASS WITH WARNINGS: APIs healthy, internal issue suspected; verified against 12 checks with 100.0% endpoint coverage.
```

### 2.3. Evaluating a Single Module with Latency Drift Detection

```powershell
apiscope health `
  --module products `
  --db .apiscope/history.db `
  --spec sample_app/openapi.yaml `
  --baseline-p95 50.0 `
  --strict
```

If p95 latency exceeds `50.0 * 1.5 = 75.0 ms`, Signal S3 triggers a `HEALTHY WITH WARNINGS` verdict.
