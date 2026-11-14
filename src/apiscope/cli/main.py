"""Command-line interface entry point for ApiScope.

Provides non-interactive Typer commands for initializing projects,
executing test suites, exporting multi-format reports, and analyzing
historical run trends.
"""

from __future__ import annotations

import contextlib
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
from apiscope.compiler.service import TestPlanCompiler
from apiscope.contract.service import ContractAnalysisService
from apiscope.core.exceptions import (
    ApiScopeError,
    ConfigurationError,
    DefinitionError,
    PathTraversalError,
)
from apiscope.core.executor import RequestExecutor, TargetAccessPolicy
from apiscope.core.results import RunResult
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
from apiscope.execution import WorkflowExecutionService
from apiscope.execution.models import WorkflowDryRunReport, WorkflowExecutionResult
from apiscope.fixtures.models import FixturePreparationReport
from apiscope.fixtures.resolver import FixtureResolver
from apiscope.fixtures.service import FixtureService
from apiscope.generators.ai_generator import AiGeneratorService, LLMConfig
from apiscope.generators.openapi_importer import OpenAPISpec, load_openapi_spec
from apiscope.generators.service import GeneratorService
from apiscope.intelligence.service import ProjectIntelligenceService
from apiscope.investigation import (
    FailureInvestigationReport,
    FailureInvestigationService,
)
from apiscope.planning.service import TestPlanningService
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


def _is_workflow_file(path: Path) -> bool:
    """Safely inspect file to determine whether it contains a stateful workflow or report."""
    if not path.is_file():
        return False
    try:
        content = path.read_text(encoding="utf-8")
        data = (
            yaml.safe_load(content)
            if path.suffix in (".yaml", ".yml")
            else json.loads(content)
        )
        if isinstance(data, dict):
            if "planned_workflows" in data:
                return True
            if "workflow_id" in data and (
                "setup_steps" in data or "test_steps" in data or "cleanup_steps" in data
            ):
                return True
    except Exception:
        return False
    return False


def _print_workflow_dry_run(report: WorkflowDryRunReport) -> None:
    """Render a structured dry-run execution plan for a stateful workflow."""
    typer.echo(f"\n=== DRY-RUN WORKFLOW PLAN: {report.workflow_id} ===")
    if report.environment_name:
        typer.echo(f"Target Environment: {report.environment_name}")
    typer.echo(f"Execution Eligible: {'YES' if report.eligible_to_execute else 'NO'}")
    if not report.eligible_to_execute and report.ineligibility_reasons:
        for reason in report.ineligibility_reasons:
            typer.echo(f"  [INELIGIBLE REASON] {reason}")

    typer.echo(
        f"\nPlanned Steps: "
        f"{len(report.planned_setup_steps)} Setup | "
        f"{len(report.planned_test_steps)} Test | "
        f"{len(report.planned_cleanup_steps)} Cleanup (LIFO)"
    )

    if report.planned_setup_steps:
        typer.echo("-" * 80)
        typer.echo("SETUP STEPS:")
        for step in report.planned_setup_steps:
            vars_info = (
                f" -> Extracts: {list(step.get('extract', {}).keys())}"
                if step.get("extract")
                else ""
            )
            typer.echo(
                f"  {step.get('method', 'GET'):<6} {step.get('url', ''):<42} | "
                f"Risk: {step.get('risk', ''):<10} | {step.get('name', '')}{vars_info}"
            )

    if report.planned_test_steps:
        typer.echo("-" * 80)
        typer.echo("TEST STEPS:")
        for step in report.planned_test_steps:
            assert_count = len(step.get("assertions", []))
            typer.echo(
                f"  {step.get('method', 'GET'):<6} {step.get('url', ''):<42} | "
                f"Risk: {step.get('risk', ''):<10} | Assertions: {assert_count:<2} | {step.get('name', '')}"
            )

    if report.planned_cleanup_steps:
        typer.echo("-" * 80)
        typer.echo("CLEANUP STEPS (Reverse / LIFO Dependency Order):")
        for step in report.planned_cleanup_steps:
            rollback = step.get("rollback_endpoint") or ""
            target_info = f" (Target: {rollback})" if rollback else ""
            typer.echo(
                f"  {step.get('method', 'DELETE'):<6} {step.get('url', ''):<42} | "
                f"Risk: {step.get('risk', ''):<10} | {step.get('name', '')}{target_info}"
            )

    if report.missing_variables:
        typer.echo("-" * 80)
        typer.echo(f"Missing Variables: {', '.join(sorted(report.missing_variables))}")
    typer.echo("-" * 80)


def _print_workflow_execution(result: WorkflowExecutionResult) -> None:
    """Print step-by-step progress and final status for a workflow execution."""
    typer.echo(f"\n--- Workflow: {result.workflow_id} ---")
    if result.setup_results:
        typer.echo("Setup Results:")
        for s in result.setup_results:
            err = f" | Error: {s.error_message}" if s.error_message else ""
            typer.echo(
                f"  [SETUP]   {s.name:<30} -> {s.status.value:<12} ({s.elapsed_ms:.1f}ms){err}"
            )

    typer.echo("Test Results:")
    for s in result.test_results:
        err = f" | Error: {s.error_message}" if s.error_message else ""
        typer.echo(
            f"  [TEST]    {s.name:<30} -> {s.status.value:<12} ({s.elapsed_ms:.1f}ms){err}"
        )

    if result.cleanup_results:
        typer.echo("Cleanup Results (LIFO):")
        for s in result.cleanup_results:
            err = f" | Error: {s.error_message}" if s.error_message else ""
            typer.echo(
                f"  [CLEANUP] {s.name:<30} -> {s.status.value:<12} ({s.elapsed_ms:.1f}ms){err}"
            )

    typer.echo(
        f"Workflow Verdict: {result.status.value} | Duration: {result.duration_ms:.1f}ms"
    )


