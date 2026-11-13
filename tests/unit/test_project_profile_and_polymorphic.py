"""Unit tests for Project Profile Loader and OpenAPI Polymorphic / CRUD Chaining.

Covers: FR-16, FR-20, FR-49, NFR-02, NFR-04, NFR-06.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apiscope.core.exceptions import DefinitionError, PathTraversalError
from apiscope.generators.openapi_importer import load_openapi_spec
from apiscope.generators.project_profile import (
    BusinessInvariantRule,
    ProjectProfile,
    RoleRule,
    StateTransitionRule,
    load_project_profile,
    parse_project_profile,
)
from apiscope.generators.rule_generator import (
    RuleBasedGenerator,
    synthesize_valid_value,
)


def test_load_project_profile_sample_app_and_stockpile() -> None:
    """Covers: FR-49, NFR-06.

    Verify loading and parsing of project profiles for sample_app and StockPile,
    checking roles, state transitions, invariants, and serialization.
    """
    # 1. Sample App Profile
    sample_profile = load_project_profile("examples/m12_sample_app_profile.yaml")
    assert isinstance(sample_profile, ProjectProfile)
    assert sample_profile.project_name == "Sample E-Commerce App"
    assert sample_profile.domain == "e-commerce"
    assert len(sample_profile.roles) >= 1

    customer_role = next(r for r in sample_profile.roles if r.role_name == "customer")
    assert isinstance(customer_role, RoleRule)
    assert customer_role.token_variable == "{{user_1_token}}"
    assert "GET /products" in customer_role.allowed_endpoints
    assert customer_role.idor_check is not None
    assert customer_role.idor_check.get("endpoint") == "GET /users/{user_id}/profile"
    assert customer_role.idor_check.get("expected_status") == 403

    assert len(sample_profile.state_transitions) >= 1
    cancel_trans = sample_profile.state_transitions[0]
    assert isinstance(cancel_trans, StateTransitionRule)
    assert cancel_trans.entity == "Order"
    assert cancel_trans.endpoint == "POST /orders/{order_id}/cancel"
    assert cancel_trans.invalid_state_id == 102
    assert (
        400 in cancel_trans.expected_status_range
        or 409 in cancel_trans.expected_status_range
    )

    assert len(sample_profile.business_invariants) >= 1
    coupon_inv = sample_profile.business_invariants[0]
    assert isinstance(coupon_inv, BusinessInvariantRule)
    assert coupon_inv.rule_type == "single_use"
    assert coupon_inv.endpoint == "POST /coupons/redeem"
    assert coupon_inv.payload.get("code") == "WELCOME50"

    # Verify serialization round-trip
    p_dict = sample_profile.to_dict()
    assert p_dict["project_name"] == "Sample E-Commerce App"
    assert len(p_dict["roles"]) == len(sample_profile.roles)
    assert len(p_dict["state_transitions"]) == len(sample_profile.state_transitions)
    assert len(p_dict["business_invariants"]) == len(sample_profile.business_invariants)

    # 2. StockPile Profile
    stockpile_profile = load_project_profile("examples/stockpile_profile.yaml")
    assert isinstance(stockpile_profile, ProjectProfile)
    assert stockpile_profile.project_name == "StockPile Inventory & Surplus Platform"
    assert stockpile_profile.domain == "inventory-management"
    assert len(stockpile_profile.roles) >= 2

    stock_roles = {r.role_name for r in stockpile_profile.roles}
    assert "warehouse_manager" in stock_roles
    assert "department_viewer" in stock_roles

    assert len(stockpile_profile.state_transitions) >= 1
    retire_rule = stockpile_profile.state_transitions[0]
    assert retire_rule.entity == "Asset"
    assert retire_rule.endpoint == "POST /surplus/listings"

    assert len(stockpile_profile.business_invariants) >= 1
    assert stockpile_profile.business_invariants[0].rule_type == "single_use"

    sp_dict = stockpile_profile.to_dict()
    assert sp_dict["project_name"] == "StockPile Inventory & Surplus Platform"
    assert "financial_cost_basis" in sp_dict["sensitive_fields_denylist"]


def test_load_project_profile_enforces_safe_path_and_validation(
    tmp_path: Path,
) -> None:
    """Covers: FR-49, NFR-02, NFR-04.

    Enforces path traversal protection, size ceiling, and YAML schema integrity.
    """
    # 1. Path traversal check
    with pytest.raises(PathTraversalError):
        load_project_profile("../../outside.yaml", base_dir=tmp_path)

    # 2. Non-existent file check
    with pytest.raises(DefinitionError, match="not found"):
        load_project_profile(tmp_path / "does_not_exist.yaml", base_dir=tmp_path)

    # 3. File size ceiling check (> 1 MB)
    large_file = tmp_path / "oversized_profile.yaml"
    # Write slightly over 1 MB (1024 * 1024 + 10 bytes)
    large_file.write_text("x" * (1024 * 1024 + 10), encoding="utf-8")
    with pytest.raises(DefinitionError, match="exceeds limit"):
        load_project_profile(large_file, base_dir=tmp_path)

    # 4. Invalid non-dict YAML root
    list_file = tmp_path / "list_profile.yaml"
    list_file.write_text("- item1\n- item2\n", encoding="utf-8")
    with pytest.raises(DefinitionError, match="must be a YAML dictionary"):
        load_project_profile(list_file, base_dir=tmp_path)

    # 5. Missing required project_name
    no_name_file = tmp_path / "no_name_profile.yaml"
    no_name_file.write_text("domain: testing\nroles: []\n", encoding="utf-8")
    with pytest.raises(DefinitionError, match="requires a non-empty 'project_name'"):
        load_project_profile(no_name_file, base_dir=tmp_path)

    # 6. parse_project_profile with non-dict input
    with pytest.raises(DefinitionError, match="must be a YAML dictionary"):
        parse_project_profile(["not", "a", "dict"])

    # 7. Invalid sub-elements
    with pytest.raises(DefinitionError, match="'roles' must be a list"):
        parse_project_profile({"project_name": "Test", "roles": "invalid_roles"})

    with pytest.raises(
        DefinitionError, match="Role rule at index 0 must be a dictionary"
    ):
        parse_project_profile({"project_name": "Test", "roles": ["not_a_dict"]})

    with pytest.raises(DefinitionError, match="requires 'role_name'"):
        parse_project_profile({"project_name": "Test", "roles": [{"forbidden": []}]})

    with pytest.raises(DefinitionError, match="must be a dictionary"):
        parse_project_profile(
            {
                "project_name": "Test",
                "roles": [{"role_name": "r1", "idor_check": "bad"}],
            }
        )

    with pytest.raises(DefinitionError, match="'state_transitions' must be a list"):
        parse_project_profile({"project_name": "Test", "state_transitions": "invalid"})

    with pytest.raises(
        DefinitionError,
        match="State transition rule at index 0 must be a dictionary",
    ):
        parse_project_profile(
            {"project_name": "Test", "state_transitions": ["invalid"]}
        )

    with pytest.raises(DefinitionError, match="requires non-empty 'endpoint'"):
        parse_project_profile(
            {
                "project_name": "Test",
                "state_transitions": [{"entity": "Order"}],
            }
        )

    with pytest.raises(DefinitionError, match="'business_invariants' must be a list"):
        parse_project_profile(
            {"project_name": "Test", "business_invariants": "invalid"}
        )

    with pytest.raises(
        DefinitionError,
        match="Business invariant rule at index 0 must be a dictionary",
    ):
        parse_project_profile(
            {"project_name": "Test", "business_invariants": ["invalid"]}
        )

    with pytest.raises(DefinitionError, match="requires non-empty 'endpoint'"):
        parse_project_profile(
            {"project_name": "Test", "business_invariants": [{"rule_id": "r1"}]}
        )


def test_synthesize_valid_value_handles_allof_oneof_anyof() -> None:
    """Covers: FR-20, NFR-06.

    Verify that synthesize_valid_value merges allOf subschemas and resolves
    oneOf and anyOf schemas deterministically.
    """
    # 1. Simple allOf combining multiple object properties
    all_of_schema = {
        "allOf": [
            {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "example": "ast-001"},
                    "name": {"type": "string", "example": "Drill Press"},
                },
                "required": ["id"],
            },
            {
                "type": "object",
                "properties": {
                    "department": {"type": "string", "example": "Tooling"},
                    "active": {"type": "boolean", "default": True},
                },
                "required": ["department"],
            },
        ]
    }
    val = synthesize_valid_value(all_of_schema)
    assert isinstance(val, dict)
    assert val.get("id") == "ast-001"
    assert val.get("name") == "Drill Press"
    assert val.get("department") == "Tooling"
    assert val.get("active") is True

    # 2. StockPile allOf schema merging (AssetDetails pattern)
    asset_details_schema = {
        "allOf": [
            {
                "type": "object",
                "required": ["name", "sku", "category"],
                "properties": {
                    "id": {"type": "string", "example": "ast-1001"},
                    "name": {"type": "string", "example": "Hydraulic Press 50T"},
                    "sku": {"type": "string", "example": "SKU-HYD-50"},
                    "category": {"type": "string", "example": "machinery"},
                    "status": {
                        "type": "string",
                        "enum": ["ACTIVE", "MAINTENANCE", "RETIRED"],
                        "default": "ACTIVE",
                    },
                },
            },
            {
                "type": "object",
                "properties": {
                    "created_by": {"type": "string", "example": "mgr_alice"},
                    "last_inspected_at": {
                        "type": "string",
                        "example": "2026-01-15T09:00:00Z",
                    },
                },
            },
        ]
    }
    asset_val = synthesize_valid_value(asset_details_schema)
    assert isinstance(asset_val, dict)
    assert asset_val.get("name") == "Hydraulic Press 50T"
    assert asset_val.get("sku") == "SKU-HYD-50"
    assert asset_val.get("category") == "machinery"
    assert asset_val.get("created_by") == "mgr_alice"
    assert asset_val.get("last_inspected_at") == "2026-01-15T09:00:00Z"

    # 3. oneOf schema resolution (selects first branch)
    one_of_schema = {
        "oneOf": [
            {"type": "string", "example": "selected-choice"},
            {"type": "integer", "example": 42},
        ]
    }
    choice = synthesize_valid_value(one_of_schema)
    assert choice == "selected-choice"

    # 4. anyOf schema resolution (selects first branch)
    any_of_schema = {
        "anyOf": [
            {"type": "integer", "minimum": 10},
            {"type": "string", "example": "fallback-str"},
        ]
    }
    any_val = synthesize_valid_value(any_of_schema)
    assert any_val == 10


def test_generate_crud_chains_pairs_post_producer_with_get_consumer() -> None:
    """Covers: FR-16, FR-20.

    Verify that RuleBasedGenerator.generate_crud_chains pairs POST producers with
    matching GET consumers (e.g. POST /assets and GET /assets/{asset_id}),
    extracting IDs from producer response and injecting them into consumer URL.
    """
    spec = load_openapi_spec("examples/stockpile_openapi.yaml")
    gen = RuleBasedGenerator()

    # Test explicit generate_crud_chains method
    chains = gen.generate_crud_chains(
        spec, module="assets", base_url_var="{{base_url}}"
    )
    assert len(chains) >= 2  # Step 1 (POST producer) and Step 2 (GET consumer)

    producer = chains[0]
    consumer = chains[1]

    assert producer.request.method == "POST"
    assert producer.request.url == "{{base_url}}/assets"
    assert "chain" in producer.tags
    assert producer.extract == {"chain_asset_id": "$.id"}

    assert consumer.request.method == "GET"
    assert consumer.request.url == "{{base_url}}/assets/{{chain_asset_id}}"
    assert "chain" in consumer.tags
    assert "{{chain_asset_id}}" in consumer.request.url

    # Also verify generate_tests with include_crud_chains=True includes the chains
    all_tests = gen.generate_tests(spec, include_crud_chains=True)
    chain_tests = [t for t in all_tests if "chain" in t.tags]
    assert len(chain_tests) >= 2
