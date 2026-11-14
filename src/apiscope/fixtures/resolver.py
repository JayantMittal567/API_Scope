"""Fixture loading, validation, and resolution engine (Task 20).

Resolves user-approved static test values, JSON/YAML fixture files, and
controlled environment bindings while validating type constraints and
strictly masking sensitive credentials.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from apiscope.core.exceptions import DefinitionError
from apiscope.definitions.environment import Environment
from apiscope.fixtures.models import (
    FixtureDefinition,
    FixtureScope,
    FixtureSourceType,
    FixtureStatus,
)


class FixtureResolver:
    """Loads, validates, and manages test fixtures from diverse sources."""

    __test__ = False

    def __init__(
        self,
        static_fixtures: dict[str, Any] | None = None,
        fixture_file: str | Path | None = None,
        environment: Environment | None = None,
    ) -> None:
        """Initialize resolver with optional static values, file, and environment."""
        self.registry: dict[str, FixtureDefinition] = {}
        self.sensitive_patterns = [
            re.compile(p, re.IGNORECASE)
            for p in (r"password", r"secret", r"token", r"api[_-]?key", r"auth")
        ]

        # 1. Load from static dictionary if provided
        if static_fixtures:
            for k, v in static_fixtures.items():
                self.register_fixture(
                    name=k,
                    value=v,
                    source_type=FixtureSourceType.USER_STATIC,
                    status=FixtureStatus.RESOLVED,
                )

        # 2. Load from fixture file if provided
        if fixture_file:
            self.load_from_file(fixture_file)

        # 3. Load from environment if provided
        if environment:
            self.load_from_environment(environment)

    def register_fixture(
        self,
        name: str,
        value: Any,
        entity: str = "",
        source_type: FixtureSourceType = FixtureSourceType.USER_STATIC,
        status: FixtureStatus = FixtureStatus.RESOLVED,
        constraints: dict[str, Any] | None = None,
        scope: FixtureScope = FixtureScope.GLOBAL,
        is_sensitive: bool | None = None,
        notes: str = "",
    ) -> FixtureDefinition:
        """Register a fixture in the resolver registry."""
        sensitive = (
            self._is_sensitive_key(name) if is_sensitive is None else is_sensitive
        )
        entity_name = entity or self._infer_entity_from_name(name)

        fixture = FixtureDefinition(
            name=name,
            entity=entity_name,
            source_type=source_type,
            value=value,
            is_sensitive=sensitive,
            status=status,
            constraints=constraints or {},
            scope=scope,
            notes=notes,
        )

        # Validate constraints
        is_valid, err_msg = self.validate_constraints(fixture)
        if not is_valid:
            fixture.status = FixtureStatus.INVALID
            fixture.notes = err_msg or "Failed constraint validation."

        self.registry[name] = fixture
        return fixture

    def load_from_file(self, file_path: str | Path) -> int:
        """Load fixtures from a JSON or YAML file."""
        p = Path(file_path)
        if not p.is_file():
            raise DefinitionError(f"Fixture file not found: '{file_path}'")

        content = p.read_text(encoding="utf-8")
        data = (
            yaml.safe_load(content)
            if p.suffix in (".yaml", ".yml")
            else json.loads(content)
        )

        if not isinstance(data, dict):
            raise ValueError(f"Fixture file must contain a mapping: '{file_path}'")

        # Support both flat {"productId": 42} and structured {"fixtures": [...]}
        raw_fixtures = data.get("fixtures", data)
        loaded_count = 0

        if isinstance(raw_fixtures, list):
            for item in raw_fixtures:
                if isinstance(item, dict) and "name" in item:
                    fix = FixtureDefinition.from_dict(item)
                    if fix.value is not None:
                        fix.status = FixtureStatus.RESOLVED
                    self.registry[fix.name] = fix
                    loaded_count += 1
        elif isinstance(raw_fixtures, dict):
            for k, v in raw_fixtures.items():
                if isinstance(v, dict) and "value" in v:
                    fix = FixtureDefinition.from_dict({"name": k, **v})
                    self.registry[k] = fix
                else:
                    self.register_fixture(
                        name=k,
                        value=v,
                        source_type=FixtureSourceType.FIXTURE_FILE,
                        status=FixtureStatus.RESOLVED,
                    )
                loaded_count += 1

        return loaded_count

    def load_from_environment(self, environment: Environment) -> int:
        """Load variables from an Environment model into the fixture registry."""
        count = 0
        for k, v in environment.variables.items():
            if k not in self.registry:
                self.register_fixture(
                    name=k,
                    value=v,
                    source_type=FixtureSourceType.ENVIRONMENT,
                    status=FixtureStatus.RESOLVED,
                )
                count += 1
        return count

    def has_fixture(self, fixture_name: str) -> bool:
        """Check if fixture is registered in the registry."""
        return self.get_fixture(fixture_name) is not None

    def get_fixture_value(self, fixture_name: str) -> Any | None:
        """Resolve fixture value directly."""
        return self.resolve(fixture_name)

    def resolve(self, fixture_name: str) -> Any | None:
        """Resolve value of a registered fixture, or None if unavailable."""
        fixture = self.get_fixture(fixture_name)
        if fixture and fixture.status == FixtureStatus.RESOLVED:
            return fixture.value
        return None

    def get_fixture(self, fixture_name: str) -> FixtureDefinition | None:
        """Lookup a FixtureDefinition by name or alias."""
        if fixture_name in self.registry:
            return self.registry[fixture_name]

        # Normalized alias lookup (e.g. product_id <-> productId)
        norm_target = fixture_name.replace("_", "").lower()
        for k, fix in self.registry.items():
            if k.replace("_", "").lower() == norm_target:
                return fix
            if fix.entity and fix.entity.lower() == norm_target:
                return fix

        return None

    def get_all_resolved_values(self) -> dict[str, Any]:
        """Return dictionary of all resolved fixture values."""
        return {
            k: fix.value
            for k, fix in self.registry.items()
            if fix.status == FixtureStatus.RESOLVED and fix.value is not None
        }

    def validate_constraints(
        self,
        fixture: FixtureDefinition,
    ) -> tuple[bool, str | None]:
        """Validate a fixture value against its declared constraints."""
        val = fixture.value
        if val is None:
            return True, None

        constraints = fixture.constraints
        expected_type = constraints.get("type")

        if expected_type == "integer" or isinstance(val, int):
            if not isinstance(val, int) and not (
                isinstance(val, str) and val.isdigit()
            ):
                return False, f"Value '{val}' is not an integer."
            int_val = int(val)
            if constraints.get("positive", False) and int_val <= 0:
                return False, f"Value '{int_val}' must be positive (> 0)."
            if "min" in constraints and int_val < constraints["min"]:
                return (
                    False,
                    f"Value '{int_val}' is less than min '{constraints['min']}'.",
                )
            if "max" in constraints and int_val > constraints["max"]:
                return (
                    False,
                    f"Value '{int_val}' is greater than max '{constraints['max']}'.",
                )

        if expected_type == "string" or isinstance(val, str):
            if not isinstance(val, str):
                return False, f"Value '{val}' is not a string."
            if "regex" in constraints and not re.search(constraints["regex"], val):
                return (
                    False,
                    f"Value '{val}' does not match '{constraints['regex']}'.",
                )

        return True, None

    def _is_sensitive_key(self, key_name: str) -> bool:
        """Check if a key name suggests sensitive content."""
        return any(p.search(key_name) for p in self.sensitive_patterns)

    def _infer_entity_from_name(self, name: str) -> str:
        """Infer entity name from fixture identifier (e.g. supplierId -> Supplier)."""
        clean = name.replace("fixture_", "").replace("chain_", "")
        match_id = re.match(r"^([a-zA-Z0-9]+)(?:Id|_id)$", clean, re.IGNORECASE)
        if match_id:
            return match_id.group(1).capitalize()
        return clean.capitalize()
