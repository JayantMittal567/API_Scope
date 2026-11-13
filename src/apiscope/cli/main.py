"""Command-line interface entry point for ApiScope.

Provides non-interactive Typer commands for initializing projects,
executing test suites, exporting multi-format reports, and analyzing
historical run trends.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

import apiscope
from apiscope.analysis.health_models import HealthVerdict, ModuleHealthReport
from apiscope.analysis.health_service import HealthService
from apiscope.analysis.silent_failure_service import (
    SilentFailureService,
    SilentFailureSignal,
    load_app_log,
)
from apiscope.analysis.zombie_service import (
    ZombieFinding,
    ZombieService,
    load_access_log,
)
from apiscope.core.exceptions import (
    ApiScopeError,
    ConfigurationError,
    DefinitionError,
    PathTraversalError,
)
from apiscope.core.run_service import RunService
from apiscope.data.connection import DatabaseManager
from apiscope.data.repositories import RunRepository
from apiscope.definitions.collection import (
    TestCollection,
    load_collection,
    validate_safe_path,
)
from apiscope.definitions.environment import Environment, load_environment
from apiscope.definitions.yaml_loader import load_test_definition
from apiscope.generators.ai_generator import AiGeneratorService, LLMConfig
from apiscope.generators.openapi_importer import OpenAPISpec, load_openapi_spec
from apiscope.generators.service import GeneratorService
from apiscope.reports.report_service import ReportService
from apiscope.web import DashboardConfig, create_web_app

# Canonical CLI Exit Codes (FR-26)
EXIT_OK: int = 0
EXIT_TEST_FAILURE: int = 1
EXIT_WARNINGS_STRICT: int = 2
EXIT_TOOL_ERROR: int = 3

app = typer.Typer(
    name="apiscope",
    help=(
        "ApiScope: Automated API Testing, Module Health Monitoring and Analysis "
        "Platform."
    ),
    add_completion=False,
    no_args_is_help=True,
)

_STARTER_ENV_YAML = """name: "development"
variables:
  base_url: "http://localhost:8000"
  api_key: "demo-secret-key"
secrets:
  - "api_key"
"""

_STARTER_SUITE_YAML = """name: "Sample Test Suite"
module: "sample"
tags:
  - "sample"
  - "smoke"
tests:
  - name: "Health Check"
    module: "health"
    tags:
      - "smoke"
    request:
      method: "GET"
      url: "{{base_url}}/health"
    assertions:
      - type: "status"
        equals: 200
