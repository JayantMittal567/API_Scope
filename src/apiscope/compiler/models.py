"""Domain models for ApiScope Test Plan Compiler (Task 19).

Defines compilation statuses, scenario-level compilation results, and comprehensive
compilation reports distinguishing COMPILED, NEEDS_REVIEW, NEEDS_FIXTURE, and BLOCKED
test plan scenarios.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class CompilationStatus(StrEnum):
    """Compilation status for a test plan scenario."""

    COMPILED = "COMPILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NEEDS_FIXTURE = "NEEDS_FIXTURE"
    BLOCKED = "BLOCKED"


@dataclass
class ScenarioCompilationResult:
    """Compilation outcome for an individual scenario."""

    __test__ = False

    scenario_id: str
    title: str
    category: str
    origin: str
    priority: str
    risk: str
    status: CompilationStatus
    reasons: list[str] = field(default_factory=list)
    compiled_test_names: list[str] = field(default_factory=list)
    missing_fixtures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert compilation result to dictionary."""
        return {
            "scenario_id": self.scenario_id,
            "title": self.title,
            "category": self.category,
            "origin": self.origin,
            "priority": self.priority,
            "risk": self.risk,
            "status": self.status.value,
            "reasons": self.reasons,
            "compiled_test_names": self.compiled_test_names,
            "missing_fixtures": self.missing_fixtures,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScenarioCompilationResult:
        """Construct compilation result from dictionary."""
        raw_status = data.get("status", "BLOCKED")
        try:
            status = CompilationStatus(raw_status)
        except ValueError:
            status = CompilationStatus.BLOCKED

        return cls(
            scenario_id=str(data.get("scenario_id", "")),
            title=str(data.get("title", "")),
            category=str(data.get("category", "")),
            origin=str(data.get("origin", "")),
            priority=str(data.get("priority", "MEDIUM")),
            risk=str(data.get("risk", "LOW")),
            status=status,
            reasons=list(data.get("reasons", [])),
            compiled_test_names=list(data.get("compiled_test_names", [])),
            missing_fixtures=list(data.get("missing_fixtures", [])),
        )


@dataclass
class CompilationReport:
    """Comprehensive compilation report across an entire TestPlan."""

    __test__ = False

    plan_id: str
    title: str = ""
    schema_version: str = "1.0.0"
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    total_scenarios: int = 0
    compiled_count: int = 0
    needs_review_count: int = 0
    needs_fixture_count: int = 0
    blocked_count: int = 0
    total_tests_generated: int = 0
    scenarios_by_category: dict[str, int] = field(default_factory=dict)
    scenarios_by_origin: dict[str, int] = field(default_factory=dict)
    unresolved_prerequisites: list[str] = field(default_factory=list)
    results: list[ScenarioCompilationResult] = field(default_factory=list)

    @property
    def unresolved_fixtures(self) -> list[str]:
        """Convenience alias for unresolved prerequisites / fixtures."""
        return self.unresolved_prerequisites

    def to_dict(self) -> dict[str, Any]:
        """Convert compilation report to dictionary."""
        return {
            "plan_id": self.plan_id,
            "title": self.title,
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "summary": {
                "total_scenarios": self.total_scenarios,
                "compiled_count": self.compiled_count,
                "needs_review_count": self.needs_review_count,
                "needs_fixture_count": self.needs_fixture_count,
                "blocked_count": self.blocked_count,
                "total_tests_generated": self.total_tests_generated,
                "scenarios_by_category": self.scenarios_by_category,
                "scenarios_by_origin": self.scenarios_by_origin,
            },
            "unresolved_prerequisites": self.unresolved_prerequisites,
            "results": [r.to_dict() for r in self.results],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompilationReport:
        """Construct compilation report from dictionary."""
        summary = data.get("summary", {})
        results = [
            ScenarioCompilationResult.from_dict(r)
            for r in data.get("results", [])
            if isinstance(r, dict)
        ]
        return cls(
            plan_id=str(data.get("plan_id", "")),
            title=str(data.get("title", "")),
            schema_version=str(data.get("schema_version", "1.0.0")),
            generated_at=str(data.get("generated_at", datetime.now(UTC).isoformat())),
            total_scenarios=int(summary.get("total_scenarios", len(results))),
            compiled_count=int(summary.get("compiled_count", 0)),
            needs_review_count=int(summary.get("needs_review_count", 0)),
            needs_fixture_count=int(summary.get("needs_fixture_count", 0)),
            blocked_count=int(summary.get("blocked_count", 0)),
            total_tests_generated=int(summary.get("total_tests_generated", 0)),
            scenarios_by_category=dict(summary.get("scenarios_by_category", {})),
            scenarios_by_origin=dict(summary.get("scenarios_by_origin", {})),
            unresolved_prerequisites=list(data.get("unresolved_prerequisites", [])),
            results=results,
        )
