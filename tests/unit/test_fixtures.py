"""Unit tests for ApiScope Fixture Management & Workflow Preparation (Task 20).

Verifies entity reference detection, fixture resolution, constraint validation,
secret masking, dependency ordering, cycle detection, LIFO cleanup sequences,
and end-to-end stateful workflow preparation.
"""

from __future__ import annotations

import json
from pathlib import Path

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
from apiscope.core.exceptions import DefinitionError
from apiscope.definitions.collection import load_collection
from apiscope.fixtures.dependency_graph import DependencyGraph
from apiscope.fixtures.entity_reference_detector import (
    EntityReferenceDetector,
)
from apiscope.fixtures.models import (
    FixtureDefinition,
    FixturePreparationReport,
    FixtureScope,
    FixtureSourceType,
    FixtureStatus,
)
from apiscope.fixtures.resolver import FixtureResolver
from apiscope.fixtures.service import FixtureService
from apiscope.fixtures.workflow_planner import StatefulWorkflowPlanner
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
def ecommerce_contract() -> CanonicalContract:
    """Canonical contract with Suppliers, Products, and Orders for workflow tests."""
    op_get_suppliers = CanonicalOperation(
        method="GET",
        path="/api/suppliers",
        summary="List suppliers",
        module="suppliers",
        parameters=[],
        responses={"200": CanonicalResponse(status_code="200", description="OK")},
        readiness=OperationReadiness.READY,
    )
    op_post_suppliers = CanonicalOperation(
        method="POST",
        path="/api/suppliers",
        summary="Create supplier",
        module="suppliers",
        parameters=[],
        request_body=CanonicalRequestBody(
            required=True,
            schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "email": {"type": "string"},
                },
            },
        ),
        responses={"201": CanonicalResponse(status_code="201", description="Created")},
        readiness=OperationReadiness.READY,
    )
    op_get_supplier_by_id = CanonicalOperation(
        method="GET",
        path="/api/suppliers/{id}",
        summary="Get supplier by ID",
        module="suppliers",
        parameters=[
            CanonicalParameter(
                name="id",
                location="path",
                required=True,
                schema={"type": "integer"},
            )
        ],
        responses={"200": CanonicalResponse(status_code="200", description="OK")},
        readiness=OperationReadiness.READY,
    )
    op_delete_supplier = CanonicalOperation(
        method="DELETE",
        path="/api/suppliers/{id}",
        summary="Delete supplier",
        module="suppliers",
        parameters=[
            CanonicalParameter(
                name="id",
                location="path",
                required=True,
                schema={"type": "integer"},
            )
        ],
        responses={"204": CanonicalResponse(status_code="204", description="Deleted")},
        readiness=OperationReadiness.READY,
    )

    op_post_products = CanonicalOperation(
        method="POST",
        path="/api/products",
        summary="Create product",
        module="products",
        parameters=[],
        request_body=CanonicalRequestBody(
            required=True,
            schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "supplierId": {"type": "integer"},
                    "price": {"type": "integer"},
                },
            },
        ),
        responses={"201": CanonicalResponse(status_code="201", description="Created")},
        readiness=OperationReadiness.READY,
    )
    op_get_product_by_id = CanonicalOperation(
        method="GET",
        path="/api/products/{id}",
        summary="Get product by ID",
        module="products",
        parameters=[
            CanonicalParameter(
                name="id",
                location="path",
                required=True,
                schema={"type": "integer"},
            )
        ],
        responses={"200": CanonicalResponse(status_code="200", description="OK")},
        readiness=OperationReadiness.READY,
    )
    op_delete_product = CanonicalOperation(
        method="DELETE",
        path="/api/products/{id}",
        summary="Delete product",
        module="products",
        parameters=[
            CanonicalParameter(
                name="id",
                location="path",
                required=True,
                schema={"type": "integer"},
            )
        ],
        responses={"204": CanonicalResponse(status_code="204", description="Deleted")},
        readiness=OperationReadiness.READY,
    )

    return CanonicalContract(
        title="ECommerce API",
        version="1.0.0",
        openapi_version="3.0.3",
        servers=[],
        operations=[
            op_get_suppliers,
            op_post_suppliers,
            op_get_supplier_by_id,
            op_delete_supplier,
            op_post_products,
            op_get_product_by_id,
            op_delete_product,
        ],
    )