def _execute_workflow_entry(
    workflow_path: Path,
    workflow_id: str | None,
    environment: Environment | None,
    allowed_targets_list: list[str],
    allow_mutation: bool,
    allow_setup_mutation: bool | None,
    allow_cleanup_mutation: bool | None,
    confirm_isolated_env: bool,
    fixtures_path: Path | None,
    dry_run: bool,
    db: Path,
    output: Path | None,
    format: str,
    strict: bool,
) -> None:
    """Execute or dry-run stateful workflows and record results."""
    static_fixtures: dict[str, Any] | None = None
    if fixtures_path is not None:
        p_fix = Path(fixtures_path)
        effective_base = p_fix.parent if p_fix.is_absolute() else Path.cwd()
        safe_fix_path = validate_safe_path(
            base_dir=effective_base,
            ref_path=fixtures_path,
        )
        if not safe_fix_path.is_file():
            raise DefinitionError(f"Fixtures file not found: '{fixtures_path}'")
        resolver = FixtureResolver(fixture_file=safe_fix_path)
        static_fixtures = resolver.get_all_resolved_values()

    policy = TargetAccessPolicy(
        allow_private_networks=False,
        allowed_targets=allowed_targets_list if allowed_targets_list else None,
    )
    executor = RequestExecutor(policy=policy)
    wf_service = WorkflowExecutionService(executor=executor)

    workflows = wf_service.load_workflows_from_file(
        file_path=workflow_path,
        workflow_id=workflow_id,
    )
    if not workflows:
        raise DefinitionError(f"No workflows found to execute in '{workflow_path}'")

    if dry_run:
        for wf in workflows:
            report = wf_service.dry_run_workflow(
                workflow=wf,
                environment=environment,
                allow_mutation=allow_mutation,
                allow_setup_mutation=allow_setup_mutation,
                allow_cleanup_mutation=allow_cleanup_mutation,
                confirm_isolated_env=confirm_isolated_env,
                static_fixtures=static_fixtures,
            )
            _print_workflow_dry_run(report)
        return

    wf_results, run_result = wf_service.execute_workflows(
        workflows=workflows,
        environment=environment,
        allow_mutation=allow_mutation,
        allow_setup_mutation=allow_setup_mutation,
        allow_cleanup_mutation=allow_cleanup_mutation,
        confirm_isolated_env=confirm_isolated_env,
        static_fixtures=static_fixtures,
    )

    for res in wf_results:
        _print_workflow_execution(res)

    db_manager = DatabaseManager(db)
    repo = RunRepository(db_manager)
    repo.save_run(run_result, has_warnings=run_result.has_warnings)

    if output is not None:
        report_service = ReportService(repo)
        report_service.generate_report(
            run_id=run_result.run_id,
            report_format=format,
            output_path=output,
        )

    typer.echo(
        f"\nRun ID: {run_result.run_id} | Total Steps: {run_result.total_tests} | "
        f"Passed: {run_result.passed_tests} | Failed: {run_result.failed_tests} | "
        f"Blocked: {run_result.blocked_tests} | Errors: {run_result.error_tests} | "
        f"Skipped: {run_result.skipped_tests} | Duration: {run_result.duration_ms:.1f}ms"
    )

    if not run_result.all_passed:
        raise typer.Exit(code=EXIT_TEST_FAILURE)

    if run_result.has_warnings and strict:
        raise typer.Exit(code=EXIT_WARNINGS_STRICT)

    raise typer.Exit(code=EXIT_OK)


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
    allow_local: Annotated[
        bool,
        typer.Option(
            "--allow-local",
            help=(
                "[REMOVED] Legacy option removed for security (CWE-272). "
                "Use --allow-target <origin> instead."
            ),
        ),
    ] = False,
    allow_target: Annotated[
        list[str] | None,
        typer.Option(
            "--allow-target",
            help=(
                "Permit execution against specific target origin (e.g. "
                "'http://localhost:8080'). Can be specified multiple times."
            ),
        ),
    ] = None,
    allow_mutation: Annotated[
        bool,
        typer.Option(
            "--allow-mutation",
            help=(
                "Authorize mutating and destructive operations (POST, PUT, "
                "PATCH, DELETE) in designated non-production environments."
            ),
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help=(
                "Preview planned operations, risk classifications, masked "
                "secrets, and execution authorization without dispatching "
                "network requests or saving runs."
            ),
        ),
    ] = False,
    workflow_id: Annotated[
        str | None,
        typer.Option(
            "--workflow-id",
            help="Specific workflow ID to run when a preparation report is provided",
        ),
    ] = None,
    allow_setup_mutation: Annotated[
        bool | None,
        typer.Option(
            "--allow-setup-mutation",
            help="Authorize setup mutating operations (POST, PUT, PATCH, DELETE)",
        ),
    ] = None,
    allow_cleanup_mutation: Annotated[
        bool | None,
        typer.Option(
            "--allow-cleanup-mutation",
            help="Authorize cleanup mutations (DELETE)",
        ),
    ] = None,
    confirm_isolated_env: Annotated[
        bool,
        typer.Option(
            "--confirm-isolated-env",
            help="Explicit confirmation that target is an isolated test environment",
        ),
    ] = False,
    fixtures: Annotated[
        Path | None,
        typer.Option(
            "--fixtures",
            help="Path to static fixtures JSON/YAML file",
        ),
    ] = None,
) -> None:
    """Execute API test suites and record execution telemetry."""
    db_manager: DatabaseManager | None = None
    try:
        if allow_local:
            typer.echo(
                "Error: '--allow-local' has been removed for security (CWE-272). "
                "Specify target origin(s) explicitly via '--allow-target <origin>' "
                "(e.g., '--allow-target http://localhost:8080').",
                err=True,
            )
            raise typer.Exit(code=EXIT_TOOL_ERROR)

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

        allowed_targets_list: list[str] = []
        if allow_target:
            for item in allow_target:
                for sub in item.split(","):
                    if sub.strip():
                        allowed_targets_list.append(sub.strip())

        suite_p = Path(suite_path)
        effective_base = suite_p.parent if suite_p.is_absolute() else Path.cwd()
        safe_suite_path = validate_safe_path(
            base_dir=effective_base, ref_path=suite_path
        )

        is_wf = (workflow_id is not None) or _is_workflow_file(safe_suite_path)
        if is_wf:
            _execute_workflow_entry(
                workflow_path=safe_suite_path,
                workflow_id=workflow_id,
                environment=environment,
                allowed_targets_list=allowed_targets_list,
                allow_mutation=allow_mutation,
                allow_setup_mutation=allow_setup_mutation,
                allow_cleanup_mutation=allow_cleanup_mutation,
                confirm_isolated_env=confirm_isolated_env,
                fixtures_path=fixtures,
                dry_run=dry_run,
                db=db,
                output=output,
                format=format,
                strict=strict,
            )
            return

        collection = _load_suite(safe_suite_path)

        policy = TargetAccessPolicy(
            allow_private_networks=False,
            allowed_targets=allowed_targets_list if allowed_targets_list else None,
        )
        executor = RequestExecutor(policy=policy)
        run_service = RunService(executor=executor)

        if dry_run:
            dry_report = run_service.dry_run(
                collection=collection,
                environment=environment,
                module=module,
                tags=tag,
                include_proposed=include_proposed,
                allow_mutation=allow_mutation,
            )
            typer.echo(f"=== DRY-RUN PLAN: {dry_report.suite_name} ===")
            if dry_report.environment_name:
                typer.echo(f"Target Environment: {dry_report.environment_name}")
            typer.echo(
                f"Planned Operations: {dry_report.total_operations} | "
                f"Executable: {dry_report.executable_count} | "
                f"Blocked: {dry_report.blocked_count}"
            )
            typer.echo(
                f"Risk Breakdown: {dry_report.read_only_count} READ_ONLY, "
                f"{dry_report.mutating_count} MUTATING, "
                f"{dry_report.destructive_count} DESTRUCTIVE, "
                f"{dry_report.unknown_count} UNKNOWN"
            )
            typer.echo("-" * 80)
            for op in dry_report.operations:
                plan_tag = (
                    "[PERMITTED]"
                    if op.will_execute
                    else f"[BLOCKED: {op.block_reason}]"
                )
                typer.echo(
                    f"{op.method:<6} {op.url:<40} | Risk: {op.risk:<11} | "
                    f"Status: {op.status:<8} | {plan_tag}"
                )
            typer.echo("-" * 80)
            if dry_report.missing_variables:
                typer.echo(
                    f"Missing Variables: {', '.join(dry_report.missing_variables)}"
                )
            return

        run_result = run_service.run_collection(
            collection=collection,
            environment=environment,
            module=module,
            tags=tag,
            data_file=data_file,
            include_proposed=include_proposed,
            allow_mutation=allow_mutation,
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


@app.command(name="inspect-target")
def inspect_target(
    spec: Annotated[
        Path,
        typer.Option(
            "--spec",
            "-s",
            help="Path to OpenAPI specification (YAML or JSON)",
        ),
    ],
    repo: Annotated[
        Path | None,
        typer.Option(
            "--repo",
            "-r",
            help="Optional path to local source repository root",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Optional output file path for intelligence report (YAML or JSON)",
        ),
    ] = None,
    ai_enrich: Annotated[
        bool,
        typer.Option(
            "--ai-enrich",
            help="Enable optional LLM-assisted enrichment hypotheses",
        ),
    ] = False,
    provider: Annotated[
        str,
        typer.Option(
            "--provider",
            help="LLM provider: gemini or openai",
        ),
    ] = "gemini",
    api_key: Annotated[
        str | None,
        typer.Option(
            "--api-key",
            help="Optional API key for LLM provider",
        ),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            "-m",
            help="Optional LLM model name",
        ),
    ] = None,
) -> None:
    """Analyze and produce structured Project Intelligence Report for a target API."""
    try:
        service = ProjectIntelligenceService()
        report = service.analyze_target(
            spec_path=spec,
            repo_path=repo,
            enable_ai=ai_enrich,
            provider_type=provider,
            api_key=api_key,
            model_name=model,
        )

        typer.echo(
            f"=== Project Intelligence: {report.project.name} "
            f"(v{report.project.version}) ==="
        )
        typer.echo(f"Language / Framework: {report.project.source_language}")
        typer.echo(f"Discovered Endpoints: {len(report.endpoints)}")

        # Module summary
        typer.echo("\n--- Modules ---")
        for m in report.modules:
            src_info = (
                f" (Controller: {m.source_controller})" if m.source_controller else ""
            )
            typer.echo(
                f"  * {m.name}: {m.endpoints_count} endpoints "
                f"[{m.status.value}]{src_info}"
            )

        # Entities summary
        if report.entities:
            typer.echo(f"\n--- Domain Entities ({len(report.entities)}) ---")
            for ent in report.entities[:10]:
                props_str = (
                    f" ({len(ent.properties)} properties)" if ent.properties else ""
                )
                typer.echo(f"  * {ent.name} [{ent.status.value}]{props_str}")
            if len(report.entities) > 10:
                typer.echo(f"  ... and {len(report.entities) - 10} more entities")

        # Auth & Roles
        typer.echo("\n--- Authentication & Roles ---")
        typer.echo(f"Auth Type: {report.auth.auth_type} [{report.auth.status.value}]")
        if report.auth.roles:
            typer.echo(f"Roles: {', '.join(report.auth.roles)}")

        # Workflows & Business Rules
        if report.workflows:
            typer.echo(f"\n--- Discovered Workflows ({len(report.workflows)}) ---")
            for wf in report.workflows:
                typer.echo(f"  * {wf.name} ({len(wf.steps)} steps) [{wf.status.value}]")

        if report.business_rules:
            typer.echo(f"\n--- Business Rules ({len(report.business_rules)}) ---")
            for br in report.business_rules[:5]:
                typer.echo(f"  * [{br.rule_type}] {br.description} [{br.status.value}]")
            if len(report.business_rules) > 5:
                typer.echo(f"  ... and {len(report.business_rules) - 5} more rules")

        # Risk Areas
        if report.risk_areas:
            typer.echo(f"\n--- Risk Areas ({len(report.risk_areas)}) ---")
            for r in report.risk_areas:
                typer.echo(f"  ! [{r.severity}] {r.category}: {r.description}")

        # Missing Information
        if report.missing_information:
            typer.echo(
                f"\n--- Missing Information & Ambiguities "
                f"({len(report.missing_information)}) ---"
            )
            for mi in report.missing_information:
                typer.echo(f"  ? [{mi.category}] {mi.description}")

        # Output file if requested
        if output is not None:
            service.save_report(report, output)
            typer.echo(f"\nIntelligence report saved to {output}")

        raise typer.Exit(code=EXIT_OK)

    except typer.Exit:
        raise
    except ApiScopeError as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err
    except Exception as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err


