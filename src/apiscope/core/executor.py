"""Core HTTP Request Execution Engine.

Provides reliable, synchronous HTTP execution using httpx, supporting all standard
methods, declarative body formats, authentication injection, SSRF target validation,
bounded exponential backoff retries, and comprehensive response capture.
"""

import ipaddress
import socket
import time
import urllib.parse
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from apiscope.core.config import AppConfig, get_config
from apiscope.core.exceptions import (
    InvalidRequestError,
    NetworkExecutionError,
    RequestTimeoutError,
)
from apiscope.core.logging_config import get_logger, redact_sensitive_text
from apiscope.core.models import (
    ApiKeyAuth,
    ApiKeyLocation,
    BasicAuth,
    BearerAuth,
    BodyType,
    HttpMethod,
    HttpRequest,
    HttpResponse,
)

IDEMPOTENT_METHODS: frozenset[HttpMethod] = frozenset(
    {
        HttpMethod.GET,
        HttpMethod.HEAD,
        HttpMethod.PUT,
        HttpMethod.DELETE,
        HttpMethod.OPTIONS,
    }
)

TRANSIENT_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_LIMIT: int = 10


def _sanitize_url(url: str) -> str:
    """Strip sensitive userinfo credentials from URL for logging and error reporting."""
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.username or parsed.password:
            netloc = parsed.hostname or ""
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            return urllib.parse.urlunsplit(
                (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
            )
        return url
    except Exception:
        return redact_sensitive_text(url)


class TargetAccessPolicy:
    """Security policy controlling allowable target destinations and network access.

    Protects against Server-Side Request Forgery (SSRF) by validating requested
    protocols, hostnames, and resolved IP addresses against configurable restrictions.

    Note on Security Limitations:
        Pre-execution DNS validation defends against direct loopback, link-local,
        and private network access. However, standard Python network transports without
        socket pinning remain susceptible to Time-of-Check to Time-of-Use (TOCTOU)
        DNS-rebinding attacks where a remote DNS record alters resolution between
        validation and socket connection. Complete protection requires lower-level
        socket dialer hooks or an isolated forward proxy.
    """

    def __init__(
        self,
        allow_private_networks: bool = False,
        allowed_hosts: Sequence[str] | set[str] | frozenset[str] | None = None,
        blocked_hosts: Sequence[str] | set[str] | frozenset[str] | None = None,
        allowed_schemes: Sequence[str] | set[str] | frozenset[str] | None = None,
        resolve_dns: bool = False,
    ) -> None:
        """Initialize target access security policy.

        Args:
            allow_private_networks: Whether loopback and private networks are permitted.
            allowed_hosts: Whitelist of authorized hosts (e.g. local test servers).
            blocked_hosts: Blacklist of disallowed hosts.
            allowed_schemes: Permitted protocol schemes (defaults to HTTP and HTTPS).
            resolve_dns: Whether to perform pre-execution DNS resolution for hostnames.
        """
        self.allow_private_networks = allow_private_networks
        self.allowed_hosts = frozenset(
            h.strip().lower() for h in (allowed_hosts or set())
        )
        self.blocked_hosts = frozenset(
            h.strip().lower() for h in (blocked_hosts or set())
        )
        self.allowed_schemes = frozenset(
            s.strip().lower() for s in (allowed_schemes or {"http", "https"})
        )
        self.resolve_dns = resolve_dns

    def validate_url(self, url: str) -> None:
        """Validate destination URL against security access policy.

        Args:
            url: Target URL string to validate.

        Raises:
            InvalidRequestError: If protocol is unsupported, host is malformed,
                or target is disallowed (unwhitelisted loopback or private network).
        """
        if not isinstance(url, str) or not url.strip():
            raise InvalidRequestError("Request URL cannot be empty.")

        try:
            parsed = urllib.parse.urlsplit(url.strip())
        except ValueError as err:
            raise InvalidRequestError(f"Malformed URL '{url}': {err}") from err

        scheme = parsed.scheme.lower()
        if not scheme:
            raise InvalidRequestError(
                f"Malformed URL '{url}': missing protocol scheme."
            )

        if scheme not in self.allowed_schemes:
            allowed = ", ".join(sorted(self.allowed_schemes))
            raise InvalidRequestError(
                f"Unsupported protocol: '{scheme}'. Only {allowed} are permitted."
            )

        hostname = parsed.hostname
        if not hostname:
            raise InvalidRequestError(f"Malformed URL '{url}': missing host component.")

        hostname_lower = hostname.lower()

        if hostname_lower in self.blocked_hosts:
            raise InvalidRequestError(
                f"Access to blocked host '{hostname}' is denied by target access "
                "policy."
            )

        # Explicitly authorized targets bypass local network restrictions
        if hostname_lower in self.allowed_hosts:
            return

        if not self.allow_private_networks:
            if hostname_lower in {"localhost", "localhost.localdomain"}:
                raise InvalidRequestError(
                    f"Access to loopback/local address '{hostname}' is disallowed "
                    "by target access policy."
                )

            # Check for direct IP address literal
            try:
                ip = ipaddress.ip_address(hostname_lower)
                if (
                    ip.is_loopback
                    or ip.is_private
                    or ip.is_link_local
                    or ip.is_reserved
                    or ip.is_unspecified
                ):
                    raise InvalidRequestError(
                        f"Access to private/local network IP '{hostname}' is "
                        "disallowed by target access policy."
                    )
            except ValueError:
                # Hostname is a domain name. Resolve DNS if explicitly configured.
                if self.resolve_dns:
                    try:
                        addr_info = socket.getaddrinfo(hostname, None)
                        for entry in addr_info:
                            ip_str = entry[4][0]
                            resolved_ip = ipaddress.ip_address(ip_str)
                            if (
                                resolved_ip.is_loopback
                                or resolved_ip.is_private
                                or resolved_ip.is_link_local
                                or resolved_ip.is_reserved
                                or resolved_ip.is_unspecified
                            ):
                                raise InvalidRequestError(
                                    f"Host '{hostname}' resolves to restricted IP "
                                    f"'{ip_str}' disallowed by target access policy."
                                )
                    except (socket.gaierror, UnicodeError):
                        pass


class RequestExecutor:
    """Core synchronous HTTP request execution engine.

    Handles uniform request execution across all HTTP methods, credential injection,
    retries with bounded exponential backoff, and comprehensive metric capture.
    """

    def __init__(
        self,
        config: AppConfig | None = None,
        policy: TargetAccessPolicy | None = None,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        backoff_factor: float = 0.5,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> None:
        """Initialize RequestExecutor.

        Args:
            config: Application configuration (defaults to global load_config).
            policy: Target access policy for SSRF protection.
            client: Optional pre-configured httpx.Client (reused if provided).
            transport: Optional custom transport (e.g. httpx.MockTransport for tests).
            backoff_factor: Multiplier for exponential backoff between retries.
            sleep_func: Callable for pausing execution during backoff (for tests).
        """
        self.config = config or get_config()
        self.policy = policy or TargetAccessPolicy()
        self.backoff_factor = max(0.0, backoff_factor)
        self.sleep_func = sleep_func
        self._external_client = client is not None
        if client is not None:
            self._client = client
        else:
            self._client = httpx.Client(
                transport=transport,
                timeout=httpx.Timeout(self.config.default_timeout),
            )
        self.logger = get_logger("core.executor")

    def _prepare_request_kwargs(
        self, request: HttpRequest
    ) -> tuple[dict[str, str], dict[str, Any], Any, dict[str, Any]]:
        """Prepare headers, query params, auth, and body kwargs for httpx."""
        headers = dict(request.headers)
        params = dict(request.params)
        body_kwargs: dict[str, Any] = {}
        auth_param: Any = None

        # Prepare request payload
        if request.body is not None:
            if request.body_type == BodyType.JSON:
                body_kwargs["json"] = request.body
            elif request.body_type == BodyType.FORM:
                body_kwargs["data"] = request.body
            else:
                # RAW
                if isinstance(request.body, str):
                    body_kwargs["content"] = request.body.encode("utf-8")
                elif isinstance(request.body, (bytes, bytearray)):
                    body_kwargs["content"] = bytes(request.body)
                else:
                    body_kwargs["content"] = str(request.body).encode("utf-8")

        # Inject authentication
        if request.auth is not None:
            if isinstance(request.auth, BearerAuth):
                # Remove any existing Authorization header to prevent duplicate headers
                auth_keys = [k for k in headers if k.lower() == "authorization"]
                for k in auth_keys:
                    headers.pop(k)
                headers["Authorization"] = f"Bearer {request.auth.get_token()}"
            elif isinstance(request.auth, BasicAuth):
                auth_param = httpx.BasicAuth(
                    username=request.auth.username,
                    password=request.auth.get_password(),
                )
            elif isinstance(request.auth, ApiKeyAuth):
                if request.auth.location == ApiKeyLocation.HEADER:
                    # Remove any existing header with matching name case-insensitively
                    matching = [
                        k for k in headers if k.lower() == request.auth.name.lower()
                    ]
                    for k in matching:
                        headers.pop(k)
                    headers[request.auth.name] = request.auth.get_value()
                elif request.auth.location == ApiKeyLocation.QUERY:
                    params[request.auth.name] = request.auth.get_value()
                elif request.auth.location == ApiKeyLocation.COOKIE:
                    cookie_entry = f"{request.auth.name}={request.auth.get_value()}"
                    existing_cookies = [k for k in headers if k.lower() == "cookie"]
                    if existing_cookies:
                        headers[existing_cookies[0]] = (
                            f"{headers[existing_cookies[0]]}; {cookie_entry}"
                        )
                    else:
                        headers["Cookie"] = cookie_entry

        return headers, params, auth_param, body_kwargs

    def _backoff(self, attempt: int) -> None:
        """Pause execution using bounded exponential backoff."""
        if self.backoff_factor > 0:
            delay = min(self.backoff_factor * (2 ** (attempt - 1)), 5.0)
            self.sleep_func(delay)

    def execute(self, request: HttpRequest) -> HttpResponse:
        """Execute an HTTP request according to specification and return response model.

        Args:
            request: The validated HTTP request specification.

        Returns:
            Observed HttpResponse domain model.

        Raises:
            InvalidRequestError: If destination URL violates policy or is malformed.
            RequestTimeoutError: If execution exceeds configured timeout.
            NetworkExecutionError: If connection or transport failures occur.
        """
        # Validate target URL against security access policy
        self.policy.validate_url(request.url)

        sanitized_url = _sanitize_url(request.url)
        max_retries = min(request.max_retries, MAX_RETRY_LIMIT)
        is_idempotent = request.method in IDEMPOTENT_METHODS
        can_retry = is_idempotent or request.retry_non_idempotent

        headers, params, auth_param, body_kwargs = self._prepare_request_kwargs(request)

        total_attempts = 1 + (max_retries if can_retry else 0)
        attempt = 0

        while attempt < total_attempts:
            attempt += 1
            if attempt == 1:
                self.logger.debug(
                    "Executing HTTP %s %s (initial attempt)",
                    request.method.value,
                    sanitized_url,
                )
            else:
                self.logger.info(
                    "Retrying HTTP %s %s (attempt %d/%d)",
                    request.method.value,
                    sanitized_url,
                    attempt,
                    total_attempts,
                )

            start_time = time.perf_counter()
            try:
                raw_response = self._client.request(
                    method=request.method.value,
                    url=request.url,
                    headers=headers,
                    params=params if params else None,
                    auth=auth_param,
                    timeout=request.timeout,
                    follow_redirects=request.follow_redirects,
                    **body_kwargs,
                )
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0

                # Evaluate transient retry criteria
                if (
                    can_retry
                    and attempt < total_attempts
                    and raw_response.status_code in TRANSIENT_STATUS_CODES
                ):
                    self.logger.warning(
                        "HTTP %s %s returned status %d. Retrying...",
                        request.method.value,
                        sanitized_url,
                        raw_response.status_code,
                    )
                    self._backoff(attempt)
                    continue

                # Normal response capture (includes non-transient 4xx and 5xx)
                final_url = (
                    str(raw_response.url)
                    if (raw_response.url and str(raw_response.url))
                    else request.url
                )
                return HttpResponse(
                    status_code=raw_response.status_code,
                    headers={str(k): str(v) for k, v in raw_response.headers.items()},
                    body=raw_response.text,
                    elapsed_ms=elapsed_ms,
                    size_bytes=len(raw_response.content),
                    url=final_url,
                )

            except httpx.TimeoutException as exc:
                self.logger.warning(
                    "Timeout executing HTTP %s %s: %s",
                    request.method.value,
                    sanitized_url,
                    exc,
                )
                if can_retry and attempt < total_attempts:
                    self._backoff(attempt)
                    continue
                raise RequestTimeoutError(
                    f"Request to '{sanitized_url}' timed out after {request.timeout}s: "
                    f"{redact_sensitive_text(str(exc))}"
                ) from exc

            except httpx.TooManyRedirects as exc:
                raise NetworkExecutionError(
                    f"Too many redirects encountered for '{sanitized_url}': "
                    f"{redact_sensitive_text(str(exc))}"
                ) from exc

            except (
                httpx.ConnectError,
                httpx.NetworkError,
                httpx.ProtocolError,
                httpx.RequestError,
            ) as exc:
                self.logger.warning(
                    "Network error executing HTTP %s %s: %s",
                    request.method.value,
                    sanitized_url,
                    exc,
                )
                if can_retry and attempt < total_attempts:
                    self._backoff(attempt)
                    continue
                raise NetworkExecutionError(
                    f"Network error executing HTTP {request.method.value} "
                    f"'{sanitized_url}': {redact_sensitive_text(str(exc))}"
                ) from exc

        # Unreachable fallback
        raise NetworkExecutionError(
            f"Execution failed for HTTP {request.method.value} '{sanitized_url}'."
        )

    def close(self) -> None:
        """Close underlying httpx Client session if owned by this executor."""
        if not self._external_client:
            self._client.close()

    def __enter__(self) -> "RequestExecutor":
        """Context management entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context management exit."""
        self.close()
