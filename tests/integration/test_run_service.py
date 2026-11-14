"""Integration tests for M4 RunService component.

Tests end-to-end request chaining, secret protection (RAW at runtime,
MASKED in results), broken chain skip logic, and fault tolerance.
"""

import json
from datetime import UTC, datetime

import httpx
import pytest

from apiscope.core.exceptions import DefinitionError
from apiscope.core.executor import RequestExecutor, TargetAccessPolicy
from apiscope.core.results import CaseStatus
from apiscope.core.run_service import RunService
from apiscope.core.safety import ExecutionSafetyPolicy
from apiscope.definitions.collection import TestCollection, load_collection
from apiscope.definitions.environment import Environment
from apiscope.definitions.models import (
    TestDefinition,
    TestRequestDefinition,
)


def test_three_step_request_chain_passes_and_resolves_types() -> None:
    """Covers: FR-16, FR-18, NFR-06

    Verify that a 3-step chained suite loads from YAML, extracts variables
    across steps, preserves native types, and completes deterministically.
    """
    recorded_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        url_str = str(request.url)

        if url_str == "https://api.example.com/users" and request.method == "POST":
            return httpx.Response(
                201,
                json={"id": 8801, "token": "alice-jwt-token-999", "status": "ACTIVE"},
            )

        if url_str == "https://api.example.com/orders" and request.method == "POST":
            assert request.headers.get("authorization") == "Bearer alice-jwt-token-999"
            body = json.loads(request.content.decode("utf-8"))
            assert body["user_id"] == "8801" or body["user_id"] == 8801
            return httpx.Response(
                201,
                json={"order_id": "ord-5555", "confirmed": True},
            )

        if (
            url_str == "https://api.example.com/orders/ord-5555"
            and request.method == "GET"
        ):
            assert request.headers.get("authorization") == "Bearer alice-jwt-token-999"
            return httpx.Response(200, json={"status": "CONFIRMED"})

        return httpx.Response(404, json={"error": "not found"})

    executor = RequestExecutor(transport=httpx.MockTransport(mock_handler))

    fixed_time = datetime(2026, 11, 12, 12, 0, 0, tzinfo=UTC)
    service = RunService(
        executor=executor,
        clock=lambda: fixed_time,
        id_generator=lambda: "fixed-run-uuid-001",
        safety_policy=ExecutionSafetyPolicy.permissive(),
    )

    collection = load_collection("examples/m4_chaining_suite.yaml")
    run_result = service.run_collection(collection)

    assert run_result.run_id == "fixed-run-uuid-001"
    assert run_result.all_passed is True
    assert run_result.total_tests == 3
    assert run_result.passed_tests == 3
    assert len(recorded_requests) == 3

    # Step 1 asserts
    step1 = run_result.results[0]
    assert step1.test_name == "Create User"
    assert step1.status == CaseStatus.PASSED
    assert step1.extracted_variables["user_id"] == 8801
    assert step1.extracted_variables["auth_token"] == "***"  # Sensitive key masked

    # Step 2 asserts
    step2 = run_result.results[1]
    assert step2.test_name == "Create Order"
    assert step2.status == CaseStatus.PASSED
    assert step2.extracted_variables["order_id"] == "ord-5555"
    assert step2.request_headers["Authorization"] == "***"

    # Step 3 asserts
    step3 = run_result.results[2]
    assert step3.test_name == "Get Order"
    assert step3.status == CaseStatus.PASSED
    assert step3.status_code == 200


