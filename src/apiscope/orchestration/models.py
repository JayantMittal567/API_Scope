"""Data models and stage states for Guided Project-Testing Orchestration (Task 26).

Defines lifecycle stage enumerations, execution records, dry-run previews,
reproducible workflow manifests, and options for unified project execution.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from apiscope.analysis.endpoint_inventory import EndpointInventoryReport
from apiscope.analysis.silent_failure_service import SilentFailureReport
from apiscope.compiler.models import CompilationReport
from apiscope.contract.models import ContractQualityReport
from apiscope.core.logging_config import redact_sensitive_text
from apiscope.core.results import RunResult
from apiscope.execution.models import WorkflowExecutionResult
from apiscope.fixtures.models import FixturePreparationReport
from apiscope.intelligence.models import ProjectIntelligenceReport
from apiscope.investigation.models import FailureInvestigationReport
from apiscope.planning.models import AIBudgetConfig, TestPlan
from apiscope.trust.models import TrustAssessmentReport


class WorkflowStage(StrEnum):
    """The 11 discrete stages of the guided ApiScope project-testing lifecycle."""

    PROJECT_INSPECTION = "PROJECT_INSPECTION"
    CONTRACT_ANALYSIS = "CONTRACT_ANALYSIS"
    TEST_PLANNING = "TEST_PLANNING"
    TEST_COMPILATION = "TEST_COMPILATION"
    FIXTURE_PREPARATION = "FIXTURE_PREPARATION"
    EXECUTION_PREVIEW = "EXECUTION_PREVIEW"
    CONTROLLED_EXECUTION = "CONTROLLED_EXECUTION"
    FAILURE_INVESTIGATION = "FAILURE_INVESTIGATION"
    SILENT_FAILURE_AND_COVERAGE_DIAGNOSTICS = "SILENT_FAILURE_AND_COVERAGE_DIAGNOSTICS"
    TRUST_ASSESSMENT = "TRUST_ASSESSMENT"
    REPORT_EXPORT = "REPORT_EXPORT"


class StageStatus(StrEnum):
    """Operational status of a specific workflow stage."""

    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


@dataclass
class StageExecutionRecord:
    """Execution telemetry and outcome for a single workflow stage."""

    __test__ = False

    stage: WorkflowStage
    status: StageStatus = StageStatus.NOT_STARTED
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: float = 0.0
    summary: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    artifact_paths: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert record to JSON-serializable dictionary with secret redaction."""
        return {
            "stage": self.stage.value,
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": round(self.duration_ms, 2),
            "summary": redact_sensitive_text(self.summary),
            "warnings": [redact_sensitive_text(w) for w in self.warnings],
            "errors": [redact_sensitive_text(e) for e in self.errors],
            "artifact_paths": list(self.artifact_paths),
            "details": {
                k: redact_sensitive_text(str(v)) if isinstance(v, str) else v
                for k, v in self.details.items()
            },
        }


