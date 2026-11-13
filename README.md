<<<<<<< HEAD
# API_Scope
=======
# ApiScope — Automated API Testing, Module Health Monitoring & Analysis Platform

ApiScope is an automated API testing, module health monitoring, and system analysis platform engineered as a modular, production-ready Python 3.11+ application. Designed for software engineering teams and academic assessment, ApiScope provides evidence-based module health verdicts, automated test generation, zombie API detection, and multi-signal silent failure diagnosis.

---

## 1. Core Problem & Academic Question

In modern microservices and modular monoliths, traditional test runners answer a narrow question: *"Did this specific test script pass or fail?"*

ApiScope asks and answers a broader, critical engineering question:
> **"Can we confidently say that the APIs of a particular software module work correctly?"**

To answer this with high confidence, ApiScope evaluates four dimensions of API reality:
1. **Active Test Execution**: Running positive, negative, boundary, and chained workflows with schema and value assertions.
2. **Endpoint Coverage & Zombie Detection**: Comparing OpenAPI 3 specifications against server access logs and test runs to detect undocumented endpoints, unused legacy routes, untested contracts, and deprecated APIs.
3. **Silent Failure Diagnosis**: Identifying cases where the API returns `HTTP 200 OK` despite failing internally (error payloads in logs, failed internal subsystems, extreme latency spikes, or zero assertions).
4. **Evidence-Based Verdicts**: Computing deterministic verdicts (**HEALTHY**, **DEGRADED**, **CRITICAL**) per module based on active pass rates, endpoint coverage, zombie findings, and silent failure signals.

---

## 2. Honest Tool Comparison

How does ApiScope compare to established industry API testing tools?

| Feature / Dimension | Postman | Newman | Bruno | Schemathesis | **ApiScope** |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Primary Focus** | Manual API testing, UI workspace, team API docs | CLI execution of Postman JSON collections in CI | Local-first, Git-friendly manual API client (Bru markup) | Property-based fuzz testing directly from OpenAPI specs | **Module health verdicts, silent failure diagnosis & zombie API detection** |
| **Test Definition** | Proprietary JSON Collections + JS sandbox scripts | Postman JSON Collections | Plain-text `.bru` files + JS scripts | Pure OpenAPI specs (auto-fuzzed) | **Declarative YAML suites** with chained variables & multi-row datasets |
| **OpenAPI Ingestion** | Import to collection | N/A (runs collections) | OpenAPI importer | Native OpenAPI fuzzer | **OpenAPI 3 Importer** with schema normalization & parameter extraction |
| **Automated Generation** | Postbot (AI in cloud app) | N/A | None | Property-based hypothesis fuzzing | **Rule-Based (M5)** positive/negative/boundary + **AI-Assisted (M12)** with Project Profile & human approval |
| **Module Health Verdicts** | No (pass/fail summary) | No (pass/fail summary) | No (pass/fail summary) | No (server crash / violation summary) | **Yes**: Deterministic `HEALTHY` / `DEGRADED` / `CRITICAL` verdicts per module |
| **Silent Failure Detection** | No (relies on user assertion scripts) | No (relies on user assertion scripts) | No (relies on user scripts) | No (detects 500s or schema mismatches) | **Yes**: Signals S1–S4 (log error on 200, health probe down, latency regression, zero assertions) |
| **Zombie API Detection** | No | No | No | No | **Yes**: 4 categories (`UNUSED`, `UNDOCUMENTED`, `UNTESTED`, `DEPRECATED-BUT-CALLED`) via log reconciliation |
| **Trace Correlation** | Manual header setting | Manual header setting | Manual header setting | Optional headers | **Native `X-Correlation-ID`** injection, DB telemetry, and log correlation |
| **Data Persistence** | Cloud Workspace / Team DB | None (ephemeral run) | Git files only | None (ephemeral run) | **Embedded SQLite repository** with run telemetry, case history & trend metrics |
| **Presentation Interfaces** | Electron Desktop UI & Cloud Web | Headless CLI output (Console / HTML) | Desktop UI (Electron) | Headless CLI output | **Dual Presentation**: Full Windows PowerShell CLI + FastAPI / Jinja2 / Chart.js Web Dashboard |
| **Deployment / Privacy** | Cloud account required for sync | Node.js npm package | Local desktop app | Python CLI tool | **100% Local / Self-Contained**: Standard Python library, no cloud account, no Node.js build step |

