"""Unit tests for ApiScope core architecture and foundational data models."""

import io
import logging

import pytest

from apiscope.core.config import AppConfig, get_config, load_config
from apiscope.core.contracts import (
    AssertionEngineProtocol,
    ExtractorProtocol,
    RequestExecutorProtocol,
    VariableResolverProtocol,
)
from apiscope.core.exceptions import (
    ApiScopeError,
    ConfigurationError,
    InvalidRequestError,
    NetworkExecutionError,
    RequestTimeoutError,
    UnsupportedMethodError,
)
from apiscope.core.logging_config import (
    configure_logging,
    get_logger,
    redact_sensitive_text,
)
from apiscope.core.models import (
    ApiKeyAuth,
    ApiKeyLocation,
    AuthType,
    BasicAuth,
    BearerAuth,
    BodyType,
    HttpMethod,
    HttpRequest,
    HttpResponse,
)


class TestHttpRequestModel:
    """Tests for HTTP request model creation and validation."""

    def test_valid_http_request_creation(self) -> None:
        """Verify request creation with all supported fields and normalization."""
        req = HttpRequest(
            method="post",
            url="https://api.example.com/v1/users",
            headers={"Accept": "application/json", "X-Custom": "test"},
            params={"role": "admin", "active": True},
            body={"name": "Alice"},
            body_type="json",
            timeout=15.0,
            max_retries=2,
            follow_redirects=False,
            auth=BearerAuth(token="auth-token-123"),
        )
        assert req.method == HttpMethod.POST
        assert req.url == "https://api.example.com/v1/users"
        assert req.headers["Accept"] == "application/json"
        assert req.params == {"role": "admin", "active": True}
        assert req.body == {"name": "Alice"}
        assert req.body_type == BodyType.JSON
        assert req.timeout == 15.0
        assert req.max_retries == 2
        assert req.follow_redirects is False
        assert req.auth is not None
        assert req.auth.auth_type == AuthType.BEARER

    def test_all_supported_http_methods(self) -> None:
        """Verify each required HTTP method is supported and normalized."""
        for method_name in [
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "HEAD",
            "OPTIONS",
        ]:
            req = HttpRequest(method=method_name.lower(), url="https://example.com")
            assert req.method == HttpMethod(method_name)

    def test_unsupported_http_method_raises_error(self) -> None:
        """Verify unsupported HTTP methods raise UnsupportedMethodError."""
        for invalid_method in ["CONNECT", "TRACE", "PURGE", "INVALID", ""]:
            with pytest.raises(UnsupportedMethodError) as exc_info:
                HttpRequest(method=invalid_method, url="https://example.com")
            assert "Unsupported HTTP method" in str(exc_info.value)

    def test_invalid_method_type_raises_error(self) -> None:
        """Verify non-string method types raise UnsupportedMethodError."""
        with pytest.raises(UnsupportedMethodError):
            HttpRequest(method=123, url="https://example.com")  # type: ignore[arg-type]

    def test_empty_or_whitespace_url_raises_error(self) -> None:
        """Verify empty URLs raise InvalidRequestError."""
        for bad_url in ["", "   ", "\t\n"]:
            with pytest.raises(InvalidRequestError) as exc_info:
                HttpRequest(method="GET", url=bad_url)
            assert "URL cannot be empty" in str(exc_info.value)

    def test_invalid_timeout_configurations(self) -> None:
        """Verify invalid timeout configurations raise InvalidRequestError."""
        # Non-positive values
        for bad_timeout in [0, -1, -0.5]:
            with pytest.raises(InvalidRequestError) as exc_info:
                HttpRequest(
                    method="GET", url="https://example.com", timeout=bad_timeout
                )
            assert "timeout must be greater than 0" in str(exc_info.value)

        # Non-numeric values (including boolean which is a subclass of int)
        for non_numeric in ["five", None, True, False]:
            with pytest.raises(InvalidRequestError) as exc_info:
                HttpRequest(
                    method="GET",
                    url="https://example.com",
                    timeout=non_numeric,  # type: ignore[arg-type]
                )
            assert "timeout must be a numeric value" in str(exc_info.value)

    def test_invalid_retry_configurations(self) -> None:
        """Verify invalid retry values raise InvalidRequestError."""
        with pytest.raises(InvalidRequestError) as exc_info:
            HttpRequest(method="GET", url="https://example.com", max_retries=-1)
        assert "retry count cannot be negative" in str(exc_info.value)

        with pytest.raises(InvalidRequestError):
            HttpRequest(
                method="GET",
                url="https://example.com",
                max_retries=1.5,  # type: ignore[arg-type]
            )

    def test_invalid_body_type_raises_error(self) -> None:
        """Verify unrecognized body types raise InvalidRequestError."""
        with pytest.raises(InvalidRequestError) as exc_info:
            HttpRequest(
                method="POST",
                url="https://example.com",
                body_type="xml_binary",
            )
        assert "Invalid body type" in str(exc_info.value)

    def test_invalid_headers_or_params_raises_error(self) -> None:
        """Verify non-dict headers or query params raise InvalidRequestError."""
        with pytest.raises(InvalidRequestError):
            HttpRequest(
                method="GET",
                url="https://example.com",
                headers="bad_headers",  # type: ignore[arg-type]
            )

        with pytest.raises(InvalidRequestError):
            HttpRequest(
                method="GET",
                url="https://example.com",
                params=["bad_params"],  # type: ignore[arg-type]
            )


