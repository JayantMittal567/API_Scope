"""Unit tests for controlled benchmark reference application (Task 28)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from apiscope.benchmarking.reference_app import (
    BenchmarkAppConfig,
    create_benchmark_app,
    get_benchmark_openapi_spec,
)


def test_reference_app_clean_mode() -> None:
    """Verify reference application functions correctly in clean baseline mode."""
    cfg = BenchmarkAppConfig()
    app = create_benchmark_app(cfg)
    client = TestClient(app)

    # Health check
    res_health = client.get("/api/health")
    assert res_health.status_code == 200
    assert "application/json" in res_health.headers.get("content-type", "")
    assert res_health.json()["status"] == "UP"

    # Products list
    res_prod = client.get("/api/products")
    assert res_prod.status_code == 200
    prods = res_prod.json()
    assert len(prods) >= 2
    assert prods[0]["price"] > 0

    # Orders list with auth
    res_orders = client.get(
        "/api/orders", headers={"Authorization": "Bearer token-user-1"}
    )
    assert res_orders.status_code == 200
    assert len(res_orders.json()) >= 1

    # Unauthenticated orders list returns 401
    res_unauth = client.get("/api/orders")
    assert res_unauth.status_code == 401


def test_reference_app_defect_toggles() -> None:
    """Verify seeded defect toggles modify API behavior deterministically."""
    cfg = BenchmarkAppConfig()
    cfg.enable_defect("BM-DEF-A01")
    app = create_benchmark_app(cfg)
    client = TestClient(app)

    # Defect A01: String price
    res = client.post(
        "/api/products",
        json={"name": "Test Item", "price": 45.0, "stock": 5},
        headers={"Authorization": "Bearer token-user-1"},
    )
    assert res.status_code == 201
    assert res.json()["price"] == "eighty-nine"  # String price planted

    # Reset defect
    cfg.disable_defect("BM-DEF-A01")
    res_clean = client.post(
        "/api/products",
        json={"name": "Clean Item", "price": 45.0, "stock": 5},
        headers={"Authorization": "Bearer token-user-1"},
    )
    assert res_clean.status_code == 201
    assert isinstance(res_clean.json()["price"], float)


def test_reference_app_order_cancellation_defect() -> None:
    """Verify defect H01 permits cancelling a SHIPPED order, while clean mode rejects it."""
    # Clean mode: 102 is SHIPPED, cancel must be 400
    clean_app = create_benchmark_app(BenchmarkAppConfig())
    client_clean = TestClient(clean_app)
    res_clean = client_clean.post(
        "/api/orders/102/cancel",
        headers={"Authorization": "Bearer token-user-1"},
    )
    assert res_clean.status_code == 400

    # Defect H01 mode: allows cancelling SHIPPED order
    cfg = BenchmarkAppConfig()
    cfg.enable_defect("BM-DEF-H01")
    defect_app = create_benchmark_app(cfg)
    client_defect = TestClient(defect_app)
    res_defect = client_defect.post(
        "/api/orders/102/cancel",
        headers={"Authorization": "Bearer token-user-1"},
    )
    assert res_defect.status_code == 200
    assert res_defect.json()["status"] == "CANCELLED"


def test_reference_app_state_reset() -> None:
    """Verify reset_state restores initial tables and ids."""
    app = create_benchmark_app(BenchmarkAppConfig())
    client = TestClient(app)

    # Add product
    client.post(
        "/api/products",
        json={"name": "New Item", "price": 10.0, "stock": 1},
        headers={"Authorization": "Bearer token-user-1"},
    )
    res_before = client.get("/api/products")
    assert len(res_before.json()) == 3

    # Reset
    app.state.reset_state()
    res_after = client.get("/api/products")
    assert len(res_after.json()) == 2


def test_get_benchmark_openapi_spec() -> None:
    """Verify OpenAPI 3.0 specification dict generation."""
    spec = get_benchmark_openapi_spec()
    assert spec["openapi"].startswith("3.0")
    assert "paths" in spec
    assert "/api/products" in spec["paths"]
    assert "/api/orders" in spec["paths"]
    assert "/api/health" in spec["paths"]
