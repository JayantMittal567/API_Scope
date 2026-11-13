# ApiScope Web Dashboard Walkthrough & Demo Guide

This guide demonstrates how to seed test execution history and run the ApiScope Web Dashboard using FastAPI, Jinja2, and Chart.js on Windows PowerShell.

---

## 1. Seed Test Execution History in SQLite

First, ensure test results exist in the SQLite history database `.apiscope/history.db`:

```powershell
# Run the planted bug probes against sample_app
apiscope run examples/m13_planted_bug_probes.yaml --env examples/m13_planted_bug_env.yaml

# Run the chaining suite
apiscope run examples/m4_chaining_suite.yaml
```

---

## 2. Launch the Web Dashboard (`apiscope serve`)

Start the local dashboard server configured with OpenAPI specifications, access logs, and application logs:

```powershell
apiscope serve `
  --host 127.0.0.1 `
  --port 8080 `
  --spec sample_app/openapi.yaml `
  --access-log sample_app/logs/access.log `
  --app-log sample_app/logs/app.log `
  --health-file examples/m9_m10_health_probe_degraded.json
```

Output:
```text
ApiScope Dashboard ready at http://127.0.0.1:8080 (DB: .apiscope\history.db)
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8080 (Press CTRL+C to quit)
```

To validate configuration without binding a blocking socket (e.g. for CI pipelines):

```powershell
apiscope serve --check-only --spec sample_app/openapi.yaml
```

---

## 3. Dashboard Navigation & Views

Open your web browser and navigate to `http://127.0.0.1:8080`:

### 3.1. Runs List & Trends Overview (`GET /` and `GET /runs`)
- **Summary Cards**: Displays total test runs recorded, latest pass rate, and execution duration.
- **Trigger Test Run Panel**: Trigger an on-demand test run from the browser UI by entering the suite path (e.g. `examples/m4_chaining_suite.yaml`) and clicking **Run Suite**.
- **Interactive Chart.js Trends**: Line charts rendering historical Pass Rate (%) and Response Time latency (`avg_ms`, `p95_ms`), backed by an HTML table.
- **Run History Table**: Lists past runs with direct links to Run Detail and report download links (`JSON`, `JUnit XML`, `HTML`).

### 3.2. Run Detail & Diagnostics (`GET /runs/{run_id}`)
- **Metadata Card**: Displays environment, timestamps, and pass/fail/error/skip totals.
- **Report Downloads**: One-click download buttons for JSON, JUnit XML, and HTML report files.
- **Expected vs. Actual Diagnostics**: Detailed per-assertion breakdown highlighting expected values, actual responses, and error messages for failures.
- **Secret Redaction**: Extracted variables, URLs, and headers mask sensitive credentials with `***`.

### 3.3. Module Health Verdicts (`GET /health`)
- Evaluates API modules across test assertions, endpoint coverage against `sample_app/openapi.yaml`, and silent failure signals.
- Displays evidence-based verdicts: `HEALTHY`, `HEALTHY WITH WARNINGS`, `UNHEALTHY`, or `INCOMPLETE`.
- Shows endpoint coverage progress bars, category breakdowns (Functional, Contract, Boundary, Auth), and canonical verdict banners:
  `"PASS WITH WARNINGS: APIs healthy, internal issue suspected"`.

### 3.4. Zombie & Silent Failure Analysis (`GET /zombies`)
- **Zombie Endpoints (Module M9)**: Breakdown and table of `UNUSED`, `UNDOCUMENTED`, `UNTESTED`, and `DEPRECATED-BUT-CALLED` endpoints derived from `access.log` and `openapi.yaml`.
- **Silent Failures (Module M10)**: Signal analysis table for `S1_LOG_ERROR` (application logs), `S2_DEPENDENCY_PROBE` (degraded `/health`), `S3_LATENCY_DRIFT`, and `S4_SUSPICIOUS_2XX`.

---

## 4. REST API Endpoints

The dashboard exposes clean JSON REST endpoints for external tooling and CI/CD integration:

- `POST /api/runs`: Trigger test suite execution via JSON payload:
  ```json
  {
    "suite_path": "examples/m4_chaining_suite.yaml",
    "module": "orders"
  }
  ```
- `GET /api/trends?limit=20`: Historical trend points for external charting.
- `GET /api/health`: JSON module health reports.
- `GET /runs/{run_id}/download?format=json|junit|html`: Programmatic report file export.
