"""Domain models representing YAML test definitions and converted requests.

Provides structured representations of test cases, extraction rules, assertion
definitions, and conversion into executable HttpRequest domain models.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from apiscope.core.models import (
    ApiKeyAuth,
    AuthConfig,
    BasicAuth,
    BearerAuth,
    BodyType,
    HttpMethod,
    HttpRequest,
)
from apiscope.core.variables import VariableResolver
from apiscope.definitions.environment import Environment


class DefinitionSource(StrEnum):
    """Source origin of a test definition."""

    HANDWRITTEN = "handwritten"
    RULE_GENERATED = "rule-generated"
    AI_GENERATED = "ai-generated"

    @classmethod
    def from_string(cls, value: str) -> "DefinitionSource":
        """Parse and normalize source string."""
        normalized = value.strip().lower()
        try:
            return cls(normalized)
        except ValueError:
            options = ", ".join(s.value for s in cls)
            raise ValueError(f"Invalid source '{value}'. Allowed: {options}.") from None


class DefinitionStatus(StrEnum):
    """Lifecycle status of a test definition."""

    APPROVED = "approved"
    PROPOSED = "proposed"

    @classmethod
    def from_string(cls, value: str) -> "DefinitionStatus":
        """Parse and normalize status string."""
        normalized = value.strip().lower()
        try:
            return cls(normalized)
        except ValueError:
            options = ", ".join(st.value for st in cls)
            raise ValueError(f"Invalid status '{value}'. Allowed: {options}.") from None


@dataclass
class TestRequestDefinition:
    """Specification of an HTTP request inside a test definition."""

    __test__ = False

    method: HttpMethod | str
    url: str
    headers: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    body: Any | None = None
    body_type: BodyType | str = BodyType.JSON
    timeout: float | None = None
    max_retries: int | None = None
    follow_redirects: bool | None = None
    retry_non_idempotent: bool | None = None
    auth: AuthConfig | None = None


@dataclass
class TestDefinition:
    """Complete specification of an individual API test case."""

    __test__ = False

    name: str
    module: str
    request: TestRequestDefinition
    tags: list[str] = field(default_factory=list)
    extract: dict[str, str] = field(default_factory=dict)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    source: DefinitionSource = DefinitionSource.HANDWRITTEN
    status: DefinitionStatus = DefinitionStatus.APPROVED
    file_path: Path | None = None

    def to_http_request(
        self,
        environment: Environment | None = None,
        resolver: VariableResolver | None = None,
    ) -> HttpRequest:
        """Convert test definition and environment into an executable HttpRequest.

        Args:
            environment: Optional environment containing variable mappings.
            resolver: Optional custom VariableResolver (instantiates default if None).

        Returns:
            Fully resolved and validated HttpRequest model.
        """
        res = resolver or VariableResolver()
        context = environment.variables if environment is not None else {}

        resolved_url = res.resolve_string(self.request.url, context)
        resolved_headers = res.resolve_data(self.request.headers, context)
        resolved_params = res.resolve_data(self.request.params, context)
        resolved_body = (
            res.resolve_data(self.request.body, context)
            if self.request.body is not None
            else None
        )

        method = (
            HttpMethod.from_string(self.request.method)
            if isinstance(self.request.method, str)
            else self.request.method
        )

        body_type = (
            BodyType.from_string(self.request.body_type)
            if isinstance(self.request.body_type, str)
            else self.request.body_type
        )

        timeout = (
            float(self.request.timeout) if self.request.timeout is not None else 30.0
        )
        max_retries = (
            int(self.request.max_retries) if self.request.max_retries is not None else 0
        )
        follow_redirects = (
            bool(self.request.follow_redirects)
            if self.request.follow_redirects is not None
            else True
        )
        retry_non_idempotent = (
            bool(self.request.retry_non_idempotent)
            if self.request.retry_non_idempotent is not None
            else False
        )

        auth = self.request.auth
        if auth is not None and res is not None:
            if isinstance(auth, BearerAuth) and isinstance(auth.token, str):
                auth = BearerAuth(token=res.resolve(auth.token, context))
            elif isinstance(auth, ApiKeyAuth) and isinstance(auth.value, str):
                auth = ApiKeyAuth(
                    name=auth.name,
                    in_location=auth.in_location,
                    value=res.resolve(auth.value, context),
                )
            elif isinstance(auth, BasicAuth):
                u = (
                    res.resolve(auth.username, context)
                    if isinstance(auth.username, str)
                    else auth.username
                )
                p = (
                    res.resolve(auth.password, context)
                    if isinstance(auth.password, str)
                    else auth.password
                )
                auth = BasicAuth(username=u, password=p)

        return HttpRequest(
            method=method,
            url=resolved_url,
            headers=resolved_headers,
            params=resolved_params,
            body=resolved_body,
            body_type=body_type,
            timeout=timeout,
            max_retries=max_retries,
            follow_redirects=follow_redirects,
            retry_non_idempotent=retry_non_idempotent,
            auth=auth,
        )