def test_secret_chaining_raw_at_runtime_and_masked_in_results() -> None:
    """Covers: FR-16, FR-18, NFR-03

    Verify that extracted secrets are sent RAW during HTTP execution,
    while all output results and serialized dictionaries mask them as '***'.
    """
    received_auth_header: list[str] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if url_str == "https://api.example.com/login":
            return httpx.Response(200, json={"token": "secret-jwt-999"})
        if url_str == "https://api.example.com/secure-data":
            auth = request.headers.get("authorization", "")
            received_auth_header.append(auth)
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404)

    test1 = TestDefinition(
        name="Login",
        module="auth",
        request=TestRequestDefinition(
            method="POST", url="https://api.example.com/login"
        ),
        extract={"token": "$.token"},
        assertions=[{"type": "status", "equals": 200}],
    )
    test2 = TestDefinition(
        name="Get Secure Data",
        module="data",
        request=TestRequestDefinition(
            method="GET",
            url="https://api.example.com/secure-data",
            headers={"Authorization": "Bearer {{token}}"},
        ),
        assertions=[{"type": "status", "equals": 200}],
    )

    collection = TestCollection(
        name="Secret Chain",
        module="auth",
        tests=[test1, test2],
    )

    executor = RequestExecutor(transport=httpx.MockTransport(mock_handler))
    service = RunService(
        executor=executor,
        safety_policy=ExecutionSafetyPolicy.permissive(),
    )

    run_result = service.run_collection(collection)

    # Side A: Sent RAW at runtime to target server
    assert len(received_auth_header) == 1
    assert received_auth_header[0] == "Bearer secret-jwt-999"

    # Side B: Masked in all stored and serialized result copies
    res1 = run_result.results[0]
    res2 = run_result.results[1]

    assert res1.extracted_variables["token"] == "***"
    assert res2.request_headers["Authorization"] == "***"

    serialized = json.dumps(run_result.to_dict())
    assert "secret-jwt-999" not in serialized
    assert "***" in serialized


def test_broken_chain_skips_dependent_step_but_runs_independent_step() -> None:
    """Covers: FR-16, FR-18, NFR-02

    Verify that when an upstream test fails, tests depending on its extracted
    variables are marked SKIPPED while independent tests still run.
    """

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if url_str == "https://api.example.com/create":
            # Upstream test fails
            return httpx.Response(500, json={"error": "Database error"})
        if url_str == "https://api.example.com/health":
            # Independent health check succeeds
            return httpx.Response(200, json={"status": "healthy"})
        return httpx.Response(404)

    step1 = TestDefinition(
        name="Create Item",
        module="items",
        request=TestRequestDefinition(
            method="POST", url="https://api.example.com/create"
        ),
        extract={"item_id": "$.id"},
        assertions=[{"type": "status", "equals": 201}],
    )
    step2 = TestDefinition(
        name="Use Item",
        module="items",
        request=TestRequestDefinition(
            method="GET", url="https://api.example.com/items/{{item_id}}"
        ),
        assertions=[{"type": "status", "equals": 200}],
    )
    step3 = TestDefinition(
        name="Health Check",
        module="system",
        request=TestRequestDefinition(
            method="GET", url="https://api.example.com/health"
        ),
        assertions=[{"type": "status", "equals": 200}],
    )

    collection = TestCollection(
        name="Chain Skip Suite", module="items", tests=[step1, step2, step3]
    )
    executor = RequestExecutor(transport=httpx.MockTransport(mock_handler))
    service = RunService(
        executor=executor,
        safety_policy=ExecutionSafetyPolicy.permissive(),
    )

    run_result = service.run_collection(collection)

    assert run_result.total_tests == 3
    assert run_result.passed_tests == 1
    assert run_result.failed_tests == 1
    assert run_result.skipped_tests == 1

    # Step 1 failed
    assert run_result.results[0].status == CaseStatus.FAILED

    # Step 2 skipped because upstream item_id failed
    assert run_result.results[1].status == CaseStatus.SKIPPED
    assert (
        "upstream chained variable '{{item_id}}' was not extracted"
        in run_result.results[1].skip_reason
    )

    # Step 3 independent step executed and passed
    assert run_result.results[2].status == CaseStatus.PASSED


def test_extraction_failure_marks_test_failed_and_skips_dependent_step() -> None:
    """Covers: FR-16, FR-18, NFR-02

    Verify that when an upstream HTTP request succeeds and assertions pass
    but extraction fails, the test is marked FAILED and dependents SKIPPED.
    """

    def mock_handler(_request: httpx.Request) -> httpx.Response:
        # Returns 200 matching assertion, but missing 'user_id' field in body
        return httpx.Response(200, json={"status": "created_without_id"})

    step1 = TestDefinition(
        name="Step 1",
        module="auth",
        request=TestRequestDefinition(
            method="POST", url="https://api.example.com/step1"
        ),
        extract={"user_id": "$.user_id"},
        assertions=[{"type": "status", "equals": 200}],
    )
    step2 = TestDefinition(
        name="Step 2",
        module="auth",
        request=TestRequestDefinition(
            method="GET", url="https://api.example.com/users/{{user_id}}"
        ),
        assertions=[{"type": "status", "equals": 200}],
    )

    collection = TestCollection(
        name="Extraction Fail Suite", module="auth", tests=[step1, step2]
    )
    executor = RequestExecutor(transport=httpx.MockTransport(mock_handler))
    service = RunService(
        executor=executor,
        safety_policy=ExecutionSafetyPolicy.permissive(),
    )

    run_result = service.run_collection(collection)

    assert run_result.results[0].status == CaseStatus.FAILED
    assert "matched no values in response" in run_result.results[0].error_message
    assert run_result.results[1].status == CaseStatus.SKIPPED
    assert (
        "upstream chained variable '{{user_id}}' was not extracted"
        in run_result.results[1].skip_reason
    )


