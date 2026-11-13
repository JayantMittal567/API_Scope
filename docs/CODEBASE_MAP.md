# ApiScope Codebase Map (Tasks 00 to 04)

> **Generated on:** 2026-11-12  
> **Repository:** `ApiScope` — Automated API Testing, Module Health Monitoring and Analysis Platform  
> **Python Version:** 3.11+  
> **Audit State:** Read-only inspection of source and test files on disk  

---

## 1. Repository Directory Tree

Excludes `.git`, `.venv`, `__pycache__`, `.pytest_cache`, `.ruff_cache`, and `*.egg-info`.

```text
ApiScope/
├── .env.example
├── .gitignore
├── README.md
├── pyproject.toml
├── .vscode/
│   └── settings.json
├── docs/
│   ├── design/
│   │   └── README.md
│   ├── project-management/
│   │   └── README.md
│   ├── requirements/
│   │   └── README.md
│   └── testing/
│       └── README.md
├── examples/
│   └── README.md
├── sample_app/
│   └── README.md
├── src/
│   └── apiscope/
│       ├── __init__.py
│       ├── analysis/
│       │   └── __init__.py
│       ├── cli/
│       │   ├── __init__.py
│       │   └── main.py
│       ├── core/
│       │   ├── __init__.py
│       │   ├── config.py
│       │   ├── contracts.py
│       │   ├── exceptions.py
│       │   ├── executor.py
│       │   ├── logging_config.py
│       │   ├── models.py
│       │   ├── variables.py
│       │   └── assertions/
│       │       ├── __init__.py
│       │       ├── engine.py
│       │       ├── evaluators.py
│       │       └── models.py
│       ├── data/
│       │   ├── __init__.py
│       │   ├── connection.py
│       │   └── repository.py
│       ├── definitions/
│       │   ├── __init__.py
│       │   ├── collection.py
│       │   ├── environment.py
│       │   ├── models.py
│       │   └── yaml_loader.py
│       ├── generators/
│       │   └── __init__.py
│       ├── reports/
│       │   └── __init__.py
│       └── web/
│           └── __init__.py
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── integration/
    │   └── __init__.py
    ├── system/
    │   └── __init__.py
    └── unit/
        ├── __init__.py
        ├── test_assertions.py
        ├── test_core.py
        ├── test_definitions.py
        ├── test_executor.py
        └── test_smoke.py
```

---

## 2. Configuration Summary (`pyproject.toml`)

- **Build System**: `setuptools>=61.0`, `setuptools.build_meta`.
- **Package Name & Version**: `apiscope` v0.1.0 (Python `>=3.11`).
- **Core Dependencies**:
  - `fastapi>=0.115.0`
  - `uvicorn>=0.30.0`
  - `typer>=0.12.0`
  - `jinja2>=3.1.0`
  - `httpx>=0.27.0`
  - `pyyaml>=6.0.0`
  - `jsonschema>=4.20.0`
  - `jsonpath-ng>=1.6.0`
  - `pydantic>=2.7.0`
  - `python-dotenv>=1.0.0`
- **Optional Dev Dependencies**:
  - `pytest>=8.0.0`
  - `pytest-cov>=5.0.0`
  - `ruff>=0.6.0`
- **Console Script**: `apiscope = "apiscope.cli.main:main"`.
- **Pytest Configuration (`[tool.pytest.ini_options]`)**:
  - `minversion = "8.0"`
  - `testpaths = ["tests"]`
  - `pythonpath = ["src"]`
  - `python_files = ["test_*.py", "*_test.py"]`
  - `addopts = "-ra --strict-markers"`
  - `norecursedirs`: `.git`, `.venv`, `venv`, `__pycache__`, `build`, `dist`, `sample_app`, `docs`, `examples`, `$RECYCLE.BIN`, `System Volume Information`, `*StockPile*`.
  - `filterwarnings`: ignores `starlette.testclient` and `httpx2` deprecation warnings.
- **Coverage Configuration (`[tool.coverage.run]` & `[tool.coverage.report]`)**:
  - `source = ["src/apiscope"]`
  - `branch = true`
  - `show_missing = true`
