"""Unit tests for the Core HTTP Request Runner and TargetAccessPolicy."""

import io
import json

import httpx
import pytest

from apiscope.core.contracts import RequestExecutorProtocol
from apiscope.core.exceptions import (
    InvalidRequestError,
    NetworkExecutionError,
    RequestTimeoutError,
)
from apiscope.core.executor import (
    RequestExecutor,
    TargetAccessPolicy,
)
from apiscope.core.logging_config import configure_logging
from apiscope.core.models import (
    ApiKeyAuth,
    ApiKeyLocation,
    BasicAuth,
    BearerAuth,
    BodyType,
    HttpRequest,
)


class TestBasicHttpExecution:
    """Tests 1-7: Execution of all supported HTTP methods."""

    def test_get_request(self) -> None:
        """Test 1: Verify GET request execution."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert str(request.url) == "https://api.example.com/items"
            return httpx.Response(200, json={"items": ["a", "b"]})

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        req = HttpRequest(method="GET", url="https://api.example.com/items")
        resp = executor.execute(req)

        assert resp.status_code == 200
        assert resp.json() == {"items": ["a", "b"]}
        assert resp.is_success is True

    def test_post_json_request(self) -> None:
        """Test 2: Verify POST with JSON body."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert request.headers["content-type"] == "application/json"
            payload = json.loads(request.content.decode("utf-8"))
            assert payload == {"name": "John", "age": 22}
            return httpx.Response(201, json={"id": 101, **payload})

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        req = HttpRequest(
            method="POST",
            url="https://api.example.com/users",
            body={"name": "John", "age": 22},
            body_type=BodyType.JSON,
        )
        resp = executor.execute(req)

        assert resp.status_code == 201
        assert resp.json()["id"] == 101

    def test_put_request(self) -> None:
        """Test 3: Verify PUT request execution."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "PUT"
            return httpx.Response(200, json={"updated": True})

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        req = HttpRequest(
            method="PUT",
            url="https://api.example.com/users/1",
            body={"name": "Updated"},
            body_type=BodyType.JSON,
        )
        resp = executor.execute(req)

        assert resp.status_code == 200
        assert resp.json() == {"updated": True}

    def test_patch_request(self) -> None:
        """Test 4: Verify PATCH request execution."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "PATCH"
            return httpx.Response(200, json={"patched": True})

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        req = HttpRequest(
            method="PATCH",
            url="https://api.example.com/users/1",
            body={"age": 23},
            body_type=BodyType.JSON,
        )
        resp = executor.execute(req)

        assert resp.status_code == 200
        assert resp.json() == {"patched": True}

    def test_delete_request(self) -> None:
        """Test 5: Verify DELETE request execution."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "DELETE"
            return httpx.Response(204)

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        req = HttpRequest(method="DELETE", url="https://api.example.com/users/1")
        resp = executor.execute(req)

        assert resp.status_code == 204
        assert resp.body == ""

    def test_head_request(self) -> None:
        """Test 6: Verify HEAD request execution."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "HEAD"
            return httpx.Response(200, headers={"X-Total-Count": "100"})

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        req = HttpRequest(method="HEAD", url="https://api.example.com/items")
        resp = executor.execute(req)

        assert resp.status_code == 200
        assert resp.headers["x-total-count"] == "100"
        assert resp.body == ""

    def test_options_request(self) -> None:
        """Test 7: Verify OPTIONS request execution."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "OPTIONS"
            return httpx.Response(200, headers={"Allow": "GET, POST, OPTIONS"})

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        req = HttpRequest(method="OPTIONS", url="https://api.example.com/items")
        resp = executor.execute(req)

        assert resp.status_code == 200
        assert "OPTIONS" in resp.headers["allow"]


class TestRequestConfiguration:
    """Tests 8-14: Verification of headers, query parameters, payloads, and auth."""

    def test_custom_headers(self) -> None:
        """Test 8: Verify arbitrary HTTP headers propagation."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["x-api-version"] == "2.0"
            assert request.headers["x-client-id"] == "apiscope-test"
            return httpx.Response(200)

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        req = HttpRequest(
            method="GET",
            url="https://api.example.com/status",
            headers={"X-API-Version": "2.0", "X-Client-ID": "apiscope-test"},
        )
        resp = executor.execute(req)
        assert resp.status_code == 200

    def test_query_parameters(self) -> None:
        """Test 9: Verify query parameters handling via httpx."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert "role=admin" in str(request.url)
            assert "active=true" in str(request.url)
            return httpx.Response(200)

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        req = HttpRequest(
            method="GET",
            url="https://api.example.com/users",
            params={"role": "admin", "active": "true"},
        )
        resp = executor.execute(req)
        assert resp.status_code == 200

    def test_form_data_body(self) -> None:
        """Test 10: Verify application/x-www-form-urlencoded body handling."""

        def handler(request: httpx.Request) -> httpx.Response:
            content_type = request.headers.get("content-type", "")
            assert "application/x-www-form-urlencoded" in content_type
            body_text = request.content.decode("utf-8")
            assert "grant_type=password" in body_text
            assert "username=alice" in body_text
            return httpx.Response(200)

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        req = HttpRequest(
            method="POST",
            url="https://api.example.com/token",
            body={"grant_type": "password", "username": "alice"},
            body_type=BodyType.FORM,
        )
        resp = executor.execute(req)
        assert resp.status_code == 200

    def test_raw_text_body(self) -> None:
        """Test 11: Verify raw text body handling."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.content == b"plain text payload data"
            return httpx.Response(200)

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        req = HttpRequest(
            method="POST",
            url="https://api.example.com/echo",
            body="plain text payload data",
            body_type=BodyType.RAW,
        )
        resp = executor.execute(req)
        assert resp.status_code == 200

    def test_bearer_authentication(self) -> None:
        """Test 12: Verify Bearer token authentication injection."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["authorization"] == "Bearer test_secret_token_123"
            return httpx.Response(200)

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        req = HttpRequest(
            method="GET",
            url="https://api.example.com/protected",
            auth=BearerAuth(token="test_secret_token_123"),
        )
        resp = executor.execute(req)
        assert resp.status_code == 200

    def test_basic_authentication(self) -> None:
        """Test 13: Verify HTTP Basic authentication injection."""

        def handler(request: httpx.Request) -> httpx.Response:
            auth_header = request.headers.get("authorization", "")
            assert auth_header.startswith("Basic ")
            return httpx.Response(200)

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        req = HttpRequest(
            method="GET",
            url="https://api.example.com/basic-protected",
            auth=BasicAuth(username="user1", password="password456"),
        )
        resp = executor.execute(req)
        assert resp.status_code == 200

    def test_api_key_authentication(self) -> None:
        """Test 14: Verify API key injection across Header, Query, and Cookie."""

        # 14a. Header
        def header_handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["x-api-key"] == "secret-key-val"
            return httpx.Response(200)

        exec_header = RequestExecutor(transport=httpx.MockTransport(header_handler))
        req_header = HttpRequest(
            method="GET",
            url="https://api.example.com/data",
            auth=ApiKeyAuth(
                name="X-API-Key",
                value="secret-key-val",
                location=ApiKeyLocation.HEADER,
            ),
        )
        assert exec_header.execute(req_header).status_code == 200

        # 14b. Query
        def query_handler(request: httpx.Request) -> httpx.Response:
            assert "api_token=secret-query-val" in str(request.url)
            return httpx.Response(200)

        exec_query = RequestExecutor(transport=httpx.MockTransport(query_handler))
        req_query = HttpRequest(
            method="GET",
            url="https://api.example.com/data",
            auth=ApiKeyAuth(
                name="api_token",
                value="secret-query-val",
                location=ApiKeyLocation.QUERY,
            ),
        )
        assert exec_query.execute(req_query).status_code == 200

        # 14c. Cookie
        def cookie_handler(request: httpx.Request) -> httpx.Response:
            cookie_header = request.headers.get("cookie", "")
            assert "session_id=secret-cookie-val" in cookie_header
            return httpx.Response(200)

        exec_cookie = RequestExecutor(transport=httpx.MockTransport(cookie_handler))
        req_cookie = HttpRequest(
            method="GET",
            url="https://api.example.com/data",
            auth=ApiKeyAuth(
                name="session_id",
                value="secret-cookie-val",
                location=ApiKeyLocation.COOKIE,
            ),
        )
        assert exec_cookie.execute(req_cookie).status_code == 200


class TestResponseHandling:
    """Tests 15-22: Response attribute capture and formatting."""

    def test_status_code_capture(self) -> None:
        """Test 15: Verify preservation of 2xx, 4xx, and 5xx status codes."""
        for code in [200, 201, 400, 401, 403, 404, 500, 502]:

            def handler(_req: httpx.Request, status: int = code) -> httpx.Response:
                return httpx.Response(status)

            executor = RequestExecutor(
                transport=httpx.MockTransport(handler), backoff_factor=0.0
            )
            req = HttpRequest(
                method="GET", url="https://api.example.com/test", max_retries=0
            )
            resp = executor.execute(req)
            assert resp.status_code == code

    def test_response_headers_capture(self) -> None:
        """Test 16: Verify response headers dictionary capture."""

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"X-Server": "TestServer/1.0", "Content-Type": "text/plain"},
            )

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        resp = executor.execute(
            HttpRequest(method="GET", url="https://api.example.com/test")
        )
        assert resp.headers["x-server"] == "TestServer/1.0"
        assert resp.headers["content-type"] == "text/plain"

    def test_response_body_capture(self) -> None:
        """Test 17: Verify text body string capture."""

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="Sample body payload")

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        resp = executor.execute(
            HttpRequest(method="GET", url="https://api.example.com/test")
        )
        assert resp.body == "Sample body payload"

    def test_response_size_calculation(self) -> None:
        """Test 18: Verify response byte size is derived from raw content."""
        payload_bytes = "Hello 🚀".encode()

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=payload_bytes)

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        resp = executor.execute(
            HttpRequest(method="GET", url="https://api.example.com/test")
        )
        assert resp.size_bytes == len(payload_bytes)

    def test_response_time_measurement(self) -> None:
        """Test 19: Verify execution time elapsed_ms is captured."""

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        resp = executor.execute(
            HttpRequest(method="GET", url="https://api.example.com/test")
        )
        assert resp.elapsed_ms >= 0.0

    def test_final_url_capture(self) -> None:
        """Test 20: Verify capture of final response URL."""

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, request=_req)

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        resp = executor.execute(
            HttpRequest(method="GET", url="https://api.example.com/endpoint?foo=bar")
        )
        assert resp.url == "https://api.example.com/endpoint?foo=bar"

    def test_empty_response_body(self) -> None:
        """Test 21: Verify handling of empty response bodies."""

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(204, content=b"")

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        resp = executor.execute(
            HttpRequest(method="DELETE", url="https://api.example.com/item/1")
        )
        assert resp.status_code == 204
        assert resp.body == ""
        assert resp.size_bytes == 0

    def test_head_response_handling(self) -> None:
        """Test 22: Verify HEAD response handles zero content with headers."""

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"Content-Length": "1024"})

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        resp = executor.execute(
            HttpRequest(method="HEAD", url="https://api.example.com/resource")
        )
        assert resp.status_code == 200
        assert resp.body == ""
        assert resp.headers["content-length"] == "1024"


class TestFailureHandling:
    """Tests 23-27: Transport and protocol error conversion."""

    def test_timeout_handling(self) -> None:
        """Test 23: Verify TimeoutException maps to RequestTimeoutError."""

        def handler(_req: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("Read timed out")

        executor = RequestExecutor(
            transport=httpx.MockTransport(handler), backoff_factor=0.0
        )
        req = HttpRequest(
            method="GET",
            url="https://api.example.com/slow",
            timeout=5.0,
            max_retries=0,
        )

        with pytest.raises(RequestTimeoutError) as exc_info:
            executor.execute(req)
        assert "timed out after 5.0s" in str(exc_info.value)

    def test_connection_error_handling(self) -> None:
        """Test 24: Verify ConnectError maps to NetworkExecutionError."""

        def handler(_req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Failed to establish a new connection")

        executor = RequestExecutor(
            transport=httpx.MockTransport(handler), backoff_factor=0.0
        )
        req = HttpRequest(
            method="GET", url="https://api.example.com/unreachable", max_retries=0
        )

        with pytest.raises(NetworkExecutionError) as exc_info:
            executor.execute(req)
        assert "Network error executing HTTP GET" in str(exc_info.value)

    def test_invalid_url_handling(self) -> None:
        """Test 25: Verify malformed URLs raise InvalidRequestError."""
        executor = RequestExecutor()
        for bad_url in ["http://", "https://", "not-a-url"]:
            with pytest.raises(InvalidRequestError):
                executor.execute(HttpRequest(method="GET", url=bad_url))

    def test_unsupported_protocol_handling(self) -> None:
        """Test 26: Verify non-HTTP protocols raise InvalidRequestError."""
        executor = RequestExecutor()
        for scheme_url in [
            "ftp://files.example.com/data",
            "file:///etc/passwd",
            "gopher://example.com",
        ]:
            with pytest.raises(InvalidRequestError) as exc_info:
                executor.execute(HttpRequest(method="GET", url=scheme_url))
            assert "Unsupported protocol" in str(exc_info.value)

    def test_too_many_redirects_handling(self) -> None:
        """Test 27: Verify TooManyRedirects maps to NetworkExecutionError."""

        def handler(_req: httpx.Request) -> httpx.Response:
            raise httpx.TooManyRedirects("Exceeded redirect limit")

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        req = HttpRequest(method="GET", url="https://api.example.com/loop")

        with pytest.raises(NetworkExecutionError) as exc_info:
            executor.execute(req)
        assert "Too many redirects" in str(exc_info.value)


class TestRetriesAndRedirects:
    """Tests 28-32: Retries, transient errors, idempotency, redirects."""

    def test_retry_after_transient_server_error(self) -> None:
        """Test 28: Verify successful retry on transient 503 response."""
        calls = 0

        def handler(_req: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(503, text="Service Unavailable")
            return httpx.Response(200, json={"status": "recovered"})

        executor = RequestExecutor(
            transport=httpx.MockTransport(handler), backoff_factor=0.0
        )
        req = HttpRequest(
            method="GET",
            url="https://api.example.com/flaky",
            max_retries=2,
        )
        resp = executor.execute(req)

        assert calls == 2
        assert resp.status_code == 200
        assert resp.json() == {"status": "recovered"}

    def test_no_retry_after_permanent_client_error(self) -> None:
        """Test 29: Verify permanent 4xx errors (400, 404) are NOT retried."""
        calls = 0

        def handler(_req: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(404, json={"error": "Not Found"})

        executor = RequestExecutor(
            transport=httpx.MockTransport(handler), backoff_factor=0.0
        )
        req = HttpRequest(
            method="GET",
            url="https://api.example.com/missing",
            max_retries=3,
        )
        resp = executor.execute(req)

        assert calls == 1  # No retries triggered
        assert resp.status_code == 404

    def test_respect_retry_limits(self) -> None:
        """Test 30: Verify retry loop halts when max_retries limit is reached."""
        calls = 0

        def handler(_req: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(500, text="Persistent Internal Error")

        executor = RequestExecutor(
            transport=httpx.MockTransport(handler), backoff_factor=0.0
        )
        req = HttpRequest(
            method="GET",
            url="https://api.example.com/broken",
            max_retries=2,
        )
        resp = executor.execute(req)

        # 1 initial attempt + 2 retries = 3 total calls
        assert calls == 3
        assert resp.status_code == 500

    def test_redirect_configuration(self) -> None:
        """Test 31: Verify follow_redirects behavior."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/old":
                return httpx.Response(
                    302,
                    headers={"Location": "https://api.example.com/new"},
                )
            return httpx.Response(200, text="New Location Content")

        # 31a: follow_redirects=False -> return 302
        exec_no_follow = RequestExecutor(transport=httpx.MockTransport(handler))
        req_no_follow = HttpRequest(
            method="GET",
            url="https://api.example.com/old",
            follow_redirects=False,
        )
        resp_no_follow = exec_no_follow.execute(req_no_follow)
        assert resp_no_follow.status_code == 302
        assert resp_no_follow.headers["location"] == "https://api.example.com/new"

    def test_prevent_automatic_retry_of_non_idempotent_requests(self) -> None:
        """Test 32: Ensure POST requests are not repeated unless configured."""
        # 32a: Default behavior -> POST not retried
        calls_default = 0

        def handler_default(_req: httpx.Request) -> httpx.Response:
            nonlocal calls_default
            calls_default += 1
            return httpx.Response(500, text="Error")

        exec_default = RequestExecutor(
            transport=httpx.MockTransport(handler_default), backoff_factor=0.0
        )
        req_default = HttpRequest(
            method="POST",
            url="https://api.example.com/orders",
            body={"item": "book"},
            body_type=BodyType.JSON,
            max_retries=3,
            retry_non_idempotent=False,
        )
        resp_default = exec_default.execute(req_default)
        assert calls_default == 1
        assert resp_default.status_code == 500

        # 32b: Explicitly allowed -> POST retried
        calls_explicit = 0

        def handler_explicit(_req: httpx.Request) -> httpx.Response:
            nonlocal calls_explicit
            calls_explicit += 1
            if calls_explicit < 3:
                return httpx.Response(500, text="Error")
            return httpx.Response(200, json={"order_id": 999})

        exec_explicit = RequestExecutor(
            transport=httpx.MockTransport(handler_explicit), backoff_factor=0.0
        )
        req_explicit = HttpRequest(
            method="POST",
            url="https://api.example.com/orders",
            body={"item": "book"},
            body_type=BodyType.JSON,
            max_retries=3,
            retry_non_idempotent=True,
        )
        resp_explicit = exec_explicit.execute(req_explicit)
        assert calls_explicit == 3
        assert resp_explicit.status_code == 200


