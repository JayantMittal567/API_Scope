"""Core Engine Layer for ApiScope.

Exposes domain models, exceptions, configuration management, logging facilities,
execution engines, variable resolution, response extraction, and orchestration
services for API test execution, health monitoring, and analysis.
"""

from apiscope.core.assertions import (
    AssertionEngine,
    AssertionResult,
    AssertionStatus,
    AssertionSummary,
    BaseAssertionEvaluator,
)
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
    DefinitionError,
    InvalidRequestError,
    NetworkExecutionError,
    PathTraversalError,
    RequestTimeoutError,
    UnsupportedMethodError,
    VariableResolutionError,
)
from apiscope.core.executor import RequestExecutor, TargetAccessPolicy
from apiscope.core.extractor import MAX_EXTRACT_BODY_BYTES, Extractor
from apiscope.core.logging_config import (
    configure_logging,
    get_logger,
    redact_sensitive_text,
)
from apiscope.core.models import (
    ApiKeyAuth,
    ApiKeyLocation,
    AuthConfig,
    AuthType,
    BasicAuth,
    BearerAuth,
    BodyType,
    HttpMethod,
    HttpRequest,
    HttpResponse,
)
from apiscope.core.results import (
    CaseResult,
    CaseStatus,
    RunResult,
    TestResult,
    TestStatus,
)
from apiscope.core.run_service import RunService
from apiscope.core.variables import VariableResolver

__all__ = [
    # Models
    "ApiKeyAuth",
    "ApiKeyLocation",
    "AuthConfig",
    "AuthType",
    "BasicAuth",
    "BearerAuth",
    "BodyType",
    "HttpMethod",
    "HttpRequest",
    "HttpResponse",
    # Results & Statuses
    "CaseResult",
    "CaseStatus",
    "RunResult",
    "TestResult",
    "TestStatus",
    # Assertions
    "AssertionEngine",
    "AssertionResult",
    "AssertionStatus",
    "AssertionSummary",
    "BaseAssertionEvaluator",
    # Exceptions
    "ApiScopeError",
    "ConfigurationError",
    "DefinitionError",
    "InvalidRequestError",
    "NetworkExecutionError",
    "PathTraversalError",
    "RequestTimeoutError",
    "UnsupportedMethodError",
    "VariableResolutionError",
    # Config
    "AppConfig",
    "get_config",
    "load_config",
    # Logging
    "configure_logging",
    "get_logger",
    "redact_sensitive_text",
    # Contracts
    "AssertionEngineProtocol",
    "ExtractorProtocol",
    "RequestExecutorProtocol",
    "VariableResolverProtocol",
    # Execution & Resolution
    "Extractor",
    "MAX_EXTRACT_BODY_BYTES",
    "RequestExecutor",
    "RunService",
    "TargetAccessPolicy",
    "VariableResolver",
]
