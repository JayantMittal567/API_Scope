"""Fixture management and workflow preparation orchestration service (Task 20).

Coordinates entity reference detection, fixture resolution, stateful workflow
planning, and native test collection preparation with strict execution fences.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from apiscope.contract.models import CanonicalContract, CanonicalOperation
from apiscope.contract.normalizer import ContractNormalizer
from apiscope.contract.service import ContractAnalysisService
from apiscope.core.exceptions import DefinitionError
from apiscope.definitions.collection import TestCollection
from apiscope.definitions.models import (
    DefinitionSource,
    DefinitionStatus,
    TestDefinition,
    TestRequestDefinition,
)
from apiscope.fixtures.entity_reference_detector import (
    DetectedEntityReference,
    EntityReferenceDetector,
)
from apiscope.fixtures.models import (
    FixturePreparationReport,
    StatefulWorkflow,
)
from apiscope.fixtures.resolver import FixtureResolver
from apiscope.fixtures.workflow_planner import StatefulWorkflowPlanner
from apiscope.generators.service import test_definition_to_dict
from apiscope.planning.models import ScenarioOrigin, TestPlan, TestScenario


class FixtureService:
    """Orchestrates test data resolution and stateful API workflow preparation."""

    __test__ = False

    def __init__(
        self,
        contract: CanonicalContract | None = None,
        contract_service: ContractAnalysisService | None = None,
    ) -> None:
        """Initialize FixtureService with optional contract context."""
        self.contract = contract
        self.contract_service = contract_service or ContractAnalysisService()

    def prepare_tests(
        self,
        plan: TestPlan | str | Path,
        spec_path: str | Path | None = None,
        contract_path: str | Path | None = None,
        intelligence_path: str | Path | None = None,
        fixture_file: str | Path | None = None,
        static_fixtures: dict[str, Any] | None = None,
        base_url_var: str = "{{base_url}}",
        allow_mutating_setup: bool = False,
        include_proposed: bool = False,
    ) -> tuple[TestCollection, FixturePreparationReport]:
        """Analyze fixtures and prepare executable stateful test collections.

        Args:
            plan: TestPlan model or path to test plan YAML/JSON.
            spec_path: Optional path to OpenAPI specification file.
            contract_path: Optional path to CanonicalContract file.
            intelligence_path: Optional path to ProjectIntelligenceReport file.
            fixture_file: Optional path to external fixtures YAML/JSON.
            static_fixtures: Optional dictionary of user-approved static test values.
            base_url_var: Base URL variable placeholder (default '{{base_url}}').
            allow_mutating_setup: Whether mutating setup actions are authorized.
            include_proposed: Whether to include unapproved AI scenarios.

        Returns:
            Tuple of (prepared TestCollection, FixturePreparationReport).
        """
        # Step 1: Resolve TestPlan model
        test_plan: TestPlan
        if isinstance(plan, (str, Path)):
            p_path = Path(plan)
            if not p_path.is_file():
                raise DefinitionError(f"Test plan file not found: '{plan}'")
            raw_data = (
                yaml.safe_load(p_path.read_text(encoding="utf-8"))
                if p_path.suffix in (".yaml", ".yml")
                else json.loads(p_path.read_text(encoding="utf-8"))
            )
            test_plan = TestPlan.from_dict(raw_data)
        else:
            test_plan = plan

        # Step 2: Resolve CanonicalContract and Project Intelligence
        contract = self.contract or self._resolve_contract(
            test_plan=test_plan,
            spec_path=spec_path,
            contract_path=contract_path,
        )
        self.contract = contract

        relationships: list[dict[str, Any]] = []
        if intelligence_path:
            intel_p = Path(intelligence_path)
            if intel_p.is_file():
                raw_intel = (
                    yaml.safe_load(intel_p.read_text(encoding="utf-8"))
                    if intel_p.suffix in (".yaml", ".yml")
                    else json.loads(intel_p.read_text(encoding="utf-8"))
                )
                relationships = raw_intel.get("relationships", [])

        # Step 3: Initialize Resolver, Detector, and Planner
        resolver = FixtureResolver(
            static_fixtures=static_fixtures,
            fixture_file=fixture_file,
        )
        resolved_values = resolver.get_all_resolved_values()

        detector = EntityReferenceDetector(
            contract=contract,
            intelligence_relationships=relationships,
        )
        planner = StatefulWorkflowPlanner(contract=contract)

        op_map = {f"{op.method.upper()} {op.path}": op for op in contract.operations}

        # Step 4: Evaluate each scenario for unverified placeholders vs verified
        # fixtures
        prepared_tests: list[TestDefinition] = []
        seen_test_names: set[str] = set()
        unverified_refs_list: list[dict[str, Any]] = []
        missing_fixtures_set: set[str] = set()

        fixture_ready_count = 0
        unresolved_fixture_count = 0
        unverified_placeholder_count = 0

        for scn in test_plan.scenarios:
            if (
                scn.origin == ScenarioOrigin.AI_PROPOSED
                and scn.approval_status != DefinitionStatus.APPROVED
                and not include_proposed
            ):
                unresolved_fixture_count += 1
                continue

            has_unresolved_data = False
            scenario_refs: list[DetectedEntityReference] = []

            for step in scn.steps:
                # 4.1. Check body and query params for entity references
                body = (
                    step.request_template.get("body") if step.request_template else None
                )
                params = (
                    step.request_template.get("parameters")
                    if step.request_template
                    else None
                )

                detected = detector.detect_references_in_payload(
                    body=body,
                    params=params,
                    resolved_fixtures=resolved_values,
                )
                scenario_refs.extend(detected)

                for ref in detected:
                    if ref.status == "UNVERIFIED":
                        has_unresolved_data = True
                        unverified_placeholder_count += 1
                        ref_dict = ref.to_dict()
                        ref_dict.update(
                            {
                                "scenario_id": scn.scenario_id,
                                "step": step.step_number,
                                "operation": f"{step.method.upper()} {step.path}",
                                "field": ref.field_name,
                                "placeholder_value": ref.current_value,
                                "issue": ref.description,
                            }
                        )
                        unverified_refs_list.append(ref_dict)
                        missing_fixtures_set.add(
                            f"Field '{ref.field_name}' ({ref.target_entity}) on "
                            f"{step.method.upper()} {step.path}"
                        )

                # 4.2. Check path parameters
                if "{" in step.path:
                    # In single step without producer, requires verified fixture
                    for part in step.path.split("/"):
                        if part.startswith("{") and part.endswith("}"):
                            param_name = part.strip("{}")
                            if (
                                param_name not in resolved_values
                                and f"fixture_{param_name}" not in resolved_values
                            ):
                                has_unresolved_data = True
                                missing_fixtures_set.add(
                                    f"Path parameter '{param_name}' on "
                                    f"{step.method.upper()} {step.path}"
                                )

            if has_unresolved_data:
                unresolved_fixture_count += 1
            else:
                fixture_ready_count += 1
                # Synthesize ready test definitions
                tests = self._synthesize_prepared_tests(
                    scenario=scn,
                    op_map=op_map,
                    resolved_values=resolved_values,
                    base_url_var=base_url_var,
                )
                for t in tests:
                    uname = t.name
                    if uname in seen_test_names:
                        uname = f"{t.name} [{scn.scenario_id}]"
                    seen_test_names.add(uname)
                    t.name = uname
                    prepared_tests.append(t)

        # Step 5: Plan representative stateful workflows for parameterized endpoints
        planned_workflows: list[StatefulWorkflow] = []
        for op in contract.operations:
            if "{" in op.path and op.method.upper() == "GET":
                wf = planner.plan_workflow_for_operation(
                    target_method=op.method,
                    target_path=op.path,
                    base_url_var=base_url_var,
                    mutation_authorized=allow_mutating_setup,
                )
                planned_workflows.append(wf)

        # Step 6: Construct Preparation Report
        report = FixturePreparationReport(
            plan_id=test_plan.plan_id,
            title=test_plan.title,
            total_scenarios_evaluated=len(test_plan.scenarios),
            fixture_ready_count=fixture_ready_count,
            unresolved_fixture_count=unresolved_fixture_count,
            unverified_placeholder_count=unverified_placeholder_count,
            workflows_planned_count=len(planned_workflows),
            resolved_fixtures=resolved_values,
            missing_fixtures=sorted(missing_fixtures_set),
            unverified_entity_references=unverified_refs_list,
            planned_workflows=planned_workflows,
            mutating_setup_allowed=allow_mutating_setup,
        )

        primary_module = (
            contract.title.lower().replace(" ", "-") if contract.title else "api"
        )
        collection = TestCollection(
            name=f"Prepared Suite: {test_plan.title}",
            module=primary_module,
            tags=["prepared-suite", "fixture-resolved"],
            tests=prepared_tests,
        )

        return collection, report

    def save_prepared_suite(
        self,
        collection: TestCollection,
        out_path: str | Path,
    ) -> None:
        """Serialize prepared TestCollection to YAML."""
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        col_dict = {
            "name": collection.name,
            "module": collection.module,
            "tags": collection.tags,
            "tests": [test_definition_to_dict(t) for t in collection.tests],
        }
        target.write_text(yaml.safe_dump(col_dict, sort_keys=False), encoding="utf-8")

    def save_preparation_report(
        self,
        report: FixturePreparationReport,
        report_path: str | Path,
    ) -> None:
        """Serialize FixturePreparationReport to JSON."""
        target = Path(report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")

    def _resolve_contract(
        self,
        test_plan: TestPlan,
        spec_path: str | Path | None,
        contract_path: str | Path | None,
    ) -> CanonicalContract:
        """Resolve or normalize CanonicalContract from file references."""
        effective_spec = spec_path
        if not effective_spec and test_plan.contract_ref:
            ref_p = Path(test_plan.contract_ref)
            if ref_p.is_file():
                effective_spec = ref_p

        if not effective_spec and not contract_path:
            raise DefinitionError(
                "Cannot prepare fixtures without a valid OpenAPI specification "
                "or contract reference. Please provide --spec or --contract."
            )

        if contract_path is not None:
            c_p = Path(contract_path)
            if not c_p.is_file():
                raise DefinitionError(f"Contract file not found: '{contract_path}'")
            raw_c = (
                yaml.safe_load(c_p.read_text(encoding="utf-8"))
                if c_p.suffix in (".yaml", ".yml")
                else json.loads(c_p.read_text(encoding="utf-8"))
            )
            raw_spec = (
                raw_c
                if "paths" in raw_c
                else self.contract_service._load_spec_file(effective_spec)
                if effective_spec
                else {}
            )
            return ContractNormalizer(raw_spec).normalize()

        raw_spec = self.contract_service._load_spec_file(effective_spec)
        return ContractNormalizer(raw_spec).normalize()

    def _synthesize_prepared_tests(
        self,
        scenario: TestScenario,
        op_map: dict[str, CanonicalOperation],
        resolved_values: dict[str, Any],
        base_url_var: str,
    ) -> list[TestDefinition]:
        """Synthesize TestDefinitions with resolved fixture values."""
        tests: list[TestDefinition] = []

        for step in scenario.steps:
            direct_sig = f"{step.method.upper()} {step.path}"
            op = op_map.get(direct_sig)
            target_module = scenario.target_module or (op.module if op else "default")

            # URL substitution
            url_path = step.path
            for k, v in resolved_values.items():
                clean_k = k.replace("fixture_", "").replace("chain_", "")
                url_path = url_path.replace(f"{{{clean_k}}}", str(v))
                url_path = url_path.replace(f"{{{k}}}", str(v))

            full_url = f"{base_url_var}{url_path}"

            # Headers and Body
            headers: dict[str, Any] = {}
            body: dict[str, Any] | None = None
            if step.method.upper() in ("POST", "PUT", "PATCH"):
                headers["Content-Type"] = "application/json"
                raw_body = (
                    step.request_template.get("body") if step.request_template else None
                )
                if isinstance(raw_body, dict):
                    body = dict(raw_body)
                    for k, v in resolved_values.items():
                        clean_k = k.replace("fixture_", "").replace("chain_", "")
                        if clean_k in body:
                            body[clean_k] = v
                        if k in body:
                            body[k] = v

            # Method-based risk tag enforcement
            clean_m = step.method.upper()
            risk_tag = (
                "risk:destructive"
                if clean_m == "DELETE"
                else "risk:mutating"
                if clean_m in ("POST", "PUT", "PATCH")
                else "risk:read_only"
            )

            expected_code = (
                int(step.expected_status[0]) if step.expected_status else 200
            )

            test = TestDefinition(
                name=f"[{scenario.scenario_id}] {step.method.upper()} {step.path}",
                module=target_module,
                status=scenario.approval_status,
                source=DefinitionSource.RULE_GENERATED,
                request=TestRequestDefinition(
                    method=step.method.upper(),
                    url=full_url,
                    headers=headers,
                    body=body,
                ),
                assertions=[{"type": "status", "equals": expected_code}],
                tags=[
                    target_module,
                    scenario.category.value.lower(),
                    f"priority:{scenario.priority.value.lower()}",
                    risk_tag,
                    "fixture_resolved",
                ],
            )
            tests.append(test)

        return tests
