"""Domain models for HTTP requests, responses, and authentication.

Provides immutable representations and contracts for core engine operations
while protecting sensitive credentials from accidental logging and exposure.
"""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from apiscope.core.exceptions import InvalidRequestError, UnsupportedMethodError

_SENSITIVE_HEADER_KEYS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "api-key",
        "apikey",
        "cookie",
        "set-cookie",
        "token",
        "secret",
    }
)


class HttpMethod(StrEnum):
    """Supported HTTP methods for ApiScope execution."""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"

    @classmethod
    def from_string(cls, value: str) -> "HttpMethod":
        """Normalize and parse an HTTP method string.

        Args:
            value: HTTP method name (case-insensitive).

        Returns:
            Matching HttpMethod enum member.

        Raises:
            UnsupportedMethodError: If the method is not supported by ApiScope.
        """
        if not isinstance(value, str):
            raise UnsupportedMethodError(
                f"HTTP method must be a string, got {type(value).__name__}."
            )
        normalized = value.strip().upper()
        try:
            return cls(normalized)
        except ValueError:
            supported = ", ".join(m.value for m in cls)
            raise UnsupportedMethodError(
                f"Unsupported HTTP method: '{normalized}'. Supported: {supported}."
            ) from None


class BodyType(StrEnum):
    """Supported payload body formats."""

    JSON = "json"
    FORM = "form"
    RAW = "raw"

    @classmethod
    def from_string(cls, value: str) -> "BodyType":
        """Normalize and parse a body type string.

        Args:
            value: Body type name (case-insensitive).

        Returns:
            Matching BodyType enum member.

        Raises:
            InvalidRequestError: If the body type is unrecognized.
        """
        if not isinstance(value, str):
            raise InvalidRequestError(
                f"Body type must be a string, got {type(value).__name__}."
            )
        normalized = value.strip().lower()
        try:
            return cls(normalized)
        except ValueError:
            supported = ", ".join(t.value for t in cls)
            raise InvalidRequestError(
                f"Invalid body type: '{normalized}'. Supported types: {supported}."
            ) from None


class AuthType(StrEnum):
    """Supported authentication types."""

    BEARER = "bearer"
    BASIC = "basic"
    API_KEY = "api_key"


class ApiKeyLocation(StrEnum):
    """Target location for API key injection."""

    HEADER = "header"
    QUERY = "query"
    COOKIE = "cookie"

    @classmethod
    def from_string(cls, value: str) -> "ApiKeyLocation":
        """Normalize and parse an API key location string.

        Args:
            value: Location name (case-insensitive).

        Returns:
            Matching ApiKeyLocation member.
        """
        if not isinstance(value, str):
            raise InvalidRequestError(
                f"API key location must be a string, got {type(value).__name__}."
            )
        normalized = value.strip().lower()
        try:
            return cls(normalized)
        except ValueError:
            supported = ", ".join(loc.value for loc in cls)
            raise InvalidRequestError(
                f"Invalid API key location: '{normalized}'. Supported: {supported}."
            ) from None


class AuthConfig(ABC):
    """Abstract base class for authentication specifications."""

    @property
    @abstractmethod
    def auth_type(self) -> AuthType:
        """Return the authentication mechanism type."""
        raise NotImplementedError


@dataclass(frozen=True)
class BearerAuth(AuthConfig):
    """Bearer token authentication configuration."""

    token: str

    def __post_init__(self) -> None:
        """Validate bearer token parameters."""
        if not isinstance(self.token, str) or not self.token.strip():
            raise InvalidRequestError("Bearer token cannot be empty.")

    @property
    def auth_type(self) -> AuthType:
        """Return Bearer auth type."""
        return AuthType.BEARER

    def get_token(self) -> str:
        """Retrieve raw secret token for authorized execution."""
        return self.token

    def __repr__(self) -> str:
        """Protect token from exposure in string representations."""
        return "BearerAuth(token='***')"

    def __str__(self) -> str:
        """Protect token from exposure in string representations."""
        return self.__repr__()


@dataclass(frozen=True)
class BasicAuth(AuthConfig):
    """HTTP Basic authentication credentials."""

    username: str
    password: str

    def __post_init__(self) -> None:
        """Validate basic authentication credentials."""
        if not isinstance(self.username, str) or not isinstance(self.password, str):
            raise InvalidRequestError(
                "BasicAuth username and password must be strings."
            )

    @property
    def auth_type(self) -> AuthType:
        """Return Basic auth type."""
        return AuthType.BASIC

    def get_password(self) -> str:
        """Retrieve raw password for authorized execution."""
        return self.password

    def __repr__(self) -> str:
        """Protect password from exposure in string representations."""
        return f"BasicAuth(username={self.username!r}, password='***')"

    def __str__(self) -> str:
        """Protect password from exposure in string representations."""
        return self.__repr__()


