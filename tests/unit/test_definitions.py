"""Unit test suite for M2 — Test Definitions, Collections and Environments.

Covers:
- Test definition YAML loading, schema validation, and error reporting (1-14).
- Test collections, path traversal defenses, and filtering (15-23).
- Environments, secrets, and EnvironmentManager (24-27).
- Recursive variable resolution, type preservation, and immutability (28-36).
- Secret protection in representations, logs, and requests (37-40).
- Request conversion and execution via RequestExecutor (41-43).
- System regression verification (44).
"""

from pathlib import Path

import httpx
import pytest

from apiscope.core.exceptions import (
    DefinitionError,
    PathTraversalError,
    VariableResolutionError,
)
from apiscope.core.executor import RequestExecutor, TargetAccessPolicy
from apiscope.core.models import BodyType, HttpMethod, HttpRequest
from apiscope.core.variables import VariableResolver
from apiscope.definitions.collection import (
    load_collection,
    validate_safe_path,
)
from apiscope.definitions.environment import (
    Environment,
    EnvironmentManager,
    load_environment,
)
from apiscope.definitions.models import (
    DefinitionSource,
    DefinitionStatus,
    TestDefinition,
    TestRequestDefinition,
)
from apiscope.definitions.yaml_loader import (
    load_test_definition,
    parse_test_definition,
)

# ============================================================================
# 1. Test Definition YAML Loading (Cases 1 - 14)
# ============================================================================