- **Ruff Configuration (`[tool.ruff]`)**:
  - `target-version = "py311"`
  - `line-length = 88`
  - `select = ["E", "W", "F", "I", "B", "C4", "UP", "ARG", "SIM"]`
  - `known-first-party = ["apiscope"]`

---

## 3. Per-File Source Code Inventory (`src/apiscope/`)

### 3.1. Root & CLI

#### `src/apiscope/__init__.py`
- **Variables**: `__version__ = "0.1.0"`.
- **Purpose**: Root package marker and version declaration.

#### `src/apiscope/cli/__init__.py`
- **Exports**: `app` from `apiscope.cli.main`.

#### `src/apiscope/cli/main.py`
- **Imports**: `typer`.
- **Variables**: `app = typer.Typer(name="apiscope", ...)`
- **Functions**:
  - `callback() -> None`: Root Typer callback ensuring `apiscope` handles subcommands cleanly.
  - `version() -> None`: CLI command printing `ApiScope version 0.1.0`.
  - `main() -> None`: Console script entrypoint executing `app()`.

---

### 3.2. Core Layer (`src/apiscope/core/`)

#### `src/apiscope/core/__init__.py`
- **Exports**: Models, auth, exceptions, configuration, logging, protocols, executor, variable resolver, and assertion engine components.
- *Audit Note*: Contains a duplicate import block importing `AssertionEngine`, `AssertionResult`, `AssertionStatus`, `AssertionSummary`, `BaseAssertionEvaluator` twice (lines 8–14 and lines 51–57), triggering Ruff `F811` and `I001` errors.

#### `src/apiscope/core/config.py`
- **Classes**:
  - `AppConfig`: Dataclass holding application configuration.
    - Fields: `app_name: str`, `app_env: str`, `debug: bool`, `default_timeout: float`, `default_max_retries: int`, `log_level: str`, `database_url: str`, `db_echo: bool`.
- **Functions**:
  - `_parse_timeout(value: Any) -> float`: Validates and parses timeout string/number into positive float.
  - `_parse_retries(value: Any) -> int`: Validates and parses retries into non-negative integer.
  - `_parse_log_level(value: Any) -> str`: Validates and normalizes standard logging levels.
  - `load_config(env_file: str | Path | None = None) -> AppConfig`: Reads `.env` and environment variables to construct an `AppConfig`.
  - `get_config() -> AppConfig`: Cached singleton accessor for application configuration.

#### `src/apiscope/core/contracts.py`
- **Protocols (`@runtime_checkable`)**:
  - `RequestExecutorProtocol(Protocol)`:
    - `execute(self, request: HttpRequest) -> HttpResponse`: Execute HTTP request specification and return response model.
  - `AssertionEngineProtocol(Protocol)`:
    - `evaluate(self, response: HttpResponse, rules: Sequence[dict[str, Any]]) -> Any`: Evaluate declarative assertion rules against an HTTP response.
  - `VariableResolverProtocol(Protocol)`:
    - `resolve(self, template: str, context: Mapping[str, Any]) -> str`: Substitute variable expressions in a template string using context.
  - `ExtractorProtocol(Protocol)`:
    - `extract(self, response: HttpResponse, expression: str) -> Any`: Extract a value from an HTTP response using a query expression (e.g., JSONPath, header name).
    - *Audit Note*: Only the protocol exists. No concrete `Extractor` class has been implemented yet (scheduled for Task 05 / M4).

#### `src/apiscope/core/exceptions.py`
- **Exception Hierarchy**:
  - `ApiScopeError(Exception)`: Base exception with `message: str`.
    - `ConfigurationError(ApiScopeError)`: Invalid application or environment configuration.
    - `InvalidRequestError(ApiScopeError)`: Invalid HTTP request model or disallowed URL/method.
      - `UnsupportedMethodError(InvalidRequestError)`: Unsupported HTTP method requested.
    - `RequestTimeoutError(ApiScopeError)`: HTTP request execution timeout.
    - `NetworkExecutionError(ApiScopeError)`: Network connection or transport failure.
    - `DefinitionError(ApiScopeError)`: Invalid or malformed YAML test definition or collection.
      - `PathTraversalError(DefinitionError)`: File reference attempting directory escape outside allowed root.
    - `VariableResolutionError(ApiScopeError)`: Referenced variable cannot be resolved in current context.

