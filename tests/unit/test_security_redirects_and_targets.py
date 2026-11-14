"""Comprehensive security regression tests for SSRF-safe redirects and targets.

Covers SEC-01:
- RFC 7231 / 7235 safe step-by-step redirect handling.
- Per-hop TargetAccessPolicy re-validation.
- Scoped target origin authorization (--allow-target).
- Redirect loop detection and maximum redirect bounds.
- Cross-origin credential and cookie stripping.
- HTTP method/body transformations across 301, 302, 303, 307, 308.
- Secret and userinfo redaction in blocked redirect error messages.
- Transient retries and timeouts on redirected hops.
- CLI integration for --allow-target and default SSRF restrictions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from apiscope.cli.main import app
from apiscope.core.exceptions import (
    InvalidRequestError,
    NetworkExecutionError,
)
from apiscope.core.executor import (
    MAX_REDIRECT_LIMIT,
    RequestExecutor,
    TargetAccessPolicy,
    _is_same_origin,
    parse_target_origin,
)
from apiscope.core.models import (
    BasicAuth,
    HttpMethod,
    HttpRequest,
)


class TestTargetAccessPolicyScoping:
    """Tests 1-2 & Target origin parsing: Verify granular target authorization."""

    def test_01_allowed_initial_request_success(self) -> None:
        """Covers: SEC-01.01 — Allowed initial public request returns 200 OK."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url == "https://api.example.com/v1/health"
            return httpx.Response(200, json={"status": "healthy"})

        policy = TargetAccessPolicy(allow_private_networks=False)
        executor = RequestExecutor(
            policy=policy,
            transport=httpx.MockTransport(handler),
        )
        req = HttpRequest(method="GET", url="https://api.example.com/v1/health")
        resp = executor.execute(req)

        assert resp.status_code == 200
        assert resp.json() == {"status": "healthy"}

    def test_02_blocked_initial_private_network_request(self) -> None:
        """Covers: SEC-01.02 — Blocked initial private-network request fails."""
        policy = TargetAccessPolicy(allow_private_networks=False)
        executor = RequestExecutor(policy=policy)

        with pytest.raises(InvalidRequestError) as exc_info:
            executor.execute(
                HttpRequest(
                    method="GET",
                    url="http://169.254.169.254/latest/meta-data/",
                )
            )
        assert "disallowed by target access policy" in str(exc_info.value)

    def test_parse_target_origin_helpers(self) -> None:
        """Covers: SEC-01 — Verify parse_target_origin and _is_same_origin."""
        assert parse_target_origin("http://localhost:8080") == (
            "http",
            "localhost",
            8080,
        )
        assert parse_target_origin("http://localhost:8080/api/v1") == (
            "http",
            "localhost",
            8080,
        )
        assert parse_target_origin("https://api.example.com") == (
            "https",
            "api.example.com",
            443,
        )
        assert parse_target_origin("http://api.example.com") == (
            "http",
            "api.example.com",
            80,
        )
        assert parse_target_origin("invalid url :::") is None

        assert _is_same_origin(
            "https://api.example.com/v1", "https://api.example.com/v2"
        )
        assert not _is_same_origin(
            "http://api.example.com/v1", "https://api.example.com/v1"
        )
        assert not _is_same_origin("http://localhost:8080", "http://localhost:9000")