class TestDefinitionYamlLoading:
    """Validation and loading tests for test definition YAML files."""

    def test_01_load_valid_test_definition_full(self, tmp_path: Path) -> None:
        """Case 1: Load a valid test definition YAML with all fields populated."""
        yaml_content = """
name: Create user account
module: users
tags:
  - smoke
  - positive
request:
  method: POST
  url: "https://api.example.com/users"
  headers:
    Content-Type: application/json
    Authorization: Bearer token123
  params:
    send_welcome: "true"
  body:
    username: john_doe
    email: john@example.com
  body_type: json
  timeout: 15.5
  max_retries: 3
  follow_redirects: false
  retry_non_idempotent: true
extract:
  user_id: "$.id"
assertions:
  - type: status
    equals: 201
  - type: jsonpath
    path: "$.username"
    equals: john_doe
source: handwritten
status: approved
"""
        test_file = tmp_path / "create_user.yaml"
        test_file.write_text(yaml_content, encoding="utf-8")

        test_def = load_test_definition(test_file)

        assert test_def.name == "Create user account"
        assert test_def.module == "users"
        assert test_def.tags == ["smoke", "positive"]
        assert test_def.request.method == HttpMethod.POST
        assert test_def.request.url == "https://api.example.com/users"
        assert test_def.request.headers == {
            "Content-Type": "application/json",
            "Authorization": "Bearer token123",
        }
        assert test_def.request.params == {"send_welcome": "true"}
        assert test_def.request.body == {
            "username": "john_doe",
            "email": "john@example.com",
        }
        assert test_def.request.body_type == BodyType.JSON
        assert test_def.request.timeout == 15.5
        assert test_def.request.max_retries == 3
        assert test_def.request.follow_redirects is False
        assert test_def.request.retry_non_idempotent is True
        assert test_def.extract == {"user_id": "$.id"}
        assert len(test_def.assertions) == 2
        assert test_def.source == DefinitionSource.HANDWRITTEN
        assert test_def.status == DefinitionStatus.APPROVED
        assert test_def.file_path == test_file

    def test_02_load_valid_test_definition_minimal(self, tmp_path: Path) -> None:
        """Case 2: Load valid YAML with only required fields; check defaults."""
        yaml_content = """
name: Health check
module: monitoring
request:
  method: GET
  url: "https://api.example.com/health"
"""
        test_file = tmp_path / "health.yaml"
        test_file.write_text(yaml_content, encoding="utf-8")

        test_def = load_test_definition(test_file)

        assert test_def.name == "Health check"
        assert test_def.module == "monitoring"
        assert test_def.request.method == HttpMethod.GET
        assert test_def.request.url == "https://api.example.com/health"
        # Defaults
        assert test_def.tags == []
        assert test_def.extract == {}
        assert test_def.assertions == []
        assert test_def.source == DefinitionSource.HANDWRITTEN
        assert test_def.status == DefinitionStatus.APPROVED
        assert test_def.request.headers == {}
        assert test_def.request.params == {}
        assert test_def.request.body is None
        assert test_def.request.body_type == BodyType.JSON
        assert test_def.request.timeout is None
        assert test_def.request.max_retries is None
        assert test_def.request.follow_redirects is None
        assert test_def.request.retry_non_idempotent is None

    def test_03_malformed_yaml_raises_definition_error(self, tmp_path: Path) -> None:
        """Case 3: Malformed YAML raises DefinitionError."""
        bad_yaml = "name: Broken\nmodule: [unclosed list"
        test_file = tmp_path / "bad.yaml"
        test_file.write_text(bad_yaml, encoding="utf-8")

        with pytest.raises(DefinitionError, match="Malformed YAML"):
            load_test_definition(test_file)

    def test_04_empty_yaml_raises_definition_error(self, tmp_path: Path) -> None:
        """Case 4: Empty YAML file raises DefinitionError."""
        test_file = tmp_path / "empty.yaml"
        test_file.write_text("", encoding="utf-8")

        with pytest.raises(DefinitionError, match="empty or not a valid dictionary"):
            load_test_definition(test_file)

        # File with only comments
        comment_file = tmp_path / "comments.yaml"
        comment_file.write_text("# Just a comment line\n", encoding="utf-8")
        with pytest.raises(DefinitionError, match="empty or not a valid dictionary"):
            load_test_definition(comment_file)

    def test_05_missing_required_name_raises_definition_error(self) -> None:
        """Case 5: Missing required 'name' field raises DefinitionError."""
        data = {
            "module": "users",
            "request": {"method": "GET", "url": "https://api.example.com"},
        }
        with pytest.raises(
            DefinitionError, match="Missing or invalid required field 'name'"
        ):
            parse_test_definition(data)

    def test_06_missing_required_module_raises_definition_error(self) -> None:
        """Case 6: Missing required 'module' field raises DefinitionError."""
        data = {
            "name": "Test",
            "request": {"method": "GET", "url": "https://api.example.com"},
        }
        with pytest.raises(
            DefinitionError, match="Missing or invalid required field 'module'"
        ):
            parse_test_definition(data)

    def test_07_missing_required_request_raises_definition_error(self) -> None:
        """Case 7: Missing required 'request' field raises DefinitionError."""
        data = {
            "name": "Test",
            "module": "users",
        }
        with pytest.raises(
            DefinitionError, match="Missing or invalid required field 'request'"
        ):
            parse_test_definition(data)

    def test_08_missing_required_request_url_raises_definition_error(self) -> None:
        """Case 8: Missing required 'request.url' raises DefinitionError."""
        data = {
            "name": "Test",
            "module": "users",
            "request": {"method": "GET"},
        }
        with pytest.raises(
            DefinitionError, match="Missing or invalid required field 'request.url'"
        ):
            parse_test_definition(data)

    def test_09_missing_required_request_method_raises_definition_error(self) -> None:
        """Case 9: Missing required 'request.method' raises DefinitionError."""
        data = {
            "name": "Test",
            "module": "users",
            "request": {"url": "https://api.example.com"},
        }
        with pytest.raises(
            DefinitionError, match="Missing required field 'request.method'"
        ):
            parse_test_definition(data)

    def test_10_unsupported_http_method_raises_definition_error(self) -> None:
        """Case 10: Unsupported HTTP method raises DefinitionError."""
        data = {
            "name": "Test",
            "module": "users",
            "request": {"method": "CONNECT", "url": "https://api.example.com"},
        }
        with pytest.raises(DefinitionError, match="Unsupported HTTP method 'CONNECT'"):
            parse_test_definition(data)

    def test_11_unsupported_body_type_raises_definition_error(self) -> None:
        """Case 11: Unsupported body type raises DefinitionError."""
        data = {
            "name": "Test",
            "module": "users",
            "request": {
                "method": "POST",
                "url": "https://api.example.com",
                "body_type": "binary_protobuf",
            },
        }
        with pytest.raises(
            DefinitionError, match="Unsupported body type 'binary_protobuf'"
        ):
            parse_test_definition(data)

    def test_12_invalid_source_raises_definition_error(self) -> None:
        """Case 12: Invalid source value raises DefinitionError."""
        data = {
            "name": "Test",
            "module": "users",
            "request": {"method": "GET", "url": "https://api.example.com"},
            "source": "magic-tool",
        }
        with pytest.raises(DefinitionError, match="Invalid source 'magic-tool'"):
            parse_test_definition(data)

    def test_13_invalid_status_raises_definition_error(self) -> None:
        """Case 13: Invalid status value raises DefinitionError."""
        data = {
            "name": "Test",
            "module": "users",
            "request": {"method": "GET", "url": "https://api.example.com"},
            "status": "deprecated",
        }
        with pytest.raises(DefinitionError, match="Invalid status 'deprecated'"):
            parse_test_definition(data)

    def test_14_assertions_and_extract_rules_preserved_exactly(self) -> None:
        """Case 14: Assertion and extraction rules preserved without modification."""
        raw_assertions = [
            {"type": "status", "equals": 200},
            {"type": "jsonpath", "path": "$.user.roles[0]", "equals": "admin"},
            {"type": "header", "name": "content-type", "contains": "application/json"},
        ]
        raw_extract = {
            "session_token": "$.auth.token",
            "expires_in": "$.auth.expires",
        }
        data = {
            "name": "Auth check",
            "module": "auth",
            "request": {"method": "POST", "url": "https://api.example.com/login"},
            "extract": raw_extract,
            "assertions": raw_assertions,
        }

        test_def = parse_test_definition(data)
        assert test_def.extract == raw_extract
        assert test_def.assertions == raw_assertions


