"""Workflow execution orchestrator and lifecycle coordinator (Task 21).

Coordinates policy-controlled execution of prepared stateful workflows, enforcing
exact-origin authorization, mutation gating, pre-execution prerequisite checks,
response ID extraction, assertion evaluation, reverse-order LIFO cleanup, and
sanitized evidence capture.
"""

from __future__ import annotations

import contextlib
import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from apiscope.core.assertions.engine import AssertionEngine
from apiscope.core.contracts import (
    AssertionEngineProtocol,
    ExtractorProtocol,
    RequestExecutorProtocol,
    VariableResolverProtocol,
)
from apiscope.core.exceptions import DefinitionError
from apiscope.core.executor import RequestExecutor, _sanitize_url
from apiscope.core.extractor import Extractor
from apiscope.core.logging_config import get_logger, redact_sensitive_text
from apiscope.core.models import HttpMethod, HttpRequest
from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.core.safety import (
    PRODUCTION_ENVIRONMENTS,
    ExecutionSafetyPolicy,
)
from apiscope.core.variables import VariableResolver
from apiscope.definitions.environment import Environment
from apiscope.execution.models import (
    CreatedResource,
    WorkflowDryRunReport,
    WorkflowExecutionResult,
    WorkflowStepResult,
    WorkflowStepType,
)
from apiscope.fixtures.models import StatefulWorkflow, WorkflowAction

_VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")
_SECRET_KEYWORDS = (
    "token",
    "secret",
    "password",
    "api_key",
    "apikey",
    "authorization",
    "passwd",
    "credential",
)


def _is_secret_key(key: str) -> bool:
    """Check if a variable name implies sensitive credential data."""
    k_lower = key.lower()
    return any(term in k_lower for term in _SECRET_KEYWORDS)


def _find_referenced_variables(data: Any) -> set[str]:
    """Recursively discover all {{var_name}} references in data structures."""
    found: set[str] = set()
    if isinstance(data, str):
        for match in _VARIABLE_PATTERN.finditer(data):
            found.add(match.group(1))
    elif isinstance(data, dict):
        for k, v in data.items():
            if isinstance(k, str):
                found |= _find_referenced_variables(k)
            found |= _find_referenced_variables(v)
    elif isinstance(data, (list, tuple)):
        for item in data:
            found |= _find_referenced_variables(item)
    return found