---

## 3. System Architecture

ApiScope enforces a strict layered architecture to ensure maintainability, testability, and decoupling between interfaces and business logic:

```
+---------------------------------------------------------------------------------+
|                               PRESENTATION LAYER                                |
|        CLI (Typer + Rich Formatting)      |     Web Dashboard (FastAPI + SSR)   |
|   (run, report, health, zombie, serve)   |   (Chart.js, Jinja2, REST endpoints) |
+---------------------------------------------------------------------------------+
                                         |
                                         v
+---------------------------------------------------------------------------------+
|                            APPLICATION SERVICES LAYER                           |
|  - RunService             - HealthService           - GeneratorService          |
|  - ZombieService          - ReportService           - AiGeneratorService        |
|  - SilentFailureService   - EvaluatorExperiment                                 |
+---------------------------------------------------------------------------------+
                                         |
                                         v
+---------------------------------------------------------------------------------+
|                                CORE ENGINE LAYER                                |
|  - RequestExecutor (HTTPX + SSRF Safety)  - AssertionEngine (12 assertion types)|
|  - VariableResolver ({{var}} templating)  - Extractor (JSONPath / Regex / Status|
+---------------------------------------------------------------------------------+
                                         |
                                         v
+---------------------------------------------------------------------------------+
|                                   DATA LAYER                                    |
|  - DatabaseManager (SQLite connection pool)                                     |
|  - RunRepository (runs, case_results, trends, PRAGMA migrations)                |
+---------------------------------------------------------------------------------+
                                         |
                                         v
+---------------------------------------------------------------------------------+
|                              EXTERNAL INTEGRATIONS                              |
|  - Target REST APIs        - Application Logs (access.log / app.log)            |
|  - OpenAPI 3 Specs         - LLM Providers (Gemini / OpenAI / Profile-Fallback) |
+---------------------------------------------------------------------------------+
```

### Architectural Principles
- **Dual Presentation, Single Core**: Both the Typer CLI and FastAPI Web routes delegate all execution, persistence, and analysis to the same application services (`RunService`, `HealthService`, `ZombieService`, `ReportService`).
- **Repository Pattern**: No SQL is embedded in presentation or business logic. All database persistence uses SQLite with standard-library `sqlite3` behind abstract repository interfaces.
- **SSRF & Security Isolation**: The `RequestExecutor` applies strict URL normalization, private IP guardrails (`TargetAccessPolicy`), and safe file-path boundary validation.

---

## 4. Windows PowerShell CLI Reference

ApiScope provides a unified CLI built on Typer with rich console output:

| Command | Arguments & Primary Options | Description |
| :--- | :--- | :--- |
| `version` | *(none)* | Display ApiScope version information. |
| `init` | `--dir <path>`, `--force` | Initialize starter test suite (`sample_suite.yaml`) and environment config (`apiscope.env.yaml`). |
| `import` | `<spec_path>`, `--output <path>` | Import and validate an OpenAPI 3 YAML/JSON specification. |
| `generate` | `<spec_path>`, `--output <path>`, `--module <name>` | Generate rule-based positive, negative, and boundary tests (Module M5). |
| `ai-generate` | `<spec_path>`, `--profile <path>`, `--output <path>`, `--provider <p>`, `--api-key <k>` | Generate project-aware tests with status `proposed` using live LLM or profile synthesis (Module M12). |
| `approve` | `<suite_path>`, `--output <path>` | Approve proposed AI test definitions, promoting them to executable status. |
| `run` | `<suite_path>`, `--env-file <path>`, `--db <path>`, `--module <m>`, `--fail-fast` | Execute test suite with chained extraction, variable resolution, and SQLite persistence. |
| `report` | `--db <path>`, `--run-id <id>`, `--format <json\|junit\|html>`, `--output <path>` | Generate and display or export test execution reports in JSON, JUnit XML, or HTML format. |
| `health` | `--db <path>`, `--run-id <id>`, `--module <m>`, `--spec <path>`, `--access-log <path>`, `--app-log <path>` | Evaluate module health verdict (`HEALTHY`, `DEGRADED`, `CRITICAL`), endpoint coverage, and silent failure signals. |
| `zombie` | `--spec <path>`, `--access-log <path>`, `--db <path>`, `--run-id <id>`, `--module <m>`, `--strict` | Detect unused, undocumented, untested, and deprecated-but-called zombie endpoints. |
| `serve` | `--host <ip>`, `--port <p>`, `--db <path>`, `--spec <path>`, `--access-log <path>`, `--app-log <path>`, `--check-only` | Launch the FastAPI + Jinja2 + Chart.js interactive Web Dashboard. |