@app.command(name="analyze-contract")
def analyze_contract(
    spec: Annotated[
        Path,
        typer.Option(
            "--spec",
            "-s",
            help="Path to OpenAPI specification (YAML or JSON)",
        ),
    ],
    repo: Annotated[
        Path | None,
        typer.Option(
            "--repo",
            "-r",
            help="Optional path to local source repository for route reconciliation",
        ),
    ] = None,
    intelligence: Annotated[
        Path | None,
        typer.Option(
            "--intelligence",
            "-i",
            help="Optional path to Project Intelligence report",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Optional output file path for contract report (YAML or JSON)",
        ),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Return exit code 2 if contract quality has HIGH severity findings",
        ),
    ] = False,
) -> None:
    """Analyze API contract, audit specification quality, and reconcile with source."""
    try:
        service = ContractAnalysisService()
        report = service.analyze_contract(
            spec_path=spec,
            repo_path=repo,
            intelligence_path=intelligence,
        )

        typer.echo(
            f"=== API Contract Quality Audit: {report.title} (v{report.version}) ==="
        )
        typer.echo(f"Total Operations: {report.total_operations}")
        methods_str = ", ".join(
            f"{m}: {c}" for m, c in sorted(report.operations_by_method.items())
        )
        typer.echo(f"Operations by Method: {methods_str}")
        typer.echo(f"Functional Modules: {report.modules_count}")
        typer.echo(f"Declared Component Schemas: {report.component_schemas_count}")

        # Readiness
        r_str = ", ".join(f"{k}: {v}" for k, v in report.readiness_summary.items())
        typer.echo(
            f"\nTest Generation Readiness: [{r_str}] => "
            f"Overall: {report.overall_readiness.value}"
        )

        # Findings Summary
        f_str = ", ".join(f"{k}: {v}" for k, v in report.findings_summary.items())
        typer.echo(f"Contract Quality Findings: [{f_str}]")

        # Top / Critical Findings
        if report.findings:
            typer.echo("\n--- Key Quality Findings ---")
            high_and_med = [
                f for f in report.findings if f.severity in ("HIGH", "MEDIUM")
            ]
            display_findings = (
                high_and_med[:10] if high_and_med else report.findings[:8]
            )
            for f in display_findings:
                typer.echo(f"  ! [{f.severity}] {f.category}: {f.description}")
            if len(high_and_med) > 10:
                typer.echo(f"  ... and {len(high_and_med) - 10} more findings")

        # Source Reconciliation Summary
        if report.source_reconciliation:
            sr = report.source_reconciliation
            typer.echo("\n--- Source Reconciliation ---")
            typer.echo(
                f"Primary Source Language: {sr.get('primary_language', 'unknown')}"
            )
            typer.echo(
                f"Target Service Routes: {sr.get('target_service_routes_count', 0)}"
            )
            typer.echo(f"Matched Operations: {sr.get('matched_operations_count', 0)}")
            typer.echo(
                f"Specification-Only Routes: {sr.get('spec_only_operations_count', 0)}"
            )
            typer.echo(f"Source-Only Routes: {sr.get('source_only_routes_count', 0)}")
            if sr.get("isolated_microservice_routes_count", 0) > 0:
                typer.echo(
                    f"Isolated Microservice Routes (Non-target service): "
                    f"{sr.get('isolated_microservice_routes_count')}"
                )

        # Schema Reconciliation
        if report.schema_reconciliation:
            scr = report.schema_reconciliation
            if (
                "inferred_structural_entities" in scr
                and scr["inferred_structural_entities"]
            ):
                typer.echo("\n--- Schema Reconciliation ---")
                d_count = scr.get("declared_component_schemas_count", 0)
                typer.echo(f"Declared Component Schemas: {d_count}")
                typer.echo(
                    f"Intelligence Inferred Structural Entities: "
                    f"{len(scr['inferred_structural_entities'])} "
                    f"({', '.join(scr['inferred_structural_entities'])})"
                )

        # Output persistence
        if output is not None:
            service.save_report(report, output)
            typer.echo(f"\nContract Quality Report saved to {output}")

        # Exit code determination
        has_high_findings = report.findings_summary.get("HIGH", 0) > 0
        if strict and has_high_findings:
            raise typer.Exit(code=EXIT_WARNINGS_STRICT)

        raise typer.Exit(code=EXIT_OK)

    except typer.Exit:
        raise
    except ApiScopeError as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err