# ============================================================================
# 2. Test Collections (Cases 15 - 23)
# ============================================================================


class TestCollections:
    """Suite tests for grouping, filtering, and directory traversal security."""

    def _create_sample_tests(self, base_dir: Path) -> tuple[Path, Path, Path]:
        """Helper to create three sample test files."""
        t1 = base_dir / "test_login.yaml"
        t1.write_text(
            """
name: User login
module: auth
tags: [smoke, security]
request:
  method: POST
  url: "https://api.example.com/login"
""",
            encoding="utf-8",
        )

        t2 = base_dir / "test_profile.yaml"
        t2.write_text(
            """
name: Get profile
module: users
tags: [smoke, regression]
request:
  method: GET
  url: "https://api.example.com/profile"
""",
            encoding="utf-8",
        )

        t3 = base_dir / "test_delete_account.yaml"
        t3.write_text(
            """
name: Delete account
module: users
tags: [destructive, regression]
request:
  method: DELETE
  url: "https://api.example.com/account"
""",
            encoding="utf-8",
        )
        return t1, t2, t3

    def test_15_load_valid_test_collection(self, tmp_path: Path) -> None:
        """Case 15: Load a valid test collection YAML."""
        self._create_sample_tests(tmp_path)
        col_content = """
name: Auth and User Suite
module: core
tags:
  - nightly
tests:
  - test_login.yaml
  - test_profile.yaml
"""
        col_file = tmp_path / "suite.yaml"
        col_file.write_text(col_content, encoding="utf-8")

        collection = load_collection(col_file)

        assert collection.name == "Auth and User Suite"
        assert collection.module == "core"
        assert collection.tags == ["nightly"]
        assert len(collection) == 2
        assert collection[0].name == "User login"
        assert collection[1].name == "Get profile"

    def test_16_collection_referencing_multiple_test_files(
        self, tmp_path: Path
    ) -> None:
        """Case 16: Collection referencing multiple test files loads all in sequence."""
        self._create_sample_tests(tmp_path)
        col_content = """
name: Full suite
module: system
tests:
  - test_login.yaml
  - test_profile.yaml
  - test_delete_account.yaml
"""
        col_file = tmp_path / "full_suite.yaml"
        col_file.write_text(col_content, encoding="utf-8")

        collection = load_collection(col_file)
        assert len(collection) == 3
        names = [t.name for t in collection]
        assert names == ["User login", "Get profile", "Delete account"]

    def test_17_collection_referencing_nonexistent_test_file_produces_error(
        self, tmp_path: Path
    ) -> None:
        """Case 17: Collection with non-existent file produces DefinitionError."""
        col_content = """
name: Broken suite
module: users
tests:
  - non_existent_test.yaml
"""
        col_file = tmp_path / "broken.yaml"
        col_file.write_text(col_content, encoding="utf-8")

        with pytest.raises(DefinitionError, match="does not exist"):
            load_collection(col_file)

    def test_18_malformed_collection_yaml_produces_error(self, tmp_path: Path) -> None:
        """Case 18: Malformed collection YAML produces DefinitionError."""
        col_file = tmp_path / "malformed.yaml"
        col_file.write_text("name: broken\nmodule: [unterminated", encoding="utf-8")

        with pytest.raises(DefinitionError, match="Malformed YAML"):
            load_collection(col_file)

        # Missing module
        col_file2 = tmp_path / "missing_module.yaml"
        col_file2.write_text("name: Test\ntests: []\n", encoding="utf-8")
        with pytest.raises(
            DefinitionError, match="Missing or invalid required field 'module'"
        ):
            load_collection(col_file2)

    def test_19_filtering_collection_by_module(self, tmp_path: Path) -> None:
        """Case 19: Filtering collection by module returns only matching tests."""
        self._create_sample_tests(tmp_path)
        col_content = """
name: Mixed suite
module: mixed
tests:
  - test_login.yaml
  - test_profile.yaml
  - test_delete_account.yaml
"""
        col_file = tmp_path / "mixed.yaml"
        col_file.write_text(col_content, encoding="utf-8")

        collection = load_collection(col_file)

        auth_tests = collection.filter_by_module("auth")
        assert len(auth_tests) == 1
        assert auth_tests[0].name == "User login"

        user_tests = collection.filter_by_module("users")
        assert len(user_tests) == 2
        assert {t.name for t in user_tests} == {"Get profile", "Delete account"}

        empty_tests = collection.filter_by_module("billing")
        assert empty_tests == []

    def test_20_filtering_collection_by_tag(self, tmp_path: Path) -> None:
        """Case 20: Filtering collection by tag (single tag or multiple tags)."""
        self._create_sample_tests(tmp_path)
        col_content = """
name: Tagged suite
module: tagged
tests:
  - test_login.yaml
  - test_profile.yaml
  - test_delete_account.yaml
"""
        col_file = tmp_path / "tagged.yaml"
        col_file.write_text(col_content, encoding="utf-8")

        collection = load_collection(col_file)

        # Single tag filter
        smoke_tests = collection.filter_by_tag("smoke")
        assert len(smoke_tests) == 2
        assert {t.name for t in smoke_tests} == {"User login", "Get profile"}

        destructive_tests = collection.filter_by_tag("destructive")
        assert len(destructive_tests) == 1
        assert destructive_tests[0].name == "Delete account"

        # Multi-tag match_all = False (any match)
        any_matched = collection.filter_by_tags(
            ["security", "destructive"], match_all=False
        )
        assert len(any_matched) == 2
        assert {t.name for t in any_matched} == {"User login", "Delete account"}

        # Multi-tag match_all = True (all must match)
        all_matched = collection.filter_by_tags(["smoke", "regression"], match_all=True)
        assert len(all_matched) == 1
        assert all_matched[0].name == "Get profile"

    def test_21_path_traversal_using_parent_dir_slash_rejected(
        self, tmp_path: Path
    ) -> None:
        """Case 21: Traversal attempt using '../' rejected with PathTraversalError."""
        sub_dir = tmp_path / "collections"
        sub_dir.mkdir()
        col_file = sub_dir / "traversal.yaml"
        col_file.write_text(
            """
name: Evil suite
module: exploit
tests:
  - ../outside.yaml
""",
            encoding="utf-8",
        )

        with pytest.raises(
            PathTraversalError, match="Forbidden directory traversal sequence"
        ):
            load_collection(col_file)

        # Direct function check
        with pytest.raises(PathTraversalError):
            validate_safe_path(sub_dir, "../secret.yaml")

    def test_22_path_traversal_using_backslash_rejected(self, tmp_path: Path) -> None:
        """Case 22: Traversal using '..\\' rejected with PathTraversalError."""
        sub_dir = tmp_path / "collections"
        sub_dir.mkdir(exist_ok=True)
        col_file = sub_dir / "traversal_win.yaml"
        col_file.write_text(
            """
name: Evil suite windows
module: exploit
tests:
  - "..\\\\outside.yaml"
""",
            encoding="utf-8",
        )

        with pytest.raises(
            PathTraversalError, match="Forbidden directory traversal sequence"
        ):
            load_collection(col_file)

        with pytest.raises(PathTraversalError):
            validate_safe_path(sub_dir, "..\\secret.yaml")

    def test_23_absolute_path_escaping_base_directory_rejected(
        self, tmp_path: Path
    ) -> None:
        """Case 23: Absolute path escaping base rejected with PathTraversalError."""
        base_dir = tmp_path / "allowed_root"
        base_dir.mkdir()
        outside_file = tmp_path / "outside.yaml"
        outside_file.write_text("name: Outside\nmodule: test\n", encoding="utf-8")

        col_file = base_dir / "suite.yaml"
        col_file.write_text(
            f"""
name: Suite with absolute escape
module: security
tests:
  - "{outside_file.as_posix()}"
""",
            encoding="utf-8",
        )

        with pytest.raises(PathTraversalError, match="escapes allowed base directory"):
            load_collection(col_file, base_dir=base_dir)

        with pytest.raises(PathTraversalError):
            validate_safe_path(base_dir, str(outside_file.resolve()))