"""


def _load_suite(suite_path: Path) -> TestCollection:
    """Safely inspect and load a TestCollection or single TestDefinition.

    Args:
        suite_path: Path to YAML test suite or test definition.

    Returns:
        TestCollection instance ready for execution.

    Raises:
        PathTraversalError: If suite_path contains directory traversal indicators.
        DefinitionError: If the suite file is missing or invalid.
    """
    ref_str = str(suite_path).strip()
    normalized = ref_str.replace("\\", "/")
    if (
        "../" in normalized
        or normalized.startswith("../")
        or normalized == ".."
        or "/../" in normalized
        or normalized.endswith("/..")
    ):
        raise PathTraversalError(
            f"Forbidden directory traversal sequence detected in '{ref_str}'."
        )

    p = Path(ref_str)
    effective_base = p.parent if p.is_absolute() else Path.cwd()
    safe_p = validate_safe_path(base_dir=effective_base, ref_path=suite_path)

    if not safe_p.is_file():
        raise DefinitionError(f"Test suite file not found: '{suite_path}'")

    try:
        content = safe_p.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
    except Exception as err:
        raise DefinitionError(
            f"Error reading test suite '{suite_path}': {err}"
        ) from err

    if not isinstance(data, dict):
        raise DefinitionError(
            f"Test suite '{suite_path}' is empty or not a valid dictionary mapping."
        )

    if "tests" in data:
        return load_collection(safe_p)
    elif "request" in data:
        test_def = load_test_definition(safe_p)
        return TestCollection(
            name=test_def.name,
            module=test_def.module,
            tags=list(test_def.tags),
            tests=[test_def],
            base_dir=safe_p.parent,
            file_path=safe_p,
        )
    else:
        raise DefinitionError(
            f"Test suite '{suite_path}' must contain either 'tests' or 'request'."
        )


@app.callback()
def callback() -> None:
    """ApiScope CLI: Automated API Testing and Monitoring Platform."""


@app.command()
def version() -> None:
    """Display ApiScope version information."""
    typer.echo(f"ApiScope version {apiscope.__version__}")


@app.command()
def init(
    directory: Annotated[
        Path,
        typer.Option(
            "--dir",
            help="Directory to initialize starter files",
        ),
    ] = Path("."),
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Overwrite existing starter files",
        ),
    ] = False,
) -> None:
    """Initialize a starter ApiScope test suite and environment configuration."""
    try:
        dir_p = Path(directory)
        effective_base = dir_p.parent if dir_p.is_absolute() else Path.cwd()
        safe_dir = validate_safe_path(base_dir=effective_base, ref_path=directory)
        safe_dir.mkdir(parents=True, exist_ok=True)

        env_path = safe_dir / "apiscope.env.yaml"
        suite_path = safe_dir / "sample_suite.yaml"

        if env_path.exists() and suite_path.exists() and not force:
            typer.echo(
                f"Starter files already exist in {safe_dir}. Use --force to overwrite."
            )
            return

        env_path.write_text(_STARTER_ENV_YAML, encoding="utf-8")
        suite_path.write_text(_STARTER_SUITE_YAML, encoding="utf-8")
        typer.echo(f"Initialized ApiScope project in {safe_dir}")
    except ApiScopeError as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err
    except Exception as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err


@app.command()
def run(
    suite_path: Annotated[
        Path,
        typer.Argument(help="Path to YAML test definition file"),
    ],
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="Path to YAML environment file"),
    ] = None,
    env: Annotated[
        str | None,
        typer.Option("--env", help="Environment name to use"),
    ] = None,
    module: Annotated[
        str | None,
        typer.Option("--module", help="Filter tests by module name"),
    ] = None,
    tag: Annotated[
        list[str] | None,
        typer.Option(
            "--tag", help="Filter tests by tag (can be passed multiple times)"
        ),
    ] = None,
    data_file: Annotated[
        Path | None,
        typer.Option("--data-file", help="Path to CSV or JSON data file"),
    ] = None,
    db: Annotated[
        Path,
        typer.Option("--db", help="Path to SQLite history database"),
    ] = Path(".apiscope/history.db"),
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Optional path to write report file"),
    ] = None,
    format: Annotated[
        str,
        typer.Option(
            "--format",
            help="Report format if --output is set (json, junit, html)",
        ),
    ] = "json",
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Treat warnings as exit code 2"),
    ] = False,
    include_proposed: Annotated[
        bool,
        typer.Option(
            "--include-proposed",
            help="Execute tests with status 'proposed'",
        ),
    ] = False,
) -> None:
    """Execute API test suites and record execution telemetry."""
    db_manager: DatabaseManager | None = None
    try:
        environment: Environment | None = None
        if env_file is not None:
            env_p = Path(env_file)
            effective_base = env_p.parent if env_p.is_absolute() else Path.cwd()
            safe_env_path = validate_safe_path(
                base_dir=effective_base, ref_path=env_file
            )
            environment = load_environment(safe_env_path)
            if env is not None and environment.name != env:
                raise ConfigurationError(
                    f"Environment '{env}' not found in '{env_file}'"
                )
        elif env is not None:
            default_env_path = Path("apiscope.env.yaml")
            if default_env_path.is_file():
                environment = load_environment(default_env_path)
                if environment.name != env:
                    raise ConfigurationError(
                        f"Environment '{env}' not found in '{default_env_path}'"
                    )
            else:
                raise ConfigurationError("No environment file provided")

        collection = _load_suite(suite_path)

        run_service = RunService()
        run_result = run_service.run_collection(
            collection=collection,
            environment=environment,
            module=module,
            tags=tag,
            data_file=data_file,
            include_proposed=include_proposed,
        )

        if run_result.total_tests == 0:
            typer.echo("Error: No tests matched the specified filters", err=True)
            raise typer.Exit(code=EXIT_TOOL_ERROR)

        has_warnings = bool(getattr(run_result, "has_warnings", False))

        db_manager = DatabaseManager(db)
        repo = RunRepository(db_manager)
        repo.save_run(run_result, has_warnings=has_warnings)

        if output is not None:
            report_service = ReportService(repo)
            report_service.generate_report(
                run_id=run_result.run_id,
                report_format=format,
                output_path=output,
            )

        typer.echo(
            f"Run ID: {run_result.run_id} | Total: {run_result.total_tests} | "
            f"Passed: {run_result.passed_tests} | Failed: {run_result.failed_tests} | "
            f"Errors: {run_result.error_tests} | Skipped: {run_result.skipped_tests} | "
            f"Duration: {run_result.duration_ms:.1f}ms"
        )

        if not run_result.all_passed:
            raise typer.Exit(code=EXIT_TEST_FAILURE)

        if has_warnings and strict:
            raise typer.Exit(code=EXIT_WARNINGS_STRICT)

        raise typer.Exit(code=EXIT_OK)

    except typer.Exit:
        raise
    except ApiScopeError as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err
    except Exception as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err


@app.command()
def report(
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Specific Run ID (defaults to latest run)"),
    ] = None,
    db: Annotated[
        Path,
        typer.Option("--db", help="Path to SQLite history database"),
    ] = Path(".apiscope/history.db"),
    format: Annotated[
        str,
        typer.Option("--format", help="Report format: json, junit, html"),
    ] = "json",
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            help="File path to save report (prints to stdout if omitted)",
        ),
    ] = None,
) -> None:
    """Generate and display or export test execution reports from history."""
    db_manager: DatabaseManager | None = None
    try:
        db_manager = DatabaseManager(db)
        repo = RunRepository(db_manager)
        service = ReportService(repo)
        rendered = service.generate_report(
            run_id=run_id, report_format=format, output_path=output
        )

        if output is None:
            typer.echo(rendered)
        else:
            typer.echo(f"Report saved to {output}")

        raise typer.Exit(code=EXIT_OK)
    except typer.Exit:
        raise
    except ApiScopeError as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err
    except Exception as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err


@app.command(name="import")
def import_cmd(
    spec_path: Annotated[
        Path,
        typer.Argument(help="Path to OpenAPI 3 YAML or JSON specification"),
    ],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Optional path to save normalized spec summary",
        ),
    ] = None,
) -> None:
    """Import and validate an OpenAPI 3 specification."""
    try:
        service = GeneratorService()
        spec = service.import_spec(spec_path, output_path=output)
        unique_modules = sorted({ep.module for ep in spec.endpoints})
        modules_str = ", ".join(unique_modules) if unique_modules else "none"
        typer.echo(
            f"Imported OpenAPI {spec.openapi_version}: {spec.title} v{spec.version} | "
            f"Endpoints: {len(spec.endpoints)} | Modules: {modules_str}"
        )
        raise typer.Exit(code=EXIT_OK)
    except typer.Exit:
        raise
    except ApiScopeError as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err
    except Exception as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err


@app.command(name="generate")
def generate_cmd(
    spec_path: Annotated[
        Path,
        typer.Argument(help="Path to OpenAPI 3 YAML or JSON specification"),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Output path for generated YAML test collection",
        ),
    ],
    module: Annotated[
        str | None,
        typer.Option(
            "--module",
            "-m",
            help="Generate tests only for the specified module",
        ),
    ] = None,
) -> None:
    """Generate rule-based API tests from an OpenAPI 3 specification."""
    try:
        service = GeneratorService()
        collection = service.generate_from_spec(
            spec_path, output_path=output, module=module
        )
        if len(collection) == 0:
            typer.echo(
                "Error: No tests were generated from the specification.",
                err=True,
            )
            raise typer.Exit(code=EXIT_TOOL_ERROR)

        pos_count = sum(1 for t in collection if "positive" in t.tags)
        neg_count = sum(1 for t in collection if "negative" in t.tags)
        bnd_count = sum(1 for t in collection if "boundary" in t.tags)

        typer.echo(
            f"Generated {len(collection)} rule-based tests -> {output} "
            f"(positive: {pos_count}, negative: {neg_count}, boundary: {bnd_count})"
        )
        raise typer.Exit(code=EXIT_OK)
    except typer.Exit:
        raise
    except ApiScopeError as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err
    except Exception as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err


@app.command(name="ai-generate")
def ai_generate(
    spec_path: Annotated[
        Path,
        typer.Argument(help="Path to OpenAPI 3 specification"),
    ],
    profile: Annotated[
        Path,
        typer.Option("--profile", "-p", help="Path to YAML Project Profile"),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Output path for generated YAML suite"),
    ],
    provider: Annotated[
        str,
        typer.Option(
            "--provider",
            help="LLM provider: gemini, openai, or profile-fallback",
        ),
    ] = "gemini",
    model: Annotated[
        str | None,
        typer.Option("--model", help="Optional LLM model override"),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option(
            "--api-key",
            envvar="APISCOPE_LLM_API_KEY",
            help="LLM API key (or set via environment / .env)",
        ),
    ] = None,
    module: Annotated[
        str | None,
        typer.Option("--module", "-m", help="Optional module filter"),
    ] = None,
) -> None:
    """Generate project-aware API test suites using live LLM or fallback synthesis."""
    try:
        cfg = LLMConfig.from_env(
            provider=provider,
            api_key=api_key,
            model=model,
        )
        service = AiGeneratorService(config=cfg)
        collection = service.generate_ai_suite(
            spec_path=spec_path,
            profile_path=profile,
            output_path=output,
            module=module,
        )
        typer.echo(
            f"Generated {len(collection)} AI test cases (status: proposed) -> {output}"
        )
        raise typer.Exit(code=EXIT_OK)
    except typer.Exit:
        raise
    except ApiScopeError as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err
    except Exception as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err


@app.command(name="approve")
def approve(
    suite_path: Annotated[
        Path,
        typer.Argument(help="Path to YAML test collection with proposed tests"),
    ],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Optional output path (updates in-place if omitted)",
        ),
    ] = None,
) -> None:
    """Approve proposed AI test definitions and promote them to executable status."""
    try:
        service = AiGeneratorService()
        target_path = output if output is not None else suite_path
        collection = service.approve_suite(
            suite_path=suite_path,
            output_path=output,
        )
        typer.echo(f"Approved {len(collection)} tests in {target_path}")
        raise typer.Exit(code=EXIT_OK)
    except typer.Exit:
        raise
    except ApiScopeError as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err
    except Exception as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err


@app.command()
def zombie(
    spec: Annotated[
        Path,
        typer.Option(
            "--spec",
            "-s",
            help="Path to OpenAPI 3 specification",
        ),
    ],
    access_log: Annotated[
        Path,
        typer.Option(
            "--access-log",
            "-l",
            help="Path to access log file",
        ),
    ],
    run_id: Annotated[
        str | None,
        typer.Option(
            "--run-id",
            help="Optional Run ID from DB for UNTESTED analysis",
        ),
    ] = None,
    db: Annotated[
        Path | None,
        typer.Option(
            "--db",
            help="Optional SQLite DB path for test coverage lookup",
        ),
    ] = None,
    module: Annotated[
        str | None,
        typer.Option(
            "--module",
            "-m",
            help="Filter findings by module",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Optional file path to write JSON zombie report",
        ),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Exit with code 2 if any zombie endpoints are found",
        ),
    ] = False,
) -> None:
    """Analyze and detect zombie API endpoints across four canonical categories."""
    db_manager: DatabaseManager | None = None
    try:
        openapi_spec = load_openapi_spec(spec)
        access_entries = load_access_log(access_log)

        run_result = None
        if db is not None:
            if not db.exists():
                raise ConfigurationError(f"Database file not found: {db}")
            db_manager = DatabaseManager(db)
            repo = RunRepository(db_manager)
            run_result = (
                repo.get_run(run_id) if run_id is not None else repo.get_latest_run()
            )

        service = ZombieService()
        report = service.analyze(
            openapi_spec=openapi_spec,
            access_entries=access_entries,
            run_result=run_result,
            module=module,
        )

        typer.echo(f"Zombie API Analysis: {report.total_findings} finding(s) detected")
        for cls_name, count in report.counts_by_class.items():
            typer.echo(f"  {cls_name}: {count}")

        if report.findings:
            typer.echo("\nFindings:")
            for f in report.findings:
                typer.echo(
                    f"  [{f.classification.value}] {f.method} {f.normalized_path} "
                    f"(module: {f.module}, calls: {f.call_count}) - {f.details}"
                )

        if output is not None:
            out_p = Path(output)
            effective_base = out_p.parent if out_p.is_absolute() else Path.cwd()
            safe_out = validate_safe_path(base_dir=effective_base, ref_path=output)
            safe_out.parent.mkdir(parents=True, exist_ok=True)
            safe_out.write_text(
                json.dumps(report.to_dict(), indent=2), encoding="utf-8"
            )
            typer.echo(f"\nZombie report saved to {safe_out}")

        if report.total_findings > 0 and strict:
            raise typer.Exit(code=EXIT_WARNINGS_STRICT)

        raise typer.Exit(code=EXIT_OK)

    except typer.Exit:
        raise
    except ApiScopeError as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err
    except Exception as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err


@app.command()
def health(
    module: Annotated[
        str | None,
        typer.Option(
            "--module",
            "-m",
            help="Module name to evaluate (evaluates all modules if omitted)",
        ),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option(
            "--run-id",
            help="Specific Run ID from DB (defaults to latest run)",
        ),
    ] = None,
    db: Annotated[
        Path,
        typer.Option(
            "--db",
            help="Path to SQLite history database",
        ),
    ] = Path(".apiscope/history.db"),
    spec: Annotated[
        Path | None,
        typer.Option(
            "--spec",
            "-s",
            help="Optional OpenAPI 3 specification file for endpoint coverage",
        ),
    ] = None,
    threshold: Annotated[
        float,
        typer.Option(
            "--threshold",
            help="Minimum endpoint coverage percentage (0-100)",
        ),
    ] = 80.0,
    baseline_p95: Annotated[
        float | None,
        typer.Option(
            "--baseline-p95",
            help="Optional baseline p95 latency in ms",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Optional file path to write JSON health report",
        ),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Exit with code 2 on HEALTHY WITH WARNINGS",
        ),
    ] = False,
    access_log: Annotated[
        Path | None,
        typer.Option(
            "--access-log",
            help="Optional access log file for zombie detection",
        ),
    ] = None,
    app_log: Annotated[
        Path | None,
        typer.Option(
            "--app-log",
            help="Optional application log file for silent failure S1 detection",
        ),
    ] = None,
    health_file: Annotated[
        Path | None,
        typer.Option(
            "--health-file",
            help="Optional JSON file containing /health probe payload for S2 detection",
        ),
    ] = None,
) -> None:
    """Evaluate and report evidence-based API health verdicts for modules."""
    db_manager: DatabaseManager | None = None
    try:
        db_manager = DatabaseManager(db)
        repo = RunRepository(db_manager)
        run_result = (
            repo.get_run(run_id) if run_id is not None else repo.get_latest_run()
        )
        if run_result is None:
            typer.echo(
                f"Error: No test run found in database '{db}'"
                + (f" for run ID '{run_id}'" if run_id else "."),
                err=True,
            )
            raise typer.Exit(code=EXIT_TOOL_ERROR)

        openapi_spec: OpenAPISpec | None = None
        if spec is not None:
            openapi_spec = load_openapi_spec(spec)

        zombie_findings: list[ZombieFinding] | None = None
        if access_log is not None:
            if openapi_spec is None:
                typer.echo(
                    "Error: --spec is required when --access-log is provided.",
                    err=True,
                )
                raise typer.Exit(code=EXIT_TOOL_ERROR)
            access_entries = load_access_log(access_log)
            z_service = ZombieService()
            z_report = z_service.analyze(
                openapi_spec=openapi_spec,
                access_entries=access_entries,
                run_result=run_result,
            )
            zombie_findings = z_report.findings

        silent_signals: list[SilentFailureSignal] | None = None
        if (
            app_log is not None
            or health_file is not None
            or baseline_p95 is not None
            or run_result is not None
        ):
            app_entries = load_app_log(app_log) if app_log is not None else None
            health_probe_payload: dict[str, Any] | None = None
            if health_file is not None:
                h_p = Path(health_file)
                effective_base = h_p.parent if h_p.is_absolute() else Path.cwd()
                safe_health_path = validate_safe_path(
                    base_dir=effective_base, ref_path=health_file
                )
                if not safe_health_path.exists():
                    raise ConfigurationError(f"Health file not found: {health_file}")
                raw_json = safe_health_path.read_text(encoding="utf-8-sig")
                try:
                    health_probe_payload = json.loads(raw_json)
                except Exception as exc:
                    raise ConfigurationError(
                        f"Invalid JSON in health file: {exc}"
                    ) from exc

            sf_service = SilentFailureService()
            sf_report = sf_service.analyze(
                run_result=run_result,
                app_log_entries=app_entries,
                health_probe_payload=health_probe_payload,
                baseline_p95_by_module=baseline_p95,
                module=module,
            )
            if sf_report.signals:
                silent_signals = sf_report.signals

        service = HealthService()
        reports: list[ModuleHealthReport] = []
        if module:
            m_clean = module.strip()
            m_lower = m_clean.lower()
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
            rep = service.evaluate_module(
                module=m_clean,
                run_result=run_result,
                openapi_spec=openapi_spec,
                coverage_threshold_pct=threshold,
                baseline_p95_ms=baseline_p95,
                zombie_count=len(m_zombies),
                zombie_evidence=([f.details for f in m_zombies] if m_zombies else None),
                silent_failure_count=len(m_silents),
                silent_failure_evidence=(
                    [f"{s.description}: {s.evidence}" for s in m_silents]
                    if m_silents
                    else None
                ),
            )
            reports.append(rep)
        else:
            reports = service.evaluate_all_modules(
                run_result=run_result,
                openapi_spec=openapi_spec,
                coverage_threshold_pct=threshold,
                baseline_p95_ms=baseline_p95,
                zombie_findings=zombie_findings,
                silent_signals=silent_signals,
            )
            if not reports:
                typer.echo("Error: No modules found to evaluate.", err=True)
                raise typer.Exit(code=EXIT_TOOL_ERROR)

        for rep in reports:
            typer.echo(f"=== Module Health: {rep.module} [{rep.verdict.value}] ===")
            typer.echo(f"Summary: {rep.summary}")
            typer.echo("Evidence:")
            for ev in rep.evidence:
                typer.echo(f"  - {ev}")
            if rep.warnings:
                typer.echo("Warnings:")
                for w in rep.warnings:
                    typer.echo(f"  ! {w}")
            if rep.failures:
                typer.echo("Failures:")
                for f in rep.failures:
                    typer.echo(f"  x {f}")
            typer.echo("")

        if output is not None:
            out_p = Path(output)
            effective_base = out_p.parent if out_p.is_absolute() else Path.cwd()
            safe_out = validate_safe_path(base_dir=effective_base, ref_path=output)
            safe_out.parent.mkdir(parents=True, exist_ok=True)
            data_to_write = (
                reports[0].to_dict()
                if len(reports) == 1 and module is not None
                else [r.to_dict() for r in reports]
            )
            safe_out.write_text(json.dumps(data_to_write, indent=2), encoding="utf-8")
            typer.echo(f"Health report saved to {safe_out}")

        has_unhealthy_or_incomplete = any(
            r.verdict in (HealthVerdict.UNHEALTHY, HealthVerdict.INCOMPLETE)
            for r in reports
        )
        if has_unhealthy_or_incomplete:
            raise typer.Exit(code=EXIT_TEST_FAILURE)

        has_warnings = any(
            r.verdict == HealthVerdict.HEALTHY_WITH_WARNINGS for r in reports
        )
        if has_warnings and strict:
            raise typer.Exit(code=EXIT_WARNINGS_STRICT)

        raise typer.Exit(code=EXIT_OK)

    except typer.Exit:
        raise
    except ApiScopeError as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err
    except Exception as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err


@app.command()
def serve(
    host: Annotated[
        str,
        typer.Option(
            "--host",
            help="Bind host (defaults to 127.0.0.1 for local safety)",
        ),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option(
            "--port",
            "-p",
            help="Bind port (1-65535)",
        ),
    ] = 8080,
    db: Annotated[
        Path,
        typer.Option(
            "--db",
            help="Path to SQLite history database",
        ),
    ] = Path(".apiscope/history.db"),
    spec: Annotated[
        Path | None,
        typer.Option(
            "--spec",
            "-s",
            help="Optional OpenAPI 3 specification file",
        ),
    ] = None,
    access_log: Annotated[
        Path | None,
        typer.Option(
            "--access-log",
            help="Optional access log file for zombie report",
        ),
    ] = None,
    app_log: Annotated[
        Path | None,
        typer.Option(
            "--app-log",
            help="Optional application log file for silent failure S1",
        ),
    ] = None,
    health_file: Annotated[
        Path | None,
        typer.Option(
            "--health-file",
            help="Optional health probe JSON file for S2",
        ),
    ] = None,
    baseline_p95: Annotated[
        float | None,
        typer.Option(
            "--baseline-p95",
            help="Optional baseline p95 latency in ms",
        ),
    ] = None,
    check_only: Annotated[
        bool,
        typer.Option(
            "--check-only",
            help="Validate configuration and build app without starting server",
        ),
    ] = False,
) -> None:
    """Launch the ApiScope Web Dashboard with FastAPI and Chart.js."""
    try:
        if port < 1 or port > 65535:
            raise ConfigurationError("Port must be between 1 and 65535")

        safe_spec: Path | None = None
        if spec is not None:
            safe_spec = validate_safe_path(base_dir=Path.cwd(), ref_path=spec)
            if not safe_spec.is_file():
                raise ConfigurationError(f"OpenAPI spec file not found: {spec}")

        safe_access_log: Path | None = None
        if access_log is not None:
            safe_access_log = validate_safe_path(
                base_dir=Path.cwd(), ref_path=access_log
            )
            if not safe_access_log.is_file():
                raise ConfigurationError(f"Access log file not found: {access_log}")

        safe_app_log: Path | None = None
        if app_log is not None:
            safe_app_log = validate_safe_path(base_dir=Path.cwd(), ref_path=app_log)
            if not safe_app_log.is_file():
                raise ConfigurationError(f"Application log file not found: {app_log}")

        safe_health_file: Path | None = None
        if health_file is not None:
            safe_health_file = validate_safe_path(
                base_dir=Path.cwd(), ref_path=health_file
            )
            if not safe_health_file.is_file():
                raise ConfigurationError(f"Health probe file not found: {health_file}")

        config = DashboardConfig(
            db_path=db,
            base_dir=Path.cwd(),
            spec_path=safe_spec,
            access_log_path=safe_access_log,
            app_log_path=safe_app_log,
            health_probe_path=safe_health_file,
            baseline_p95_ms=baseline_p95,
        )

        web_app = create_web_app(config)
        typer.echo(f"ApiScope Dashboard ready at http://{host}:{port} (DB: {db})")

        if check_only:
            raise typer.Exit(code=EXIT_OK)

        import uvicorn

        uvicorn.run(web_app, host=host, port=port, log_level="info")
        raise typer.Exit(code=EXIT_OK)

    except typer.Exit:
        raise
    except ApiScopeError as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err
    except Exception as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err


def main() -> None:
    """Primary entry point for the console script."""
    app()


if __name__ == "__main__":
    main()
