"""Web security helpers: CSRF protection, path validation, and secret sanitization (Task 27).

Enforces mandatory web security requirements:
- Protection against Cross-Site Request Forgery (CSRF) on state-changing dashboard actions.
- Path traversal prevention on artifact and report downloads.
- Recursive secret redaction to prevent accidental exposure of tokens/credentials.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any

from apiscope.core.exceptions import PathTraversalError
from apiscope.core.logging_config import redact_sensitive_text


def generate_csrf_token(secret: str, session_id: str = "apiscope-local") -> str:
    """Generate a time-stamped, HMAC-signed CSRF token.

    Args:
        secret: Application or session secret key.
        session_id: Optional session identifier.

    Returns:
        URL-safe, signed CSRF token string.
    """
    ts = str(int(time.time()))
    payload = f"{session_id}:{ts}"
    signature = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    raw = f"{payload}:{signature}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8")


def verify_csrf_token(
    token: str | None,
    secret: str,
    max_age_seconds: int = 86400,
) -> bool:
    """Verify that a CSRF token has a valid HMAC signature and has not expired.

    Args:
        token: The token string submitted via form or header.
        secret: Application or session secret key.
        max_age_seconds: Maximum allowed token lifetime (defaults to 24 hours).

    Returns:
        True if the token is authentic and within TTL, False otherwise.
    """
    if not token or not isinstance(token, str):
        return False

    try:
        raw = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        parts = raw.split(":")
        if len(parts) != 3:
            return False

        session_id, ts_str, signature = parts
        token_ts = int(ts_str)

        # Check expiry
        now = int(time.time())
        if now - token_ts > max_age_seconds or token_ts > now + 300:
            return False

        # Re-compute expected signature
        payload = f"{session_id}:{ts_str}"
        expected = hmac.new(
            secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(signature, expected)
    except Exception:
        return False


def validate_artifact_filename(filename: str) -> str:
    """Validate that a requested artifact filename is a safe, simple basename.

    Rejects directory separators, path traversal sequences ('..'), and null bytes.

    Args:
        filename: Requested artifact filename.

    Returns:
        Sanitized filename if valid.

    Raises:
        PathTraversalError: If traversal characters or invalid formats are detected.
    """
    if not filename or not isinstance(filename, str):
        raise PathTraversalError("Invalid empty artifact filename")

    if "/" in filename or "\\" in filename or ".." in filename or "\x00" in filename:
        raise PathTraversalError(
            f"Illegal path traversal characters in filename: '{filename}'"
        )

    clean = filename.strip()
    if clean.startswith("."):
        raise PathTraversalError(
            f"Access to hidden or dotfiles is forbidden: '{filename}'"
        )

    return clean


def sanitize_display_context(data: Any) -> Any:
    """Recursively redact sensitive text (tokens, passwords, keys) across data structures."""
    if isinstance(data, str):
        return redact_sensitive_text(data)
    elif isinstance(data, dict):
        sanitized: dict[Any, Any] = {}
        for k, v in data.items():
            k_clean = redact_sensitive_text(k) if isinstance(k, str) else k
            k_lower = str(k).lower() if isinstance(k, str) else ""
            if any(
                s in k_lower
                for s in (
                    "token",
                    "password",
                    "secret",
                    "api_key",
                    "apikey",
                    "credential",
                )
            ):
                sanitized[k_clean] = "***"
            else:
                sanitized[k_clean] = sanitize_display_context(v)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_display_context(item) for item in data]
    elif isinstance(data, tuple):
        return tuple(sanitize_display_context(item) for item in data)
    elif hasattr(data, "to_dict") and callable(data.to_dict):
        try:
            return sanitize_display_context(data.to_dict())
        except Exception:
            return data
    return data