class WorkflowExecutionService:
    """Orchestrates policy-controlled autonomous execution of stateful workflows."""

    __test__ = False

    def __init__(
        self,
        executor: RequestExecutorProtocol | None = None,
        resolver: VariableResolverProtocol | None = None,
        assertion_engine: AssertionEngineProtocol | None = None,
        extractor: ExtractorProtocol | None = None,
        safety_policy: ExecutionSafetyPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        id_generator: Callable[[], str] | None = None,
        correlation_id_generator: Callable[[], str] | None = None,
    ) -> None:
        """Initialize WorkflowExecutionService with pluggable dependencies."""
        self._executor = executor or RequestExecutor()
        self._resolver = resolver or VariableResolver()
        self._assertion_engine = assertion_engine or AssertionEngine()
        self._extractor = extractor or Extractor()
        self.safety_policy = safety_policy or ExecutionSafetyPolicy.read_only()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_generator = id_generator or (lambda: str(uuid.uuid4()))
        self._correlation_id_generator = correlation_id_generator or (
            lambda: f"corr-wf-{uuid.uuid4().hex[:12]}"
        )
        self.logger = get_logger("apiscope.execution.service")

    def _get_secret_values(
        self,
        known_secrets: set[str],
        runtime_vars: dict[str, Any],
    ) -> list[str]:
        """Collect raw secret string values for output redaction."""
        values: list[str] = []
        for k, v in runtime_vars.items():
            if (k in known_secrets or _is_secret_key(k)) and v is not None:
                str_v = str(v).strip()
                if str_v:
                    values.append(str_v)
        values.sort(key=len, reverse=True)
        return values

    def _mask_text(self, text: str, secret_values: list[str]) -> str:
        """Mask explicit secrets and credential patterns in text."""
        if not text:
            return text
        sanitized = text
        for s in secret_values:
            if s and s in sanitized:
                sanitized = sanitized.replace(s, "***")
        return redact_sensitive_text(sanitized)

    def _mask_url(
        self,
        url: str,
        known_secrets: set[str],
        runtime_vars: dict[str, Any],
    ) -> str:
        """Sanitize URL userinfo and mask query params containing secret values."""
        clean_url = _sanitize_url(url)
        secret_values = self._get_secret_values(known_secrets, runtime_vars)
        return self._mask_text(clean_url, secret_values)

    def _mask_headers(
        self,
        headers: dict[str, str],
        known_secrets: set[str],
        runtime_vars: dict[str, Any],
    ) -> dict[str, str]:
        """Return a copy of headers with sensitive keys and secrets masked."""
        secret_values = self._get_secret_values(known_secrets, runtime_vars)
        masked: dict[str, str] = {}
        for k, v in headers.items():
            k_lower = k.lower()
            if _is_secret_key(k) or k_lower in (
                "authorization",
                "cookie",
                "set-cookie",
                "proxy-authorization",
            ):
                masked[k] = "***"
            else:
                masked[k] = self._mask_text(str(v), secret_values)
        return masked

    def load_workflows_from_file(
        self,
        file_path: str | Path,
        workflow_id: str | None = None,
    ) -> list[StatefulWorkflow]:
        """Load one or all workflows from a file or FixturePreparationReport.

        Args:
            file_path: Path to workflow file or preparation report.
            workflow_id: Optional specific workflow ID to filter by.

        Returns:
            List of StatefulWorkflow domain models.

        Raises:
            DefinitionError: If the file is missing or contains invalid workflow data.
        """
        p = Path(file_path)
        if not p.is_file():
            raise DefinitionError(f"Workflow file not found: '{file_path}'")

        content = p.read_text(encoding="utf-8")
        data = (
            yaml.safe_load(content)
            if p.suffix in (".yaml", ".yml")
            else json.loads(content)
        )

        if not isinstance(data, dict):
            raise DefinitionError(
                f"Workflow file must contain a mapping: '{file_path}'"
            )

        # 1. Direct StatefulWorkflow dictionary
        if "workflow_id" in data and ("setup_steps" in data or "test_steps" in data):
            wf = StatefulWorkflow.from_dict(data)
            if workflow_id and wf.workflow_id.lower() != workflow_id.lower():
                raise DefinitionError(
                    f"Workflow ID '{workflow_id}' does not match file workflow '{wf.workflow_id}'"
                )
            return [wf]

        # 2. FixturePreparationReport dictionary containing planned_workflows
        if "planned_workflows" in data and isinstance(data["planned_workflows"], list):
            workflows = [
                StatefulWorkflow.from_dict(w)
                for w in data["planned_workflows"]
                if isinstance(w, dict)
            ]
            if not workflows:
                raise DefinitionError(
                    f"No planned workflows found in report: '{file_path}'"
                )
            if workflow_id:
                for wf in workflows:
                    if wf.workflow_id.lower() == workflow_id.lower():
                        return [wf]
                raise DefinitionError(
                    f"Workflow ID '{workflow_id}' not found in report: '{file_path}'"
                )
            return workflows

        raise DefinitionError(
            f"File does not contain a recognized StatefulWorkflow: '{file_path}'"
        )

    def load_workflow_from_file(
        self,
        file_path: str | Path,
        workflow_id: str | None = None,
    ) -> StatefulWorkflow:
        """Load a StatefulWorkflow from a YAML, JSON, or Preparation Report file.

        Args:
            file_path: Path to workflow file or preparation report.
            workflow_id: Optional specific workflow ID to extract from a report.

        Returns:
            StatefulWorkflow domain model.

        Raises:
            DefinitionError: If the file is missing or contains invalid workflow data.
        """
        wfs = self.load_workflows_from_file(
            file_path=file_path, workflow_id=workflow_id
        )
        return wfs[0]

    def dry_run_workflow(
        self,
        workflow: StatefulWorkflow,
        environment: Environment | None = None,
        allow_mutation: bool = False,
        allow_setup_mutation: bool | None = None,
        allow_cleanup_mutation: bool | None = None,
        confirm_isolated_env: bool = False,
        static_fixtures: dict[str, Any] | None = None,
        base_url_var: str = "{{base_url}}",
    ) -> WorkflowDryRunReport:
        """Preview workflow execution and verify prerequisites without network calls.

        Performs zero network calls, zero mutations, and zero database writes.

        Args:
            workflow: StatefulWorkflow model to preview.
            environment: Optional target environment with variable bindings.
            allow_mutation: Whether general mutations are authorized.
            allow_setup_mutation: Explicit authorization for setup mutations.
            allow_cleanup_mutation: Explicit authorization for cleanup mutations.
            confirm_isolated_env: Confirmation that target is an isolated test env.
            static_fixtures: Optional pre-approved static fixture dictionary.
            base_url_var: Base URL variable placeholder.

        Returns:
            WorkflowDryRunReport containing planned step previews and eligibility.
        """
        env_name = environment.name if environment else None
        eff_setup_mut = (
            allow_setup_mutation if allow_setup_mutation is not None else allow_mutation
        )
        eff_cleanup_mut = (
            allow_cleanup_mutation
            if allow_cleanup_mutation is not None
            else allow_mutation
        )

        runtime_vars: dict[str, Any] = {}
        if environment:
            runtime_vars.update(environment.variables)
        if static_fixtures:
            runtime_vars.update(static_fixtures)

        known_secrets: set[str] = set()
        if environment:
            known_secrets.update(environment.secrets)
        for k in runtime_vars:
            if _is_secret_key(k):
                known_secrets.add(k)

        # Track variables that will be provided dynamically by setup actions
        dynamic_vars: set[str] = set(workflow.provided_fixtures)
        for s in workflow.setup_steps:
            dynamic_vars.update(s.extract.keys())

        # Collect required variables across all actions
        all_required_vars: set[str] = set()
        for a in [*workflow.setup_steps, *workflow.test_steps, *workflow.cleanup_steps]:
            all_required_vars |= _find_referenced_variables(a.url)
            all_required_vars |= _find_referenced_variables(a.headers)
            all_required_vars |= _find_referenced_variables(a.body)

        # Remove base_url_var if standard placeholder
        cleaned_required = {
            v
            for v in all_required_vars
            if v != base_url_var.strip("{}").strip() and v != "base_url"
        }

        # Missing variables are those not in runtime_vars and not provided dynamically
        missing_vars = sorted(
            [
                v
                for v in cleaned_required
                if v not in runtime_vars and v not in dynamic_vars
            ]
        )

        ineligibility_reasons: list[str] = []

        # 1. Environment and production boundary check
        if env_name and env_name.strip().lower() in PRODUCTION_ENVIRONMENTS:
            ineligibility_reasons.append(
                f"Execution blocked: environment '{env_name}' is designated as PRODUCTION."
            )

        # 2. Mutation authorizations
        has_setup_mut = any(
            s.risk in ("risk:mutating", "risk:destructive")
            or s.method.upper() not in ("GET", "HEAD", "OPTIONS")
            for s in workflow.setup_steps
        )
        has_test_mut = any(
            t.risk in ("risk:mutating", "risk:destructive")
            or t.method.upper() not in ("GET", "HEAD", "OPTIONS")
            for t in workflow.test_steps
        )
        has_cleanup_mut = any(
            c.risk in ("risk:mutating", "risk:destructive")
            or c.method.upper() not in ("GET", "HEAD", "OPTIONS")
            for c in workflow.cleanup_steps
        )

        any_mutation_needed = has_setup_mut or has_test_mut or has_cleanup_mut

        if any_mutation_needed and not confirm_isolated_env:
            ineligibility_reasons.append(
                "Mutating operations require explicit isolated test environment confirmation "
                "(--confirm-isolated-env)."
            )

        if has_setup_mut and not eff_setup_mut:
            ineligibility_reasons.append(
                "Setup operations contain mutations but setup mutations are not authorized "
                "(--allow-mutation or --allow-setup-mutation)."
            )

        if has_test_mut and not allow_mutation:
            ineligibility_reasons.append(
                "Main test operations contain mutations but mutations are not authorized "
                "(--allow-mutation)."
            )

        if has_cleanup_mut and not eff_cleanup_mut:
            ineligibility_reasons.append(
                "Cleanup operations contain destructive mutations but cleanup mutations are "
                "not authorized (--allow-mutation or --allow-cleanup-mutation)."
            )

        if missing_vars:
            ineligibility_reasons.append(
                f"Missing required input variables/fixtures: {', '.join(missing_vars)}."
            )

        if workflow.status == "BLOCKED" and workflow.blocker_reasons:
            ineligibility_reasons.extend(workflow.blocker_reasons)

        eligible = len(ineligibility_reasons) == 0

        # Construct step previews with masked secrets
        def _preview_actions(actions: list[WorkflowAction]) -> list[dict[str, Any]]:
            previews: list[dict[str, Any]] = []
            for act in actions:
                resolved_url = act.url
                with contextlib.suppress(Exception):
                    resolved_url = self._resolver.resolve_string(act.url, runtime_vars)
                previews.append(
                    {
                        "step_number": act.step_number,
                        "name": act.name,
                        "method": act.method,
                        "url": self._mask_url(
                            resolved_url, known_secrets, runtime_vars
                        ),
                        "risk": act.risk,
                        "extract": dict(act.extract),
                        "assertions": list(act.assertions),
                        "can_rollback": act.can_rollback,
                    }
                )
            return previews

        target_origin = "unknown"
        if environment and "base_url" in environment.variables:
            target_origin = str(environment.variables["base_url"])
        elif workflow.test_steps:
            target_origin = workflow.test_steps[0].url.split("/api")[0]

        return WorkflowDryRunReport(
            workflow_id=workflow.workflow_id,
            title=workflow.title,
            target_origin=self._mask_text(
                target_origin, self._get_secret_values(known_secrets, runtime_vars)
            ),
            environment_name=env_name,
            planned_setup_steps=_preview_actions(workflow.setup_steps),
            planned_test_steps=_preview_actions(workflow.test_steps),
            planned_cleanup_steps=_preview_actions(workflow.cleanup_steps),
            required_fixtures=list(workflow.required_fixtures),
            provided_fixtures=list(workflow.provided_fixtures),
            missing_variables=missing_vars,
            is_mutation_authorized=bool(allow_mutation or eff_setup_mut),
            is_isolated_env_confirmed=confirm_isolated_env,
            estimated_operations_count=(
                len(workflow.setup_steps)
                + len(workflow.test_steps)
                + len(workflow.cleanup_steps)
            ),
            eligible_to_execute=eligible,
            ineligibility_reasons=ineligibility_reasons,
        )

    def execute_workflow(
        self,
        workflow: StatefulWorkflow,
        environment: Environment | None = None,
        allow_mutation: bool = False,
        allow_setup_mutation: bool | None = None,
        allow_cleanup_mutation: bool | None = None,
        confirm_isolated_env: bool = False,
        target_origin: str | None = None,
        static_fixtures: dict[str, Any] | None = None,
        timeout_seconds: float = 60.0,
    ) -> WorkflowExecutionResult:
        """Execute a stateful workflow with setup, test execution, and safe cleanup.

        Args:
            workflow: StatefulWorkflow model to execute.
            environment: Optional environment providing base variable bindings.
            allow_mutation: Authorize mutating operations.
            allow_setup_mutation: Authorize setup mutations (defaults to allow_mutation).
            allow_cleanup_mutation: Authorize cleanup mutations (defaults to allow_mutation).
            confirm_isolated_env: Mandatory user confirmation of isolated test target.
            target_origin: Optional explicit target origin to enforce.
            static_fixtures: Optional dictionary of pre-approved test data.
            timeout_seconds: Hard ceiling for total workflow execution duration.

        Returns:
            WorkflowExecutionResult capturing all step outcomes, assertions, and cleanup.
        """
        start_time = self._clock()
        started_at_iso = start_time.isoformat()
        env_name = environment.name if environment else None

        eff_setup_mut = (
            allow_setup_mutation if allow_setup_mutation is not None else allow_mutation
        )
        eff_cleanup_mut = (
            allow_cleanup_mutation
            if allow_cleanup_mutation is not None
            else allow_mutation
        )

        # Fresh isolated runtime variable context for this workflow run
        runtime_vars: dict[str, Any] = {}
        if environment:
            runtime_vars.update(environment.variables)
        if static_fixtures:
            runtime_vars.update(static_fixtures)

        known_secrets: set[str] = set()
        if environment:
            known_secrets.update(environment.secrets)
        for k in runtime_vars:
            if _is_secret_key(k):
                known_secrets.add(k)

        # Track pre-existing keys to never cleanup resources provided by user fixtures
        pre_existing_keys = set(runtime_vars.keys())

        # Resources confirmed created and owned by THIS workflow run
        created_resources: list[CreatedResource] = []

        setup_results: list[WorkflowStepResult] = []
        test_results: list[WorkflowStepResult] = []
        cleanup_results: list[WorkflowStepResult] = []

        eff_origin = target_origin
        if not eff_origin and "base_url" in runtime_vars:
            eff_origin = str(runtime_vars["base_url"])

        # -------------------------------------------------------------
        # 1. PRE-EXECUTION SECURITY & POLICY GATES
        # -------------------------------------------------------------
        if env_name and env_name.strip().lower() in PRODUCTION_ENVIRONMENTS:
            finish_time = self._clock()
            return WorkflowExecutionResult(
                workflow_id=workflow.workflow_id,
                title=workflow.title,
                status=CaseStatus.BLOCKED,
                started_at=started_at_iso,
                finished_at=finish_time.isoformat(),
                duration_ms=(finish_time - start_time).total_seconds() * 1000.0,
                target_origin=eff_origin or "unknown",
                environment_name=env_name,
                error_message=(
                    f"Execution blocked: environment '{env_name}' is designated as PRODUCTION."
                ),
            )

        has_setup_mut = any(
            s.risk in ("risk:mutating", "risk:destructive")
            or s.method.upper() not in ("GET", "HEAD", "OPTIONS")
            for s in workflow.setup_steps
        )
        has_test_mut = any(
            t.risk in ("risk:mutating", "risk:destructive")
            or t.method.upper() not in ("GET", "HEAD", "OPTIONS")
            for t in workflow.test_steps
        )
        has_cleanup_mut = any(
            c.risk in ("risk:mutating", "risk:destructive")
            or c.method.upper() not in ("GET", "HEAD", "OPTIONS")
            for c in workflow.cleanup_steps
        )
        any_mutation = has_setup_mut or has_test_mut or has_cleanup_mut

        if any_mutation and not confirm_isolated_env:
            finish_time = self._clock()
            return WorkflowExecutionResult(
                workflow_id=workflow.workflow_id,
                title=workflow.title,
                status=CaseStatus.BLOCKED,
                started_at=started_at_iso,
                finished_at=finish_time.isoformat(),
                duration_ms=(finish_time - start_time).total_seconds() * 1000.0,
                target_origin=eff_origin or "unknown",
                environment_name=env_name,
                error_message=(
                    "Mutating workflow execution requires explicit isolated test "
                    "environment confirmation (--confirm-isolated-env)."
                ),
            )

        if has_setup_mut and not eff_setup_mut:
            finish_time = self._clock()
            return WorkflowExecutionResult(
                workflow_id=workflow.workflow_id,
                title=workflow.title,
                status=CaseStatus.BLOCKED,
                started_at=started_at_iso,
                finished_at=finish_time.isoformat(),
                duration_ms=(finish_time - start_time).total_seconds() * 1000.0,
                target_origin=eff_origin or "unknown",
                environment_name=env_name,
                error_message=(
                    "Setup actions require mutations but setup mutations are not authorized "
                    "(--allow-mutation or --allow-setup-mutation)."
                ),
            )

        # -------------------------------------------------------------
        # 2. PRE-VALIDATE SETUP REQUIREMENTS (FAIL CLOSED BEFORE SENDING)
        # -------------------------------------------------------------
        setup_blocked = False
        setup_block_reason = ""
        provided_by_setup: set[str] = set()

        for s_act in workflow.setup_steps:
            needed = _find_referenced_variables(s_act.url) | _find_referenced_variables(
                s_act.body
            )
            # Filter base_url placeholder
            needed = {v for v in needed if v not in ("base_url", "{{base_url}}")}
            unresolved = [
                v
                for v in needed
                if v not in runtime_vars and v not in provided_by_setup
            ]
            if unresolved:
                setup_blocked = True
                setup_block_reason = (
                    f"Setup action '{s_act.name}' has unresolved variables: "
                    f"{', '.join(unresolved)}"
                )
                break
            provided_by_setup.update(s_act.extract.keys())

        if setup_blocked:
            finish_time = self._clock()
            return WorkflowExecutionResult(
                workflow_id=workflow.workflow_id,
                title=workflow.title,
                status=CaseStatus.BLOCKED,
                started_at=started_at_iso,
                finished_at=finish_time.isoformat(),
                duration_ms=(finish_time - start_time).total_seconds() * 1000.0,
                target_origin=eff_origin or "unknown",
                environment_name=env_name,
                error_message=setup_block_reason,
            )

        # -------------------------------------------------------------
        # 3. EXECUTE SETUP STEPS IN DEPENDENCY ORDER
        # -------------------------------------------------------------
        setup_failed = False
        setup_failure_reason = ""

        for s_act in workflow.setup_steps:
            # Check elapsed timeout
            if (self._clock() - start_time).total_seconds() > timeout_seconds:
                setup_failed = True
                setup_failure_reason = (
                    f"Workflow execution exceeded timeout of {timeout_seconds}s."
                )
                setup_results.append(
                    WorkflowStepResult(
                        step_number=s_act.step_number,
                        name=s_act.name,
                        step_type=WorkflowStepType.SETUP,
                        status=CaseStatus.ERROR,
                        method=s_act.method,
                        url=s_act.url,
                        error_message=setup_failure_reason,
                    )
                )
                break

            if setup_failed:
                setup_results.append(
                    WorkflowStepResult(
                        step_number=s_act.step_number,
                        name=s_act.name,
                        step_type=WorkflowStepType.SETUP,
                        status=CaseStatus.SKIPPED,
                        method=s_act.method,
                        url=s_act.url,
                        skip_reason=f"Skipped due to preceding setup failure: {setup_failure_reason}",
                    )
                )
                continue

            # Execute individual setup action
            res = self._execute_action(
                action=s_act,
                step_type=WorkflowStepType.SETUP,
                runtime_vars=runtime_vars,
                known_secrets=known_secrets,
                allow_mutation=eff_setup_mut,
            )
            setup_results.append(res)

            if res.status != CaseStatus.PASSED:
                setup_failed = True
                setup_failure_reason = (
                    res.error_message or res.skip_reason or "Setup assertion failed."
                )
                break

            # Handle extractions for confirmed created entity
            if s_act.extract:
                for var_name in s_act.extract:
                    val = runtime_vars.get(var_name)
                    if val is not None and var_name not in pre_existing_keys:
                        # Find corresponding cleanup action if declared
                        matching_clean = None
                        for c in workflow.cleanup_steps:
                            if var_name in c.url:
                                matching_clean = c
                                break
                        created_resources.append(
                            CreatedResource(
                                entity=s_act.name,
                                resource_id=val,
                                cleanup_action=matching_clean,
                                cleanup_url=matching_clean.url
                                if matching_clean
                                else "",
                                created_at_step=s_act.step_number,
                            )
                        )
                        res.created_resource = created_resources[-1]

        # -------------------------------------------------------------
        # 4. EXECUTE TEST STEPS (ONLY IF SETUP SUCCEEDED)
        # -------------------------------------------------------------
        test_failed = False
        test_error = False
        test_blocked = False

        for t_act in workflow.test_steps:
            if setup_failed:
                test_results.append(
                    WorkflowStepResult(
                        step_number=t_act.step_number,
                        name=t_act.name,
                        step_type=WorkflowStepType.TEST,
                        status=CaseStatus.SKIPPED,
                        method=t_act.method,
                        url=t_act.url,
                        skip_reason=f"Skipped because setup failed: {setup_failure_reason}",
                    )
                )
                continue

            if (self._clock() - start_time).total_seconds() > timeout_seconds:
                test_error = True
                test_results.append(
                    WorkflowStepResult(
                        step_number=t_act.step_number,
                        name=t_act.name,
                        step_type=WorkflowStepType.TEST,
                        status=CaseStatus.ERROR,
                        method=t_act.method,
                        url=t_act.url,
                        error_message=f"Workflow execution exceeded timeout of {timeout_seconds}s.",
                    )
                )
                break

            res = self._execute_action(
                action=t_act,
                step_type=WorkflowStepType.TEST,
                runtime_vars=runtime_vars,
                known_secrets=known_secrets,
                allow_mutation=allow_mutation,
            )
            test_results.append(res)

            if res.status == CaseStatus.FAILED:
                test_failed = True
            elif res.status == CaseStatus.ERROR:
                test_error = True
            elif res.status == CaseStatus.BLOCKED:
                test_blocked = True

        # -------------------------------------------------------------
        # 5. ATTEMPT PERMITTED CLEANUP IN REVERSE DEPENDENCY ORDER (LIFO)
        # -------------------------------------------------------------
        cleanup_failed = False

        if not eff_cleanup_mut:
            for created_res in reversed(created_resources):
                cleanup_results.append(
                    WorkflowStepResult(
                        step_number=len(setup_results)
                        + len(test_results)
                        + len(cleanup_results)
                        + 1,
                        name=f"Cleanup {created_res.entity} ({created_res.resource_id})",
                        step_type=WorkflowStepType.CLEANUP,
                        status=CaseStatus.SKIPPED,
                        method="DELETE",
                        url=created_res.cleanup_url,
                        skip_reason="Cleanup skipped: cleanup mutations not authorized.",
                    )
                )
        else:
            # LIFO iteration over resources confirmed created in THIS run
            for created_res in reversed(created_resources):
                step_num = (
                    len(setup_results) + len(test_results) + len(cleanup_results) + 1
                )
                clean_act = created_res.cleanup_action
                if not clean_act:
                    continue

                # Execute cleanup action with substituted created ID
                clean_res = self._execute_cleanup_action(
                    action=clean_act,
                    resource_id=created_res.resource_id,
                    step_number=step_num,
                    runtime_vars=runtime_vars,
                    known_secrets=known_secrets,
                )
                cleanup_results.append(clean_res)

                if clean_res.status not in (CaseStatus.PASSED, CaseStatus.SKIPPED):
                    cleanup_failed = True
                    self.logger.warning(
                        "Cleanup action failed for %s (%s): %s",
                        created_res.entity,
                        created_res.resource_id,
                        clean_res.error_message,
                    )

        # -------------------------------------------------------------
        # 6. OVERALL WORKFLOW STATUS DETERMINATION
        # -------------------------------------------------------------
        overall_status = CaseStatus.PASSED

        if setup_failed:
            overall_status = (
                CaseStatus.BLOCKED
                if any(s.status == CaseStatus.BLOCKED for s in setup_results)
                else (
                    CaseStatus.ERROR
                    if any(s.status == CaseStatus.ERROR for s in setup_results)
                    else CaseStatus.FAILED
                )
            )
        elif test_error:
            overall_status = CaseStatus.ERROR
        elif test_blocked:
            overall_status = CaseStatus.BLOCKED
        elif test_failed:
            overall_status = CaseStatus.FAILED
        elif cleanup_failed:
            overall_status = CaseStatus.CLEANUP_FAILED

        finish_time = self._clock()
        finished_at_iso = finish_time.isoformat()
        duration_ms = (finish_time - start_time).total_seconds() * 1000.0

        return WorkflowExecutionResult(
            workflow_id=workflow.workflow_id,
            title=workflow.title,
            status=overall_status,
            started_at=started_at_iso,
            finished_at=finished_at_iso,
            duration_ms=duration_ms,
            target_origin=eff_origin or "unknown",
            environment_name=env_name,
            setup_results=setup_results,
            test_results=test_results,
            cleanup_results=cleanup_results,
            runtime_variables=dict(runtime_vars),
            created_resources=[r.to_dict() for r in created_resources],
            error_message=(
                setup_failure_reason
                if setup_failed
                else (
                    "One or more assertions failed."
                    if test_failed
                    else (
                        "Required cleanup operations failed."
                        if cleanup_failed
                        else None
                    )
                )
            ),
            is_mutation_authorized=allow_mutation or eff_setup_mut,
            is_isolated_env_confirmed=confirm_isolated_env,
        )

    def _execute_action(
        self,
        action: WorkflowAction,
        step_type: WorkflowStepType,
        runtime_vars: dict[str, Any],
        known_secrets: set[str],
        allow_mutation: bool,
    ) -> WorkflowStepResult:
        """Execute a single setup or test action with variable resolution and safety."""
        # 1. Discover and check required variables
        needed_vars = _find_referenced_variables(
            action.url
        ) | _find_referenced_variables(action.body)
        # Exclude {{base_url}} if standard
        needed_vars = {v for v in needed_vars if v not in ("base_url", "{{base_url}}")}
        missing = [v for v in needed_vars if v not in runtime_vars]
        if missing:
            return WorkflowStepResult(
                step_number=action.step_number,
                name=action.name,
                step_type=step_type,
                status=CaseStatus.BLOCKED,
                method=action.method,
                url=self._mask_url(action.url, known_secrets, runtime_vars),
                skip_reason=f"Missing required variable: '{missing[0]}'",
            )

        # 2. Check mutation authorization
        is_mut = action.risk in (
            "risk:mutating",
            "risk:destructive",
        ) or action.method.upper() not in ("GET", "HEAD", "OPTIONS")
        if is_mut and not allow_mutation:
            return WorkflowStepResult(
                step_number=action.step_number,
                name=action.name,
                step_type=step_type,
                status=CaseStatus.BLOCKED,
                method=action.method,
                url=self._mask_url(action.url, known_secrets, runtime_vars),
                skip_reason="Mutating operation is not authorized by policy.",
            )

        # 3. Resolve variables into URL, headers, and body
        resolved_url = action.url
        with contextlib.suppress(Exception):
            resolved_url = self._resolver.resolve_string(action.url, runtime_vars)

        resolved_headers: dict[str, str] = {}
        for k, v in action.headers.items():
            resolved_headers[str(k)] = str(
                self._resolver.resolve_string(str(v), runtime_vars)
            )

        resolved_body: Any = None
        if action.body is not None:
            resolved_body = self._resolver.resolve_data(action.body, runtime_vars)

        # Ensure Content-Type header if JSON body
        if resolved_body and "Content-Type" not in resolved_headers:
            resolved_headers["Content-Type"] = "application/json"

        corr_id = self._correlation_id_generator()
        resolved_headers["X-Correlation-ID"] = corr_id

        # 4. Construct HttpRequest
        method_enum = HttpMethod.from_string(action.method)
        http_req = HttpRequest(
            method=method_enum,
            url=resolved_url,
            headers=resolved_headers,
            body=resolved_body,
        )

        # 5. Dispatch HTTP request through existing RequestExecutor
        t_start = self._clock()
        try:
            response = self._executor.execute(http_req)
        except Exception as exc:
            elapsed = (self._clock() - t_start).total_seconds() * 1000.0
            return WorkflowStepResult(
                step_number=action.step_number,
                name=action.name,
                step_type=step_type,
                status=CaseStatus.ERROR,
                method=action.method,
                url=self._mask_url(resolved_url, known_secrets, runtime_vars),
                elapsed_ms=elapsed,
                error_message=self._mask_text(
                    str(exc), self._get_secret_values(known_secrets, runtime_vars)
                ),
                correlation_id=corr_id,
            )

        elapsed = (self._clock() - t_start).total_seconds() * 1000.0

        # 6. Evaluate assertions using AssertionEngine
        assertions_eval = self._assertion_engine.evaluate(response, action.assertions)
        step_passed = assertions_eval.failed == 0

        # 7. Response variable extractions
        extracted: dict[str, Any] = {}
        extraction_errors: list[str] = []

        if step_passed and action.extract:
            for var_name, expr in action.extract.items():
                ext_val = self._extractor.extract(response, expr)
                if ext_val is None:
                    extraction_errors.append(
                        f"Failed to extract '{var_name}' from path '{expr}'"
                    )
                else:
                    extracted[var_name] = ext_val
                    runtime_vars[var_name] = ext_val

        status = (
            CaseStatus.PASSED
            if (step_passed and not extraction_errors)
            else (CaseStatus.FAILED if not step_passed else CaseStatus.ERROR)
        )

        return WorkflowStepResult(
            step_number=action.step_number,
            name=action.name,
            step_type=step_type,
            status=status,
            method=action.method,
            url=self._mask_url(resolved_url, known_secrets, runtime_vars),
            status_code=response.status_code,
            elapsed_ms=elapsed,
            request_headers=self._mask_headers(
                resolved_headers, known_secrets, runtime_vars
            ),
            response_headers=dict(response.headers),
            assertions=[r.to_dict() for r in assertions_eval.results],
            extracted_variables=extracted,
            extraction_errors=extraction_errors,
            error_message="; ".join(extraction_errors) if extraction_errors else None,
            correlation_id=corr_id,
        )

    def _execute_cleanup_action(
        self,
        action: WorkflowAction,
        resource_id: Any,
        step_number: int,
        runtime_vars: dict[str, Any],
        known_secrets: set[str],
    ) -> WorkflowStepResult:
        """Execute a cleanup action (DELETE) for a resource created during this run."""
        # Resolve variables using resolver first
        clean_url = action.url
        with contextlib.suppress(Exception):
            clean_url = self._resolver.resolve_string(action.url, runtime_vars)

        # Substitute any remaining OpenAPI path parameters like {id} or {categoryId}
        clean_url = re.sub(r"\{[a-zA-Z0-9_.-]+\}", str(resource_id), clean_url)

        corr_id = self._correlation_id_generator()
        headers = {
            "Accept": "application/json",
            "X-Correlation-ID": corr_id,
        }

        http_req = HttpRequest(
            method=HttpMethod.DELETE,
            url=clean_url,
            headers=headers,
        )

        t_start = self._clock()
        try:
            response = self._executor.execute(http_req)
        except Exception as exc:
            elapsed = (self._clock() - t_start).total_seconds() * 1000.0
            return WorkflowStepResult(
                step_number=step_number,
                name=f"[CLEANUP] Delete {action.name} ({resource_id})",
                step_type=WorkflowStepType.CLEANUP,
                status=CaseStatus.CLEANUP_FAILED,
                method="DELETE",
                url=self._mask_url(clean_url, known_secrets, runtime_vars),
                elapsed_ms=elapsed,
                error_message=self._mask_text(
                    str(exc), self._get_secret_values(known_secrets, runtime_vars)
                ),
                correlation_id=corr_id,
            )

        elapsed = (self._clock() - t_start).total_seconds() * 1000.0

        # Accepted cleanup status codes: 204 No Content, 200 OK, or 404 (already deleted)
        is_success = response.status_code in (200, 204, 404)
        status = CaseStatus.PASSED if is_success else CaseStatus.CLEANUP_FAILED

        return WorkflowStepResult(
            step_number=step_number,
            name=f"[CLEANUP] Delete {action.name} ({resource_id})",
            step_type=WorkflowStepType.CLEANUP,
            status=status,
            method="DELETE",
            url=self._mask_url(clean_url, known_secrets, runtime_vars),
            status_code=response.status_code,
            elapsed_ms=elapsed,
            error_message=(
                f"Cleanup DELETE returned unexpected status {response.status_code}"
                if not is_success
                else None
            ),
            correlation_id=corr_id,
        )

    def execute_workflows(
        self,
        workflows: list[StatefulWorkflow],
        environment: Environment | None = None,
        allow_mutation: bool = False,
        allow_setup_mutation: bool | None = None,
        allow_cleanup_mutation: bool | None = None,
        confirm_isolated_env: bool = False,
        target_origin: str | None = None,
        static_fixtures: dict[str, Any] | None = None,
    ) -> tuple[list[WorkflowExecutionResult], RunResult]:
        """Execute multiple workflows with strict variable isolation between each."""
        wf_results: list[WorkflowExecutionResult] = []
        all_case_results: list[CaseResult] = []

        start_time = self._clock()

        for wf in workflows:
            # Each workflow receives its own isolated execution context
            res = self.execute_workflow(
                workflow=wf,
                environment=environment,
                allow_mutation=allow_mutation,
                allow_setup_mutation=allow_setup_mutation,
                allow_cleanup_mutation=allow_cleanup_mutation,
                confirm_isolated_env=confirm_isolated_env,
                target_origin=target_origin,
                static_fixtures=static_fixtures,
            )
            wf_results.append(res)
            if res.all_steps:
                all_case_results.extend(
                    s.to_case_result(wf.workflow_id) for s in res.all_steps
                )
            else:
                all_case_results.append(
                    CaseResult(
                        test_name=f"[{wf.workflow_id}] Pre-execution Gate",
                        module=wf.title or "workflow",
                        tags=[
                            f"workflow:{wf.workflow_id.lower()}",
                            f"status:{res.status.value.lower()}",
                        ],
                        status=res.status,
                        verified=False,
                        method="POLICY",
                        url=res.target_origin,
                        status_code=None,
                        elapsed_ms=res.duration_ms,
                        request_headers={},
                        response_headers={},
                        assertion_results=[],
                        extracted_variables={},
                        extraction_errors=[],
                        error_message=res.error_message,
                    )
                )

        finish_time = self._clock()
        duration = (finish_time - start_time).total_seconds() * 1000.0

        passed = sum(1 for c in all_case_results if c.status == CaseStatus.PASSED)
        failed = sum(1 for c in all_case_results if c.status == CaseStatus.FAILED)
        errors = sum(1 for c in all_case_results if c.status == CaseStatus.ERROR)
        skipped = sum(1 for c in all_case_results if c.status == CaseStatus.SKIPPED)
        blocked = sum(1 for c in all_case_results if c.status == CaseStatus.BLOCKED)

        run_id = f"run-wfs-{int(start_time.timestamp())}"
        aggregated_run = RunResult(
            run_id=run_id,
            environment_name=environment.name if environment else None,
            started_at=start_time,
            finished_at=finish_time,
            duration_ms=duration,
            total_tests=len(all_case_results),
            passed_tests=passed,
            failed_tests=failed,
            error_tests=errors,
            skipped_tests=skipped,
            blocked_tests=blocked,
            results=all_case_results,
            has_warnings=any(w.status == CaseStatus.CLEANUP_FAILED for w in wf_results),
        )

        return wf_results, aggregated_run