# =====================================================================
# UNIT TESTS: FIXTURE DEFINITIONS & SECRECY
# =====================================================================


def test_fixture_definition_serialization_and_masking() -> None:
    """Verify FixtureDefinition correctly sanitizes sensitive secrets."""
    fix = FixtureDefinition(
        name="auth_token",
        entity="Auth",
        value="super_secret_bearer_token_xyz",
        status=FixtureStatus.RESOLVED,
        source_type=FixtureSourceType.USER_STATIC,
        scope=FixtureScope.GLOBAL,
        is_sensitive=True,
    )
    raw = fix.to_dict()
    assert raw["value"] == "***REDACTED***"

    safe = fix.to_safe_dict()
    assert safe["value"] == "***REDACTED***"
    assert safe["is_sensitive"] is True

    # Round trip with unredacted data
    unredacted = {
        "name": "auth_token",
        "entity": "Auth",
        "value": "super_secret_bearer_token_xyz",
        "status": "RESOLVED",
        "source_type": "USER_STATIC",
        "is_sensitive": True,
    }
    reconstructed = FixtureDefinition.from_dict(unredacted)
    assert reconstructed.name == "auth_token"
    assert reconstructed.value == "super_secret_bearer_token_xyz"
    assert reconstructed.status == FixtureStatus.RESOLVED


def test_fixture_preparation_report_masking() -> None:
    """Verify FixturePreparationReport masks secret keys in to_dict."""
    report = FixturePreparationReport(
        plan_id="PLAN-001",
        title="Test Plan Report",
        resolved_fixtures={
            "supplier_id": 42,
            "api_key": "secret_api_key_123",
            "db_password": "super_password",
        },
    )
    exported = report.to_dict()
    assert exported["resolved_fixtures"]["supplier_id"] == 42
    assert exported["resolved_fixtures"]["api_key"] == "***REDACTED***"
    assert exported["resolved_fixtures"]["db_password"] == "***REDACTED***"


# =====================================================================
# UNIT TESTS: RESOLVER & CONSTRAINT VALIDATION
# =====================================================================


def test_fixture_resolver_static_dict() -> None:
    """Verify FixtureResolver resolves static dictionaries."""
    resolver = FixtureResolver(
        static_fixtures={
            "supplier_id": 100,
            "product_id": 200,
        }
    )
    assert resolver.has_fixture("supplier_id")
    assert resolver.has_fixture("product_id")
    assert not resolver.has_fixture("non_existent")

    val = resolver.get_fixture_value("supplier_id")
    assert val == 100


def test_fixture_resolver_file_loading(tmp_path: Path) -> None:
    """Verify FixtureResolver loads YAML and JSON fixture files."""
    # YAML file
    yaml_file = tmp_path / "fixtures.yaml"
    yaml_file.write_text(
        yaml.dump(
            {
                "category_id": 5,
                "user_id": 10,
            }
        ),
        encoding="utf-8",
    )
    resolver = FixtureResolver(fixture_file=yaml_file)
    assert resolver.get_fixture_value("category_id") == 5
    assert resolver.get_fixture_value("user_id") == 10

    # JSON file
    json_file = tmp_path / "fixtures.json"
    json_file.write_text(json.dumps({"store_id": 99}), encoding="utf-8")
    resolver_json = FixtureResolver(fixture_file=json_file)
    assert resolver_json.get_fixture_value("store_id") == 99


def test_fixture_resolver_missing_file_fails(tmp_path: Path) -> None:
    """Verify FixtureResolver raises DefinitionError on non-existent file."""
    with pytest.raises(DefinitionError, match="not found"):
        FixtureResolver(fixture_file=tmp_path / "missing.yaml")