@app.command("plan-tests")
def plan_tests(
    spec: Annotated[
        Path | None,
        typer.Option(
            "--spec",
            "-s",
            help="Path to OpenAPI specification (YAML or JSON)",
        ),
    ] = None,
    contract: Annotated[
        Path | None,
        typer.Option(
            "--contract",
            "-c",
            help="Optional path to pre-computed Contract Quality Report (YAML or JSON)",
        ),
    ] = None,
    intelligence: Annotated[
        Path | None,
        typer.Option(
            "--intelligence",
            "-i",
            help="Optional path to Project Intelligence report",
        ),
    ] = None,
    repo: Annotated[
        Path | None,
        typer.Option(
            "--repo",
            "-r",
            help="Optional path to local source repository",
        ),
    ] = None,
    ai: Annotated[
        bool,
        typer.Option(
            "--ai",
            help="Enable budget-aware Gemini reasoning for domain workflows",
        ),
    ] = False,
    provider: Annotated[
        str,
        typer.Option(
            "--provider",
            help="AI provider ('gemini' or 'openai')",
        ),
    ] = "gemini",
    api_key: Annotated[
        str | None,
        typer.Option(
            "--api-key",
            help="Optional provider API key (defaults to GEMINI_API_KEY from .env)",
        ),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            help="Optional model name override",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Optional output path for generated TestPlan (YAML or JSON)",
        ),
    ] = None,
) -> None:
    """Generate comprehensive, evidence-backed test plan.

    Supports deterministic generation with optional Gemini reasoning.
    """
    try:
        if spec is None and contract is None:
            typer.echo(
                "Error: Either --spec or --contract must be specified.", err=True
            )
            raise typer.Exit(code=EXIT_TOOL_ERROR)

        service = TestPlanningService()
        plan = service.plan_tests(
            spec_path=spec,
            contract_path=contract,
            intelligence_path=intelligence,
            repo_path=repo,
            enable_ai=ai,
            provider_type=provider,
            api_key=api_key,
            model_name=model,
        )

        typer.echo(f"=== API Test Plan: {plan.title} (v{plan.version}) ===")
        typer.echo(f"Total Proposed Scenarios: {len(plan.scenarios)}")

        # Breakdown by origin
        orig_counts = plan.summary.get("scenarios_by_origin", {})
        det_count = orig_counts.get("DETERMINISTIC", 0)
        ai_count = orig_counts.get("AI_PROPOSED", 0)
        typer.echo(f"  - Deterministic Contract Baseline: {det_count}")
        typer.echo(f"  - AI-Proposed Domain Scenarios: {ai_count}")

        # Approval breakdown
        appr_counts = plan.summary.get("approval_summary", {})
        typer.echo(
            f"Approval Status: [Approved: {appr_counts.get('approved', 0)}, "
            f"Proposed/Review-Required: {appr_counts.get('proposed', 0)}]"
        )

        # Category breakdown
        cat_counts = plan.summary.get("scenarios_by_category", {})
        cat_str = ", ".join(f"{k}: {v}" for k, v in sorted(cat_counts.items()))
        typer.echo(f"\nScenarios by Category:\n  {cat_str}")

        # Priority breakdown
        prio_counts = plan.summary.get("scenarios_by_priority", {})
        prio_str = ", ".join(f"{k}: {v}" for k, v in sorted(prio_counts.items()))
        typer.echo(f"Scenarios by Priority:\n  {prio_str}")

        # Unmet prerequisites
        if plan.missing_prerequisites:
            typer.echo(
                f"\nUnmet Prerequisites & Required Fixtures: "
                f"{len(plan.missing_prerequisites)}"
            )
            for prereq in plan.missing_prerequisites[:6]:
                typer.echo(f"  * {prereq}")
            if len(plan.missing_prerequisites) > 6:
                typer.echo(f"  ... and {len(plan.missing_prerequisites) - 6} more")

        # Untestable operations
        if plan.untestable_operations:
            typer.echo(
                f"\nUntestable Operations (Blocked by contract defects): "
                f"{len(plan.untestable_operations)}"
            )
            for u in plan.untestable_operations[:5]:
                reasons = ", ".join(u.get("blocking_reasons", []))
                typer.echo(f"  ! {u['operation']}: {reasons}")

        # AI Usage Reporting
        if ai or plan.ai_usage.status != "SKIPPED":
            typer.echo("\n--- AI Reasoning Usage ---")
            typer.echo(f"Status: {plan.ai_usage.status}")
            typer.echo(f"Calls Made: {plan.ai_usage.calls_made}")
            typer.echo(
                f"Estimated Tokens: {plan.ai_usage.total_tokens} "
                f"(prompt: {plan.ai_usage.prompt_tokens}, "
                f"completion: {plan.ai_usage.completion_tokens})"
            )
            if plan.ai_usage.details:
                typer.echo(f"Details: {plan.ai_usage.details}")

        # Top Scenarios Preview
        if plan.scenarios:
            typer.echo("\n--- Key Test Scenarios ---")
            for s in plan.scenarios[:8]:
                steps_summary = " -> ".join(
                    f"{st.method} {st.path}" for st in s.steps[:3]
                )
                if len(s.steps) > 3:
                    steps_summary += f" (+{len(s.steps) - 3} steps)"
                typer.echo(
                    f"  [{s.priority}] {s.scenario_id}: {s.title} "
                    f"({s.category}, {s.origin}) => {steps_summary}"
                )
            if len(plan.scenarios) > 8:
                typer.echo(f"  ... and {len(plan.scenarios) - 8} more scenarios")

        # Output persistence
        if output is not None:
            service.save_plan(plan, output)
            typer.echo(f"\nTest Plan saved to {output}")

        raise typer.Exit(code=EXIT_OK)

    except typer.Exit:
        raise
    except ApiScopeError as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err