class TestHttpResponseModel:
    """Tests for HTTP response model creation and helper behavior."""

    def test_valid_http_response_creation(self) -> None:
        """Verify response creation with full metrics and helpers."""
        resp = HttpResponse(
            status_code=200,
            headers={"Content-Type": "application/json"},
            body='{"id": 42, "status": "active"}',
            elapsed_ms=145.2,
            size_bytes=32,
            url="https://api.example.com/v1/users/42",
        )
        assert resp.status_code == 200
        assert resp.is_success is True
        assert resp.is_client_error is False
        assert resp.is_server_error is False
        assert resp.elapsed_ms == 145.2
        assert resp.size_bytes == 32
        assert resp.json() == {"id": 42, "status": "active"}

    def test_response_status_categories(self) -> None:
        """Verify status classification properties across response categories."""
        success_resp = HttpResponse(status_code=201)
        assert success_resp.is_success is True

        client_err = HttpResponse(status_code=404)
        assert client_err.is_client_error is True
        assert client_err.is_success is False

        server_err = HttpResponse(status_code=503)
        assert server_err.is_server_error is True
        assert server_err.is_success is False

    def test_auto_size_calculation_from_body(self) -> None:
        """Verify byte size is automatically computed when passed as 0."""
        text = "Hello, World! 🚀"
        resp = HttpResponse(status_code=200, body=text, size_bytes=0)
        assert resp.size_bytes == len(text.encode("utf-8"))

    def test_invalid_response_status_code(self) -> None:
        """Verify out-of-range status codes raise InvalidRequestError."""
        for invalid_code in [99, 600, -200]:
            with pytest.raises(InvalidRequestError):
                HttpResponse(status_code=invalid_code)

    def test_invalid_response_metrics(self) -> None:
        """Verify negative elapsed time or byte sizes raise InvalidRequestError."""
        with pytest.raises(InvalidRequestError):
            HttpResponse(status_code=200, elapsed_ms=-1.0)

        with pytest.raises(InvalidRequestError):
            HttpResponse(status_code=200, size_bytes=-10)

    def test_json_parsing_failure_raises_value_error(self) -> None:
        """Verify non-JSON response bodies raise ValueError on json() call."""
        resp = HttpResponse(status_code=200, body="<html>Not JSON</html>")
        with pytest.raises(ValueError) as exc_info:
            resp.json()
        assert "not valid JSON" in str(exc_info.value)