### PowerShell Quickstart Examples

```powershell
# 1. Initialize starter files
apiscope init --dir ./starter

# 2. Ingest OpenAPI specification
apiscope import sample_app/openapi.yaml --output ./starter/spec_summary.json

# 3. Generate rule-based test suite
apiscope generate sample_app/openapi.yaml --output ./starter/rule_suite.yaml --module orders

# 4. Synthesize AI tests and approve them
apiscope ai-generate sample_app/openapi.yaml --profile examples/m12_sample_app_profile.yaml --output ./starter/ai_proposed.yaml --provider profile-fallback
apiscope approve ./starter/ai_proposed.yaml --output ./starter/ai_approved.yaml

# 5. Execute test suite against live target
apiscope run ./starter/ai_approved.yaml --env-file examples/sample_app.env.yaml --db .apiscope/history.db

# 6. Export multi-format reports
apiscope report --db .apiscope/history.db --format json --output ./starter/report.json
apiscope report --db .apiscope/history.db --format junit --output ./starter/report.xml
apiscope report --db .apiscope/history.db --format html --output ./starter/report.html

# 7. Evaluate module health and zombie APIs
apiscope health --db .apiscope/history.db --spec sample_app/openapi.yaml --access-log sample_app/logs/access.log --app-log sample_app/logs/app.log
apiscope zombie --spec sample_app/openapi.yaml --access-log sample_app/logs/access.log --db .apiscope/history.db

# 8. Start interactive Web Dashboard
apiscope serve --db .apiscope/history.db --spec sample_app/openapi.yaml --access-log sample_app/logs/access.log --app-log sample_app/logs/app.log --port 8080
```

---

## 5. Web Dashboard & REST API

The ApiScope Web Dashboard is a zero-build, server-rendered interface built with **FastAPI**, **Jinja2**, and **Chart.js** (served from static bundles or self-contained templates).

### Launching the Dashboard
```powershell
apiscope serve --db .apiscope/history.db --spec sample_app/openapi.yaml --access-log sample_app/logs/access.log --app-log sample_app/logs/app.log --port 8080
```

### Interactive UI Views
- **`/` (Runs & Trends)**: Visualizes historical pass rates, execution duration trends via Chart.js line charts, and quick-trigger test suite form.
- **`/runs`**: Paginated table of historical test runs with total, passed, failed, and skipped counts.
- **`/runs/{run_id}`**: Detailed drill-down for a single run showing every case result, status badge, HTTP method, URL, elapsed latency, correlation ID, error message, and assertion breakdown.
- **`/health`**: Interactive Module Health Matrix with color-coded verdict badges (`HEALTHY` green, `DEGRADED` amber, `CRITICAL` red), pass rates, endpoint coverage progress bars, and silent failure signal tables.
- **`/zombies`**: Zombie API breakdown grouped by category (`UNUSED`, `UNDOCUMENTED`, `UNTESTED`, `DEPRECATED-BUT-CALLED`).

### REST API Endpoints
The dashboard exposes clean JSON REST endpoints consumed by the frontend and CI systems:
- `GET /api/trends?module=<name>&limit=20`: Pass rate and latency trend series for charting.
- `POST /api/runs`: Trigger an asynchronous or in-process suite execution with JSON payload `{"suite_path": "...", "env_file": "..."}`.
- `GET /api/runs/{run_id}`: Fetch complete execution telemetry and individual case results.
- `GET /api/health`: Compute and return real-time module health verdicts and signal evidence.
- `GET /api/zombies`: Return detected zombie endpoints categorized with access log counts.

---

## 6. Empirical Evaluation Experiment (Planted Bugs & Clean Baseline)

To evaluate ApiScope rigorously against ground truth, an automated 4-way evaluation harness (`src/apiscope/analysis/evaluator_experiment.py`) was executed across **12 planted bugs** in `sample_app` alongside a **clean baseline target** (`StockPile`).

