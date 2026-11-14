"""Unit tests for ApiScope Test Planning Engine (Task 18).

Verifies deterministic test planning, boundary/negative validation,
schema conformance, auth boundary probes, readiness handling, budget-aware
Gemini reasoning, prompt minimization, injection resistance, caching,
quota fallback, and plan serialization.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from apiscope.contract.models import (
    CanonicalContract,
    CanonicalOperation,
    CanonicalParameter,
    CanonicalRequestBody,
    CanonicalResponse,
    OperationReadiness,
)
from apiscope.core.exceptions import NetworkExecutionError
from apiscope.definitions.models import DefinitionStatus
from apiscope.generators.ai_generator import LLMConfig
from apiscope.intelligence.models import (
    DiscoveredBusinessRule,
    DiscoveredEntity,
    DiscoveredWorkflow,
    EvidenceReference,
    EvidenceSourceType,
    ProjectIdentity,
    ProjectIntelligenceReport,
    VerificationStatus,
)
from apiscope.planning.ai_planner import GeminiTestPlanner
from apiscope.planning.cache import PlanningCache
from apiscope.planning.deterministic_planner import DeterministicTestPlanner
from apiscope.planning.models import (
    AIBudgetConfig,
    AIUsageRecord,
    OracleType,
    RiskClassification,
    ScenarioCategory,
    ScenarioOrigin,
    ScenarioPriority,
    ScenarioStep,
    TestPlan,
    TestScenario,
)
from apiscope.planning.service import TestPlanningService

# =====================================================================
# FIXTURES
# =====================================================================


@pytest.fixture
def sample_contract() -> CanonicalContract:
    """Canonical contract with mixed GET, POST, and DELETE operations."""
    op_list_items = CanonicalOperation(
        method="GET",
        path="/api/items",
        operation_id="listItems",
        summary="List all items",
        module="items",
        parameters=[
            CanonicalParameter(
                name="category",
                location="query",
                required=False,
                schema={"type": "string"},
            ),
            CanonicalParameter(
                name="limit",
                location="query",
                required=False,
                schema={"type": "integer", "minimum": 1, "maximum": 100},
            ),
        ],
        responses={
            "200": CanonicalResponse(
                status_code="200",
                description="List of items",
                is_array=True,
                item_schema_ref="#/components/schemas/Item",
            )
        },
        readiness=OperationReadiness.READY,
    )

    op_create_item = CanonicalOperation(
        method="POST",
        path="/api/items",
        operation_id="createItem",
        summary="Create item",
        module="items",
        requires_auth=True,
        security_schemes=[{"BearerAuth": []}],
        request_body=CanonicalRequestBody(
            content_types=["application/json"],
            required=True,
            schema={
                "type": "object",
                "required": ["name", "price"],
                "properties": {
                    "name": {"type": "string", "minLength": 2},
                    "price": {"type": "number", "minimum": 0},
                },
            },
            schema_ref="#/components/schemas/ItemInput",
        ),
        responses={
            "201": CanonicalResponse(
                status_code="201",
                description="Created item",
                schema_ref="#/components/schemas/Item",
            )
        },
        readiness=OperationReadiness.READY,
    )

    op_get_item = CanonicalOperation(
        method="GET",
        path="/api/items/{id}",
        operation_id="getItemById",
        summary="Get item by id",
        module="items",
        parameters=[
            CanonicalParameter(
                name="id",
                location="path",
                required=True,
                schema={"type": "integer"},
            )
        ],
        responses={
            "200": CanonicalResponse(
                status_code="200",
                description="Item found",
                schema_ref="#/components/schemas/Item",
            ),
            "404": CanonicalResponse(
                status_code="404", description="Item not found", is_empty_body=True
            ),
        },
        readiness=OperationReadiness.READY,
    )

    op_delete_item = CanonicalOperation(
        method="DELETE",
        path="/api/items/{id}",
        operation_id="deleteItem",
        summary="Delete item",
        module="items",
        requires_auth=False,  # Undocumented mutation security
        parameters=[
            CanonicalParameter(
                name="id",
                location="path",
                required=True,
                schema={"type": "integer"},
            )
        ],
        responses={
            "204": CanonicalResponse(
                status_code="204", description="Deleted", is_empty_body=True
            )
        },
        readiness=OperationReadiness.PARTIAL,
        readiness_reasons=["Undocumented security scheme on mutating operation"],
    )

    op_blocked = CanonicalOperation(
        method="GET",
        path="/api/broken/{missingParam}",
        operation_id="brokenOp",
        module="broken",
        parameters=[],  # Missing path parameter declaration
        readiness=OperationReadiness.BLOCKED,
        readiness_reasons=["Path parameter '{missingParam}' is not declared"],
    )

    return CanonicalContract(
        title="Sample Warehouse API",
        version="1.0.0",
        openapi_version="3.0.0",
        operations=[
            op_list_items,
            op_create_item,
            op_get_item,
            op_delete_item,
            op_blocked,
        ],
        schemas={
            "Item": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
            }
        },
    )


@pytest.fixture
def sample_intelligence() -> ProjectIntelligenceReport:
    """Project intelligence report with business rules and workflows."""
    return ProjectIntelligenceReport(
        project=ProjectIdentity(
            name="Sample Warehouse API",
            version="1.0.0",
            source_language="java/spring-boot",
        ),
        entities=[
            DiscoveredEntity(
                name="Item",
                properties=["id", "name", "price", "quantity"],
                status=VerificationStatus.CONFIRMED,
            )
        ],
        business_rules=[
            DiscoveredBusinessRule(
                rule_id="rule_positive_stock",
                module="items",
                description="Stock quantity must never be negative",
                endpoint="POST /api/items",
                rule_type="invariant",
                status=VerificationStatus.CONFIRMED,
            )
        ],
        workflows=[
            DiscoveredWorkflow(
                workflow_id="wf_item_lifecycle",
                name="Item Lifecycle",
                description="Create item then verify and delete",
                steps=[
                    {"step": 1, "path": "/api/items", "method": "POST"},
                    {"step": 2, "path": "/api/items/{id}", "method": "GET"},
                ],
                status=VerificationStatus.CONFIRMED,
            )
        ],
    )


# =====================================================================
# 1. DETERMINISTIC TEST PLANNER TESTS
# =====================================================================


def test_deterministic_planner_happy_path_generation(
    sample_contract: CanonicalContract,
) -> None:
    """Verify happy-path scenarios generated for ready operations."""
    planner = DeterministicTestPlanner(sample_contract)
    scenarios, untestable, prereqs = planner.plan()

    pos_scenarios = [
        s for s in scenarios if s.category == ScenarioCategory.CONTRACT_POSITIVE
    ]
    # 4 non-blocked operations get happy-path tests
    assert len(pos_scenarios) == 4

    post_scn = next(s for s in pos_scenarios if "POST /api/items" in s.target_endpoints)
    assert post_scn.priority == ScenarioPriority.HIGH
    assert post_scn.origin == ScenarioOrigin.DETERMINISTIC
    assert len(post_scn.steps) == 1
    assert post_scn.steps[0].expected_status == ["201"]
    assert post_scn.steps[0].oracle_type == OracleType.EXPLICIT_CONTRACT


def test_deterministic_planner_input_validation(
    sample_contract: CanonicalContract,
) -> None:
    """Verify missing required body fields produce validation scenarios."""
    planner = DeterministicTestPlanner(sample_contract)
    scenarios, _, _ = planner.plan()

    val_scenarios = [
        s for s in scenarios if s.category == ScenarioCategory.INPUT_VALIDATION
    ]
    assert len(val_scenarios) >= 1

    # Should flag missing required body field 'name'
    body_val = next(
        s for s in val_scenarios if "Missing Required Body Field" in s.title
    )
    assert body_val.priority == ScenarioPriority.HIGH
    assert body_val.steps[0].expected_status == ["400", "422"]


def test_deterministic_planner_boundary_conditions(
    sample_contract: CanonicalContract,
) -> None:
    """Verify numeric minimum constraints produce boundary underflow scenarios."""
    planner = DeterministicTestPlanner(sample_contract)
    scenarios, _, _ = planner.plan()

    bound_scenarios = [s for s in scenarios if s.category == ScenarioCategory.BOUNDARY]
    assert len(bound_scenarios) >= 1

    limit_bound = next(s for s in bound_scenarios if "limit < 1" in s.title)
    assert limit_bound.steps[0].request_template["parameters"]["limit"] == 0
    assert limit_bound.steps[0].expected_status == ["400", "422"]


def test_deterministic_planner_response_schema_validation(
    sample_contract: CanonicalContract,
) -> None:
    """Verify operations with 2xx schemas get schema conformance scenarios."""
    planner = DeterministicTestPlanner(sample_contract)
    scenarios, _, _ = planner.plan()

    schema_scenarios = [
        s for s in scenarios if s.category == ScenarioCategory.RESPONSE_SCHEMA
    ]
    # GET /api/items, POST /api/items, and GET /api/items/{id} have schemas
    assert len(schema_scenarios) == 3


def test_deterministic_planner_auth_security_probes(
    sample_contract: CanonicalContract,
) -> None:
    """Verify authentication probes for secured and undocumented mutating endpoints."""
    planner = DeterministicTestPlanner(sample_contract)
    scenarios, _, _ = planner.plan()

    sec_scenarios = [
        s for s in scenarios if s.category == ScenarioCategory.AUTH_SECURITY
    ]
    assert len(sec_scenarios) >= 3

    # 1. Missing Auth Token on POST /api/items
    missing_auth = next(
        s for s in sec_scenarios if "Missing Auth: POST /api/items" in s.title
    )
    assert missing_auth.steps[0].expected_status == ["401"]
    assert missing_auth.priority == ScenarioPriority.CRITICAL

    # 2. Invalid Token on POST /api/items
    invalid_auth = next(
        s for s in sec_scenarios if "Invalid Auth Token: POST /api/items" in s.title
    )
    assert invalid_auth.steps[0].expected_status == ["401"]

    # 3. Undocumented Mutation probe on DELETE /api/items/{id}
    mutation_audit = next(
        s for s in sec_scenarios if "Undocumented Mutation" in s.title
    )
    assert mutation_audit.approval_status == DefinitionStatus.PROPOSED
    assert mutation_audit.risk_classification == RiskClassification.HIGH


def test_deterministic_planner_resource_existence_checks(
    sample_contract: CanonicalContract,
) -> None:
    """Verify path parameter endpoints get 404 non-existent resource checks."""
    planner = DeterministicTestPlanner(sample_contract)
    scenarios, _, _ = planner.plan()

    res_scenarios = [
        s for s in scenarios if s.category == ScenarioCategory.RESOURCE_EXISTENCE
    ]
    assert len(res_scenarios) >= 2  # GET /api/items/{id} and DELETE /api/items/{id}

    get_res = next(
        s for s in res_scenarios if "GET /api/items/{id}" in s.target_endpoints[0]
    )
    assert get_res.steps[0].path == "/api/items/999999"
    assert get_res.steps[0].expected_status == ["404"]


def test_deterministic_planner_malformed_syntax_error_handling(
    sample_contract: CanonicalContract,
) -> None:
    """Verify endpoints with request body get malformed JSON tests."""
    planner = DeterministicTestPlanner(sample_contract)
    scenarios, _, _ = planner.plan()

    err_scenarios = [
        s for s in scenarios if s.category == ScenarioCategory.ERROR_HANDLING
    ]
    assert len(err_scenarios) >= 1
    assert "Malformed JSON" in err_scenarios[0].title
    assert err_scenarios[0].steps[0].expected_status == ["400"]


def test_deterministic_planner_cross_endpoint_consistency(
    sample_contract: CanonicalContract,
) -> None:
    """Verify paired POST/GET on same path generates consistency test."""
    planner = DeterministicTestPlanner(sample_contract)
    scenarios, _, _ = planner.plan()

    cons_scenarios = [
        s for s in scenarios if s.category == ScenarioCategory.CONSISTENCY_CHECK
    ]
    assert len(cons_scenarios) >= 1
    assert "Read-After-Write Consistency" in cons_scenarios[0].title
    assert len(cons_scenarios[0].steps) == 2
    assert cons_scenarios[0].steps[0].method == "POST"
    assert cons_scenarios[0].steps[1].method == "GET"


def test_deterministic_planner_respects_operation_readiness(
    sample_contract: CanonicalContract,
) -> None:
    """Verify BLOCKED operations are segregated into untestable list."""
    planner = DeterministicTestPlanner(sample_contract)
    _, untestable, _ = planner.plan()

    assert len(untestable) == 1
    assert untestable[0]["operation"] == "GET /api/broken/{missingParam}"
    assert untestable[0]["readiness"] == "BLOCKED"
    assert len(untestable[0]["blocking_reasons"]) >= 1


# =====================================================================
# 2. GEMINI AI TEST PLANNER & BUDGET / CACHING TESTS
# =====================================================================


def test_gemini_planner_skips_when_api_key_missing(
    sample_contract: CanonicalContract,
) -> None:
    """Verify AI planner skips safely without error when API key is empty."""
    config = LLMConfig(provider="gemini", api_key="", model="gemini-2.5-flash")
    planner = GeminiTestPlanner(config=config)

    scenarios, usage = planner.plan_project_scenarios(sample_contract)
    assert len(scenarios) == 0
    assert "SKIPPED" in usage.status
    assert usage.calls_made == 0


def test_gemini_planner_proposes_domain_workflows_with_mock(
    sample_contract: CanonicalContract,
    sample_intelligence: ProjectIntelligenceReport,
    tmp_path: Path,
) -> None:
    """Verify Gemini proposes structured multi-step business workflow scenarios."""
    mock_provider = MagicMock()
    mock_provider.generate_yaml.return_value = json.dumps(
        {
            "scenarios": [
                {
                    "scenario_id": "SCN-AI-PO-WORKFLOW-01",
                    "title": "Item Restock Lifecycle",
                    "description": "Create item and verify immediate availability",
                    "category": "WORKFLOW_STATE_TRANSITION",
                    "priority": "HIGH",
                    "target_module": "items",
                    "related_rule": "PositiveStockInvariant",
                    "preconditions": ["Valid manager authentication"],
                    "test_data_requirements": ["Item payload"],
                    "steps": [
                        {
                            "step_number": 1,
                            "method": "POST",
                            "path": "/api/items",
                            "description": "Create inventory item",
                            "expected_status": ["201"],
                            "expected_outcome": "Item created successfully",
                        },
                        {
                            "step_number": 2,
                            "method": "GET",
                            "path": "/api/items/{id}",
                            "description": "Verify created item exists",
                            "expected_status": ["200"],
                            "expected_outcome": "Item details match created state",
                        },
                    ],
                    "risk_classification": "MEDIUM",
                    "missing_dependencies": ["needs_created_id"],
                    "rationale": "Validates core lifecycle without silent failures",
                }
            ]
        }
    )

    config = LLMConfig(provider="gemini", api_key="test-key", model="gemini-2.5-flash")
    cache = PlanningCache(cache_dir=tmp_path / "cache")
    planner = GeminiTestPlanner(config=config, provider=mock_provider, cache=cache)

    scenarios, usage = planner.plan_project_scenarios(
        sample_contract, sample_intelligence
    )

    assert len(scenarios) == 1
    scn = scenarios[0]
    assert scn.scenario_id == "SCN-AI-PO-WORKFLOW-01"
    assert scn.origin == ScenarioOrigin.AI_PROPOSED
    assert scn.approval_status == DefinitionStatus.PROPOSED
    assert scn.confidence == VerificationStatus.PROPOSED
    assert len(scn.steps) == 2
    assert scn.steps[0].oracle_type == OracleType.AI_HYPOTHESIS
    assert usage.calls_made == 1
    assert usage.status == "COMPLETED"


def test_gemini_planner_quarantines_hallucinated_endpoints(
    sample_contract: CanonicalContract,
    tmp_path: Path,
) -> None:
    """Verify planner discards steps with hallucinated endpoints not in contract."""
    mock_provider = MagicMock()
    mock_provider.generate_yaml.return_value = json.dumps(
        {
            "scenarios": [
                {
                    "scenario_id": "SCN-AI-GHOST",
                    "title": "Hallucinated endpoint scenario",
                    "steps": [
                        {
                            "step_number": 1,
                            "method": "POST",
                            "path": "/api/nonexistent-ghost-endpoint",
                            "expected_status": ["200"],
                        }
                    ],
                }
            ]
        }
    )

    config = LLMConfig(provider="gemini", api_key="test-key", model="gemini-2.5-flash")
    cache = PlanningCache(cache_dir=tmp_path / "cache")
    planner = GeminiTestPlanner(config=config, provider=mock_provider, cache=cache)

    scenarios, usage = planner.plan_project_scenarios(sample_contract)
    # The hallucinated step was quarantined; scenario had 0 valid steps, so dropped
    assert len(scenarios) == 0


def test_gemini_planner_caching_and_reuse(
    sample_contract: CanonicalContract,
    tmp_path: Path,
) -> None:
    """Verify repeated planning calls reuse cached results with zero token cost."""
    cache = PlanningCache(cache_dir=tmp_path / "cache")
    mock_provider = MagicMock()
    mock_provider.generate_yaml.return_value = json.dumps(
        {
            "scenarios": [
                {
                    "scenario_id": "SCN-AI-CACHED-01",
                    "title": "Cached Workflow",
                    "category": "WORKFLOW_STATE_TRANSITION",
                    "steps": [
                        {
                            "step_number": 1,
                            "method": "GET",
                            "path": "/api/items",
                            "expected_status": ["200"],
                        }
                    ],
                }
            ]
        }
    )

    config = LLMConfig(provider="gemini", api_key="test-key", model="gemini-2.5-flash")
    planner = GeminiTestPlanner(config=config, provider=mock_provider, cache=cache)

    # First call: Cache miss -> invokes provider
    scenarios_1, usage_1 = planner.plan_project_scenarios(sample_contract)
    assert len(scenarios_1) == 1
    assert usage_1.calls_made == 1
    assert usage_1.status == "COMPLETED"

    # Second call with same contract: Cache hit -> zero provider calls!
    mock_provider.generate_yaml.reset_mock()
    scenarios_2, usage_2 = planner.plan_project_scenarios(sample_contract)
    assert len(scenarios_2) == 1
    assert usage_2.calls_made == 0
    assert usage_2.status == "CACHED"
    mock_provider.generate_yaml.assert_not_called()


def test_gemini_planner_graceful_quota_exhaustion_fallback(
    sample_contract: CanonicalContract,
) -> None:
    """Verify HTTP 429 quota exhaustion degrades gracefully without raising."""
    mock_provider = MagicMock()
    mock_provider.generate_yaml.side_effect = NetworkExecutionError(
        "LLM API request failed with HTTP 429: RESOURCE_EXHAUSTED"
    )

    config = LLMConfig(provider="gemini", api_key="test-key", model="gemini-2.5-flash")
    planner = GeminiTestPlanner(config=config, provider=mock_provider)

    scenarios, usage = planner.plan_project_scenarios(sample_contract)
    assert len(scenarios) == 0
    assert "429" in usage.status or "Quota exhausted" in usage.status


def test_gemini_planner_budget_limit_pruning(
    sample_contract: CanonicalContract,
) -> None:
    """Verify budget check stops when prompt size exceeds token limit."""
    mock_provider = MagicMock()
    config = LLMConfig(provider="gemini", api_key="test-key", model="gemini-2.5-flash")
    # Set artificially low token budget (10 tokens)
    strict_budget = AIBudgetConfig(max_total_estimated_tokens=10)
    planner = GeminiTestPlanner(
        config=config, budget=strict_budget, provider=mock_provider
    )

    scenarios, usage = planner.plan_project_scenarios(sample_contract)
    assert len(scenarios) == 0
    assert "Budget exceeded" in usage.status
    mock_provider.generate_yaml.assert_not_called()


# =====================================================================
# 3. TEST PLANNING SERVICE & PLAN SERIALIZATION TESTS
# =====================================================================


def test_planning_service_orchestration_offline(
    tmp_path: Path,
) -> None:
    """Verify TestPlanningService generates offline plan and saves YAML/JSON."""
    spec_file = tmp_path / "spec.yaml"
    spec_data = {
        "openapi": "3.0.0",
        "info": {"title": "Warehouse Mini", "version": "1.0"},
        "paths": {
            "/api/boxes": {
                "get": {
                    "operationId": "listBoxes",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    spec_file.write_text(yaml.safe_dump(spec_data))

    service = TestPlanningService()
    plan = service.plan_tests(spec_path=spec_file, enable_ai=False)

    assert isinstance(plan, TestPlan)
    assert plan.title == "Warehouse Mini"
    assert len(plan.scenarios) >= 1
    assert plan.summary["total_scenarios"] == len(plan.scenarios)
    assert plan.ai_usage.status == "SKIPPED"

    out_yaml = tmp_path / "plan.yaml"
    out_json = tmp_path / "plan.json"

    service.save_plan(plan, out_yaml)
    service.save_plan(plan, out_json)

    assert out_yaml.exists()
    assert out_json.exists()

    # Verify round-trip load
    loaded_plan = service.load_plan(out_yaml)
    assert loaded_plan.title == "Warehouse Mini"
    assert len(loaded_plan.scenarios) == len(plan.scenarios)


def test_test_plan_to_dict_and_from_dict() -> None:
    """Verify TestPlan and TestScenario serialization integrity."""
    step = ScenarioStep(
        step_number=1,
        method="POST",
        path="/api/orders",
        description="Create order",
        expected_status=["201"],
        oracle_type=OracleType.EXPLICIT_CONTRACT,
        expected_outcome="Order created",
        oracle_evidence=EvidenceReference(
            source_type=EvidenceSourceType.OPENAPI,
            source_ref="paths./api/orders",
            snippet="201 Created",
            confidence=1.0,
            explanation="Explicit 201 response declared",
        ),
    )

    scenario = TestScenario(
        scenario_id="SCN-ORDERS-001",
        title="Create Order",
        description="Creates order and checks 201",
        category=ScenarioCategory.CONTRACT_POSITIVE,
        priority=ScenarioPriority.HIGH,
        origin=ScenarioOrigin.DETERMINISTIC,
        target_module="orders",
        target_endpoints=["POST /api/orders"],
        steps=[step],
        risk_classification=RiskClassification.HIGH,
        confidence=VerificationStatus.CONFIRMED,
        approval_status=DefinitionStatus.APPROVED,
    )

    plan = TestPlan(
        plan_id="plan_test_123",
        title="Order Service",
        version="2.0.0",
        summary={"total_scenarios": 1},
        scenarios=[scenario],
        missing_prerequisites=["auth_token"],
        untestable_operations=[],
        ai_usage=AIUsageRecord(status="COMPLETED", calls_made=1, total_tokens=150),
    )

    plan_dict = plan.to_dict()
    assert plan_dict["plan_id"] == "plan_test_123"
    assert plan_dict["scenarios"][0]["scenario_id"] == "SCN-ORDERS-001"
    assert (
        plan_dict["scenarios"][0]["steps"][0]["oracle_evidence"]["source_type"]
        == "OPENAPI"
    )

    restored = TestPlan.from_dict(plan_dict)
    assert restored.plan_id == "plan_test_123"
    assert len(restored.scenarios) == 1
    assert restored.scenarios[0].scenario_id == "SCN-ORDERS-001"
    assert restored.scenarios[0].steps[0].oracle_evidence is not None
    assert (
        restored.scenarios[0].steps[0].oracle_evidence.source_type
        == EvidenceSourceType.OPENAPI
    )
