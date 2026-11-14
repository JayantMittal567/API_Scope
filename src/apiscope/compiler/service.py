"""Test Plan Compiler Service (Task 19).

Compiles validated Task 18 TestPlans into native ApiScope executable YAML collections
while rigorously validating test oracles, detecting missing fixture dependencies,
enforcing approval governance, and preventing security bypasses.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from apiscope.compiler.fixture_analyzer import FixtureAnalyzer
from apiscope.compiler.models import (
    CompilationReport,
    CompilationStatus,
    ScenarioCompilationResult,
)
from apiscope.compiler.oracle_validator import OracleValidator
from apiscope.contract.models import (
    CanonicalContract,
    CanonicalOperation,
    OperationReadiness,
)
from apiscope.contract.normalizer import ContractNormalizer
from apiscope.contract.service import ContractAnalysisService
from apiscope.core.exceptions import DefinitionError
from apiscope.core.models import BodyType, HttpMethod
from apiscope.definitions.collection import TestCollection
from apiscope.definitions.models import (
    DefinitionSource,
    DefinitionStatus,
    TestDefinition,
    TestRequestDefinition,
)
from apiscope.generators.service import test_definition_to_dict
from apiscope.planning.models import (
    ScenarioCategory,
    ScenarioOrigin,
    TestPlan,
    TestScenario,
)


def _risk_tag_for_method(method: str) -> str:
    """Derive strict risk tag from HTTP method to prevent security policy downgrades."""
    norm = (method or "").strip().upper()
    if norm == "DELETE":
        return "risk:destructive"
    if norm in ("POST", "PUT", "PATCH"):
        return "risk:mutating"
    return "risk:read_only"


class TestPlanCompiler:
    """Compiles abstract TestPlan scenarios into native ApiScope test suites."""

    __test__ = False

    def __init__(
        self,
        contract: CanonicalContract | None = None,
        contract_service: ContractAnalysisService | None = None,
    ) -> None:
        """Initialize TestPlanCompiler with optional contract context."""
        self.contract = contract
        self.contract_service = contract_service or ContractAnalysisService()
        self.oracle_validator: OracleValidator | None = (
            OracleValidator(contract) if contract else None
        )
        self.fixture_analyzer: FixtureAnalyzer | None = (
            FixtureAnalyzer(contract) if contract else None
        )

    def compile_plan(
        self,
        plan: TestPlan | str | Path,
        spec_path: str | Path | None = None,
        contract_path: str | Path | None = None,
        intelligence_path: str | Path | None = None,
        base_url_var: str = "{{base_url}}",
        include_proposed: bool = False,
    ) -> tuple[TestCollection, CompilationReport]:
        """Compile a TestPlan into a native TestCollection and CompilationReport.

        Args:
            plan: TestPlan model instance or path to test plan YAML/JSON.
            spec_path: Optional path to OpenAPI spec file.
            contract_path: Optional path to CanonicalContract / quality report.
            intelligence_path: Optional path to ProjectIntelligenceReport.
            base_url_var: Base URL variable placeholder (default '{{base_url}}').
            include_proposed: Whether to allow proposed scenarios to compile
                as PROPOSED.

        Returns:
            Tuple of (TestCollection containing COMPILED tests, CompilationReport).
        """
        # Step 1: Resolve TestPlan model
        test_plan: TestPlan
        if isinstance(plan, (str, Path)):
            p_path = Path(plan)
            if not p_path.is_file():
                raise DefinitionError(f"Test plan file not found: '{plan}'")
            raw_text = p_path.read_text(encoding="utf-8")
            data = (
                yaml.safe_load(raw_text)
                if p_path.suffix.lower() in (".yaml", ".yml")
                else json.loads(raw_text)
            )
            test_plan = TestPlan.from_dict(data)
        else:
            test_plan = plan

        # Step 2: Resolve CanonicalContract context
        contract = self.contract or self._resolve_contract(
            test_plan=test_plan,
            spec_path=spec_path,
            contract_path=contract_path,
            intelligence_path=intelligence_path,
        )
        self.contract = contract
        self.oracle_validator = OracleValidator(contract)

        intel_rels: list[dict[str, Any]] = []
        if intelligence_path:
            ip = Path(intelligence_path)
            if ip.is_file():
                try:
                    raw_i = (
                        yaml.safe_load(ip.read_text(encoding="utf-8"))
                        if ip.suffix in (".yaml", ".yml")
                        else json.loads(ip.read_text(encoding="utf-8"))
                    )
                    if isinstance(raw_i, dict):
                        intel_rels = raw_i.get("inferred_relationships", [])
                except Exception:
                    intel_rels = []

        self.fixture_analyzer = FixtureAnalyzer(
            contract,
            intelligence_relationships=intel_rels,
        )

        op_map = {f"{op.method.upper()} {op.path}": op for op in contract.operations}

        # Step 3: Evaluate each scenario
        results: list[ScenarioCompilationResult] = []
        all_compiled_tests: list[TestDefinition] = []
        seen_test_names: set[str] = set()
        unresolved_fixtures_set: set[str] = set()

        compiled_count = 0
        needs_review_count = 0
        needs_fixture_count = 0
        blocked_count = 0
        scenarios_by_category: dict[str, int] = {}
        scenarios_by_origin: dict[str, int] = {}

        for scn in test_plan.scenarios:
            scenarios_by_category[scn.category.value] = (
                scenarios_by_category.get(scn.category.value, 0) + 1
            )
            scenarios_by_origin[scn.origin.value] = (
                scenarios_by_origin.get(scn.origin.value, 0) + 1
            )

            status = CompilationStatus.COMPILED
            reasons: list[str] = []
            missing_fixtures: list[str] = []
            compiled_names: list[str] = []

            # 3.1. Endpoint Contract Validation
            is_blocked = False
            for step in scn.steps:
                op = self._find_operation(step.method, step.path, op_map)
                if op is None:
                    status = CompilationStatus.BLOCKED
                    reasons.append(
                        f"Step {step.step_number}: Referenced endpoint "
                        f"'{step.method.upper()} {step.path}' is not declared "
                        f"in OpenAPI contract."
                    )
                    is_blocked = True
                    break
                if op.readiness == OperationReadiness.BLOCKED:
                    status = CompilationStatus.BLOCKED
                    reasons.append(
                        f"Step {step.step_number}: Operation '{op.method} {op.path}' "
                        f"is blocked by contract defects: "
                        f"{', '.join(op.readiness_reasons)}."
                    )
                    is_blocked = True
                    break

            # 3.2. Oracle Defensibility Check
            if not is_blocked:
                is_defensible, review_reasons = (
                    self.oracle_validator.validate_scenario_oracles(
                        scn, include_proposed=include_proposed
                    )
                )
                if not is_defensible:
                    status = CompilationStatus.NEEDS_REVIEW
                    reasons.extend(review_reasons)

            # 3.3. AI Approval Governance Check
            if (
                not is_blocked
                and scn.origin == ScenarioOrigin.AI_PROPOSED
                and scn.approval_status != DefinitionStatus.APPROVED
                and not include_proposed
            ):
                status = CompilationStatus.NEEDS_REVIEW
                msg = (
                    "AI-proposed scenario requires explicit human review and approval."
                )
                if msg not in reasons:
                    reasons.append(msg)

            # 3.4. Fixture and Dependency Validation
            if not is_blocked:
                has_unresolved, unres_list = (
                    self.fixture_analyzer.analyze_scenario_fixtures(scn)
                )
                if has_unresolved:
                    missing_fixtures.extend(unres_list)
                    reasons.extend(unres_list)
                    unresolved_fixtures_set.update(unres_list)
                    if status == CompilationStatus.COMPILED:
                        status = CompilationStatus.NEEDS_FIXTURE

            # 3.5. Native Test Synthesis for COMPILED scenarios
            if status == CompilationStatus.COMPILED:
                synthesized = self._synthesize_tests(scn, op_map, base_url_var)
                for test_def in synthesized:
                    # Deduplicate test names deterministically
                    unique_name = test_def.name
                    if unique_name in seen_test_names:
                        unique_name = f"{test_def.name} [{scn.scenario_id}]"
                    seen_test_names.add(unique_name)
                    test_def.name = unique_name

                    all_compiled_tests.append(test_def)
                    compiled_names.append(unique_name)
                compiled_count += 1
            elif status == CompilationStatus.NEEDS_FIXTURE:
                needs_fixture_count += 1
            elif status == CompilationStatus.NEEDS_REVIEW:
                needs_review_count += 1
            elif status == CompilationStatus.BLOCKED:
                blocked_count += 1

            results.append(
                ScenarioCompilationResult(
                    scenario_id=scn.scenario_id,
                    title=scn.title,
                    category=scn.category.value,
                    origin=scn.origin.value,
                    priority=scn.priority.value,
                    risk=scn.risk_classification.value,
                    status=status,
                    reasons=reasons,
                    compiled_test_names=compiled_names,
                    missing_fixtures=missing_fixtures,
                )
            )

        # Step 4: Build compilation report
        report = CompilationReport(
            plan_id=test_plan.plan_id,
            title=test_plan.title,
            total_scenarios=len(test_plan.scenarios),
            compiled_count=compiled_count,
            needs_review_count=needs_review_count,
            needs_fixture_count=needs_fixture_count,
            blocked_count=blocked_count,
            total_tests_generated=len(all_compiled_tests),
            scenarios_by_category=scenarios_by_category,
            scenarios_by_origin=scenarios_by_origin,
            unresolved_prerequisites=sorted(unresolved_fixtures_set),
            results=results,
        )

        # Step 5: Construct native TestCollection
        primary_module = (
            contract.title.lower().replace(" ", "-") if contract.title else "api"
        )
        collection = TestCollection(
            name=f"Compiled Suite: {test_plan.title}",
            module=primary_module,
            tags=["compiled-suite"],
            tests=all_compiled_tests,
        )

        return collection, report

    def save_compiled_suite(
        self,
        collection: TestCollection,
        out_path: str | Path,
    ) -> None:
        """Serialize compiled TestCollection into valid native YAML."""
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        coll_dict: dict[str, Any] = {
            "name": collection.name,
            "module": collection.module,
            "tags": list(collection.tags),
            "tests": [test_definition_to_dict(t) for t in collection.tests],
        }
        yaml_text = yaml.safe_dump(coll_dict, sort_keys=False)
        target.write_text(yaml_text, encoding="utf-8")

    def save_compilation_report(
        self,
        report: CompilationReport,
        report_path: str | Path,
    ) -> None:
        """Serialize CompilationReport to JSON."""
        target = Path(report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")

    def _resolve_contract(
        self,
        test_plan: TestPlan,
        spec_path: str | Path | None,
        contract_path: str | Path | None,
        intelligence_path: str | Path | None,
    ) -> CanonicalContract:
        """Resolve or analyze CanonicalContract from available file references."""
        _ = intelligence_path
        effective_spec = spec_path
        if not effective_spec and test_plan.contract_ref:
            ref_p = Path(test_plan.contract_ref)
            if ref_p.is_file():
                effective_spec = ref_p

        if not effective_spec and not contract_path:
            raise DefinitionError(
                "Cannot compile TestPlan without a valid OpenAPI specification "
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

    def _find_operation(
        self,
        method: str,
        path: str,
        op_map: dict[str, CanonicalOperation],
    ) -> CanonicalOperation | None:
        """Match an HTTP interaction step to a CanonicalOperation in the contract."""
        clean_method = method.upper()
        direct_sig = f"{clean_method} {path}"
        if direct_sig in op_map:
            return op_map[direct_sig]

        # Template regex match
        for sig, op in op_map.items():
            if not sig.startswith(clean_method + " "):
                continue
            escaped = re.escape(op.path)
            pattern = "^" + re.sub(r"\\\{[^}]+\\\}", r"[^/]+", escaped) + "$"
            if re.fullmatch(pattern, path):
                return op

        return None

    def _synthesize_tests(
        self,
        scenario: TestScenario,
        op_map: dict[str, CanonicalOperation],
        base_url_var: str,
    ) -> list[TestDefinition]:
        """Synthesize native TestDefinition instances for a COMPILED scenario."""
        tests: list[TestDefinition] = []
        is_multi_step = len(scenario.steps) > 1

        for idx, step in enumerate(scenario.steps, start=1):
            op = self._find_operation(step.method, step.path, op_map)
            target_module = scenario.target_module or (op.module if op else "default")
            expected_code = (
                int(step.expected_status[0]) if step.expected_status else 200
            )

            # Step title
            step_desc = f" ({step.description})" if step.description else ""
            step_name = (
                f"[{scenario.scenario_id}] {step.method.upper()} {step.path}"
                if not is_multi_step
                else f"[{scenario.scenario_id}:{idx}] {step.method.upper()} "
                f"{step.path}{step_desc}"
            )

            # URL construction with chaining variable replacement
            url_path = step.path
            if is_multi_step and idx > 1:
                # Replace path parameter with chained variable from producer step
                url_path = re.sub(r"\{([^}]+)\}", r"{{chain_\1}}", url_path)
            full_url = f"{base_url_var}{url_path}"

            # Headers & Body
            headers: dict[str, Any] = {}
            body = None
            if step.request_template:
                if "headers" in step.request_template:
                    headers.update(step.request_template["headers"])
                if "body" in step.request_template:
                    body = step.request_template["body"]
                    if body and "Content-Type" not in headers:
                        headers["Content-Type"] = "application/json"

            request_def = TestRequestDefinition(
                method=HttpMethod.from_string(step.method),
                url=full_url,
                headers=headers,
                params=dict(step.request_template.get("parameters", {}))
                if "{" not in step.path
                else {},
                body=body,
                body_type=BodyType.JSON,
            )

            # Assertions
            assertions: list[dict[str, Any]] = [
                {"type": "status", "equals": expected_code}
            ]

            # Response schema assertion if requested
            if scenario.category == ScenarioCategory.RESPONSE_SCHEMA and op is not None:
                resp = op.responses.get(str(expected_code))
                if resp:
                    if resp.schema:
                        assertions.append(
                            {"type": "json_schema", "schema": resp.schema}
                        )
                    elif resp.is_array:
                        assertions.append(
                            {"type": "json_schema", "schema": {"type": "array"}}
                        )
                    else:
                        assertions.append(
                            {"type": "json_schema", "schema": {"type": "object"}}
                        )

            # Extraction for multi-step producer
            extract: dict[str, str] = {}
            if is_multi_step and idx == 1 and step.method.upper() in ("POST", "PUT"):
                # Detect target path param for consumer step
                consumer_param = "id"
                if len(scenario.steps) > 1:
                    match = re.search(r"\{([^}]+)\}", scenario.steps[1].path)
                    if match:
                        consumer_param = match.group(1)
                extract[f"chain_{consumer_param}"] = "$.id"

            # Strict risk tag enforcement
            risk_tag = _risk_tag_for_method(step.method)
            tags = [
                target_module,
                scenario.category.value.lower(),
                f"priority:{scenario.priority.value.lower()}",
                risk_tag,
                scenario.origin.value.lower().replace("_", "-"),
            ]

            source = (
                DefinitionSource.AI_GENERATED
                if scenario.origin == ScenarioOrigin.AI_PROPOSED
                else DefinitionSource.RULE_GENERATED
            )

            tests.append(
                TestDefinition(
                    name=step_name,
                    module=target_module,
                    request=request_def,
                    tags=tags,
                    extract=extract,
                    assertions=assertions,
                    source=source,
                    status=scenario.approval_status,
                )
            )

        return tests