@dataclass(frozen=True)
class ApiKeyAuth(AuthConfig):
    """API key authentication specification."""

    name: str
    value: str
    location: ApiKeyLocation = ApiKeyLocation.HEADER

    def __post_init__(self) -> None:
        """Validate and normalize API key parameters."""
        if not isinstance(self.name, str) or not self.name.strip():
            raise InvalidRequestError("API key name cannot be empty.")
        if not isinstance(self.value, str) or not self.value.strip():
            raise InvalidRequestError("API key value cannot be empty.")
        if isinstance(self.location, str):
            object.__setattr__(
                self, "location", ApiKeyLocation.from_string(self.location)
            )

    @property
    def auth_type(self) -> AuthType:
        """Return API key auth type."""
        return AuthType.API_KEY

    def get_value(self) -> str:
        """Retrieve raw secret API key value for authorized execution."""
        return self.value

    def __repr__(self) -> str:
        """Protect API key value from exposure in string representations."""
        return (
            f"ApiKeyAuth(name={self.name!r}, value='***', "
            f"location={self.location.value!r})"
        )

    def __str__(self) -> str:
        """Protect API key value from exposure in string representations."""
        return self.__repr__()


@dataclass
class HttpRequest:
    """Domain model representing an HTTP request specification."""

    method: HttpMethod | str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    body: Any | None = None
    body_type: BodyType | str = BodyType.RAW
    timeout: float = 30.0
    max_retries: int = 0
    follow_redirects: bool = True
    auth: AuthConfig | None = None
    retry_non_idempotent: bool = False

    def __post_init__(self) -> None:
        """Validate and normalize request parameters."""
        if isinstance(self.method, str):
            self.method = HttpMethod.from_string(self.method)
        elif not isinstance(self.method, HttpMethod):
            raise UnsupportedMethodError(f"Invalid method type: {type(self.method)}")

        if not isinstance(self.url, str) or not self.url.strip():
            raise InvalidRequestError("Request URL cannot be empty.")
        self.url = self.url.strip()

        if isinstance(self.body_type, str):
            self.body_type = BodyType.from_string(self.body_type)

        if not isinstance(self.timeout, (int, float)) or isinstance(self.timeout, bool):
            raise InvalidRequestError("Request timeout must be a numeric value.")
        if self.timeout <= 0:
            raise InvalidRequestError(
                f"Request timeout must be greater than 0, got {self.timeout}."
            )
        self.timeout = float(self.timeout)

        if not isinstance(self.max_retries, int) or isinstance(self.max_retries, bool):
            raise InvalidRequestError("Maximum retry count must be an integer.")
        if self.max_retries < 0:
            raise InvalidRequestError(
                f"Maximum retry count cannot be negative, got {self.max_retries}."
            )

        if not isinstance(self.headers, dict):
            raise InvalidRequestError("Request headers must be a dictionary.")
        self.headers = {str(k): str(v) for k, v in self.headers.items()}

        if not isinstance(self.params, dict):
            raise InvalidRequestError("Query parameters must be a dictionary.")

        if not isinstance(self.retry_non_idempotent, bool):
            raise InvalidRequestError("retry_non_idempotent must be a boolean.")

    def masked_headers(self) -> dict[str, str]:
        """Return headers with sensitive credentials redacted."""
        return {
            k: ("***" if k.lower() in _SENSITIVE_HEADER_KEYS else v)
            for k, v in self.headers.items()
        }

    def __repr__(self) -> str:
        """Format request representation ensuring credential redaction."""
        return (
            f"HttpRequest(method={self.method.value!r}, url={self.url!r}, "
            f"headers={self.masked_headers()!r}, params={self.params!r}, "
            f"body_type={self.body_type.value!r}, timeout={self.timeout}, "
            f"max_retries={self.max_retries}, auth={self.auth!r}, "
            f"retry_non_idempotent={self.retry_non_idempotent})"
        )


@dataclass
class HttpResponse:
    """Domain model capturing observed HTTP response data."""

    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""
    elapsed_ms: float = 0.0
    size_bytes: int = 0
    url: str = ""

    def __post_init__(self) -> None:
        """Validate response fields and compute defaults."""
        if not isinstance(self.status_code, int) or isinstance(self.status_code, bool):
            raise InvalidRequestError("HTTP status code must be an integer.")
        if not (100 <= self.status_code <= 599):
            raise InvalidRequestError(
                f"Invalid HTTP status code: {self.status_code}. Must be 100-599."
            )

        if (
            not isinstance(self.elapsed_ms, (int, float))
            or isinstance(self.elapsed_ms, bool)
            or self.elapsed_ms < 0
        ):
            raise InvalidRequestError(
                "Response elapsed time must be a non-negative number."
            )
        self.elapsed_ms = float(self.elapsed_ms)

        if not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise InvalidRequestError(
                "Response size in bytes must be a non-negative integer."
            )

        if self.size_bytes == 0 and self.body:
            self.size_bytes = len(self.body.encode("utf-8"))

        if not isinstance(self.headers, dict):
            self.headers = dict(self.headers)

    @property
    def is_success(self) -> bool:
        """Check if response status code indicates success (2xx)."""
        return 200 <= self.status_code < 300

    @property
    def is_client_error(self) -> bool:
        """Check if response status code indicates a client error (4xx)."""
        return 400 <= self.status_code < 500

    @property
    def is_server_error(self) -> bool:
        """Check if response status code indicates a server error (5xx)."""
        return 500 <= self.status_code < 600

    def json(self) -> Any:
        """Parse response body string as JSON.

        Returns:
            Deserialized Python object.

        Raises:
            ValueError: If body cannot be decoded as valid JSON.
        """
        try:
            return json.loads(self.body)
        except (json.JSONDecodeError, TypeError) as err:
            raise ValueError(f"Response body is not valid JSON: {err}") from err