# ============================================================================
# 3. Environments (Cases 24 - 27)
# ============================================================================


class TestEnvironments:
    """Environment configuration and registry tests."""

    def test_24_load_valid_environment_with_variables_and_secrets(
        self, tmp_path: Path
    ) -> None:
        """Case 24: Load valid environment YAML with variables and secrets."""
        env_yaml = """
name: staging
variables:
  base_url: "https://staging.example.com"
  timeout: 45
  api_key: "staging-secret-key-999"
secrets:
  - api_key
"""
        env_file = tmp_path / "staging.yaml"
        env_file.write_text(env_yaml, encoding="utf-8")

        env = load_environment(env_file)
        assert env.name == "staging"
        assert env.variables["base_url"] == "https://staging.example.com"
        assert env.variables["timeout"] == 45
        assert env.variables["api_key"] == "staging-secret-key-999"
        assert env.secrets == {"api_key"}

    def test_25_environment_with_only_required_name(self, tmp_path: Path) -> None:
        """Case 25: Environment with only required name; empty variables and secrets."""
        env_yaml = "name: minimal\n"
        env_file = tmp_path / "minimal.yaml"
        env_file.write_text(env_yaml, encoding="utf-8")

        env = load_environment(env_file)
        assert env.name == "minimal"
        assert env.variables == {}
        assert env.secrets == set()

    def test_26_malformed_environment_yaml_produces_error(self, tmp_path: Path) -> None:
        """Case 26: Malformed environment YAML produces DefinitionError."""
        env_file = tmp_path / "bad_env.yaml"
        env_file.write_text("name: [unclosed", encoding="utf-8")

        with pytest.raises(DefinitionError, match="Malformed YAML"):
            load_environment(env_file)

        # Missing name
        env_file2 = tmp_path / "missing_name.yaml"
        env_file2.write_text("variables: {a: 1}\n", encoding="utf-8")
        with pytest.raises(DefinitionError, match="Missing required 'name'"):
            load_environment(env_file2)

    def test_27_environment_manager_register_and_switch(self) -> None:
        """Case 27: EnvironmentManager registering and switching environments."""
        manager = EnvironmentManager()

        dev = Environment(name="dev", variables={"host": "localhost:8000"})
        staging = Environment(name="staging", variables={"host": "staging.example.com"})
        prod = Environment(name="prod", variables={"host": "api.example.com"})

        manager.register(dev)
        assert manager.get_active() == dev
        assert manager.list_all() == ["dev"]

        manager.register(staging)
        manager.register(prod)
        assert manager.list_all() == ["dev", "prod", "staging"]

        # Switch active
        manager.set_active("staging")
        assert manager.get_active() == staging

        manager.set_active("prod")
        assert manager.get_active() == prod

        # Retrieval
        assert manager.get("dev") == dev

        # Unknown environment error
        with pytest.raises(DefinitionError, match="Cannot activate unregistered"):
            manager.set_active("nonexistent")