class TestSecurityAndTargetAccessPolicy:
    """Tests 33-35: Security logging safeguards and SSRF target access policy."""

    def test_sensitive_credentials_not_written_to_logs(self) -> None:
        """Test 33: Ensure credentials and tokens do not leak into executor logs."""
        log_stream = io.StringIO()
        configure_logging(level="DEBUG", stream=log_stream)

        secret_token = "secret_jwt_token_987654"
        secret_api_key = "super_secret_api_key_321"

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        executor = RequestExecutor(transport=httpx.MockTransport(handler))
        req = HttpRequest(
            method="GET",
            url=f"https://api.example.com/test?token={secret_token}",
            headers={"Authorization": f"Bearer {secret_token}"},
            auth=ApiKeyAuth("X-Key", secret_api_key, location=ApiKeyLocation.HEADER),
        )
        executor.execute(req)

        logged_content = log_stream.getvalue()
        assert secret_token not in logged_content
        assert secret_api_key not in logged_content

    def test_target_access_policy_blocks_restricted_addresses(self) -> None:
        """Test 34: TargetAccessPolicy rejects loopback, private, and link-local."""
        policy = TargetAccessPolicy(allow_private_networks=False)
        executor = RequestExecutor(policy=policy)

        disallowed_urls = [
            "http://localhost/admin",
            "http://localhost.localdomain/admin",
            "http://127.0.0.1/admin",
            "http://127.0.0.1:8080/metrics",
            "http://10.0.0.1/private",
            "http://172.16.0.1/internal",
            "http://192.168.1.1/router",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]/status",
        ]

        for url in disallowed_urls:
            with pytest.raises(InvalidRequestError) as exc_info:
                executor.execute(HttpRequest(method="GET", url=url))
            assert "disallowed by target access policy" in str(
                exc_info.value
            ) or "disallowed" in str(exc_info.value)

    def test_explicitly_authorized_local_test_targets_work(self) -> None:
        """Test 35: Whitelisted local test hosts are permitted to execute."""

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "local_dev_ok"})

        # Whitelist localhost and 127.0.0.1 explicitly
        policy = TargetAccessPolicy(
            allow_private_networks=False,
            allowed_hosts={"localhost", "127.0.0.1"},
        )
        executor = RequestExecutor(
            policy=policy, transport=httpx.MockTransport(handler)
        )

        resp_localhost = executor.execute(
            HttpRequest(method="GET", url="http://localhost:8000/api")
        )
        assert resp_localhost.status_code == 200
        assert resp_localhost.json() == {"status": "local_dev_ok"}

        resp_ip = executor.execute(
            HttpRequest(method="GET", url="http://127.0.0.1:8000/api")
        )
        assert resp_ip.status_code == 200

    def test_blocked_hosts_blacklist(self) -> None:
        """Verify explicit blocked_hosts blacklist works."""
        policy = TargetAccessPolicy(blocked_hosts={"malicious.example.com"})
        executor = RequestExecutor(policy=policy)

        with pytest.raises(InvalidRequestError) as exc_info:
            executor.execute(
                HttpRequest(method="GET", url="https://malicious.example.com/api")
            )
        assert "Access to blocked host" in str(exc_info.value)

    def test_executor_satisfies_protocol(self) -> None:
        """Verify RequestExecutor conforms to RequestExecutorProtocol."""
        executor = RequestExecutor()
        assert isinstance(executor, RequestExecutorProtocol)
        executor.close()

    def test_context_manager_lifecycle(self) -> None:
        """Verify RequestExecutor functions cleanly as a context manager."""
        with RequestExecutor() as executor:
            assert isinstance(executor, RequestExecutor)