def test_fixture_resolver_constraint_validation() -> None:
    """Verify constraint enforcement for types, positivity, and ranges."""
    resolver = FixtureResolver()

    # Valid positive integer
    resolver.register_fixture(
        name="valid_id",
        value=15,
        entity="Item",
        constraints={"type": "integer", "positive": True, "min": 1, "max": 100},
    )
    assert resolver.get_fixture("valid_id").status == FixtureStatus.RESOLVED

    # Non-integer where integer is required
    resolver.register_fixture(
        name="invalid_type",
        value="not_an_int",
        entity="Item",
        constraints={"type": "integer"},
    )
    assert resolver.get_fixture("invalid_type").status == FixtureStatus.INVALID

    # Negative integer where positive is required
    resolver.register_fixture(
        name="invalid_neg",
        value=-5,
        entity="Item",
        constraints={"positive": True},
    )
    assert resolver.get_fixture("invalid_neg").status == FixtureStatus.INVALID

    # Regex constraint
    resolver.register_fixture(
        name="sku_code",
        value="SKU-12345",
        entity="Product",
        constraints={"regex": r"^SKU-\d+$"},
    )
    assert resolver.get_fixture("sku_code").status == FixtureStatus.RESOLVED

    resolver.register_fixture(
        name="invalid_sku",
        value="INVALID",
        entity="Product",
        constraints={"regex": r"^SKU-\d+$"},
    )
    assert resolver.get_fixture("invalid_sku").status == FixtureStatus.INVALID


# =====================================================================
# UNIT TESTS: ENTITY REFERENCE DETECTOR
# =====================================================================


def test_entity_reference_detector_detects_body_foreign_keys() -> None:
    """Verify EntityReferenceDetector identifies foreign key placeholders in bodies."""
    detector = EntityReferenceDetector()

    body = {
        "name": "Surplus Item",
        "productId": 1,  # Unverified placeholder
        "quantity": 10,
    }
    refs = detector.detect_references(
        method="POST", path="/api/surplus-listings", body=body
    )
    assert len(refs) == 1
    ref = refs[0]
    assert ref.field_name == "productId"
    assert ref.target_entity == "Product"
    assert ref.current_value == 1
    assert ref.is_placeholder is True
    assert ref.status == "UNVERIFIED"


def test_entity_reference_detector_chained_variable_recognized() -> None:
    """Verify variables {{chain_product_id}} are marked CHAINED, not unverified."""
    detector = EntityReferenceDetector()

    body = {
        "name": "Surplus Item",
        "productId": "{{chain_product_id}}",
        "quantity": 10,
    }
    refs = detector.detect_references(
        method="POST", path="/api/surplus-listings", body=body
    )
    assert len(refs) == 1
    ref = refs[0]
    assert ref.is_placeholder is False
    assert ref.status == "CHAINED"


def test_entity_reference_detector_resolved_fixture_recognized() -> None:
    """Verify resolved fixtures in mapping are recognized as RESOLVED."""
    detector = EntityReferenceDetector()

    body = {
        "name": "Surplus Item",
        "productId": 42,
    }
    refs = detector.detect_references(
        method="POST",
        path="/api/surplus-listings",
        body=body,
        resolved_fixtures={"productId": 42},
    )
    assert len(refs) == 1
    ref = refs[0]
    assert ref.is_placeholder is False
    assert ref.status == "RESOLVED"


def test_entity_reference_detector_query_parameters() -> None:
    """Verify detector inspects query parameters."""
    detector = EntityReferenceDetector()

    params = {"productId": 1, "status": "ACTIVE"}
    refs = detector.detect_references(
        method="GET", path="/api/inventory", params=params
    )
    assert len(refs) == 1
    assert refs[0].field_name == "productId"
    assert refs[0].location == "query"
    assert refs[0].status == "UNVERIFIED"


# =====================================================================
# UNIT TESTS: DEPENDENCY GRAPH & TOPOLOGICAL SORT
# =====================================================================


