"""Execution Safety Policy, Mutation Fencing, and Dry-Run Planning.

Defines operational risk classifications (READ_ONLY, MUTATING, DESTRUCTIVE, UNKNOWN),
conservative execution policies, and pre-execution safety gates that prevent accidental
data mutations against unauthorized, production, or unsuitable target environments.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from apiscope.core.exceptions import ExecutionSafetyError

# Built-in endpoints and path patterns that produce side effects even via GET
RISKY_ENDPOINT_PATTERNS: tuple[str, ...] = (
    "/_test/reset",
    "/reset",
    "/restart",
    "/shutdown",
    "/clear",
    "/purge",
    "/truncate",
    "/drop",
    "/destroy",
    "/cleanup",
)

# Standard non-production environment names
DEFAULT_NON_PROD_ENVIRONMENTS: frozenset[str] = frozenset(
    {
        "local",
        "dev",
        "development",
        "test",
        "testing",
        "test_env",
        "integration-test",
        "staging",
        "sandbox",
        "sample_app",
        "qa",
        "ci",
    }
)

# Known production environment designations
PRODUCTION_ENVIRONMENTS: frozenset[str] = frozenset({"prod", "production", "live"})


class OperationRisk(StrEnum):
    """Risk classification for API operations."""

    READ_ONLY = "READ_ONLY"
    MUTATING = "MUTATING"
    DESTRUCTIVE = "DESTRUCTIVE"
    UNKNOWN = "UNKNOWN"


class ExecutionMode(StrEnum):
    """Enforcement mode for ExecutionSafetyPolicy."""

    READ_ONLY = "READ_ONLY"
    PERMIT_MUTATIONS = "PERMIT_MUTATIONS"
    PERMIT_ALL = "PERMIT_ALL"


@dataclass(frozen=True)
class PolicyEvaluationResult:
    """Outcome of an execution safety policy evaluation."""

    permitted: bool
    risk: OperationRisk
    reason: str | None = None


@dataclass(frozen=True)
class PlannedOperation:
    """Preview of a planned test operation in dry-run mode."""

    test_name: str
    module: str
    method: str
    url: str
    risk: OperationRisk
    status: str
    will_execute: bool
    block_reason: str | None = None
    missing_variables: list[str] = field(default_factory=list)
    masked_headers: dict[str, str] = field(default_factory=dict)


@dataclass
class DryRunReport:
    """Aggregated dry-run execution preview report."""

    suite_name: str
    environment_name: str | None
    total_operations: int
    read_only_count: int
    mutating_count: int
    destructive_count: int
    unknown_count: int
    approved_count: int
    proposed_count: int
    executable_count: int
    blocked_count: int
    operations: list[PlannedOperation]
    missing_variables: list[str] = field(default_factory=list)


class ExecutionSafetyPolicy:
    """Security policy controlling permissions for mutating and destructive requests.

    Prevents accidental data corruption by enforcing read-only operation by default,
    requiring explicit authorization for mutating/destructive requests, fencing
    execution away from production environments, and providing dry-run simulation
    capabilities.
    """

    def __init__(
        self,
        mode: ExecutionMode | str = ExecutionMode.READ_ONLY,
        allow_mutations: bool = False,
        allow_destructive: bool = False,
        allowed_environments: Sequence[str] | set[str] | frozenset[str] | None = None,
        blocked_environments: Sequence[str] | set[str] | frozenset[str] | None = None,
        endpoint_overrides: dict[str, OperationRisk | str] | None = None,
    ) -> None:
        """Initialize ExecutionSafetyPolicy.

        Args:
            mode: Safety mode (READ_ONLY, PERMIT_MUTATIONS, or PERMIT_ALL).
            allow_mutations: Whether mutating requests (POST, PUT, PATCH) are allowed.
            allow_destructive: Whether destructive requests (DELETE) are allowed.
            allowed_environments: Whitelist of designated non-prod environments.
            blocked_environments: Blacklist of production environments.
            endpoint_overrides: Explicit per-endpoint risk category overrides.
        """
        if isinstance(mode, str):
            mode = ExecutionMode(mode.upper())
        self.mode = mode
        self.allow_mutations = allow_mutations or (
            mode in (ExecutionMode.PERMIT_MUTATIONS, ExecutionMode.PERMIT_ALL)
        )
        self.allow_destructive = (
            allow_destructive
            or (mode == ExecutionMode.PERMIT_ALL)
            or self.allow_mutations
        )

        self.allowed_environments = frozenset(
            h.strip().lower()
            for h in (allowed_environments or DEFAULT_NON_PROD_ENVIRONMENTS)
        )
        self.blocked_environments = frozenset(
            h.strip().lower() for h in (blocked_environments or PRODUCTION_ENVIRONMENTS)
        )

        overrides: dict[str, OperationRisk] = {}
        if endpoint_overrides:
            for k, v in endpoint_overrides.items():
                if isinstance(v, str):
                    overrides[k.strip().lower()] = OperationRisk(v.upper())
                else:
                    overrides[k.strip().lower()] = v
        self.endpoint_overrides = overrides

    @classmethod
    def read_only(cls) -> ExecutionSafetyPolicy:
        """Create a strict default read-only execution safety policy."""
        return cls(mode=ExecutionMode.READ_ONLY, allow_mutations=False)

    @classmethod
    def permissive(cls) -> ExecutionSafetyPolicy:
        """Create an authorized policy allowing mutations in test environments."""
        return cls(
            mode=ExecutionMode.PERMIT_ALL,
            allow_mutations=True,
            allow_destructive=True,
        )

    def clone(self, **kwargs: Any) -> ExecutionSafetyPolicy:
        """Create a modified copy of this safety policy."""
        params: dict[str, Any] = {
            "mode": self.mode,
            "allow_mutations": self.allow_mutations,
            "allow_destructive": self.allow_destructive,
            "allowed_environments": self.allowed_environments,
            "blocked_environments": self.blocked_environments,
            "endpoint_overrides": self.endpoint_overrides,
        }
        params.update(kwargs)
        return ExecutionSafetyPolicy(**params)

    def classify_operation(
        self,
        method: str,
        url: str = "",
        tags: Sequence[str] | None = None,
    ) -> OperationRisk:
        """Classify the risk level of an HTTP operation.

        Args:
            method: HTTP method name.
            url: Target URL string.
            tags: Optional tags associated with the test definition.

        Returns:
            OperationRisk enum value (READ_ONLY, MUTATING, DESTRUCTIVE, or UNKNOWN).
        """
        # 1. Check explicit test tags
        if tags:
            for tag in tags:
                tag_lower = tag.strip().lower()
                if tag_lower in (
                    "risk:destructive",
                    "destructive",
                    "destructive-operation",
                ):
                    return OperationRisk.DESTRUCTIVE
                if tag_lower in (
                    "risk:mutating",
                    "mutating",
                    "mutation",
                ):
                    return OperationRisk.MUTATING
                if tag_lower in (
                    "risk:read_only",
                    "risk:readonly",
                    "read_only",
                    "readonly",
                ):
                    return OperationRisk.READ_ONLY

        # 2. Check explicit endpoint overrides
        if url:
            try:
                parsed = urllib.parse.urlsplit(url)
                path = (parsed.path or "").lower()
            except Exception:
                path = url.lower()

            for pattern, override_risk in self.endpoint_overrides.items():
                if pattern in path or path.startswith(pattern):
                    return override_risk

            # 3. Check built-in risky endpoint patterns (even on GET)
            for risky in RISKY_ENDPOINT_PATTERNS:
                if risky in path or path.endswith(risky):
                    return OperationRisk.DESTRUCTIVE

        # 4. Standard HTTP method classification
        norm_method = (method or "").strip().upper()
        if norm_method in ("GET", "HEAD", "OPTIONS"):
            return OperationRisk.READ_ONLY
        if norm_method in ("POST", "PUT", "PATCH"):
            return OperationRisk.MUTATING
        if norm_method == "DELETE":
            return OperationRisk.DESTRUCTIVE

        return OperationRisk.UNKNOWN

    def evaluate(
        self,
        test_def: Any = None,
        request: Any = None,
        environment: Any = None,
    ) -> PolicyEvaluationResult:
        """Evaluate whether an operation is permitted to execute under this policy.

        Args:
            test_def: Optional TestDefinition model.
            request: Optional HttpRequest or TestRequestDefinition model.
            environment: Optional Environment model.

        Returns:
            PolicyEvaluationResult containing permission decision and explanation.
        """
        # Extract method, url, tags
        method = ""
        url = ""
        tags: list[str] = []

        if test_def is not None:
            tags = list(getattr(test_def, "tags", []))
            req = getattr(test_def, "request", None)
            if req is not None:
                method = getattr(req, "method", "")
                url = getattr(req, "url", "")
        elif request is not None:
            method = getattr(request, "method", "")
            url = getattr(request, "url", "")

        if hasattr(method, "value"):
            method = method.value
        method_str = str(method).strip().upper()

        risk = self.classify_operation(method=method_str, url=url, tags=tags)

        # 1. READ_ONLY operations are permitted under all policy modes
        if risk == OperationRisk.READ_ONLY:
            return PolicyEvaluationResult(permitted=True, risk=risk)

        # 2. UNKNOWN operations are blocked conservatively
        if risk == OperationRisk.UNKNOWN:
            return PolicyEvaluationResult(
                permitted=False,
                risk=risk,
                reason=(
                    f"Blocked by execution safety policy: Operation "
                    f"'{method_str} {url}' has UNKNOWN risk classification "
                    "and is blocked under conservative policy."
                ),
            )

        # 3. Check for production environment fencing
        if environment is not None:
            env_name = getattr(environment, "name", "").strip().lower()
            is_prod = bool(getattr(environment, "is_production", False))
            if is_prod or env_name in self.blocked_environments:
                return PolicyEvaluationResult(
                    permitted=False,
                    risk=risk,
                    reason=(
                        f"Blocked by execution safety policy: Mutating operation "
                        f"'{method_str} {url}' cannot execute against designated "
                        f"production environment '{environment.name}'."
                    ),
                )

        # 4. Check for mutation authorization
        if risk == OperationRisk.DESTRUCTIVE and (
            not self.allow_destructive and not self.allow_mutations
        ):
            return PolicyEvaluationResult(
                permitted=False,
                risk=risk,
                reason=(
                    f"Blocked by execution safety policy: Operation "
                    f"'{method_str} {url}' is classified as DESTRUCTIVE, "
                    f"but policy is {self.mode.value} (authorize via "
                    "--allow-mutation in a designated non-production environment)."
                ),
            )

        if risk == OperationRisk.MUTATING and not self.allow_mutations:
            return PolicyEvaluationResult(
                permitted=False,
                risk=risk,
                reason=(
                    f"Blocked by execution safety policy: Operation "
                    f"'{method_str} {url}' is classified as MUTATING, "
                    f"but policy is {self.mode.value} (authorize via "
                    "--allow-mutation in a designated non-production environment)."
                ),
            )

        return PolicyEvaluationResult(permitted=True, risk=risk)

    def validate_request(
        self,
        request: Any,
        environment: Any = None,
    ) -> None:
        """Validate request against safety policy, raising ExecutionSafetyError.

        Args:
            request: HttpRequest or TestRequestDefinition instance.
            environment: Optional Environment instance.

        Raises:
            ExecutionSafetyError: If the request is mutating and not authorized.
        """
        result = self.evaluate(request=request, environment=environment)
        if not result.permitted:
            raise ExecutionSafetyError(result.reason or "Operation not permitted")
