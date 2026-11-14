"""Unit tests for ApiScope Test Plan Compiler (Task 19).

Verifies hybrid executable test synthesis, evidence-gated compilation,
strict oracle defensibility, fixture dependency analysis, multi-step chaining,
AI proposal gating, execution safety risk tagging, and YAML persistence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apiscope.compiler.fixture_analyzer import FixtureAnalyzer
from apiscope.compiler.models import (
    CompilationReport,
    CompilationStatus,
    ScenarioCompilationResult,
)
from apiscope.compiler.oracle_validator import OracleValidator
from apiscope.compiler.service import TestPlanCompiler
from apiscope.contract.models import (
    CanonicalContract,
    CanonicalOperation,
    CanonicalParameter,
    CanonicalResponse,
    OperationReadiness,
)
from apiscope.core.executor import ExecutionSafetyPolicy
from apiscope.core.run_service import RunService
from apiscope.definitions.collection import load_collection
from apiscope.definitions.environment import Environment
from apiscope.definitions.models import DefinitionStatus
from apiscope.planning.models import (
    RiskClassification,
    ScenarioCategory,
    ScenarioOrigin,
    ScenarioPriority,
    ScenarioStep,
    TestPlan,
    TestScenario,
)

# =====================================================================
# FIXTURES & HELPERS
# =====================================================================


@pytest.fixture
def sample_contract() -> CanonicalContract:
    """Provide a minimal CanonicalContract with items and widgets."""
    op_get_items = CanonicalOperation(
        method="GET",
        path="/api/items",
        summary="List all items",
        module="items",
        parameters=[],
        responses={
            "200": CanonicalResponse(
                status_code="200",
                description="Success",
                schema={
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}},
                    },
                },
            )
        },
        readiness=OperationReadiness.READY,
    )

    op_post_items = CanonicalOperation(
        method="POST",
        path="/api/items",
        summary="Create an item",
        module="items",
        parameters=[],
        responses={
            "201": CanonicalResponse(
                status_code="201",
                description="Created",
                schema={
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                    },
                },
            )
        },
        readiness=OperationReadiness.READY,
    )

    op_get_item_by_id = CanonicalOperation(
        method="GET",
        path="/api/items/{id}",
        summary="Get item by ID",
        module="items",
        parameters=[
            CanonicalParameter(
                name="id", location="path", required=True, schema={"type": "integer"}
            )
        ],
        responses={
            "200": CanonicalResponse(
                status_code="200",
                description="Success",
                schema={"type": "object", "properties": {"id": {"type": "integer"}}},
            ),
            "404": CanonicalResponse(status_code="404", description="Item not found"),
        },
        readiness=OperationReadiness.READY,
    )

    op_delete_item = CanonicalOperation(
        method="DELETE",
        path="/api/items/{id}",
        summary="Delete an item",
        module="items",
        parameters=[
            CanonicalParameter(
                name="id", location="path", required=True, schema={"type": "integer"}
            )
        ],
        responses={
            "204": CanonicalResponse(
                status_code="204", description="Deleted successfully"
            )
        },
        readiness=OperationReadiness.READY,
    )

    op_blocked = CanonicalOperation(
        method="PUT",
        path="/api/items/{id}",
        summary="Update item",
        module="items",
        parameters=[
            CanonicalParameter(
                name="id", location="path", required=True, schema={"type": "integer"}
            )
        ],
        responses={},
        readiness=OperationReadiness.BLOCKED,
        readiness_reasons=["Missing request body and response specifications."],
    )

    return CanonicalContract(
        title="Items API",
        version="1.0.0",
        openapi_version="3.0.3",
        operations=[
            op_get_items,
            op_post_items,
            op_get_item_by_id,
            op_delete_item,
            op_blocked,
        ],
    )


def make_simple_plan(scenarios: list[TestScenario]) -> TestPlan:
    """Helper to create a TestPlan instance."""
    return TestPlan(
        plan_id="PLAN-TEST-001",
        title="Unit Test Plan",
        version="1.0.0",
        contract_ref="tests/openapi.yaml",
        scenarios=scenarios,
    )


# =====================================================================
# DATA MODEL & SERIALIZATION TESTS
# =====================================================================


def test_compilation_models_serialization_round_trip() -> None:
    """Verify ScenarioCompilationResult and CompilationReport to/from dict."""
    result = ScenarioCompilationResult(
        scenario_id="SCN-001",
        title="Test Scenario",
        category="CONTRACT_POSITIVE",
        origin="DETERMINISTIC",
        priority="HIGH",
        risk="LOW",
        status=CompilationStatus.COMPILED,
        reasons=["All expectations verified"],
        compiled_test_names=["[SCN-001] GET /api/items"],
        missing_fixtures=[],
    )
    r_dict = result.to_dict()
    restored_r = ScenarioCompilationResult.from_dict(r_dict)
    assert restored_r.scenario_id == "SCN-001"
    assert restored_r.status == CompilationStatus.COMPILED
    assert restored_r.compiled_test_names == ["[SCN-001] GET /api/items"]

    report = CompilationReport(
        plan_id="PLAN-001",
        title="Sample Plan",
        total_scenarios=1,
        compiled_count=1,
        needs_review_count=0,
        needs_fixture_count=0,
        blocked_count=0,
        total_tests_generated=1,
        unresolved_prerequisites=["needs_id"],
        results=[result],
    )
    rep_dict = report.to_dict()
    restored_rep = CompilationReport.from_dict(rep_dict)
    assert restored_rep.plan_id == "PLAN-001"
    assert restored_rep.title == "Sample Plan"
    assert restored_rep.compiled_count == 1
    assert restored_rep.unresolved_fixtures == ["needs_id"]
    assert len(restored_rep.results) == 1


# =====================================================================
# ORACLE VALIDATOR TESTS
# =====================================================================


def test_oracle_validator_defensible_contract_operation(
    sample_contract: CanonicalContract,
) -> None:
    """Verify standard contract operation with documented response passes."""
    validator = OracleValidator(sample_contract)
    scenario = TestScenario(
        scenario_id="SCN-001",
        title="Get Items",
        description="Verify GET items returns 200",
        category=ScenarioCategory.CONTRACT_POSITIVE,
        origin=ScenarioOrigin.DETERMINISTIC,
        priority=ScenarioPriority.HIGH,
        risk_classification=RiskClassification.LOW,
        steps=[
            ScenarioStep(
                step_number=1,
                method="GET",
                path="/api/items",
                description="List all items",
                expected_status=["200"],
            )
        ],
    )
    is_defensible, reasons = validator.validate_scenario_oracles(scenario)
    assert is_defensible is True
    assert len(reasons) == 0


def test_oracle_validator_synthetic_404_needs_review(
    sample_contract: CanonicalContract,
) -> None:
    """Verify synthetic 404 expectation on nonexistent ID requires human review."""
    validator = OracleValidator(sample_contract)
    scenario = TestScenario(
        scenario_id="SCN-002",
        title="Get Nonexistent Item",
        description="Probes synthetic ID 999999 expecting 404",
        category=ScenarioCategory.RESOURCE_EXISTENCE,
        origin=ScenarioOrigin.DETERMINISTIC,
        priority=ScenarioPriority.LOW,
        risk_classification=RiskClassification.LOW,
        steps=[
            ScenarioStep(
                step_number=1,
                method="GET",
                path="/api/items/999999",
                description="Probe synthetic ID",
                expected_status=["404"],
            )
        ],
    )
    is_defensible, reasons = validator.validate_scenario_oracles(scenario)
    assert is_defensible is False
    assert any("Probing synthetic identifier '999999'" in r for r in reasons)


def test_oracle_validator_undocumented_400_needs_review(
    sample_contract: CanonicalContract,
) -> None:
    """Verify error handling expectation flagged when 400 not declared in OpenAPI."""
    validator = OracleValidator(sample_contract)
    scenario = TestScenario(
        scenario_id="SCN-003",
        title="Malformed JSON",
        description="Expect 400 on malformed body",
        category=ScenarioCategory.ERROR_HANDLING,
        origin=ScenarioOrigin.DETERMINISTIC,
        priority=ScenarioPriority.LOW,
        risk_classification=RiskClassification.LOW,
        steps=[
            ScenarioStep(
                step_number=1,
                method="POST",
                path="/api/items",
                description="Send malformed payload",
                expected_status=["400"],
            )
        ],
    )
    is_defensible, reasons = validator.validate_scenario_oracles(scenario)
    assert is_defensible is False
    assert any("HTTP 400 Bad Request is not declared" in r for r in reasons)


def test_oracle_validator_undocumented_auth_needs_review(
    sample_contract: CanonicalContract,
) -> None:
    """Verify undocumented mutation security probe flagged for human review."""
    validator = OracleValidator(sample_contract)
    scenario = TestScenario(
        scenario_id="SCN-004",
        title="Auth Probe",
        description="Probe undocumented mutation endpoint auth",
        category=ScenarioCategory.AUTH_SECURITY,
        origin=ScenarioOrigin.DETERMINISTIC,
        priority=ScenarioPriority.HIGH,
        risk_classification=RiskClassification.HIGH,
        steps=[
            ScenarioStep(
                step_number=1,
                method="POST",
                path="/api/items",
                description="Check unauthorized response",
                expected_status=["401", "403"],
            )
        ],
    )
    is_defensible, reasons = validator.validate_scenario_oracles(scenario)
    assert is_defensible is False
    assert any("relies on unverified authentication inference" in r for r in reasons)


def test_oracle_validator_consistency_needs_review(
    sample_contract: CanonicalContract,
) -> None:
    """Verify consistency workflow flagged for review when not formally specified."""
    validator = OracleValidator(sample_contract)
    scenario = TestScenario(
        scenario_id="SCN-005",
        title="Consistency Workflow",
        description="Verify read-after-write consistency",
        category=ScenarioCategory.CONSISTENCY_CHECK,
        origin=ScenarioOrigin.DETERMINISTIC,
        priority=ScenarioPriority.MEDIUM,
        risk_classification=RiskClassification.MEDIUM,
        steps=[
            ScenarioStep(
                step_number=1,
                method="POST",
                path="/api/items",
                description="Create item",
                expected_status=["201"],
            ),
            ScenarioStep(
                step_number=2,
                method="GET",
                path="/api/items/{id}",
                description="Read item immediately",
                expected_status=["200"],
            ),
        ],
    )
    is_defensible, reasons = validator.validate_scenario_oracles(scenario)
    assert is_defensible is False
    assert any("consistency is not formally specified" in r for r in reasons)


# =====================================================================
# FIXTURE ANALYZER TESTS
# =====================================================================


def test_fixture_analyzer_unchained_path_parameter(
    sample_contract: CanonicalContract,
) -> None:
    """Verify single-step path with {id} flags missing fixture."""
    analyzer = FixtureAnalyzer(sample_contract)
    scenario = TestScenario(
        scenario_id="SCN-010",
        title="Get Item By ID",
        description="Read item by parameterized ID",
        category=ScenarioCategory.CONTRACT_POSITIVE,
        origin=ScenarioOrigin.DETERMINISTIC,
        priority=ScenarioPriority.HIGH,
        risk_classification=RiskClassification.LOW,
        missing_dependencies=["needs_existing_resource_id for path '/api/items/{id}'"],
        steps=[
            ScenarioStep(
                step_number=1,
                method="GET",
                path="/api/items/{id}",
                description="Get item by ID",
                expected_status=["200"],
            )
        ],
    )
    has_unresolved, fixtures = analyzer.analyze_scenario_fixtures(scenario)
    assert has_unresolved is True
    assert any("Path parameter '{id}'" in f for f in fixtures)


def test_fixture_analyzer_resolved_by_producer_step(
    sample_contract: CanonicalContract,
) -> None:
    """Verify multi-step scenario where POST produces entity resolves ID dependency."""
    analyzer = FixtureAnalyzer(sample_contract)
    scenario = TestScenario(
        scenario_id="SCN-011",
        title="Create Then Read",
        description="Create then read item",
        category=ScenarioCategory.CONTRACT_POSITIVE,
        origin=ScenarioOrigin.DETERMINISTIC,
        priority=ScenarioPriority.HIGH,
        risk_classification=RiskClassification.HIGH,
        missing_dependencies=["needs_created_id"],
        steps=[
            ScenarioStep(
                step_number=1,
                method="POST",
                path="/api/items",
                description="Create item",
                expected_status=["201"],
            ),
            ScenarioStep(
                step_number=2,
                method="GET",
                path="/api/items/{id}",
                description="Read created item",
                expected_status=["200"],
            ),
        ],
    )
    has_unresolved, fixtures = analyzer.analyze_scenario_fixtures(scenario)
    assert has_unresolved is False
    assert len(fixtures) == 0


def test_fixture_analyzer_ignores_security_auth_policy_dependencies(
    sample_contract: CanonicalContract,
) -> None:
    """Verify authorization/security review dependencies are not counted as fixtures."""
    analyzer = FixtureAnalyzer(sample_contract)
    scenario = TestScenario(
        scenario_id="SCN-012",
        title="Auth Probe",
        description="Probe authentication",
        category=ScenarioCategory.AUTH_SECURITY,
        origin=ScenarioOrigin.DETERMINISTIC,
        priority=ScenarioPriority.HIGH,
        risk_classification=RiskClassification.HIGH,
        missing_dependencies=[
            "target_environment_authorization",
            "undocumented_mutation_security (verify auth requirement)",
        ],
        steps=[
            ScenarioStep(
                step_number=1,
                method="POST",
                path="/api/items",
                description="Probe auth",
                expected_status=["401"],
            )
        ],
    )
    has_unresolved, fixtures = analyzer.analyze_scenario_fixtures(scenario)
    assert has_unresolved is False
    assert len(fixtures) == 0


# =====================================================================
# COMPILER SERVICE TESTS
# =====================================================================


def test_compiler_compiles_parameterless_get(
    sample_contract: CanonicalContract,
) -> None:
    """Verify parameterless GET compiles to valid executable native test."""
    compiler = TestPlanCompiler(contract=sample_contract)
    scenario = TestScenario(
        scenario_id="SCN-101",
        title="List Items",
        description="List all items",
        category=ScenarioCategory.CONTRACT_POSITIVE,
        origin=ScenarioOrigin.DETERMINISTIC,
        priority=ScenarioPriority.HIGH,
        risk_classification=RiskClassification.LOW,
        approval_status=DefinitionStatus.APPROVED,
        steps=[
            ScenarioStep(
                step_number=1,
                method="GET",
                path="/api/items",
                description="List items",
                expected_status=["200"],
            )
        ],
    )
    plan = make_simple_plan([scenario])
    suite, report = compiler.compile_plan(plan)

    assert report.total_scenarios == 1
    assert report.compiled_count == 1
    assert report.total_tests_generated == 1
    assert len(suite.tests) == 1

    test = suite.tests[0]
    assert test.name == "[SCN-101] GET /api/items"
    assert test.request.method.value == "GET"
    assert test.request.url == "{{base_url}}/api/items"
    assert test.status == DefinitionStatus.APPROVED
    assert "risk:read_only" in test.tags
    assert any(
        isinstance(a, dict) and a.get("type") == "status" and a.get("equals") == 200
        for a in test.assertions
    )


def test_compiler_risk_classification_tag_enforcement(
    sample_contract: CanonicalContract,
) -> None:
    """Verify HTTP methods enforce strict risk tags regardless of scenario metadata."""
    compiler = TestPlanCompiler(contract=sample_contract)
    scn_post = TestScenario(
        scenario_id="SCN-POST",
        title="Post Item",
        description="Create an item",
        category=ScenarioCategory.CONTRACT_POSITIVE,
        origin=ScenarioOrigin.DETERMINISTIC,
        priority=ScenarioPriority.HIGH,
        risk_classification=RiskClassification.LOW,  # Untrusted downgrade attempt
        approval_status=DefinitionStatus.APPROVED,
        steps=[
            ScenarioStep(
                step_number=1,
                method="POST",
                path="/api/items",
                description="Create item",
                expected_status=["201"],
            )
        ],
    )
    plan = make_simple_plan([scn_post])
    suite, _ = compiler.compile_plan(plan)

    assert len(suite.tests) == 1
    post_test = suite.tests[0]
    # Compiler strictly tags POST as risk:mutating
    assert "risk:mutating" in post_test.tags
    assert "risk:read_only" not in post_test.tags


def test_compiler_unresolved_fixture_classified_needs_fixture(
    sample_contract: CanonicalContract,
) -> None:
    """Verify scenario with unchained {id} is deferred as NEEDS_FIXTURE."""
    compiler = TestPlanCompiler(contract=sample_contract)
    scenario = TestScenario(
        scenario_id="SCN-FIX-01",
        title="Get Item By ID",
        description="Read item by ID",
        category=ScenarioCategory.CONTRACT_POSITIVE,
        origin=ScenarioOrigin.DETERMINISTIC,
        priority=ScenarioPriority.HIGH,
        risk_classification=RiskClassification.LOW,
        approval_status=DefinitionStatus.APPROVED,
        missing_dependencies=["needs_existing_resource_id for path '/api/items/{id}'"],
        steps=[
            ScenarioStep(
                step_number=1,
                method="GET",
                path="/api/items/{id}",
                description="Read item",
                expected_status=["200"],
            )
        ],
    )
    plan = make_simple_plan([scenario])
    suite, report = compiler.compile_plan(plan)

    assert report.compiled_count == 0
    assert report.needs_fixture_count == 1
    assert len(suite.tests) == 0
    assert report.results[0].status == CompilationStatus.NEEDS_FIXTURE
    assert len(report.results[0].missing_fixtures) > 0


def test_compiler_unapproved_ai_scenario_classified_needs_review(
    sample_contract: CanonicalContract,
) -> None:
    """Verify AI-proposed scenario requires approval and defaults to NEEDS_REVIEW."""
    compiler = TestPlanCompiler(contract=sample_contract)
    scenario = TestScenario(
        scenario_id="SCN-AI-01",
        title="AI Proposed List",
        description="AI proposed scenario",
        category=ScenarioCategory.CONTRACT_POSITIVE,
        origin=ScenarioOrigin.AI_PROPOSED,
        priority=ScenarioPriority.MEDIUM,
        risk_classification=RiskClassification.LOW,
        approval_status=DefinitionStatus.PROPOSED,  # Not approved!
        steps=[
            ScenarioStep(
                step_number=1,
                method="GET",
                path="/api/items",
                description="List items",
                expected_status=["200"],
            )
        ],
    )
    plan = make_simple_plan([scenario])
    suite, report = compiler.compile_plan(plan, include_proposed=False)

    assert report.compiled_count == 0
    assert report.needs_review_count == 1
    assert report.results[0].status == CompilationStatus.NEEDS_REVIEW
    assert any(
        "AI-proposed scenario requires explicit human review" in r
        for r in report.results[0].reasons
    )

    # When include_proposed=True, compiles as COMPILED with status PROPOSED
    suite_inc, report_inc = compiler.compile_plan(plan, include_proposed=True)
    assert report_inc.compiled_count == 1
    assert suite_inc.tests[0].status == DefinitionStatus.PROPOSED


def test_compiler_blocked_when_endpoint_not_in_contract(
    sample_contract: CanonicalContract,
) -> None:
    """Verify scenario referencing unknown endpoint is classified as BLOCKED."""
    compiler = TestPlanCompiler(contract=sample_contract)
    scenario = TestScenario(
        scenario_id="SCN-BLK-01",
        title="Unknown Endpoint",
        description="Target unknown endpoint",
        category=ScenarioCategory.CONTRACT_POSITIVE,
        origin=ScenarioOrigin.DETERMINISTIC,
        priority=ScenarioPriority.HIGH,
        risk_classification=RiskClassification.LOW,
        approval_status=DefinitionStatus.APPROVED,
        steps=[
            ScenarioStep(
                step_number=1,
                method="GET",
                path="/api/nonexistent",
                description="Unknown path",
                expected_status=["200"],
            )
        ],
    )
    plan = make_simple_plan([scenario])
    suite, report = compiler.compile_plan(plan)

    assert report.compiled_count == 0
    assert report.blocked_count == 1
    assert report.results[0].status == CompilationStatus.BLOCKED
    assert any(
        "not declared in OpenAPI contract" in r for r in report.results[0].reasons
    )


def test_compiler_blocked_when_operation_is_defective(
    sample_contract: CanonicalContract,
) -> None:
    """Verify operation with readiness BLOCKED is classified as BLOCKED."""
    compiler = TestPlanCompiler(contract=sample_contract)
    scenario = TestScenario(
        scenario_id="SCN-BLK-02",
        title="Update Item Defective",
        description="Target defective operation",
        category=ScenarioCategory.CONTRACT_POSITIVE,
        origin=ScenarioOrigin.DETERMINISTIC,
        priority=ScenarioPriority.HIGH,
        risk_classification=RiskClassification.HIGH,
        approval_status=DefinitionStatus.APPROVED,
        steps=[
            ScenarioStep(
                step_number=1,
                method="PUT",
                path="/api/items/{id}",
                description="Update item",
                expected_status=["200"],
            )
        ],
    )
    plan = make_simple_plan([scenario])
    suite, report = compiler.compile_plan(plan)

    assert report.blocked_count == 1
    assert report.results[0].status == CompilationStatus.BLOCKED
    assert any("blocked by contract defects" in r for r in report.results[0].reasons)


def test_compiler_multi_step_chaining_synthesis(
    sample_contract: CanonicalContract,
) -> None:
    """Verify multi-step scenario creates variable extraction and URL placeholder."""
    compiler = TestPlanCompiler(contract=sample_contract)
    scenario = TestScenario(
        scenario_id="SCN-CHAIN-01",
        title="Create and Retrieve Item",
        description="Multi-step chaining test",
        category=ScenarioCategory.CONTRACT_POSITIVE,
        origin=ScenarioOrigin.DETERMINISTIC,
        priority=ScenarioPriority.HIGH,
        risk_classification=RiskClassification.HIGH,
        approval_status=DefinitionStatus.APPROVED,
        steps=[
            ScenarioStep(
                step_number=1,
                method="POST",
                path="/api/items",
                description="create",
                expected_status=["201"],
            ),
            ScenarioStep(
                step_number=2,
                method="GET",
                path="/api/items/{id}",
                description="read",
                expected_status=["200"],
            ),
        ],
    )
    plan = make_simple_plan([scenario])
    suite, report = compiler.compile_plan(plan)

    assert report.compiled_count == 1
    assert len(suite.tests) == 2

    step1 = suite.tests[0]
    step2 = suite.tests[1]

    # Step 1 extracts ID
    assert step1.request.method.value == "POST"
    assert step1.extract is not None
    assert "chain_id" in step1.extract
    assert step1.extract["chain_id"] == "$.id"

    # Step 2 consumes chained variable
    assert step2.request.method.value == "GET"
    assert step2.request.url == "{{base_url}}/api/items/{{chain_id}}"


def test_compiler_test_name_deduplication(
    sample_contract: CanonicalContract,
) -> None:
    """Verify scenarios with identical step paths generate unique test names."""
    compiler = TestPlanCompiler(contract=sample_contract)
    scn1 = TestScenario(
        scenario_id="SCN-DEDUP-01",
        title="List Items 1",
        description="First list test",
        category=ScenarioCategory.CONTRACT_POSITIVE,
        origin=ScenarioOrigin.DETERMINISTIC,
        priority=ScenarioPriority.HIGH,
        risk_classification=RiskClassification.LOW,
        approval_status=DefinitionStatus.APPROVED,
        steps=[
            ScenarioStep(
                step_number=1,
                method="GET",
                path="/api/items",
                description="list",
                expected_status=["200"],
            )
        ],
    )
    scn2 = TestScenario(
        scenario_id="SCN-DEDUP-02",
        title="List Items 2",
        description="Second list test",
        category=ScenarioCategory.CONTRACT_POSITIVE,
        origin=ScenarioOrigin.DETERMINISTIC,
        priority=ScenarioPriority.HIGH,
        risk_classification=RiskClassification.LOW,
        approval_status=DefinitionStatus.APPROVED,
        steps=[
            ScenarioStep(
                step_number=1,
                method="GET",
                path="/api/items",
                description="list",
                expected_status=["200"],
            )
        ],
    )
    plan = make_simple_plan([scn1, scn2])
    suite, report = compiler.compile_plan(plan)

    assert report.compiled_count == 2
    names = [t.name for t in suite.tests]
    assert len(names) == 2
    assert names[0] != names[1]
    assert len(set(names)) == 2


def test_compiler_yaml_round_trip_and_safety_fencing(
    sample_contract: CanonicalContract,
    tmp_path: Path,
) -> None:
    """Verify YAML persistence round-trip and mutation fencing in RunService."""
    compiler = TestPlanCompiler(contract=sample_contract)
    scn_get = TestScenario(
        scenario_id="SCN-GET",
        title="List Items",
        description="List items",
        category=ScenarioCategory.CONTRACT_POSITIVE,
        origin=ScenarioOrigin.DETERMINISTIC,
        priority=ScenarioPriority.HIGH,
        risk_classification=RiskClassification.LOW,
        approval_status=DefinitionStatus.APPROVED,
        steps=[
            ScenarioStep(
                step_number=1,
                method="GET",
                path="/api/items",
                description="list",
                expected_status=["200"],
            )
        ],
    )
    scn_post = TestScenario(
        scenario_id="SCN-POST",
        title="Create Item",
        description="Create item",
        category=ScenarioCategory.CONTRACT_POSITIVE,
        origin=ScenarioOrigin.DETERMINISTIC,
        priority=ScenarioPriority.HIGH,
        risk_classification=RiskClassification.HIGH,
        approval_status=DefinitionStatus.APPROVED,
        steps=[
            ScenarioStep(
                step_number=1,
                method="POST",
                path="/api/items",
                description="create",
                expected_status=["201"],
            )
        ],
    )
    plan = make_simple_plan([scn_get, scn_post])
    suite, report = compiler.compile_plan(plan)

    out_file = tmp_path / "compiled_suite.yaml"
    rep_file = tmp_path / "compilation_report.json"

    compiler.save_compiled_suite(suite, out_file)
    compiler.save_compilation_report(report, rep_file)

    assert out_file.is_file()
    assert rep_file.is_file()

    # Verify native collection loading
    loaded_col = load_collection(out_file)
    assert len(loaded_col.tests) == 2

    # Verify RunService dry-run with ExecutionSafetyPolicy
    safety_policy = ExecutionSafetyPolicy(allow_mutations=False)
    run_service = RunService(safety_policy=safety_policy)
    env = Environment(name="local", variables={"base_url": "http://127.0.0.1:8000"})

    dry_run = run_service.dry_run(collection=loaded_col, environment=env)
    assert dry_run.total_operations == 2
    assert dry_run.read_only_count == 1
    assert dry_run.mutating_count == 1
    assert dry_run.executable_count == 1  # Only read-only GET can execute
    assert dry_run.blocked_count == 1  # Mutating POST is fenced