def test_dependency_graph_topological_sort() -> None:
    """Verify topological sorting orders producers before consumers."""
    graph = DependencyGraph()

    # Product consumes Supplier
    graph.add_node(
        node_id="create_product",
        entity="Product",
        method="POST",
        path="/api/products",
        consumes=["Supplier"],
    )
    # Supplier consumes nothing
    graph.add_node(
        node_id="create_supplier",
        entity="Supplier",
        method="POST",
        path="/api/suppliers",
        consumes=[],
    )
    graph.add_dependency("create_product", "create_supplier")

    sorted_nodes = graph.topological_sort()
    assert len(sorted_nodes) == 2
    assert sorted_nodes[0].node_id == "create_supplier"
    assert sorted_nodes[1].node_id == "create_product"


def test_dependency_graph_cycle_detection() -> None:
    """Verify cycle detection raises ValueError."""
    graph = DependencyGraph()
    graph.add_node(
        node_id="node_a", entity="A", method="POST", path="/a", consumes=["B"]
    )
    graph.add_node(
        node_id="node_b", entity="B", method="POST", path="/b", consumes=["A"]
    )
    graph.add_dependency("node_a", "node_b")
    graph.add_dependency("node_b", "node_a")

    with pytest.raises(ValueError, match="Circular"):
        graph.topological_sort()


def test_dependency_graph_unresolved_dependencies() -> None:
    """Verify missing producer detection."""
    graph = DependencyGraph()
    graph.add_node(
        node_id="create_order",
        entity="Order",
        method="POST",
        path="/api/orders",
        consumes=["Warehouse"],  # Warehouse producer is missing
    )
    missing = graph.find_missing_producers()
    assert any("Warehouse" in m for m in missing)


# =====================================================================
# UNIT TESTS: WORKFLOW PLANNER & LIFECYCLE
# =====================================================================


def test_workflow_planner_generates_setup_test_and_cleanup(
    ecommerce_contract: CanonicalContract,
) -> None:
    """Verify StatefulWorkflowPlanner creates multi-step setup and LIFO cleanup."""
    planner = StatefulWorkflowPlanner(ecommerce_contract)

    # Plan workflow for GET /api/products/{id}
    # Dependencies: Product requires Supplier
    wf = planner.plan_workflow_for_operation(
        target_method="GET",
        target_path="/api/products/{id}",
        base_url_var="{{base_url}}",
        mutation_authorized=False,
    )

    assert wf.workflow_id == "WF-GET-API-PRODUCTS-ID"
    assert wf.status == "PLANNED"
    assert not wf.is_mutation_authorized

    # 1. Setup steps: Supplier first, then Product
    assert len(wf.setup_steps) == 2
    setup_supplier = wf.setup_steps[0]
    assert setup_supplier.method == "POST"
    assert setup_supplier.url == "{{base_url}}/api/suppliers"
    assert setup_supplier.extract == {"chain_supplier_id": "$.id"}
    assert setup_supplier.can_rollback is True

    setup_product = wf.setup_steps[1]
    assert setup_product.method == "POST"
    assert setup_product.url == "{{base_url}}/api/products"
    assert setup_product.body.get("supplierId") == "{{chain_supplier_id}}"
    assert setup_product.extract == {"chain_product_id": "$.id"}

    # 2. Test step: GET /api/products/{{chain_product_id}}
    assert len(wf.test_steps) == 1
    test_step = wf.test_steps[0]
    assert test_step.method == "GET"
    assert test_step.url == "{{base_url}}/api/products/{{chain_product_id}}"
    assert test_step.assertions == [{"type": "status", "equals": 200}]

    # 3. Cleanup steps: Reverse order (LIFO) -> Delete Product, then Delete Supplier
    assert len(wf.cleanup_steps) == 2
    del_product = wf.cleanup_steps[0]
    assert del_product.method == "DELETE"
    assert del_product.url == "{{base_url}}/api/products/{{chain_product_id}}"
    assert del_product.risk == "risk:destructive"

    del_supplier = wf.cleanup_steps[1]
    assert del_supplier.method == "DELETE"
    assert del_supplier.url == "{{base_url}}/api/suppliers/{{chain_supplier_id}}"


