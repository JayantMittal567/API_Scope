"""Core exception hierarchy for ApiScope.

Provides structured, domain-specific exceptions across execution, validation,
and configuration domains without leaking sensitive secrets.
"""


class ApiScopeError(Exception):
    """Base exception for all ApiScope domain and runtime errors."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ConfigurationError(ApiScopeError):
    """Raised when application or environment configuration is invalid."""


class InvalidRequestError(ApiScopeError):
    """Raised when an HTTP request model or specification is invalid."""


class UnsupportedMethodError(InvalidRequestError):
    """Raised when an unsupported HTTP method is requested."""


class RequestTimeoutError(ApiScopeError):
    """Raised when an HTTP request times out during execution."""


class NetworkExecutionError(ApiScopeError):
    """Raised when a network transport failure occurs during request execution."""


class DefinitionError(ApiScopeError):
    """Raised when a test definition or collection file is invalid or malformed."""


class PathTraversalError(DefinitionError):
    """Raised when a file reference attempts directory traversal outside the root."""


class VariableResolutionError(ApiScopeError):
    """Raised when a referenced variable cannot be resolved in the current context."""


class ExecutionSafetyError(ApiScopeError):
    """Raised when an operation violates execution safety policy."""
