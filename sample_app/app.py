"""Sample E-Commerce Target Application with 12 Planted Bugs (Module M13).

Provides an in-memory FastAPI implementation with deterministic seed state,
live logging middleware, and 12 canonical planted bugs for ApiScope evaluation.
"""

from __future__ import annotations

import copy
import datetime
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

# Initial Seed State for In-Memory Database
INITIAL_USERS: dict[int, dict[str, Any]] = {
    1: {
        "id": 1,
        "username": "alice",
        "email": "alice@example.com",
        "role": "customer",
        "password_hash": "$2b$12$secretAliceHash123",
        "internal_ssn": "999-00-1234",
    },
    2: {
        "id": 2,
        "username": "bob",
        "email": "bob@example.com",
        "role": "customer",
        "password_hash": "$2b$12$secretBobHash456",
        "internal_ssn": "999-00-5678",
    },
    99: {
        "id": 99,
        "username": "admin",
        "email": "admin@example.com",
        "role": "admin",
        "password_hash": "$2b$12$secretAdminHash999",
        "internal_ssn": "999-00-9999",
    },
}

INITIAL_TOKENS: dict[str, int] = {
    "token-user-1": 1,
    "token-user-2": 2,
    "token-admin": 99,
}

INITIAL_PRODUCTS: dict[int, dict[str, Any]] = {
    1: {
        "id": 1,
        "name": "Mechanical Keyboard",
        "price": 89.99,
        "stock": 25,
    },
    2: {
        "id": 2,
        "name": "Wireless Mouse",
        "price": 49.99,
        "stock": 40,
    },
}

INITIAL_COUPONS: dict[str, dict[str, Any]] = {
    "WELCOME50": {
        "code": "WELCOME50",
        "discount_pct": 50,
        "single_use": True,
        "used": False,
    },
    "SAVE10": {
        "code": "SAVE10",
        "discount_pct": 10,
        "single_use": False,
        "used": False,
    },
}