@app.command("compile-plan")
def compile_plan_cmd(
    plan: Annotated[
        Path,
        typer.Option(
            "--plan",
            "-p",
            help="Path to TestPlan YAML or JSON file",
        ),
    ],
    spec: Annotated[
        Path | None,
        typer.Option(
            "--spec",
            "-s",
            help="Path to OpenAPI spec file (if not in contract or plan)",
        ),
    ] = None,
    contract: Annotated[
        Path | None,
        typer.Option(
            "--contract",
            "-c",
            help="Path to CanonicalContract YAML/JSON file",
        ),
    ] = None,
    intelligence: Annotated[
        Path | None,
        typer.Option(
            "--intelligence",
            "-i",
            help="Path to ProjectIntelligenceReport JSON/YAML file",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Output path for compiled executable native test suite (YAML)",
        ),
    ] = None,
    report: Annotated[
        Path | None,
        typer.Option(
            "--report",
            "-r",
            help="Output path for detailed compilation report (JSON)",
        ),
    ] = None,
    base_url_var: Annotated[
        str,
        typer.Option(
            "--base-url-var",
            help="Base URL placeholder (defaults to '{{base_url}}')",
        ),
    ] = "{{base_url}}",
    include_proposed: Annotated[
        bool,
        typer.Option(
            "--include-proposed",
            help="Compile AI-proposed scenarios without human approval",
        ),
    ] = False,
) -> None:
    """Compile abstract TestPlan scenarios into executable native ApiScope test suites.

    Validates oracles against contracts, analyzes fixtures, gates AI proposals,
    and assigns strict execution risk tags.
    """
    try:
        compiler = TestPlanCompiler()
        suite, compilation_report = compiler.compile_plan(
            plan=plan,
            spec_path=spec,
            contract_path=contract,
            intelligence_path=intelligence,
            base_url_var=base_url_var,
            include_proposed=include_proposed,
        )

        typer.echo(
            f"=== ApiScope Test Plan Compilation: {compilation_report.title} ==="
        )
        typer.echo(f"Total Scenarios Evaluated: {compilation_report.total_scenarios}")
        typer.echo("\nCompilation Status Breakdown:")
        typer.echo(f"  [COMPILED]     : {compilation_report.compiled_count}")
        typer.echo(f"  [NEEDS_REVIEW] : {compilation_report.needs_review_count}")
        typer.echo(f"  [NEEDS_FIXTURE]: {compilation_report.needs_fixture_count}")
        typer.echo(f"  [BLOCKED]      : {compilation_report.blocked_count}")
        typer.echo(
            f"\nTotal Executable Tests Synthesized: "
            f"{compilation_report.total_tests_generated}"
        )

        if compilation_report.unresolved_fixtures:
            total_unresolved = len(compilation_report.unresolved_fixtures)
            typer.echo(f"\nUnresolved Fixtures ({total_unresolved} total):")
            for fix in compilation_report.unresolved_fixtures[:8]:
                typer.echo(f"  * {fix}")
            if total_unresolved > 8:
                typer.echo(f"  ... and {total_unresolved - 8} more")

        if output is not None:
            compiler.save_compiled_suite(suite, output)
            typer.echo(f"\nCompiled native suite saved to: {output}")

        if report is not None:
            compiler.save_compilation_report(compilation_report, report)
            typer.echo(f"Compilation report saved to: {report}")

        raise typer.Exit(code=EXIT_OK)

    except typer.Exit:
        raise
    except ApiScopeError as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err
    except Exception as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err


@app.command("prepare-tests")
def prepare_tests_cmd(
    plan: Annotated[
        Path,
        typer.Option(
            "--plan",
            "-p",
            help="Path to TestPlan YAML or JSON file",
        ),
    ],
    spec: Annotated[
        Path | None,
        typer.Option(
            "--spec",
            "-s",
            help="Path to OpenAPI spec file (if not in contract or plan)",
        ),
    ] = None,
    contract: Annotated[
        Path | None,
        typer.Option(
            "--contract",
            "-c",
            help="Path to CanonicalContract YAML/JSON file",
        ),
    ] = None,
    intelligence: Annotated[
        Path | None,
        typer.Option(
            "--intelligence",
            "-i",
            help="Path to ProjectIntelligenceReport JSON/YAML file",
        ),
    ] = None,
    fixtures: Annotated[
        Path | None,
        typer.Option(
            "--fixtures",
            "-f",
            help="Path to static fixture definition file (YAML/JSON)",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Output path for prepared executable test suite (YAML)",
        ),
    ] = None,
    report: Annotated[
        Path | None,
        typer.Option(
            "--report",
            "-r",
            help="Output path for detailed preparation report (JSON)",
        ),
    ] = None,
    base_url_var: Annotated[
        str,
        typer.Option(
            "--base-url-var",
            help="Base URL placeholder (defaults to '{{base_url}}')",
        ),
    ] = "{{base_url}}",
    allow_mutating_setup: Annotated[
        bool,
        typer.Option(
            "--allow-mutating-setup",
            help="Allow stateful workflow setup to generate mutating operations",
        ),
    ] = False,
    include_proposed: Annotated[
        bool,
        typer.Option(
            "--include-proposed",
            help="Include AI-proposed scenarios without human approval",
        ),
    ] = False,
) -> None:
    """Prepare executable tests with fixture resolution and stateful workflow setup.

    Detects payload foreign key references, maps static fixtures, constructs
    dependency ordering graphs, and plans LIFO cleanup sequences.
    """
    try:
        service = FixtureService()
        suite, prep_report = service.prepare_tests(
            plan=plan,
            spec_path=spec,
            contract_path=contract,
            intelligence_path=intelligence,
            fixture_file=fixtures,
            base_url_var=base_url_var,
            allow_mutating_setup=allow_mutating_setup,
            include_proposed=include_proposed,
        )

        typer.echo(
            f"=== ApiScope Test Data & Fixture Preparation: {prep_report.title} ==="
        )
        typer.echo(
            f"Total Scenarios Evaluated: {prep_report.total_scenarios_evaluated}"
        )
        typer.echo("\nPreparation Status Breakdown:")
        typer.echo(f"  [FIXTURE_READY]       : {prep_report.fixture_ready_count}")
        typer.echo(f"  [UNRESOLVED_FIXTURES] : {prep_report.unresolved_fixture_count}")
        typer.echo(
            f"  [UNVERIFIED_BODY_FK]  : {len(prep_report.unverified_body_placeholders)}"
        )
        typer.echo(
            f"  [WORKFLOWS_PLANNED]   : {prep_report.stateful_workflows_planned}"
        )
        typer.echo(f"  [MUTATING_SETUP_ALLOWED]: {prep_report.mutating_setup_allowed}")
        typer.echo(
            f"\nTotal Executable Tests Synthesized: {prep_report.total_tests_generated}"
        )

        if prep_report.unverified_body_placeholders:
            typer.echo(
                f"\nUnverified Body Placeholders "
                f"({len(prep_report.unverified_body_placeholders)} detected):"
            )
            for ref in prep_report.unverified_body_placeholders[:8]:
                field_name = ref.get("field_name")
                target = ref.get("target_entity")
                val = ref.get("current_value")
                loc = ref.get("location", "body")
                typer.echo(
                    f"  * {loc} field '{field_name}' -> '{target}' (value: {val!r})"
                )
            remaining_fk = len(prep_report.unverified_body_placeholders) - 8
            if remaining_fk > 0:
                typer.echo(f"  ... and {remaining_fk} more")

        if prep_report.unresolved_fixtures:
            typer.echo(
                f"\nMissing / Unresolved Fixtures "
                f"({len(prep_report.unresolved_fixtures)} total):"
            )
            for fix in prep_report.unresolved_fixtures[:8]:
                typer.echo(f"  * {fix}")
            if len(prep_report.unresolved_fixtures) > 8:
                typer.echo(f"  ... and {len(prep_report.unresolved_fixtures) - 8} more")

        if output is not None:
            service.save_prepared_suite(suite, output)
            typer.echo(f"\nPrepared test suite saved to: {output}")

        if report is not None:
            service.save_preparation_report(prep_report, report)
            typer.echo(f"Preparation report saved to: {report}")

        raise typer.Exit(code=EXIT_OK)

    except typer.Exit:
        raise
    except ApiScopeError as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err
    except Exception as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err


