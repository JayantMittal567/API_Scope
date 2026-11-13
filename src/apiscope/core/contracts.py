"""Shared type contracts and architectural protocols for ApiScope core components.

Defines structural protocols for request execution, assertion evaluation,
variable resolution, and response extraction without premature concrete coupling.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from apiscope.core.models import HttpRequest, HttpResponse


@runtime_checkable
class RequestExecutorProtocol(Protocol):
    """Structural protocol for HTTP request execution components."""

    def execute(self, request: HttpRequest) -> HttpResponse:
        """Execute an HTTP request and return the observed response.

        Args:
            request: The validated HTTP request specification.

        Returns:
            The observed HTTP response domain model.
        """
        ...


@runtime_checkable
class AssertionEngineProtocol(Protocol):
    """Structural protocol for evaluating assertions against an HTTP response."""

    def evaluate(
        self,
        response: HttpResponse,
        rules: Sequence[dict[str, Any]],
    ) -> Any:
        """Evaluate declarative assertion rules against an HTTP response.

        Args:
            response: The observed HTTP response.
            rules: Sequence of declarative assertion definitions.

        Returns:
            Structured summary or dictionary containing assertion outcomes.
        """
        ...


@runtime_checkable
class VariableResolverProtocol(Protocol):
    """Structural protocol for resolving templated variables across test steps."""

    def resolve(self, template: str, context: Mapping[str, Any]) -> str:
        """Substitute variable expressions in a template string using context.

        Args:
            template: The string containing variable expressions.
            context: Key-value mapping of available variables.

        Returns:
            The string with resolved variable values.
        """
        ...


@runtime_checkable
class ExtractorProtocol(Protocol):
    """Structural protocol for extracting values from an HTTP response."""

    def extract(self, response: HttpResponse, expression: str) -> Any:
        """Extract a value from the HTTP response using a query expression.

        Args:
            response: The observed HTTP response.
            expression: Extraction locator (e.g., JSONPath, header name).

        Returns:
            The extracted value.
        """
        ...