INITIAL_ORDERS: dict[int, dict[str, Any]] = {
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

# Live in-memory mutable state
USERS: dict[int, dict[str, Any]] = copy.deepcopy(INITIAL_USERS)
PRODUCTS: dict[int, dict[str, Any]] = copy.deepcopy(INITIAL_PRODUCTS)
COUPONS: dict[str, dict[str, Any]] = copy.deepcopy(INITIAL_COUPONS)
ORDERS: dict[int, dict[str, Any]] = copy.deepcopy(INITIAL_ORDERS)
NEXT_ORDER_ID: int = 103
NEXT_PRODUCT_ID: int = 3


def reset_state() -> None:
    """Reset all in-memory entities to their exact initial seed values."""
    global USERS, PRODUCTS, COUPONS, ORDERS, NEXT_ORDER_ID, NEXT_PRODUCT_ID
    USERS = copy.deepcopy(INITIAL_USERS)
    PRODUCTS = copy.deepcopy(INITIAL_PRODUCTS)
    COUPONS = copy.deepcopy(INITIAL_COUPONS)
    ORDERS = copy.deepcopy(INITIAL_ORDERS)
    NEXT_ORDER_ID = 103
    NEXT_PRODUCT_ID = 3


def authenticate_bearer(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> dict[str, Any]:
    """Validate Bearer authentication header and return authenticated user."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: missing or invalid Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split("Bearer ", 1)[1].strip()
    user_id = INITIAL_TOKENS.get(token)
    if user_id is None or user_id not in USERS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: invalid or unrecognized Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return USERS[user_id]


CurrentUser = Annotated[dict[str, Any], Depends(authenticate_bearer)]


def log_app_event(app_instance: FastAPI, level: str, module: str, message: str) -> None:
    """Append a structured log line to the application log file if configured."""
    app_log_p: Path | None = getattr(app_instance.state, "app_log_path", None)
    if app_log_p:
        now_iso = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"{now_iso} {level.upper()} [{module}] {message}\n"
        try:
            app_log_p.parent.mkdir(parents=True, exist_ok=True)
            with open(app_log_p, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass


def create_sample_app(
    app_log_path: Path | None = None,
    access_log_path: Path | None = None,
    simulated_delay_sec: float = 0.15,
) -> FastAPI:
    """Factory creating an isolated instance of the sample e-commerce application."""
    sample_app = FastAPI(
        title="Sample E-Commerce Store API",
        version="1.0.0",
        description="FastAPI target application with 12 planted bugs for ApiScope.",
    )

    sample_app.state.app_log_path = Path(app_log_path) if app_log_path else None
    sample_app.state.access_log_path = (
        Path(access_log_path) if access_log_path else None
    )
    sample_app.state.simulated_delay_sec = simulated_delay_sec

    # Access logging middleware (Combined Log Format)
    @sample_app.middleware("http")
    async def access_log_middleware(request: Request, call_next):
        response: Response = await call_next(request)
        access_p: Path | None = getattr(request.app.state, "access_log_path", None)
        if access_p:
            now_str = datetime.datetime.now(datetime.UTC).strftime(
                "%d/%b/%Y:%H:%M:%S +0000"
            )
            method = request.method
            path = request.url.path
            status_code = response.status_code
            client_ip = (
                request.client.host
                if request.client and request.client.host
                else "127.0.0.1"
            )
            line = (
                f'{client_ip} - - [{now_str}] "{method} {path} HTTP/1.1" '
                f'{status_code} - "-" "ApiScope/1.0"\n'
            )
            try:
                access_p.parent.mkdir(parents=True, exist_ok=True)
                with open(access_p, "a", encoding="utf-8") as f:
                    f.write(line)
            except Exception:
                pass
        return response

    # -------------------------------------------------------------------------
    # Test State Helper Endpoints
    # -------------------------------------------------------------------------
    @sample_app.post("/_test/reset", include_in_schema=False)
    def handle_test_reset() -> dict[str, str]:
        """Reset in-memory data for deterministic testing."""
        reset_state()
        return {"status": "RESET_OK"}

    # -------------------------------------------------------------------------
    # Health Module Endpoints
    # -------------------------------------------------------------------------
    @sample_app.get("/health", tags=["health"])
    def get_health(
        degraded: bool = False,
        x_simulate_degraded: Annotated[
            str | None, Header(alias="X-Simulate-Degraded")
        ] = None,
    ) -> dict[str, Any]:
        """Health check endpoint supporting normal and degraded dependency modes."""
        is_degraded = degraded or (
            x_simulate_degraded is not None and x_simulate_degraded.lower() == "true"
        )
        if is_degraded:
            return {
                "status": "DEGRADED",
                "dependencies": {
                    "database": "UP",
                    "payment_gateway": "DOWN",
                },
            }
        return {
            "status": "UP",
            "dependencies": {
                "database": "UP",
                "payment_gateway": "UP",
            },
        }

    # -------------------------------------------------------------------------
    # Products Module Endpoints
    # -------------------------------------------------------------------------
    @sample_app.get("/products", tags=["products"])
    def list_products() -> list[dict[str, Any]]:
        """Clean endpoint: List all active products."""
        return list(PRODUCTS.values())

    @sample_app.get("/products/recommendations", tags=["products"])
    def get_recommendations(request: Request) -> list[dict[str, Any]]:
        """Product recommendations endpoint.

        # PLANTED BUG-08: Latency regression (artificial sleep simulates slow query)
        """
        delay = getattr(request.app.state, "simulated_delay_sec", 0.15)
        if delay > 0:
            time.sleep(delay)
        return list(PRODUCTS.values())

    @sample_app.get("/products/v1-catalog", tags=["products"], deprecated=True)
    def get_v1_catalog() -> list[dict[str, Any]]:
        """Legacy v1 product catalog.

        # PLANTED BUG-12: Deprecated-but-called endpoint (active in access.log)
        """
        return list(PRODUCTS.values())

    @sample_app.get("/products/{product_id}", tags=["products"])
    def get_product(product_id: int) -> dict[str, Any]:
        """Clean endpoint: Get product details by ID."""
        if product_id not in PRODUCTS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found",
            )
        return PRODUCTS[product_id]

    @sample_app.post(
        "/products", tags=["products"], status_code=status.HTTP_201_CREATED
    )
    async def create_product(
        request: Request,
        _current_user: CurrentUser,
    ) -> dict[str, Any]:
        """Create a new product.

        # PLANTED BUG-07: Missing required-field check (defaults price to 0.0)
        """
        global NEXT_PRODUCT_ID
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON body",
            ) from None

        if not isinstance(payload, dict) or "name" not in payload:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing required field 'name'",
            )

        name = str(payload["name"])
        # Planted Bug 07: omits price check and defaults to 0.0
        price = float(payload.get("price", 0.0))
        stock = int(payload.get("stock", 0))

        product = {
            "id": NEXT_PRODUCT_ID,
            "name": name,
            "price": price,
            "stock": stock,
        }
        PRODUCTS[NEXT_PRODUCT_ID] = product
        NEXT_PRODUCT_ID += 1
        return product

    @sample_app.delete("/products/{product_id}", tags=["products"])
    def delete_product(product_id: int) -> dict[str, Any]:
        """Delete product by ID.

        # PLANTED BUG-01: Missing auth on sensitive endpoint (omits Bearer check)
        """
        if product_id not in PRODUCTS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found",
            )
        del PRODUCTS[product_id]
        return {
            "message": "Product deleted successfully",
            "product_id": product_id,
        }

    # -------------------------------------------------------------------------
    # Orders Module Endpoints
    # -------------------------------------------------------------------------
    @sample_app.get("/orders/{order_id}", tags=["orders"])
    def get_order(
        order_id: int,
        _current_user: CurrentUser,
    ) -> dict[str, Any]:
        """Clean endpoint: Retrieve order by ID."""
        if order_id not in ORDERS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found",
            )
        return ORDERS[order_id]

    @sample_app.post("/orders", tags=["orders"])
    async def create_order(
        request: Request,
        current_user: CurrentUser,
    ) -> JSONResponse:
        """Create order.

        # PLANTED BUG-04: Negative quantity accepted (fails quantity >= 1 boundary)
        # PLANTED BUG-06: Wrong HTTP status code (returns 200 OK instead of 201 Created)
        """
        global NEXT_ORDER_ID
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON body",
            ) from None

        if not isinstance(payload, dict) or "product_id" not in payload:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing required field 'product_id'",
            )

        product_id = int(payload["product_id"])
        # Planted Bug 04: Accepts quantity <= 0 without validation
        quantity = int(payload.get("quantity", 1))

        product = PRODUCTS.get(product_id)
        unit_price = product["price"] if product else 10.0
        total_price = round(unit_price * quantity, 2)

        order = {
            "id": NEXT_ORDER_ID,
            "user_id": current_user["id"],
            "product_id": product_id,
            "quantity": quantity,
            "total_price": total_price,
            "status": "PENDING",
        }
        ORDERS[NEXT_ORDER_ID] = order
        NEXT_ORDER_ID += 1

        # Planted Bug 06: Returns status 200 instead of 201 Created
        return JSONResponse(status_code=status.HTTP_200_OK, content=order)

    @sample_app.post("/orders/{order_id}/cancel", tags=["orders"])
    def cancel_order(
        order_id: int,
        _current_user: CurrentUser,
    ) -> dict[str, Any]:
        """Cancel an order.

        # PLANTED BUG-05: Illegal state transition (allows cancelling shipped orders)
        """
        if order_id not in ORDERS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found",
            )
        order = ORDERS[order_id]
        # PLANTED BUG-05: Illegal state transition (cancels shipped order)
        order["status"] = "CANCELLED"
        return {
            "message": "Order cancelled successfully",
            "order_id": order_id,
            "status": "CANCELLED",
        }

    @sample_app.post("/orders/{order_id}/sync-invoice", tags=["orders"])
    def sync_invoice(
        order_id: int,
        request: Request,
        _current_user: CurrentUser,
    ) -> Response:
        """Synchronize invoice with billing gateway.

        # PLANTED BUG-10: Swallowed exception returning 200 with error body and warning
        """
        if order_id not in ORDERS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found",
            )

        try:
            # Planted Bug 10: Swallowed exception
            raise RuntimeError(
                "InvoiceGatewayTimeout: failed to persist invoice ledger"
            )
        except Exception as exc:
            corr_id = request.headers.get("X-Correlation-ID", "")
            corr_tag = f" [corr_id={corr_id}]" if corr_id else ""
            log_app_event(
                request.app,
                "ERROR",
                "orders",
                (
                    f"{exc}: failed to persist invoice ledger for "
                    f"order_id={order_id}{corr_tag}"
                ),
            )
            # Returns 200 OK with warning header and error payload
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "order_id": order_id,
                    "synced": False,
                    "error": "Internal billing sync failed silently",
                },
                headers={"X-ApiScope-Warning": "Deprecated sync fallback triggered"},
            )

    # -------------------------------------------------------------------------
    # Coupons Module Endpoints
    # -------------------------------------------------------------------------
    @sample_app.get("/coupons/{code}", tags=["coupons"])
    def get_coupon(
        code: str,
        _current_user: CurrentUser,
    ) -> dict[str, Any]:
        """Clean endpoint: Retrieve coupon details by code."""
        if code not in COUPONS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Coupon not found",
            )
        return COUPONS[code]

    @sample_app.post("/coupons/redeem", tags=["coupons"])
    async def redeem_coupon(
        request: Request,
        _current_user: CurrentUser,
    ) -> dict[str, Any]:
        """Redeem discount coupon.

        # PLANTED BUG-03: Reusable single-use coupon (fails to mutate used flag to True)
        """
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON body",
            ) from None

        if not isinstance(payload, dict) or "code" not in payload:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing required field 'code'",
            )

        code = str(payload["code"])
        if code not in COUPONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Coupon not found",
            )

        coupon = COUPONS[code]
        if coupon.get("used", False):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Coupon already used",
            )

        # Planted Bug 03: Fails to execute coupon["used"] = True for single-use coupons!
        return {
            "message": "Coupon redeemed successfully",
            "code": code,
            "discount_pct": coupon["discount_pct"],
        }

    @sample_app.get("/coupons/legacy-archive", tags=["coupons"])
    def get_legacy_archive(
        _current_user: CurrentUser,
    ) -> list[dict[str, Any]]:
        """Historical coupons archive.

        # PLANTED BUG-12: Unused endpoint (in openapi.yaml but 0 calls in access.log)
        """
        return [
            {
                "code": "VINTAGE2020",
                "discount_pct": 20,
                "single_use": True,
                "used": True,
            }
        ]

    # -------------------------------------------------------------------------
    # Users Module Endpoints
    # -------------------------------------------------------------------------
    @sample_app.get("/users/{user_id}/profile", tags=["users"])
    def get_user_profile(
        user_id: int,
        _current_user: CurrentUser,
    ) -> dict[str, Any]:
        """Retrieve user profile.

        # PLANTED BUG-02: IDOR on user profile (does not verify user_id == id)
        # PLANTED BUG-09: Sensitive data leakage (leaks password_hash and internal_ssn)
        """
        if user_id not in USERS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        # Planted Bug 02: omits check `if user_id != current_user["id"]: raise 403`
        # Planted Bug 09: returns full user dict leaking password_hash and internal_ssn
        return USERS[user_id]

    # -------------------------------------------------------------------------
    # Shadow / Undocumented Endpoints
    # -------------------------------------------------------------------------
    @sample_app.get("/debug/internal-metrics", include_in_schema=False)
    def get_internal_metrics() -> dict[str, Any]:
        """Internal telemetry metrics.

        # PLANTED BUG-11: Undocumented shadow endpoint (omitted from openapi.yaml)
        """
        return {
            "uptime_seconds": 1234,
            "active_connections": 4,
            "memory_mb": 128,
            "status": "HEALTHY",
        }

    return sample_app


# Default application instance for uvicorn or standard import
_default_app_log = Path(__file__).parent / "logs" / "app.log"
_default_access_log = Path(__file__).parent / "logs" / "access.log"

app: FastAPI = create_sample_app(
    app_log_path=_default_app_log,
    access_log_path=_default_access_log,
    simulated_delay_sec=0.15,
)
