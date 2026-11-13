# ApiScope Codebase Map (Final Architecture & API Inventory)

> **Generated on:** 2026-11-13  
> **Repository:** `ApiScope` — Automated API Testing, Module Health Monitoring and Analysis Platform  
> **Python Version:** 3.11+ (tested on Python 3.11, 3.12, 3.13)  
> **Audit Status:** Verified pre-submission ground truth derived directly from filesystem AST inspection  
> **Total Verified Tests:** 323 passing tests (0 failures, 0 errors, 89% total code coverage)  

---

## 1. Complete Repository Directory Tree

Excludes `.git`, `.venv`, `__pycache__`, `.pytest_cache`, `.ruff_cache`, and `*.egg-info`.

```text
ApiScope/
├── .apiscope
│   └── history.db
├── .github
│   └── workflows
│       └── ci.yml
├── .vscode
│   └── settings.json
├── docs
│   ├── design
│   │   └── README.md
│   ├── project-management
│   │   └── README.md
│   ├── requirements
│   │   └── README.md
│   ├── testing
│   │   └── README.md
│   ├── AUDIT_04V.md
│   ├── CODEBASE_MAP.md
│   ├── DEVLOG.md
│   └── EVALUATION_REPORT.md
├── examples
│   ├── m11_dashboard_demo.md
│   ├── m12_ai_proposed_suite.yaml
│   ├── m12_sample_app_profile.yaml
│   ├── m13_planted_bug_probes.yaml
│   ├── m4_chaining_suite.yaml
│   ├── m4_orders_data.csv
│   ├── m4_orders_data.json
│   ├── m5_generated_suite.yaml
│   ├── m5_sample_openapi.yaml
│   ├── m6_m7_cli_commands.md
│   ├── m6_m7_demo_env.yaml
│   ├── m8_health_demo.md
│   ├── m9_m10_analysis_commands.md
│   ├── m9_m10_health_probe_degraded.json
│   ├── README.md
│   ├── stockpile_openapi.yaml
│   └── stockpile_profile.yaml
├── sample_app
│   ├── logs
│   │   ├── access.log
│   │   └── app.log
│   ├── __init__.py
│   ├── app.py
│   ├── openapi.yaml
│   └── README.md
├── src
│   └── apiscope
│       ├── analysis
│       │   ├── __init__.py
│       │   ├── evaluator_experiment.py
│       │   ├── health_models.py
│       │   ├── health_service.py
│       │   ├── path_normalizer.py
│       │   ├── silent_failure_service.py
│       │   └── zombie_service.py
│       ├── cli
│       │   ├── __init__.py
│       │   └── main.py
│       ├── core
│       │   ├── assertions
│       │   │   ├── __init__.py
│       │   │   ├── engine.py
│       │   │   ├── evaluators.py
│       │   │   └── models.py
│       │   ├── __init__.py
│       │   ├── config.py
│       │   ├── contracts.py
│       │   ├── exceptions.py
│       │   ├── executor.py
│       │   ├── extractor.py
│       │   ├── logging_config.py
│       │   ├── models.py
│       │   ├── results.py
│       │   ├── run_service.py
│       │   └── variables.py
│       ├── data
│       │   ├── __init__.py
│       │   ├── connection.py
│       │   ├── repositories.py
│       │   └── repository.py
│       ├── definitions
│       │   ├── __init__.py
│       │   ├── collection.py
│       │   ├── data_loader.py
│       │   ├── environment.py
│       │   ├── models.py
│       │   └── yaml_loader.py
│       ├── generators
│       │   ├── __init__.py
│       │   ├── ai_generator.py
│       │   ├── openapi_importer.py
│       │   ├── project_profile.py
│       │   ├── rule_generator.py
│       │   └── service.py
│       ├── reports
│       │   ├── __init__.py
│       │   ├── models.py
│       │   └── report_service.py
│       ├── web
│       │   ├── __init__.py
│       │   ├── app.py
│       │   └── templates.py
│       └── __init__.py
├── tests
│   ├── integration
│   │   ├── __init__.py
│   │   ├── test_ai_workflow_and_governance.py
│   │   ├── test_cli_health.py
│   │   ├── test_cli_run_and_report.py
│   │   ├── test_cli_zombie_and_health_integration.py
│   │   ├── test_generator_and_cli.py
│   │   ├── test_run_service.py
│   │   ├── test_sample_app.py
│   │   └── test_web_trigger_and_cli_serve.py
│   ├── system
│   │   ├── __init__.py
│   │   └── test_end_to_end_evaluation.py
│   ├── unit
│   │   ├── __init__.py
│   │   ├── test_ai_generator.py
│   │   ├── test_assertions.py
│   │   ├── test_core.py
│   │   ├── test_data_loader.py
│   │   ├── test_definitions.py
│   │   ├── test_executor.py
│   │   ├── test_extractor.py
│   │   ├── test_health_service.py
│   │   ├── test_openapi_importer.py
│   │   ├── test_path_normalizer.py
│   │   ├── test_project_profile_and_polymorphic.py
│   │   ├── test_report_service.py
│   │   ├── test_repositories.py
│   │   ├── test_results.py
│   │   ├── test_rule_generator.py
│   │   ├── test_silent_failure_service.py
│   │   ├── test_smoke.py
│   │   ├── test_web_dashboard.py
│   │   └── test_zombie_service.py
│   ├── __init__.py
│   └── conftest.py
├── .coverage
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

---

## 2. Configuration & Build Specification (`pyproject.toml`)

- **Build System**: `setuptools>=61.0`, `setuptools.build_meta`.
- **Package Name & Version**: `apiscope` v0.1.0 (Python `>=3.11`).
- **Core Production Dependencies**:
  - `fastapi>=0.115.0` (Web dashboard framework)
  - `uvicorn>=0.30.0` (ASGI production server)
  - `typer>=0.12.0` (Rich CLI presentation framework)
  - `jinja2>=3.1.0` (Server-side autoescaped template engine)
  - `httpx>=0.27.0` (Synchronous and asynchronous HTTP client)
  - `pyyaml>=6.0.1` (Safe YAML loader and dumper)
  - `jsonschema>=4.23.0` (Draft 7/2020-12 schema validation)
  - `jsonpath-ng>=1.6.0` (JSONPath evaluation for assertions and extractor)
  - `rich>=13.0.0` (Console table and formatting renderer)
  - `python-multipart>=0.0.9` (Form data and multipart parser for FastAPI)
- **Development & Testing Dependencies**:
  - `pytest>=8.0.0`
  - `pytest-cov>=5.0.0`
  - `ruff>=0.5.0`
- **Tool Configurations**:
  - `tool.ruff`: Target Python 3.11, line-length = 88, pycodestyle (`E`, `W`), Pyflakes (`F`), isort (`I`), bandit (`S`), flake8-bugbear (`B`).
  - `tool.pytest.ini_options`: `testpaths = ["tests"]`, strict markers, anyio integration.
  - `tool.coverage.run`: `source = ["src/apiscope"]`, branch coverage enabled.

---

## 3. Source Code Inventory & API Signatures

### 3.1. Presentation Layer (`src/apiscope/cli/`, `src/apiscope/web/`)

#### `src/apiscope/cli/main.py`
- **CLI Commands (Typer)**:
  - `version()`: Display version and system environment info.
  - `init(directory, force)`: Initialize starter test suite and environment configuration.
  - `run(suite_path, env_file, env, module, tag, data_file, db, output, format, strict, include_proposed)`: Execute test suite with chained resolution, telemetry persistence, and exit codes.
  - `report(run_id, db, format, output)`: Export execution reports in JSON, JUnit XML, or HTML format.
  - `import_cmd(spec_path, output)`: Validate and summarize OpenAPI 3 specifications.
  - `generate_cmd(spec_path, output, module)`: Generate rule-based positive, negative, and boundary tests.
  - `ai_generate(spec_path, profile, output, provider, model, api_key, module)`: Synthesize domain-aware test definitions with proposed status.
  - `approve(suite_path, output)`: Promote proposed AI tests to approved executable status.
  - `zombie(spec, access_log, run_id, db, module, output, strict)`: Categorize unused, undocumented, untested, and deprecated endpoints.
  - `health(module, run_id, db, spec, threshold, baseline_p95, output, strict, access_log, app_log, health_file)`: Calculate module health verdicts and evaluate silent failure signals.
  - `serve(host, port, db, spec, access_log, app_log, health_file, baseline_p95, check_only)`: Launch FastAPI + Jinja2 + Chart.js web dashboard.

#### `src/apiscope/web/app.py`
- **class DashboardConfig**: Configuration dataclass encapsulating DB, spec, and log file paths.
- **class TriggerRunRequest(BaseModel)**: Pydantic schema for `POST /api/runs`.
- **class ApproveSuiteRequest(BaseModel)**: Pydantic schema for `POST /api/approve`.
- **class AiGenerateSuiteRequest(BaseModel)**: Pydantic schema for `POST /api/ai-generate`.
- **def create_web_app(config, run_service)**: FastAPI application factory registering web pages and REST API routes:
  - `GET /`, `GET /runs`, `GET /runs/{run_id}`, `GET /health`, `GET /zombies`
  - `GET /api/trends`, `POST /api/runs`, `GET /api/runs/{run_id}`, `GET /api/health`, `GET /api/zombies`, `POST /api/approve`, `POST /api/ai-generate`

#### `src/apiscope/web/templates.py`
- **def create_jinja_environment()**: Factory initializing Jinja2 `Environment` with strict `autoescape=True` and embedded production templates (`base.html`, `runs.html`, `run_detail.html`, `health.html`, `zombies.html`).

---

### 3.2. Application Services Layer (`src/apiscope/analysis/`, `src/apiscope/generators/`, `src/apiscope/reports/`)

#### `src/apiscope/analysis/evaluator_experiment.py`
- **class StrategyEvaluationMetrics**: Dataclass holding execution duration, tests executed, bug counts, and false positives.
- **class EvaluationExperimentReport**: Dataclass aggregating metrics across Strategies 1, 2, 3, and 4, plus clean baseline summary.
- **def run_planted_bug_experiment(base_dir, simulated_delay_sec)**: Reproducible in-process evaluation runner executing all 4 strategies and clean target.
- **def render_evaluation_markdown(report)**: Markdown renderer generating `docs/EVALUATION_REPORT.md`.

#### `src/apiscope/analysis/health_models.py`
- **class HealthVerdict(StrEnum)**: `HEALTHY`, `HEALTHY_WITH_WARNINGS`, `UNHEALTHY`, `INCOMPLETE`.
- **class CategoryCheckStats**: Statistics tracker for functional tests, coverage, zombies, and silent signals.
- **class ModuleHealthReport**: Aggregated module health verdict with reason list and evidence records.

#### `src/apiscope/analysis/health_service.py`
- **class HealthService**: Core evaluation engine combining test pass rates, endpoint coverage thresholds, zombie findings, and silent failure signals into deterministic verdicts.
  - `evaluate_module(...)`: Evaluate a single named module.
  - `evaluate_all_modules(...)`: Evaluate all modules present in spec or test results.

#### `src/apiscope/analysis/path_normalizer.py`
- **def extract_url_path(url_or_path)**: Extract clean URI path stripped of scheme, host, port, and query strings.
- **def compile_spec_path_regex(template_path)**: Compile OpenAPI template path into matching regex.
- **def match_path_to_spec(concrete_path, spec_paths)**: Match concrete URL to OpenAPI path template prioritizing static segments.
- **def normalize_unmatched_path(concrete_path)**: Fallback normalizer replacing UUID and integer segments with parameter placeholders.

#### `src/apiscope/analysis/silent_failure_service.py`
- **class SilentSignalType(StrEnum)**: `S1_LOG_ERROR`, `S2_DEPENDENCY_PROBE`, `S3_LATENCY_DRIFT`, `S4_SUSPICIOUS_2XX`.
- **class AppLogEntry**: Parsed structured application log event.
- **class SilentFailureSignal**: Diagnostic signal evidence citing module, endpoint, and trace ID.
- **class SilentFailureReport**: Aggregated report with module filtering and secret masking.
- **def parse_app_log(content)**: Parser for structured application log lines.
- **def load_app_log(file_path, base_dir)**: Safe file loader with size bounds and directory traversal protection.
- **class SilentFailureService**: Signal detection engine correlating application logs (`X-Correlation-ID`), health probes, response times, and 2xx error bodies.

#### `src/apiscope/analysis/zombie_service.py`
- **class ZombieClass(StrEnum)**: `UNUSED`, `UNDOCUMENTED`, `UNTESTED`, `DEPRECATED_BUT_CALLED`.
- **class AccessLogEntry**: Parsed server access log line.
- **class ZombieFinding**: Finding record with normalized path, method, module, and hit count.
- **class ZombieReport**: Aggregated zombie report.
- **def parse_access_log(content)**: Parser for Common/Combined and JSON Lines access logs.
- **def load_access_log(file_path, base_dir)**: Safe access log file loader.
- **class ZombieService**: Reconciliation engine cross-referencing OpenAPI specs, access logs, and test execution runs.

#### `src/apiscope/generators/ai_generator.py`
- **class LLMConfig**: LLM configuration supporting Gemini, OpenAI, and profile-fallback providers.
- **class LLMProviderProtocol(Protocol)**: Interface for LLM adapters.
- **class LiveHttpLLMProvider(LLMProviderProtocol)**: HTTPS transport client sending raw API keys and scrubbing secret leakage.
- **class ProfileDrivenSynthesisProvider(LLMProviderProtocol)**: Deterministic fallback synthesizer driven by ProjectProfile rules.
- **def build_ai_generation_prompt(spec, profile, module)**: Structured prompt synthesizer.
- **class AiGeneratorService**: Lifecycle manager enforcing `source: ai-generated`, `status: proposed`, max 200 tests limit, and approval promotion.

#### `src/apiscope/generators/openapi_importer.py`
- **class ParameterSpec**: Parameter metadata (name, in, required, schema).
- **class EndpointSpec**: Normalized endpoint definition with request body and response schemas.
- **class OpenAPISpec**: Ingested OpenAPI 3 specification model.
- **def parse_openapi_spec(data)**: Parser with recursive `$ref` resolution and circular reference guards.
- **def load_openapi_spec(file_path, base_dir)**: Safe YAML/JSON spec loader.

#### `src/apiscope/generators/project_profile.py`
- **class RoleRule**: User role definitions and access permissions.
- **class StateTransitionRule**: Valid and invalid state transition rules for business workflows.
- **class BusinessInvariantRule**: Domain boundary conditions and numerical constraints.
- **class ProjectProfile**: Complete project domain profile.
- **def parse_project_profile(data)**: Schema parser for profile YAML.
- **def load_project_profile(file_path, base_dir)**: Safe profile loader.

#### `src/apiscope/generators/rule_generator.py`
- **def synthesize_valid_value(schema, field_name)**: Recursive value synthesizer resolving primitives, enums, `allOf`, `oneOf`, and `anyOf`.
- **class RuleBasedGenerator**: Synthesizer generating Positive tests (2xx + schema), Negative tests (5 categories: missing body, invalid type, 404 ID, missing auth, 405 method), Boundary tests (min/max, length, enum), and CRUD chains.

#### `src/apiscope/generators/service.py`
- **def test_definition_to_dict(test_def)**: Serializer converting TestDefinition dataclass to clean YAML dict.
- **def dump_tests_to_yaml(tests, collection_name, module)**: Lossless YAML dumper.
- **class GeneratorService**: Application service coordinating OpenAPI spec import and rule-based test generation.

#### `src/apiscope/reports/models.py`
- **class TrendPoint**: Dataclass holding run historical metrics (pass rate, avg latency, p95).
- **class TrendSummary**: Historical trends aggregator.

#### `src/apiscope/reports/report_service.py`
- **class ReportFormat(StrEnum)**: `JSON`, `JUNIT`, `HTML`.
- **def render_json_report(run_result, trends)**: JSON report generator with Expected vs. Actual diffs.
- **def render_junit_report(run_result)**: Standard JUnit XML exporter.
- **def render_html_report(run_result, trends)**: Self-contained autoescaped HTML report with embedded CSS.
- **class ReportService**: Exporter coordinating report rendering and disk export.

---

### 3.3. Core Engine Layer (`src/apiscope/core/`, `src/apiscope/definitions/`)

#### `src/apiscope/core/config.py`
- **class GlobalConfig**: Global system settings (timeouts, secret masking, connection pool size).
- **def validate_safe_path(base_dir, ref_path)**: Strict path traversal prevention ensuring paths resolve within authorized roots.

#### `src/apiscope/core/contracts.py`
- **class RequestExecutorProtocol(Protocol)**: Interface for executing HTTP requests.
- **class AssertionEngineProtocol(Protocol)**: Interface for evaluating assertion rules.
- **class VariableResolverProtocol(Protocol)**: Interface for interpolating template variables.
- **class ExtractorProtocol(Protocol)**: Interface for extracting response values.

#### `src/apiscope/core/exceptions.py`
- **class ApiScopeError(Exception)**: Base exception for all domain errors.
- **class ConfigurationError**, **class PathTraversalError**, **class SpecParseError**, **class ExecutionError**, **class TimeoutError**, **class SecurityViolationError**, **class ExtractionError**, **class AssertionEvaluationError**.

#### `src/apiscope/core/executor.py`
- **class TargetAccessPolicy**: SSRF guardrail blocking loopback/private IP addresses unless explicitly permitted.
- **class RequestExecutor(RequestExecutorProtocol)**: Secure HTTP client with redirect control, timeout enforcement, session reuse, and secret masking.

#### `src/apiscope/core/extractor.py`
- **class Extractor(ExtractorProtocol)**: Variable extraction engine evaluating JSONPath, regex, headers, and status code from responses.

#### `src/apiscope/core/logging_config.py`
- **class SecretMaskingFilter(logging.Filter)**: Logging filter redacting known secrets and tokens to `***`.

#### `src/apiscope/core/models.py`
- **class HttpMethod(StrEnum)**: `GET`, `POST`, `PUT`, `DELETE`, `PATCH`, `HEAD`, `OPTIONS`.
- **class Authentication(ABC)**: Base auth class (`to_headers()`, `to_query_params()`).
- **class BearerAuth**, **class ApiKeyAuth**, **class BasicAuth**.
- **class SecretString**: Wrapper encapsulating sensitive credentials, preventing raw leakage in `__repr__` and `__str__`.
- **class HttpRequest**, **class HttpResponse**.

#### `src/apiscope/core/results.py`
- **class CaseStatus(StrEnum)**: `PASSED`, `FAILED`, `ERROR`, `SKIPPED`.
- **class CaseResult**: Test case execution outcome with assertions, elapsed time, error message, and correlation ID.
- **class RunResult**: Aggregated suite outcome with counts, duration, and case list.

#### `src/apiscope/core/run_service.py`
- **class RunService**: Orchestration engine executing test collections with variable resolution, chained extraction, data-driven row iteration, `X-Correlation-ID` injection, and governance skipping.

#### `src/apiscope/core/variables.py`
- **class VariableResolver(VariableResolverProtocol)**: Recursive template resolver replacing `{variable}` patterns with typed or string values.

#### `src/apiscope/core/assertions/engine.py`
- **class AssertionEngine(AssertionEngineProtocol)**: Assertion dispatcher managing evaluators and evaluating rule sets.

#### `src/apiscope/core/assertions/evaluators.py`
- **class BaseAssertionEvaluator(ABC)**: Base assertion evaluator.
- **class StatusAssertionEvaluator**: Evaluates HTTP status code (`equals`, `not_equals`, `in`, `not_in`).
- **class ResponseTimeAssertionEvaluator**: Evaluates response duration (`less_than`, `greater_than`).
- **class HeaderAssertionEvaluator**: Evaluates header presence and matching (`exists`, `equals`, `contains`, `matches`).
- **class JsonPathAssertionEvaluator**: Evaluates JSONPath queries (`equals`, `contains`, `exists`, `type`, `count`).
- **class JsonSchemaAssertionEvaluator**: Evaluates JSON response bodies against Draft 7/2020-12 JSON Schema.

#### `src/apiscope/core/assertions/models.py`
- **class AssertionRule**: Declarative assertion specification.
- **class AssertionResult**: Individual assertion evaluation outcome.

#### `src/apiscope/definitions/collection.py`
- **class TestCollection**: Named suite container holding test definitions and metadata.
- **def parse_collection(data)**: Parser for collection YAML.
- **def load_collection(file_path, base_dir)**: Safe collection loader.

#### `src/apiscope/definitions/data_loader.py`
- **def load_data_rows(file_path, base_dir)**: Multi-format dataset loader supporting CSV and JSON data files with type coercion.

#### `src/apiscope/definitions/environment.py`
- **class Environment**: Environment configuration managing variables and secret redaction.
- **class EnvironmentManager**: Registry managing multiple named environments.
- **def load_environment(file_path)**: Safe environment YAML loader.

#### `src/apiscope/definitions/models.py`
- **class DefinitionSource(StrEnum)**: `HANDWRITTEN`, `RULE_GENERATED`, `AI_GENERATED`.
- **class DefinitionStatus(StrEnum)**: `DRAFT`, `PROPOSED`, `APPROVED`.
- **class TestRequestDefinition**: Request template dataclass.
- **class TestDefinition**: Executable test scenario model.

#### `src/apiscope/definitions/yaml_loader.py`
- **def parse_test_definition(data, file_path)**: Parser for individual test YAML.
- **def load_test_definition(file_path)**: Safe single-test loader.

---

### 3.4. Data Layer (`src/apiscope/data/`)

#### `src/apiscope/data/connection.py`
- **class DatabaseManager**: Thread-safe SQLite connection manager with WAL mode, foreign keys, and context-managed transactions.

#### `src/apiscope/data/repositories.py`
- **class RunRepository(RunRepositoryProtocol)**: SQLite persistence repository storing test runs, individual case results, and assertion breakdowns; computing historical trends; and managing automatic schema migrations (`correlation_id`).

#### `src/apiscope/data/repository.py`
- **class RunRepositoryProtocol(Protocol)**: Interface for test run telemetry persistence.

---

### 3.5. Sample Target Application (`sample_app/`)

#### `sample_app/app.py`
- **def reset_state()**: Re-initializes in-memory databases (users, products, orders, coupons) for clean test isolation.
- **def authenticate_bearer(authorization)**: Token authenticator for sample app endpoints.
- **def log_app_event(app_instance, level, module, message)**: Structured event logger writing to `app.log`.
- **def create_sample_app(app_log_path, access_log_path, simulated_delay_sec)**: FastAPI factory implementing all 15 endpoints and the 12 planted bugs (`BUG-01` through `BUG-12`).

---

## 4. Test Suite Inventory (`tests/`)

The ApiScope test suite consists of **323 total tests** distributed across 28 test files in 3 test tiers:

| Test File Path | Category | Test Count |
| :--- | :--- | :---: |
| `tests/integration/test_ai_workflow_and_governance.py` | `integration` | 3 |
| `tests/integration/test_cli_health.py` | `integration` | 4 |
| `tests/integration/test_cli_run_and_report.py` | `integration` | 11 |
| `tests/integration/test_cli_zombie_and_health_integration.py` | `integration` | 2 |
| `tests/integration/test_generator_and_cli.py` | `integration` | 4 |
| `tests/integration/test_run_service.py` | `integration` | 12 |
| `tests/integration/test_sample_app.py` | `integration` | 13 |
| `tests/integration/test_web_trigger_and_cli_serve.py` | `integration` | 3 |
| `tests/system/test_end_to_end_evaluation.py` | `system` | 3 |
| `tests/unit/test_ai_generator.py` | `unit` | 12 |
| `tests/unit/test_assertions.py` | `unit` | 53 |
| `tests/unit/test_core.py` | `unit` | 33 |
| `tests/unit/test_data_loader.py` | `unit` | 10 |
| `tests/unit/test_definitions.py` | `unit` | 44 |
| `tests/unit/test_executor.py` | `unit` | 38 |
| `tests/unit/test_extractor.py` | `unit` | 8 |
| `tests/unit/test_health_service.py` | `unit` | 8 |
| `tests/unit/test_openapi_importer.py` | `unit` | 6 |
| `tests/unit/test_path_normalizer.py` | `unit` | 4 |
| `tests/unit/test_project_profile_and_polymorphic.py` | `unit` | 4 |
| `tests/unit/test_report_service.py` | `unit` | 7 |
| `tests/unit/test_repositories.py` | `unit` | 7 |
| `tests/unit/test_results.py` | `unit` | 2 |
| `tests/unit/test_rule_generator.py` | `unit` | 8 |
| `tests/unit/test_silent_failure_service.py` | `unit` | 6 |
| `tests/unit/test_smoke.py` | `unit` | 5 |
| `tests/unit/test_web_dashboard.py` | `unit` | 9 |
| `tests/unit/test_zombie_service.py` | `unit` | 4 |
| **TOTAL** | **All Categories** | **323** |

### Test Distribution by Tier
- **Unit Tests (`tests/unit/`)**: 268 tests (isolated domain models, evaluators, parsers, resolvers)
- **Integration Tests (`tests/integration/`)**: 52 tests (CLI commands, multi-step chains, database repositories, sample app)
- **System Tests (`tests/system/`)**: 3 tests (4-way evaluation experiment, trace correlation, complete user pipeline)
- **Overall Result**: **323 passed, 0 failed, 0 errors** (100% pass rate).

---

## 5. Architectural & Layering Rules

ApiScope strictly enforces the following architectural guardrails:
1. **Layer Dependency Direction**: Presentation (`cli`, `web`) -> Services (`analysis`, `generators`, `reports`) -> Core Engine (`core`, `definitions`) -> Data (`data`). No backward dependencies exist.
2. **Persistence Isolation**: SQL queries (`CREATE`, `SELECT`, `INSERT`) exist exclusively in `src/apiscope/data/repositories.py`. No presentation or service module directly accesses `sqlite3`.
3. **Execution Decoupling**: Both the Typer CLI (`apiscope run`) and FastAPI Dashboard (`POST /api/runs`) execute tests through the shared `RunService`.
4. **Security & Path Sandboxing**: All file access (specifications, profiles, test suites, logs, reports) passes through `validate_safe_path()`, preventing directory traversal (`..`) attacks.
5. **Secret Redaction**: Sensitive tokens and credentials wrapped in `SecretString` are scrubbed to `***` before appearing in logs, SQLite history, exported reports, CLI tables, or Web UI views.