#### `src/apiscope/core/models.py`
- **Enums**:
  - `HttpMethod(StrEnum)`: `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS`. Provides `from_string(cls, value: str) -> HttpMethod`.
  - `BodyType(StrEnum)`: `JSON`, `FORM`, `RAW`. Provides `from_string(cls, value: str) -> BodyType`.
  - `AuthType(StrEnum)`: `BEARER`, `BASIC`, `API_KEY`.
  - `ApiKeyLocation(StrEnum)`: `HEADER`, `QUERY`, `COOKIE`.
- **Classes & Dataclasses**:
  - `AuthConfig`: Base dataclass for authentication credentials.
  - `BearerAuth(AuthConfig)`: Bearer token container with `token: str`.
    - `get_token() -> str`: Retrieve raw token.
    - `__repr__() -> str`: Masked token representation (`***`).
  - `BasicAuth(AuthConfig)`: HTTP Basic auth container with `username: str`, `password: str`.
    - `get_password() -> str`: Retrieve raw password.
    - `__repr__() -> str`: Masked password representation (`***`).
  - `ApiKeyAuth(AuthConfig)`: API key container with `name: str`, `value: str`, `location: ApiKeyLocation`.
    - `get_value() -> str`: Retrieve raw key value.
    - `__repr__() -> str`: Masked key representation (`***`).
  - `HttpRequest`: Domain model for an outbound request.
    - Fields: `method: HttpMethod`, `url: str`, `headers: dict[str, str]`, `params: dict[str, Any]`, `body: Any`, `body_type: BodyType`, `timeout: float = 30.0`, `max_retries: int = 0`, `follow_redirects: bool = True`, `retry_non_idempotent: bool = False`, `auth: AuthConfig | None = None`.
    - `masked_headers() -> dict[str, str]`: Headers with sensitive auth keys replaced with `***`.
    - `__repr__() -> str`: Masked string representation.
  - `HttpResponse`: Domain model for observed HTTP responses.
    - Fields: `status_code: int`, `headers: dict[str, str]`, `body: str = ""`, `elapsed_ms: float = 0.0`, `size_bytes: int = 0`, `url: str = ""`.
    - Properties: `is_success -> bool` (2xx), `is_client_error -> bool` (4xx), `is_server_error -> bool` (5xx).
    - `json() -> Any`: Parse body string as JSON (raises `ValueError` on malformed content).

#### `src/apiscope/core/logging_config.py`
- **Constants**: `_SENSITIVE_PATTERNS`, `_SENSITIVE_KEYS`.
- **Functions**:
  - `redact_sensitive_text(text: str) -> str`: Regular expression sanitizer masking credentials (`Bearer`, passwords, API keys) in strings.
  - `configure_logging(level: str = "INFO", log_file: Path | str | None = None) -> logging.Logger`: Configures structured logging with `RedactingFormatter`.
  - `get_logger(name: str) -> logging.Logger`: Factory retrieving a namespaced `apiscope.*` logger.

#### `src/apiscope/core/variables.py`
- **Classes**:
  - `VariableResolver(VariableResolverProtocol)`:
    - `resolve(self, template: str, context: Mapping[str, Any]) -> str`: Resolves `{{variable}}` string template.
    - `resolve_string(self, text: str, context: Mapping[str, Any]) -> str`: Replaces all `{{var}}` placeholders with string representations.
    - `resolve_value(self, value: Any, context: Mapping[str, Any]) -> Any`: Resolves value while preserving native types (`int`, `float`, `bool`) when input is an exact single variable string.
    - `resolve_data(self, data: Any, context: Mapping[str, Any]) -> Any`: Deep-copies input structure and recursively resolves dictionaries, lists, and tuples without mutating the original input.

#### `src/apiscope/core/executor.py`
- **Constants**: `IDEMPOTENT_METHODS = {GET, HEAD, PUT, DELETE, OPTIONS}`, `TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}`, `MAX_RETRY_LIMIT = 10`.
- **Functions**:
  - `_sanitize_url(url: str) -> str`: Strips sensitive userinfo credentials (`user:pass@host`) from URLs.
