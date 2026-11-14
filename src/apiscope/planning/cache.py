"""Lightweight file-based cache for AI test planning proposals (Task 18).

Prevents paying token costs for unchanged OpenAPI specifications and intelligence
findings by fingerprinting contract, intelligence context, model, and prompt schema.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path
from typing import Any

from apiscope.core.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_CACHE_DIR = Path(".apiscope/planning_cache")
PROMPT_SCHEMA_VERSION = "1.0.0"


class PlanningCache:
    """Deterministic, secret-safe file cache for AI planning proposals."""

    def __init__(self, cache_dir: Path | str | None = None) -> None:
        """Initialize cache in specified or default local directory."""
        if cache_dir is not None:
            self.cache_dir = Path(cache_dir)
        else:
            self.cache_dir = DEFAULT_CACHE_DIR

    def compute_fingerprint(
        self,
        contract_summary: str,
        intelligence_summary: str,
        model_name: str,
        settings_str: str = "",
    ) -> str:
        """Compute SHA-256 hash representing unique target planning context.

        Args:
            contract_summary: Canonical representation of contract operations.
            intelligence_summary: Canonical representation of verified rules/entities.
            model_name: Model identifier (e.g. 'gemini-2.5-flash').
            settings_str: Extra planning parameters affecting output.

        Returns:
            Hex-encoded SHA-256 string.
        """
        hasher = hashlib.sha256()
        hasher.update(PROMPT_SCHEMA_VERSION.encode("utf-8"))
        hasher.update(b"::")
        hasher.update(model_name.encode("utf-8"))
        hasher.update(b"::")
        hasher.update(contract_summary.encode("utf-8"))
        hasher.update(b"::")
        hasher.update(intelligence_summary.encode("utf-8"))
        hasher.update(b"::")
        hasher.update(settings_str.encode("utf-8"))
        return hasher.hexdigest()

    def get(self, cache_key: str) -> list[dict[str, Any]] | None:
        """Retrieve cached proposals if present.

        Args:
            cache_key: Hex string from compute_fingerprint.

        Returns:
            List of cached scenario dictionaries, or None on cache miss.
        """
        cache_file = self.cache_dir / f"{cache_key}.json"
        if not cache_file.exists():
            return None

        try:
            with open(cache_file, encoding="utf-8") as f:
                payload = json.load(f)
            if isinstance(payload, list):
                logger.debug("Planning cache hit: %s", cache_key)
                return payload
            if isinstance(payload, dict) and "scenarios" in payload:
                logger.debug("Planning cache hit: %s", cache_key)
                return payload.get("scenarios", [])
            return None
        except Exception as exc:
            logger.warning("Failed to read planning cache %s: %s", cache_file, exc)
            return None

    def put(self, cache_key: str, scenarios_data: list[dict[str, Any]]) -> None:
        """Store generated proposals in cache.

        Args:
            cache_key: Hex string from compute_fingerprint.
            scenarios_data: List of serializable scenario dictionaries.
        """
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = self.cache_dir / f"{cache_key}.json"
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(scenarios_data, f, indent=2)
            logger.debug("Saved proposals to planning cache: %s", cache_key)
        except Exception as exc:
            logger.warning("Failed to write planning cache %s: %s", cache_key, exc)

    def clear(self) -> None:
        """Clear all cached planning files."""
        if not self.cache_dir.exists():
            return
        for item in self.cache_dir.glob("*.json"):
            with contextlib.suppress(Exception):
                item.unlink()