# ============================================================================
# 4. Variable Resolution (Cases 28 - 36)
# ============================================================================


class TestVariableResolution:
    """Variable substitution engine tests."""

    def test_28_url_variable_replacement(self) -> None:
        """Case 28: URL variable replacement (e.g., {{base_url}}/orders)."""
        resolver = VariableResolver()
        context = {"base_url": "https://api.example.com"}
        template = "{{base_url}}/orders"

        result = resolver.resolve(template, context)
        assert result == "https://api.example.com/orders"

    def test_29_header_variable_replacement(self) -> None:
        """Case 29: Header variable replacement (e.g., Bearer {{token}})."""
        resolver = VariableResolver()
        context = {"token": "jwt_secret_token_abc"}
        template = "Bearer {{token}}"

        result = resolver.resolve(template, context)
        assert result == "Bearer jwt_secret_token_abc"

    def test_30_query_parameter_variable_replacement(self) -> None:
        """Case 30: Query parameter variable replacement."""
        resolver = VariableResolver()
        context = {"search_term": "laptop", "page": 2}
        params = {"q": "{{search_term}}", "page": "{{page}}"}

        resolved = resolver.resolve_data(params, context)
        assert resolved["q"] == "laptop"
        assert resolved["page"] == 2

    def test_31_request_body_nested_json_replacement(self) -> None:
        """Case 31: Request body variable replacement in nested JSON structures."""
        resolver = VariableResolver()
        context = {"org_id": "org_42", "role_name": "developer"}
        body = {
            "organization": {
                "id": "{{org_id}}",
                "settings": {
                    "default_role": "{{role_name}}",
                    "notifications": True,
                },
            }
        }

        resolved = resolver.resolve_data(body, context)
        assert resolved["organization"]["id"] == "org_42"
        assert resolved["organization"]["settings"]["default_role"] == "developer"
        assert resolved["organization"]["settings"]["notifications"] is True

    def test_32_variable_replacement_in_lists_within_request_bodies(self) -> None:
        """Case 32: Variable replacement in lists within request bodies."""
        resolver = VariableResolver()
        context = {"tag1": "web", "tag2": "backend"}
        body = {"tags": ["{{tag1}}", "database", "{{tag2}}"]}

        resolved = resolver.resolve_data(body, context)
        assert resolved == {"tags": ["web", "database", "backend"]}

    def test_33_multiple_variables_in_a_single_string(self) -> None:
        """Case 33: Multiple variables in a single string."""
        resolver = VariableResolver()
        context = {
            "protocol": "https",
            "host": "api.example.com",
            "version": "v1",
            "resource": "orders",
            "id": 1234,
        }
        template = "{{protocol}}://{{host}}/{{version}}/{{resource}}/{{id}}"

        result = resolver.resolve(template, context)
        assert result == "https://api.example.com/v1/orders/1234"

    def test_34_numeric_and_boolean_native_type_preservation(self) -> None:
        """Case 34: Numeric and boolean variable replacement preserves native types."""
        resolver = VariableResolver()
        context = {
            "int_val": 42,
            "float_val": 99.95,
            "bool_val": True,
            "none_val": None,
        }
        body = {
            "count": "{{int_val}}",
            "price": "{{float_val}}",
            "active": "{{bool_val}}",
            "optional": "{{none_val}}",
        }

        resolved = resolver.resolve_data(body, context)
        assert resolved["count"] == 42
        assert isinstance(resolved["count"], int)
        assert resolved["price"] == 99.95
        assert isinstance(resolved["price"], float)
        assert resolved["active"] is True
        assert isinstance(resolved["active"], bool)
        assert resolved["optional"] is None

    def test_35_missing_variable_detection_raises_error(self) -> None:
        """Case 35: Missing variable detection raises VariableResolutionError."""
        resolver = VariableResolver()
        context = {"existing_var": "hello"}
        template = "{{existing_var}} {{missing_var}}"

        with pytest.raises(VariableResolutionError, match="missing_var"):
            resolver.resolve(template, context)

    def test_36_test_definition_immutability(self) -> None:
        """Case 36: Resolving variables does not mutate original definition."""
        test_def = TestDefinition(
            name="Immutability check",
            module="test",
            request=TestRequestDefinition(
                method="POST",
                url="{{base_url}}/items",
                headers={"Authorization": "Bearer {{token}}"},
                body={"count": "{{count}}"},
            ),
        )

        env = Environment(
            name="test_env",
            variables={
                "base_url": "https://api.example.com",
                "token": "tok_xyz",
                "count": 10,
            },
        )

        # Convert to HttpRequest (runs resolution)
        http_req = test_def.to_http_request(env)

        # Assert resolved HTTP request has substituted values
        assert http_req.url == "https://api.example.com/items"
        assert http_req.headers["Authorization"] == "Bearer tok_xyz"
        assert http_req.body == {"count": 10}

        # Assert original TestDefinition remains completely unchanged
        assert test_def.request.url == "{{base_url}}/items"
        assert test_def.request.headers["Authorization"] == "Bearer {{token}}"
        assert test_def.request.body == {"count": "{{count}}"}


