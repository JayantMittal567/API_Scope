# ApiScope — Automated API Testing, Module Health Monitoring & Analysis Platform

ApiScope is an automated API testing, module health monitoring, and system analysis platform engineered as a modular, production-ready Python 3.11+ application. Designed for software engineering teams and reliability verification, ApiScope provides evidence-based module health verdicts, automated test generation, zombie API detection, multi-signal silent failure diagnosis, and defensible trust assessments.

---

## 1. Core Problem & Engineering Philosophy

In modern microservices and modular monoliths, traditional test runners answer a narrow question: *"Did this specific test script pass or fail?"*

ApiScope asks and answers a broader, critical engineering question:
> **"Can we confidently say that the APIs of a particular software module work correctly and safely in practice?"**

To answer this with high confidence, ApiScope evaluates multiple dimensions of API reality:
1. **Active Test Execution**: Running positive, negative, boundary, and chained workflows with schema, header, status, and value assertions.
2. **Endpoint Coverage & Zombie Detection**: Comparing OpenAPI 3 specifications against server access logs and test runs to detect undocumented endpoints, unused legacy routes, untested contracts, and deprecated APIs.
3. **Silent Failure & Latency Drift Diagnosis**: Identifying cases where the API returns `HTTP 200 OK` despite failing internally (error payloads in logs, failed internal subsystems, latency drift regressions, or zero assertions).
4. **Defensible Trust Verdicts**: Correlating multi-source signals into deduplicated verdicts, distinguishing authentic application defects from test fixture failures, configuration blocks, or inconclusive epistemic states.

---

## 2. Key Capabilities & Architecture

| Capability | Module / Layer | Description |
| :--- | :--- | :--- |
| **Project Intelligence** | `apiscope.intelligence` | Scans source code and OpenAPI specifications to identify frameworks, entity relationships, auth schemes, and testability. |
| **Contract Reconciliation** | `apiscope.contract` | Normalizes OpenAPI specs, audits contract quality, and reconciles route definitions with implementation code. |
| **Intelligent Planning** | `apiscope.planning` | Generates balanced, category-based test plans (deterministic offline or budget-aware Gemini with local caching). |
| **Test Compilation & Fixtures** | `apiscope.compiler`, `apiscope.fixtures` | Compiles abstract scenarios into executable native suites with dependency ordering, setup chains, and teardown. |
| **Safe Workflow Execution** | `apiscope.execution`, `apiscope.core` | Enforces execution safety policies (`READ_ONLY` default, mutation fencing, SSRF protections, and non-production gates). |
| **Failure Investigation** | `apiscope.investigation` | Performs deterministic root-cause triage attributing failures to contracts, application logic, or test infrastructure. |
| **Silent Failure Diagnostics** | `apiscope.analysis` | Multi-signal detection (S1–S8) uncovering swallowed errors, 200-wrapped failure envelopes, and latency regressions. |
| **Trust & Evidence Engine** | `apiscope.trust` | Synthesizes multi-run findings, checks assertion strength, suppresses inconclusive noise, and assigns confidence ratings. |
| **Guided Orchestration** | `apiscope.orchestration` | Turnkey 11-stage guided workflow executing analysis, planning, review, safety checks, and reporting in a single command. |
| **Web Dashboard** | `apiscope.web` | Server-rendered (FastAPI + Jinja2 + Chart.js) interface with CSRF protection, trend analytics, and drill-down reports. |
| **Defect Benchmarking** | `apiscope.benchmarking` | Independent evaluation framework with 32 ground-truth cases measuring precision, recall, F1, and false-positive rates. |

---

## 3. Quickstart & Installation

### Prerequisites
- Python >= 3.11 (tested on 3.11, 3.12, and 3.13).
- Windows, Linux, or macOS.

### Standard Installation

```bash
# 1. Clone repository
git clone https://github.com/organization/apiscope.git
cd apiscope

# 2. Create and activate virtual environment
python -m venv .venv
# On Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# On Linux/macOS:
# source .venv/bin/activate

# 3. Install in editable mode with development dependencies
pip install -e ".[dev]"

# 4. Verify installation
apiscope version
```

---

## 4. Operational Usage & Workflows

### 4.1 Turnkey Guided Workflow (Single Command)

Analyze an API specification, reconcile against source code, synthesize a balanced test plan, and execute safely:

```bash
# Offline analysis (no external credentials required)
apiscope guided --spec sample_app/openapi.yaml --repo sample_app --env-name test
```

### 4.2 Interactive Web Dashboard

Launch the zero-build developer dashboard:

```bash
apiscope serve --host 127.0.0.1 --port 8000 --db-path .apiscope/history.db
```
The dashboard provides visual trend charts, guided workflow inspection, run drill-downs, and module health matrices with CSRF protection and exact-origin restrictions.

### 4.3 Independent Defect-Detection Benchmark (Task 28 Reproduction)

Reproduce the independent 32-case defect-detection accuracy evaluation:

```bash
apiscope benchmark --output-dir benchmarks/task28 --mode all --format console
```
Output artifacts are saved to `benchmarks/task28/` (`ground_truth_manifest.json`, `benchmark_report.json`, `BENCHMARK_SUMMARY.md`).

### 4.4 Optional AI-Assisted Test Planning (Google Gemini)

ApiScope operates 100% offline by default. To optionally enable AI-assisted planning:

1. Configure `.env` from template:
   ```bash
   cp .env.example .env
   ```
2. Set your Gemini API key in `.env`:
   ```bash
   GEMINI_API_KEY=your-gemini-api-key-here
   GEMINI_MODEL=gemini-2.5-flash
   ```
3. Run planning with AI opt-in:
   ```bash
   apiscope plan-tests --spec sample_app/openapi.yaml --use-ai
   ```
ApiScope enforces token budgets, caches responses deterministically, and never makes unauthorized external network calls.

---

## 5. Docker Deployment

A lightweight, non-root container deployment is provided for local dashboard inspection:

```bash
# Build the container image
docker build -t apiscope .

# Run container (bound strictly to host loopback 127.0.0.1)
docker run -p 127.0.0.1:8000:8000 -v apiscope_data:/app/.apiscope apiscope

# Or deploy via Docker Compose
docker compose up -d
```

### Container Security Characteristics
- **Non-Root Execution**: Runs under unprivileged `apiscope` user (UID 10001).
- **Loopback Binding**: Host port mapped strictly to `127.0.0.1:8000` to prevent unintended network exposure.
- **Passive Read-Only Mode**: Default container command launches the dashboard server; it **never** triggers automated test runs, Gemini calls, or target mutations.
- **Zero Secrets**: Build context excludes `.env`, credentials, databases, and keys via `.dockerignore`.

---

## 6. Comprehensive CLI Reference

The `apiscope` CLI exposes 25 specialized commands:

| Command | Description |
| :--- | :--- |
| `version` | Display ApiScope version information. |
| `init` | Initialize starter test suite (`sample_suite.yaml`) and environment config. |
| `import` | Import and validate an OpenAPI 3 specification. |
| `generate` | Generate rule-based positive, negative, and boundary test suites. |
| `ai-generate` | Generate project-aware tests with status `proposed` via LLM or profile fallback. |
| `approve` | Approve proposed AI test definitions, promoting them to executable status. |
| `run` | Execute test suites with chained extraction, variable resolution, and persistence. |
| `report` | Generate and export test execution reports in JSON, JUnit XML, or HTML format. |
| `health` | Evaluate module health verdicts (`HEALTHY`, `DEGRADED`, `CRITICAL`). |
| `zombie` | Detect unused, undocumented, untested, and deprecated-but-called endpoints. |
| `serve` | Launch the FastAPI + Jinja2 + Chart.js interactive Web Dashboard. |
| `inspect-target` | Analyze source code and produce a structured Project Intelligence Report. |
| `analyze-contract` | Audit specification quality and reconcile OpenAPI contract with source routes. |
| `plan-tests` | Generate balanced, risk-prioritized test plans (offline or budget-aware Gemini). |
| `compile-plan` | Compile abstract TestPlan scenarios into executable native test suites. |
| `prepare-tests` | Resolve fixture dependencies and sequence stateful setup workflows. |
| `run-workflow` | Execute policy-fenced stateful workflows with setup and LIFO cleanup. |
| `investigate` | Triage test run failures and attribute findings to contracts, app bugs, or fixtures. |
| `diagnose-silent` | Detect swallowed errors, 200 error envelopes, and unverified mutations (S1–S8). |
| `latency-drift` | Analyze latency drift and statistical response-time regressions between runs. |
| `diagnose-zombies` | Compute endpoint inventory, spec reconciliation, and execution coverage. |
| `assess` | Correlate multi-source diagnostic outputs into deduplicated trust verdicts. |
| `analyze-project` | Run the unified 11-stage guided API analysis and testing workflow. |
| `guided` | Alias for `analyze-project` (single-command guided execution). |
| `benchmark` | Execute independent defect-detection benchmarking and accuracy evaluation. |

---

## 7. Security Restrictions & Known Limitations

1. **Execution Safety Policy**:
   - By default, ApiScope executes in `READ_ONLY` mode.
   - Mutating operations (`POST`, `PUT`, `PATCH`, `DELETE`) are strictly blocked unless explicit `--allow-mutations` authorization is granted.
   - Mutations against production environment designations (`prod`, `production`, `live`) are unconditionally rejected.
2. **SSRF Guardrails**:
   - `TargetAccessPolicy` blocks requests to private/loopback IP ranges unless explicitly whitelisted via origin or host allowlists.
3. **Web Dashboard Security**:
   - State-changing operations are protected by time-limited, HMAC-signed CSRF tokens.
   - All rendered templates enforce autoescaping against XSS.
   - Artifact downloads strictly prevent path traversal using normalized boundary checks.
4. **Scope & Performance Realism**:
   - Benchmark evaluation measures performance against controlled reference models and authentic recorded artifacts. Real-world API defect detection depends on target accessibility, specification accuracy, and telemetry availability. No testing system can guarantee 100% bug detection in arbitrary uninstrumented software.

---

## 8. Verification & Quality Gates

The codebase maintains strict quality thresholds verified by automated CI checks:

```bash
# Run linting
python -m ruff check .

# Verify formatting
python -m ruff format --check .

# Verify bytecode compilation
python -m compileall src sample_app

# Run full test suite with coverage
python -m pytest tests --cov=apiscope --cov-report=term-missing
```

- **Passing Tests:** 720 tests passing.
- **Coverage:** >= 88% statement coverage across all core modules.
- **Zero Warnings:** All in-process deprecation warnings resolved.
