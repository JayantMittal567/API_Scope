"""Domain models for ApiScope Test Data & Fixture Management (Task 20).

Defines structured fixture representations, lifecycle scopes, resolution statuses,
stateful workflow actions, and preparation reports with strict secret redaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class FixtureStatus(StrEnum):
    """Resolution status of a test fixture."""

    RESOLVED = "RESOLVED"
    PLANNED = "PLANNED"
    MISSING = "MISSING"
    INVALID = "INVALID"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"


class FixtureSourceType(StrEnum):
    """Source from which a fixture value originates."""

    USER_STATIC = "USER_STATIC"
    FIXTURE_FILE = "FIXTURE_FILE"
    ENVIRONMENT = "ENVIRONMENT"
    RESPONSE_EXTRACTION = "RESPONSE_EXTRACTION"
    SETUP_WORKFLOW = "SETUP_WORKFLOW"


class FixtureScope(StrEnum):
    """Lifecycle scope and lifetime of a fixture."""

    GLOBAL = "GLOBAL"
    PER_WORKFLOW = "PER_WORKFLOW"
    PER_TEST = "PER_TEST"


@dataclass
class FixtureDefinition:
    """Structured representation of a test data fixture."""

    __test__ = False

    name: str
    entity: str
    source_type: FixtureSourceType = FixtureSourceType.USER_STATIC
    value: Any | None = None
    is_sensitive: bool = False
    status: FixtureStatus = FixtureStatus.MISSING
    producer_endpoint: str | None = None
    producer_field: str | None = None
    cleanup_endpoint: str | None = None
    dependencies: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    scope: FixtureScope = FixtureScope.PER_WORKFLOW
    evidence: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert fixture definition to dictionary, masking sensitive data."""
        return self.to_safe_dict()

    def to_safe_dict(self) -> dict[str, Any]:
        """Convert to dictionary with sensitive values redacted."""
        val = self.value
        if self.is_sensitive and val is not None:
            val = "***REDACTED***"

        return {
            "name": self.name,
            "entity": self.entity,
            "source_type": self.source_type.value,
            "value": val,
            "is_sensitive": self.is_sensitive,
            "status": self.status.value,
            "producer_endpoint": self.producer_endpoint,
            "producer_field": self.producer_field,
            "cleanup_endpoint": self.cleanup_endpoint,
            "dependencies": list(self.dependencies),
            "constraints": dict(self.constraints),
            "scope": self.scope.value,
            "evidence": list(self.evidence),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FixtureDefinition:
        """Construct fixture definition from dictionary."""
        source_str = str(data.get("source_type", "USER_STATIC")).upper()
        try:
            source_type = FixtureSourceType(source_str)
        except ValueError:
            source_type = FixtureSourceType.USER_STATIC

        status_str = str(data.get("status", "MISSING")).upper()
        try:
            status = FixtureStatus(status_str)
        except ValueError:
            status = FixtureStatus.MISSING

        scope_str = str(data.get("scope", "PER_WORKFLOW")).upper()
        try:
            scope = FixtureScope(scope_str)
        except ValueError:
            scope = FixtureScope.PER_WORKFLOW

        return cls(
            name=str(data.get("name", "")),
            entity=str(data.get("entity", "")),
            source_type=source_type,
            value=data.get("value"),
            is_sensitive=bool(data.get("is_sensitive", False)),
            status=status,
            producer_endpoint=data.get("producer_endpoint"),
            producer_field=data.get("producer_field"),
            cleanup_endpoint=data.get("cleanup_endpoint"),
            dependencies=list(data.get("dependencies", [])),
            constraints=dict(data.get("constraints", {})),
            scope=scope,
            evidence=list(data.get("evidence", [])),
            notes=str(data.get("notes", "")),
        )


@dataclass
class WorkflowAction:
    """An individual step within a planned stateful workflow."""

    __test__ = False

    step_number: int
    name: str
    action_type: str  # "SETUP", "TEST", "CLEANUP"
    method: str
    url: str
    headers: dict[str, Any] = field(default_factory=dict)
    body: dict[str, Any] | None = None
    extract: dict[str, str] = field(default_factory=dict)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    risk: str = "risk:read_only"
    can_rollback: bool = True
    rollback_endpoint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert workflow action to dictionary."""
        return {
            "step_number": self.step_number,
            "name": self.name,
            "action_type": self.action_type,
            "method": self.method,
            "url": self.url,
            "headers": dict(self.headers),
            "body": self.body,
            "extract": dict(self.extract),
            "assertions": list(self.assertions),
            "risk": self.risk,
            "can_rollback": self.can_rollback,
            "rollback_endpoint": self.rollback_endpoint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowAction:
        """Construct workflow action from dictionary."""
        return cls(
            step_number=int(data.get("step_number", 1)),
            name=str(data.get("name", "")),
            action_type=str(data.get("action_type", "TEST")),
            method=str(data.get("method", "GET")),
            url=str(data.get("url", "")),
            headers=dict(data.get("headers", {})),
            body=data.get("body"),
            extract=dict(data.get("extract", {})),
            assertions=list(data.get("assertions", [])),
            risk=str(data.get("risk", "risk:read_only")),
            can_rollback=bool(data.get("can_rollback", True)),
            rollback_endpoint=data.get("rollback_endpoint"),
        )


@dataclass
class StatefulWorkflow:
    """A multi-step API workflow with planned setup, test, and cleanup sequences."""

    __test__ = False

    workflow_id: str
    title: str
    description: str
    setup_steps: list[WorkflowAction] = field(default_factory=list)
    test_steps: list[WorkflowAction] = field(default_factory=list)
    cleanup_steps: list[WorkflowAction] = field(default_factory=list)
    required_fixtures: list[str] = field(default_factory=list)
    provided_fixtures: list[str] = field(default_factory=list)
    status: str = "PLANNED"  # "READY", "PLANNED", "BLOCKED", "REQUIRES_REVIEW"
    blocker_reasons: list[str] = field(default_factory=list)
    is_mutation_authorized: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert stateful workflow to dictionary."""
        return {
            "workflow_id": self.workflow_id,
            "title": self.title,
            "description": self.description,
            "setup_steps": [s.to_dict() for s in self.setup_steps],
            "test_steps": [t.to_dict() for t in self.test_steps],
            "cleanup_steps": [c.to_dict() for c in self.cleanup_steps],
            "required_fixtures": list(self.required_fixtures),
            "provided_fixtures": list(self.provided_fixtures),
            "status": self.status,
            "blocker_reasons": list(self.blocker_reasons),
            "is_mutation_authorized": self.is_mutation_authorized,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StatefulWorkflow:
        """Construct stateful workflow from dictionary."""
        return cls(
            workflow_id=str(data.get("workflow_id", "")),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            setup_steps=[
                WorkflowAction.from_dict(s) for s in data.get("setup_steps", [])
            ],
            test_steps=[
                WorkflowAction.from_dict(t) for t in data.get("test_steps", [])
            ],
            cleanup_steps=[
                WorkflowAction.from_dict(c) for c in data.get("cleanup_steps", [])
            ],
            required_fixtures=list(data.get("required_fixtures", [])),
            provided_fixtures=list(data.get("provided_fixtures", [])),
            status=str(data.get("status", "PLANNED")),
            blocker_reasons=list(data.get("blocker_reasons", [])),
            is_mutation_authorized=bool(data.get("is_mutation_authorized", False)),
        )


@dataclass
class FixturePreparationReport:
    """Comprehensive report on fixture analysis and workflow preparation."""

    __test__ = False

    plan_id: str
    title: str
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    total_scenarios_evaluated: int = 0
    fixture_ready_count: int = 0
    unresolved_fixture_count: int = 0
    unverified_placeholder_count: int = 0
    workflows_planned_count: int = 0
    resolved_fixtures: dict[str, Any] = field(default_factory=dict)
    missing_fixtures: list[str] = field(default_factory=list)
    unverified_entity_references: list[dict[str, Any]] = field(default_factory=list)
    planned_workflows: list[StatefulWorkflow] = field(default_factory=list)
    mutating_setup_allowed: bool = False

    @property
    def unverified_body_placeholders(self) -> list[dict[str, Any]]:
        """Alias for unverified entity references."""
        return self.unverified_entity_references

    @property
    def stateful_workflows_planned(self) -> int:
        """Alias for workflows planned count."""
        return self.workflows_planned_count

    @property
    def unresolved_fixtures(self) -> list[str]:
        """Alias for missing fixtures."""
        return self.missing_fixtures

    @property
    def total_tests_generated(self) -> int:
        """Alias for fixture ready tests count."""
        return self.fixture_ready_count

    def to_dict(self) -> dict[str, Any]:
        """Convert preparation report to dictionary."""
        # Sanitize resolved fixtures (mask any sensitive keys)
        safe_resolved: dict[str, Any] = {}
        for k, v in self.resolved_fixtures.items():
            if any(s in k.lower() for s in ("token", "secret", "password", "key")):
                safe_resolved[k] = "***REDACTED***"
            else:
                safe_resolved[k] = v

        return {
            "plan_id": self.plan_id,
            "title": self.title,
            "generated_at": self.generated_at,
            "summary": {
                "total_scenarios_evaluated": self.total_scenarios_evaluated,
                "fixture_ready_count": self.fixture_ready_count,
                "unresolved_fixture_count": self.unresolved_fixture_count,
                "unverified_placeholder_count": self.unverified_placeholder_count,
                "workflows_planned_count": self.workflows_planned_count,
                "mutating_setup_allowed": self.mutating_setup_allowed,
            },
            "resolved_fixtures": safe_resolved,
            "missing_fixtures": list(self.missing_fixtures),
            "unverified_entity_references": list(self.unverified_entity_references),
            "planned_workflows": [w.to_dict() for w in self.planned_workflows],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FixturePreparationReport:
        """Construct preparation report from dictionary."""
        summary = data.get("summary", {})
        workflows = [
            StatefulWorkflow.from_dict(w)
            for w in data.get("planned_workflows", [])
            if isinstance(w, dict)
        ]
        return cls(
            plan_id=str(data.get("plan_id", "")),
            title=str(data.get("title", "")),
            generated_at=str(data.get("generated_at", datetime.now(UTC).isoformat())),
            total_scenarios_evaluated=int(summary.get("total_scenarios_evaluated", 0)),
            fixture_ready_count=int(summary.get("fixture_ready_count", 0)),
            unresolved_fixture_count=int(summary.get("unresolved_fixture_count", 0)),
            unverified_placeholder_count=int(
                summary.get("unverified_placeholder_count", 0)
            ),
            workflows_planned_count=int(
                summary.get("workflows_planned_count", len(workflows))
            ),
            resolved_fixtures=dict(data.get("resolved_fixtures", {})),
            missing_fixtures=list(data.get("missing_fixtures", [])),
            unverified_entity_references=list(
                data.get("unverified_entity_references", [])
            ),
            planned_workflows=workflows,
            mutating_setup_allowed=bool(summary.get("mutating_setup_allowed", False)),
        )