def test_undefined_non_chained_variable_marks_test_error() -> None:
    """Covers: FR-18, NFR-02

    Verify that referencing a variable that was never part of any extraction rule
    records CaseStatus.ERROR without crashing suite execution.
    """
    test1 = TestDefinition(
        name="Bad Variable Test",
        module="misc",
        request=TestRequestDefinition(
            method="GET", url="https://api.example.com/items/{{unknown_var_123}}"
        ),
        assertions=[{"type": "status", "equals": 200}],
    )

    collection = TestCollection(name="Error Suite", module="misc", tests=[test1])
    service = RunService()

    run_result = service.run_collection(collection)

    assert run_result.total_tests == 1
    assert run_result.error_tests == 1
    assert run_result.results[0].status == CaseStatus.ERROR
    assert (
        "Missing required variable: 'unknown_var_123'"
        in run_result.results[0].error_message
    )


def test_network_timeout_and_ssrf_block_mark_test_error_without_crashing_run() -> None:
    """Covers: FR-18, NFR-02, NFR-04

    Verify that network transport timeouts and SSRF policy violations result in
    CaseStatus.ERROR while allowing remaining tests in the suite to execute.
    """

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "timeout" in url_str:
            raise httpx.ConnectTimeout("Connection timed out after 30s")
        return httpx.Response(200, json={"ok": True})

    test_timeout = TestDefinition(
        name="Timeout Test",
        module="net",
        request=TestRequestDefinition(
            method="GET", url="https://api.example.com/timeout"
        ),
        assertions=[{"type": "status", "equals": 200}],
    )
    test_ssrf = TestDefinition(
        name="SSRF Test",
        module="security",
        request=TestRequestDefinition(method="GET", url="http://192.168.1.1/admin"),
        assertions=[{"type": "status", "equals": 200}],
    )
    test_ok = TestDefinition(
        name="Healthy Test",
        module="net",
        request=TestRequestDefinition(method="GET", url="https://api.example.com/ok"),
        assertions=[{"type": "status", "equals": 200}],
    )

    collection = TestCollection(
        name="Resilience Suite",
        module="net",
        tests=[test_timeout, test_ssrf, test_ok],
    )

    policy = TargetAccessPolicy(allow_private_networks=False)
    executor = RequestExecutor(
        policy=policy, transport=httpx.MockTransport(mock_handler)
    )
    service = RunService(executor=executor)

    run_result = service.run_collection(collection)

    assert run_result.total_tests == 3
    assert run_result.error_tests == 2
    assert run_result.passed_tests == 1

    assert run_result.results[0].status == CaseStatus.ERROR
    assert "timed out" in run_result.results[0].error_message.lower()

    assert run_result.results[1].status == CaseStatus.ERROR
    assert "private" in run_result.results[1].error_message.lower()

    assert run_result.results[2].status == CaseStatus.PASSED


