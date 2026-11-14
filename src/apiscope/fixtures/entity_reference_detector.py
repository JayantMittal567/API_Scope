"""Entity reference and foreign key detector for API payloads (Task 20).

Identifies foreign key references in request bodies and query parameters,
distinguishing schema-valid placeholder values (e.g. productId: 1) from
verified test fixtures, preceding step extractions, or resolved variables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from apiscope.contract.models import CanonicalContract


@dataclass
class DetectedEntityReference:
    """A detected foreign key or entity reference within a request."""

    __test__ = False

    field_name: str
    target_entity: str
    current_value: Any
    is_placeholder: bool
    location: str  # "body", "query", "path"
    status: str  # "UNVERIFIED", "CHAINED", "RESOLVED"
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert detected entity reference to dictionary."""
        return {
            "field_name": self.field_name,
            "target_entity": self.target_entity,
            "current_value": self.current_value,
            "is_placeholder": self.is_placeholder,
            "location": self.location,
            "status": self.status,
            "description": self.description,
        }


class EntityReferenceDetector:
    """Detects foreign key references in request bodies and parameters."""

    __test__ = False

    def __init__(
        self,
        contract: CanonicalContract | None = None,
        intelligence_relationships: list[dict[str, Any]] | None = None,
    ) -> None:
        """Initialize detector with optional contract and intelligence context."""
        self.contract = contract
        self.intelligence_relationships = intelligence_relationships or []

        # Build lookup set of known relationship FK fields from intelligence
        self.known_fk_fields: dict[str, str] = {}
        for rel in self.intelligence_relationships:
            fk = rel.get("foreign_key_field")
            target = rel.get("target_entity", "UnknownEntity")
            if fk:
                self.known_fk_fields[fk] = target

        # Standard entity name heuristics (e.g. productId -> Product)
        self._common_entity_roots = {
            "product": "Product",
            "supplier": "Supplier",
            "order": "PurchaseOrder",
            "purchase_order": "PurchaseOrder",
            "purchaseorder": "PurchaseOrder",
            "sale": "Sale",
            "audit": "StockAudit",
            "stock_audit": "StockAudit",
            "stockaudit": "StockAudit",
            "item": "Item",
            "user": "User",
            "account": "Account",
            "customer": "Customer",
            "warehouse": "Warehouse",
            "expense": "Expense",
            "listing": "SurplusListing",
            "surplus_listing": "SurplusListing",
        }

    def detect_references(
        self,
        method: str = "",
        path: str = "",
        body: Any = None,
        params: dict[str, Any] | None = None,
        resolved_fixtures: dict[str, Any] | None = None,
    ) -> list[DetectedEntityReference]:
        """Detect entity references across request payload and query params."""
        _ = (method, path)
        return self.detect_references_in_payload(
            body=body,
            params=params,
            resolved_fixtures=resolved_fixtures,
        )

    def detect_references_in_payload(
        self,
        body: Any,
        params: dict[str, Any] | None = None,
        resolved_fixtures: dict[str, Any] | None = None,
    ) -> list[DetectedEntityReference]:
        """Detect entity references across a request payload and query params.

        Args:
            body: Request body (dict or list of dicts).
            params: Optional query parameters dict.
            resolved_fixtures: Optional mapping of already verified fixture names.

        Returns:
            List of DetectedEntityReference instances.
        """
        results: list[DetectedEntityReference] = []
        fixtures_map = resolved_fixtures or {}

        # 1. Inspect request body
        if isinstance(body, dict):
            results.extend(self._inspect_dict(body, "body", fixtures_map))
        elif isinstance(body, list):
            for item in body:
                if isinstance(item, dict):
                    results.extend(self._inspect_dict(item, "body", fixtures_map))

        # 2. Inspect query parameters
        if params and isinstance(params, dict):
            results.extend(self._inspect_dict(params, "query", fixtures_map))

        return results

    def _inspect_dict(
        self,
        data: dict[str, Any],
        location: str,
        fixtures_map: dict[str, Any],
    ) -> list[DetectedEntityReference]:
        """Inspect a single dictionary for entity reference fields."""
        detected: list[DetectedEntityReference] = []

        for key, val in data.items():
            # Check if this field represents an entity reference
            target_entity = self._infer_target_entity(key)
            if not target_entity:
                continue

            # Determine whether the value is a chained variable or raw placeholder
            is_var = isinstance(val, str) and ("{{" in val and "}}" in val)
            is_resolved = key in fixtures_map or (
                is_var and self._extract_var_name(val) in fixtures_map
            )

            if is_resolved:
                detected.append(
                    DetectedEntityReference(
                        field_name=key,
                        target_entity=target_entity,
                        current_value=val,
                        is_placeholder=False,
                        location=location,
                        status="RESOLVED",
                        description=(
                            f"Resolved with verified fixture for '{target_entity}'."
                        ),
                    )
                )
            elif is_var:
                detected.append(
                    DetectedEntityReference(
                        field_name=key,
                        target_entity=target_entity,
                        current_value=val,
                        is_placeholder=False,
                        location=location,
                        status="CHAINED",
                        description=f"Chained variable placeholder '{val}'.",
                    )
                )
            else:
                # Raw literal value (e.g. 1, "sample_id", 0) -> unverified placeholder!
                detected.append(
                    DetectedEntityReference(
                        field_name=key,
                        target_entity=target_entity,
                        current_value=val,
                        is_placeholder=True,
                        location=location,
                        status="UNVERIFIED",
                        description=(
                            f"Field '{key}' references '{target_entity}' but uses an "
                            f"unverified literal placeholder value ({val!r})."
                        ),
                    )
                )

        return detected

    def _infer_target_entity(self, field_name: str) -> str | None:
        """Infer target entity name from field naming and intelligence evidence."""
        # Check intelligence relationships first
        if field_name in self.known_fk_fields:
            return self.known_fk_fields[field_name]

        # Ignore plain "id" in request bodies unless explicit
        if field_name in ("id", "uuid"):
            return None

        # Pattern: *Id (e.g. productId, supplierId)
        match_id = re.match(r"^([a-zA-Z0-9]+)Id$", field_name)
        if match_id:
            root = match_id.group(1).lower()
            return self._common_entity_roots.get(root, match_id.group(1).capitalize())

        # Pattern: *_id (e.g. product_id, supplier_id)
        match_snake = re.match(r"^([a-zA-Z0-9_]+)_id$", field_name)
        if match_snake:
            root = match_snake.group(1).lower()
            return self._common_entity_roots.get(
                root, match_snake.group(1).replace("_", "").capitalize()
            )

        # Pattern: *Ids (e.g. productIds)
        match_ids = re.match(r"^([a-zA-Z0-9]+)Ids$", field_name)
        if match_ids:
            root = match_ids.group(1).lower()
            return self._common_entity_roots.get(root, match_ids.group(1).capitalize())

        return None

    def _extract_var_name(self, templated_str: str) -> str:
        """Extract variable name from {{var_name}}."""
        m = re.search(r"\{\{\s*([^}\s]+)\s*\}\}", templated_str)
        return m.group(1) if m else templated_str
