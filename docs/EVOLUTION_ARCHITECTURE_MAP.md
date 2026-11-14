# APISCOPE EVOLUTION — ARCHITECTURE MAP & SYSTEM BLUEPRINT

**Document Version:** 1.0.0 (Evolution Baseline)  
**Target:** ApiScope Quality Engineering Platform  
**Architecture Style:** Strict Layered Architecture with Strategy & Repository Patterns  

---

## 1. System Architecture Overview

ApiScope follows a strict 3-tier layered architecture designed for deterministic execution, extensibility, and maintainability:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        1. PRESENTATION LAYER                           │
│   • Typer CLI (apiscope/cli/main.py)                                   │
│   • FastAPI & Jinja2 Web Dashboard (apiscope/web/app.py)               │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│              2. APPLICATION SERVICES & CORE ENGINE                     │
│   • Core Execution: RequestExecutor, TargetAccessPolicy, RunService   │
│   • Declarative Evaluation: AssertionEngine, VariableResolver,         │
│     Extractor, CollectionLoader                                        │
│   • Generation Engine: OpenAPI Importer, RuleBasedGenerator,          │
│     AiGeneratorService, ProjectProfile Parser                          │
│   • Diagnostic Services: HealthService, ZombieService,                 │
│     SilentFailureService                                               │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│                    3. DATA & REPORTING LAYER                           │
│   • SQLite Repositories (SqliteRunRepository, CaseResultRepository)    │
│   • Multi-Format Exporters (HTML, JUnit XML, JSON Summary)             │
│   • Metric & Signal Aggregations                                       │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Current vs. Target Evolution Architecture

### Current State (Gate 0 Baseline)
- **Input:** Static OpenAPI YAML/JSON + manually created YAML Project Profile.
- **Generation:** Rule-based generation with placeholder values (`1`, `"test_value"`) + optional LLM prompting blind to backend code.
- **Execution:** Serial HTTP execution with all-or-nothing `--allow-local` SSRF toggle; no database fixture setup or rollback.
- **Diagnostics:** Post-hoc log file parsing (requires pre-existing `access.log` and `app.log` files on disk).

### Target State (Evolution Tasks 16–26)
- **Project Intelligence (Task 16):** Codebase scanners extract real database schemas, Spring/FastAPI routes, entities, and business validation rules directly from application source code.
- **Contract Normalization (Task 17):** Reconciles OpenAPI documentation with actual backend routes, automatically correcting generic tags (e.g. `stockpile-controller`) into distinct domain modules.
- **Intelligent Planning & TDM (Tasks 18 & 20):** Dependency-aware test ordering with isolated test data generation, database state snapshots, and automatic rollback/reset.
- **Hybrid Generation & Safety (Tasks 19 & 21):** Synthesizes deep business invariant assertions with execution safety fencing (dry-run, mutation authorization).
- **Evidence & Unified Pipeline (Tasks 22–26):** Automated root-cause classification distinguishing application bugs from test setup flaws, backed by single-command execution.

```mermaid
flowchart TB
    subgraph INTAKE["1. Intelligent Intake & Normalization (Tasks 16 & 17)"]
        OAS["OpenAPI Specification"]
        SRC["Application Source Code (Java/Python)"]
        OAS --> NORM["Contract Normalizer (Task 17)"]
        SRC --> PINT["Project Intelligence Engine (Task 16)"]
        PINT --> NORM
    end

    subgraph PLANNING["2. Planning & Data Management (Tasks 18 & 20)"]
        NORM --> PLAN["Intelligent Test Planner (Task 18)"]
        DB["Target Database / State"] --> TDM["Test Data Manager (Task 20)"]
        TDM --> PLAN
    end

    subgraph GENERATION["3. Hybrid Generation (Task 19)"]
        PLAN --> HGEN["Hybrid Test Generator (Task 19)"]
        HGEN --> SUITE["Validated Multi-Tier Suite (YAML)"]
    end

    subgraph EXECUTION["4. Safe Autonomous Execution (Task 21)"]
        SUITE --> SAFE["Safe Execution Engine (Task 21)"]
        SAFE --> POL["Host-Scoped SSRF Policy"]
        POL --> TGT["Target Application (e.g. StockPile)"]
    end

    subgraph INVESTIGATION["5. Diagnostics & Evidence (Tasks 22 - 25)"]
        TGT --> RUNRES["Run Result"]
        RUNRES --> FINV["Failure Investigator (Task 22)"]
        RUNRES --> ASIL["Adv. Silent Failure (Task 23)"]
        RUNRES --> AZOM["Adv. Zombie Service (Task 24)"]
        FINV --> EVID["Trust & Evidence Engine (Task 25)"]
        ASIL --> EVID
        AZOM --> EVID
    end

    subgraph UNIFIED["6. Unified Platform (Task 26)"]
        EVID --> UNICLI["Unified CLI / Web Experience (Task 26)"]
    end
```

---

## 3. Component Interface Contracts & Data Flow

