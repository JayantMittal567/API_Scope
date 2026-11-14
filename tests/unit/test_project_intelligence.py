"""Unit tests for ApiScope Project Intelligence Engine (Task 16).

Verifies deterministic OpenAPI analysis, generic tag resolution, safe repository
inspection, framework discovery (Spring Boot & FastAPI), role boundary inference,
defensive AI enrichment, and ProjectProfile conversion.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from apiscope.core.exceptions import PathTraversalError
from apiscope.intelligence.ai_enricher import AiProjectEnricher
from apiscope.intelligence.models import (
    DiscoveredBusinessRule,
    EvidenceSourceType,
    ProjectIdentity,
    ProjectIntelligenceReport,
    VerificationStatus,
)
from apiscope.intelligence.openapi_analyzer import OpenApiAnalyzer
from apiscope.intelligence.service import ProjectIntelligenceService
from apiscope.intelligence.source_scanner import (
    SourceCodeScanner,
)

# =====================================================================
# FIXTURES
# =====================================================================


@pytest.fixture
def sample_openapi_dict() -> dict[str, Any]:
    """Sample OpenAPI 3.0 specification with generic tags and relationships."""
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Inventory Management API",
            "version": "1.2.0",
            "description": "Enterprise inventory service",
        },
        "servers": [{"url": "http://localhost:8080/api/v1"}],
        "paths": {
            "/products": {
                "get": {
                    "tags": ["generic-controller"],
                    "operationId": "listProducts",
                    "summary": "List all products",
                    "parameters": [
                        {
                            "name": "page",
                            "in": "query",
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "$ref": "#/components/schemas/Product"
                                        },
                                    }
                                }
                            },
                        }
                    },
                },
                "post": {
                    "tags": ["generic-controller"],
                    "operationId": "createProduct",
                    "summary": "Create new product",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/CreateProductRequest"
                                }
                            }
                        }
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Product"}
                                }
                            },
                        }
                    },
                },
            },
            "/products/{id}": {
                "get": {
                    "tags": ["generic-controller"],
                    "operationId": "getProductById",
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
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Product"}
                                }
                            },
                        }
                    },
                },
                "put": {
                    "tags": ["generic-controller"],
                    "operationId": "updateProduct",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {"200": {"description": "Updated"}},
                },
                "delete": {
                    "tags": ["generic-controller"],
                    "operationId": "deleteProduct",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {"204": {"description": "Deleted"}},
                },
            },
            "/suppliers": {
                "get": {
                    "tags": ["generic-controller"],
                    "operationId": "listSuppliers",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "$ref": "#/components/schemas/Supplier"
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
        },
        "components": {
            "schemas": {
                "Product": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "supplierId": {"type": "integer"},
                        "price": {"type": "number"},
                    },
                    "required": ["id", "name"],
                },
                "CreateProductRequest": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "supplierId": {"type": "integer"},
                        "price": {"type": "number"},
                    },
                },
                "Supplier": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "companyName": {"type": "string"},
                    },
                },
            },
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                }
            },
        },
    }


# =====================================================================
# 1. DETERMINISTIC OPENAPI ANALYZER TESTS
# =====================================================================


def test_openapi_analyzer_project_identity(sample_openapi_dict: dict[str, Any]) -> None:
    """Verify OpenAPI analyzer extracts project metadata and evidence."""
    analyzer = OpenApiAnalyzer.from_dict(sample_openapi_dict)
    identity = analyzer.extract_project_identity()

    assert identity.name == "Inventory Management API"
    assert identity.version == "1.2.0"
    assert identity.description == "Enterprise inventory service"
    assert len(identity.servers) == 1
    assert "http://localhost:8080/api/v1" in identity.servers
    assert len(identity.evidence) >= 1
    assert identity.evidence[0].source_type == EvidenceSourceType.OPENAPI


def test_generic_tag_heuristic_infers_modules(
    sample_openapi_dict: dict[str, Any],
) -> None:
    """Verify generic controller tag is discarded and modules inferred from paths."""
    analyzer = OpenApiAnalyzer.from_dict(sample_openapi_dict)
    endpoints, modules, missing = analyzer.analyze_endpoints_and_modules()

    assert len(endpoints) == 6
    module_names = {m.name for m in modules}
    assert "products" in module_names
    assert "suppliers" in module_names
    assert "generic-controller" not in module_names

    # Check inferred status
    for m in modules:
        assert m.status == VerificationStatus.INFERRED

    # Verify missing information recorded generic tag note
    generic_notes = [m for m in missing if m.category == "generic_tags"]
    assert len(generic_notes) == 1
    assert "generic-controller" in generic_notes[0].description


def test_domain_specific_tags_preserved() -> None:
    """Verify non-generic, domain-specific tags are confirmed directly."""
    spec_dict = {
        "openapi": "3.0.0",
        "info": {"title": "Shop", "version": "1.0"},
        "paths": {
            "/items": {
                "get": {
                    "tags": ["InventoryManagement"],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/orders": {
                "get": {
                    "tags": ["OrderFulfillment"],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
    }
    analyzer = OpenApiAnalyzer.from_dict(spec_dict)
    _, modules, missing = analyzer.analyze_endpoints_and_modules()

    module_names = {m.name for m in modules}
    assert (
        "inventorymanagement" in module_names or "InventoryManagement" in module_names
    )
    assert "orderfulfillment" in module_names or "OrderFulfillment" in module_names
    # No generic tag missing note
    assert not any(m.category == "generic_tags" for m in missing)


def test_entity_and_relationship_extraction(
    sample_openapi_dict: dict[str, Any],
) -> None:
    """Verify schema entity extraction and foreign key relationship discovery."""
    analyzer = OpenApiAnalyzer.from_dict(sample_openapi_dict)
    endpoints, _, _ = analyzer.analyze_endpoints_and_modules()
    entities, relationships = analyzer.analyze_entities_and_relationships(endpoints)

    entity_names = {e.name for e in entities}
    assert "Product" in entity_names
    assert "Supplier" in entity_names

    # Check Product has supplierId property
    product_entity = next(e for e in entities if e.name == "Product")
    assert "supplierId" in product_entity.properties
    assert product_entity.status == VerificationStatus.CONFIRMED

    # Check relationship discovered
    rel = next((r for r in relationships if r.source_entity == "Product"), None)
    assert rel is not None
    assert rel.target_entity == "Supplier"
    assert rel.foreign_key_field == "supplierId"
    assert rel.relationship_type == "foreign_key_reference"


def test_workflow_discovery_lifecycle(sample_openapi_dict: dict[str, Any]) -> None:
    """Verify CRUD lifecycle workflow synthesis for modules with POST/GET/DELETE."""
    analyzer = OpenApiAnalyzer.from_dict(sample_openapi_dict)
    endpoints, modules, _ = analyzer.analyze_endpoints_and_modules()
    workflows = analyzer.analyze_workflows(modules, endpoints)

    prod_wf = next((w for w in workflows if "product" in w.name.lower()), None)
    assert prod_wf is not None
    assert len(prod_wf.steps) >= 3  # create -> read -> update/delete
    assert prod_wf.status == VerificationStatus.INFERRED


def test_risk_area_detection(sample_openapi_dict: dict[str, Any]) -> None:
    """Verify identification of unauthenticated mutations and IDOR endpoints."""
    analyzer = OpenApiAnalyzer.from_dict(sample_openapi_dict)
    endpoints, _, _ = analyzer.analyze_endpoints_and_modules()
    risks = analyzer.analyze_risk_areas(endpoints)

    risk_categories = {r.category for r in risks}
    assert "security_authentication" in risk_categories
    assert "security_idor" in risk_categories

    idor_risk = next(r for r in risks if r.category == "security_idor")
    assert any("/products/{id}" in ep for ep in idor_risk.affected_endpoints)


def test_openapi_analyzer_full_analyze(sample_openapi_dict: dict[str, Any]) -> None:
    """Verify full analyzer.analyze() returns well-formed ProjectIntelligenceReport."""
    analyzer = OpenApiAnalyzer.from_dict(sample_openapi_dict)
    report = analyzer.analyze()

    assert isinstance(report, ProjectIntelligenceReport)
    assert report.project.name == "Inventory Management API"
    assert len(report.endpoints) == 6
    assert len(report.modules) == 2
    assert len(report.entities) >= 2
    assert len(report.workflows) >= 1
    assert len(report.risk_areas) >= 1
    assert report.schema_version == "1.0.0"


# =====================================================================
# 2. SOURCE CODE SCANNER SAFETY & SECURITY PERIMETER
# =====================================================================


def test_scanner_rejects_path_traversal(tmp_path: Path) -> None:
    """Verify SourceCodeScanner strictly rejects directory traversal attacks."""
    with pytest.raises(PathTraversalError, match="Directory traversal forbidden"):
        SourceCodeScanner(str(tmp_path) + "/../escaped")


def test_scanner_ignores_secrets_and_binaries(tmp_path: Path) -> None:
    """Verify scanner ignores secrets, .env, and binary files."""
    # Create sensitive files
    (tmp_path / ".env").write_text("DATABASE_URL=postgres://root:secret@db/prod")
    (tmp_path / ".env.production").write_text("API_KEY=prod_secret_12345")
    (tmp_path / "server.key").write_text("-----BEGIN PRIVATE KEY-----")
    (tmp_path / "artifact.jar").write_bytes(b"\x50\x4b\x03\x04")

    # Create one valid controller
    java_dir = tmp_path / "src" / "main" / "java"
    java_dir.mkdir(parents=True)
    (java_dir / "TestController.java").write_text(
        "@RestController\n"
        '@RequestMapping("/api/test")\n'
        "public class TestController {\n"
        "    @GetMapping\n"
        '    public String getTest() { return "ok"; }\n'
        "}\n"
    )

    scanner = SourceCodeScanner(tmp_path)
    findings = scanner.scan()

    assert findings.source_language == "java/spring-boot"
    assert len(findings.endpoints) == 1
    assert findings.endpoints[0].path == "/api/test"
    # Ensure sensitive files were not scanned or recorded
    for ep in findings.endpoints:
        assert ".env" not in ep.file_path
        assert "server.key" not in ep.file_path


def test_scanner_skips_large_files(tmp_path: Path) -> None:
    """Verify scanner skips files exceeding MAX_FILE_BYTES (>500KB)."""
    large_file = tmp_path / "LargeFile.java"
    # Write 600 KB file
    large_file.write_text("a" * (600 * 1024))

    scanner = SourceCodeScanner(tmp_path)
    findings = scanner.scan()

    assert findings.scanned_files_count == 0


# =====================================================================
# 3. STATIC SCANNING: SPRING BOOT & FASTAPI
# =====================================================================


def test_scanner_spring_boot_controller_and_roles(tmp_path: Path) -> None:
    """Verify Spring Boot controller, role annotations, and entity parsing."""
    java_dir = tmp_path / "com" / "example"
    java_dir.mkdir(parents=True)

    # Controller with security annotations
    (java_dir / "OrderController.java").write_text(
        "package com.example;\n"
        "@RestController\n"
        '@RequestMapping("/api/orders")\n'
        "public class OrderController {\n"
        "    @GetMapping\n"
        "    @PreAuthorize(\"hasRole('ADMIN')\")\n"
        "    public List<Order> getOrders() { return null; }\n"
        "    @PostMapping\n"
        '    @Secured("ROLE_MANAGER")\n'
        "    public Order createOrder() { return null; }\n"
        "}\n"
    )

    # JPA Entity
    (java_dir / "Order.java").write_text(
        "package com.example;\n"
        "@Entity\n"
        '@Table(name = "orders")\n'
        "public class Order {\n"
        "    @Id\n"
        "    private Long id;\n"
        "    private String customerId;\n"
        "    private Double totalAmount;\n"
        "}\n"
    )

    scanner = SourceCodeScanner(tmp_path)
    findings = scanner.scan()

    assert findings.source_language == "java/spring-boot"
    assert len(findings.endpoints) == 2
    assert "Admin" in findings.roles
    assert "Manager" in findings.roles

    # Check model
    order_model = next((m for m in findings.models if m.name == "Order"), None)
    assert order_model is not None
    assert order_model.is_entity
    assert "id" in order_model.fields
    assert "customerId" in order_model.fields
    assert "totalAmount" in order_model.fields


def test_scanner_python_fastapi_and_pydantic(tmp_path: Path) -> None:
    """Verify FastAPI router decorators and Pydantic models parsing."""
    py_dir = tmp_path / "app"
    py_dir.mkdir()

    (py_dir / "routes.py").write_text(
        "from fastapi import APIRouter\n"
        "from pydantic import BaseModel\n\n"
        "class ItemModel(BaseModel):\n"
        "    name: str\n"
        "    quantity: int\n\n"
        "router = APIRouter()\n\n"
        "@router.get('/items')\n"
        "def list_items():\n"
        "    '''Retrieve all inventory items.'''\n"
        "    return []\n\n"
        "@router.post('/items')\n"
        "def create_item(item: ItemModel):\n"
        "    return item\n"
    )

    scanner = SourceCodeScanner(tmp_path)
    findings = scanner.scan()

    assert findings.source_language == "python/fastapi"
    assert len(findings.endpoints) == 2
    assert len(findings.models) == 1

    model = findings.models[0]
    assert model.name == "ItemModel"
    assert "name" in model.fields
    assert "quantity" in model.fields

    get_ep = next(e for e in findings.endpoints if e.method == "GET")
    assert get_ep.path == "/items"
    assert "Retrieve all inventory items" in get_ep.docstring


# =====================================================================
# 4. DEFENSIVE AI ENRICHMENT (SANDBOXED & VALIDATED)
# =====================================================================


class MockAiProvider:
    """Mock LLM provider returning controlled JSON enrichment responses."""

    def __init__(self, response_json: str) -> None:
        self.response_json = response_json

    def generate_yaml(self, system_prompt: str, user_prompt: str) -> str:
        _ = (system_prompt, user_prompt)
        return self.response_json


def test_ai_enricher_parses_proposed_claims() -> None:
    """Verify AI enricher parses structured JSON into PROPOSED claims."""
    mock_payload = json.dumps(
        {
            "suggested_workflows": [
                {
                    "workflow_id": "wf_checkout",
                    "name": "Cart Checkout",
                    "description": "Full purchase workflow",
                    "steps": [
                        {"step": 1, "action": "add_to_cart"},
                        {"step": 2, "action": "pay"},
                    ],
                }
            ],
            "business_rules": [
                {
                    "rule_id": "rule_stock_limit",
                    "module": "inventory",
                    "description": "Quantity cannot be negative",
                    "endpoint": "/api/items",
                    "rule_type": "invariant",
                    "details": {"constraint": "quantity >= 0"},
                }
            ],
            "risk_areas": [
                {
                    "area_id": "risk_race_condition",
                    "category": "concurrency",
                    "description": "Stock count race condition during checkouts",
                    "affected_endpoints": ["POST /api/checkout"],
                    "severity": "HIGH",
                }
            ],
            "missing_information": [
                {
                    "category": "business_logic",
                    "description": "Missing coupon expiration policy",
                    "recommendation": "Confirm policy with product manager",
                }
            ],
        }
    )

    enricher = AiProjectEnricher(provider=MockAiProvider(mock_payload))
    workflows, rules, risks, missing = enricher.enrich(
        project_name="TestApp",
        project_version="1.0",
        source_language="python",
        endpoints=[],
        entities=[],
        doc_notes=[],
    )

    assert len(workflows) == 1
    assert workflows[0].workflow_id == "wf_checkout"
    assert workflows[0].status == VerificationStatus.PROPOSED
    assert workflows[0].evidence[0].source_type == EvidenceSourceType.LLM

    assert len(rules) == 1
    assert rules[0].status == VerificationStatus.PROPOSED
    assert rules[0].rule_type == "invariant"

    assert len(risks) == 1
    assert risks[0].severity == "HIGH"
    assert len(missing) == 1


def test_ai_enricher_handles_malformed_llm_output() -> None:
    """Verify enricher degrades gracefully when LLM returns non-JSON text."""
    enricher = AiProjectEnricher(
        provider=MockAiProvider("I am an AI and here is my advice: write more tests!")
    )
    workflows, rules, risks, missing = enricher.enrich(
        project_name="TestApp",
        project_version="1.0",
        source_language="python",
        endpoints=[],
        entities=[],
        doc_notes=[],
    )

    assert len(workflows) == 0
    assert len(rules) == 0
    assert len(risks) == 0
    assert len(missing) == 0


# =====================================================================
# 5. PROJECT PROFILE CONVERSION COMPATIBILITY
# =====================================================================


def test_report_converts_to_project_profile() -> None:
    """Verify ProjectIntelligenceReport converts cleanly to ProjectProfile."""
    report = ProjectIntelligenceReport(
        project=ProjectIdentity(name="ProfileTest", version="1.0"),
        auth=report_auth_model(),
        business_rules=[
            DiscoveredBusinessRule(
                rule_id="rule_state_1",
                module="orders",
                description="Cannot transition from CANCELLED to COMPLETED",
                endpoint="/api/orders/{id}/complete",
                rule_type="state_transition",
                details={
                    "entity": "Order",
                    "from_state": "CANCELLED",
                    "to_state": "COMPLETED",
                },
            ),
            DiscoveredBusinessRule(
                rule_id="rule_inv_1",
                module="products",
                description="Price must be positive",
                endpoint="/api/products",
                rule_type="invariant",
                details={
                    "field": "price",
                    "condition": "> 0",
                    "payload": {"price": -5},
                },
            ),
        ],
    )

    profile = report.to_project_profile()
    assert profile.project_name == "ProfileTest"
    assert profile.domain == "profiletest"
    assert len(profile.roles) == 2
    role_names = {r.role_name for r in profile.roles}
    assert "Admin" in role_names
    assert "User" in role_names

    # Check state transition
    assert len(profile.state_transitions) == 1
    st = profile.state_transitions[0]
    assert st.entity == "Order"
    assert st.description == "Cannot transition from CANCELLED to COMPLETED"

    # Check business invariant
    assert len(profile.business_invariants) == 1
    bi = profile.business_invariants[0]
    assert bi.rule_id == "rule_inv_1"
    assert bi.payload == {"price": -5}


def report_auth_model() -> Any:
    """Helper to construct auth model with role rules."""
    from apiscope.intelligence.models import DiscoveredAuthModel

    return DiscoveredAuthModel(
        auth_type="bearer_jwt",
        roles=["Admin", "User"],
        role_rules=[
            {
                "role_name": "Admin",
                "allowed_endpoints": [
                    "POST /api/products",
                    "DELETE /api/products/{id}",
                ],
                "forbidden_endpoints": [],
            },
            {
                "role_name": "User",
                "allowed_endpoints": ["GET /api/products"],
                "forbidden_endpoints": ["DELETE /api/products/{id}"],
            },
        ],
        status=VerificationStatus.CONFIRMED,
    )


# =====================================================================
# 6. PROJECT INTELLIGENCE SERVICE (ORCHESTRATION & SHADOW ENDPOINTS)
# =====================================================================


def test_service_detects_shadow_endpoints(tmp_path: Path) -> None:
    """Verify service flags endpoints in source code that are missing from OpenAPI."""
    # Create minimal OpenAPI spec with only GET /api/items
    spec_file = tmp_path / "spec.yaml"
    spec_data = {
        "openapi": "3.0.0",
        "info": {"title": "ShadowTest", "version": "1.0"},
        "paths": {
            "/api/items": {
                "get": {
                    "tags": ["items"],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    spec_file.write_text(yaml.safe_dump(spec_data))

    # Create source repo with GET /api/items AND undocumented POST /api/admin/purge
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "routes.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/api/items')\n"
        "def list_items(): return []\n"
        "@router.post('/api/admin/purge')\n"
        "def purge_database(): return {'status': 'purged'}\n"
    )

    service = ProjectIntelligenceService()
    report = service.analyze_target(spec_path=spec_file, repo_path=repo_dir)

    assert len(report.endpoints) == 1
    assert report.project.source_language == "python/fastapi"

    # Verify shadow endpoint detected in missing information
    shadow_items = [
        m for m in report.missing_information if m.category == "SHADOW_ENDPOINT"
    ]
    assert len(shadow_items) == 1
    assert "POST /api/admin/purge" in shadow_items[0].description


def test_service_save_report_yaml_and_json(tmp_path: Path) -> None:
    """Verify service saves report in both YAML and JSON formats."""
    spec_file = tmp_path / "spec.yaml"
    spec_data = {
        "openapi": "3.0.0",
        "info": {"title": "SaveTest", "version": "1.0"},
        "paths": {"/ping": {"get": {"responses": {"200": {"description": "pong"}}}}},
    }
    spec_file.write_text(yaml.safe_dump(spec_data))

    service = ProjectIntelligenceService()
    report = service.analyze_target(spec_path=spec_file)

    yaml_out = tmp_path / "report.yaml"
    json_out = tmp_path / "report.json"

    service.save_report(report, yaml_out)
    service.save_report(report, json_out)

    assert yaml_out.is_file()
    assert json_out.is_file()

    # Verify contents
    with open(yaml_out, encoding="utf-8") as f:
        y_data = yaml.safe_load(f.read())
    assert y_data["project"]["name"] == "SaveTest"

    with open(json_out, encoding="utf-8") as f:
        j_data = json.load(f)
    assert j_data["project"]["name"] == "SaveTest"


def test_ai_enrich_report_method() -> None:
    """Verify enrich_report directly enriches a ProjectIntelligenceReport."""
    mock_payload = json.dumps(
        {
            "suggested_workflows": [
                {
                    "workflow_id": "wf_1",
                    "name": "Flow 1",
                    "description": "Flow 1 desc",
                    "steps": [],
                }
            ],
            "business_rules": [],
            "risk_areas": [],
            "missing_information": [],
        }
    )
    enricher = AiProjectEnricher(provider=MockAiProvider(mock_payload))
    report = ProjectIntelligenceReport(
        project=ProjectIdentity(name="Test", version="1.0"),
    )
    res = enricher.enrich_report(report)
    assert len(res.workflows) == 1
    assert res.workflows[0].workflow_id == "wf_1"


def test_service_merge_source_security_mismatch_and_entities(
    tmp_path: Path,
) -> None:
    """Verify security mismatch detection and model merging."""
    spec_file = tmp_path / "spec.yaml"
    spec_data = {
        "openapi": "3.0.0",
        "info": {"title": "SecTest", "version": "1.0"},
        "paths": {
            "/api/secure": {
                "post": {
                    "tags": ["secure"],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        "components": {
            "schemas": {
                "User": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}},
                }
            }
        },
    }
    spec_file.write_text(yaml.safe_dump(spec_data))

    # Source code with @PreAuthorize on POST /api/secure (unauth in spec)
    # and JPA entity User with additional field 'email'
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "UserController.java").write_text(
        "package com.example;\n"
        "@RestController\n"
        '@RequestMapping("/api/secure")\n'
        "public class UserController {\n"
        "    @PostMapping\n"
        "    @PreAuthorize(\"hasRole('ADMIN')\")\n"
        '    public String save() { return "ok"; }\n'
        "}\n"
    )
    (repo_dir / "User.java").write_text(
        "package com.example;\n"
        "@Entity\n"
        "public class User {\n"
        "    private Long id;\n"
        "    private String email;\n"
        "}\n"
    )
    (repo_dir / "Order.java").write_text(
        "package com.example;\n"
        "@Entity\n"
        "public class Order {\n"
        "    private Long orderId;\n"
        "}\n"
    )

    service = ProjectIntelligenceService()
    report = service.analyze_target(spec_path=spec_file, repo_path=repo_dir)

    # Security mismatch detected
    mismatch_risks = [r for r in report.risk_areas if r.category == "SECURITY_MISMATCH"]
    assert len(mismatch_risks) == 1
    assert "POST /api/secure" in mismatch_risks[0].affected_endpoints[0]

    # Existing entity User enriched with 'email'
    user_ent = next((e for e in report.entities if e.name == "User"), None)
    assert user_ent is not None
    assert "email" in user_ent.properties

    # New entity Order added
    order_ent = next((e for e in report.entities if e.name == "Order"), None)
    assert order_ent is not None
    assert "orderId" in order_ent.properties