class TestAuthenticationModels:
    """Tests for Bearer, Basic, and API Key authentication models."""

    def test_bearer_auth(self) -> None:
        """Verify Bearer authentication model attributes and validation."""
        auth = BearerAuth(token="test_bearer_token")
        assert auth.auth_type == AuthType.BEARER
        assert auth.get_token() == "test_bearer_token"

        # Empty token validation
        with pytest.raises(InvalidRequestError):
            BearerAuth(token="")
        with pytest.raises(InvalidRequestError):
            BearerAuth(token="   ")

    def test_basic_auth(self) -> None:
        """Verify Basic authentication model attributes and validation."""
        auth = BasicAuth(username="admin_user", password="secure_pass_123")
        assert auth.auth_type == AuthType.BASIC
        assert auth.username == "admin_user"
        assert auth.get_password() == "secure_pass_123"

        with pytest.raises(InvalidRequestError):
            BasicAuth(username=123, password="abc")  # type: ignore[arg-type]

    def test_api_key_auth(self) -> None:
        """Verify API key authentication model across supported locations."""
        header_auth = ApiKeyAuth(
            name="X-API-KEY",
            value="key_value_123",
            location=ApiKeyLocation.HEADER,
        )
        assert header_auth.auth_type == AuthType.API_KEY
        assert header_auth.name == "X-API-KEY"
        assert header_auth.get_value() == "key_value_123"
        assert header_auth.location == ApiKeyLocation.HEADER

        # String location normalization
        query_auth = ApiKeyAuth(name="api_key", value="xyz", location="query")  # type: ignore[arg-type]
        assert query_auth.location == ApiKeyLocation.QUERY

        # Empty values validation
        with pytest.raises(InvalidRequestError):
            ApiKeyAuth(name="", value="val")
        with pytest.raises(InvalidRequestError):
            ApiKeyAuth(name="key", value="")
        with pytest.raises(InvalidRequestError):
            ApiKeyAuth(name="key", value="val", location="invalid_loc")  # type: ignore[arg-type]


class TestSecretProtection:
    """Tests ensuring secrets are never leaked into string representations or logs."""

    def test_auth_repr_and_str_mask_secrets(self) -> None:
        """Verify auth models mask credentials in both __repr__ and __str__."""
        bearer = BearerAuth(token="super_secret_bearer_token_xyz")
        assert "super_secret_bearer_token_xyz" not in repr(bearer)
        assert "super_secret_bearer_token_xyz" not in str(bearer)
        assert "***" in repr(bearer)

        basic = BasicAuth(username="user1", password="top_secret_password_456")
        assert "top_secret_password_456" not in repr(basic)
        assert "top_secret_password_456" not in str(basic)
        assert "user1" in repr(basic)
        assert "***" in repr(basic)

        api_key = ApiKeyAuth(name="X-Key", value="ultra_confidential_key_789")
        assert "ultra_confidential_key_789" not in repr(api_key)
        assert "ultra_confidential_key_789" not in str(api_key)
        assert "X-Key" in repr(api_key)
        assert "***" in repr(api_key)

    def test_request_repr_masks_sensitive_headers(self) -> None:
        """Verify HttpRequest masks sensitive credentials in headers."""
        req = HttpRequest(
            method="GET",
            url="https://example.com/api",
            headers={
                "Authorization": "Bearer raw_token_value_abc",
                "X-API-Key": "raw_api_key_secret",
                "Cookie": "session_id=12345",
                "Accept": "application/json",
            },
            auth=BearerAuth(token="auth_token_999"),
        )
        rep = repr(req)
        assert "raw_token_value_abc" not in rep
        assert "raw_api_key_secret" not in rep
        assert "12345" not in rep
        assert "auth_token_999" not in rep
        assert "application/json" in rep

    def test_logging_redaction_scrubs_secrets(self) -> None:
        """Verify centralized logging scrubs passwords, tokens, and keys."""
        log_stream = io.StringIO()
        logger = configure_logging(level="DEBUG", stream=log_stream)

        logger.info("Executing request with Bearer ghp_secret_access_token_123456")
        logger.info("Connecting with password=my_db_password_xyz to database")
        logger.info("Supplying api_key='sk-prod-9876543210' for external API")
        logger.info("Connecting to https://admin:p%40ssword@internal-service.local/api")

        output = log_stream.getvalue()
        assert "ghp_secret_access_token_123456" not in output
        assert "my_db_password_xyz" not in output
        assert "sk-prod-9876543210" not in output
        assert "p%40ssword" not in output

        # Assert redactions are present
        assert "Bearer ***" in output
        assert "password='***'" in output
        assert "api_key='***'" in output
        assert "***@" in output

    def test_redact_sensitive_text_utility(self) -> None:
        """Verify the direct text redaction helper function."""
        cleaned = redact_sensitive_text("Authorization: Bearer my_jwt_token_here")
        assert "my_jwt_token_here" not in cleaned
        assert cleaned == "Authorization: Bearer ***"


