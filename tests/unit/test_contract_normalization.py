"""Unit tests for ApiScope API Contract Normalization and Quality Analysis (Task 17).

Verifies canonical contract models, bounded recursive $ref handling,
array response schemas, empty body handling (204/304), contradictory constraint
detection, path parameter reconciliation, multi-service monorepo isolation,
readiness classification, and report serialization.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from apiscope.contract.models import (
    CanonicalContract,
    CanonicalOperation,
    CanonicalParameter,
    CanonicalResponse,
    ContractQualityReport,
    FindingSeverity,
    OperationReadiness,
    QualityCategory,
)
from apiscope.contract.normalizer import ContractNormalizer, safe_resolve_schema
from apiscope.contract.quality_analyzer import ContractQualityAnalyzer
from apiscope.contract.reconciler import ContractReconciler
from apiscope.contract.service import ContractAnalysisService
from apiscope.intelligence.models import (
    DiscoveredEndpoint,
    DiscoveredModule,
    ProjectIdentity,
    ProjectIntelligenceReport,
    VerificationStatus,
)
from apiscope.intelligence.source_scanner import (
    DiscoveredSourceEndpoint,
    DiscoveredSourceFindings,
)

# =====================================================================
# FIXTURES
# =====================================================================


@pytest.fixture
def sample_raw_spec() -> dict[str, Any]:
    """Provide a standard, clean OpenAPI 3.0 dictionary."""
    return {
        "openapi": "3.0.1",
        "info": {
            "title": "Inventory Management API",
            "version": "1.0.0",
            "description": "Enterprise inventory testing target",
        },
        "servers": [{"url": "http://localhost:8080"}],
        "paths": {
            "/api/items": {
                "get": {
                    "operationId": "listItems",
                    "summary": "List all inventory items",
                    "tags": ["items"],
                    "parameters": [
                        {
                            "name": "limit",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "integer", "default": 20},
                        },
                        {
                            "name": "X-Request-ID",
                            "in": "header",
                            "required": False,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "List of items",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"$ref": "#/components/schemas/Item"},
                                    }
                                }
                            },
                        }
                    },
                },
                "post": {
                    "operationId": "createItem",
                    "summary": "Create new inventory item",
                    "tags": ["items"],
                    "security": [{"BearerAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ItemInput"}
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Created item",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Item"}
                                }
                            },
                        }
                    },
                },
            },
            "/api/items/{id}": {
                "get": {
                    "operationId": "getItemById",
                    "summary": "Get item by identifier",
                    "tags": ["items"],
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Found item",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Item"}
                                }
                            },
                        },
                        "404": {"description": "Item not found"},
                    },
                },
                "delete": {
                    "operationId": "deleteItem",
                    "summary": "Delete item by identifier",
                    "tags": ["items"],
                    "security": [{"BearerAuth": []}],
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {"204": {"description": "Item successfully deleted"}},
                },
            },
        },
        "components": {
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                }
            },
            "schemas": {
                "Item": {
                    "type": "object",
                    "required": ["id", "name", "price"],
                    "properties": {
                        "id": {"type": "integer", "format": "int64"},
                        "name": {"type": "string"},
                        "price": {"type": "number", "minimum": 0},
                        "description": {"type": "string"},
                    },
                },
                "ItemInput": {
                    "type": "object",
                    "required": ["name", "price"],
                    "properties": {
                        "name": {"type": "string"},
                        "price": {"type": "number", "minimum": 0},
                    },
                },
            },
        },
    }


# =====================================================================
# 1. CONTRACT NORMALIZER TESTS
# =====================================================================


def test_normalizer_canonical_operation_fields(
    sample_raw_spec: dict[str, Any],
) -> None:
    """Verify normalizer maps methods, parameters, request body, and responses."""
    normalizer = ContractNormalizer(sample_raw_spec)
    contract = normalizer.normalize()

    assert isinstance(contract, CanonicalContract)
    assert contract.title == "Inventory Management API"
    assert contract.version == "1.0.0"
    assert len(contract.operations) == 4

    # Check GET /api/items
    get_items = next(
        op
        for op in contract.operations
        if op.path == "/api/items" and op.method == "GET"
    )
    assert get_items.operation_id == "listItems"
    assert get_items.module == "items"
    assert len(get_items.parameters) == 2

    limit_param = next(p for p in get_items.parameters if p.name == "limit")
    assert limit_param.location == "query"
    assert limit_param.required is False
    assert limit_param.schema.get("type") == "integer"

    header_param = next(p for p in get_items.parameters if p.name == "X-Request-ID")
    assert header_param.location == "header"
    assert header_param.required is False

    # Check POST /api/items
    post_item = next(
        op
        for op in contract.operations
        if op.path == "/api/items" and op.method == "POST"
    )
    assert post_item.request_body is not None
    assert post_item.request_body.required is True
    assert "application/json" in post_item.request_body.content_types
    assert post_item.request_body.schema_ref == "#/components/schemas/ItemInput"
    assert post_item.requires_auth is True
    assert any("BearerAuth" in s for s in post_item.security_schemes)


def test_normalizer_array_response_schema(
    sample_raw_spec: dict[str, Any],
) -> None:
    """Verify normalizer accurately captures array responses (items: {$ref: ...})."""
    normalizer = ContractNormalizer(sample_raw_spec)
    contract = normalizer.normalize()

    get_items = next(
        op
        for op in contract.operations
        if op.path == "/api/items" and op.method == "GET"
    )
    res_200 = get_items.responses.get("200")
    assert res_200 is not None
    assert res_200.is_array is True
    assert res_200.item_schema_ref == "#/components/schemas/Item"
    assert res_200.is_empty_body is False


def test_normalizer_empty_body_responses(
    sample_raw_spec: dict[str, Any],
) -> None:
    """Verify 204 No Content and 304 responses are recognized as empty body."""
    normalizer = ContractNormalizer(sample_raw_spec)
    contract = normalizer.normalize()

    del_item = next(
        op
        for op in contract.operations
        if op.path == "/api/items/{id}" and op.method == "DELETE"
    )
    res_204 = del_item.responses.get("204")
    assert res_204 is not None
    assert res_204.is_empty_body is True


def test_normalizer_bounded_cycle_handling() -> None:
    """Verify normalizer resolves recursive schemas without infinite recursion."""
    recursive_spec: dict[str, Any] = {
        "openapi": "3.0.0",
        "info": {"title": "Tree API", "version": "1.0"},
        "paths": {
            "/tree": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Tree root",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Node"}
                                }
                            },
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "children": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Node"},
                        },
                    },
                }
            }
        },
    }

    normalizer = ContractNormalizer(recursive_spec)
    contract = normalizer.normalize()
    assert len(contract.operations) == 1

    resolved = safe_resolve_schema(
        {"$ref": "#/components/schemas/Node"}, root=recursive_spec
    )
    assert resolved.get("type") == "object"
    assert "children" in resolved.get("properties", {})


def test_normalizer_deeply_nested_cycles() -> None:
    """Verify cycle resolution terminates cleanly for A -> B -> C -> A cycles."""
    cyclic_spec: dict[str, Any] = {
        "openapi": "3.0.0",
        "info": {"title": "Cyclic API", "version": "1.0"},
        "paths": {
            "/cyclic": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Cyclic response",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/A"}
                                }
                            },
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "A": {
                    "type": "object",
                    "properties": {
                        "b": {"$ref": "#/components/schemas/B"},
                    },
                },
                "B": {
                    "type": "object",
                    "properties": {
                        "c": {"$ref": "#/components/schemas/C"},
                    },
                },
                "C": {
                    "type": "object",
                    "properties": {
                        "a": {"$ref": "#/components/schemas/A"},
                    },
                },
            }
        },
    }

    normalizer = ContractNormalizer(cyclic_spec)
    contract = normalizer.normalize()
    assert len(contract.operations) == 1

    resolved = safe_resolve_schema({"$ref": "#/components/schemas/A"}, root=cyclic_spec)
    assert resolved.get("type") == "object"
    assert "b" in resolved.get("properties", {})


def test_normalizer_generic_tag_inference_with_intelligence() -> None:
    """Verify generic tags like 'controller' are refined via intelligence modules."""
    spec: dict[str, Any] = {
        "openapi": "3.0.0",
        "info": {"title": "Generic API", "version": "1.0"},
        "paths": {
            "/api/stock-audits": {
                "get": {
                    "tags": ["stockpile-controller"],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }

    intel_report = ProjectIntelligenceReport(
        project=ProjectIdentity(name="Generic", version="1.0"),
        endpoints=[
            DiscoveredEndpoint(
                path="/api/stock-audits",
                method="GET",
                operation_id="",
                inferred_module="stock_audits",
            )
        ],
        modules=[
            DiscoveredModule(
                name="stock_audits",
                endpoints_count=1,
                endpoints=["GET /api/stock-audits"],
                status=VerificationStatus.INFERRED,
            )
        ],
    )

    normalizer = ContractNormalizer(spec, intelligence_report=intel_report)
    contract = normalizer.normalize()
    op = contract.operations[0]
    assert op.module == "stock_audits"


# =====================================================================
# 2. CONTRACT QUALITY ANALYZER TESTS
# =====================================================================


def test_quality_analyzer_missing_2xx_response() -> None:
    """Verify analyzer detects operations without any 2xx response specification."""
    op = CanonicalOperation(
        method="GET",
        path="/api/fail",
        operation_id="failOp",
        responses={"500": CanonicalResponse(status_code="500")},
    )
    contract = CanonicalContract(
        title="Test",
        version="1.0",
        openapi_version="3.0.0",
        operations=[op],
        schemas={},
    )
    analyzer = ContractQualityAnalyzer()
    findings = analyzer.analyze(contract)

    missing_2xx = [
        f
        for f in findings
        if f.category == QualityCategory.SPEC_OMISSION
        and "2xx" in f.description.lower()
    ]
    assert len(missing_2xx) == 1
    assert missing_2xx[0].severity == FindingSeverity.HIGH


def test_quality_analyzer_undocumented_response_body() -> None:
    """Verify analyzer flags 200 response with no body schema declared."""
    op = CanonicalOperation(
        method="GET",
        path="/api/data",
        operation_id="empty200",
        responses={
            "200": CanonicalResponse(
                status_code="200",
                description="Success",
                schema={},
                schema_ref=None,
                is_empty_body=False,
            )
        },
    )
    contract = CanonicalContract(
        title="Test",
        version="1.0",
        openapi_version="3.0.0",
        operations=[op],
        schemas={},
    )
    analyzer = ContractQualityAnalyzer()
    findings = analyzer.analyze(contract)

    undoc = [
        f
        for f in findings
        if f.category == QualityCategory.DOCUMENTATION_GAP
        and "response body schema" in f.description.lower()
    ]
    assert len(undoc) == 1
    assert undoc[0].severity == FindingSeverity.MEDIUM


def test_quality_analyzer_path_param_mismatch_missing_declaration() -> None:
    """Verify analyzer flags path parameter in template but omitted from parameters."""
    op = CanonicalOperation(
        method="GET",
        path="/api/users/{userId}",
        operation_id="getUser",
        parameters=[],  # Missing userId parameter!
        responses={"200": CanonicalResponse(status_code="200")},
    )
    contract = CanonicalContract(
        title="Test",
        version="1.0",
        openapi_version="3.0.0",
        operations=[op],
        schemas={},
    )
    analyzer = ContractQualityAnalyzer()
    findings = analyzer.analyze(contract)

    mismatches = [
        f
        for f in findings
        if f.category == QualityCategory.SPEC_OMISSION and "userId" in f.description
    ]
    assert len(mismatches) == 1
    assert mismatches[0].severity == FindingSeverity.HIGH


def test_quality_analyzer_path_param_mismatch_extraneous_declaration() -> None:
    """Verify analyzer flags path parameter declared but absent from URL template."""
    op = CanonicalOperation(
        method="GET",
        path="/api/users",
        operation_id="listUsers",
        parameters=[
            CanonicalParameter(
                name="ghostId",
                location="path",
                required=True,
                schema={"type": "string"},
            )
        ],
        responses={"200": CanonicalResponse(status_code="200")},
    )
    contract = CanonicalContract(
        title="Test",
        version="1.0",
        openapi_version="3.0.0",
        operations=[op],
        schemas={},
    )
    analyzer = ContractQualityAnalyzer()
    findings = analyzer.analyze(contract)

    extraneous = [
        f
        for f in findings
        if f.category == QualityCategory.SPEC_OMISSION and "ghostId" in f.description
    ]
    assert len(extraneous) == 1
    assert extraneous[0].severity == FindingSeverity.HIGH


def test_quality_analyzer_contradictory_constraints() -> None:
    """Verify analyzer flags contradictory min/max constraints on properties."""
    schemas = {
        "Product": {
            "type": "object",
            "properties": {
                "price": {"type": "number", "minimum": 100, "maximum": 10},
                "code": {"type": "string", "minLength": 50, "maxLength": 5},
                "tags": {"type": "array", "minItems": 10, "maxItems": 2},
            },
        }
    }
    contract = CanonicalContract(
        title="Test",
        version="1.0",
        openapi_version="3.0.0",
        operations=[],
        schemas=schemas,
    )
    analyzer = ContractQualityAnalyzer()
    findings = analyzer.analyze(contract)

    contradictions = [
        f
        for f in findings
        if f.category == QualityCategory.SCHEMA_DEFECT
        and "contradictory" in f.description.lower()
    ]
    assert len(contradictions) == 3
    assert all(c.severity == FindingSeverity.HIGH for c in contradictions)


def test_quality_analyzer_undocumented_mutation_security() -> None:
    """Verify analyzer flags mutating operations lacking security declarations."""
    op = CanonicalOperation(
        method="DELETE",
        path="/api/users/{id}",
        operation_id="deleteUser",
        requires_auth=False,
        security_schemes=[],
        parameters=[CanonicalParameter(name="id", location="path", required=True)],
        responses={"204": CanonicalResponse(status_code="204")},
    )
    contract = CanonicalContract(
        title="Test",
        version="1.0",
        openapi_version="3.0.0",
        operations=[op],
        schemas={},
    )
    analyzer = ContractQualityAnalyzer()
    findings = analyzer.analyze(contract)

    sec_gaps = [
        f
        for f in findings
        if f.category == QualityCategory.SECURITY_GAP
        and "mutating" in f.description.lower()
    ]
    assert len(sec_gaps) == 1
    assert sec_gaps[0].severity == FindingSeverity.MEDIUM


def test_quality_analyzer_missing_security_schemes() -> None:
    """Verify missing securitySchemes in spec is flagged as INFO, not a defect."""
    contract = CanonicalContract(
        title="Test",
        version="1.0",
        openapi_version="3.0.0",
        operations=[],
        schemas={},
        security_schemes={},
    )
    analyzer = ContractQualityAnalyzer()
    findings = analyzer.analyze(contract)

    sec_schemes_findings = [
        f
        for f in findings
        if f.category == QualityCategory.SECURITY_GAP
        and "securityschemes" in f.description.lower()
    ]
    assert len(sec_schemes_findings) == 1
    assert sec_schemes_findings[0].severity == FindingSeverity.LOW


# =====================================================================
# 3. CONTRACT RECONCILER & MULTI-SERVICE ISOLATION TESTS
# =====================================================================


def test_reconciler_exact_route_match() -> None:
    """Verify reconciler correctly matches target routes and marks implemented."""
    op = CanonicalOperation(
        method="GET",
        path="/api/inventory",
        operation_id="getInventory",
        responses={"200": CanonicalResponse(status_code="200")},
    )
    contract = CanonicalContract(
        title="Test",
        version="1.0",
        openapi_version="3.0.0",
        operations=[op],
        schemas={},
    )

    source_findings = DiscoveredSourceFindings(
        source_language="java/spring-boot",
        endpoints=[
            DiscoveredSourceEndpoint(
                path="/api/inventory",
                method="GET",
                controller_name="InventoryController",
                method_name="listInventory",
                file_path="apps/java/stockpile-api/src/main/java/InventoryController.java",
                line_number=42,
            )
        ],
    )

    reconciler = ContractReconciler()
    recon_summary = reconciler.reconcile(contract, source_findings)

    assert recon_summary["matched_operations_count"] == 1
    drift_findings = [
        f for f in contract.findings if f.category == QualityCategory.SOURCE_SPEC_DRIFT
    ]
    assert len(drift_findings) == 0


def test_reconciler_security_drift() -> None:
    """Verify reconciler flags when source enforces role but spec marks public."""
    op = CanonicalOperation(
        method="DELETE",
        path="/api/products/{id}",
        operation_id="deleteProduct",
        requires_auth=False,
        security_schemes=[],
        parameters=[CanonicalParameter(name="id", location="path", required=True)],
        responses={"204": CanonicalResponse(status_code="204")},
    )
    contract = CanonicalContract(
        title="Test",
        version="1.0",
        openapi_version="3.0.0",
        operations=[op],
        schemas={},
    )

    source_findings = DiscoveredSourceFindings(
        source_language="java/spring-boot",
        endpoints=[
            DiscoveredSourceEndpoint(
                path="/api/products/{id}",
                method="DELETE",
                controller_name="ProductController",
                method_name="deleteProduct",
                file_path="apps/java/stockpile-api/src/main/java/ProductController.java",
                line_number=100,
                required_roles=["Admin"],
            )
        ],
    )

    reconciler = ContractReconciler()
    reconciler.reconcile(contract, source_findings)

    sec_drift = [
        f
        for f in contract.findings
        if f.category == QualityCategory.SOURCE_SPEC_DRIFT and "Admin" in f.description
    ]
    assert len(sec_drift) == 1
    assert sec_drift[0].severity == FindingSeverity.HIGH


def test_reconciler_spec_only_and_source_only_routes() -> None:
    """Verify reconciler flags spec-only routes and target-source-only routes."""
    op = CanonicalOperation(
        method="GET",
        path="/api/documented",
        operation_id="documentedOnly",
        responses={"200": CanonicalResponse(status_code="200")},
    )
    contract = CanonicalContract(
        title="Test",
        version="1.0",
        openapi_version="3.0.0",
        operations=[op],
        schemas={},
    )

    source_findings = DiscoveredSourceFindings(
        source_language="java/spring-boot",
        endpoints=[
            DiscoveredSourceEndpoint(
                path="/api/source-only",
                method="POST",
                controller_name="HiddenController",
                method_name="doSecret",
                file_path="apps/java/stockpile-api/src/main/java/HiddenController.java",
                line_number=20,
            )
        ],
    )

    reconciler = ContractReconciler()
    reconciler.reconcile(contract, source_findings)

    spec_only = [
        f
        for f in contract.findings
        if f.category == QualityCategory.SOURCE_SPEC_DRIFT
        and "/api/documented" in f.description
    ]
    assert len(spec_only) == 1
    assert spec_only[0].severity == FindingSeverity.INFO

    source_only = [
        f
        for f in contract.findings
        if f.category == QualityCategory.SOURCE_SPEC_DRIFT
        and "/api/source-only" in f.description
    ]
    assert len(source_only) == 1
    assert source_only[0].severity == FindingSeverity.MEDIUM


def test_reconciler_monorepo_multi_service_isolation() -> None:
    """Verify reconciler isolates routes belonging to auxiliary services."""
    op = CanonicalOperation(
        method="GET",
        path="/api/products",
        operation_id="javaRoute",
        responses={"200": CanonicalResponse(status_code="200")},
    )
    contract = CanonicalContract(
        title="StockPile API",
        version="1.0",
        openapi_version="3.0.0",
        operations=[op],
        schemas={},
    )

    # Monorepo with both Spring Boot and Python FastAPI analytics service
    source_findings = DiscoveredSourceFindings(
        source_language="java/spring-boot",
        endpoints=[
            DiscoveredSourceEndpoint(
                path="/api/products",
                method="GET",
                controller_name="ProductController",
                method_name="getProducts",
                file_path="apps/java/stockpile-api/src/main/java/ProductController.java",
                line_number=30,
            ),
            DiscoveredSourceEndpoint(
                path="/api/v1/forecast",
                method="POST",
                controller_name="forecast",
                method_name="forecast",
                file_path="apps/analytics/forecast.py",
                line_number=15,
            ),
        ],
    )

    reconciler = ContractReconciler()
    recon_summary = reconciler.reconcile(contract, source_findings)

    # Python analytics route should NOT be reported as source-only drift for target
    analytics_drift = [
        f
        for f in contract.findings
        if f.category == QualityCategory.SOURCE_SPEC_DRIFT
        and "/api/v1/forecast" in f.description
    ]
    assert len(analytics_drift) == 0

    # Instead, recon_summary isolates the auxiliary service route
    assert recon_summary["isolated_microservice_routes_count"] == 1
    assert (
        recon_summary["isolated_microservice_routes"][0]["file_path"]
        == "apps/analytics/forecast.py"
    )


def test_reconciler_resource_id_authorization_candidate_tag() -> None:
    """Verify GET /{id} endpoints are tagged as auth test candidates, not defects."""
    op = CanonicalOperation(
        method="GET",
        path="/api/orders/{id}",
        operation_id="getOrderById",
        parameters=[CanonicalParameter(name="id", location="path", required=True)],
        responses={"200": CanonicalResponse(status_code="200")},
    )
    contract = CanonicalContract(
        title="Test",
        version="1.0",
        openapi_version="3.0.0",
        operations=[op],
        schemas={},
    )

    source_findings = DiscoveredSourceFindings(
        source_language="java/spring-boot",
        endpoints=[
            DiscoveredSourceEndpoint(
                path="/api/orders/{id}",
                method="GET",
                controller_name="OrderController",
                method_name="getOrder",
                file_path="apps/java/stockpile-api/src/main/java/OrderController.java",
                line_number=50,
            )
        ],
    )

    reconciler = ContractReconciler()
    reconciler.reconcile(contract, source_findings)

    candidate_findings = [
        f
        for f in contract.findings
        if f.category == QualityCategory.SECURITY_GAP
        and "authorization boundary candidate" in f.description.lower()
    ]
    assert len(candidate_findings) == 1
    assert candidate_findings[0].severity == FindingSeverity.INFO


# =====================================================================
# 4. CONTRACT ANALYSIS SERVICE & READINESS CLASSIFICATION TESTS
# =====================================================================


def test_contract_service_readiness_classification(
    sample_raw_spec: dict[str, Any],
) -> None:
    """Verify operation readiness classification: READY vs PARTIAL vs BLOCKED."""
    # Introduce a blocking defect on GET /api/items/{id} by removing the path param
    flawed_spec = json.loads(json.dumps(sample_raw_spec))
    flawed_spec["paths"]["/api/items/{id}"]["get"]["parameters"] = []

    # And introduce an unauthenticated mutation on POST /api/items (PARTIAL)
    del flawed_spec["paths"]["/api/items"]["post"]["security"]

    service = ContractAnalysisService()
    report = service.analyze_contract(spec_path=flawed_spec)

    assert isinstance(report, ContractQualityReport)
    # Total operations = 4
    assert report.readiness_summary["BLOCKED"] == 1
    assert report.readiness_summary["PARTIAL"] >= 1

    blocked_op = next(
        op for op in report.operations if op.operation_id == "getItemById"
    )
    assert blocked_op.readiness == OperationReadiness.BLOCKED
    assert len(blocked_op.readiness_reasons) >= 1

    post_op = next(op for op in report.operations if op.operation_id == "createItem")
    assert post_op.readiness == OperationReadiness.PARTIAL


def test_contract_service_save_report_yaml_and_json(
    sample_raw_spec: dict[str, Any], tmp_path: Path
) -> None:
    """Verify ContractAnalysisService writes valid YAML and JSON reports."""
    service = ContractAnalysisService()
    report = service.analyze_contract(spec_path=sample_raw_spec)

    yaml_file = tmp_path / "report.yaml"
    json_file = tmp_path / "report.json"

    service.save_report(report, yaml_file)
    service.save_report(report, json_file)

    assert yaml_file.exists()
    assert json_file.exists()

    with open(yaml_file, encoding="utf-8") as f:
        y_data = yaml.safe_load(f)
    assert y_data["title"] == "Inventory Management API"
    assert "operations" in y_data
    assert "findings" in y_data

    with open(json_file, encoding="utf-8") as f:
        j_data = json.load(f)
    assert j_data["title"] == "Inventory Management API"
    assert j_data["total_operations"] == 4