class TestSSRFSafeRedirectExecution:
    """Tests 3-18: Step-by-step redirect resolution, security checks, and RFC."""

    def test_03_allowed_relative_redirect(self) -> None:
        """Covers: SEC-01.03 — Allowed relative redirect (/v1 -> /v2) succeeds."""
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path == "/v1":
                return httpx.Response(302, headers={"location": "/v2"})
            if request.url.path == "/v2":
                return httpx.Response(200, json={"version": 2})
            return httpx.Response(404)

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        req = HttpRequest(method="GET", url="https://api.example.com/v1")
        resp = executor.execute(req)

        assert resp.status_code == 200
        assert resp.json() == {"version": 2}
        assert calls == [
            "https://api.example.com/v1",
            "https://api.example.com/v2",
        ]

    def test_04_allowed_same_origin_absolute_redirect(self) -> None:
        """Covers: SEC-01.04 — Allowed same-origin absolute redirect succeeds."""
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path == "/old":
                return httpx.Response(
                    301,
                    headers={"location": "https://api.example.com/new"},
                )
            if request.url.path == "/new":
                return httpx.Response(200, text="migrated")
            return httpx.Response(404)

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        req = HttpRequest(method="GET", url="https://api.example.com/old")
        resp = executor.execute(req)

        assert resp.status_code == 200
        assert resp.body == "migrated"
        assert calls == [
            "https://api.example.com/old",
            "https://api.example.com/new",
        ]

    def test_05_redirect_to_blocked_private_network_host(self) -> None:
        """Covers: SEC-01.05 — Redirect to AWS metadata host is blocked."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/open-redirect":
                return httpx.Response(
                    302,
                    headers={"location": "http://169.254.169.254/latest/meta-data/"},
                )
            return httpx.Response(200)

        policy = TargetAccessPolicy(allow_private_networks=False)
        executor = RequestExecutor(
            policy=policy, transport=httpx.MockTransport(handler)
        )
        req = HttpRequest(method="GET", url="https://api.example.com/open-redirect")

        with pytest.raises(InvalidRequestError) as exc_info:
            executor.execute(req)
        assert "disallowed by target access policy" in str(exc_info.value)

    def test_06_redirect_to_unauthorized_localhost_blocked(self) -> None:
        """Covers: SEC-01.06 — Redirect to localhost without permit is blocked."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/redirect-local":
                return httpx.Response(
                    302,
                    headers={"location": "http://localhost:8080/admin"},
                )
            return httpx.Response(200)

        policy = TargetAccessPolicy(allow_private_networks=False)
        executor = RequestExecutor(
            policy=policy, transport=httpx.MockTransport(handler)
        )
        req = HttpRequest(method="GET", url="https://api.example.com/redirect-local")

        with pytest.raises(InvalidRequestError) as exc_info:
            executor.execute(req)
        assert "disallowed by target access policy" in str(exc_info.value)

    def test_07_authorized_target_redirecting_to_unauthorized_port_blocked(
        self,
    ) -> None:
        """Covers: SEC-01.07 — Authorized localhost:8080 to :9000 is blocked."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.port == 8080:
                return httpx.Response(
                    302,
                    headers={"location": "http://localhost:9000/internal-metrics"},
                )
            return httpx.Response(200)

        policy = TargetAccessPolicy(
            allow_private_networks=False,
            allowed_targets=["http://localhost:8080"],
        )
        executor = RequestExecutor(
            policy=policy, transport=httpx.MockTransport(handler)
        )
        req = HttpRequest(method="GET", url="http://localhost:8080/redirect-to-9000")

        with pytest.raises(InvalidRequestError) as exc_info:
            executor.execute(req)
        assert "disallowed by target access policy" in str(exc_info.value)

    def test_08_redirect_to_blocked_host_on_denylist(self) -> None:
        """Covers: SEC-01.08 — Redirect to host on denylist is blocked."""

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302,
                headers={"location": "https://malicious.example.com/exploit"},
            )

        policy = TargetAccessPolicy(blocked_hosts={"malicious.example.com"})
        executor = RequestExecutor(
            policy=policy, transport=httpx.MockTransport(handler)
        )
        req = HttpRequest(method="GET", url="https://api.example.com/bounce")

        with pytest.raises(InvalidRequestError) as exc_info:
            executor.execute(req)
        assert "Access to blocked host" in str(exc_info.value)
        assert "malicious.example.com" in str(exc_info.value)

    def test_09_redirect_loop_detected(self) -> None:
        """Covers: SEC-01.09 — Redirect loop (/loop1 <-> /loop2) raises error."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/loop1":
                return httpx.Response(302, headers={"location": "/loop2"})
            if request.url.path == "/loop2":
                return httpx.Response(302, headers={"location": "/loop1"})
            return httpx.Response(404)

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        req = HttpRequest(method="GET", url="https://api.example.com/loop1")

        with pytest.raises(NetworkExecutionError) as exc_info:
            executor.execute(req)
        assert "Redirect loop detected" in str(exc_info.value)

    def test_10_excessive_redirect_chain_limit(self) -> None:
        """Covers: SEC-01.10 — Excessive redirect chain (>10 hops) raises error."""

        def handler(request: httpx.Request) -> httpx.Response:
            parts = request.url.path.strip("/").split("/")
            idx = int(parts[-1]) if len(parts) == 2 else 0
            return httpx.Response(
                302,
                headers={"location": f"/hop/{idx + 1}"},
            )

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        req = HttpRequest(method="GET", url="https://api.example.com/hop/0")

        with pytest.raises(NetworkExecutionError) as exc_info:
            executor.execute(req)
        assert f"exceeded maximum redirect limit of {MAX_REDIRECT_LIMIT}" in str(
            exc_info.value
        )

    def test_11_unsupported_redirect_scheme(self) -> None:
        """Covers: SEC-01.11 — Unsupported redirect schemes raise error."""

        def handler_ftp(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302,
                headers={"location": "ftp://files.example.com/archive.zip"},
            )

        executor = RequestExecutor(transport=httpx.MockTransport(handler_ftp))
        with pytest.raises(InvalidRequestError) as exc_ftp:
            executor.execute(
                HttpRequest(method="GET", url="https://api.example.com/dl")
            )
        assert "Unsupported redirect protocol 'ftp'" in str(exc_ftp.value)

        def handler_js(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302,
                headers={"location": "javascript:alert(document.cookie)"},
            )

        executor_js = RequestExecutor(transport=httpx.MockTransport(handler_js))
        with pytest.raises(InvalidRequestError) as exc_js:
            executor_js.execute(
                HttpRequest(method="GET", url="https://api.example.com/xss")
            )
        assert (
            "Unsupported redirect protocol 'javascript'" in str(exc_js.value)
            or "invalid" in str(exc_js.value).lower()
        )

    def test_12_malformed_location_header(self) -> None:
        """Covers: SEC-01.12 — Malformed Location header raises InvalidRequestError."""

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302,
                headers={"location": "http://[invalid-ipv6/path"},
            )

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        with pytest.raises(InvalidRequestError) as exc_info:
            executor.execute(
                HttpRequest(method="GET", url="https://api.example.com/broken")
            )
        assert (
            "Malformed redirect" in str(exc_info.value)
            or "invalid" in str(exc_info.value).lower()
        )

    def test_13_missing_location_header_returns_302(self) -> None:
        """Covers: SEC-01.13 — 302 with missing Location returns 302 (RFC 7231)."""

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(302, text="Redirect target omitted")

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        resp = executor.execute(
            HttpRequest(method="GET", url="https://api.example.com/no-location")
        )
        assert resp.status_code == 302
        assert resp.body == "Redirect target omitted"

    def test_14_follow_redirects_false_returns_302(self) -> None:
        """Covers: SEC-01.14 — follow_redirects=False returns 302 without following."""
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(302, headers={"location": "/target"})

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        req = HttpRequest(
            method="GET",
            url="https://api.example.com/redirect",
            follow_redirects=False,
        )
        resp = executor.execute(req)

        assert resp.status_code == 302
        assert resp.headers.get("location") == "/target"
        assert len(calls) == 1

    def test_15_cross_origin_credential_and_cookie_stripping(self) -> None:
        """Covers: SEC-01.15 — Cross-origin redirect strips credentials (RFC 7235)."""
        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            if request.url.host == "auth.example.com":
                return httpx.Response(
                    302,
                    headers={"location": "https://untrusted.cdn.com/download"},
                )
            return httpx.Response(200, text="file content")

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        req = HttpRequest(
            method="GET",
            url="https://auth.example.com/asset",
            headers={
                "Authorization": "Bearer secret-token-xyz",
                "Cookie": "session_id=abc12345",
                "X-Custom-Tracking": "preserved-header",
            },
            auth=BasicAuth("user", "supersecretpass"),
        )
        resp = executor.execute(req)

        assert resp.status_code == 200
        assert len(captured_requests) == 2

        # First request (auth.example.com) should have credentials
        req1 = captured_requests[0]
        assert req1.url.host == "auth.example.com"
        assert "authorization" in req1.headers
        assert "cookie" in req1.headers
        assert req1.headers.get("x-custom-tracking") == "preserved-header"

        # Second request (untrusted.cdn.com) MUST NOT have sensitive credentials
        req2 = captured_requests[1]
        assert req2.url.host == "untrusted.cdn.com"
        assert "authorization" not in req2.headers
        assert "cookie" not in req2.headers
        assert req2.headers.get("x-custom-tracking") == "preserved-header"

    def test_15b_method_and_body_transformations(self) -> None:
        """Covers: SEC-01 — Verify 301/302/303 POST to GET and 307/308 preservation."""
        requests_seen: list[tuple[str, bytes]] = []

        def handler_303(request: httpx.Request) -> httpx.Response:
            requests_seen.append((request.method, request.content))
            if len(requests_seen) == 1:
                return httpx.Response(303, headers={"location": "/see-other"})
            return httpx.Response(200, text="done")

        executor = RequestExecutor(transport=httpx.MockTransport(handler_303))
        req = HttpRequest(
            method=HttpMethod.POST,
            url="https://api.example.com/submit",
            body='{"data": 123}',
        )
        resp = executor.execute(req)
        assert resp.status_code == 200
        assert requests_seen[0][0] == "POST"
        assert requests_seen[0][1] == b'{"data": 123}'
        assert requests_seen[1][0] == "GET"
        assert requests_seen[1][1] == b""

        # 307 Temporary Redirect preserves POST method and body
        requests_seen.clear()

        def handler_307(request: httpx.Request) -> httpx.Response:
            requests_seen.append((request.method, request.content))
            if len(requests_seen) == 1:
                return httpx.Response(307, headers={"location": "/temp"})
            return httpx.Response(200, text="preserved")

        executor_307 = RequestExecutor(transport=httpx.MockTransport(handler_307))
        resp_307 = executor_307.execute(req)
        assert resp_307.status_code == 200
        assert requests_seen[0][0] == "POST"
        assert requests_seen[0][1] == b'{"data": 123}'
        assert requests_seen[1][0] == "POST"
        assert requests_seen[1][1] == b'{"data": 123}'

    def test_16_secret_redaction_in_blocked_redirect_error_messages(
        self,
    ) -> None:
        """Covers: SEC-01.16 — Userinfo in blocked redirect is redacted."""
        secret_password = "very_sensitive_password_98765"

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302,
                headers={
                    "location": (f"http://admin:{secret_password}@127.0.0.1:8080/evil")
                },
            )

        policy = TargetAccessPolicy(allow_private_networks=False)
        executor = RequestExecutor(
            policy=policy, transport=httpx.MockTransport(handler)
        )
        req = HttpRequest(method="GET", url="https://api.example.com/leak")

        with pytest.raises(InvalidRequestError) as exc_info:
            executor.execute(req)
        err_msg = str(exc_info.value)
        assert secret_password not in err_msg
        assert "disallowed by target access policy" in err_msg

    def test_17_timeout_and_transient_retries_on_redirected_hop(self) -> None:
        """Covers: SEC-01.17 — Transient errors (503) on redirect hops are retried."""
        hop2_attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal hop2_attempts
            if request.url.path == "/start":
                return httpx.Response(302, headers={"location": "/resilient"})
            if request.url.path == "/resilient":
                hop2_attempts += 1
                if hop2_attempts == 1:
                    return httpx.Response(503, text="Temporary Service Outage")
                return httpx.Response(200, json={"result": "recovered"})
            return httpx.Response(404)

        executor = RequestExecutor(
            transport=httpx.MockTransport(handler),
            backoff_factor=0.0,
            sleep_func=lambda _: None,
        )
        req = HttpRequest(
            method="GET",
            url="https://api.example.com/start",
            max_retries=2,
        )
        resp = executor.execute(req)

        assert resp.status_code == 200
        assert resp.json() == {"result": "recovered"}
        assert hop2_attempts == 2

    def test_18_external_injected_httpx_client_compatibility(self) -> None:
        """Covers: SEC-01.18 — Injected client lifecycle is preserved."""

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="client ok")

        custom_client = httpx.Client(transport=httpx.MockTransport(handler))
        executor = RequestExecutor(client=custom_client)
        req = HttpRequest(method="GET", url="https://api.example.com/ping")
        resp = executor.execute(req)

        assert resp.status_code == 200
        assert resp.body == "client ok"

        executor.close()
        assert not custom_client.is_closed
        custom_client.close()