- **Classes**:
  - `TargetAccessPolicy`: SSRF security guard.
    - `__init__(self, allow_private_networks: bool = False, allowed_hosts: Sequence[str] | set[str] | frozenset[str] | None = None, blocked_hosts: Sequence[str] | set[str] | frozenset[str] | None = None, allowed_schemes: Sequence[str] | set[str] | frozenset[str] | None = None, resolve_dns: bool = False) -> None`.
    - `validate_url(self, url: str) -> None`: Validates scheme, blocks disallowed hosts, blocks private/loopback IP ranges unless explicitly allowlisted, and optionally checks resolved DNS records.
  - `RequestExecutor(RequestExecutorProtocol)`:
    - `__init__(self, config: AppConfig | None = None, policy: TargetAccessPolicy | None = None, client: httpx.Client | None = None, transport: httpx.BaseTransport | None = None, backoff_factor: float = 0.5, sleep_func: Callable[[float], None] = time.sleep) -> None`.
    - `_prepare_request_kwargs(self, request: HttpRequest) -> tuple[dict[str, str], dict[str, Any], Any, dict[str, Any]]`: Prepares headers, query params, auth injection, and body payload kwargs.
    - `_backoff(self, attempt: int) -> None`: Calculates bounded exponential backoff delay `min(backoff_factor * 2^(attempt-1), 5.0)` and calls `sleep_func`.
    - `execute(self, request: HttpRequest) -> HttpResponse`: Executes request via httpx with retry logic for idempotent methods on transient errors, capturing timing, size, and status.
    - `close(self) -> None`: Closes the internal httpx client.
    - `__enter__()` / `__exit__()`: Context management lifecycle support.

---

### 3.3. Assertion Subsystem (`src/apiscope/core/assertions/`)

#### `src/apiscope/core/assertions/__init__.py`
- **Exports**: `AssertionEngine`, `AssertionResult`, `AssertionStatus`, `AssertionSummary`, `BaseAssertionEvaluator`, `StatusAssertionEvaluator`, `ResponseTimeAssertionEvaluator`, `HeaderAssertionEvaluator`, `JsonPathAssertionEvaluator`, `JsonSchemaAssertionEvaluator`.

#### `src/apiscope/core/assertions/models.py`
- **Classes & Dataclasses**:
  - `AssertionStatus(StrEnum)`: `PASS`, `FAIL`.
  - `AssertionResult`:
    - `__test__ = False`
    - Fields: `assertion_type: str`, `passed: bool`, `expected: Any`, `actual: Any`, `message: str`, `diagnostics: dict[str, Any] | None = None`.
    - Property: `status -> AssertionStatus`.
    - `to_dict() -> dict[str, Any]`.
  - `AssertionSummary`:
    - `__test__ = False`
    - Fields: `total: int`, `passed: int`, `failed: int`, `success: bool`, `verified: bool`, `results: list[AssertionResult]`, `message: str = ""`.
    - `to_dict() -> dict[str, Any]`.
    - `__len__() -> int`, `__iter__() -> Any`, `__getitem__(index_or_key: int | str) -> Any`, `get(key: str, default: Any = None) -> Any`.

#### `src/apiscope/core/assertions/evaluators.py`
- **Classes**:
  - `BaseAssertionEvaluator(ABC)`:
    - `@abstractmethod evaluate(self, rule: dict[str, Any], response: HttpResponse) -> AssertionResult`: Evaluates a single rule dictionary against an HTTP response.
  - `StatusAssertionEvaluator(BaseAssertionEvaluator)`:
    - Handles exact status match (`equals: 200`) and inclusive range (`range: [min, max]`). Rejects inverted ranges and non-integer conditions.
  - `ResponseTimeAssertionEvaluator(BaseAssertionEvaluator)`:
    - Handles `max_time_ms` with `value: float | int`. Rejects negative thresholds.
  - `HeaderAssertionEvaluator(BaseAssertionEvaluator)`:
    - Handles `header_exists`, `header_equals`, and `header` with `name`, `equals`, and `contains` using case-insensitive header name matching.
  - `JsonPathAssertionEvaluator(BaseAssertionEvaluator)`:
    - Parses JSON body, compiles JSONPath via `jsonpath_ng.parse`, finds matches, and evaluates conditions (`equals`, `contains`, `greater_than`, `regex`, `type_check`).
    - Enforces deterministic multi-match logic (all matches must satisfy condition; 0 matches fail).
    - `_check_type(self, val: Any, expected_type: str) -> tuple[bool, str]`: Distinguishes Python `bool` from `int` and `number`.
  - `JsonSchemaAssertionEvaluator(BaseAssertionEvaluator)`:
    - Validates body against JSON Schema via `jsonschema`. Validates schema definition syntax with `check_schema()` and captures first error path without leaking entire bodies.

