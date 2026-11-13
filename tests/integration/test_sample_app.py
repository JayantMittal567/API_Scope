"""Integration tests for Module M13: Sample Target Application and 12 Planted Bugs.

Covers: M13, BUG-01 through BUG-12, FR-19, FR-20, FR-21, FR-22.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sample_app.app import create_sample_app, reset_state

from apiscope.core.assertions import AssertionEngine
from apiscope.core.models import HttpResponse
from apiscope.generators.openapi_importer import load_openapi_spec
from apiscope.generators.rule_generator import RuleBasedGenerator


class TestAppClient:
    """Synchronous test client wrapping httpx.AsyncClient with ASGITransport."""

    __test__ = False

    def __init__(self, app: FastAPI) -> None:
        self.app = app

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        async def _call() -> httpx.Response:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                base_url="http://testserver",
            ) as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(_call())

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", path, **kwargs)


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    """Reset in-memory database before each test execution."""
    reset_state()


def test_sample_app_openapi_spec_loads_and_generates_rule_tests() -> None:
    """Covers: M13, FR-19, FR-20, FR-21, FR-22
    Verify sample_app/openapi.yaml loads with zero errors via load_openapi_spec
    and generates positive, negative, and boundary test suites via RuleBasedGenerator.
    """
    spec_path = Path("sample_app/openapi.yaml")
    assert spec_path.is_file()

    spec = load_openapi_spec(spec_path)
    assert spec.title == "Sample E-Commerce Store API"
    assert spec.version == "1.0.0"
    assert len(spec.endpoints) == 15

    # Generate rule-based tests
    generator = RuleBasedGenerator()
    tests = generator.generate_tests(spec)
    assert len(tests) > 0

    pos_tests = [t for t in tests if "positive" in t.tags]
    neg_tests = [t for t in tests if "negative" in t.tags]
    bnd_tests = [t for t in tests if "boundary" in t.tags]

    assert len(pos_tests) == 15
    assert len(neg_tests) > 0
    assert len(bnd_tests) > 0


def test_clean_endpoints_and_health_probe_modes(tmp_path: Path) -> None:
    """Covers: M13
    Verify baseline clean endpoints (products, orders, health probe normal & degraded).
    """
    app = create_sample_app(
        app_log_path=tmp_path / "app.log",
        access_log_path=tmp_path / "access.log",
        simulated_delay_sec=0.0,
    )
    client = TestAppClient(app)

    # 1. GET /products
    res_products = client.get("/products")
    assert res_products.status_code == 200
    products = res_products.json()
    assert len(products) == 2
    assert products[0]["name"] == "Mechanical Keyboard"

    # 2. GET /products/1
    res_product_1 = client.get("/products/1")
    assert res_product_1.status_code == 200
    assert res_product_1.json()["id"] == 1

    # 3. GET /orders/101 (authorized)
    res_order_101 = client.get(
        "/orders/101", headers={"Authorization": "Bearer token-user-1"}
    )
    assert res_order_101.status_code == 200
    assert res_order_101.json()["id"] == 101

    # 4. GET /health (normal mode)
    res_health_normal = client.get("/health")
    assert res_health_normal.status_code == 200
    assert res_health_normal.json()["status"] == "UP"
    assert res_health_normal.json()["dependencies"]["payment_gateway"] == "UP"

    # 5. GET /health (degraded mode via query param)
    res_health_degraded = client.get("/health?degraded=true")
    assert res_health_degraded.status_code == 200
    assert res_health_degraded.json()["status"] == "DEGRADED"
    assert res_health_degraded.json()["dependencies"]["payment_gateway"] == "DOWN"

    # 6. GET /health (degraded mode via header)
    res_health_hdr = client.get("/health", headers={"X-Simulate-Degraded": "true"})
    assert res_health_hdr.status_code == 200
    assert res_health_hdr.json()["status"] == "DEGRADED"


def test_planted_bug_01_missing_auth_on_delete_product(tmp_path: Path) -> None:
    """Covers: M13, BUG-01
    Verify unauthenticated DELETE /products/1 succeeds with 200 OK (omits auth check),
    and that an ApiScope negative auth assertion catches this failure.
    """
    app = create_sample_app(
        app_log_path=tmp_path / "app.log",
        access_log_path=tmp_path / "access.log",
        simulated_delay_sec=0.0,
    )
    client = TestAppClient(app)

    # BUG-01: unauthenticated call succeeds
    response = client.delete("/products/1")
    assert response.status_code == 200
    assert "deleted" in response.json()["message"]

    # Verify ApiScope AssertionEngine catches this bug
    engine = AssertionEngine()
    mock_resp = HttpResponse(
        status_code=response.status_code,
        headers=dict(response.headers),
        body=response.text,
        elapsed_ms=5.0,
    )
    # Negative test expecting 401 or 403
    eval_result = engine.evaluate(mock_resp, [{"type": "status", "range": [401, 403]}])
    assert eval_result.success is False
    assert eval_result.failed == 1


def test_planted_bug_02_idor_on_user_profile(tmp_path: Path) -> None:
    """Covers: M13, BUG-02
    Verify IDOR vulnerability: User 1 with token-user-1 accesses User 2 profile
    and receives 200 OK instead of 403 Forbidden.
    """
    app = create_sample_app(
        app_log_path=tmp_path / "app.log",
        access_log_path=tmp_path / "access.log",
        simulated_delay_sec=0.0,
    )
    client = TestAppClient(app)

    # BUG-02: User 1 accesses user 2's profile
    response = client.get(
        "/users/2/profile", headers={"Authorization": "Bearer token-user-1"}
    )
    assert response.status_code == 200
    assert response.json()["id"] == 2
    assert response.json()["username"] == "bob"


def test_planted_bug_03_reusable_single_use_coupon(tmp_path: Path) -> None:
    """Covers: M13, BUG-03
    Verify reusable single-use coupon: redeeming WELCOME50 twice both return 200 OK
    because the used flag is not mutated to True.
    """
    app = create_sample_app(
        app_log_path=tmp_path / "app.log",
        access_log_path=tmp_path / "access.log",
        simulated_delay_sec=0.0,
    )
    client = TestAppClient(app)

    headers = {"Authorization": "Bearer token-user-1"}
    payload = {"code": "WELCOME50"}

    # First redemption
    res1 = client.post("/coupons/redeem", json=payload, headers=headers)
    assert res1.status_code == 200

    # BUG-03: Second redemption of single-use coupon succeeds instead of returning 400
    res2 = client.post("/coupons/redeem", json=payload, headers=headers)
    assert res2.status_code == 200
    assert res2.json()["code"] == "WELCOME50"


def test_planted_bug_04_negative_quantity_accepted(tmp_path: Path) -> None:
    """Covers: M13, BUG-04
    Verify boundary violation: POST /orders accepts negative quantity (e.g. -5)
    instead of rejecting with 400 or 422.
    """
    app = create_sample_app(
        app_log_path=tmp_path / "app.log",
        access_log_path=tmp_path / "access.log",
        simulated_delay_sec=0.0,
    )
    client = TestAppClient(app)

    headers = {"Authorization": "Bearer token-user-1"}
    payload = {"product_id": 1, "quantity": -5}

    response = client.post("/orders", json=payload, headers=headers)
    # BUG-04: accepted with 200 OK and negative total price
    assert response.status_code == 200
    order = response.json()
    assert order["quantity"] == -5
    assert order["total_price"] < 0


def test_planted_bug_05_cancel_shipped_order(tmp_path: Path) -> None:
    """Covers: M13, BUG-05
    Verify illegal state transition: order 102 (status SHIPPED) is cancelled
    and returns 200 OK instead of 400 or 409.
    """
    app = create_sample_app(
        app_log_path=tmp_path / "app.log",
        access_log_path=tmp_path / "access.log",
        simulated_delay_sec=0.0,
    )
    client = TestAppClient(app)

    headers = {"Authorization": "Bearer token-user-1"}

    # Verify initial status is SHIPPED
    res_get = client.get("/orders/102", headers=headers)
    assert res_get.json()["status"] == "SHIPPED"

    # BUG-05: Shipped order cancellation succeeds
    response = client.post("/orders/102/cancel", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"


def test_planted_bug_06_wrong_status_code_on_order_creation(tmp_path: Path) -> None:
    """Covers: M13, BUG-06
    Verify status code violation: POST /orders returns 200 OK instead of 201 Created.
    """
    app = create_sample_app(
        app_log_path=tmp_path / "app.log",
        access_log_path=tmp_path / "access.log",
        simulated_delay_sec=0.0,
    )
    client = TestAppClient(app)

    headers = {"Authorization": "Bearer token-user-1"}
    payload = {"product_id": 1, "quantity": 1}

    response = client.post("/orders", json=payload, headers=headers)
    # BUG-06: returns 200 OK instead of 201 Created
    assert response.status_code == 200
    assert response.status_code != 201


def test_planted_bug_07_missing_required_price_field_accepted(
    tmp_path: Path,
) -> None:
    """Covers: M13, BUG-07
    Verify missing required-field validation: POST /products without 'price'
    returns 201 Created with defaulted price 0.0 instead of 400 or 422.
    """
    app = create_sample_app(
        app_log_path=tmp_path / "app.log",
        access_log_path=tmp_path / "access.log",
        simulated_delay_sec=0.0,
    )
    client = TestAppClient(app)

    headers = {"Authorization": "Bearer token-admin"}
    payload = {"name": "No-Price Gadget"}  # missing 'price'

    response = client.post("/products", json=payload, headers=headers)
    # BUG-07: accepts missing price, defaults to 0.0
    assert response.status_code == 201
    assert response.json()["price"] == 0.0


def test_planted_bug_08_slow_recommendations_endpoint(tmp_path: Path) -> None:
    """Covers: M13, BUG-08
    Verify latency regression: GET /products/recommendations takes > 50ms due to sleep.
    """
    app = create_sample_app(
        app_log_path=tmp_path / "app.log",
        access_log_path=tmp_path / "access.log",
        simulated_delay_sec=0.10,  # 100ms
    )
    client = TestAppClient(app)

    start = time.perf_counter()
    response = client.get("/products/recommendations")
    duration_ms = (time.perf_counter() - start) * 1000

    assert response.status_code == 200
    # BUG-08: Exceeds 50ms latency threshold
    assert duration_ms >= 50.0


def test_planted_bug_09_sensitive_data_leakage_in_user_profile(
    tmp_path: Path,
) -> None:
    """Covers: M13, BUG-09
    Verify data leakage: GET /users/1/profile returns password_hash and internal_ssn,
    failing schema validation when additionalProperties: false is enforced.
    """
    app = create_sample_app(
        app_log_path=tmp_path / "app.log",
        access_log_path=tmp_path / "access.log",
        simulated_delay_sec=0.0,
    )
    client = TestAppClient(app)

    response = client.get(
        "/users/1/profile", headers={"Authorization": "Bearer token-user-1"}
    )
    assert response.status_code == 200
    data = response.json()

    # BUG-09: Sensitive fields leaked
    assert "password_hash" in data
    assert "internal_ssn" in data

    # Verify ApiScope AssertionEngine catches schema violation
    engine = AssertionEngine()
    mock_resp = HttpResponse(
        status_code=response.status_code,
        headers=dict(response.headers),
        body=response.text,
        elapsed_ms=5.0,
    )
    schema_def = {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "required": ["id", "username", "email"],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "integer"},
                "username": {"type": "string"},
                "email": {"type": "string"},
            },
        },
    }
    eval_result = engine.evaluate(mock_resp, [schema_def])
    assert eval_result.success is False
    assert eval_result.failed == 1


def test_planted_bug_10_swallowed_exception_logs_error_and_returns_200_with_warning(
    tmp_path: Path,
) -> None:
    """Covers: M13, BUG-10
    Verify swallowed exception: POST /orders/101/sync-invoice returns 200 OK,
    attaches X-ApiScope-Warning header, returns error in body, and logs an ERROR line.
    """
    app_log = tmp_path / "app.log"
    app = create_sample_app(
        app_log_path=app_log,
        access_log_path=tmp_path / "access.log",
        simulated_delay_sec=0.0,
    )
    client = TestAppClient(app)

    response = client.post(
        "/orders/101/sync-invoice",
        headers={"Authorization": "Bearer token-user-1"},
    )

    # BUG-10: returns 200 OK with warning header and error body
    assert response.status_code == 200
    assert response.headers.get("x-apiscope-warning") == (
        "Deprecated sync fallback triggered"
    )
    body = response.json()
    assert body["synced"] is False
    assert "Internal billing sync failed silently" in body["error"]

    # Verify log entry was written
    assert app_log.is_file()
    log_content = app_log.read_text(encoding="utf-8")
    assert "ERROR [orders]" in log_content
    assert "InvoiceGatewayTimeout" in log_content


def test_planted_bugs_11_and_12_undocumented_unused_and_deprecated_endpoints(
    tmp_path: Path,
) -> None:
    """Covers: M13, BUG-11, BUG-12
    Verify:
    - BUG-11: /debug/internal-metrics responds 200, absent from openapi.yaml,
      and present in sample_app/logs/access.log.
    - BUG-12: /coupons/legacy-archive in openapi.yaml and absent from access.log.
    - BUG-12: /products/v1-catalog is deprecated in openapi.yaml and in access.log.
    """
    app = create_sample_app(
        app_log_path=tmp_path / "app.log",
        access_log_path=tmp_path / "access.log",
        simulated_delay_sec=0.0,
    )
    client = TestAppClient(app)

    # BUG-11: Active shadow endpoint in app
    res_shadow = client.get("/debug/internal-metrics")
    assert res_shadow.status_code == 200
    assert res_shadow.json()["status"] == "HEALTHY"

    # Verify openapi.yaml contract
    spec = load_openapi_spec("sample_app/openapi.yaml")
    spec_paths = set(spec.path_methods.keys())

    # BUG-11: Absent from spec
    assert "/debug/internal-metrics" not in spec_paths

    # BUG-12: Documented in spec
    assert "/coupons/legacy-archive" in spec_paths
    assert "/products/v1-catalog" in spec_paths

    # Verify deprecated flag on v1-catalog
    v1_ep = next(ep for ep in spec.endpoints if ep.path == "/products/v1-catalog")
    assert v1_ep.deprecated is True

    # Verify access.log fixture ground-truth
    access_log = Path("sample_app/logs/access.log")
    assert access_log.is_file()
    access_text = access_log.read_text(encoding="utf-8")

    # BUG-11: Present in access log
    assert "/debug/internal-metrics" in access_text

    # BUG-12: /coupons/legacy-archive is absent from access log
    assert "/coupons/legacy-archive" not in access_text

    # BUG-12: /products/v1-catalog is present in access log
    assert "/products/v1-catalog" in access_text