def test_workflow_planner_mutation_authorized_sets_ready(
    ecommerce_contract: CanonicalContract,
) -> None:
    """Verify workflow status is READY when mutation is authorized."""
    planner = StatefulWorkflowPlanner(ecommerce_contract)
    wf = planner.plan_workflow_for_operation(
        target_method="GET",
        target_path="/api/suppliers/{id}",
        mutation_authorized=True,
    )
    assert wf.status == "READY"
    assert wf.is_mutation_authorized is True


# =====================================================================
# UNIT TESTS: FIXTURE SERVICE PREPARATION
# =====================================================================


def test_fixture_service_prepare_tests_end_to_end(
    ecommerce_contract: CanonicalContract,
) -> None:
    """Verify FixtureService detects FKs and prepares native test collections."""
    service = FixtureService(contract=ecommerce_contract)

    # Create plan with 1 clean GET, 1 POST with unverified FK, 1 GET with path param
    scn_get = TestScenario(
        scenario_id="SCN-001",
        title="List Suppliers",
        description="Clean GET request",
        category=ScenarioCategory.CONTRACT_POSITIVE,
        origin=ScenarioOrigin.DETERMINISTIC,
        priority=ScenarioPriority.HIGH,
        risk_classification=RiskClassification.LOW,
        steps=[
            ScenarioStep(
                step_number=1,
                method="GET",
                path="/api/suppliers",
                description="List suppliers",
            )
        ],
    )
    scn_post_fk = TestScenario(
        scenario_id="SCN-002",
        title="Create Product with Dummy FK",
        description="POST with unverified FK",
        category=ScenarioCategory.CONTRACT_POSITIVE,
        origin=ScenarioOrigin.DETERMINISTIC,
        priority=ScenarioPriority.HIGH,
        risk_classification=RiskClassification.HIGH,
        steps=[
            ScenarioStep(
                step_number=1,
                method="POST",
                path="/api/products",
                description="Create product",
                request_template={"body": {"name": "Widget", "supplierId": 1}},
            )
        ],
    )
    plan = TestPlan(
        plan_id="PLAN-TEST",
        title="Test ECommerce Plan",
        version="1.0.0",
        scenarios=[scn_get, scn_post_fk],
    )

    collection, report = service.prepare_tests(plan)

    assert report.total_scenarios_evaluated == 2
    assert report.fixture_ready_count == 1  # Only SCN-001 is ready
    assert report.unresolved_fixture_count == 1  # SCN-002 has unverified FK
    assert len(report.unverified_body_placeholders) == 1
    assert report.unverified_body_placeholders[0]["field_name"] == "supplierId"

    # Verify prepared test collection
    assert len(collection.tests) == 1
    assert "[SCN-001]" in collection.tests[0].name


def test_fixture_service_file_persistence(
    tmp_path: Path, ecommerce_contract: CanonicalContract
) -> None:
    """Verify saving prepared suites and reports to disk."""
    service = FixtureService(contract=ecommerce_contract)
    plan = TestPlan(
        plan_id="PLAN-SAVE",
        title="Save Plan",
        version="1.0.0",
        scenarios=[
            TestScenario(
                scenario_id="SCN-010",
                title="Get Suppliers",
                description="Read suppliers",
                category=ScenarioCategory.CONTRACT_POSITIVE,
                origin=ScenarioOrigin.DETERMINISTIC,
                priority=ScenarioPriority.HIGH,
                risk_classification=RiskClassification.LOW,
                steps=[
                    ScenarioStep(
                        step_number=1,
                        method="GET",
                        path="/api/suppliers",
                        description="",
                    )
                ],
            )
        ],
    )
    collection, report = service.prepare_tests(plan)

    suite_file = tmp_path / "prepared_suite.yaml"
    report_file = tmp_path / "prep_report.json"

    service.save_prepared_suite(collection, suite_file)
    service.save_preparation_report(report, report_file)

    assert suite_file.is_file()
    assert report_file.is_file()

    # Validate with load_collection
    loaded_suite = load_collection(suite_file)
    assert len(loaded_suite.tests) == 1

    # Validate JSON report
    loaded_rep = json.loads(report_file.read_text(encoding="utf-8"))
    assert loaded_rep["summary"]["total_scenarios_evaluated"] == 1
    assert loaded_rep["summary"]["fixture_ready_count"] == 1
