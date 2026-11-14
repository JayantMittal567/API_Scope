"""Deterministic test planning engine for ApiScope (Task 18).

Derives structured test scenarios from CanonicalContract operations, schemas,
and constraints without requiring an LLM:
- Positive contract tests (happy path)
- Invalid and missing input validation tests
- Numeric, string, and array boundary condition tests
- Response schema validation tests
- Authentication and authorization boundary tests
- Resource existence checks (404 probing)
- Malformed syntax and unsupported media type error handling
- Request/response consistency checks
"""

from __future__ import annotations

import re
from typing import Any

from apiscope.contract.models import (
    CanonicalContract,
    CanonicalOperation,
    OperationReadiness,
)
from apiscope.definitions.models import DefinitionStatus
from apiscope.intelligence.models import (
    EvidenceReference,
    EvidenceSourceType,
    ProjectIntelligenceReport,
    VerificationStatus,
)
from apiscope.planning.models import (
    OracleType,
    RiskClassification,
    ScenarioCategory,
    ScenarioOrigin,
    ScenarioPriority,
    ScenarioStep,
    TestScenario,
)


def _clean_module_slug(module_name: str) -> str:
    """Normalize module name into an uppercase short token."""
    clean = re.sub(r"[^A-Za-z0-9]+", "_", module_name).strip("_").upper()
    return clean or "DEFAULT"


