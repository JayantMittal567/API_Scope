"""Controlled Reference Benchmark Application with Deterministic Seeded Defects (Task 28).

Provides an in-memory FastAPI reference application supporting:
1. Clean baseline mode (all endpoints fully contract- and invariant-compliant).
2. One-defect-at-a-time mode (seeding exactly one defect while keeping all other routes clean).
3. Controlled combined-defect mode (multi-defect interaction and deduplication testing).
4. Resettable in-memory state with deterministic fixtures and zero external network calls.
"""

from __future__ import annotations

import copy
import datetime
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse


@dataclass
class BenchmarkAppConfig:
    """Runtime configuration and seeded defect toggles for the benchmark application."""

    active_defects: set[str] = field(default_factory=set)
    app_log_path: Path | None = None
    access_log_path: Path | None = None
    simulated_delay_sec: float = 0.15
    flaky_state_counter: int = 0

    def is_active(self, defect_id: str) -> bool:
        """Check if a specific planted defect is currently activated."""
        return defect_id in self.active_defects

    def enable_defect(self, defect_id: str) -> None:
        """Activate a specific planted defect."""
        self.active_defects.add(defect_id)

    def disable_defect(self, defect_id: str) -> None:
        """Deactivate a specific planted defect."""
        self.active_defects.discard(defect_id)

    def clear_defects(self) -> None:
        """Reset to 100% clean baseline mode."""
        self.active_defects.clear()


# Default seed data
INITIAL_BENCHMARK_USERS: dict[int, dict[str, Any]] = {
    1: {
        "id": 1,
        "username": "alice",
        "email": "alice@example.com",
        "role": "customer",
    },
    2: {
        "id": 2,
        "username": "bob",
        "email": "bob@example.com",
        "role": "customer",
    },
    99: {
        "id": 99,
        "username": "admin",
        "email": "admin@example.com",
        "role": "admin",
    },
}

INITIAL_BENCHMARK_TOKENS: dict[str, int] = {
    "token-user-1": 1,
    "token-user-2": 2,
    "token-admin": 99,
}

INITIAL_BENCHMARK_PRODUCTS: dict[int, dict[str, Any]] = {
    1: {
        "id": 1,
        "name": "Mechanical Keyboard",
        "price": 89.99,
        "stock": 25,
        "category": "electronics",
    },
    2: {
        "id": 2,
        "name": "Wireless Mouse",
        "price": 49.99,
        "stock": 40,
        "category": "electronics",
    },
}

INITIAL_BENCHMARK_ORDERS: dict[int, dict[str, Any]] = {
    101: {
        "id": 101,
        "user_id": 1,
        "product_id": 1,
        "quantity": 1,
        "total_price": 89.99,
        "status": "PENDING",
    },
    102: {
        "id": 102,
        "user_id": 1,
        "product_id": 2,
        "quantity": 2,
        "total_price": 99.98,
        "status": "SHIPPED",
    },
}

INITIAL_BENCHMARK_COUPONS: dict[str, dict[str, Any]] = {
    "SAVE10": {
        "code": "SAVE10",
        "discount_pct": 10,
        "single_use": False,
        "used": False,
    },
    "WELCOME50": {
        "code": "WELCOME50",
        "discount_pct": 50,
        "single_use": True,
        "used": False,
    },
    "VINTAGE2020": {
        "code": "VINTAGE2020",
        "discount_pct": 20,
        "single_use": True,
        "used": True,
    },
}


