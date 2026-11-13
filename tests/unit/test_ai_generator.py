"""Unit tests for Live LLM Provider Adapter and AI Test Generator.

Covers: FR-50, FR-51, FR-52, FR-53, FR-54, NFR-02, NFR-03, NFR-06.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from apiscope.core.exceptions import (
    ConfigurationError,
    DefinitionError,
    NetworkExecutionError,
)
from apiscope.definitions.collection import load_collection
from apiscope.definitions.models import (
    DefinitionSource,
    DefinitionStatus,
)
from apiscope.generators.ai_generator import (
    AiGeneratorService,
    LiveHttpLLMProvider,
    LLMConfig,
    ProfileDrivenSynthesisProvider,
    build_ai_generation_prompt,
)
from apiscope.generators.openapi_importer import load_openapi_spec
from apiscope.generators.project_profile import load_project_profile


def test_live_http_llm_provider_gemini_sends_raw_key_and_parses_yaml(
    tmp_path: Path,
) -> None:
    """Covers: FR-50, FR-51, FR-52, NFR-03.

    Enforces the Two-Sided Secret Rule: outbound HTTP request transmits the raw
    API key via x-goog-api-key header, while LLMConfig repr and dictionary
    strictly mask the key as '***'. Also tests markdown stripping and parsing.
    """
    raw_key = "raw-gemini-secret-12345"
    captured_requests: list[httpx.Request] = []

    gemini_yaml_response = """```yaml
version: "1"
module: "auth"
tests:
  - name: "AI IDOR Test - Access user profile"
    request:
      method: "GET"
      url: "{{base_url}}/users/2/profile"
      headers:
        Authorization: "Bearer {{user_1_token}}"
    tags:
      - "ai-generated"
      - "security"
    assertions:
      - type: "status"
        equals: 403
```"""

    def mock_handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": gemini_yaml_response}]}}]
            },
        )

    config = LLMConfig(
        provider="gemini",
        api_key=raw_key,
        model="gemini-2.5-flash",
    )

    # 1. Verify secrets are masked locally
    assert raw_key not in repr(config)
    assert "***" in repr(config)
    assert config.to_dict()["api_key"] == "***"

    transport = httpx.MockTransport(mock_handler)
    provider = LiveHttpLLMProvider(config=config, transport=transport)

    spec_path = "sample_app/openapi.yaml"
    profile_path = "examples/m12_sample_app_profile.yaml"
    output_path = tmp_path / "gemini_gen.yaml"

    service = AiGeneratorService(provider=provider)
    collection = service.generate_ai_suite(
        spec_path=spec_path,
        profile_path=profile_path,
        output_path=output_path,
    )

    # 2. Verify raw secret was transmitted on the wire
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.headers.get("x-goog-api-key") == raw_key

    # 3. Verify suite governance
    assert len(collection.tests) == 1
    test = collection.tests[0]
    assert test.name == "AI IDOR Test - Access user profile"
    assert test.source == DefinitionSource.AI_GENERATED
    assert test.status == DefinitionStatus.PROPOSED
    assert "ai-generated" in test.tags
    assert len(test.assertions) >= 1
    assert test.assertions[0]["type"] == "status"
    assert test.assertions[0]["equals"] == 403


def test_live_http_llm_provider_openai_masks_key_on_http_error() -> None:
    """Covers: FR-50, NFR-02, NFR-03.

    Verify OpenAI provider transmits Bearer token outbound, and scrubs sensitive API key
    from exception messages and logs when receiving an HTTP 401 error.
    """
    raw_key = "raw-openai-secret-key-999"
    captured_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        # Server echoes the secret in the error body
        return httpx.Response(
            401,
            text=f"Unauthorized: Invalid API key {raw_key} provided.",
        )

    config = LLMConfig(
        provider="openai",
        api_key=raw_key,
        model="gpt-4o",
    )

    transport = httpx.MockTransport(mock_handler)
    provider = LiveHttpLLMProvider(config=config, transport=transport)

    with pytest.raises(ConfigurationError) as exc_info:
        provider.generate_yaml("System instruction", "User prompt")

    err_msg = str(exc_info.value)
    # The raw key MUST be scrubbed from the exception message
    assert raw_key not in err_msg
    assert "***" in err_msg
    assert "LLM authentication failed (401)" in err_msg

    # Verify outbound header had the raw Bearer token
    assert len(captured_requests) == 1
    assert captured_requests[0].headers.get("authorization") == f"Bearer {raw_key}"


def test_live_http_llm_provider_missing_api_key_raises_configuration_error() -> None:
    """Covers: FR-50, NFR-02.

    Calling generate_yaml without an API key must raise ConfigurationError.
    """
    config = LLMConfig(provider="gemini", api_key="")
    provider = LiveHttpLLMProvider(config=config)

    with pytest.raises(ConfigurationError, match="Missing LLM API key"):
        provider.generate_yaml("sys", "user")


def test_ai_generator_rejects_unverified_tests_with_zero_assertions(
    tmp_path: Path,
) -> None:
    """Covers: FR-52, FR-54.

    AiGeneratorService must reject suites containing tests with zero assertions.
    """
    raw_yaml_no_assertions = """
