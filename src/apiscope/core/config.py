"""Centralized configuration management for ApiScope.

Provides immutable application settings with environment variable resolution,
sensible defaults, and rigorous validation.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

import apiscope
from apiscope.core.exceptions import ConfigurationError

_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL"})


@dataclass(frozen=True)
class AppConfig:
    """Immutable application configuration container."""

    app_name: str = "ApiScope"
    app_version: str = apiscope.__version__
    default_timeout: float = 30.0
    default_retries: int = 0
    db_path: str = "apiscope.db"
    log_level: str = "INFO"
    environment: str = "development"


def _parse_timeout(value: Any) -> float:
    """Parse and validate request timeout setting."""
    try:
        timeout = float(value)
    except (ValueError, TypeError) as err:
        raise ConfigurationError(
            f"Invalid APISCOPE_REQUEST_TIMEOUT '{value}': must be numeric."
        ) from err

    if timeout <= 0:
        raise ConfigurationError(
            f"Invalid APISCOPE_REQUEST_TIMEOUT '{value}': must be greater than 0."
        )
    return timeout


def _parse_retries(value: Any) -> int:
    """Parse and validate retry count setting."""
    try:
        retries = int(value)
    except (ValueError, TypeError) as err:
        raise ConfigurationError(
            f"Invalid APISCOPE_MAX_RETRIES '{value}': must be an integer."
        ) from err

    if retries < 0:
        raise ConfigurationError(
            f"Invalid APISCOPE_MAX_RETRIES '{value}': cannot be negative."
        )
    return retries


def _parse_log_level(value: Any) -> str:
    """Parse and validate logging level setting."""
    if not isinstance(value, str):
        raise ConfigurationError(
            f"Invalid APISCOPE_LOG_LEVEL '{value}': must be a string."
        )
    normalized = value.strip().upper()
    if normalized == "WARN":
        normalized = "WARNING"
    if normalized not in _VALID_LOG_LEVELS:
        valid_options = ", ".join(
            sorted(lvl for lvl in _VALID_LOG_LEVELS if lvl != "WARN")
        )
        raise ConfigurationError(
            f"Invalid APISCOPE_LOG_LEVEL '{value}'. Allowed: {valid_options}."
        )
    return normalized


def load_config(
    env_file: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> AppConfig:
    """Load configuration from environment variables with optional overrides.

    Args:
        env_file: Optional path to a .env file to load.
        overrides: Optional dictionary of explicit values taking precedence.

    Returns:
        Validated immutable AppConfig instance.

    Raises:
        ConfigurationError: If any configuration value fails validation.
    """
    if env_file:
        load_dotenv(dotenv_path=env_file, override=False)
    else:
        # Load default .env if present in workspace without overwriting existing env
        load_dotenv(override=False)

    overrides = overrides or {}

    app_name = str(
        overrides.get("app_name", os.getenv("APISCOPE_APP_NAME", "ApiScope"))
    )
    app_version = str(overrides.get("app_version", apiscope.__version__))

    timeout_raw = overrides.get(
        "default_timeout", os.getenv("APISCOPE_REQUEST_TIMEOUT", 30.0)
    )
    default_timeout = _parse_timeout(timeout_raw)

    retries_raw = overrides.get("default_retries", os.getenv("APISCOPE_MAX_RETRIES", 0))
    default_retries = _parse_retries(retries_raw)

    db_path_raw = overrides.get("db_path", os.getenv("APISCOPE_DB_PATH", "apiscope.db"))
    if not isinstance(db_path_raw, str) or not db_path_raw.strip():
        raise ConfigurationError("Database path cannot be empty.")
    db_path = db_path_raw.strip()

    log_level_raw = overrides.get("log_level", os.getenv("APISCOPE_LOG_LEVEL", "INFO"))
    log_level = _parse_log_level(log_level_raw)

    environment = (
        str(overrides.get("environment", os.getenv("APISCOPE_ENV", "development")))
        .strip()
        .lower()
    )

    return AppConfig(
        app_name=app_name,
        app_version=app_version,
        default_timeout=default_timeout,
        default_retries=default_retries,
        db_path=db_path,
        log_level=log_level,
        environment=environment,
    )


def get_config() -> AppConfig:
    """Convenience accessor for the standard application configuration."""
    return load_config()