@app.command("run-workflow")
def run_workflow(
    workflow_file: Annotated[
        Path,
        typer.Argument(
            help=(
                "Path to stateful workflow YAML/JSON or FixturePreparationReport file"
            ),
        ),
    ],
    workflow_id: Annotated[
        str | None,
        typer.Option(
            "--workflow-id",
            help="Specific workflow ID to run when a preparation report is provided",
        ),
    ] = None,
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="Path to YAML environment file"),
    ] = None,
    env: Annotated[
        str | None,
        typer.Option("--env", help="Environment name to use"),
    ] = None,
    fixtures: Annotated[
        Path | None,
        typer.Option("--fixtures", help="Path to static fixtures JSON/YAML file"),
    ] = None,
    allow_target: Annotated[
        list[str] | None,
        typer.Option(
            "--allow-target",
            help=(
                "Permit execution against specific target origin (e.g. "
                "'http://localhost:8080'). Can be specified multiple times."
            ),
        ),
    ] = None,
    allow_mutation: Annotated[
        bool,
        typer.Option(
            "--allow-mutation",
            help=(
                "Authorize mutating and destructive operations (POST, PUT, "
                "PATCH, DELETE) in designated non-production environments."
            ),
        ),
    ] = False,
    allow_setup_mutation: Annotated[
        bool | None,
        typer.Option(
            "--allow-setup-mutation",
            help="Authorize setup mutating operations (POST, PUT, PATCH, DELETE)",
        ),
    ] = None,
    allow_cleanup_mutation: Annotated[
        bool | None,
        typer.Option(
            "--allow-cleanup-mutation",
            help="Authorize cleanup mutations (DELETE)",
        ),
    ] = None,
    confirm_isolated_env: Annotated[
        bool,
        typer.Option(
            "--confirm-isolated-env",
            help="Explicit confirmation that target is an isolated test environment",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help=(
                "Preview planned workflow steps, risk classifications, masked "
                "secrets, and execution eligibility without network requests."
            ),
        ),
    ] = False,
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
        typer.Option("--strict", help="Treat cleanup warnings as exit code 2"),
    ] = False,
) -> None:
    """Execute policy-controlled stateful workflows with setup and LIFO cleanup."""
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

        allowed_targets_list: list[str] = []
        if allow_target:
            for item in allow_target:
                for sub in item.split(","):
                    if sub.strip():
                        allowed_targets_list.append(sub.strip())

        wf_p = Path(workflow_file)
        effective_base = wf_p.parent if wf_p.is_absolute() else Path.cwd()
        safe_wf_path = validate_safe_path(
            base_dir=effective_base,
            ref_path=workflow_file,
        )

        _execute_workflow_entry(
            workflow_path=safe_wf_path,
            workflow_id=workflow_id,
            environment=environment,
            allowed_targets_list=allowed_targets_list,
            allow_mutation=allow_mutation,
            allow_setup_mutation=allow_setup_mutation,
            allow_cleanup_mutation=allow_cleanup_mutation,
            confirm_isolated_env=confirm_isolated_env,
            fixtures_path=fixtures,
            dry_run=dry_run,
            db=db,
            output=output,
            format=format,
            strict=strict,
        )
    except typer.Exit:
        raise
    except ApiScopeError as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err
    except Exception as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err


def _print_investigation_report(report: FailureInvestigationReport) -> None:
    """Print readable CLI summary table of failure investigation findings."""
    typer.echo("\n=== ApiScope Root-Cause Failure Investigation ===")
    typer.echo(f"Run ID:                  {report.run_id}")
    typer.echo(f"Evaluated Cases:         {report.total_cases_evaluated}")
    typer.echo(f"Failures / Blocks:       {report.total_failures_detected}")
    typer.echo(f"Primary Failures:        {report.primary_failures_count}")
    typer.echo(f"Downstream Consequences: {report.downstream_consequences_count}")

    if report.total_failures_detected == 0:
        typer.echo(
            "\n[Summary] All cases executed cleanly with no failures or policy blocks."
        )
        return

    typer.echo("\n[Category Breakdown]")
    for cat, count in sorted(report.category_counts.items()):
        typer.echo(f"  * {cat}: {count}")

    primary_findings = [f for f in report.findings if f.is_primary_failure]
    if primary_findings:
        typer.echo("\n[Primary Failures & Root-Cause Hypotheses]")
        for f in primary_findings:
            phase_str = f"[{f.execution_phase.value}]"
            code_str = f"HTTP {f.status_code}" if f.status_code else "No Response"
            typer.echo(
                f"\n* {f.finding_id} {phase_str} {f.method} {f.url} -> {code_str}"
            )
            typer.echo(f"  Symptom:     {f.observed_symptom}")
            typer.echo(
                f"  Category:    {f.category.value} | Confidence: {f.confidence.value} | Severity: {f.severity.value}"
            )
            typer.echo(f"  Disposition: {f.disposition.value}")
            if f.candidate_causes:
                typer.echo("  Candidate Causes:")
                for c in f.candidate_causes:
                    typer.echo(f"    - {c}")
            if f.recommended_next_step:
                typer.echo(f"  Recommendation: {f.recommended_next_step}")
            if f.downstream_impact_count > 0:
                typer.echo(
                    f"  Cascade Impact: {f.downstream_impact_count} downstream step(s) skipped/blocked"
                )
            if f.ai_hypothesis:
                typer.echo(f"  AI Hypothesis:  {f.ai_hypothesis}")

    downstream_findings = [f for f in report.findings if not f.is_primary_failure]
    if downstream_findings:
        typer.echo("\n[Downstream Cascades (Caused by Upstream Failures)]")
        for f in downstream_findings:
            typer.echo(
                f"  - {f.finding_id} [{f.execution_phase.value}] {f.method} {f.url} "
                f"(Caused by {f.primary_finding_id} -> {f.observed_symptom})"
            )

    if report.missing_evidence_gaps:
        typer.echo("\n[Evidence Gaps & Uncertainties]")
        for gap in report.missing_evidence_gaps:
            typer.echo(f"  ? {gap}")