def authenticate_benchmark_token(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> dict[str, Any]:
    """Validate Bearer authentication header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: missing or invalid Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split("Bearer ", 1)[1].strip()
    uid = INITIAL_BENCHMARK_TOKENS.get(token)
    if uid is None or uid not in INITIAL_BENCHMARK_USERS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: unrecognized token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return INITIAL_BENCHMARK_USERS[uid]


CurrentUser = Annotated[dict[str, Any], Depends(authenticate_benchmark_token)]


def create_benchmark_app(config: BenchmarkAppConfig | None = None) -> FastAPI:
    """Factory creating an isolated, configurable benchmark FastAPI application."""
    cfg = config or BenchmarkAppConfig()

    app = FastAPI(
        title="ApiScope Benchmark Reference API",
        version="1.0.0",
        description="Deterministic reference API with configurable seeded defects for Task 28.",
    )

    app.state.config = cfg

    # In-memory mutable entity tables
    users: dict[int, dict[str, Any]] = copy.deepcopy(INITIAL_BENCHMARK_USERS)
    products: dict[int, dict[str, Any]] = copy.deepcopy(INITIAL_BENCHMARK_PRODUCTS)
    orders: dict[int, dict[str, Any]] = copy.deepcopy(INITIAL_BENCHMARK_ORDERS)
    coupons: dict[str, dict[str, Any]] = copy.deepcopy(INITIAL_BENCHMARK_COUPONS)
    next_product_id: list[int] = [3]
    next_order_id: list[int] = [103]

    def reset_internal_state() -> None:
        nonlocal users, products, orders, coupons, next_product_id, next_order_id
        users = copy.deepcopy(INITIAL_BENCHMARK_USERS)
        products = copy.deepcopy(INITIAL_BENCHMARK_PRODUCTS)
        orders = copy.deepcopy(INITIAL_BENCHMARK_ORDERS)
        coupons = copy.deepcopy(INITIAL_BENCHMARK_COUPONS)
        next_product_id[0] = 3
        next_order_id[0] = 103
        cfg.flaky_state_counter = 0

    app.state.reset_state = reset_internal_state

    def log_event(level: str, module: str, message: str) -> None:
        """Write structured log line if log path configured."""
        if cfg.app_log_path:
            now_iso = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            line = f"{now_iso} {level.upper()} [{module}] {message}\n"
            try:
                cfg.app_log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(cfg.app_log_path, "a", encoding="utf-8") as f:
                    f.write(line)
            except Exception:
                pass

    def authenticate_token(
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> dict[str, Any]:
        """Validate Bearer authentication header."""
        return authenticate_benchmark_token(authorization)

    # -------------------------------------------------------------------------
    # Test State Helper Endpoints
    # -------------------------------------------------------------------------
    @app.post("/_test/reset", include_in_schema=False)
    def test_reset() -> dict[str, str]:
        """Reset state for deterministic testing."""
        reset_internal_state()
        return {"status": "RESET_OK"}

    # -------------------------------------------------------------------------
    # Health Module Endpoints
    # -------------------------------------------------------------------------
    @app.get("/api/health", tags=["health"])
    def get_health() -> Response:
        """Health check endpoint.

        # PLANTED DEFECT J01: returns text/plain contradicting application/json
        """
        if cfg.is_active("BM-DEF-J01"):
            return PlainTextResponse("OK (plain text)", status_code=200)

        return JSONResponse(
            status_code=200,
            content={
                "status": "UP",
                "dependencies": {"database": "UP", "billing": "UP"},
            },
            headers={"Content-Type": "application/json"},
        )

    # -------------------------------------------------------------------------
    # System Module Endpoints
    # -------------------------------------------------------------------------
    @app.get("/api/system/version", tags=["system"])
    def get_system_version() -> dict[str, Any]:
        """System version endpoint (Clean Control 06: healthy but untested)."""
        return {"service": "ApiScope Benchmark", "version": "1.0.0", "status": "LIVE"}

    # -------------------------------------------------------------------------
    # Products Module Endpoints
    # -------------------------------------------------------------------------
    @app.get("/api/products", tags=["products"])
    def list_products(
        category: str | None = None,
        page: int = 1,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """List products with optional category filter and pagination."""
        all_items = list(products.values())
        if category:
            all_items = [p for p in all_items if p.get("category") == category]
        start = (page - 1) * limit
        return all_items[start : start + limit]

    @app.get("/api/products/recommendations", tags=["products"])
    def get_recommendations() -> list[dict[str, Any]]:
        """Recommendations endpoint.

        # PLANTED DEFECT K01: Injected latency regression (> 40% drift)
        """
        if cfg.is_active("BM-DEF-K01"):
            time.sleep(cfg.simulated_delay_sec)
        return list(products.values())

    @app.get("/api/products/{product_id}", tags=["products"])
    def get_product(product_id: int) -> dict[str, Any]:
        """Retrieve single product by ID.

        # PLANTED DEFECT D01: Missing required data fields 'id' and 'name'
        """
        if product_id not in products:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found",
            )

        if cfg.is_active("BM-DEF-D01"):
            # Planted Bug D01: returns empty dict or dict missing id and name
            return {}

        return products[product_id]

    @app.post("/api/products", tags=["products"])
    async def create_product(
        request: Request,
        _user: CurrentUser,
    ) -> Response:
        """Create new product.

        # PLANTED DEFECT A01: Response schema violation (string price)
        # PLANTED DEFECT E01: Broken creation side-effect (omits persisting to DB)
        """
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON"
            ) from None

        name = str(body.get("name", "Product"))
        raw_price = body.get("price", 10.0)
        stock = int(body.get("stock", 0))

        pid = next_product_id[0]
        next_product_id[0] += 1

        # Check Planted Defect E01: Skip database persistence!
        if not cfg.is_active("BM-DEF-E01"):
            products[pid] = {
                "id": pid,
                "name": name,
                "price": float(raw_price) if not cfg.is_active("BM-DEF-A01") else 99.99,
                "stock": stock,
                "category": str(body.get("category", "general")),
            }

        # Check Planted Defect A01: Return string price violating numeric schema
        resp_price = "eighty-nine" if cfg.is_active("BM-DEF-A01") else float(raw_price)

        resp_payload = {
            "id": pid,
            "name": name,
            "price": resp_price,
            "stock": stock,
        }
        return JSONResponse(status_code=status.HTTP_201_CREATED, content=resp_payload)

    @app.put("/api/products/{product_id}/touch", tags=["products"])
    def touch_product(product_id: int, _user: CurrentUser) -> Response:
        """Clean 204 No Content endpoint."""
        if product_id not in products:
            raise HTTPException(status_code=404, detail="Product not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.delete("/api/products/{product_id}", tags=["products"])
    def delete_product(product_id: int, current_user: CurrentUser) -> dict[str, Any]:
        """Delete product by ID.

        # Clean Control 14: Non-admin rejected with 403 Forbidden.
        # PLANTED DEFECT F01: DELETE returns 200 without removing entity from DB.
        """
        if current_user.get("role") != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: admin privilege required to delete products",
            )

        if product_id not in products:
            raise HTTPException(status_code=404, detail="Product not found")

        # Check Planted Defect F01: Omit database removal!
        if not cfg.is_active("BM-DEF-F01"):
            del products[product_id]

        return {
            "message": "Product deleted successfully",
            "product_id": product_id,
        }

    # -------------------------------------------------------------------------
    # Orders Module Endpoints
    # -------------------------------------------------------------------------
    @app.get("/api/orders", tags=["orders"])
    def list_orders(_user: CurrentUser) -> list[dict[str, Any]]:
        """List orders for authenticated user."""
        return list(orders.values())

    @app.get("/api/orders/{order_id}", tags=["orders"])
    def get_order(order_id: int, _user: CurrentUser) -> dict[str, Any]:
        """Retrieve single order by ID."""
        if order_id not in orders:
            raise HTTPException(status_code=404, detail="Order not found")
        return orders[order_id]

    @app.post("/api/orders", tags=["orders"])
    async def create_order(request: Request, current_user: CurrentUser) -> Response:
        """Create a new order.

        # PLANTED DEFECT B01: Incorrect status code (returns 200 instead of 201)
        # PLANTED DEFECT G01: Invariant violation (permits quantity < 1)
        """
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON"
            ) from None

        product_id = int(body.get("product_id", 1))
        quantity = int(body.get("quantity", 1))

        # Check Planted Defect G01: If NOT active, enforce quantity >= 1 invariant
        if not cfg.is_active("BM-DEF-G01") and quantity < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order quantity must be at least 1",
            )

        oid = next_order_id[0]
        next_order_id[0] += 1

        product = products.get(product_id)
        unit_price = product["price"] if product else 10.0
        total_price = (
            round(unit_price * quantity, 2)
            if isinstance(unit_price, (int, float))
            else 10.0
        )

        order_record = {
            "id": oid,
            "user_id": current_user["id"],
            "product_id": product_id,
            "quantity": quantity,
            "total_price": total_price,
            "status": "PENDING",
        }
        orders[oid] = order_record

        # Check Planted Defect B01: Returns HTTP 200 OK instead of 201 Created
        status_code = (
            status.HTTP_200_OK
            if cfg.is_active("BM-DEF-B01")
            else status.HTTP_201_CREATED
        )

        return JSONResponse(status_code=status_code, content=order_record)

    @app.post("/api/orders/{order_id}/cancel", tags=["orders"])
    def cancel_order(order_id: int, _user: CurrentUser) -> dict[str, Any]:
        """Cancel an existing order.

        # PLANTED DEFECT H01: Broken state transition (allows cancelling SHIPPED order)
        """
        if order_id not in orders:
            raise HTTPException(status_code=404, detail="Order not found")

        order = orders[order_id]

        # Check Planted Defect H01: If NOT active, reject cancellation of SHIPPED orders
        if not cfg.is_active("BM-DEF-H01") and order["status"] == "SHIPPED":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot cancel an order that has already shipped",
            )

        order["status"] = "CANCELLED"
        return {
            "order_id": order_id,
            "status": "CANCELLED",
            "message": "Order cancelled successfully",
        }

    @app.post("/api/orders/{order_id}/sync-invoice", tags=["orders"])
    def sync_invoice(order_id: int, _user: CurrentUser) -> Response:
        """Synchronize invoice with billing gateway.

        # PLANTED DEFECT C01 & O01: 200 error envelope and/or S1 error log
        """
        if order_id not in orders:
            raise HTTPException(status_code=404, detail="Order not found")

        is_c01 = cfg.is_active("BM-DEF-C01")
        is_o01 = cfg.is_active("BM-DEF-O01")

        if is_c01 or is_o01:
            if is_o01:
                log_event(
                    "ERROR",
                    "orders",
                    f"BillingGatewayTimeout: invoice ledger write failed for order_id={order_id}",
                )

            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "order_id": order_id,
                    "synced": False,
                    "error": "Internal billing sync failed silently",
                },
                headers={"X-ApiScope-Warning": "Fallback billing envelope triggered"},
            )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"order_id": order_id, "synced": True, "invoice_id": "INV-1010"},
        )

    # -------------------------------------------------------------------------
    # Coupons Module Endpoints
    # -------------------------------------------------------------------------
    @app.get("/api/coupons/{code}", tags=["coupons"])
    def get_coupon(code: str, _user: CurrentUser) -> dict[str, Any]:
        """Retrieve coupon details."""
        if code not in coupons:
            raise HTTPException(status_code=404, detail="Coupon not found")
        return coupons[code]

    @app.get("/api/coupons/legacy-archive", tags=["coupons"], deprecated=True)
    def get_legacy_archive(_user: CurrentUser) -> list[dict[str, Any]]:
        """Historical coupons archive (PLANTED DEFECT N01: Zombie Z3/Z4)."""
        return list(coupons.values())

    # -------------------------------------------------------------------------
    # Users Module Endpoints
    # -------------------------------------------------------------------------
    @app.get("/api/users/{user_id}/profile", tags=["users"])
    def get_user_profile(
        user_id: int,
        request: Request,
    ) -> dict[str, Any]:
        """Retrieve user profile.

        # PLANTED DEFECT I01: Missing auth check allows unauthenticated access!
        """
        if not cfg.is_active("BM-DEF-I01"):
            # Enforce authentication in clean mode
            auth_header = request.headers.get("Authorization")
            authenticate_token(auth_header)

        if user_id not in users:
            raise HTTPException(status_code=404, detail="User not found")

        return users[user_id]

    # -------------------------------------------------------------------------
    # Telemetry & Flakiness Endpoints
    # -------------------------------------------------------------------------
    @app.get("/api/flaky-status", tags=["health"])
    def get_flaky_status() -> dict[str, Any]:
        """Flaky status probe.

        # PLANTED DEFECT L01: Alternates between 200 OK and 500 Server Error
        """
        if cfg.is_active("BM-DEF-L01"):
            cfg.flaky_state_counter += 1
            if cfg.flaky_state_counter % 2 == 0:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Intermittent telemetry backend panic",
                )
        return {"status": "OPERATIONAL", "counter": cfg.flaky_state_counter}

    # -------------------------------------------------------------------------
    # Setup / Fixture Prerequisite Endpoints
    # -------------------------------------------------------------------------
    @app.post("/api/setup/prerequisite", tags=["setup"])
    def setup_prerequisite() -> dict[str, Any]:
        """Setup prerequisite endpoint.

        # PLANTED DEFECT P01: Setup fails with 500, causing downstream tests to skip
        """
        if cfg.is_active("BM-DEF-P01"):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database setup transaction deadlocked",
            )
        return {"status": "READY", "fixture_token": "fix-123"}

    # -------------------------------------------------------------------------
    # Streaming Orders Endpoint
    # -------------------------------------------------------------------------
    @app.get("/api/orders/v2/stream", tags=["orders"])
    def stream_orders(_user: CurrentUser) -> dict[str, Any]:
        """Streaming orders endpoint.

        # PLANTED DEFECT M01: Returns 404 Not Found at runtime despite being in spec!
        """
        if cfg.is_active("BM-DEF-M01"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Endpoint not implemented in this deployment tier",
            )
        return {"stream": "active", "events": []}

    return app


def get_benchmark_openapi_spec() -> dict[str, Any]:
    """Return the authoritative OpenAPI 3.0 specification for the benchmark target."""
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "ApiScope Benchmark Reference API",
            "version": "1.0.0",
            "description": "Standard e-commerce API specification with 16 planted defects for Task 28.",
        },
        "servers": [{"url": "http://127.0.0.1:8000"}],
        "paths": {
            "/api/health": {
                "get": {
                    "operationId": "getHealth",
                    "summary": "Health status check",
                    "tags": ["health"],
                    "responses": {
                        "200": {
                            "description": "Service health payload",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["status"],
                                        "properties": {
                                            "status": {"type": "string"},
                                            "dependencies": {"type": "object"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/system/version": {
                "get": {
                    "operationId": "getSystemVersion",
                    "summary": "System version information",
                    "tags": ["system"],
                    "responses": {
                        "200": {
                            "description": "Version details",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["service", "version"],
                                        "properties": {
                                            "service": {"type": "string"},
                                            "version": {"type": "string"},
                                            "status": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/products": {
                "get": {
                    "operationId": "listProducts",
                    "summary": "List all products",
                    "tags": ["products"],
                    "parameters": [
                        {
                            "name": "category",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "page",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "integer", "default": 1},
                        },
                        {
                            "name": "limit",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "integer", "default": 10},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "List of products",
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
                    "operationId": "createProduct",
                    "summary": "Create new product",
                    "tags": ["products"],
                    "security": [{"bearerAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["name", "price"],
                                    "properties": {
                                        "name": {"type": "string"},
                                        "price": {"type": "number", "minimum": 0.01},
                                        "stock": {"type": "integer", "minimum": 0},
                                        "category": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Product created successfully",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Product"}
                                }
                            },
                        },
                        "400": {"description": "Validation error"},
                        "401": {"description": "Unauthorized"},
                    },
                },
            },
            "/api/products/recommendations": {
                "get": {
                    "operationId": "getRecommendations",
                    "summary": "Product recommendations",
                    "tags": ["products"],
                    "responses": {
                        "200": {
                            "description": "Recommended products",
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
                }
            },
            "/api/products/{product_id}": {
                "get": {
                    "operationId": "getProduct",
                    "summary": "Retrieve product by ID",
                    "tags": ["products"],
                    "parameters": [
                        {
                            "name": "product_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Product details",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Product"}
                                }
                            },
                        },
                        "404": {"description": "Product not found"},
                    },
                },
                "delete": {
                    "operationId": "deleteProduct",
                    "summary": "Delete product by ID",
                    "tags": ["products"],
                    "security": [{"bearerAuth": []}],
                    "parameters": [
                        {
                            "name": "product_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {
                        "200": {"description": "Product deleted successfully"},
                        "403": {"description": "Forbidden - requires admin"},
                        "404": {"description": "Product not found"},
                    },
                },
            },
            "/api/products/{product_id}/touch": {
                "put": {
                    "operationId": "touchProduct",
                    "summary": "Touch product timestamp",
                    "tags": ["products"],
                    "security": [{"bearerAuth": []}],
                    "parameters": [
                        {
                            "name": "product_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {
                        "204": {"description": "Product touched successfully"},
                        "404": {"description": "Product not found"},
                    },
                }
            },
            "/api/orders": {
                "get": {
                    "operationId": "listOrders",
                    "summary": "List orders for user",
                    "tags": ["orders"],
                    "security": [{"bearerAuth": []}],
                    "responses": {
                        "200": {
                            "description": "List of orders",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"$ref": "#/components/schemas/Order"},
                                    }
                                }
                            },
                        },
                        "401": {"description": "Unauthorized"},
                    },
                },
                "post": {
                    "operationId": "createOrder",
                    "summary": "Create new order",
                    "tags": ["orders"],
                    "security": [{"bearerAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["product_id", "quantity"],
                                    "properties": {
                                        "product_id": {"type": "integer"},
                                        "quantity": {"type": "integer", "minimum": 1},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Order created successfully",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Order"}
                                }
                            },
                        },
                        "400": {"description": "Invalid quantity or body"},
                        "401": {"description": "Unauthorized"},
                    },
                },
            },
            "/api/orders/{order_id}": {
                "get": {
                    "operationId": "getOrder",
                    "summary": "Retrieve order by ID",
                    "tags": ["orders"],
                    "security": [{"bearerAuth": []}],
                    "parameters": [
                        {
                            "name": "order_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Order details",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Order"}
                                }
                            },
                        },
                        "404": {"description": "Order not found"},
                    },
                }
            },
            "/api/orders/{order_id}/cancel": {
                "post": {
                    "operationId": "cancelOrder",
                    "summary": "Cancel order",
                    "tags": ["orders"],
                    "security": [{"bearerAuth": []}],
                    "parameters": [
                        {
                            "name": "order_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {
                        "200": {"description": "Order cancelled successfully"},
                        "400": {"description": "Cannot cancel shipped order"},
                        "404": {"description": "Order not found"},
                    },
                }
            },
            "/api/orders/{order_id}/sync-invoice": {
                "post": {
                    "operationId": "syncInvoice",
                    "summary": "Sync invoice with billing",
                    "tags": ["orders"],
                    "security": [{"bearerAuth": []}],
                    "parameters": [
                        {
                            "name": "order_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {
                        "200": {"description": "Invoice synced"},
                        "500": {"description": "Billing sync failed"},
                    },
                }
            },
            "/api/orders/v2/stream": {
                "get": {
                    "operationId": "streamOrders",
                    "summary": "Stream order events",
                    "tags": ["orders"],
                    "security": [{"bearerAuth": []}],
                    "responses": {
                        "200": {"description": "Streaming events"},
                        "404": {"description": "Not implemented"},
                    },
                }
            },
            "/api/coupons/{code}": {
                "get": {
                    "operationId": "getCoupon",
                    "summary": "Retrieve coupon",
                    "tags": ["coupons"],
                    "security": [{"bearerAuth": []}],
                    "parameters": [
                        {
                            "name": "code",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Coupon details",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Coupon"}
                                }
                            },
                        },
                        "404": {"description": "Coupon not found"},
                    },
                }
            },
            "/api/coupons/legacy-archive": {
                "get": {
                    "operationId": "getLegacyArchive",
                    "summary": "Historical coupons archive",
                    "tags": ["coupons"],
                    "deprecated": True,
                    "security": [{"bearerAuth": []}],
                    "responses": {
                        "200": {
                            "description": "Archive list",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "$ref": "#/components/schemas/Coupon"
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/users/{user_id}/profile": {
                "get": {
                    "operationId": "getUserProfile",
                    "summary": "Retrieve user profile",
                    "tags": ["users"],
                    "security": [{"bearerAuth": []}],
                    "parameters": [
                        {
                            "name": "user_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "User profile",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/User"}
                                }
                            },
                        },
                        "401": {"description": "Unauthorized"},
                        "404": {"description": "User not found"},
                    },
                }
            },
            "/api/flaky-status": {
                "get": {
                    "operationId": "getFlakyStatus",
                    "summary": "Flaky status check",
                    "tags": ["health"],
                    "responses": {
                        "200": {"description": "Operational"},
                        "500": {"description": "Internal error"},
                    },
                }
            },
            "/api/setup/prerequisite": {
                "post": {
                    "operationId": "setupPrerequisite",
                    "summary": "Fixture setup step",
                    "tags": ["setup"],
                    "responses": {
                        "200": {"description": "Setup ready"},
                        "500": {"description": "Setup error"},
                    },
                }
            },
        },
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                }
            },
            "schemas": {
                "Product": {
                    "type": "object",
                    "required": ["id", "name", "price", "stock"],
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "price": {"type": "number"},
                        "stock": {"type": "integer"},
                        "category": {"type": "string"},
                    },
                },
                "Order": {
                    "type": "object",
                    "required": [
                        "id",
                        "user_id",
                        "product_id",
                        "quantity",
                        "total_price",
                        "status",
                    ],
                    "properties": {
                        "id": {"type": "integer"},
                        "user_id": {"type": "integer"},
                        "product_id": {"type": "integer"},
                        "quantity": {"type": "integer", "minimum": 1},
                        "total_price": {"type": "number", "minimum": 0.0},
                        "status": {
                            "type": "string",
                            "enum": ["PENDING", "SHIPPED", "CANCELLED"],
                        },
                    },
                },
                "Coupon": {
                    "type": "object",
                    "required": ["code", "discount_pct"],
                    "properties": {
                        "code": {"type": "string"},
                        "discount_pct": {"type": "integer"},
                        "single_use": {"type": "boolean"},
                        "used": {"type": "boolean"},
                    },
                },
                "User": {
                    "type": "object",
                    "required": ["id", "username", "email", "role"],
                    "properties": {
                        "id": {"type": "integer"},
                        "username": {"type": "string"},
                        "email": {"type": "string"},
                        "role": {"type": "string"},
                    },
                },
            },
        },
    }