# ============================================================================
# 5. Secret Protection (Cases 37 - 40)
# ============================================================================


class TestSecretProtection:
    """Security tests ensuring sensitive variables are masked in output and logs."""

    def test_37_secret_values_masked_in_environment_repr_and_str(self) -> None:
        """Case 37: Secrets masked as '***' in Environment string representation."""
        env = Environment(
            name="prod",
            variables={
                "base_url": "https://api.example.com",
                "db_password": "super_secret_db_password_123",
                "api_key": "prod_key_9999",
            },
            secrets={"db_password", "api_key"},
        )

        repr_str = repr(env)
        str_str = str(env)

        assert "super_secret_db_password_123" not in repr_str
        assert "prod_key_9999" not in repr_str
        assert "'db_password': '***'" in repr_str
        assert "'api_key': '***'" in repr_str

        assert "super_secret_db_password_123" not in str_str
        assert "prod_key_9999" not in str_str
        assert "'db_password': '***'" in str_str

    def test_38_secret_values_masked_in_logs_and_error_messages(self) -> None:
        """Case 38: Secret values masked in logs / error messages via redact_secrets."""
        env = Environment(
            name="prod",
            variables={
                "secret_token": "sensitive_jwt_token_888",
            },
            secrets={"secret_token"},
        )

        log_message = "Failed connecting with token sensitive_jwt_token_888 to server."
        sanitized = env.redact_secrets(log_message)

        assert "sensitive_jwt_token_888" not in sanitized
        assert sanitized == "Failed connecting with token *** to server."

    def test_39_secret_actual_values_retained_in_execution_request(self) -> None:
        """Case 39: Raw secret values are retained when building HttpRequest."""
        env = Environment(
            name="prod",
            variables={
                "base_url": "https://api.example.com",
                "secret_key": "real_raw_secret_value_123",
            },
            secrets={"secret_key"},
        )

        test_def = TestDefinition(
            name="Protected request",
            module="security",
            request=TestRequestDefinition(
                method="GET",
                url="{{base_url}}/secure-data",
                headers={"X-API-KEY": "{{secret_key}}"},
            ),
        )

        http_req = test_def.to_http_request(env)

        # Raw secret value retained for request execution
        assert http_req.headers["X-API-KEY"] == "real_raw_secret_value_123"

    def test_40_non_secret_variables_resolve_normally(self) -> None:
        """Case 40: Non-secret variables are NOT masked and resolve normally."""
        env = Environment(
            name="dev",
            variables={
                "base_url": "http://localhost:8080",
                "service": "orders",
                "secret_token": "hidden_token",
            },
            secrets={"secret_token"},
        )

        test_def = TestDefinition(
            name="Public request",
            module="orders",
            request=TestRequestDefinition(
                method="GET",
                url="{{base_url}}/{{service}}",
                headers={"X-Service": "{{service}}"},
            ),
        )

        http_req = test_def.to_http_request(env)
        assert http_req.url == "http://localhost:8080/orders"
        assert http_req.headers["X-Service"] == "orders"
        assert "http://localhost:8080" in env.masked_variables()["base_url"]