@dataclass
class ExecutionPreview:
    """First-class dry-run preview synthesized without issuing target network requests."""

    __test__ = False

    project_name: str = "Unknown Project"
    domain_modules: list[str] = field(default_factory=list)
    contract_readiness_summary: dict[str, Any] = field(default_factory=dict)
    total_scenarios_planned: int = 0
    deterministic_scenarios_count: int = 0
    ai_scenarios_count: int = 0
    scenarios_needing_review: int = 0
    compiled_test_count: int = 0
    fixture_readiness_summary: dict[str, Any] = field(default_factory=dict)
    planned_setup_actions: list[str] = field(default_factory=list)
    planned_cleanup_actions: list[str] = field(default_factory=list)
    readonly_operations_count: int = 0
    mutating_operations_count: int = 0
    policy_restrictions: list[str] = field(default_factory=list)
    missing_prerequisites: list[str] = field(default_factory=list)
    estimated_requests: int = 0
    recommended_next_actions: list[str] = field(default_factory=list)
    target_origin: str | None = None
    is_execution_authorized: bool = False
    authorization_blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert preview to JSON-serializable dictionary with secret redaction."""
        return {
            "project_name": redact_sensitive_text(self.project_name),
            "domain_modules": list(self.domain_modules),
            "contract_readiness_summary": dict(self.contract_readiness_summary),
            "total_scenarios_planned": self.total_scenarios_planned,
            "deterministic_scenarios_count": self.deterministic_scenarios_count,
            "ai_scenarios_count": self.ai_scenarios_count,
            "scenarios_needing_review": self.scenarios_needing_review,
            "compiled_test_count": self.compiled_test_count,
            "fixture_readiness_summary": dict(self.fixture_readiness_summary),
            "planned_setup_actions": [
                redact_sensitive_text(a) for a in self.planned_setup_actions
            ],
            "planned_cleanup_actions": [
                redact_sensitive_text(a) for a in self.planned_cleanup_actions
            ],
            "readonly_operations_count": self.readonly_operations_count,
            "mutating_operations_count": self.mutating_operations_count,
            "policy_restrictions": [
                redact_sensitive_text(r) for r in self.policy_restrictions
            ],
            "missing_prerequisites": [
                redact_sensitive_text(m) for m in self.missing_prerequisites
            ],
            "estimated_requests": self.estimated_requests,
            "recommended_next_actions": [
                redact_sensitive_text(a) for a in self.recommended_next_actions
            ],
            "target_origin": redact_sensitive_text(self.target_origin)
            if self.target_origin
            else None,
            "is_execution_authorized": self.is_execution_authorized,
            "authorization_blockers": [
                redact_sensitive_text(b) for b in self.authorization_blockers
            ],
        }


@dataclass
class WorkflowManifest:
    """Reproducible, audit-grade workflow execution manifest and provenance record."""

    __test__ = False

    workflow_id: str
    schema_version: str = "1.0.0"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None
    spec_path: str = ""
    spec_hash: str = ""
    repo_path: str | None = None
    repo_hash: str | None = None
    target_origin: str | None = None
    environment_name: str = "test"
    is_isolated_env: bool = False
    allow_mutation: bool = False
    allow_destructive: bool = False
    ai_enabled: bool = False
    model_name: str | None = None
    stage_records: dict[str, StageExecutionRecord] = field(default_factory=dict)
    artifact_manifest: dict[str, str] = field(default_factory=dict)
    run_id: str | None = None
    overall_status: str = "NOT_STARTED"
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert manifest to JSON-serializable dictionary with sanitized secrets."""
        return {
            "workflow_id": self.workflow_id,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "spec_path": redact_sensitive_text(self.spec_path),
            "spec_hash": self.spec_hash,
            "repo_path": redact_sensitive_text(self.repo_path)
            if self.repo_path
            else None,
            "repo_hash": self.repo_hash,
            "target_origin": redact_sensitive_text(self.target_origin)
            if self.target_origin
            else None,
            "environment_name": self.environment_name,
            "is_isolated_env": self.is_isolated_env,
            "allow_mutation": self.allow_mutation,
            "allow_destructive": self.allow_destructive,
            "ai_enabled": self.ai_enabled,
            "model_name": self.model_name,
            "stage_records": {k: v.to_dict() for k, v in self.stage_records.items()},
            "artifact_manifest": dict(self.artifact_manifest),
            "run_id": self.run_id,
            "overall_status": self.overall_status,
            "warnings": [redact_sensitive_text(w) for w in self.warnings],
            "errors": [redact_sensitive_text(e) for e in self.errors],
            "next_actions": [redact_sensitive_text(a) for a in self.next_actions],
        }

    def to_json(self, indent: int = 2) -> str:
        """Export manifest to pretty JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def to_yaml(self) -> str:
        """Export manifest to clean YAML string."""
        return yaml.dump(self.to_dict(), sort_keys=False)


@dataclass
class GuidedWorkflowOptions:
    """User-supplied options controlling the guided end-to-end testing workflow."""

    __test__ = False

    spec_path: str | Path
    repo_path: str | Path | None = None
    output_dir: str | Path | None = None
    target_origin: str | None = None
    environment_name: str = "test"
    is_isolated_env: bool = False
    confirm_isolated_env: bool = False
    allow_mutation: bool = False
    allow_destructive: bool = False
    execute: bool = False
    preview_only: bool = False
    existing_run_file: str | Path | None = None
    existing_workflow_report: str | Path | None = None
    existing_intelligence_file: str | Path | None = None
    existing_contract_file: str | Path | None = None
    existing_plan_file: str | Path | None = None
    enable_ai: bool = False
    ai_provider: str = "gemini"
    ai_api_key: str | None = None
    ai_model: str | None = None
    ai_budget: AIBudgetConfig | None = None
    fixture_file: str | Path | None = None
    static_fixtures: dict[str, Any] | None = None
    module_filter: str | None = None
    format: str = "json"
    strict: bool = False
    resume_from_manifest: str | Path | None = None

    def __post_init__(self) -> None:
        """Harmonize isolated environment confirmation flags."""
        if self.confirm_isolated_env:
            self.is_isolated_env = True
        elif self.is_isolated_env:
            self.confirm_isolated_env = True


@dataclass
class GuidedWorkflowResult:
    """Consolidated outcome of the guided workflow execution."""

    __test__ = False

    manifest: WorkflowManifest
    preview: ExecutionPreview | None = None
    intelligence_report: ProjectIntelligenceReport | None = None
    contract_report: ContractQualityReport | None = None
    test_plan: TestPlan | None = None
    compilation_report: CompilationReport | None = None
    fixture_report: FixturePreparationReport | None = None
    run_result: RunResult | None = None
    workflow_result: WorkflowExecutionResult | None = None
    investigation_report: FailureInvestigationReport | None = None
    silent_report: SilentFailureReport | None = None
    inventory_report: EndpointInventoryReport | None = None
    trust_report: TrustAssessmentReport | None = None
    is_success: bool = True
    exit_code: int = 0
