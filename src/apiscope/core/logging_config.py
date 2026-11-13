"""Centralized logging configuration with automated sensitive credential redaction.

Ensures consistent log formatting across CLI and Web presentation layers while
preventing tokens, passwords, and secret keys from leaking into log outputs.
"""

import logging
import re
import sys
from collections.abc import Sequence
from typing import TextIO

DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_REDACTION_PATTERNS: Sequence[tuple[re.Pattern[str], str]] = (
    # Bearer tokens in headers or strings: Bearer <token>
    (
        re.compile(r"(?i)\b(bearer\s+)[^\s,'\"]+", re.IGNORECASE),
        r"\1***",
    ),
    # Basic auth in headers: Basic <base64>
    (
        re.compile(r"(?i)\b(basic\s+)[^\s,'\"]+", re.IGNORECASE),
        r"\1***",
    ),
    # Basic auth passwords embedded in URLs: http://user:pass@host
    (
        re.compile(r"(https?://[^:\s/]+:)[^@\s/]+(@)", re.IGNORECASE),
        r"\1***\2",
    ),
    # Key-value assignments: password=..., token=..., api_key=..., secret=...
    (
        re.compile(
            r"""(?i)\b(password|secret|token|api[_-]?key|apikey)\s*([:=])\s*['"]?([^\s,'"&]+)['"]?""",
            re.IGNORECASE,
        ),
        r"\1\2'***'",
    ),
    # Raw Authorization header without Bearer/Basic: Authorization: <token>
    (
        re.compile(
            r"""(?i)\b(authorization)\s*:\s*(?!bearer|basic)['"]?([^\s,'"&]+)['"]?""",
            re.IGNORECASE,
        ),
        r"\1: '***'",
    ),
)


def redact_sensitive_text(text: str) -> str:
    """Sanitize sensitive credentials, tokens, and passwords from log strings.

    Args:
        text: Raw log message text.

    Returns:
        Sanitized message string with sensitive tokens masked as '***'.
    """
    sanitized = text
    for pattern, replacement in _REDACTION_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


class SensitiveDataFormatter(logging.Formatter):
    """Logging formatter that automatically scrubs sensitive credentials."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record and redact sensitive credentials."""
        formatted = super().format(record)
        return redact_sensitive_text(formatted)


def configure_logging(
    level: str | int = "INFO",
    stream: TextIO | None = None,
) -> logging.Logger:
    """Initialize and configure the centralized ApiScope logger.

    Args:
        level: Logging level as a string (e.g. 'DEBUG', 'INFO') or integer constant.
        stream: Target output stream for the handler (defaults to sys.stderr).

    Returns:
        Configured 'apiscope' logger instance.
    """
    if isinstance(level, str):
        level_int = getattr(logging, level.strip().upper(), logging.INFO)
    else:
        level_int = int(level)

    logger = logging.getLogger("apiscope")
    logger.setLevel(level_int)

    # Remove existing handlers to maintain idempotency
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setLevel(level_int)
    formatter = SensitiveDataFormatter(DEFAULT_LOG_FORMAT, DEFAULT_DATE_FORMAT)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # Prevent duplicate emission to root handlers
    logger.propagate = False

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Retrieve an ApiScope namespaced logger.

    Args:
        name: Sub-module name (e.g. 'core', 'cli', 'web').

    Returns:
        Configured logger instance under the 'apiscope' namespace.
    """
    if not name or name == "apiscope":
        return logging.getLogger("apiscope")
    if name.startswith("apiscope."):
        return logging.getLogger(name)
    return logging.getLogger(f"apiscope.{name}")