```mermaid
sequenceDiagram
    autonumber
    participant CLI as Typer CLI / Web Dashboard
    participant RunSvc as RunService
    participant Exec as RequestExecutor
    participant SSRF as TargetAccessPolicy
    participant Target as Target HTTP Server
    participant Ext as Extractor
    participant Assert as AssertionEngine
    participant Repo as SqliteRunRepository

    CLI->>RunSvc: run_collection(collection, env)
    loop For each TestDefinition
        RunSvc->>RunSvc: VariableResolver.resolve(step)
        RunSvc->>Exec: execute(HttpRequest)
        Exec->>SSRF: validate_url(url)
        alt URL Disallowed
            SSRF-->>Exec: InvalidRequestError (SSRF block)
            Exec-->>RunSvc: CaseResult(status=ERROR)
        else URL Allowed
            Exec->>Target: HTTP Request
            Target-->>Exec: HTTP Response
            Exec-->>RunSvc: HttpResponse
        end
        opt Has Extract Rules
            RunSvc->>Ext: extract(response, rules)
            Ext-->>RunSvc: session_variables.update()
        end
        RunSvc->>Assert: evaluate(response, test.assertions)
        Assert-->>RunSvc: AssertionSummary (status, passed, failed)
    end
    RunSvc-->>CLI: RunResult
    CLI->>Repo: save_run(RunResult)
```

---

## 4. Canonical Domain Data Models

```mermaid
classDiagram
    class HttpRequest {
        +HttpMethod method
        +str url
        +dict headers
        +dict params
        +Any body
        +BodyType body_type
        +Auth auth
        +float timeout
        +int max_retries
        +bool follow_redirects
    }

    class HttpResponse {
        +int status_code
        +dict headers
        +str body
        +bytes raw_body
        +float elapsed_ms
        +json() Any
    }

    class TestDefinition {
        +str id
        +str name
        +str module
        +list tags
        +DefinitionSource source
        +DefinitionStatus status
        +TestRequestDefinition request
        +dict extract
        +list assertions
    }

    class AssertionResult {
        +str assertion_type
        +bool passed
        +Any expected
        +Any actual
        +str message
        +dict diagnostics
    }

    class RunResult {
        +str run_id
        +datetime started_at
        +datetime finished_at
        +int total_tests
        +int passed_tests
        +int failed_tests
        +list results
    }

    class ModuleHealthReport {
        +str module
        +HealthVerdict verdict
        +float pass_rate_pct
        +float endpoint_coverage_pct
        +int zombie_count
        +int silent_failure_count
        +str summary
        +list evidence
    }

    TestDefinition --> HttpRequest : creates
    HttpRequest --> HttpResponse : produces
    HttpResponse --> AssertionResult : evaluated against
    TestDefinition --> RunResult : aggregated into
    RunResult --> ModuleHealthReport : evaluated by HealthService
```

---

## 5. Architectural Boundaries for Evolution Tasks (Tasks 16–26)

| Evolution Task | Target Subsystem / Layer | New / Extended Modules | Inbound Interfaces | Outbound Interfaces |
| :--- | :--- | :--- | :--- | :--- |
| **Task 16: Project Intelligence** | Core Analysis / Extraction | `apiscope.intelligence.scanner`, `apiscope.intelligence.models` | Application source root path (Java/Python) | Discovered metadata model (`DiscoveredProjectModel`) |
| **Task 17: Contract Normalization** | Generator / Normalization | `apiscope.generators.normalizer` | Raw `OpenAPISpec` + `DiscoveredProjectModel` | Normalized `OpenAPISpec` with accurate module tags & schemas |
| **Task 18: Intelligent Test Planning** | Core Application | `apiscope.planning.planner`, `apiscope.planning.dag` | Normalized `OpenAPISpec` + dependencies | Dependency graph & ordered execution plan (`ExecutionPlan`) |
| **Task 19: Hybrid Test Generation** | Generator Engine | `apiscope.generators.hybrid_generator` | `ExecutionPlan` + LLM Provider | Multi-tier test suite with deep semantic assertions |
| **Task 20: Test Data Management** | Core Data / Fixtures | `apiscope.data.fixtures`, `apiscope.data.snapshot` | Database connection or reset HTTP webhook | Seeded test records & rollback handlers |
| **Task 21: Safe Autonomous Execution** | Core Execution | `apiscope.core.safety_fencing`, `TargetAccessPolicy` | `ExecutionPlan` + Target environment config | Non-destructive execution guarantee |
| **Task 22: Failure Investigation** | Analysis & Diagnostics | `apiscope.analysis.failure_investigator` | Failed `CaseResult` + response payloads | Root cause diagnosis (Bug vs Setup Error) |
| **Task 23: Adv. Silent Failure** | Diagnostic Engine | `apiscope.analysis.silent_failure_service` | Run results + live container log streams | Extended S1–S4 anomaly signals |
| **Task 24: Adv. Zombie Analysis** | Diagnostic Engine | `apiscope.analysis.zombie_service` | Normalized spec + access logs | Risk-ranked zombie catalog & deprecation countdown |
| **Task 25: Trust & Evidence Engine** | Reporting & Governance | `apiscope.analysis.evidence_engine` | Test results, anomaly signals, coverage | Evidence confidence score (0–100%) |
| **Task 26: Unified Workflow** | Presentation & Automation | `apiscope.cli.main`, `apiscope.workflow` | Single CLI command (`apiscope audit`) | Complete audit report, HTML dashboard, JUnit artifacts |

---

*End of Architecture Map.*