### 12 Planted Bugs in Sample Target Application (`sample_app`)
- **BUG-01 (orders)**: `POST /orders` missing required `quantity` returns `500 Internal Server Error` instead of `422 Unprocessable Entity`.
- **BUG-02 (orders)**: `POST /orders` accepts negative `unit_price: -10.0` and creates order with negative total.
- **BUG-03 (products)**: `GET /products/{product_id}` with invalid ID returns string `"999"` instead of integer `product_id`.
- **BUG-04 (coupons)**: `POST /coupons/redeem` accepts expired coupon `SUMMER2020` without error.
- **BUG-05 (auth)**: `POST /auth/login` accepts any non-empty password for existing users.
- **BUG-06 (products)**: `GET /products/recommendations` takes 1,100ms (exceeds 500ms SLA, tripping Signal S3).
- **BUG-07 (coupons)**: `GET /coupons/legacy-archive` documented in OpenAPI but receives 0 calls in access logs (`UNUSED` zombie).
- **BUG-08 (debug)**: `GET /debug/internal-metrics` active in access logs but absent from OpenAPI (`UNDOCUMENTED` zombie).
- **BUG-09 (system)**: `GET /health` returns `200 OK {"status": "ok"}` while database reports `"database": "down"` (Signal S2).
- **BUG-10 (orders)**: `POST /orders/{order_id}/sync-invoice` returns `200 OK` while logging severe error (Signal S1).
- **BUG-11 (users)**: `PUT /users/{user_id}/role` allows standard users to escalate to `admin` without authorization.
- **BUG-12 (products)**: `GET /products/v1-catalog` marked `deprecated: true` but actively called in access logs (`DEPRECATED-BUT-CALLED` zombie).

### 4-Way Comparative Results

| Test Strategy | Test Suite Source | Tests Executed | Bugs Detected | Detection Rate (%) | False Positives | FP Rate (%) | Module Health Impact |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **Strategy 1: Planted Bug Probes** | `examples/m13_planted_bug_probes.yaml` | 15 | **10 / 12** | **83.3%** | **0** | **0.0%** | orders, products, coupons, auth, users flagged |
| **Strategy 2: Rule-Based Generation (M5)** | Generated from `sample_app/openapi.yaml` | 27 | **5 / 12** | **41.7%** | **0** | **0.0%** | orders, products, coupons flagged |
| **Strategy 3: AI-Assisted Generation (M12)** | Synthesized via Project Profile & approved | 25 | **8 / 12** | **66.7%** | **0** | **0.0%** | orders, products, coupons, auth, users flagged |
| **Strategy 4: Combined Hybrid Pipeline** | Strategy 1 + 2 + 3 + Diagnostics (M8/M9/M10) | 67 | **12 / 12** | **100.0%** | **0** | **0.0%** | **All 12 planted bugs detected** |

### Specificity & False Positive Control on Clean Target (`StockPile`)
To verify that ApiScope's detectors do not generate false alarms on well-behaved software, the identical analysis pipeline was run against the clean **StockPile Inventory Management API**:
- **Planted Bugs in StockPile**: 0
- **False Positives Detected**: **0**
- **Module Health Verdicts**: **ALL 5 MODULES HEALTHY** (`inventory`, `suppliers`, `audit`, `auth`, `reports`).
- **Confirmation**: High specificity (zero false positive rate) confirms that detections on `sample_app` reflect genuine software defects rather than detector noise.