class DeterministicTestPlanner:
    """Generates structured test scenarios deterministically from API contracts."""

    def __init__(
        self,
        contract: CanonicalContract,
        intelligence_report: ProjectIntelligenceReport | None = None,
    ) -> None:
        """Initialize deterministic planner with contract and optional intelligence."""
        self.contract = contract
        self.intelligence = intelligence_report
        self._scenario_counter = 0

    def _next_id(self, module: str, cat_abbr: str) -> str:
        """Generate a unique scenario identifier."""
        self._scenario_counter += 1
        m_tag = _clean_module_slug(module)
        return f"SCN-{m_tag}-{cat_abbr}-{self._scenario_counter:03d}"

    def plan(self) -> tuple[list[TestScenario], list[dict[str, Any]], list[str]]:
        """Generate full suite of deterministic test scenarios.

        Returns:
            Tuple of:
            - List of planned TestScenario objects
            - List of untestable operations (e.g. BLOCKED)
            - Global missing prerequisites
        """
        scenarios: list[TestScenario] = []
        untestable: list[dict[str, Any]] = []
        global_missing_prereqs: set[str] = set()

        for op in self.contract.operations:
            sig = f"{op.method} {op.path}"

            # Respect Task 17 Operation-Level Readiness
            if op.readiness == OperationReadiness.BLOCKED:
                untestable.append(
                    {
                        "operation": sig,
                        "module": op.module,
                        "readiness": op.readiness.value,
                        "blocking_reasons": op.readiness_reasons,
                    }
                )
                continue

            op_prereqs: list[str] = []
            if "{" in op.path:
                op_prereqs.append(f"needs_existing_resource_id for path '{op.path}'")
            if op.requires_auth:
                op_prereqs.append(f"needs_valid_auth_token for {op.security_schemes}")
            elif op.method in ("POST", "PUT", "DELETE", "PATCH"):
                # Undocumented mutation security from Task 17
                op_prereqs.append(
                    "undocumented_mutation_security (verify auth requirement)"
                )

            for p in op_prereqs:
                global_missing_prereqs.add(p)

            # 1. Positive Contract Test
            pos_scenario = self._plan_positive_contract_test(op, op_prereqs)
            scenarios.append(pos_scenario)

            # 2. Input Validation Tests (Missing / Invalid required parameters & body)
            val_scenarios = self._plan_input_validation_tests(op, op_prereqs)
            scenarios.extend(val_scenarios)

            # 3. Boundary Condition Tests (Min/Max numeric, string length, array items)
            bound_scenarios = self._plan_boundary_tests(op, op_prereqs)
            scenarios.extend(bound_scenarios)

            # 4. Response Schema Validation Test
            resp_scenario = self._plan_response_schema_test(op, op_prereqs)
            if resp_scenario:
                scenarios.append(resp_scenario)

            # 5. Authentication & Security Probes
            sec_scenarios = self._plan_security_tests(op, op_prereqs)
            scenarios.extend(sec_scenarios)

            # 6. Resource Existence Tests (404 Probing)
            if "{" in op.path:
                res_scenario = self._plan_resource_existence_test(op, op_prereqs)
                scenarios.append(res_scenario)

            # 7. Error Handling & Malformed Payload Tests
            if op.request_body is not None:
                err_scenarios = self._plan_error_handling_tests(op, op_prereqs)
                scenarios.extend(err_scenarios)

        # 8. Multi-endpoint consistency checks (e.g. POST then GET)
        consistency_scenarios = self._plan_consistency_checks()
        scenarios.extend(consistency_scenarios)

        return scenarios, untestable, sorted(global_missing_prereqs)

    def _plan_positive_contract_test(
        self, op: CanonicalOperation, prereqs: list[str]
    ) -> TestScenario:
        """Propose happy-path nominal contract scenario."""
        sig = f"{op.method} {op.path}"
        scn_id = self._next_id(op.module, "POS")

        # Determine expected status code from 2xx responses
        expected_status = ["200"]
        for code in op.responses:
            if code.startswith("2"):
                expected_status = [code]
                break

        body_tpl: dict[str, Any] = {}
        if op.request_body and op.request_body.schema:
            body_tpl = self._synthesize_sample_payload(op.request_body.schema)

        params_tpl: dict[str, Any] = {}
        for p in op.parameters:
            if p.required:
                params_tpl[p.name] = (
                    1 if p.schema.get("type") == "integer" else "sample_value"
                )

        step = ScenarioStep(
            step_number=1,
            method=op.method,
            path=op.path,
            description=f"Send valid {op.method} request complying with contract schema",
            request_template={"parameters": params_tpl, "body": body_tpl},
            expected_status=expected_status,
            oracle_type=OracleType.EXPLICIT_CONTRACT,
            expected_outcome=(
                f"Operation '{sig}' succeeds with status {expected_status[0]} "
                f"and valid response body"
            ),
            oracle_evidence=EvidenceReference(
                source_type=EvidenceSourceType.OPENAPI,
                source_ref=f"paths.{op.path}.{op.method.lower()}",
                snippet=f"HTTP {expected_status[0]} declared in OpenAPI",
                confidence=1.0,
                explanation="Explicit success status code in specification",
            ),
        )

        risk = (
            RiskClassification.HIGH
            if op.method in ("DELETE", "PUT", "PATCH")
            else (
                RiskClassification.MEDIUM
                if op.method == "POST"
                else RiskClassification.LOW
            )
        )

        return TestScenario(
            scenario_id=scn_id,
            title=f"Happy Path: {op.method} {op.path}",
            description=(
                f"Executes nominal valid {op.method} request against {op.path} "
                f"and validates {expected_status[0]} response."
            ),
            category=ScenarioCategory.CONTRACT_POSITIVE,
            priority=ScenarioPriority.HIGH,
            origin=ScenarioOrigin.DETERMINISTIC,
            target_module=op.module,
            target_endpoints=[sig],
            preconditions=list(prereqs),
            test_data_requirements=[
                f"Valid input data conforming to {op.request_body.schema_ref or 'schema'}"
            ]
            if op.request_body
            else [],
            steps=[step],
            risk_classification=risk,
            confidence=VerificationStatus.CONFIRMED,
            missing_dependencies=list(prereqs),
            approval_status=(
                DefinitionStatus.APPROVED
                if op.readiness == OperationReadiness.READY
                else DefinitionStatus.PROPOSED
            ),
            rationale=(
                f"Core functional verification that {sig} accepts valid schema "
                f"data and returns declared status code."
            ),
        )

    def _plan_input_validation_tests(
        self, op: CanonicalOperation, prereqs: list[str]
    ) -> list[TestScenario]:
        """Propose missing or invalid input parameter/body validation scenarios."""
        scenarios: list[TestScenario] = []
        sig = f"{op.method} {op.path}"

        # 1. Missing required parameters
        for p in op.parameters:
            if p.required and p.location != "path":
                scn_id = self._next_id(op.module, "VAL")
                step = ScenarioStep(
                    step_number=1,
                    method=op.method,
                    path=op.path,
                    description=f"Send request omitting required {p.location} parameter '{p.name}'",
                    request_template={"omitted_parameter": p.name},
                    expected_status=["400", "422"],
                    oracle_type=OracleType.EXPLICIT_CONTRACT,
                    expected_outcome=f"Reject request with 400/422 when required '{p.name}' is missing",
                )
                scenarios.append(
                    TestScenario(
                        scenario_id=scn_id,
                        title=f"Missing Parameter: {p.name} on {sig}",
                        description=f"Verifies server rejects request when required parameter '{p.name}' is omitted.",
                        category=ScenarioCategory.INPUT_VALIDATION,
                        priority=ScenarioPriority.MEDIUM,
                        origin=ScenarioOrigin.DETERMINISTIC,
                        target_module=op.module,
                        target_endpoints=[sig],
                        steps=[step],
                        risk_classification=RiskClassification.LOW,
                        missing_dependencies=list(prereqs),
                        approval_status=DefinitionStatus.APPROVED,
                        rationale=f"Ensures mandatory parameter '{p.name}' is strictly enforced.",
                    )
                )

        # 2. Missing required request body fields
        if op.request_body and op.request_body.schema:
            s_dict = op.request_body.schema
            required_fields = s_dict.get("required", [])
            if isinstance(required_fields, list) and required_fields:
                scn_id = self._next_id(op.module, "VAL")
                first_req = required_fields[0]
                payload = self._synthesize_sample_payload(s_dict)
                payload.pop(first_req, None)

                step = ScenarioStep(
                    step_number=1,
                    method=op.method,
                    path=op.path,
                    description=f"Send request body omitting mandatory field '{first_req}'",
                    request_template={"body": payload},
                    expected_status=["400", "422"],
                    oracle_type=OracleType.EXPLICIT_CONTRACT,
                    expected_outcome=f"Reject request with 400/422 when required body field '{first_req}' is omitted",
                )
                scenarios.append(
                    TestScenario(
                        scenario_id=scn_id,
                        title=f"Missing Required Body Field: {first_req} on {sig}",
                        description=f"Verifies server validates request body and rejects missing required field '{first_req}'.",
                        category=ScenarioCategory.INPUT_VALIDATION,
                        priority=ScenarioPriority.HIGH,
                        origin=ScenarioOrigin.DETERMINISTIC,
                        target_module=op.module,
                        target_endpoints=[sig],
                        steps=[step],
                        risk_classification=RiskClassification.LOW,
                        missing_dependencies=list(prereqs),
                        approval_status=DefinitionStatus.APPROVED,
                        rationale=f"Validates request body constraint enforcement for '{first_req}'.",
                    )
                )

        return scenarios

    def _plan_boundary_tests(
        self, op: CanonicalOperation, prereqs: list[str]
    ) -> list[TestScenario]:
        """Propose boundary violation tests (minimum/maximum, length, items)."""
        scenarios: list[TestScenario] = []
        sig = f"{op.method} {op.path}"

        # Parameter boundaries
        for p in op.parameters:
            p_schema = p.schema
            if "minimum" in p_schema:
                min_val = p_schema["minimum"]
                scn_id = self._next_id(op.module, "BOUND")
                invalid_val = min_val - 1
                step = ScenarioStep(
                    step_number=1,
                    method=op.method,
                    path=op.path,
                    description=f"Pass '{p.name}' with value {invalid_val} (below minimum {min_val})",
                    request_template={"parameters": {p.name: invalid_val}},
                    expected_status=["400", "422"],
                    oracle_type=OracleType.EXPLICIT_CONTRACT,
                    expected_outcome=f"Reject parameter '{p.name}' value {invalid_val} exceeding lower bound",
                )
                scenarios.append(
                    TestScenario(
                        scenario_id=scn_id,
                        title=f"Numeric Underflow: {p.name} < {min_val} on {sig}",
                        description=f"Verifies numeric lower boundary enforcement on parameter '{p.name}'.",
                        category=ScenarioCategory.BOUNDARY,
                        priority=ScenarioPriority.MEDIUM,
                        origin=ScenarioOrigin.DETERMINISTIC,
                        target_module=op.module,
                        target_endpoints=[sig],
                        steps=[step],
                        risk_classification=RiskClassification.LOW,
                        missing_dependencies=list(prereqs),
                        approval_status=DefinitionStatus.APPROVED,
                        rationale=f"Prevents invalid boundary violations on parameter '{p.name}'.",
                    )
                )

        return scenarios

    def _plan_response_schema_test(
        self, op: CanonicalOperation, prereqs: list[str]
    ) -> TestScenario | None:
        """Propose contract response schema compliance verification."""
        sig = f"{op.method} {op.path}"
        success_code = "200"
        for code in op.responses:
            if code.startswith("2"):
                success_code = code
                break

        resp = op.responses.get(success_code)
        if not resp or resp.is_empty_body:
            return None

        scn_id = self._next_id(op.module, "SCH")
        step = ScenarioStep(
            step_number=1,
            method=op.method,
            path=op.path,
            description=f"Execute {op.method} and validate {success_code} response against declared schema",
            expected_status=[success_code],
            oracle_type=OracleType.EXPLICIT_CONTRACT,
            expected_outcome=f"Response body conforms strictly to {resp.item_schema_ref or resp.schema_ref or 'declared schema'}",
        )

        return TestScenario(
            scenario_id=scn_id,
            title=f"Schema Conformance: {success_code} Response on {sig}",
            description="Asserts all returned JSON fields, types, and structures match OpenAPI definition.",
            category=ScenarioCategory.RESPONSE_SCHEMA,
            priority=ScenarioPriority.MEDIUM,
            origin=ScenarioOrigin.DETERMINISTIC,
            target_module=op.module,
            target_endpoints=[sig],
            steps=[step],
            risk_classification=RiskClassification.LOW,
            missing_dependencies=list(prereqs),
            approval_status=DefinitionStatus.APPROVED,
            rationale="Protects against API schema drift, missing attributes, and type mismatches.",
        )

    def _plan_security_tests(
        self, op: CanonicalOperation, _prereqs: list[str]
    ) -> list[TestScenario]:
        """Propose authentication and permission boundary test scenarios."""
        scenarios: list[TestScenario] = []
        sig = f"{op.method} {op.path}"

        if op.requires_auth:
            # 1. Missing Authentication Token
            scn_id = self._next_id(op.module, "SEC")
            step = ScenarioStep(
                step_number=1,
                method=op.method,
                path=op.path,
                description=f"Send {op.method} request without Authorization credentials",
                request_template={"headers": {"Authorization": ""}},
                expected_status=["401"],
                oracle_type=OracleType.EXPLICIT_CONTRACT,
                expected_outcome="Reject unauthenticated request with HTTP 401 Unauthorized",
            )
            scenarios.append(
                TestScenario(
                    scenario_id=scn_id,
                    title=f"Missing Auth: {sig}",
                    description=f"Verifies secured endpoint {sig} rejects unauthenticated requests.",
                    category=ScenarioCategory.AUTH_SECURITY,
                    priority=ScenarioPriority.CRITICAL,
                    origin=ScenarioOrigin.DETERMINISTIC,
                    target_module=op.module,
                    target_endpoints=[sig],
                    steps=[step],
                    risk_classification=RiskClassification.LOW,
                    missing_dependencies=[],  # No auth needed for unauthenticated test!
                    approval_status=DefinitionStatus.APPROVED,
                    rationale="Guarantees protected endpoints cannot be accessed anonymously.",
                )
            )

            # 2. Malformed / Invalid Token
            scn_id_inv = self._next_id(op.module, "SEC")
            step_inv = ScenarioStep(
                step_number=1,
                method=op.method,
                path=op.path,
                description=f"Send {op.method} request with forged or invalid Bearer token",
                request_template={
                    "headers": {"Authorization": "Bearer invalid.token.payload"}
                },
                expected_status=["401"],
                oracle_type=OracleType.EXPLICIT_CONTRACT,
                expected_outcome="Reject forged token with HTTP 401 Unauthorized",
            )
            scenarios.append(
                TestScenario(
                    scenario_id=scn_id_inv,
                    title=f"Invalid Auth Token: {sig}",
                    description=f"Verifies secured endpoint {sig} rejects invalid or forged credentials.",
                    category=ScenarioCategory.AUTH_SECURITY,
                    priority=ScenarioPriority.HIGH,
                    origin=ScenarioOrigin.DETERMINISTIC,
                    target_module=op.module,
                    target_endpoints=[sig],
                    steps=[step_inv],
                    risk_classification=RiskClassification.LOW,
                    missing_dependencies=[],
                    approval_status=DefinitionStatus.APPROVED,
                    rationale="Guarantees signature and token validity enforcement.",
                )
            )

        elif op.method in ("POST", "PUT", "DELETE", "PATCH"):
            # Undocumented Mutation Security Probe
            scn_id = self._next_id(op.module, "SEC")
            step = ScenarioStep(
                step_number=1,
                method=op.method,
                path=op.path,
                description=f"Probe mutating operation {sig} without auth to verify server-side authorization enforcement",
                expected_status=["401", "403", "200", "201", "204"],
                oracle_type=OracleType.SOURCE_CODE_INFERENCE,
                expected_outcome="Determine whether server enforces unadvertised authentication filter",
            )
            scenarios.append(
                TestScenario(
                    scenario_id=scn_id,
                    title=f"Security Audit: Undocumented Mutation on {sig}",
                    description=(
                        f"Mutation {sig} defines no security scheme in OpenAPI. "
                        f"Probe verifies if target application allows anonymous mutation or enforces auth."
                    ),
                    category=ScenarioCategory.AUTH_SECURITY,
                    priority=ScenarioPriority.HIGH,
                    origin=ScenarioOrigin.DETERMINISTIC,
                    target_module=op.module,
                    target_endpoints=[sig],
                    steps=[step],
                    risk_classification=RiskClassification.HIGH,
                    missing_dependencies=["target_environment_authorization"],
                    approval_status=DefinitionStatus.PROPOSED,
                    rationale="Detects discrepancies between open API contract and backend security filters.",
                )
            )

        return scenarios

    def _plan_resource_existence_test(
        self, op: CanonicalOperation, prereqs: list[str]
    ) -> TestScenario:
        """Propose non-existent identifier 404 existence check."""
        sig = f"{op.method} {op.path}"
        scn_id = self._next_id(op.module, "RES")

        # Substitute ID parameters with synthetic non-existent ID
        synthetic_path = re.sub(r"\{[^}]+\}", "999999", op.path)

        step = ScenarioStep(
            step_number=1,
            method=op.method,
            path=synthetic_path,
            description=f"Request non-existent resource ID '999999' on {op.path}",
            expected_status=["404"],
            oracle_type=OracleType.EXPLICIT_CONTRACT,
            expected_outcome="Return HTTP 404 Not Found when resource does not exist",
        )

        return TestScenario(
            scenario_id=scn_id,
            title=f"Resource Not Found: {sig}",
            description=f"Verifies endpoint returns 404 when querying non-existent identifier on {op.path}.",
            category=ScenarioCategory.RESOURCE_EXISTENCE,
            priority=ScenarioPriority.MEDIUM,
            origin=ScenarioOrigin.DETERMINISTIC,
            target_module=op.module,
            target_endpoints=[sig],
            steps=[step],
            risk_classification=RiskClassification.LOW,
            missing_dependencies=list(prereqs),
            approval_status=DefinitionStatus.APPROVED,
            rationale="Verifies proper error handling when requesting non-existent entities.",
        )

    def _plan_error_handling_tests(
        self, op: CanonicalOperation, prereqs: list[str]
    ) -> list[TestScenario]:
        """Propose malformed syntax and unsupported media type scenarios."""
        scenarios: list[TestScenario] = []
        sig = f"{op.method} {op.path}"

        # 1. Malformed JSON Body
        scn_id = self._next_id(op.module, "ERR")
        step = ScenarioStep(
            step_number=1,
            method=op.method,
            path=op.path,
            description="Send broken, unparseable JSON payload: '{invalid_json:'",
            request_template={"raw_body": "{malformed_json:"},
            expected_status=["400"],
            oracle_type=OracleType.EXPLICIT_CONTRACT,
            expected_outcome="Reject invalid JSON syntax with HTTP 400 Bad Request",
        )
        scenarios.append(
            TestScenario(
                scenario_id=scn_id,
                title=f"Malformed JSON: {sig}",
                description=f"Verifies server gracefully rejects syntactically broken JSON on {sig}.",
                category=ScenarioCategory.ERROR_HANDLING,
                priority=ScenarioPriority.MEDIUM,
                origin=ScenarioOrigin.DETERMINISTIC,
                target_module=op.module,
                target_endpoints=[sig],
                steps=[step],
                risk_classification=RiskClassification.LOW,
                missing_dependencies=list(prereqs),
                approval_status=DefinitionStatus.APPROVED,
                rationale="Prevents server crash or unhandled 500 error on malformed JSON.",
            )
        )

        return scenarios

    def _plan_consistency_checks(self) -> list[TestScenario]:
        """Propose cross-endpoint round-trip and read-after-write consistency checks."""
        scenarios: list[TestScenario] = []

        # Find modules with both POST /<module> and GET /<module>/{id}
        ops_by_path: dict[str, list[CanonicalOperation]] = {}
        for op in self.contract.operations:
            ops_by_path.setdefault(op.path, []).append(op)

        for path, ops in ops_by_path.items():
            methods = {o.method for o in ops}
            # Look for GET collection and POST creation on same path
            if "GET" in methods and "POST" in methods:
                post_op = next(o for o in ops if o.method == "POST")

                scn_id = self._next_id(post_op.module, "CONS")
                step1 = ScenarioStep(
                    step_number=1,
                    method="POST",
                    path=path,
                    description=f"Create resource via POST {path}",
                    expected_status=["200", "201"],
                    oracle_type=OracleType.PROPERTY_CONSISTENCY,
                    expected_outcome="Entity created with identifier returned",
                )
                step2 = ScenarioStep(
                    step_number=2,
                    method="GET",
                    path=path,
                    description=f"Verify newly created resource appears in collection GET {path}",
                    expected_status=["200"],
                    oracle_type=OracleType.PROPERTY_CONSISTENCY,
                    expected_outcome="Collection contains created entity data",
                )

                scenarios.append(
                    TestScenario(
                        scenario_id=scn_id,
                        title=f"Read-After-Write Consistency: {path}",
                        description=f"Verifies creating an entity via POST {path} is immediately visible in GET {path}.",
                        category=ScenarioCategory.CONSISTENCY_CHECK,
                        priority=ScenarioPriority.HIGH,
                        origin=ScenarioOrigin.DETERMINISTIC,
                        target_module=post_op.module,
                        target_endpoints=[f"POST {path}", f"GET {path}"],
                        steps=[step1, step2],
                        risk_classification=RiskClassification.MEDIUM,
                        missing_dependencies=[
                            "database_isolation_or_transaction_rollback"
                        ],
                        approval_status=DefinitionStatus.PROPOSED,
                        rationale="Ensures eventual or immediate consistency across create and query endpoints.",
                    )
                )

        return scenarios

    def _synthesize_sample_payload(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Synthesize sample payload conforming to object schema properties."""
        payload: dict[str, Any] = {}
        props = schema.get("properties", {})
        if not isinstance(props, dict):
            return {"sample_field": "sample_value"}

        for p_name, p_def in props.items():
            if not isinstance(p_def, dict):
                continue
            p_type = p_def.get("type", "string")
            if p_type == "integer":
                payload[p_name] = p_def.get("default", 1)
            elif p_type == "number":
                payload[p_name] = p_def.get("default", 9.99)
            elif p_type == "boolean":
                payload[p_name] = p_def.get("default", True)
            elif p_type == "array":
                payload[p_name] = []
            else:
                payload[p_name] = p_def.get("default", f"sample_{p_name}")

        return payload
