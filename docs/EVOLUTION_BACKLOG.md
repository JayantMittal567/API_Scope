# APISCOPE EVOLUTION — BACKLOG & TASK READINESS SPECIFICATION

**Document Version:** 1.0.0 (Evolution Gate 0)  
**Author:** Independent Senior Software Architect & AI Systems Specialist  
**Audience:** ApiScope Core Development Team  

---

## 1. Prioritized Pre-Task 16 Blockers

Before beginning development on Task 16 (Project Intelligence), the following **4 critical blockers** must be resolved in the repository:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        PRIORITIZED BLOCKERS                            │
│                                                                        │
│  [P0] BLOCKER-01: Fix Open Redirect SSRF Vulnerability                 │
│       File: src/apiscope/core/executor.py                              │
│       Risk: High (CWE-918 / CWE-601)                                   │
│                                                                        │
│  [P0] BLOCKER-02: Implement Host-Scoped Target Authorization          │
│       Files: src/apiscope/core/executor.py, src/apiscope/cli/main.py   │
│       Risk: Medium (Over-permissive --allow-local)                     │
│                                                                        │
│  [P1] BLOCKER-03: Add Mutation Safety & Dry-Run Execution Fencing      │
│       Files: src/apiscope/core/executor.py, rule_generator.py          │
│       Risk: High (Accidental data loss in live targets)                │
│                                                                        │
│  [P1] BLOCKER-04: Multi-Strategy OpenAPI Module Disambiguation         │
│       File: src/apiscope/generators/openapi_importer.py                │
│       Risk: Medium (Generic module collapse: stockpile-controller)     │
└────────────────────────────────────────────────────────────────────────┘
```

### Actionable Fix Specifications

#### BLOCKER-01: Open Redirect SSRF Vulnerability
- **Root Cause:** In [`src/apiscope/core/executor.py:370`](file:///E:/3rd%20sem%20acads/SE/APIScope/src/apiscope/core/executor.py), `httpx.Client.request(..., follow_redirects=request.follow_redirects)` automatically follows 301/302 redirects without checking the `Location` header against `TargetAccessPolicy.validate_url()`.
- **Correction:** Implement a custom redirect event hook or set `follow_redirects=False` in `httpx` and handle 3xx responses in a loop within `RequestExecutor.execute()`, calling `self.policy.validate_url(redirect_target)` before dispatching each redirect step.

#### BLOCKER-02: Host-Scoped Target Authorization
- **Root Cause:** In [`src/apiscope/cli/main.py:306-316`](file:///E:/3rd%20sem%20acads/SE/APIScope/src/apiscope/cli/main.py), passing `--allow-local` enables `allow_private_networks=True`, completely disabling private network protections across all subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, AWS metadata `169.254.169.254`).
- **Correction:** Add a `--allowed-host <host>` option (e.g. `--allowed-host localhost` or `--allowed-host 127.0.0.1:8080`) to `apiscope run`, mapping directly to `TargetAccessPolicy(allowed_hosts={host})` while leaving global private network blocking intact.

#### BLOCKER-03: Mutation Safety & Dry-Run Fencing
- **Root Cause:** Auto-generated test suites indiscriminately emit `DELETE`, `PUT`, and `POST` commands without an execution safety mode.
- **Correction:** Introduce `ExecutionSafetyMode` (`READ_ONLY`, `MUTATION_PERMITTED`) in `RequestExecutor` and `RunService`. In read-only mode, any non-idempotent or mutating request is aborted before dispatch.

#### BLOCKER-04: Multi-Strategy Module Disambiguation
- **Root Cause:** In [`src/apiscope/generators/openapi_importer.py:46`](file:///E:/3rd%20sem%20acads/SE/APIScope/src/apiscope/generators/openapi_importer.py), `_extract_module` naively accepts `tags[0]`. When Spring Boot bundles all endpoints under `stockpile-controller`, module division is lost.
- **Correction:** Implement a composite classification strategy: if `tags[0]` contains generic suffixes (e.g. `-controller`, `default`, `api`), parse the path prefix (e.g. `/api/products` → `products`, `/api/sales` → `sales`).

---

## 2. Task-by-Task Readiness Assessment (Tasks 16 to 26)

### Task 16: Project Intelligence
- **Objective:** Discover application framework, exposed routes, database models, entities, and business constraints directly by scanning backend source repositories (e.g. Spring Boot Java code, FastAPI Python code).
- **Existing Reusable Components:**
  - `ProjectProfile` models ([`src/apiscope/generators/project_profile.py`](file:///E:/3rd%20sem%20acads/SE/APIScope/src/apiscope/generators/project_profile.py)) for holding structured domain rules.
  - Path normalizer ([`src/apiscope/analysis/path_normalizer.py`](file:///E:/3rd%20sem%20acads/SE/APIScope/src/apiscope/analysis/path_normalizer.py)).
- **Missing Capabilities:**
  - Java / Spring Boot AST / regex parsing for `@RestController`, `@GetMapping`, `@PostMapping`, `@PathVariable`, and `record` data classes.
  - SQLite/PostgreSQL schema introspection.
- **Dependencies:** Python `ast` for Python projects; tree-sitter or regex/JavaParser for Java projects.
- **Migration Risks:** Low; operates purely as an intake scanner generating structured metadata.
- **Recommended Boundary:** Keep in new subpackage `apiscope.intelligence`, outputting an immutable `DiscoveredProjectModel`.

---

### Task 17: Contract Normalization
- **Objective:** Reconcile OpenAPI specifications with reality: fix misconfigured tags, fill missing schema types, and flag undocumented routes.
- **Existing Reusable Components:**
  - `OpenAPISpec` and `load_openapi_spec` ([`src/apiscope/generators/openapi_importer.py`](file:///E:/3rd%20sem%20acads/SE/APIScope/src/apiscope/generators/openapi_importer.py)).
- **Missing Capabilities:**
  - Bidirectional diffing between OpenAPI endpoints and discovered source routes.
  - Schema patching and parameter augmentation.
- **Dependencies:** Task 16 (`DiscoveredProjectModel`).
- **Migration Risks:** Low; preserves existing `OpenAPISpec` interface while returning a refined instance.
- **Recommended Boundary:** `apiscope.generators.normalizer.normalize_contract(spec, project_model) -> OpenAPISpec`.

---

### Task 18: Intelligent Test Planning
- **Objective:** Build an execution DAG (Directed Acyclic Graph) of endpoints to ensure producer endpoints (e.g. Create Product) execute before consumer endpoints (e.g. Get Product by ID, Delete Product).
- **Existing Reusable Components:**
  - CRUD chaining logic in `RuleBasedGenerator.generate_crud_chains`.
  - Extractor variable injection in `RunService`.
- **Missing Capabilities:**
  - Topological sorting algorithm for multi-step dependencies.
  - Classification of endpoints into Safe Read-Only, State-Mutating, and Teardown Deletions.
- **Dependencies:** Task 17 (Normalized Spec).
- **Migration Risks:** Medium; must maintain backward compatibility with flat YAML test collections.
- **Recommended Boundary:** New module `apiscope.planning.dag`, producing an `ExecutionPlan`.

---

### Task 19: Hybrid Test Generation
- **Objective:** Combine deterministic rule-based boundary values with LLM-synthesized semantic assertions (e.g., verifying `subtotal - discount == total`, verifying currency codes, checking state machine transitions).
- **Existing Reusable Components:**
  - `RuleBasedGenerator` ([`src/apiscope/generators/rule_generator.py`](file:///E:/3rd%20sem%20acads/SE/APIScope/src/apiscope/generators/rule_generator.py)).
  - `AiGeneratorService` ([`src/apiscope/generators/ai_generator.py`](file:///E:/3rd%20sem%20acads/SE/APIScope/src/apiscope/generators/ai_generator.py)).
  - `AssertionEngine` ([`src/apiscope/core/assertions/engine.py`](file:///E:/3rd%20sem%20acads/SE/APIScope/src/apiscope/core/assertions/engine.py)).
- **Missing Capabilities:**
  - Prompt enrichment with real source-derived entity structures and invariants from Task 16.
  - Multi-tier assertion templates (Status + Schema + JSONPath + Invariant).
- **Dependencies:** Tasks 16, 17, 18.
- **Migration Risks:** Low; generated output remains standard `TestDefinition` instances.
- **Recommended Boundary:** `apiscope.generators.hybrid_generator.HybridTestGenerator`.

---

### Task 20: Test Data Management (TDM)
- **Objective:** Manage database fixtures, pre-test seeding, state snapshots, and automatic database resets.
- **Existing Reusable Components:**
  - `data_loader.py` for CSV/JSON parameter injection.
  - `Environment` variables.
- **Missing Capabilities:**
  - Pre-run database state backup and post-run restoration (e.g. SQLite file snapshot copy, HTTP `/reset` webhook calls).
  - Fixture seeding lifecycle hooks (`before_all`, `after_all`, `before_each`, `after_each`).
- **Dependencies:** Target database access or admin endpoint.
- **Migration Risks:** Medium; must handle target applications that do not expose a reset webhook gracefully.
- **Recommended Boundary:** `apiscope.data.fixtures.FixtureManager`.

---

### Task 21: Safe Autonomous Execution
- **Objective:** Execute tests with strict safety fences: host-scoped allowlisting, dry-run simulation mode, and automatic rollback of mutations.
- **Existing Reusable Components:**
  - `TargetAccessPolicy` and `RequestExecutor` ([`src/apiscope/core/executor.py`](file:///E:/3rd%20sem%20acads/SE/APIScope/src/apiscope/core/executor.py)).
  - `RunService` ([`src/apiscope/core/run_service.py`](file:///E:/3rd%20sem%20acads/SE/APIScope/src/apiscope/core/run_service.py)).
- **Missing Capabilities:**
  - Host-scoped authorization in CLI.
  - Interception of mutating requests when run against protected environments.
- **Dependencies:** Resolution of Blockers 01, 02, and 03.
- **Migration Risks:** Low.
- **Recommended Boundary:** Enhance `TargetAccessPolicy` and add `ExecutionSafetyFence`.

---

### Task 22: Failure Investigation
- **Objective:** Automatically diagnose failed assertions: distinguish whether a failure is a real application bug, an invalid test prerequisite, or a network timeout, and generate a curl reproduction command.
- **Existing Reusable Components:**
  - `AssertionResult` diagnostics ([`src/apiscope/core/assertions/models.py`](file:///E:/3rd%20sem%20acads/SE/APIScope/src/apiscope/core/assertions/models.py)).
  - `CaseResult` model.
- **Missing Capabilities:**
  - Root cause classification taxonomy (`APPLICATION_DEFECT`, `MISSING_PREREQUISITE`, `CONTRACT_DEVIATION`, `INFRASTRUCTURE_FAILURE`).
  - Automated `curl` command generator for failed HTTP requests.
- **Dependencies:** `CaseResult`, `AssertionResult`.
- **Migration Risks:** None (pure analytical service).
- **Recommended Boundary:** `apiscope.analysis.failure_investigator.FailureInvestigator`.

---

### Task 23: Advanced Silent Failure Detection
- **Objective:** Extend silent failure detection beyond static keywords to catch masked stack traces, rollbacked database transactions, and empty collection anomalies.
- **Existing Reusable Components:**
  - `SilentFailureService` ([`src/apiscope/analysis/silent_failure_service.py`](file:///E:/3rd%20sem%20acads/SE/APIScope/src/apiscope/analysis/silent_failure_service.py)) with existing S1–S4 detectors.
- **Missing Capabilities:**
  - Streaming log ingestion (e.g. tailing live Docker or local log files).
  - Database rollback log pattern matching (`Rolled back transaction`, `ConstraintViolationException`).
- **Dependencies:** Task 16 (for identifying logging frameworks).
- **Migration Risks:** Low; preserves existing `SilentFailureSignal` data models.
- **Recommended Boundary:** Extend `SilentFailureService` with plugin pattern detectors.

---

### Task 24: Advanced Zombie Analysis
- **Objective:** Risk-rank zombie endpoints by traffic exposure, identify shadow query parameters, and calculate deprecation retirement countdowns.
- **Existing Reusable Components:**
  - `ZombieService` ([`src/apiscope/analysis/zombie_service.py`](file:///E:/3rd%20sem%20acads/SE/APIScope/src/apiscope/analysis/zombie_service.py)).
- **Missing Capabilities:**
  - Traffic frequency weighting (e.g. 100,000 hits/day on an undocumented endpoint is CRITICAL risk).
  - Query parameter usage vs declaration analysis.
- **Dependencies:** `ZombieService`, access logs.
- **Migration Risks:** None.
- **Recommended Boundary:** Add `ZombieRiskScorer` within `apiscope.analysis.zombie_service`.

---

### Task 25: Trust & Evidence Engine
- **Objective:** Provide a unified confidence score (0–100%) for health verdicts, verifying that tests were backed by deep assertions rather than superficial 200 OK checks.
- **Existing Reusable Components:**
  - `HealthService` ([`src/apiscope/analysis/health_service.py`](file:///E:/3rd%20sem%20acads/SE/APIScope/src/apiscope/analysis/health_service.py)).
  - `AssertionSummary` ([`src/apiscope/core/assertions/models.py`](file:///E:/3rd%20sem%20acads/SE/APIScope/src/apiscope/core/assertions/models.py)).
- **Missing Capabilities:**
  - Assertion Quality Index (penalizing suites that only assert HTTP status).
  - Negative test verification ratio.
- **Dependencies:** `HealthService`, `RunResult`.
- **Migration Risks:** Low; enhances `ModuleHealthReport` evidence list.
- **Recommended Boundary:** `apiscope.analysis.evidence_engine.EvidenceEngine`.

---

### Task 26: Unified Workflow
- **Objective:** Provide a seamless, single-command experience (`apiscope audit --target <url> --source <dir>`) that runs discovery, planning, safe execution, diagnostics, and reporting without manual scripts.
- **Existing Reusable Components:**
  - Typer CLI (`apiscope/cli/main.py`).
  - ReportService (`apiscope/reports/report_service.py`).
  - FastAPI Web Dashboard (`apiscope/web/app.py`).
- **Missing Capabilities:**
  - Workflow orchestrator class coordinating Tasks 16–25 into a single execution transaction.
- **Dependencies:** All previous tasks.
- **Migration Risks:** Low; wraps existing CLI commands into a unified high-level verb.
- **Recommended Boundary:** `apiscope.workflow.engine.WorkflowOrchestrator`.

---

## 3. Separation of Epistemological Categories

To maintain engineering rigor, all findings in this audit are explicitly classified into three categories:

```
┌────────────────────────────────────────────────────────────────────────┐
│                      EPISTEMOLOGICAL AUDIT AUDIT                       │
├────────────────────────────────────────────────────────────────────────┤
│ 1. VERIFIED FACTS (Empirically Proven by Code & Tests)                 │
│    • 324 pytest unit/integration tests pass in 17.93s.                 │
│    • Total statement test coverage is 89% (4995 stmts, 431 misses).    │
│    • Zero Ruff static analysis errors across entire repository.        │
│    • HTTP redirect following in executor.py does not re-validate SSRF. │
│    • CLI --allow-local enables broad allow_private_networks=True.      │
│    • StockPile live read-only suite executed 30/30 tests (120/120 pass)│
│                                                                        │
│ 2. INFERRED CONCERNS (High-Probability Engineering Risks)              │
│    • Destructive DELETE tests in generated_suite.yaml will corrupt     │
│      unprepared environments if executed without reset webhooks.       │
│    • Developers testing local Spring Boot applications will continue   │
│      bypassing the CLI with manual scripts unless host-scoped allow-   │
│      listing is introduced.                                            │
│    • External LLM generation without source-code context will continue │
│      hallucinating placeholder parameters and generic modules.         │
│                                                                        │
│ 3. UNVERIFIED ASSUMPTIONS (Requires Further Testing in Future Tasks)   │
│    • Assumption that future target applications expose standardized    │
│      database reset webhooks or allow raw SQLite file swapping.        │
│    • Assumption that Java backend repositories will always be locally  │
│      accessible on the same filesystem as ApiScope.                    │
└────────────────────────────────────────────────────────────────────────┘
```

---

*End of Backlog & Task Readiness Specification.*