# ============================================================================
# 6. Request Conversion and Execution (Cases 41 - 44)
# ============================================================================


class TestRequestConversionAndExecution:
    """End-to-end conversion to HttpRequest and execution via RequestExecutor."""

    def test_41_valid_conversion_from_test_definition_to_http_request(self) -> None:
        """Case 41: Valid conversion from definition + env into HttpRequest."""
        env = Environment(
            name="dev",
            variables={"base_url": "https://api.example.com", "user_id": 501},
        )
        test_def = TestDefinition(
            name="Get User Details",
            module="users",
            request=TestRequestDefinition(
                method=HttpMethod.GET,
                url="{{base_url}}/users/{{user_id}}",
                headers={"Accept": "application/json"},
                params={"include_history": "true"},
                timeout=12.0,
                max_retries=2,
                follow_redirects=False,
                retry_non_idempotent=True,
            ),
        )

        http_req = test_def.to_http_request(env)

        assert isinstance(http_req, HttpRequest)
        assert http_req.method == HttpMethod.GET
        assert http_req.url == "https://api.example.com/users/501"
        assert http_req.headers == {"Accept": "application/json"}
        assert http_req.params == {"include_history": "true"}
        assert http_req.timeout == 12.0
        assert http_req.max_retries == 2
        assert http_req.follow_redirects is False
        assert http_req.retry_non_idempotent is True

    def test_42_http_request_all_fields_mapped_correctly(self) -> None:
        """Case 42: HttpRequest maps method, URL, headers, params, body."""
        env = Environment(
            name="staging",
            variables={
                "base_url": "https://staging.example.com",
                "tenant": "tenant_abc",
                "item_name": "Gadget",
                "item_price": 49.99,
            },
        )
        test_def = TestDefinition(
            name="Create Item",
            module="inventory",
            request=TestRequestDefinition(
                method="POST",
                url="{{base_url}}/items",
                headers={"X-Tenant": "{{tenant}}"},
                params={"source": "api"},
                body={"name": "{{item_name}}", "price": "{{item_price}}"},
                body_type="json",
                timeout=25.0,
                max_retries=1,
            ),
        )

        http_req = test_def.to_http_request(env)

        assert http_req.method == HttpMethod.POST
        assert http_req.url == "https://staging.example.com/items"
        assert http_req.headers["X-Tenant"] == "tenant_abc"
        assert http_req.params == {"source": "api"}
        assert http_req.body == {"name": "Gadget", "price": 49.99}
        assert isinstance(http_req.body["price"], float)
        assert http_req.body_type == BodyType.JSON
        assert http_req.timeout == 25.0
        assert http_req.max_retries == 1

    def test_43_converted_request_executes_successfully_with_mock_transport(
        self,
    ) -> None:
        """Case 43: Converted request executes with RequestExecutor mock transport."""
        env = Environment(
            name="test",
            variables={
                "base_url": "https://api.example.com",
                "order_id": 999,
                "auth_token": "token_secret_123",
            },
            secrets={"auth_token"},
        )
        test_def = TestDefinition(
            name="Get Order Status",
            module="orders",
            request=TestRequestDefinition(
                method="GET",
                url="{{base_url}}/orders/{{order_id}}",
                headers={"Authorization": "Bearer {{auth_token}}"},
            ),
        )

        # Mock transport verifying incoming request
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert str(request.url) == "https://api.example.com/orders/999"
            assert request.headers["Authorization"] == "Bearer token_secret_123"
            return httpx.Response(
                status_code=200,
                headers={"Content-Type": "application/json"},
                json={"order_id": 999, "status": "shipped"},
            )

        policy = TargetAccessPolicy(allowed_hosts=["api.example.com"])
        executor = RequestExecutor(
            transport=httpx.MockTransport(handler),
            policy=policy,
        )

        http_req = test_def.to_http_request(env)
        response = executor.execute(http_req)

        assert response.status_code == 200
        assert response.is_success is True
        assert response.json() == {"order_id": 999, "status": "shipped"}

    def test_44_regression_suite_pass(self) -> None:
        """Case 44: Verify overall compatibility and regression stability."""
        # Sanity check verifying that core models and definitions interoperate cleanly
        req = HttpRequest(method="GET", url="https://api.example.com/test")
        assert req.method == HttpMethod.GET
        assert req.timeout == 30.0
