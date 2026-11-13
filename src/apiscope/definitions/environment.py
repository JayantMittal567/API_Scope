"""Environment configuration and variable management.

Provides environment loading, secret identification, and credential redaction,
allowing identical test definitions to execute across multiple targets.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from apiscope.core.exceptions import DefinitionError


@dataclass
class Environment:
    """Environment configuration containing variable bindings and secret identifiers."""

    name: str
    variables: dict[str, Any] = field(default_factory=dict)
    secrets: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        """Validate and normalize environment fields."""
        if not isinstance(self.name, str) or not self.name.strip():
            raise DefinitionError("Environment 'name' must be a non-empty string.")
        self.name = self.name.strip()

        if not isinstance(self.variables, dict):
            raise DefinitionError("Environment 'variables' must be a dictionary.")

        if isinstance(self.secrets, (list, tuple)):
            self.secrets = {str(s).strip() for s in self.secrets if str(s).strip()}
        elif not isinstance(self.secrets, set):
            raise DefinitionError(
                "Environment 'secrets' must be a list or set of names."
            )

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve the raw value of a variable for execution."""
        return self.variables.get(key, default)

    def masked_variables(self) -> dict[str, Any]:
        """Return variables mapping with designated secret values masked as '***'."""
        masked: dict[str, Any] = {}
        for k, v in self.variables.items():
            if k in self.secrets:
                masked[k] = "***"
            else:
                masked[k] = v
        return masked

    def redact_secrets(self, text: str) -> str:
        """Replace occurrences of raw secret values in text with '***'."""
        if not isinstance(text, str) or not text:
            return text
        sanitized = text
        for secret_name in self.secrets:
            secret_val = self.variables.get(secret_name)
            if isinstance(secret_val, str) and secret_val:
                sanitized = sanitized.replace(secret_val, "***")
        return sanitized

    def __repr__(self) -> str:
        """Format environment representation ensuring secret values remain protected."""
        return (
            f"Environment(name={self.name!r}, "
            f"variables={self.masked_variables()!r}, "
            f"secrets={sorted(self.secrets)!r})"
        )

    def __str__(self) -> str:
        """Format environment string ensuring secret values remain protected."""
        return self.__repr__()


def load_environment(file_path: str | Path) -> Environment:
    """Safely load and validate an Environment from a YAML file.

    Args:
        file_path: Path to the environment YAML file.

    Returns:
        Validated Environment instance.

    Raises:
        DefinitionError: If the file is missing, empty, or structurally invalid.
    """
    path = Path(file_path)
    if not path.is_file():
        raise DefinitionError(f"Environment file not found: '{file_path}'")

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as err:
        raise DefinitionError(
            f"Error reading environment file '{file_path}': {err}"
        ) from err

    try:
        data = yaml.safe_load(content)
    except Exception as err:
        raise DefinitionError(
            f"Malformed YAML in environment file '{file_path}': {err}"
        ) from err

    if not isinstance(data, dict):
        raise DefinitionError(
            f"Environment file '{file_path}' is empty or does not contain a mapping."
        )

    name = data.get("name")
    if not name or not isinstance(name, str):
        raise DefinitionError(
            f"Missing required 'name' in environment file '{file_path}'."
        )

    variables = data.get("variables", {})
    if variables is None:
        variables = {}
    if not isinstance(variables, dict):
        raise DefinitionError(f"'variables' must be a dictionary in '{file_path}'.")

    secrets = data.get("secrets", [])
    if secrets is None:
        secrets = []
    if not isinstance(secrets, (list, set, tuple)):
        raise DefinitionError(f"'secrets' must be a list in '{file_path}'.")

    return Environment(name=name, variables=variables, secrets=set(secrets))


class EnvironmentManager:
    """Registry for managing multiple named environments and active context."""

    def __init__(self) -> None:
        """Initialize empty environment manager."""
        self._environments: dict[str, Environment] = {}
        self._active_env_name: str | None = None

    def register(self, environment: Environment, make_active: bool = False) -> None:
        """Register an environment by its name."""
        self._environments[environment.name] = environment
        if make_active or self._active_env_name is None:
            self._active_env_name = environment.name

    def get(self, name: str) -> Environment:
        """Retrieve an environment by name."""
        if name not in self._environments:
            raise DefinitionError(f"Environment '{name}' is not registered.")
        return self._environments[name]

    def set_active(self, name: str) -> None:
        """Set the active environment."""
        if name not in self._environments:
            raise DefinitionError(f"Cannot activate unregistered environment '{name}'.")
        self._active_env_name = name

    def get_active(self) -> Environment | None:
        """Retrieve the currently active environment if one is set."""
        if self._active_env_name is not None:
            return self._environments.get(self._active_env_name)
        return None

    def list_all(self) -> list[str]:
        """Return sorted names of all registered environments."""
        return sorted(self._environments.keys())
