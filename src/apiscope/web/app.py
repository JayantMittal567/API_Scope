"""FastAPI application factory and route definitions for ApiScope Web Dashboard.

Covers: FR-46, FR-47, FR-48, NFR-02, NFR-03, NFR-04, NFR-06.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, Query, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from apiscope.analysis.health_models import ModuleHealthReport
from apiscope.analysis.health_service import HealthService
from apiscope.analysis.silent_failure_service import (
    SilentFailureReport,
    SilentFailureService,
    load_app_log,
)
from apiscope.analysis.zombie_service import (
    ZombieReport,
    ZombieService,
    load_access_log,
)
from apiscope.core.exceptions import (
    ApiScopeError,
    ConfigurationError,
    DefinitionError,
    PathTraversalError,
)
from apiscope.core.logging_config import redact_sensitive_text
from apiscope.core.results import RunResult
from apiscope.core.run_service import RunService
from apiscope.data.connection import DatabaseManager
from apiscope.data.repositories import RunRepository
from apiscope.definitions.collection import (
    TestCollection,
    load_collection,
    validate_safe_path,
)
from apiscope.definitions.environment import load_environment
from apiscope.definitions.yaml_loader import load_test_definition
from apiscope.generators.ai_generator import AiGeneratorService, LLMConfig
from apiscope.generators.openapi_importer import OpenAPISpec, load_openapi_spec
from apiscope.reports.report_service import ReportFormat, ReportService
from apiscope.web.templates import create_jinja_environment


@dataclass(frozen=True)
class DashboardConfig:
    """Configuration parameters for the ApiScope Web Dashboard."""

    db_path: Path = Path(".apiscope/history.db")
    base_dir: Path = Path(".")
    spec_path: Path | None = None
    access_log_path: Path | None = None
    app_log_path: Path | None = None
    health_probe_path: Path | None = None
    baseline_p95_ms: float | None = None

    __test__ = False


class TriggerRunRequest(BaseModel):
    """Payload schema for triggering an on-demand test execution run."""

    suite_path: str
    env_file: str | None = None
    module: str | None = None
    tags: list[str] | None = None
    data_file: str | None = None

    __test__ = False


class ApproveSuiteRequest(BaseModel):
    """Payload schema for approving proposed test definitions in a suite."""

    suite_path: str
    output_path: str | None = None

    __test__ = False


class AiGenerateSuiteRequest(BaseModel):
    """Payload schema for triggering AI test suite generation."""

    spec_path: str
    profile_path: str
    output_path: str
    provider: str = "gemini"
    model: str | None = None
    api_key: str | None = None
    module: str | None = None

    __test__ = False


def _load_suite_safely(suite_path: str, base_dir: Path) -> TestCollection:
    """Safely validate, read, and load a TestCollection or single TestDefinition."""
    safe_path = validate_safe_path(base_dir=base_dir, ref_path=suite_path)
    if not safe_path.is_file():
        raise DefinitionError(f"Test suite file not found: '{suite_path}'")

    try:
        content = safe_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
    except Exception as err:
        raise DefinitionError(
            f"Error reading test suite '{suite_path}': {err}"
        ) from err

    if not isinstance(data, dict):
        raise DefinitionError(
            f"Test suite '{suite_path}' is not a valid dictionary mapping."
        )

    if "tests" in data:
        return load_collection(safe_path, base_dir=base_dir)
    elif "request" in data:
        test_def = load_test_definition(safe_path)
        return TestCollection(
            name=test_def.name,
            module=test_def.module,
            tags=list(test_def.tags),
            tests=[test_def],
            base_dir=safe_path.parent,
            file_path=safe_path,
        )
    else:
        raise DefinitionError(
            f"Test suite '{suite_path}' must contain either 'tests' or 'request'."
        )


def create_web_app(
    config: DashboardConfig | None = None,
    run_service: RunService | None = None,
) -> FastAPI:
    """Create and configure the FastAPI web dashboard application.

    Args:
        config: Optional dashboard configuration settings.
        run_service: Optional RunService instance for suite execution.

    Returns:
        Configured FastAPI application instance.
    """
    cfg = config or DashboardConfig()
    runner = run_service or RunService()
    jinja_env = create_jinja_environment()

    app = FastAPI(
        title="ApiScope Dashboard",
        description="Automated API Testing, Health Monitoring & Analysis Dashboard",
        version="0.1.0",
    )

    def _get_db_and_repo() -> tuple[DatabaseManager, RunRepository]:
        db_mgr = DatabaseManager(cfg.db_path)
        repo = RunRepository(db_mgr)
        return db_mgr, repo

    # -------------------------------------------------------------------------
    # HTML Views
    # -------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    @app.get("/runs", response_class=HTMLResponse)
    def runs_page(module: str | None = None) -> HTMLResponse:
        """Render the test execution runs history and trends overview page."""
        db_mgr, repo = _get_db_and_repo()
        try:
            runs = repo.list_runs(limit=50, module=module)
            trends = repo.get_trends(limit=20, module=module)
        finally:
            db_mgr.close()

        template = jinja_env.get_template("runs.html")
        rendered = template.render(
            runs=runs,
            total_runs=len(runs),
            latest_run=runs[0] if runs else None,
            trends=trends,
            trends_json=json.dumps(trends.to_dict()) if trends else "{}",
            active_tab="runs",
        )
        return HTMLResponse(content=rendered)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail_page(run_id: str) -> HTMLResponse:
        """Render detailed execution breakdown and diagnostics for a specific run."""
        db_mgr, repo = _get_db_and_repo()
        try:
            run_result = repo.get_run(run_id)
        finally:
            db_mgr.close()

        if run_result is None:
            err_tpl = jinja_env.get_template("error.html")
            err_content = err_tpl.render(
                status_code=404,
                error_title="Run Not Found",
                error_message=f"Run ID '{run_id}' not found in database.",
                active_tab="runs",
            )
            return HTMLResponse(
                content=err_content, status_code=status.HTTP_404_NOT_FOUND
            )

        template = jinja_env.get_template("run_detail.html")
        rendered = template.render(
            run=run_result,
            active_tab="runs",
        )
        return HTMLResponse(content=rendered)

    @app.get("/runs/{run_id}/download")
    def download_report(
        run_id: str,
        format: str = Query("json", description="Report format: json, junit, html"),
    ) -> Response:
        """Download an execution report file in JSON, JUnit XML, or HTML format."""
        db_mgr, repo = _get_db_and_repo()
        try:
            service = ReportService(repo)
            rendered = service.generate_report(run_id=run_id, report_format=format)
        except ConfigurationError as exc:
            msg = str(exc)
            if "not found" in msg.lower():
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"error": f"Run ID '{run_id}' not found"},
                )
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": msg},
            )
        finally:
            db_mgr.close()

        fmt_clean = format.strip().lower()
        if fmt_clean == ReportFormat.JSON.value:
            media_type = "application/json; charset=utf-8"
            filename = f"apiscope-report-{run_id}.json"
        elif fmt_clean == ReportFormat.JUNIT.value:
            media_type = "application/xml; charset=utf-8"
            filename = f"apiscope-report-{run_id}.xml"
        elif fmt_clean == ReportFormat.HTML.value:
            media_type = "text/html; charset=utf-8"
            filename = f"apiscope-report-{run_id}.html"
        else:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": f"Unsupported report format '{format}'"},
            )

        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
        }
        return Response(content=rendered, media_type=media_type, headers=headers)

    @app.get("/health", response_class=HTMLResponse)
    def health_page(
        module: str | None = None,
        run_id: str | None = None,
    ) -> HTMLResponse:
        """Render evidence-based module health verdicts, warnings, and coverage."""
        db_mgr, repo = _get_db_and_repo()
        try:
            run_result = repo.get_run(run_id) if run_id else repo.get_latest_run()
        finally:
            db_mgr.close()

        if run_result is None:
            template = jinja_env.get_template("health.html")
            rendered = template.render(reports=[], active_tab="health")
            return HTMLResponse(content=rendered)

        openapi_spec: OpenAPISpec | None = None
        if cfg.spec_path is not None and cfg.spec_path.exists():
            try:
                openapi_spec = load_openapi_spec(cfg.spec_path)
            except Exception:
                openapi_spec = None

        zombie_findings: list[Any] | None = None
        if (
            cfg.spec_path is not None
            and cfg.access_log_path is not None
            and cfg.spec_path.exists()
            and cfg.access_log_path.exists()
            and openapi_spec is not None
        ):
            try:
                entries = load_access_log(cfg.access_log_path)
                z_rep = ZombieService().analyze(
                    openapi_spec=openapi_spec,
                    access_entries=entries,
                    run_result=run_result,
                )
                zombie_findings = z_rep.findings
            except Exception:
                zombie_findings = None

        silent_signals: list[Any] | None = None
        if (
            cfg.app_log_path is not None
            or cfg.health_probe_path is not None
            or cfg.baseline_p95_ms is not None
            or run_result is not None
        ):
            try:
                app_entries = (
                    load_app_log(cfg.app_log_path)
                    if cfg.app_log_path and cfg.app_log_path.exists()
                    else None
                )
                probe_payload: dict[str, Any] | None = None
                if cfg.health_probe_path and cfg.health_probe_path.exists():
                    raw_p = cfg.health_probe_path.read_text(encoding="utf-8-sig")
                    probe_payload = json.loads(raw_p)

                sf_rep = SilentFailureService().analyze(
                    run_result=run_result,
                    app_log_entries=app_entries,
                    health_probe_payload=probe_payload,
                    baseline_p95_by_module=cfg.baseline_p95_ms,
                )
                silent_signals = sf_rep.signals
            except Exception:
                silent_signals = None

        health_svc = HealthService()
        reports: list[ModuleHealthReport] = []
        if module:
            clean_m = module.strip()
            m_lower = clean_m.lower()
            m_zombies = (
                [f for f in zombie_findings if f.module.strip().lower() == m_lower]
                if zombie_findings
                else []
            )
            m_silents = (
                [s for s in silent_signals if s.module.strip().lower() == m_lower]
                if silent_signals
                else []
            )
            rep = health_svc.evaluate_module(
                module=clean_m,
                run_result=run_result,
                openapi_spec=openapi_spec,
                baseline_p95_ms=cfg.baseline_p95_ms,
                zombie_count=len(m_zombies),
                zombie_evidence=[f.details for f in m_zombies] if m_zombies else None,
                silent_failure_count=len(m_silents),
                silent_failure_evidence=[
                    f"{s.description}: {s.evidence}" for s in m_silents
                ]
                if m_silents
                else None,
            )
            reports.append(rep)
        else:
            reports = health_svc.evaluate_all_modules(
                run_result=run_result,
                openapi_spec=openapi_spec,
                baseline_p95_ms=cfg.baseline_p95_ms,
                zombie_findings=zombie_findings,
                silent_signals=silent_signals,
            )

        template = jinja_env.get_template("health.html")
        rendered = template.render(
            reports=reports,
            active_tab="health",
        )
        return HTMLResponse(content=rendered)

    @app.get("/zombies", response_class=HTMLResponse)
    def zombies_page() -> HTMLResponse:
        """Render zombie endpoints breakdown (M9) and silent failure signals (M10)."""
        db_mgr, repo = _get_db_and_repo()
        try:
            latest_run = repo.get_latest_run()
        finally:
            db_mgr.close()

        zombie_report: ZombieReport | None = None
        if (
            cfg.spec_path is not None
            and cfg.access_log_path is not None
            and cfg.spec_path.exists()
            and cfg.access_log_path.exists()
        ):
            try:
                spec = load_openapi_spec(cfg.spec_path)
                entries = load_access_log(cfg.access_log_path)
                zombie_report = ZombieService().analyze(
                    openapi_spec=spec,
                    access_entries=entries,
                    run_result=latest_run,
                )
            except Exception:
                zombie_report = None

        silent_report: SilentFailureReport | None = None
        if (
            cfg.app_log_path is not None
            or cfg.health_probe_path is not None
            or cfg.baseline_p95_ms is not None
            or latest_run is not None
        ):
            try:
                app_entries = (
                    load_app_log(cfg.app_log_path)
                    if cfg.app_log_path and cfg.app_log_path.exists()
                    else None
                )
                probe_payload: dict[str, Any] | None = None
                if cfg.health_probe_path and cfg.health_probe_path.exists():
                    raw_p = cfg.health_probe_path.read_text(encoding="utf-8-sig")
                    probe_payload = json.loads(raw_p)

                silent_report = SilentFailureService().analyze(
                    run_result=latest_run,
                    app_log_entries=app_entries,
                    health_probe_payload=probe_payload,
                    baseline_p95_by_module=cfg.baseline_p95_ms,
                )
            except Exception:
                silent_report = None

        template = jinja_env.get_template("zombies.html")
        rendered = template.render(
            zombie_report=zombie_report,
            silent_report=silent_report,
            active_tab="zombies",
        )
        return HTMLResponse(content=rendered)

    # -------------------------------------------------------------------------
    # REST API Endpoints
    # -------------------------------------------------------------------------

    @app.post("/api/runs", status_code=status.HTTP_201_CREATED)
    def trigger_run_api(payload: TriggerRunRequest) -> JSONResponse:
        """Trigger an on-demand test suite run, save to SQLite, and return JSON."""
        # 1. Security validation against base_dir (NFR-04)
        try:
            validate_safe_path(base_dir=cfg.base_dir, ref_path=payload.suite_path)
            if payload.env_file:
                validate_safe_path(base_dir=cfg.base_dir, ref_path=payload.env_file)
            if payload.data_file:
                validate_safe_path(base_dir=cfg.base_dir, ref_path=payload.data_file)
        except PathTraversalError as pte:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": str(pte)},
            )

        # 2. Load collection and environment
        try:
            collection = _load_suite_safely(payload.suite_path, cfg.base_dir)
            environment = None
            if payload.env_file:
                safe_env = validate_safe_path(
                    base_dir=cfg.base_dir, ref_path=payload.env_file
                )
                environment = load_environment(safe_env)
        except ApiScopeError as err:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": redact_sensitive_text(str(err))},
            )
        except Exception as err:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": redact_sensitive_text(str(err))},
            )

        # 3. Execute collection
        try:
            run_result: RunResult = runner.run_collection(
                collection=collection,
                environment=environment,
                module=payload.module,
                tags=payload.tags,
                data_file=payload.data_file,
                base_dir=cfg.base_dir,
            )
        except ApiScopeError as err:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": redact_sensitive_text(str(err))},
            )
        except Exception as err:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": redact_sensitive_text(str(err))},
            )

        if run_result.total_tests == 0:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "No tests matched the specified filters"},
            )

        # 4. Persist to SQLite
        db_mgr, repo = _get_db_and_repo()
        try:
            repo.save_run(run_result)
        finally:
            db_mgr.close()

        return JSONResponse(
            status_code=status.HTTP_201_CREATED,
            content=run_result.to_dict(),
        )

    @app.get("/api/trends")
    def get_trends_api(
        limit: int = Query(20, ge=1, le=500, description="Max history points"),
        module: str | None = None,
    ) -> JSONResponse:
        """Fetch chronological execution trends JSON for Chart.js."""
        db_mgr, repo = _get_db_and_repo()
        try:
            trends = repo.get_trends(limit=limit, module=module)
            return JSONResponse(content=trends.to_dict())
        except ConfigurationError as exc:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": str(exc)},
            )
        finally:
            db_mgr.close()

    @app.get("/api/health")
    def get_health_api(
        module: str | None = None,
        run_id: str | None = None,
    ) -> JSONResponse:
        """Fetch module health verdict JSON report."""
        db_mgr, repo = _get_db_and_repo()
        try:
            run_result = repo.get_run(run_id) if run_id else repo.get_latest_run()
        finally:
            db_mgr.close()

        if run_result is None:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": "No test runs available to evaluate module health"},
            )

        openapi_spec: OpenAPISpec | None = None
        if cfg.spec_path is not None and cfg.spec_path.exists():
            try:
                openapi_spec = load_openapi_spec(cfg.spec_path)
            except Exception:
                openapi_spec = None

        zombie_findings: list[Any] | None = None
        if (
            cfg.spec_path is not None
            and cfg.access_log_path is not None
            and cfg.spec_path.exists()
            and cfg.access_log_path.exists()
            and openapi_spec is not None
        ):
            try:
                entries = load_access_log(cfg.access_log_path)
                z_rep = ZombieService().analyze(
                    openapi_spec=openapi_spec,
                    access_entries=entries,
                    run_result=run_result,
                )
                zombie_findings = z_rep.findings
            except Exception:
                zombie_findings = None

        silent_signals: list[Any] | None = None
        if (
            cfg.app_log_path is not None
            or cfg.health_probe_path is not None
            or cfg.baseline_p95_ms is not None
            or run_result is not None
        ):
            try:
                app_entries = (
                    load_app_log(cfg.app_log_path)
                    if cfg.app_log_path and cfg.app_log_path.exists()
                    else None
                )
                probe_payload: dict[str, Any] | None = None
                if cfg.health_probe_path and cfg.health_probe_path.exists():
                    raw_p = cfg.health_probe_path.read_text(encoding="utf-8-sig")
                    probe_payload = json.loads(raw_p)

                sf_rep = SilentFailureService().analyze(
                    run_result=run_result,
                    app_log_entries=app_entries,
                    health_probe_payload=probe_payload,
                    baseline_p95_by_module=cfg.baseline_p95_ms,
                )
                silent_signals = sf_rep.signals
            except Exception:
                silent_signals = None

        health_svc = HealthService()
        reports: list[ModuleHealthReport] = []
        if module:
            clean_m = module.strip()
            m_lower = clean_m.lower()
            m_zombies = (
                [f for f in zombie_findings if f.module.strip().lower() == m_lower]
                if zombie_findings
                else []
            )
            m_silents = (
                [s for s in silent_signals if s.module.strip().lower() == m_lower]
                if silent_signals
                else []
            )
            rep = health_svc.evaluate_module(
                module=clean_m,
                run_result=run_result,
                openapi_spec=openapi_spec,
                baseline_p95_ms=cfg.baseline_p95_ms,
                zombie_count=len(m_zombies),
                zombie_evidence=[f.details for f in m_zombies] if m_zombies else None,
                silent_failure_count=len(m_silents),
                silent_failure_evidence=[
                    f"{s.description}: {s.evidence}" for s in m_silents
                ]
                if m_silents
                else None,
            )
            reports.append(rep)
        else:
            reports = health_svc.evaluate_all_modules(
                run_result=run_result,
                openapi_spec=openapi_spec,
                baseline_p95_ms=cfg.baseline_p95_ms,
                zombie_findings=zombie_findings,
                silent_signals=silent_signals,
            )

        return JSONResponse(
            content={
                "run_id": run_result.run_id,
                "reports": [r.to_dict() for r in reports],
            }
        )

    @app.post("/api/approve", status_code=status.HTTP_200_OK)
    def approve_suite_api(payload: ApproveSuiteRequest) -> JSONResponse:
        """Approve proposed test definitions in a suite and promote to approved."""
        try:
            validate_safe_path(base_dir=cfg.base_dir, ref_path=payload.suite_path)
            if payload.output_path:
                validate_safe_path(base_dir=cfg.base_dir, ref_path=payload.output_path)
            service = AiGeneratorService()
            collection = service.approve_suite(
                suite_path=payload.suite_path,
                output_path=payload.output_path,
                base_dir=cfg.base_dir,
            )
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "status": "success",
                    "approved_count": len(collection),
                    "suite_path": payload.suite_path,
                },
            )
        except ApiScopeError as exc:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": redact_sensitive_text(str(exc))},
            )
        except Exception as exc:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": redact_sensitive_text(str(exc))},
            )

    @app.post("/api/ai-generate", status_code=status.HTTP_201_CREATED)
    def ai_generate_api(payload: AiGenerateSuiteRequest) -> JSONResponse:
        """Trigger AI test generation via live LLM or fallback synthesis."""
        try:
            validate_safe_path(base_dir=cfg.base_dir, ref_path=payload.spec_path)
            validate_safe_path(base_dir=cfg.base_dir, ref_path=payload.profile_path)
            validate_safe_path(base_dir=cfg.base_dir, ref_path=payload.output_path)

            llm_cfg = LLMConfig.from_env(
                provider=payload.provider,
                api_key=payload.api_key,
                model=payload.model,
            )
            service = AiGeneratorService(config=llm_cfg)
            collection = service.generate_ai_suite(
                spec_path=payload.spec_path,
                profile_path=payload.profile_path,
                output_path=payload.output_path,
                module=payload.module,
                base_dir=cfg.base_dir,
            )
            return JSONResponse(
                status_code=status.HTTP_201_CREATED,
                content={
                    "status": "success",
                    "generated_count": len(collection),
                    "output_path": payload.output_path,
                },
            )
        except ApiScopeError as exc:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": redact_sensitive_text(str(exc))},
            )
        except Exception as exc:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": redact_sensitive_text(str(exc))},
            )

    return app