class TestExceptionHierarchy:
    """Tests verifying the exception tree and catching behavior."""

    def test_exception_inheritance(self) -> None:
        """Verify all domain exceptions descend from ApiScopeError."""
        assert issubclass(ConfigurationError, ApiScopeError)
        assert issubclass(InvalidRequestError, ApiScopeError)
        assert issubclass(UnsupportedMethodError, InvalidRequestError)
        assert issubclass(UnsupportedMethodError, ApiScopeError)
        assert issubclass(RequestTimeoutError, ApiScopeError)
        assert issubclass(NetworkExecutionError, ApiScopeError)
        assert issubclass(ApiScopeError, Exception)

    def test_polymorphic_exception_handling(self) -> None:
        """Verify catching ApiScopeError handles all specialized child errors."""
        exceptions = [
            ConfigurationError("Config issue"),
            InvalidRequestError("Request issue"),
            UnsupportedMethodError("Method issue"),
            RequestTimeoutError("Timeout issue"),
            NetworkExecutionError("Network issue"),
        ]

        for exc in exceptions:
            try:
                raise exc
            except ApiScopeError as caught:
                assert str(caught) in [
                    "Config issue",
                    "Request issue",
                    "Method issue",
                    "Timeout issue",
                    "Network issue",
                ]


class TestConfigurationManagement:
    """Tests for centralized configuration defaults and environment overrides."""

    def test_configuration_defaults(self) -> None:
        """Verify default configuration values without environment variables."""
        cfg = load_config(overrides={})
        assert isinstance(cfg, AppConfig)
        assert cfg.app_name == "ApiScope"
        assert cfg.default_timeout == 30.0
        assert cfg.default_retries == 0
        assert cfg.db_path == "apiscope.db"
        assert cfg.log_level == "INFO"
        assert cfg.environment == "development"

        # Verify convenience accessor
        current_cfg = get_config()
        assert current_cfg.app_name == "ApiScope"

    def test_configuration_environment_overrides(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify environment variables correctly override default settings."""
        monkeypatch.setenv("APISCOPE_APP_NAME", "CustomScope")
        monkeypatch.setenv("APISCOPE_REQUEST_TIMEOUT", "12.5")
        monkeypatch.setenv("APISCOPE_MAX_RETRIES", "4")
        monkeypatch.setenv("APISCOPE_DB_PATH", "custom_store.db")
        monkeypatch.setenv("APISCOPE_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("APISCOPE_ENV", "testing")

        cfg = load_config()
        assert cfg.app_name == "CustomScope"
        assert cfg.default_timeout == 12.5
        assert cfg.default_retries == 4
        assert cfg.db_path == "custom_store.db"
        assert cfg.log_level == "DEBUG"
        assert cfg.environment == "testing"

    def test_configuration_validation_errors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify invalid environment configurations raise ConfigurationError."""
        # Non-numeric timeout
        monkeypatch.setenv("APISCOPE_REQUEST_TIMEOUT", "not_a_number")
        with pytest.raises(ConfigurationError) as exc_info:
            load_config()
        assert "must be numeric" in str(exc_info.value)

        # Non-positive timeout
        monkeypatch.setenv("APISCOPE_REQUEST_TIMEOUT", "0")
        with pytest.raises(ConfigurationError):
            load_config()

        # Negative retries
        monkeypatch.setenv("APISCOPE_REQUEST_TIMEOUT", "30.0")
        monkeypatch.setenv("APISCOPE_MAX_RETRIES", "-3")
        with pytest.raises(ConfigurationError):
            load_config()

        # Invalid log level
        monkeypatch.setenv("APISCOPE_MAX_RETRIES", "0")
        monkeypatch.setenv("APISCOPE_LOG_LEVEL", "VERBOSE_TRACE")
        with pytest.raises(ConfigurationError) as exc_info:
            load_config()
        assert "Allowed:" in str(exc_info.value)

        # Empty database path
        monkeypatch.setenv("APISCOPE_LOG_LEVEL", "INFO")
        monkeypatch.setenv("APISCOPE_DB_PATH", "   ")
        with pytest.raises(ConfigurationError):
            load_config()


class TestLoggingInitialization:
    """Tests for logging initialization and namespacing."""

    def test_logging_initialization(self) -> None:
        """Verify configure_logging sets level and stream correctly."""
        stream = io.StringIO()
        logger = configure_logging(level="WARNING", stream=stream)
        assert logger.level == logging.WARNING
        assert len(logger.handlers) == 1

        logger.warning("Test warning message")
        output = stream.getvalue()
        assert "[WARNING]" in output
        assert "Test warning message" in output

    def test_get_logger_namespacing(self) -> None:
        """Verify get_logger produces apiscope-prefixed loggers."""
        root_logger = get_logger()
        assert root_logger.name == "apiscope"

        cli_logger = get_logger("cli")
        assert cli_logger.name == "apiscope.cli"

        already_prefixed = get_logger("apiscope.data")
        assert already_prefixed.name == "apiscope.data"


class TestSharedProtocols:
    """Tests verifying runtime checking of architectural protocols."""

    def test_request_executor_protocol(self) -> None:
        """Verify RequestExecutorProtocol can check conforming classes."""

        class ConformingExecutor:
            def execute(self, _request: HttpRequest) -> HttpResponse:
                return HttpResponse(status_code=200)

        class NonConformingExecutor:
            pass

        assert isinstance(ConformingExecutor(), RequestExecutorProtocol)
        assert not isinstance(NonConformingExecutor(), RequestExecutorProtocol)

    def test_assertion_engine_protocol(self) -> None:
        """Verify AssertionEngineProtocol can check conforming classes."""

        class ConformingEngine:
            def evaluate(self, _response: HttpResponse, _rules: list[dict]) -> dict:
                return {"passed": True}

        assert isinstance(ConformingEngine(), AssertionEngineProtocol)

    def test_variable_resolver_protocol(self) -> None:
        """Verify VariableResolverProtocol can check conforming classes."""

        class ConformingResolver:
            def resolve(self, template: str, _context: dict) -> str:
                return template

        assert isinstance(ConformingResolver(), VariableResolverProtocol)

    def test_extractor_protocol(self) -> None:
        """Verify ExtractorProtocol can check conforming classes."""

        class ConformingExtractor:
            def extract(self, _response: HttpResponse, _expression: str) -> str:
                return "extracted"

        assert isinstance(ConformingExtractor(), ExtractorProtocol)