def test_data_driven_execution_with_csv_and_json_isolates_rows() -> None:
    """Covers: FR-17, FR-18

    Verify that data-driven test execution iterates across CSV and JSON rows,
    records row_index, and isolates variable context between rows.
    """
    seen_items: list[tuple[str, int, bool]] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        seen_items.append((body["sku"], body["qty"], body["exp"]))
        return httpx.Response(
            200, json={"status": "SAVED", "row_id": f"id-{body['sku']}"}
        )

    test_case = TestDefinition(
        name="Process Order Row",
        module="orders",
        request=TestRequestDefinition(
            method="POST",
            url="https://api.example.com/orders/batch",
            body={"sku": "{{item_sku}}", "qty": "{{quantity}}", "exp": "{{expedited}}"},
        ),
        extract={"extracted_id": "$.row_id"},
        assertions=[{"type": "status", "equals": 200}],
    )

    collection = TestCollection(name="Order Batch", module="orders", tests=[test_case])
    executor = RequestExecutor(transport=httpx.MockTransport(mock_handler))
    service = RunService(
        executor=executor,
        safety_policy=ExecutionSafetyPolicy.permissive(),
    )

    # 1. Run with examples/m4_orders_data.csv
    seen_items.clear()
    csv_run = service.run_collection(
        collection,
        data_file="examples/m4_orders_data.csv",
    )

    assert csv_run.total_tests == 3
    assert csv_run.all_passed is True
    assert [r.row_index for r in csv_run.results] == [0, 1, 2]
    assert seen_items == [
        ("0042", 3, True),
        ("007", 1, False),
        (999, 10, True),  # 999 without leading zero coerced to int in CSV
    ]

    # Verify extracted_id is row-specific
    assert csv_run.results[0].extracted_variables["extracted_id"] == "id-0042"
    assert csv_run.results[1].extracted_variables["extracted_id"] == "id-007"
    assert csv_run.results[2].extracted_variables["extracted_id"] == "id-999"

    # 2. Run with examples/m4_orders_data.json
    seen_items.clear()
    json_run = service.run_collection(
        collection,
        data_file="examples/m4_orders_data.json",
    )

    assert json_run.total_tests == 3
    assert json_run.all_passed is True
    assert [r.row_index for r in json_run.results] == [0, 1, 2]
    assert seen_items == [
        ("0042", 3, True),
        ("007", 1, False),
        ("999", 10, True),  # "999" is explicit string in JSON
    ]


def test_run_service_filters_by_module_and_tags() -> None:
    """Covers: FR-18

    Verify that RunService applies module filtering and tag filtering correctly
    before execution.
    """

    def mock_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    t1 = TestDefinition(
        name="User Auth",
        module="auth",
        tags=["smoke", "core"],
        request=TestRequestDefinition(method="GET", url="https://api.example.com/auth"),
        assertions=[{"type": "status", "equals": 200}],
    )
    t2 = TestDefinition(
        name="User Profile",
        module="users",
        tags=["core"],
        request=TestRequestDefinition(
            method="GET", url="https://api.example.com/users"
        ),
        assertions=[{"type": "status", "equals": 200}],
    )
    t3 = TestDefinition(
        name="Create Invoice",
        module="billing",
        tags=["slow", "finance"],
        request=TestRequestDefinition(
            method="GET", url="https://api.example.com/invoice"
        ),
        assertions=[{"type": "status", "equals": 200}],
    )

    collection = TestCollection(name="Filter Suite", module="all", tests=[t1, t2, t3])
    executor = RequestExecutor(transport=httpx.MockTransport(mock_handler))
    service = RunService(
        executor=executor,
        safety_policy=ExecutionSafetyPolicy.permissive(),
    )

    # Filter by module
    module_run = service.run_collection(collection, module="auth")
    assert module_run.total_tests == 1
    assert module_run.results[0].test_name == "User Auth"

    # Filter by tags
    tag_run = service.run_collection(collection, tags=["slow"])
    assert tag_run.total_tests == 1
    assert tag_run.results[0].test_name == "Create Invoice"

    # Both data_rows and data_file conflict
    with pytest.raises(DefinitionError, match="Specify either data_rows or data_file"):
        service.run_collection(collection, data_rows=[{"a": 1}], data_file="dummy.csv")


def test_run_duration_limit_skips_remaining_tests_when_exceeded() -> None:
    """Covers: FR-18, NFR-02

    Verify that when suite run duration exceeds max_run_duration_seconds,
    remaining tests are marked SKIPPED with an explanatory skip_reason.
    """
    call_count = 0
    start_time = datetime(2026, 11, 12, 10, 0, 0, tzinfo=UTC)
    expired_time = datetime(2026, 11, 12, 10, 5, 1, tzinfo=UTC)  # +301 seconds

    def fake_clock() -> datetime:
        nonlocal call_count
        call_count += 1
        # Start and first test at start_time, second test advances past 300s limit
        if call_count <= 2:
            return start_time
        return expired_time

    def mock_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    t1 = TestDefinition(
        name="Fast Test",
        module="core",
        request=TestRequestDefinition(method="GET", url="https://api.example.com/t1"),
        assertions=[{"type": "status", "equals": 200}],
    )
    t2 = TestDefinition(
        name="Timed Out Test",
        module="core",
        request=TestRequestDefinition(method="GET", url="https://api.example.com/t2"),
        assertions=[{"type": "status", "equals": 200}],
    )

    collection = TestCollection(name="Timeout Suite", module="core", tests=[t1, t2])
    executor = RequestExecutor(transport=httpx.MockTransport(mock_handler))
    service = RunService(
        executor=executor,
        clock=fake_clock,
        max_run_duration_seconds=300.0,
    )

    run_result = service.run_collection(collection)

    assert run_result.total_tests == 2
    assert run_result.passed_tests == 1
    assert run_result.skipped_tests == 1
    assert run_result.results[0].status == CaseStatus.PASSED
    assert run_result.results[1].status == CaseStatus.SKIPPED
    assert "Run duration exceeded maximum limit" in run_result.results[1].skip_reason