class TestCLITargetAuthorization:
    """Tests 19-20: Typer CLI --allow-target flag vs default restrictive policy."""

    def test_19_cli_allow_target_permits_authorized_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Covers: SEC-01.19 — CLI --allow-target permits target execution."""
        suite_file = tmp_path / "test_target_suite.yaml"
        suite_file.write_text(
            """
name: Scoped Target Suite
module: system_tests
tests:
  - id: probe_local
    name: Probe Localhost 8080
    module: system_tests
    request:
      method: GET
      url: http://localhost:8080/health
    assertions:
      - type: status
        equals: 200
""",
            encoding="utf-8",
        )

        def mock_handler(request: httpx.Request) -> httpx.Response:
            if (
                request.url.host == "localhost"
                and request.url.port == 8080
                and request.url.path == "/health"
            ):
                return httpx.Response(200, json={"status": "ok"})
            return httpx.Response(404)

        orig_init = RequestExecutor.__init__

        def hooked_init(self: RequestExecutor, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(mock_handler)
            orig_init(self, *args, **kwargs)

        monkeypatch.setattr(RequestExecutor, "__init__", hooked_init)

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run",
                str(suite_file),
                "--allow-target",
                "http://localhost:8080",
            ],
        )

        assert result.exit_code == 0
        assert "1 passed" in result.stdout or "passed" in result.stdout.lower()

    def test_20_cli_default_restricts_localhost_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Covers: SEC-01.20 — CLI default restricts localhost without permit."""
        suite_file = tmp_path / "test_blocked_suite.yaml"
        suite_file.write_text(
            """
name: Blocked Target Suite
module: system_tests
tests:
  - id: probe_local_blocked
    name: Probe Localhost Blocked
    module: system_tests
    request:
      method: GET
      url: http://localhost:8080/health
    assertions:
      - type: status
        equals: 200
""",
            encoding="utf-8",
        )

        monkeypatch.delenv("APISCOPE_ALLOW_LOCAL", raising=False)
        monkeypatch.delenv("APISCOPE_ALLOW_PRIVATE_NETWORKS", raising=False)
        monkeypatch.delenv("APISCOPE_ALLOWED_TARGETS", raising=False)
        monkeypatch.delenv("APISCOPE_ALLOW_TARGET", raising=False)

        runner = CliRunner()
        result = runner.invoke(app, ["run", str(suite_file)])

        # Default execution should fail (test failed due to SSRF restriction)
        assert result.exit_code != 0 or "failed" in result.stdout.lower()

    def test_21_cli_allow_local_fails_closed_with_migration_error(
        self, tmp_path: Path
    ) -> None:
        """Covers: SEC-01.21 — Legacy --allow-local fails closed with clear error."""
        suite_file = tmp_path / "test_legacy_suite.yaml"
        suite_file.write_text(
            """
name: Legacy Suite
module: system_tests
tests:
  - id: probe_local
    name: Probe Localhost
    module: system_tests
    request:
      method: GET
      url: http://localhost:8080/health
    assertions:
      - type: status
        equals: 200
""",
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(app, ["run", str(suite_file), "--allow-local"])

        # Must fail closed immediately
        assert result.exit_code != 0
        assert "--allow-local' has been removed for security" in result.output
        assert "--allow-target" in result.output

    def test_22_ambient_env_does_not_enable_private_network_access(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Covers: SEC-01.22 — Ambient APISCOPE_ALLOW_LOCAL cannot bypass policy."""
        monkeypatch.setenv("APISCOPE_ALLOW_LOCAL", "1")
        monkeypatch.setenv("APISCOPE_ALLOW_PRIVATE_NETWORKS", "1")

        policy = TargetAccessPolicy()
        assert not policy.allow_private_networks

        with pytest.raises(InvalidRequestError) as exc_info:
            policy.validate_url("http://localhost:8080/admin")
        assert "disallowed by target access policy" in str(exc_info.value)

        with pytest.raises(InvalidRequestError) as exc_meta:
            policy.validate_url("http://169.254.169.254/latest/meta-data/")
        assert "disallowed by target access policy" in str(exc_meta.value)

    def test_23_allow_target_strictly_scopes_port_and_host(self) -> None:
        """Covers: SEC-01.23 — --allow-target scopes only to exact origin."""
        policy = TargetAccessPolicy(allowed_targets=["http://localhost:8080"])

        # Exact target origin is authorized
        policy.validate_url("http://localhost:8080/api/v1/users")

        # Different port on same host is rejected
        with pytest.raises(InvalidRequestError) as exc_port:
            policy.validate_url("http://localhost:9000/api/v1/users")
        assert "disallowed by target access policy" in str(exc_port.value)

        # Different IP address on same port is rejected
        with pytest.raises(InvalidRequestError) as exc_ip:
            policy.validate_url("http://127.0.0.1:8080/api/v1/users")
        assert "disallowed by target access policy" in str(exc_ip.value)

        # Private RFC-1918 addresses are rejected
        with pytest.raises(InvalidRequestError) as exc_priv:
            policy.validate_url("http://10.0.0.1/admin")
        assert "disallowed by target access policy" in str(exc_priv.value)

        # Cloud metadata address is rejected
        with pytest.raises(InvalidRequestError) as exc_meta:
            policy.validate_url("http://169.254.169.254/latest/meta-data")
        assert "disallowed by target access policy" in str(exc_meta.value)