#### `src/apiscope/core/assertions/engine.py`
- **Classes**:
  - `AssertionEngine(AssertionEngineProtocol)`:
    - `__init__(self, registry: dict[str, BaseAssertionEvaluator] | None = None) -> None`: Registers default evaluators for `status`, `max_time_ms`, `header_exists`, `header_equals`, `header`, `jsonpath`, `json_schema`, `schema`.
    - `register_evaluator(self, assertion_type: str, evaluator: BaseAssertionEvaluator) -> None`: Registers/overrides an evaluator strategy.
    - `get_evaluator(self, assertion_type: str) -> BaseAssertionEvaluator | None`: Retrieves strategy by type string.
    - `evaluate(self, response: HttpResponse, rules: Sequence[dict[str, Any]]) -> AssertionSummary`: Evaluates all assertion rules sequentially in original order, isolates rule failures from crashing the engine, handles unknown types, and returns aggregate `AssertionSummary`. Empty rule lists return `verified=False, success=False`.

---

### 3.4. Definitions & Environment (`src/apiscope/definitions/`)

#### `src/apiscope/definitions/__init__.py`
- **Exports**: `TestDefinition`, `TestRequestDefinition`, `DefinitionSource`, `DefinitionStatus`, `TestCollection`, `load_collection`, `parse_collection`, `validate_safe_path`, `Environment`, `EnvironmentManager`, `load_environment`, `load_test_definition`, `parse_test_definition`.

#### `src/apiscope/definitions/models.py`
- **Enums**:
  - `DefinitionSource(StrEnum)`: `HANDWRITTEN`, `RULE_GENERATED`, `AI_GENERATED`. Provides `from_string(cls, value: str) -> DefinitionSource`.
  - `DefinitionStatus(StrEnum)`: `APPROVED`, `PROPOSED`. Provides `from_string(cls, value: str) -> DefinitionStatus`.
- **Classes & Dataclasses**:
  - `TestRequestDefinition`:
    - `__test__ = False`
    - Fields: `method: HttpMethod | str`, `url: str`, `headers: dict[str, Any]`, `params: dict[str, Any]`, `body: Any | None`, `body_type: BodyType | str`, `timeout: float | None`, `max_retries: int | None`, `follow_redirects: bool | None`, `retry_non_idempotent: bool | None`, `auth: AuthConfig | None = None`.
  - `TestDefinition`:
    - `__test__ = False`
    - Fields: `name: str`, `module: str`, `request: TestRequestDefinition`, `tags: list[str]`, `extract: dict[str, str]`, `assertions: list[dict[str, Any]]`, `source: DefinitionSource`, `status: DefinitionStatus`, `file_path: Path | None = None`.
    - `to_http_request(self, environment: Environment | None = None, resolver: VariableResolver | None = None) -> HttpRequest`: Resolves URL, headers, params, and body using `VariableResolver` against environment variables and outputs an executable `HttpRequest`.
    - *Audit Note on `extract` Rules*: Currently stored as `extract: dict[str, str]`, mapping variable name to extraction expression (e.g., `{"order_id": "$.id"}`).

#### `src/apiscope/definitions/environment.py`
- **Classes**:
  - `Environment`:
    - Fields: `name: str`, `variables: dict[str, Any]`, `secrets: set[str]`.
    - `get(self, key: str, default: Any = None) -> Any`: Retrieves raw variable value for execution.
    - `masked_variables(self) -> dict[str, Any]`: Returns mapping with designated secret keys replaced by `'***'`.
    - `redact_secrets(self, text: str) -> str`: Replaces occurrences of raw secret values in text with `'***'`.
    - `__repr__()` / `__str__()`: Formats environment string with masked variable values.
  - `EnvironmentManager`:
    - `__init__(self) -> None`: Initializes empty registry.
    - `register(self, environment: Environment, make_active: bool = False) -> None`: Registers environment.
    - `get(self, name: str) -> Environment`: Retrieves environment by name.
    - `set_active(self, name: str) -> None`: Sets active environment.
    - `get_active(self) -> Environment | None`: Retrieves currently active environment.
    - `list_all(self) -> list[str]`: Lists registered environment names in sorted order.
