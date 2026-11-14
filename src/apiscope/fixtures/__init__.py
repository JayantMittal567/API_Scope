"""ApiScope Test Data Management & Fixture Resolution Engine (Task 20).

Provides structured fixture modeling, foreign key reference detection in request bodies,
dependency graph ordering, stateful workflow planning, and safe fixture lifecycles.
"""

from __future__ import annotations

from apiscope.fixtures.dependency_graph import DependencyGraph, DependencyNode
from apiscope.fixtures.entity_reference_detector import EntityReferenceDetector
from apiscope.fixtures.models import (
    FixtureDefinition,
    FixturePreparationReport,
    FixtureScope,
    FixtureSourceType,
    FixtureStatus,
    StatefulWorkflow,
    WorkflowAction,
)
from apiscope.fixtures.resolver import FixtureResolver
from apiscope.fixtures.service import FixtureService
from apiscope.fixtures.workflow_planner import StatefulWorkflowPlanner

__all__ = [
    "DependencyGraph",
    "DependencyNode",
    "EntityReferenceDetector",
    "FixtureDefinition",
    "FixturePreparationReport",
    "FixtureResolver",
    "FixtureScope",
    "FixtureService",
    "FixtureSourceType",
    "FixtureStatus",
    "StatefulWorkflow",
    "StatefulWorkflowPlanner",
    "WorkflowAction",
]
