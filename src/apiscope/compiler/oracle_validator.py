"""Strict test oracle validator for ApiScope Test Plan Compiler (Task 19).

Enforces that every test expectation is justified by an explicit OpenAPI contract,
verified business requirement, or approved specification. Classifies indefensible,
speculative, or unverified expectations as NEEDS_REVIEW.
"""

from __future__ import annotations

from apiscope.contract.models import CanonicalContract, CanonicalOperation
from apiscope.definitions.models import DefinitionStatus
from apiscope.planning.models import (
    OracleType,
    ScenarioCategory,
    ScenarioOrigin,
    TestScenario,
)


class OracleValidator:
    """Validates test oracles and defensibility of expected outcomes."""

    def __init__(self, contract: CanonicalContract) -> None:
        """Initialize validator with contract context."""
        self.contract = contract
        self._op_map: dict[str, CanonicalOperation] = {
            f"{op.method.upper()} {op.path}": op for op in contract.operations
        }

    def validate_scenario_oracles(
        self,
        scenario: TestScenario,
        include_proposed: bool = False,
    ) -> tuple[bool, list[str]]:
        """Evaluate whether all scenario steps possess defensible test oracles.

        Args:
            scenario: TestScenario to validate.
            include_proposed: Whether to allow proposed scenarios without approval.

        Returns:
            Tuple of (is_defensible, list_of_review_reasons).
        """
        review_reasons: list[str] = []

        # 1. AI proposals must be explicitly approved
        if (
            scenario.origin == ScenarioOrigin.AI_PROPOSED
            and scenario.approval_status != DefinitionStatus.APPROVED
            and not include_proposed
        ):
            review_reasons.append(
                "AI-proposed scenario requires explicit human review and approval."
            )

        # 2. Category-specific oracle defensibility checks
        if scenario.category == ScenarioCategory.AUTH_SECURITY:
            # Undocumented mutation probes or unverified security inferences
            review_reasons.append(
                "Security audit probe relies on unverified authentication inference "
                "rather than explicit contract specification."
            )

        elif scenario.category == ScenarioCategory.RESOURCE_EXISTENCE:
            # Probing non-existent ID 999999
            for step in scenario.steps:
                op = self._resolve_operation(step.method, step.path)
                if op is not None:
                    if "404" not in op.responses:
                        review_reasons.append(
                            f"HTTP 404 response is not declared in OpenAPI contract "
                            f"for {op.method} {op.path}."
                        )
                    else:
                        review_reasons.append(
                            "Probing synthetic identifier '999999' does not guarantee "
                            "HTTP 404 without a verified non-existence fixture."
                        )

        elif scenario.category == ScenarioCategory.ERROR_HANDLING:
            # Malformed JSON body
            for step in scenario.steps:
                op = self._resolve_operation(step.method, step.path)
                if op is not None and "400" not in op.responses:
                    review_reasons.append(
                        f"HTTP 400 Bad Request is not declared in contract for "
                        f"{op.method} {op.path}; server may return 415, 422, "
                        f"or default."
                    )

        elif scenario.category == ScenarioCategory.CONSISTENCY_CHECK:
            # Read-after-write consistency without verified specification
            review_reasons.append(
                "Immediate read-after-write consistency is not formally specified in "
                "contract and requires human review."
            )

        # 3. Step-level oracle checks
        for step in scenario.steps:
            sig = f"{step.method.upper()} {step.path}"
            op = self._resolve_operation(step.method, step.path)
            if op is None:
                continue  # Endpoint existence handled by compiler endpoint validation

            # AI Hypothesis oracle type
            if step.oracle_type == OracleType.AI_HYPOTHESIS:
                review_reasons.append(
                    f"Step {step.step_number} ({sig}) oracle type AI_HYPOTHESIS "
                    f"requires human verification before execution."
                )

            # Source code inference oracle type
            elif step.oracle_type == OracleType.SOURCE_CODE_INFERENCE:
                review_reasons.append(
                    f"Step {step.step_number} ({sig}) oracle type "
                    f"SOURCE_CODE_INFERENCE relies on implementation heuristics."
                )

            # Status code validation against contract responses
            if scenario.category == ScenarioCategory.CONTRACT_POSITIVE:
                has_matching_response = any(
                    code in op.responses for code in step.expected_status
                )
                if not has_matching_response:
                    review_reasons.append(
                        f"Step {step.step_number} ({sig}) expected status "
                        f"{step.expected_status} is not declared in contract responses."
                    )

            # Schema validation check
            elif scenario.category == ScenarioCategory.RESPONSE_SCHEMA:
                success_code = next(
                    (c for c in step.expected_status if c in op.responses),
                    None,
                )
                if success_code:
                    resp = op.responses[success_code]
                    if resp.is_empty_body or (not resp.schema and not resp.schema_ref):
                        review_reasons.append(
                            f"Step {step.step_number} ({sig}) schema requested, "
                            f"but no schema declared for HTTP {success_code}."
                        )

        is_defensible = len(review_reasons) == 0
        return is_defensible, review_reasons

    def _resolve_operation(self, method: str, path: str) -> CanonicalOperation | None:
        """Resolve CanonicalOperation matching method and path template."""
        clean_method = method.upper()
        # Direct match
        direct_sig = f"{clean_method} {path}"
        if direct_sig in self._op_map:
            return self._op_map[direct_sig]

        # Template match if synthetic ID was substituted (e.g. 999999 or 1)
        import re

        for sig, op in self._op_map.items():
            if not sig.startswith(clean_method + " "):
                continue
            pattern = re.sub(r"\{[^}]+\}", r"[^/]+", op.path)
            if re.fullmatch(f"^{pattern}$", path):
                return op

        return None