@app.command("investigate")
def investigate(
    run_id: Annotated[
        str | None,
        typer.Option(
            "--run-id",
            "-r",
            help="Execution run ID to investigate from SQLite history database",
        ),
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option(
            "--file",
            "-f",
            help="Path to recorded test run or workflow execution JSON report file",
        ),
    ] = None,
    spec: Annotated[
        Path | None,
        typer.Option(
            "--spec",
            "-s",
            help="Optional path to OpenAPI specification for contract reconciliation",
        ),
    ] = None,
    plan: Annotated[
        Path | None,
        typer.Option(
            "--plan",
            "-p",
            help="Optional path to test plan / compiler report for scenario provenance",
        ),
    ] = None,
    fixtures: Annotated[
        Path | None,
        typer.Option(
            "--fixtures",
            help="Optional path to fixture preparation report JSON/YAML file",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Optional path to save structured investigation report (JSON/YAML)",
        ),
    ] = None,
    format: Annotated[
        str,
        typer.Option(
            "--format",
            help="Output serialization format if --output is set ('json' or 'yaml')",
        ),
    ] = "json",
    with_ai: Annotated[
        bool,
        typer.Option(
            "--with-ai",
            help="Opt-in to Gemini AI-assisted root-cause diagnostic hypotheses",
        ),
    ] = False,
    provider: Annotated[
        str,
        typer.Option(
            "--provider",
            help="LLM provider for AI reasoning ('gemini' or 'openai')",
        ),
    ] = "gemini",
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            help="Custom model name for AI reasoning",
        ),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option(
            "--api-key",
            help="API key for LLM provider (or via GEMINI_API_KEY / OPENAI_API_KEY)",
        ),
    ] = None,
    db: Annotated[
        Path,
        typer.Option(
            "--db",
            help="Path to SQLite history database",
        ),
    ] = Path(".apiscope/history.db"),
) -> None:
    """Investigate recorded test failures with deterministic triage and evidence attribution."""
    try:
        if not run_id and not file:
            typer.echo(
                "Error: Please provide either --run-id <RUN_ID> or --file <PATH>",
                err=True,
            )
            raise typer.Exit(code=EXIT_TOOL_ERROR)

        db_manager = DatabaseManager(db)
        inv_service = FailureInvestigationService(db_manager=db_manager)

        run_result: RunResult | None = None

        if run_id:
            repo = RunRepository(db_manager)
            run_result = repo.get_run(run_id)
            if run_result is None:
                typer.echo(
                    f"Error: Run '{run_id}' not found in SQLite database '{db}'",
                    err=True,
                )
                raise typer.Exit(code=EXIT_TOOL_ERROR)
        elif file:
            p_file = Path(file)
            effective_base = p_file.parent if p_file.is_absolute() else Path.cwd()
            safe_file = validate_safe_path(base_dir=effective_base, ref_path=file)
            run_result = inv_service.load_run_from_file(safe_file)

        if run_result is None:
            typer.echo("Error: Unable to load execution run result", err=True)
            raise typer.Exit(code=EXIT_TOOL_ERROR)

        # Optional contract reconciliation
        contract = None
        if spec is not None:
            spec_p = Path(spec)
            effective_base = spec_p.parent if spec_p.is_absolute() else Path.cwd()
            safe_spec = validate_safe_path(base_dir=effective_base, ref_path=spec)
            contract_service = ContractAnalysisService()
            contract = contract_service.analyze_contract(spec_path=safe_spec)

        # Optional compiler report
        compiler_report = None
        if plan is not None:
            plan_p = Path(plan)
            effective_base = plan_p.parent if plan_p.is_absolute() else Path.cwd()
            safe_plan = validate_safe_path(base_dir=effective_base, ref_path=plan)
            with contextlib.suppress(Exception):
                content = safe_plan.read_text(encoding="utf-8")
                compiler_report = json.loads(content)

        # Optional fixture report
        fixture_report = None
        if fixtures is not None:
            fix_p = Path(fixtures)
            effective_base = fix_p.parent if fix_p.is_absolute() else Path.cwd()
            safe_fix = validate_safe_path(base_dir=effective_base, ref_path=fixtures)
            with contextlib.suppress(Exception):
                content = safe_fix.read_text(encoding="utf-8")
                fix_data = json.loads(content)
                fixture_report = FixturePreparationReport.from_dict(fix_data)

        # Optional AI configuration
        llm_config = None
        if with_ai:
            llm_config = LLMConfig(
                provider=provider,
                model=model or "",
                api_key=api_key or "",
            )

        report = inv_service.investigate_run(
            run_result=run_result,
            contract=contract,
            compiler_report=compiler_report,
            fixture_report=fixture_report,
            with_ai=with_ai,
            llm_config=llm_config,
        )

        _print_investigation_report(report)

        if output is not None:
            out_p = Path(output)
            effective_base = out_p.parent if out_p.is_absolute() else Path.cwd()
            safe_out = validate_safe_path(base_dir=effective_base, ref_path=output)
            inv_service.export_report(report, safe_out, format=format)
            typer.echo(f"\nInvestigation report written to: {safe_out}")

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
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          f"  * [{f.verification_state.value}] [{f.signal_type.value}] {f.module}: {f.description}"
            )
            typer.echo(f"      Evidence: {f.evidence}")
            if f.suggested_action:
                typer.echo(f"      Action:   {f.suggested_action}")


def _print_latency_report(report: LatencyDriftReport) -> None:
    """Print structured latency drift report to console."""
    typer.echo("\n" + "=" * 60)
    typer.echo("  LATENCY DRIFT DIAGNOSTIC REPORT")
    typer.echo("=" * 60)
    typer.echo(f"Summary: {report.summary}")
    typer.echo(
        f"Regressions: {report.regressions_count} | Stable: {report.stable_count} | "
        f"Improved: {report.improved_count} | Insufficient Data: {report.insufficient_data_count} | "
        f"Incompatible Context: {report.incompatible_context_count}"
    )

    if report.comparisons:
        typer.echo("\n[Operation Comparisons]")
        for comp in report.comparisons:
            p95_str = (
                f" (p95: {comp.baseline_p95_ms} -> {comp.current_p95_ms} ms)"
                if comp.current_p95_ms is not None and comp.baseline_p95_ms is not None
                else ""
            )
            typer.echo(
                f"  * [{comp.verdict.value}] {comp.operation}: "
                f"med {comp.baseline_median_ms:.1f} -> {comp.current_median_ms:.1f} ms "
                f"({comp.abs_change_ms:+} ms, {comp.rel_change_pct:+}%)"
                f"{p95_str}"
            )
            typer.echo(f"      Details: {comp.details}")