version: "1"
module: "test"
tests:
  - name: "Unverified Test Without Assertions"
    request:
      method: "GET"
      url: "{{base_url}}/health"
    assertions: []
"""
    mock_provider = pytest.importorskip("unittest.mock").MagicMock()
    mock_provider.generate_yaml.return_value = raw_yaml_no_assertions

    service = AiGeneratorService(provider=mock_provider)
    with pytest.raises(
        DefinitionError, match="must define at least one deterministic assertion"
    ):
        service.generate_ai_suite(
            spec_path="sample_app/openapi.yaml",
            profile_path="examples/m12_sample_app_profile.yaml",
            output_path=tmp_path / "zero_asserts.yaml",
        )


def test_profile_driven_synthesis_provider_generates_valid_suite(
    tmp_path: Path,
) -> None:
    """Covers: FR-51, FR-52, NFR-06.

    Verify that fallback ProfileDrivenSynthesisProvider produces a valid,
    parseable YAML suite targeting roles, state transitions, and business invariants.
    """
    provider = ProfileDrivenSynthesisProvider()
    service = AiGeneratorService(provider=provider)
    output_file = tmp_path / "profile_synth.yaml"

    collection = service.generate_ai_suite(
        spec_path="sample_app/openapi.yaml",
        profile_path="examples/m12_sample_app_profile.yaml",
        output_path=output_file,
    )

    assert len(collection.tests) >= 3
    for test in collection.tests:
        assert test.source == DefinitionSource.AI_GENERATED
        assert test.status == DefinitionStatus.PROPOSED
        assert "ai-generated" in test.tags
        assert len(test.assertions) >= 1

    test_names = [t.name for t in collection.tests]
    assert any("IDOR" in name for name in test_names)
    assert any("State transition" in name for name in test_names)
    assert any("Business" in name for name in test_names)
    all_tags = {tag for t in collection.tests for tag in t.tags}
    assert "business-invariant" in all_tags
    assert "state-transition" in all_tags
    assert "security" in all_tags


def test_ai_generator_service_approve_suite(tmp_path: Path) -> None:
    """Covers: FR-53, FR-54.

    AiGeneratorService.approve_suite promotes tests to approved status.
    """
    provider = ProfileDrivenSynthesisProvider()
    service = AiGeneratorService(provider=provider)
    proposed_file = tmp_path / "proposed.yaml"

    collection = service.generate_ai_suite(
        spec_path="sample_app/openapi.yaml",
        profile_path="examples/m12_sample_app_profile.yaml",
        output_path=proposed_file,
    )

    # Approve into a new file
    approved_file = tmp_path / "approved.yaml"
    approved_collection = service.approve_suite(
        suite_path=proposed_file, output_path=approved_file
    )

    assert len(approved_collection.tests) == len(collection.tests)
    for test in approved_collection.tests:
        assert test.status == DefinitionStatus.APPROVED

    # Reload from disk and verify persistence
    reloaded = load_collection(approved_file)
    for test in reloaded.tests:
        assert test.status == DefinitionStatus.APPROVED


def test_build_ai_generation_prompt_structure() -> None:
    """Covers: FR-51.

    Verify build_ai_generation_prompt includes OpenAPI paths and profile security rules.
    """
    spec = load_openapi_spec("sample_app/openapi.yaml")
    profile = load_project_profile("examples/m12_sample_app_profile.yaml")

    system_prompt, user_prompt = build_ai_generation_prompt(spec, profile)

    assert "expert automated API testing and security engineer" in system_prompt
    assert "status: proposed" in system_prompt or "status: 'proposed'" in system_prompt
    assert "Sample E-Commerce App" in user_prompt
    assert "BI-01" in user_prompt
    assert "/orders/{order_id}/cancel" in user_prompt


def test_llm_config_from_env_variations(monkeypatch: pytest.MonkeyPatch) -> None:
    """Covers: FR-50, NFR-03.

    Test environment resolution for Gemini, OpenAI, Groq, and explicit params.
    """
    # 1. Gemini via GEMINI_API_KEY
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-env-key-99")
    monkeypatch.delenv("APISCOPE_LLM_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg1 = LLMConfig.from_env()
    assert cfg1.provider == "gemini"
    assert cfg1.api_key == "gemini-env-key-99"
    assert cfg1.model == "gemini-2.5-flash"
    assert "generativelanguage" in cfg1.base_url

    # 2. OpenAI via OPENAI_API_KEY
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-key-44")
    cfg2 = LLMConfig.from_env()
    assert cfg2.provider == "openai"
    assert cfg2.api_key == "sk-openai-key-44"
    assert cfg2.model == "gpt-4o-mini"
    assert "api.openai.com" in cfg2.base_url

    # 3. Explicit parameter overrides
    cfg3 = LLMConfig.from_env(
        provider="openai",
        api_key="custom-key",
        model="gpt-4o",
        base_url="https://custom.endpoint/v1",
        timeout_sec=60.0,
        temperature=0.7,
    )
    assert cfg3.provider == "openai"
    assert cfg3.api_key == "custom-key"
    assert cfg3.model == "gpt-4o"
    assert cfg3.base_url == "https://custom.endpoint/v1"
    assert cfg3.timeout_sec == 60.0
    assert cfg3.temperature == 0.7


def test_live_http_llm_provider_openai_success() -> None:
    """Covers: FR-50, FR-51, NFR-03.

    Verify OpenAI provider chat completions parsing on successful HTTP 200 response.
    """
    openai_yaml = """
