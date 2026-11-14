"""Domain models for workflow execution, lifecycle steps, and safety telemetry (Task 21).

Provides structured records for stateful workflow executions, individual step
outcomes (setup, test, cleanup), created resource ownership, and dry-run previews.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.fixtures.models import WorkflowAction


class WorkflowStepType(StrEnum):
    """Categorization of individual workflow lifecycle actions."""

    SETUP = "SETUP"
    TEST = "TEST"
    CLEANUP = "CLEANUP"


@dataclass
class CreatedResource:
    """Record of a resource instance created and owned by a workflow run."""

    __test__ = False

    entity: str
    resource_id: Any
    cleanup_action: WorkflowAction | None = None
    cleanup_url: str = ""
    created_at_step: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Convert created resource record to dictionary."""
        return {
            "entity": self.entity,
            "resource_id": self.resource_id,
            "cleanup_url": self.cleanup_url,
            "created_at_step": self.created_at_step,
        }


@dataclass
class WorkflowStepResult:
    """Execution telemetry for a single workflow action (setup, test, or cleanup)."""

    __test__ = False

    step_number: int
    name: str
    step_type: WorkflowStepType
    status: CaseStatus
    method: str
    url: str
    status_code: int | None = None
    elapsed_ms: float = 0.0
    request_headers: dict[str, str] = field(default_factory=dict)
    response_headers: dict[str, str] = field(default_factory=dict)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    extracted_variables: dict[str, Any] = field(default_factory=dict)
    extraction_errors: list[str] = field(default_factory=list)
    error_message: str | None = None
    skip_reason: str | None = None
    correlation_id: str = ""
    created_resource: CreatedResource | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert step result to dictionary."""
        return {
            "step_number": self.step_number,
            "name": self.name,
            "step_type": self.step_type.value,
            "status": self.status.value,
            "method": self.method,
            "url": self.url,
            "status_code": self.status_code,
            "elapsed_ms": self.elapsed_ms,
            "request_headers": dict(self.request_headers),
            "response_headers": dict(self.response_headers),
            "assertions": list(self.assertions),
            "extracted_variables": dict(self.extracted_variables),
            "extraction_errors": list(self.extraction_errors),
            "error_message": self.error_message,
            "skip_reason": self.skip_reason,
            "correlation_id": self.correlation_id,
            "created_resource": (
                self.created_resource.to_dict() if self.created_resource else None
            ),
        }

    def to_case_result(self, workflow_id: str, module: str = "workflow") -> CaseResult:
        """Convert workflow step result to a standard ApiScope CaseResult."""
        prefix = f"[{self.step_type.value}:{self.step_number}]"
        tags = [
            f"workflow:{workflow_id.lower()}",
            f"step-type:{self.step_type.value.lower()}",
            f"status:{self.status.value.lower()}",
        ]
        return CaseResult(
            test_name=f"[{workflow_id}] {prefix} {self.name}",
            module=module,
            tags=tags,
            status=self.status,
            verified=(self.status == CaseStatus.PASSED),
            method=self.method,
            url=self.url,
            status_code=self.status_code,
            elapsed_ms=self.elapsed_ms,
            request_headers=dict(self.request_headers),
            response_headers=dict(self.response_headers),
            assertion_results=list(self.assertions),
            extracted_variables=dict(self.extracted_variables),
            extraction_errors=list(self.extraction_errors),
            error_message=self.error_message,
            skip_reason=self.skip_reason,
            correlation_id=self.correlation_id,
        )


@dataclass
class WorkflowExecutionResult:
    """Comprehensive execution outcome of an entire stateful workflow."""

    __test__ = False

    workflow_id: str
    title: str
    status: CaseStatus
    started_at: str
    finished_at: str
    duration_ms: float
    target_origin: str
    environment_name: str | None = None
    setup_results: list[WorkflowStepResult] = field(default_factory=list)
    test_results: list[WorkflowStepResult] = field(default_factory=list)
    cleanup_results: list[WorkflowStepResult] = field(default_factory=list)
    runtime_variables: dict[str, Any] = field(default_factory=dict)
    created_resources: list[dict[str, Any]] = field(default_factory=list)
    error_message: str | None = None
    is_mutation_authorized: bool = False
    is_isolated_env_confirmed: bool = False

    @property
    def all_passed(self) -> bool:
        """Return True only if the workflow status is PASSED."""
        return self.status == CaseStatus.PASSED

    @property
    def all_steps(self) -> list[WorkflowStepResult]:
        """Return flattened list of all executed workflow steps."""
        return [*self.setup_results, *self.test_results, *self.cleanup_results]

    def to_dict(self) -> dict[str, Any]:
        """Convert workflow result to JSON-serializable dictionary."""
        # Mask sensitive keys in runtime variables
        safe_runtime: dict[str, Any] = {}
        for k, v in self.runtime_variables.items():
            if any(s in k.lower() for s in ("token", "secret", "password", "key")):
                safe_runtime[k] = "***REDACTED***"
            else:
                safe_runtime[k] = v

        return {
            "workflow_id": self.workflow_id,
            "title": self.title,
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "target_origin": self.target_origin,
            "environment_name": self.environment_name,
            "summary": {
                "total_steps": len(self.all_steps),
                "setup_steps": len(self.setup_results),
                "test_steps": len(self.test_results),
                "cleanup_steps": len(self.cleanup_results),
                "all_passed": self.all_passed,
                "is_mutation_authorized": self.is_mutation_authorized,
                "is_isolated_env_confirmed": self.is_isolated_env_confirmed,
            },
            "setup_results": [s.to_dict() for s in self.setup_results],
            "test_results": [t.to_dict() for t in self.test_results],
            "cleanup_results": [c.to_dict() for c in self.cleanup_results],
            "runtime_variables": safe_runtime,
            "created_resources": list(self.created_resources),
            "error_message": self.error_message,
        }

    def to_run_result(
        self,
        run_id: str | None = None,
        module: str = "workflow",
    ) -> RunResult:
        """Convert workflow execution outcome into a standard ApiScope RunResult."""
        if self.all_steps:
            case_results = [
                s.to_case_result(self.workflow_id, module=module)
                for s in self.all_steps
            ]
        else:
            case_results = [
                CaseResult(
                    test_name=f"[{self.workflow_id}] Pre-execution Gate",
                    module=module,
                    tags=[
                        f"workflow:{self.workflow_id.lower()}",
                        f"status:{self.status.value.lower()}",
                    ],
                    status=self.status,
                    verified=False,
                    method="POLICY",
                    url=self.target_origin,
                    status_code=None,
                    elapsed_ms=self.duration_ms,
                    request_headers={},
                    response_headers={},
                    assertion_results=[],
                    extracted_variables={},
                    extraction_errors=[],
                    error_message=self.error_message,
                )
            ]
        passed = sum(1 for c in case_results if c.status == CaseStatus.PASSED)
        failed = sum(1 for c in case_results if c.status == CaseStatus.FAILED)
        errors = sum(1 for c in case_results if c.status == CaseStatus.ERROR)
        skipped = sum(1 for c in case_results if c.status == CaseStatus.SKIPPED)
        blocked = sum(1 for c in case_results if c.status == CaseStatus.BLOCKED)

        # Parse ISO timestamps or fallback to now
        try:
            start_dt = datetime.fromisoformat(self.started_at)
        except Exception:
            start_dt = datetime.now(UTC)
        try:
            finish_dt = datetime.fromisoformat(self.finished_at)
        except Exception:
            finish_dt = datetime.now(UTC)

        effective_run_id = (
            run_id or f"run-wf-{self.workflow_id.lower()}-{int(start_dt.timestamp())}"
        )

        return RunResult(
            run_id=effective_run_id,
            environment_name=self.environment_name,
            started_at=start_dt,
            finished_at=finish_dt,
            duration_ms=self.duration_ms,
            total_tests=len(case_results),
            passed_tests=passed,
            failed_tests=failed,
            error_tests=errors,
            skipped_tests=skipped,
            blocked_tests=blocked,
            results=case_results,
            has_warnings=(self.status == CaseStatus.CLEANUP_FAILED or blocked > 0),
        )


@dataclass
class WorkflowDryRunReport:
    """Pre-execution dry-run simulation preview for stateful workflows."""

    __test__ = False

    workflow_id: str
    title: str
    target_origin: str
    environment_name: str | None
    planned_setup_steps: list[dict[str, Any]]
    planned_test_steps: list[dict[str, Any]]
    planned_cleanup_steps: list[dict[str, Any]]
    required_fixtures: list[str]
    provided_fixtures: list[str]
    missing_variables: list[str]
    is_mutation_authorized: bool
    is_isolated_env_confirmed: bool
    estimated_operations_count: int
    eligible_to_execute: bool
    ineligibility_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert dry-run report to dictionary."""
        return {
            "workflow_id": self.workflow_id,
            "title": self.title,
            "target_origin": self.target_origin,
            "environment_name": self.environment_name,
            "planned_setup_steps": list(self.planned_setup_steps),
            "planned_test_steps": list(self.planned_test_steps),
            "planned_cleanup_steps": list(self.planned_cleanup_steps),
            "required_fixtures": list(self.required_fixtures),
            "provided_fixtures": list(self.provided_fixtures),
            "missing_variables": list(self.missing_variables),
            "is_mutation_authorized": self.is_mutation_authorized,
            "is_isolated_env_confirmed": self.is_isolated_env_confirmed,
            "estimated_operations_count": self.estimated_operations_count,
            "eligible_to_execute": self.eligible_to_execute,
            "ineligibility_reasons": list(self.ineligibility_reasons),
        }