The complete empirical evaluation report is available at [`docs/EVALUATION_REPORT.md`](file:///E:/3rd%20sem%20acads/SE/APIScope/docs/EVALUATION_REPORT.md).

---

## 7. Distributed Trace Correlation (`X-Correlation-ID`)

ApiScope implements end-to-end distributed trace correlation to bridge test execution and backend application logs:
1. When `RunService` executes a test case, it assigns a unique UUID (or accepts an injected generator) and attaches an `X-Correlation-ID: <corr_id>` header to the outbound HTTP request.
2. The correlation ID is recorded in the SQLite database (`case_results.correlation_id`).
3. Backend applications (such as `sample_app`) include `[corr_id=<corr_id>]` when recording error events in `app.log`.
4. `SilentFailureService` (Signal S1) parses backend error events and matches them directly with the corresponding test execution via the correlation ID, producing airtight evidence linking test cases to backend failures.

---

## 8. Verification & Quality Metrics

- **Test Suite**: **323 passed tests**, 0 failures across unit, integration, and system test suites.
- **Code Coverage**: **89% total repository coverage** (exceeds the 88% project baseline).
- **Analysis Experiment Harness Coverage**: **90% coverage** on `evaluator_experiment.py`.
- **Code Quality**: 100% clean with `ruff check .` and `ruff format --check .`.
- **Type Safety**: Fully typed with strict type annotations across all modules.
- **CI/CD Pipeline**: GitHub Actions workflow running on Ubuntu, Python 3.11 and Python 3.12 (`.github/workflows/ci.yml`).
- **Deterministic Artifacts**: Pristine log verification (`access.log` 15 lines, `app.log` 8 lines).

---

## 9. Getting Started

### Prerequisites
- Python 3.11, 3.12, or 3.13 installed.
- Windows PowerShell (or bash/zsh on Linux/macOS).

### Installation

```powershell
# 1. Clone repository
git clone https://github.com/organization/apiscope.git
cd apiscope

# 2. Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install in editable mode with development dependencies
pip install -e ".[dev]"

# 4. Verify installation
apiscope version
```

### Running the Test Suite & Verification

```powershell
# Run full pytest suite with coverage
pytest --cov=src/apiscope --cov-report=term-missing

# Run Ruff linter and code formatting checks
ruff check .
ruff format --check .

# Run 4-way evaluation experiment system test
pytest tests/system/test_end_to_end_evaluation.py -v
```

---

## 10. Repository Structure

```
ApiScope/
├── pyproject.toml              # Build metadata, dependencies, ruff/pytest/cov configuration
├── README.md                   # Comprehensive project documentation and evaluation summary
├── .gitignore                  # Git ignore rules for Python, SQLite, caches, and venv
├── .env.example                # Template environment variables
├── .github/
│   └── workflows/
│       └── ci.yml              # GitHub Actions CI matrix (Python 3.11 & 3.12, ruff, pytest)
│
├── src/
│   └── apiscope/
│       ├── __init__.py         # Package entry and version metadata
│       ├── core/               # Executor, AssertionEngine, VariableResolver, Extractor, RunService
│       ├── definitions/        # TestDefinition, TestCollection, YAML loader, DataLoader, Environment
│       ├── generators/         # OpenAPIImporter, RuleGenerator (M5), AiGenerator & Profile (M12)
│       ├── analysis/           # HealthService (M8), ZombieService (M9), SilentFailure (M10), Experiment
│       ├── data/               # SQLite connection manager and RunRepository
│       ├── reports/            # Multi-format report exporter (JSON, JUnit XML, HTML)
│       ├── cli/                # 11 Typer commands (version, init, run, report, health, zombie, serve...)
│       └── web/                # FastAPI application, SSR Jinja2 templates, Chart.js trends, REST API
│
├── sample_app/                 # Target application with 12 planted bugs, logs & OpenAPI spec
│   ├── app.py                  # In-memory FastAPI application with 12 planted bugs
│   ├── openapi.yaml            # OpenAPI 3 specification documenting sample_app endpoints
│   └── logs/
│       ├── access.log          # Common Log Format server access log (15 lines)
│       └── app.log             # Structured application event log (8 lines)
│
├── tests/
│   ├── unit/                   # 150+ isolated component tests
│   ├── integration/            # Multi-component, CLI, and database integration tests
│   └── system/                 # 4-way evaluation experiment, trace correlation & full pipeline tests
│
├── examples/                   # Sample test suites, environment configs, and evaluation profiles
└── docs/                       # Architectural designs, requirements, codebase map, devlog & evaluation report
    ├── CODEBASE_MAP.md         # Exhaustive architecture and module mapping
    ├── DEVLOG.md               # Cumulative implementation log across all development tasks
    └── EVALUATION_REPORT.md    # Measured 4-way planted-bug & clean-baseline evaluation report
```

---

## 11. Authors & Academic Context

ApiScope was created as a software engineering capstone and research platform for automated API testing and module health monitoring.
Engineered with clean, explicit, object-oriented Python, zero external JavaScript build chains, and complete architectural decoupling.
>>>>>>> 0b29647 (feat: complete ApiScope platform (Modules M1-M13, CLI, Web Dashboard, Live LLM Generator & Evaluation))