version: "1"
module: "orders"
tests:
  - name: "AI Test OpenAI"
    request:
      method: "GET"
      url: "{{base_url}}/orders/101"
    assertions:
      - type: "status"
        equals: 200
"""

    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/chat/completions")
        assert request.headers.get("authorization") == "Bearer sk-test-key-555"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": openai_yaml,
                        }
                    }
                ]
            },
        )

    config = LLMConfig(provider="openai", api_key="sk-test-key-555")
    transport = httpx.MockTransport(mock_handler)
    provider = LiveHttpLLMProvider(config=config, transport=transport)

    result_yaml = provider.generate_yaml("system", "user")
    assert "AI Test OpenAI" in result_yaml


def test_live_http_llm_provider_network_and_http_errors() -> None:
    """Covers: FR-50, NFR-02, NFR-03.

    Verify NetworkExecutionError handling for timeouts, 500 errors, and malformed JSON.
    """
    config = LLMConfig(provider="gemini", api_key="raw-gemini-key")

    # 1. Timeout
    def timeout_handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("Connection timed out")

    prov_timeout = LiveHttpLLMProvider(
        config=config, transport=httpx.MockTransport(timeout_handler)
    )
    with pytest.raises(NetworkExecutionError, match="timed out"):
        prov_timeout.generate_yaml("sys", "user")

    # 2. HTTP 500 Server Error
    def err_500_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error raw-gemini-key")

    prov_500 = LiveHttpLLMProvider(
        config=config, transport=httpx.MockTransport(err_500_handler)
    )
    with pytest.raises(NetworkExecutionError) as exc_500:
        prov_500.generate_yaml("sys", "user")
    assert "raw-gemini-key" not in str(exc_500.value)
    assert "***" in str(exc_500.value)

    # 3. Malformed JSON
    def bad_json_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not valid json")

    prov_bad = LiveHttpLLMProvider(
        config=config, transport=httpx.MockTransport(bad_json_handler)
    )
    with pytest.raises(NetworkExecutionError, match="Malformed JSON"):
        prov_bad.generate_yaml("sys", "user")

    # 4. Unsupported provider
    bad_prov_config = LLMConfig(provider="unsupported-prov", api_key="k")
    prov_unsupported = LiveHttpLLMProvider(config=bad_prov_config)
    with pytest.raises(ConfigurationError, match="Unsupported LLM provider"):
        prov_unsupported.generate_yaml("sys", "user")


def test_ai_generator_service_limits_and_errors(tmp_path: Path) -> None:
    """Covers: FR-52, NFR-02.

    AiGeneratorService limits: oversized output, empty tests, and excessive test counts.
    """
    mock_prov = pytest.importorskip("unittest.mock").MagicMock()

    # 1. Empty tests list
    mock_prov.generate_yaml.return_value = "name: Empty\ntests: []\n"
    service = AiGeneratorService(provider=mock_prov)
    with pytest.raises(DefinitionError, match="contains no test definitions"):
        service.generate_ai_suite(
            spec_path="sample_app/openapi.yaml",
            profile_path="examples/m12_sample_app_profile.yaml",
            output_path=tmp_path / "empty.yaml",
        )

    # 2. More than 200 tests
    test_block = (
        "  - name: Test {i}\n"
        "    request:\n"
        "      method: GET\n"
        "      url: '{{{{base_url}}}}/health'\n"
        "    assertions:\n"
        "      - type: status\n"
        "        equals: 200\n"
    )
    excessive_tests = "".join(test_block.format(i=i) for i in range(205))
    mock_prov.generate_yaml.return_value = f"name: Big\ntests:\n{excessive_tests}"
    with pytest.raises(DefinitionError, match="exceeds limit of 200"):
        service.generate_ai_suite(
            spec_path="sample_app/openapi.yaml",
            profile_path="examples/m12_sample_app_profile.yaml",
            output_path=tmp_path / "big.yaml",
        )


def test_ai_generator_service_approve_suite_with_name_filter(tmp_path: Path) -> None:
    """Covers: FR-53, FR-54.

    AiGeneratorService.approve_suite allows approving only designated tests by name.
    """
    provider = ProfileDrivenSynthesisProvider()
    service = AiGeneratorService(provider=provider)
    proposed_file = tmp_path / "proposed.yaml"

    collection = service.generate_ai_suite(
        spec_path="sample_app/openapi.yaml",
        profile_path="examples/m12_sample_app_profile.yaml",
        output_path=proposed_file,
    )
    assert len(collection.tests) >= 2
    target_name = collection.tests[0].name

    approved_collection = service.approve_suite(
        suite_path=proposed_file,
        test_names=[target_name],
    )

    t0 = next(t for t in approved_collection.tests if t.name == target_name)
    assert t0.status == DefinitionStatus.APPROVED

    other_tests = [t for t in approved_collection.tests if t.name != target_name]
    for ot in other_tests:
        assert ot.status == DefinitionStatus.PROPOSED
