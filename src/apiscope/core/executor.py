"""Core HTTP Request Execution Engine.

Provides reliable, synchronous HTTP execution using httpx, supporting all standard
methods, declarative body formats, authentication injection, SSRF target validation,
bounded exponential backoff retries, and comprehensive response capture.
"""

import ipaddress
import os
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
from apiscope.core.safety import ExecutionSafetyPolicy

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
MAX_REDIRECT_LIMIT: int = 10
REDIRECT_STATUS_CODES: frozenset[int] = frozenset({301, 302, 303, 307, 308})


def parse_target_origin(target: str) -> tuple[str, str, int] | None:
    """Parse a target URL into a normalized (scheme, hostname, port) origin tuple.

    Examples:
        'http://localhost:8080' -> ('http', 'localhost', 8080)
        'https://127.0.0.1:8443/api' -> ('https', '127.0.0.1', 8443)
        'http://localhost' -> ('http', 'localhost', 80)
        'https://localhost' -> ('https', 'localhost', 443)
    """
    if not target or not isinstance(target, str):
        return None
    raw = target.strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"http://{raw}"
    try:
        parsed = urllib.parse.urlsplit(raw)
        scheme = (parsed.scheme or "").lower()
        hostname = (parsed.hostname or "").lower()
        if not hostname or not scheme:
            return None
        port = parsed.port
        if port is None:
            port = 443 if scheme == "https" else 80
        return (scheme, hostname, port)
    except Exception:
        return None


