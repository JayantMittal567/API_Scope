"""Dependency and fixture requirement analyzer for ApiScope Compiler (Task 19).

Identifies unresolved test data prerequisites, missing path parameter fixtures,
and external environment dependencies without fabricating arbitrary resource IDs
or artificial credentials.
"""

from __future__ import annotations

import re

from apiscope.contract.models import CanonicalContract
from apiscope.planning.models import ScenarioCategory, TestScenario


class FixtureAnalyzer:
    """Analyzes scenarios for unresolved data dependencies and required fixtures."""

    def __init__(self, contract: CanonicalContract) -> None:
        """Initialize analyzer with contract context."""
        self.contract = contract

    def analyze_scenario_fixtures(
        self,
        scenario: TestScenario,
    ) -> tuple[bool, list[str]]:
        """Identify any unresolved fixture dependencies in the scenario.

        Args:
            scenario: TestScenario to inspect.

        Returns:
            Tuple of (has_unresolved_fixtures, list_of_missing_fixtures).
        """
        missing_fixtures: list[str] = []

        # 1. Inspect scenario-level declared missing dependencies
        for dep in scenario.missing_dependencies:
            # Skip environment authorization or security review requirements
            if dep == "target_environment_authorization" or "security" in dep:
                continue
            # Check if resolved internally by multi-step chaining
            if self._is_dependency_resolved_by_scenario_steps(dep, scenario):
                continue
            missing_fixtures.append(f"Unresolved dependency: {dep}")

        # 2. Inspect step paths for unchained path parameters
        for step in scenario.steps:
            path_params = re.findall(r"\{([^}]+)\}", step.path)
            if not path_params:
                continue

            # In a multi-step scenario, check if earlier steps produce these params
            for param in path_params:
                # If param starts with chain_ or is extracted, it is chained
                if param.startswith("chain_") or self._is_param_produced_by_prior_step(
                    param, step.step_number, scenario
                ):
                    continue

                missing_fixtures.append(
                    f"Path parameter '{{{param}}}' on {step.method} {step.path} "
                    f"requires existing resource fixture."
                )

        # 3. Check for synthetic placeholder substitution (e.g. ID: 1 in single step)
        if (
            scenario.category
            in (ScenarioCategory.CONTRACT_POSITIVE, ScenarioCategory.RESPONSE_SCHEMA)
            and len(scenario.steps) == 1
        ):
            step = scenario.steps[0]
            if "{" in step.path or any(
                "needs_existing_resource_id" in dep
                for dep in scenario.missing_dependencies
            ):
                missing_fixtures.append(
                    f"Single-step scenario targeting parameterized path '{step.path}' "
                    f"lacks a preceding setup fixture or resource producer."
                )

        # Deduplicate while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for item in missing_fixtures:
            if item not in seen:
                seen.add(item)
                deduped.append(item)

        has_unresolved = len(deduped) > 0
        return has_unresolved, deduped

    def _is_dependency_resolved_by_scenario_steps(
        self,
        dependency: str,
        scenario: TestScenario,
    ) -> bool:
        """Check if dependency is fulfilled by an earlier step in scenario."""
        # e.g., "needs_created_id" is fulfilled if step 1 is a POST that creates entity
        if "needs_created_id" in dependency and len(scenario.steps) > 1:
            first_step = scenario.steps[0]
            if first_step.method.upper() == "POST":
                return True
        return False

    def _is_param_produced_by_prior_step(
        self,
        _param_name: str,
        current_step_num: int,
        scenario: TestScenario,
    ) -> bool:
        """Check if a preceding step in this scenario provides the parameter."""
        for s in scenario.steps:
            if s.step_number >= current_step_num:
                break
            # A preceding POST or PUT step acts as a producer
            if s.method.upper() in ("POST", "PUT"):
                return True
        return False