def test_run_service_with_environment_variables_and_secrets() -> None:
    """Covers: FR-18, NFR-03

    Verify that an explicit Environment with secret keys propagates into the run,
    resolves raw at runtime, and masks secrets in results and environment_name.
    """

    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-secret") == "my-top-secret"
        return httpx.Response(200, json={"message": "welcome"})

    env = Environment(
        name="staging",
        variables={
            "base_url": "https://api.example.com",
            "top_secret": "my-top-secret",
        },
        secrets={"top_secret"},
    )

    test = TestDefinition(
        name="Env Test",
        module="core",
        request=TestRequestDefinition(
            method="GET",
            url="{{base_url}}/items",
            headers={"x-secret": "{{top_secret}}"},
        ),
        assertions=[{"type": "status", "equals": 200}],
    )

    collection = TestCollection(name="Env Suite", module="core", tests=[test])
    executor = RequestExecutor(transport=httpx.MockTransport(mock_handler))
    service = RunService(
        executor=executor,
        safety_policy=ExecutionSafetyPolicy.permissive(),
    )

    run_result = service.run_collection(collection, environment=env)

    assert run_result.environment_name == "staging"
    assert run_result.all_passed is True
    res = run_result.results[0]
    assert res.request_headers["x-secret"] == "***"


def test_run_service_empty_assertions_fails_unverified() -> None:
    """Covers: FR-18, NFR-02

    Verify that a test case with zero assertions is marked CaseStatus.FAILED
    as unverified.
    """

    def mock_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    test = TestDefinition(
        name="No Assertions Test",
        module="core",
        request=TestRequestDefinition(
            method="GET", url="https://api.example.com/check"
        ),
        assertions=[],  # 0 assertions
    )

    collection = TestCollection(name="Unverified Suite", module="core", tests=[test])
    executor = RequestExecutor(transport=httpx.MockTransport(mock_handler))
    service = RunService(
        executor=executor,
        safety_policy=ExecutionSafetyPolicy.permissive(),
    )

    run_result = service.run_collection(collection)

    assert run_result.total_tests == 1
    assert run_result.failed_tests == 1
    assert run_result.results[0].status == CaseStatus.FAILED
    assert "unverified" in run_result.results[0].error_message.lower()


def test_run_service_nested_list_and_templated_dict_keys() -> None:
    """Covers: FR-16, FR-18

    Verify that variable discovery and resolution works through nested list bodies
    and templated dictionary keys.
    """
    captured_body: list[dict] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        captured_body.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"id": 100})

    test = TestDefinition(
        name="List and Dict Keys Test",
        module="core",
        request=TestRequestDefinition(
            method="POST",
            url="https://api.example.com/bulk",
            body={
                "{{prefix}}_key": "value",
                "items": ["{{item_1}}", "{{item_2}}"],
            },
        ),
        assertions=[{"type": "status", "equals": 200}],
    )

    collection = TestCollection(name="Complex Body Suite", module="core", tests=[test])
    executor = RequestExecutor(transport=httpx.MockTransport(mock_handler))
    service = RunService(
        executor=executor,
        safety_policy=ExecutionSafetyPolicy.permissive(),
    )

    run_result = service.run_collection(
        collection,
        data_rows=[{"prefix": "dynamic", "item_1": "apple", "item_2": "banana"}],
    )

    assert run_result.all_passed is True
    assert len(captured_body) == 1
    assert captured_body[0]["dynamic_key"] == "value"
    assert captured_body[0]["items"] == ["apple", "banana"]