def _is_same_origin(url_a: str, url_b: str) -> bool:
    """Determine whether two URLs share the exact same scheme, hostname, and port."""
    try:
        pa = urllib.parse.urlsplit(url_a)
        pb = urllib.parse.urlsplit(url_b)
        scheme_a = (pa.scheme or "").lower()
        scheme_b = (pb.scheme or "").lower()
        host_a = (pa.hostname or "").lower()
        host_b = (pb.hostname or "").lower()
        port_a = pa.port or (443 if scheme_a == "https" else 80)
        port_b = pb.port or (443 if scheme_b == "https" else 80)
        return (scheme_a == scheme_b) and (host_a == host_b) and (port_a == port_b)
    except Exception:
        return False


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
        allow_private_networks: bool | None = None,
        allowed_hosts: Sequence[str] | set[str] | frozenset[str] | None = None,
        blocked_hosts: Sequence[str] | set[str] | frozenset[str] | None = None,
        allowed_schemes: Sequence[str] | set[str] | frozenset[str] | None = None,
        allowed_targets: Sequence[str] | set[str] | frozenset[str] | None = None,
        resolve_dns: bool = False,
    ) -> None:
        """Initialize target access security policy.

        Args:
            allow_private_networks: Whether loopback and private networks are permitted.
            allowed_hosts: Whitelist of authorized hosts (e.g. local test servers).
            blocked_hosts: Blacklist of disallowed hosts.
            allowed_schemes: Permitted protocol schemes (defaults to HTTP and HTTPS).
            allowed_targets: Whitelist of specific target URLs/origins (e.g. 'http://localhost:8080').
            resolve_dns: Whether to perform pre-execution DNS resolution for hostnames.
        """
        self.allow_private_networks = bool(allow_private_networks)

        hosts = {h.strip().lower() for h in (allowed_hosts or set())}
        env_hosts = os.environ.get("APISCOPE_ALLOWED_HOSTS", "")
        if env_hosts:
            hosts.update(h.strip().lower() for h in env_hosts.split(",") if h.strip())

        # Collect allowed target origins (scheme, host, port)
        target_origins: set[tuple[str, str, int]] = set()
        if allowed_targets:
            for t in allowed_targets:
                origin = parse_target_origin(t)
                if origin:
                    target_origins.add(origin)

        env_targets = os.environ.get("APISCOPE_ALLOWED_TARGETS", "") or os.environ.get(
            "APISCOPE_ALLOW_TARGET", ""
        )
        if env_targets:
            for item in env_targets.split(","):
                origin = parse_target_origin(item.strip())
                if origin:
                    target_origins.add(origin)

        self.allow_private_networks = bool(allow_private_networks)
        self.allowed_hosts = frozenset(hosts)
        self.blocked_hosts = frozenset(
            h.strip().lower() for h in (blocked_hosts or set())
        )
        self.allowed_schemes = frozenset(
            s.strip().lower() for s in (allowed_schemes or {"http", "https"})
        )
        self.allowed_target_origins: frozenset[tuple[str, str, int]] = frozenset(
            target_origins
        )
        self.resolve_dns = resolve_dns

    @property
    def allowed_targets(self) -> frozenset[str]:
        """Return string origins for configured allowed targets."""
        return frozenset(
            f"{scheme}://{host}:{port}"
            for scheme, host, port in self.allowed_target_origins
        )

    def is_target_authorized(self, url: str) -> bool:
        """Check if a URL matches an explicitly authorized target origin."""
        origin = parse_target_origin(url)
        return origin is not None and origin in self.allowed_target_origins

    def is_allowed_host(self, host: str) -> bool:
        """Check if a hostname matches an authorized host."""
        return host.strip().lower() in self.allowed_hosts

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

        # Explicitly authorized targets bypass private network restrictions
        url_origin = parse_target_origin(url)
        if url_origin is not None and url_origin in self.allowed_target_origins:
            return

        # Explicitly authorized hostnames bypass private network restrictions
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
        safety_policy: ExecutionSafetyPolicy | None = None,
    ) -> None:
        """Initialize RequestExecutor.

        Args:
            config: Application configuration (defaults to global load_config).
            policy: Target access policy for SSRF protection.
            client: Optional pre-configured httpx.Client (reused if provided).
            transport: Optional custom transport (e.g. httpx.MockTransport for tests).
            backoff_factor: Multiplier for exponential backoff between retries.
            sleep_func: Callable for pausing execution during backoff (for tests).
            safety_policy: Optional execution safety policy for mutation fencing.
        """
        self.config = config or get_config()
        self.policy = policy or TargetAccessPolicy()
        self.safety_policy = safety_policy
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

    def _dispatch_hop(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any] | None,
        auth: Any,
        body_kwargs: dict[str, Any],
        timeout: float,
        max_retries: int,
        can_retry: bool,
    ) -> tuple[httpx.Response, float]:
        """Dispatch a single HTTP request hop with transient error retry handling."""
        sanitized_url = _sanitize_url(url)
        total_attempts = 1 + (max_retries if can_retry else 0)
        attempt = 0
        hop_elapsed_ms = 0.0

        while attempt < total_attempts:
            attempt += 1
            if attempt > 1:
                self.logger.info(
                    "Retrying HTTP %s %s (attempt %d/%d)",
                    method,
                    sanitized_url,
                    attempt,
                    total_attempts,
                )

            start_time = time.perf_counter()
            try:
                raw_response = self._client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    auth=auth,
                    timeout=timeout,
                    follow_redirects=False,
                    **body_kwargs,
                )
                hop_elapsed_ms += (time.perf_counter() - start_time) * 1000.0

                # Evaluate transient retry criteria
                if (
                    can_retry
                    and attempt < total_attempts
                    and raw_response.status_code in TRANSIENT_STATUS_CODES
                ):
                    self.logger.warning(
                        "HTTP %s %s returned status %d. Retrying...",
                        method,
                        sanitized_url,
                        raw_response.status_code,
                    )
                    self._backoff(attempt)
                    continue

                return raw_response, hop_elapsed_ms

            except httpx.TimeoutException as exc:
                hop_elapsed_ms += (time.perf_counter() - start_time) * 1000.0
                self.logger.warning(
                    "Timeout executing HTTP %s %s: %s",
                    method,
                    sanitized_url,
                    exc,
                )
                if can_retry and attempt < total_attempts:
                    self._backoff(attempt)
                    continue
                raise RequestTimeoutError(
                    f"Request to '{sanitized_url}' timed out after {timeout}s: "
                    f"{redact_sensitive_text(str(exc))}"
                ) from exc

            except httpx.InvalidURL as exc:
                raise InvalidRequestError(
                    f"Malformed or invalid request URL '{sanitized_url}': "
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
                hop_elapsed_ms += (time.perf_counter() - start_time) * 1000.0
                self.logger.warning(
                    "Network error executing HTTP %s %s: %s",
                    method,
                    sanitized_url,
                    exc,
                )
                if can_retry and attempt < total_attempts:
                    self._backoff(attempt)
                    continue
                raise NetworkExecutionError(
                    f"Network error executing HTTP {method} "
                    f"'{sanitized_url}': {redact_sensitive_text(str(exc))}"
                ) from exc

        raise NetworkExecutionError(
            f"Execution failed for HTTP {method} '{sanitized_url}'."
        )

    def execute(self, request: HttpRequest) -> HttpResponse:
        """Execute an HTTP request according to specification and return response model.

        Performs SSRF target validation, credential injection, bounded exponential
        backoff retries, and secure step-by-step redirect resolution. Every redirect
        target is re-validated against TargetAccessPolicy before dispatch.

        Args:
            request: The validated HTTP request specification.

        Returns:
            Observed HttpResponse domain model.

        Raises:
            InvalidRequestError: If destination URL violates policy or is malformed.
            RequestTimeoutError: If execution exceeds configured timeout.
            NetworkExecutionError: If connection or transport failures occur.
        """
        # Validate against execution safety policy if configured
        if self.safety_policy is not None:
            self.safety_policy.validate_request(request)

        # Validate initial target URL against security access policy
        self.policy.validate_url(request.url)

        sanitized_initial_url = _sanitize_url(request.url)
        max_retries = min(request.max_retries, MAX_RETRY_LIMIT)
        is_idempotent = request.method in IDEMPOTENT_METHODS
        can_retry = is_idempotent or request.retry_non_idempotent

        headers, params, auth_param, body_kwargs = self._prepare_request_kwargs(request)

        current_url = request.url
        current_method = request.method.value
        current_headers = headers
        current_params = params if params else None
        current_auth = auth_param
        current_body_kwargs = body_kwargs
        total_elapsed_ms = 0.0

        self.logger.debug(
            "Executing HTTP %s %s (initial attempt)",
            current_method,
            sanitized_initial_url,
        )

        raw_response, hop_elapsed = self._dispatch_hop(
            method=current_method,
            url=current_url,
            headers=current_headers,
            params=current_params,
            auth=current_auth,
            body_kwargs=current_body_kwargs,
            timeout=request.timeout,
            max_retries=max_retries,
            can_retry=can_retry,
        )
        total_elapsed_ms += hop_elapsed

        # Secure step-by-step redirect following with per-hop policy validation
        if (
            request.follow_redirects
            and raw_response.status_code in REDIRECT_STATUS_CODES
        ):
            redirect_count = 0
            visited_urls = {current_url}

            while raw_response.status_code in REDIRECT_STATUS_CODES:
                location = raw_response.headers.get("Location")
                if not location:
                    # RFC 7231: 3xx without Location header should be returned as-is
                    break

                redirect_count += 1
                if redirect_count > MAX_REDIRECT_LIMIT:
                    raise NetworkExecutionError(
                        f"Too many redirects for '{sanitized_initial_url}': "
                        f"exceeded maximum redirect limit of {MAX_REDIRECT_LIMIT}."
                    )

                # Resolve relative or absolute redirect URL
                try:
                    resolved_url = urllib.parse.urljoin(current_url, location)
                except Exception as exc:
                    raise InvalidRequestError(
                        f"Malformed redirect Location header '{location}': {exc}"
                    ) from exc

                # Parse and validate protocol scheme
                try:
                    parsed_next = urllib.parse.urlsplit(resolved_url)
                    next_scheme = (parsed_next.scheme or "").lower()
                except Exception as exc:
                    raise InvalidRequestError(
                        f"Malformed redirect URL '{_sanitize_url(resolved_url)}': {exc}"
                    ) from exc

                if not next_scheme or next_scheme not in self.policy.allowed_schemes:
                    allowed_s = ", ".join(sorted(self.policy.allowed_schemes))
                    raise InvalidRequestError(
                        f"Unsupported redirect protocol '{next_scheme}' in "
                        f"'{_sanitize_url(resolved_url)}'. Permitted: {allowed_s}"
                    )

                # Redirect loop detection
                if resolved_url in visited_urls:
                    raise NetworkExecutionError(
                        f"Redirect loop detected: '{_sanitize_url(resolved_url)}' "
                        "has already been visited in this redirect chain."
                    )
                visited_urls.add(resolved_url)

                # Validate redirect target against TargetAccessPolicy
                self.policy.validate_url(resolved_url)

                # Check same-origin for credential stripping (RFC 7235 Section 2.2)
                if not _is_same_origin(current_url, resolved_url):
                    current_headers = {
                        k: v
                        for k, v in current_headers.items()
                        if k.lower() not in {"authorization", "cookie"}
                    }
                    current_auth = None

                # Method and body transformations (RFC 7231 / RFC 7538)
                status = raw_response.status_code
                if status == 303 or (
                    status in {301, 302} and current_method.upper() == "POST"
                ):
                    current_method = "GET"
                    current_body_kwargs = {}
                    current_headers = {
                        k: v
                        for k, v in current_headers.items()
                        if k.lower() not in {"content-length", "content-type"}
                    }
                # 307 and 308 preserve current_method and current_body_kwargs

                current_params = None
                current_url = resolved_url
                sanitized_next_url = _sanitize_url(current_url)

                self.logger.debug(
                    "Following redirect to HTTP %s %s (hop %d)",
                    current_method,
                    sanitized_next_url,
                    redirect_count,
                )

                hop_idempotent = current_method.upper() in {
                    "GET",
                    "HEAD",
                    "PUT",
                    "DELETE",
                    "OPTIONS",
                }
                hop_can_retry = hop_idempotent or request.retry_non_idempotent

                raw_response, hop_elapsed = self._dispatch_hop(
                    method=current_method,
                    url=current_url,
                    headers=current_headers,
                    params=current_params,
                    auth=current_auth,
                    body_kwargs=current_body_kwargs,
                    timeout=request.timeout,
                    max_retries=max_retries,
                    can_retry=hop_can_retry,
                )
                total_elapsed_ms += hop_elapsed

        final_url = (
            str(raw_response.url)
            if (raw_response.url and str(raw_response.url))
            else current_url
        )

        return HttpResponse(
            status_code=raw_response.status_code,
            headers={str(k): str(v) for k, v in raw_response.headers.items()},
            body=raw_response.text,
            elapsed_ms=total_elapsed_ms,
            size_bytes=len(raw_response.content),
            url=final_url,
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