- **Functions**:
  - `load_environment(file_path: str | Path) -> Environment`: Safely parses environment YAML file.

#### `src/apiscope/definitions/yaml_loader.py`
- **Functions**:
  - `parse_test_definition(data: Any, file_path: Path | None = None) -> TestDefinition`: Validates dictionary mapping and builds `TestDefinition` instance.
  - `load_test_definition(file_path: str | Path) -> TestDefinition`: Safely reads file and executes `yaml.safe_load(content)`.

#### `src/apiscope/definitions/collection.py`
- **Functions**:
  - `validate_safe_path(base_dir: Path, ref_path: str | Path) -> Path`: Rejects `../`, `..\`, and absolute escapes; raises `PathTraversalError` if target path does not resolve within `base_dir`.
  - `parse_collection(data: Any, file_path: Path | None = None, base_dir: Path | None = None, load_tests: bool = True) -> TestCollection`: Parses collection dictionary mapping.
  - `load_collection(file_path: str | Path, base_dir: Path | None = None) -> TestCollection`: Reads file, parses YAML via `yaml.safe_load`, and validates safe paths for all referenced test files.
- **Classes**:
  - `TestCollection`:
    - `__test__ = False`
    - Fields: `name: str`, `module: str`, `tags: list[str]`, `test_files: list[str]`, `tests: list[TestDefinition]`, `base_dir: Path | None`, `file_path: Path | None`.
    - `__len__() -> int`, `__iter__() -> Iterator[TestDefinition]`, `__getitem__(index: int) -> TestDefinition`.
    - `filter_by_module(self, module: str) -> list[TestDefinition]`: Returns tests matching module (case-insensitive).
    - `filter_by_tags(self, tags: Sequence[str] | str, match_all: bool = False) -> list[TestDefinition]`: Matches tests with tag subset/intersection.
    - `filter_by_tag(self, tag: str) -> list[TestDefinition]`: Convenience single-tag filter.
    - `load_all_tests(self) -> list[TestDefinition]`: Loads and parses all referenced test files.

---

### 3.5. Data Layer (`src/apiscope/data/`)

#### `src/apiscope/data/__init__.py`
- **Exports**: `BaseRepository`, `DatabaseConfig`, `create_connection`, `get_connection`.

#### `src/apiscope/data/connection.py`
- **Classes**:
  - `DatabaseConfig`:
    - `__init__(self, db_path: str | Path = "apiscope.db") -> None`: Holds database file path.
- **Functions**:
  - `create_connection(db_path: str | Path = "apiscope.db") -> sqlite3.Connection`: Connects to SQLite, sets `row_factory = sqlite3.Row`, enables `PRAGMA foreign_keys = ON`, and sets `PRAGMA journal_mode = WAL` for file databases.
  - `get_connection(db_path: str | Path = "apiscope.db") -> Generator[sqlite3.Connection, None, None]`: Context manager yielding connection, committing on success, rolling back on exception, and closing in `finally`.
  - *Audit Note*: The project requirements mentioned `DatabaseManager`. The actual implementation uses the functions `create_connection` and `get_connection` with `DatabaseConfig`.

#### `src/apiscope/data/repository.py`
- **Classes**:
  - `BaseRepository(ABC, Generic[T, ID])`: Abstract base repository interface with `get_by_id`, `list_all`, `save`, and `delete`.

---

### 3.6. Placeholder Packages
- `src/apiscope/analysis/__init__.py` (Empty package marker)
- `src/apiscope/generators/__init__.py` (Empty package marker)
- `src/apiscope/reports/__init__.py` (Empty package marker)
- `src/apiscope/web/__init__.py` (Empty package marker)

---

## 4. Per-File Test Suite Inventory (`tests/`)

Total active tests: **173 tests across 5 test files**.

| Test File Path | Test Count | Scenarios Tested |
| :--- | :---: | :--- |
| `tests/unit/test_smoke.py` | 5 | Package version import, CLI version subcommand, CLI `--help` display, SQLite in-memory PRAGMA foreign keys and row factory, `get_connection` transactional commit/rollback context manager. |
| `tests/unit/test_core.py` | 33 | `HttpRequest` model validation (all 7 HTTP methods, unsupported method rejection, timeout/retry boundaries, invalid headers/params), `HttpResponse` status categorization (2xx, 4xx, 5xx, auto size calculation, JSON decode failure), `BearerAuth`, `BasicAuth`, and `ApiKeyAuth` (header/query/cookie) masking, sensitive header redaction, log sanitization regexes, `ApiScopeError` polymorphic inheritance, `AppConfig` default/env/validation, logger namespacing, and runtime Protocol checks (`RequestExecutorProtocol`, `AssertionEngineProtocol`, `VariableResolverProtocol`, `ExtractorProtocol`). |
| `tests/unit/test_executor.py` | 38 | `RequestExecutor` execution across all 7 HTTP methods (GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS) with mock transports, headers, query parameters, body payloads (JSON, form, raw text), auth credential injection (Bearer, Basic, ApiKey), response metadata capture (elapsed_ms, size_bytes, status, final_url), failure handling (timeouts, connection errors, invalid URLs, unsupported protocols, redirect loops), bounded exponential backoff retries on transient 5xx/429 for idempotent methods, non-retry of non-idempotent methods, and `TargetAccessPolicy` SSRF protections (blocking loopback, private subnets, allowed hosts allowlist, and blocked hosts blacklist). |
| `tests/unit/test_definitions.py` | 44 | Test definition YAML loading (full, minimal defaults, malformed, empty, missing required name/module/request/url/method, invalid methods/body types/source/status, verbatim assertion preservation), collection suite loading, ordering, module filtering, tag filtering, path traversal protection (`../`, `..\`, absolute escape), environment loading, environment variable masking and secret redaction, `EnvironmentManager` switching, `VariableResolver` recursive resolution across URLs, headers, params, nested dicts, lists, multi-variables, native type preservation (`int`, `float`, `bool`), missing variable errors, definition immutability, request conversion to `HttpRequest`, and mock execution with `RequestExecutor`. |
| `tests/unit/test_assertions.py` | 53 | HTTP status assertions (exact match, inclusive range, boundaries, inverted range rejection, missing condition), response time assertions (below, at, above limit, negative limits), header assertions (existence, case-insensitivity, equality), JSONPath assertions (equality, nested paths, string/list/dict contains, numeric greater-than strictly excluding booleans, regex matching, type checks distinguishing bool from int/number, missing paths, invalid syntax, multiple matches all passing, one failing multiple match, malformed JSON response body), JSON Schema assertions (conforming body, missing required field, wrong type, constraint violation, malformed schema), engine behavior (multi-assertion dispatch, error isolation across failed assertions, ordering preservation, aggregate counts, mixed pass/fail, empty assertion unverified state, unknown types, malformed rules, empty response body, model immutability), and custom strategy evaluator registration. |

---

## 5. Architectural Contracts Summary

| Contract / Interface | Type | Methods Defined | Status |
| :--- | :--- | :--- | :--- |
| `RequestExecutorProtocol` | Protocol | `execute(request: HttpRequest) -> HttpResponse` | Implemented by `RequestExecutor` |
| `AssertionEngineProtocol` | Protocol | `evaluate(response: HttpResponse, rules: Sequence[dict[str, Any]]) -> Any` | Implemented by `AssertionEngine` |
| `VariableResolverProtocol`| Protocol | `resolve(template: str, context: Mapping[str, Any]) -> str` | Implemented by `VariableResolver` |
| `ExtractorProtocol` | Protocol | `extract(response: HttpResponse, expression: str) -> Any` | **No concrete implementation yet** (Target for Task 05 / M4) |
| `BaseAssertionEvaluator` | ABC | `evaluate(rule: dict[str, Any], response: HttpResponse) -> AssertionResult` | Implemented by 5 strategy evaluators |
| `BaseRepository` | ABC | `get_by_id`, `list_all`, `save`, `delete` | Generic repository base in `src/apiscope/data/` |