@app.command("diagnose-silent")
def diagnose_silent(
    run_id: Annotated[
        str | None,
        typer.Option(
            "--run-id",
            "-r",
            help="Execution run ID to diagnose from SQLite history database",
        ),
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option(
            "--file",
            "-f",
            help="Path to recorded test run JSON report file",
        ),
    ] = None,
    workflow: Annotated[
        Path | None,
        typer.Option(
            "--workflow",
            "-w",
            help="Path to executed workflow result JSON file",
        ),
    ] = None,
    app_log: Annotated[
        Path | None,
        typer.Option(
            "--app-log",
            "-l",
            help="Path to application log file for S1 error correlation",
        ),
    ] = None,
    health_file: Annotated[
        Path | None,
        typer.Option(
            "--health-file",
            help="Path to health probe JSON file for S2 dependency check",
        ),
    ] = None,
    baseline_run: Annotated[
        Path | None,
        typer.Option(
            "--baseline-run",
            "-b",
            help="Path to historical baseline test run JSON for S3 latency drift",
        ),
    ] = None,
    spec: Annotated[
        Path | None,
        typer.Option(
            "--spec",
            "-s",
            help="Optional path to OpenAPI spec for S6 contract reconciliation",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Optional path to save structured silent failure report",
        ),
    ] = None,
    format: Annotated[
        str,
        typer.Option(
            "--format",
            help="Output format: 'json' or 'yaml'",
        ),
    ] = "json",
    db: Annotated[
        Path,
        typer.Option(
            "--db",
            help="Path to SQLite history database",
        ),
    ] = Path(".apiscope/history.db"),
) -> None:
    """Diagnose silent failures, swallowed errors, and unverified state effects."""
    try:
        run_res: RunResult | None = None
        db_manager = DatabaseManager(db)

        if run_id:
            repo = RunRepository(db_manager)
            run_res = repo.get_run(run_id)
            if run_res is None:
                typer.echo(
                    f"Error: Run '{run_id}' not found in SQLite database '{db}'",
                    err=True,
                )
                raise typer.Exit(code=EXIT_TOOL_ERROR)
        elif file:
            p_file = Path(file)
            effective_base = p_file.parent if p_file.is_absolute() else Path.cwd()
            safe_file = validate_safe_path(base_dir=effective_base, ref_path=file)
            run_res = load_run_file(safe_file)
        elif workflow:
            p_wf = Path(workflow)
            effective_base = p_wf.parent if p_wf.is_absolute() else Path.cwd()
            safe_wf = validate_safe_path(base_dir=effective_base, ref_path=workflow)
            run_res = load_run_file(safe_wf)

        app_entries = None
        if app_log:
            p_log = Path(app_log)
            effective_base = p_log.parent if p_log.is_absolute() else Path.cwd()
            safe_log = validate_safe_path(base_dir=effective_base, ref_path=app_log)
            app_entries = load_app_log(safe_log)

        health_payload = None
        if health_file:
            p_h = Path(health_file)
            effective_base = p_h.parent if p_h.is_absolute() else Path.cwd()
            safe_h = validate_safe_path(base_dir=effective_base, ref_path=health_file)
            content = safe_h.read_text(encoding="utf-8")
            health_payload = json.loads(content)

        base_res = None
        if baseline_run:
            p_b = Path(baseline_run)
            effective_base = p_b.parent if p_b.is_absolute() else Path.cwd()
            safe_b = validate_safe_path(base_dir=effective_base, ref_path=baseline_run)
            base_res = load_run_file(safe_b)

        contract = None
        if spec:
            p_s = Path(spec)
            effective_base = p_s.parent if p_s.is_absolute() else Path.cwd()
            safe_s = validate_safe_path(base_dir=effective_base, ref_path=spec)
            contract_service = ContractAnalysisService()
            contract = contract_service.analyze_contract(spec_path=safe_s)

        service = SilentFailureService()
        report = service.analyze(
            run_result=run_res,
            app_log_entries=app_entries,
            health_probe_payload=health_payload,
            baseline_run=base_res,
            contract=contract,
        )

        _print_silent_report(report)

        if output is not None:
            out_p = Path(output)
            effective_base = out_p.parent if out_p.is_absolute() else Path.cwd()
            safe_out = validate_safe_path(base_dir=effective_base, ref_path=output)
            if format.lower() == "yaml":
                safe_out.write_text(report.to_yaml(), encoding="utf-8")
            else:
                safe_out.write_text(report.to_json(indent=2), encoding="utf-8")
            typer.echo(f"\nSilent failure report written to: {safe_out}")

    except typer.Exit:
        raise
    except ApiScopeError as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err
    except Exception as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(code=EXIT_TOOL_ERROR) from err


@app.command("latency-drift")
def latency_drift(
    baseline: Annotated[
        str,
        typer.Option(
            "--baseline",
            "-b",
            help="Baseline run ID from database or path to baseline run JSON file",
        ),
    ],
    current: Annotated[
        str,
        typer.Option(
            "--current",
            "-c",
            help="Current run ID from database or path to current run JSON file",
        ),
    ],
    min_samples: Annotated[
        int,
        typer.Option(
            "--min-samples",
            help="Minimum sample count per operation to evaluate drift (default: 5)",
        ),
    ] = 5,
    tolerance_pct: Annotated[
        float,
        typer.Option(
            "--tolerance-pct",
            help="Relative drift tolerance percentage before flagging regression (default: 40.0%)",
        ),
    ] = 40.0,
    min_abs_ms: Annotated[
        float,
        typer.Option(
            "--min-abs-ms",
            help="Minimum absolute latency increase in ms to flag regression (default: 20.0 ms)",
        ),
    ] = 20.0,
    strict_env: Annotated[
        bool,
        typer.Option(
            "--strict-env/--allow-cross-env",
            help="Reject cross-environment latency comparisons (default: strict)",
        ),
    ] = True,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Optional path to save structured latency drift report",
        ),
    ] = None,
    format: Annotated[
        str,
        typer.Option(
            "--format",
            help="Output serialization format ('json' or 'yaml')",
        ),
    ] = "json",
    db: Annotated[
        Path,
        typer.Option(
            "--db",
            help="Path to SQLite history database",
        ),
    ] = Path(".apiscope/history.db"),
) -> None:
    """Analyze latency drift and statistical response time regressions between test runs."""
    try:
        db_manager = DatabaseManager(db)
        repo = RunRepository(db_manager)

        def _resolve_run(identifier: str) -> RunResult:
            p = Path(identifier)
            if p.is_file():
                effective_base = p.parent if p.is_absolute() else Path.cwd()
                safe_p = validate_safe_path(
                    base_dir=effective_base, ref_path=identifier
                )
                return load_run_file(safe_p)
            # Try database lookup
            run_res = repo.get_run(identifier)
            if run_res is not None:
                return run_res
            raise ConfigurationError(
                f"Could not resolve test run from file or database ID '{identifier}'"
            )

        baseline_res = _resolve_run(baseline)
        current_res = _resolve_run(current)

        tolerance_factor = 1.0 + (tolerance_pct / 100.0)
        analyzer = LatencyDriftAnalyzer(
            min_samples_threshold=min_samples,
            tolerance_factor=tolerance_factor,
            min_abs_drift_ms=min_abs_ms,
            strict_environment_match=strict_env,
        )

        report = analyzer.compare_runs(
            baseline_run=baseline_res, current_run=current_res
        )

        _print_latency_report(report)

        if output is not None:
            out_p = Path(output)
            effective_base = out_p.parent if out_p.is_absolute() else Path.cwd()
            safe_out = validate_safe_path(base_dir=effective_base, ref_path=output)
            if format.lower() == "yaml":
                safe_out.write_text(report.to_yaml(), encoding="utf-8")
            else:
                safe_out.write_text(report.to_json(indent=2), encoding="utf-8")
            typer.echo(f"\nLatency drift report written to: {safe_out}")

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
