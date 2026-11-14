"""AI test generation service with live LLM providers and approval workflow.

Covers: FR-50, FR-51, FR-52, FR-53, FR-54, NFR-02, NFR-03, NFR-06.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
import yaml

from apiscope.core.exceptions import (
    ConfigurationError,
    DefinitionError,
    NetworkExecutionError,
    PathTraversalError,
)
from apiscope.core.logging_config import redact_sensitive_text
from apiscope.definitions.collection import (
    TestCollection,
    load_collection,
    validate_safe_path,
)
from apiscope.definitions.models import (
    DefinitionStatus,
    TestDefinition,
)
from apiscope.definitions.yaml_loader import parse_test_definition
from apiscope.generators.openapi_importer import OpenAPISpec, load_openapi_spec
from apiscope.generators.project_profile import ProjectProfile, load_project_profile
from apiscope.generators.service import dump_tests_to_yaml

MAX_LLM_RESPONSE_BYTES: int = 1_048_576  # 1 MB
MAX_AI_GENERATED_TESTS: int = 200
DEFAULT_LLM_TIMEOUT_SEC: float = 45.0


@dataclass(frozen=True)
class LLMConfig:
    """Connection credentials and runtime parameters for LLM providers."""

    __test__ = False

    provider: str = "gemini"  # "gemini", "openai", or "profile-fallback"
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    timeout_sec: float = DEFAULT_LLM_TIMEOUT_SEC
    temperature: float = 0.0

    def __post_init__(self) -> None:
        """Set default model and base_url if not explicitly provided."""
        if not self.base_url:
            if self.provider == "gemini":
                object.__setattr__(
                    self,
                    "base_url",
                    "https://generativelanguage.googleapis.com/v1beta",
                )
            elif self.provider == "openai":
                object.__setattr__(self, "base_url", "https://api.openai.com/v1")
        if not self.model:
            if self.provider == "gemini":
                object.__setattr__(self, "model", "gemini-2.5-flash")
            elif self.provider == "openai":
                object.__setattr__(self, "model", "gpt-4o-mini")

    def __repr__(self) -> str:
        """Mask raw API key in string representations to prevent secret leaks."""
        masked_key = "***" if self.api_key else ""
        return (
            f"LLMConfig(provider={self.provider!r}, api_key={masked_key!r}, "
            f"model={self.model!r}, base_url={self.base_url!r}, "
            f"timeout_sec={self.timeout_sec}, temperature={self.temperature})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert configuration to dictionary with masked secrets."""
        return {
            "provider": self.provider,
            "api_key": "***" if self.api_key else "",
            "model": self.model,
            "base_url": self.base_url,
            "timeout_sec": self.timeout_sec,
            "temperature": self.temperature,
        }

    @classmethod
    def from_env(
        cls,
        provider: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_sec: float | None = None,
        temperature: float | None = None,
    ) -> LLMConfig:
        """Resolve LLMConfig from environment variables and .env file."""
        # Auto-load .env in current working directory if present
        env_file = Path.cwd() / ".env"
        if env_file.is_file():
            try:
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or "=" not in stripped:
                        continue
                    k, v = stripped.split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    if (v.startswith('"') and v.endswith('"')) or (
                        v.startswith("'") and v.endswith("'")
                    ):
                        v = v[1:-1]
                    if k:
                        os.environ.setdefault(k, v)
            except Exception:
                pass

        # Check explicit api_key or env vars in priority order
        key = (
            api_key
            or os.environ.get("APISCOPE_LLM_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("GROQ_API_KEY")
            or ""
        )

        # Provider auto-detection
        if provider:
            prov = provider.strip().lower()
        elif os.environ.get("APISCOPE_LLM_PROVIDER"):
            prov = os.environ["APISCOPE_LLM_PROVIDER"].strip().lower()
        elif os.environ.get("GEMINI_API_KEY"):
            prov = "gemini"
        elif os.environ.get("OPENAI_API_KEY") or os.environ.get("GROQ_API_KEY"):
            prov = "openai"
        else:
            prov = "gemini"

        # Model defaults
        if model:
            mod = model.strip()
        elif prov == "gemini":
            mod = "gemini-2.5-flash"
        elif prov == "openai":
            mod = "gpt-4o-mini"
        else:
            mod = "default"

        # Base URL defaults
        if base_url:
            b_url = base_url.strip()
        elif prov == "gemini":
            b_url = "https://generativelanguage.googleapis.com/v1beta"
        elif prov == "openai":
            b_url = "https://api.openai.com/v1"
        else:
            b_url = ""

        t_out = (
            float(timeout_sec) if timeout_sec is not None else DEFAULT_LLM_TIMEOUT_SEC
        )
        temp = float(temperature) if temperature is not None else 0.0

        return cls(
            provider=prov,
            api_key=key,
            model=mod,
            base_url=b_url,
            timeout_sec=t_out,
            temperature=temp,
        )


class LLMProviderProtocol(Protocol):
    """Protocol for LLM test generation providers."""

    def generate_yaml(self, system_prompt: str, user_prompt: str) -> str:
        """Send prompt to LLM and return raw YAML text string."""
        ...


class LiveHttpLLMProvider(LLMProviderProtocol):
    """Live HTTPS LLM API adapter for Gemini and OpenAI-compatible endpoints.

    In outbound requests, sends the RAW API key in the appropriate header.
    In all exceptions, logs, and error messages, strips and masks the API key.
    """

    __test__ = False

    def __init__(
        self,
        config: LLMConfig,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self._custom_client = client
        self._transport = transport
        self._owned_client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._custom_client is not None:
            return self._custom_client
        if self._owned_client is None:
            self._owned_client = httpx.Client(
                transport=self._transport,
                timeout=self.config.timeout_sec,
            )
        return self._owned_client

    def _scrub(self, text: str) -> str:
        """Scrub secret API key and sensitive credentials from any text output."""
        if self.config.api_key:
            text = text.replace(self.config.api_key, "***")
        return redact_sensitive_text(text)

    def generate_yaml(self, system_prompt: str, user_prompt: str) -> str:
        """Call LLM chat completion API and return generated YAML response string."""
        if not self.config.api_key or not self.config.api_key.strip():
            raise ConfigurationError(
                "Missing LLM API key. Set APISCOPE_LLM_API_KEY, GEMINI_API_KEY, "
                "or OPENAI_API_KEY in your environment or .env file."
            )

        client = self._get_client()

        if self.config.provider == "gemini":
            url = (
                f"{self.config.base_url.rstrip('/')}/models/"
                f"{self.config.model}:generateContent"
            )
            headers = {
                "x-goog-api-key": self.config.api_key,
                "Content-Type": "application/json",
            }
            payload = {
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {"temperature": self.config.temperature},
            }
        elif self.config.provider == "openai":
            url = f"{self.config.base_url.rstrip('/')}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.config.model,
                "temperature": self.config.temperature,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
        else:
            raise ConfigurationError(
                f"Unsupported LLM provider: '{self.config.provider}'"
            )

        try:
            resp = client.post(url, headers=headers, json=payload)
            if len(resp.content) > MAX_LLM_RESPONSE_BYTES:
                raise DefinitionError(
                    f"LLM response exceeded limit of {MAX_LLM_RESPONSE_BYTES} bytes."
                )

            if resp.status_code in (401, 403):
                err_body = self._scrub(resp.text)
                raise ConfigurationError(
                    f"LLM authentication failed ({resp.status_code}): {err_body}"
                )

            if resp.status_code >= 400:
                err_body = self._scrub(resp.text)
                raise NetworkExecutionError(
                    f"LLM API request failed with HTTP {resp.status_code}: {err_body}"
                )

            resp_json = resp.json()

            if self.config.provider == "gemini":
                candidates = resp_json.get("candidates", [])
                if not candidates or "content" not in candidates[0]:
                    raise NetworkExecutionError(
                        f"Malformed Gemini response: {self._scrub(resp.text)}"
                    )
                parts = candidates[0]["content"].get("parts", [])
                if not parts or "text" not in parts[0]:
                    raise NetworkExecutionError(
                        f"Missing text in Gemini response: {self._scrub(resp.text)}"
                    )
                return str(parts[0]["text"])
            else:  # openai
                choices = resp_json.get("choices", [])
                if not choices or "message" not in choices[0]:
                    raise NetworkExecutionError(
                        f"Malformed OpenAI response: {self._scrub(resp.text)}"
                    )
                content = choices[0]["message"].get("content", "")
                return str(content)

        except (ConfigurationError, DefinitionError, NetworkExecutionError):
            raise
        except httpx.TimeoutException as exc:
            msg = self._scrub(
                f"LLM request timed out after {self.config.timeout_sec}s: {exc}"
            )
            raise NetworkExecutionError(msg) from exc
        except httpx.HTTPError as exc:
            msg = self._scrub(f"LLM network error: {exc}")
            raise NetworkExecutionError(msg) from exc
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            msg = self._scrub(f"Malformed JSON response from LLM provider: {exc}")
            raise NetworkExecutionError(msg) from exc
        except Exception as exc:
            msg = self._scrub(
                f"Unexpected error communicating with LLM provider: {exc}"
            )
            raise NetworkExecutionError(msg) from exc


class ProfileDrivenSynthesisProvider(LLMProviderProtocol):
    """Deterministic offline synthesis provider using ProjectProfile rules.

    Emits structured YAML tests matching the business rules when offline or
    when provider == 'profile-fallback'.
    """

    __test__ = False

    def __init__(
        self,
        profile: ProjectProfile | None = None,
        spec: OpenAPISpec | None = None,
        module: str | None = None,
    ) -> None:
        self.profile = profile
        self.spec = spec
        self.module = module

    def generate_yaml(self, system_prompt: str, user_prompt: str) -> str:
        """Generate deterministic YAML tests covering IDOR, states, and invariants."""
        _ = (system_prompt, user_prompt)
        tests_data: list[dict[str, Any]] = []

        # 1. IDOR Tests from roles
        if self.profile is not None:
            for r in self.profile.roles:
                if r.idor_check:
                    idor = r.idor_check
                    endpoint_str = str(idor.get("endpoint", ""))
                    expected_status = int(idor.get("expected_status", 403))
                    other_id = idor.get("other_id", 2)

                    method = "GET"
                    path = endpoint_str
                    if " " in endpoint_str:
                        method, path = endpoint_str.split(" ", 1)

                    resolved_path = path.replace("{user_id}", str(other_id))
                    token_var = r.token_variable
                    test_name = f"AI Security - IDOR boundary {r.role_name} on {path}"

                    tests_data.append(
                        {
                            "name": test_name,
                            "module": self.module or self.profile.domain,
                            "tags": ["security", "idor", "ai-generated"],
                            "source": "ai-generated",
                            "status": "proposed",
                            "request": {
                                "method": method.upper(),
                                "url": f"{{{{base_url}}}}{resolved_path}",
                                "headers": {
                                    "Authorization": f"Bearer {token_var}",
                                    "Content-Type": "application/json",
                                },
                            },
                            "extract": {},
                            "assertions": [
                                {
                                    "type": "status",
                                    "equals": expected_status,
                                }
                            ],
                        }
                    )

            # 2. State transition tests
            for s in self.profile.state_transitions:
                endpoint_str = s.endpoint
                method = "POST"
                path = endpoint_str
                if " " in endpoint_str:
                    method, path = endpoint_str.split(" ", 1)

                resolved_path = path.replace(
                    "{order_id}", str(s.invalid_state_id)
                ).replace("{asset_id}", str(s.invalid_state_id))
                expected_range = (
                    s.expected_status_range if s.expected_status_range else [400, 409]
                )
                state_test_name = (
                    f"AI Logic - State transition invalid {s.entity} "
                    f"{s.invalid_state_name}"
                )

                tests_data.append(
                    {
                        "name": state_test_name,
                        "module": self.module or self.profile.domain,
                        "tags": [
                            "state-transition",
                            "negative",
                            "ai-generated",
                        ],
                        "source": "ai-generated",
                        "status": "proposed",
                        "request": {
                            "method": method.upper(),
                            "url": f"{{{{base_url}}}}{resolved_path}",
                            "headers": {
                                "Content-Type": "application/json",
                            },
                            "body": s.body if s.body is not None else {},
                        },
                        "extract": {},
                        "assertions": [
                            {
                                "type": "status",
                                "in": expected_range,
                            }
                        ],
                    }
                )

            # 3. Business invariant tests (e.g. single-use coupon)
            for b in self.profile.business_invariants:
                endpoint_str = b.endpoint
                method = "POST"
                path = endpoint_str
                if " " in endpoint_str:
                    method, path = endpoint_str.split(" ", 1)

                if b.rule_type == "single_use":
                    # Step 1: Initial redemption
                    tests_data.append(
                        {
                            "name": f"AI Business - {b.description} (initial use)",
                            "module": b.module or self.module or self.profile.domain,
                            "tags": [
                                "business-invariant",
                                "single-use",
                                "ai-generated",
                            ],
                            "source": "ai-generated",
                            "status": "proposed",
                            "request": {
                                "method": method.upper(),
                                "url": f"{{{{base_url}}}}{path}",
                                "headers": {"Content-Type": "application/json"},
                                "body": b.payload,
                            },
                            "extract": {},
                            "assertions": [
                                {
                                    "type": "status",
                                    "equals": b.first_expected_status,
                                }
                            ],
                        }
                    )
                    # Step 2: Repeat rejection
                    tests_data.append(
                        {
                            "name": f"AI Business - {b.description} (repeat rejected)",
                            "module": b.module or self.module or self.profile.domain,
                            "tags": [
                                "business-invariant",
                                "single-use",
                                "ai-generated",
                            ],
                            "source": "ai-generated",
                            "status": "proposed",
                            "request": {
                                "method": method.upper(),
                                "url": f"{{{{base_url}}}}{path}",
                                "headers": {"Content-Type": "application/json"},
                                "body": b.payload,
                            },
                            "extract": {},
                            "assertions": [
                                {
                                    "type": "status",
                                    "in": b.repeat_expected_status_range,
                                }
                            ],
                        }
                    )

        if not tests_data:
            tests_data.append(
                {
                    "name": "AI Baseline Health Ping",
                    "module": "health",
                    "tags": ["smoke", "ai-generated"],
                    "source": "ai-generated",
                    "status": "proposed",
                    "request": {
                        "method": "GET",
                        "url": "{{base_url}}/health",
                    },
                    "extract": {},
                    "assertions": [{"type": "status", "equals": 200}],
                }
            )

        proj_title = self.profile.project_name if self.profile else "AI"
        coll_dict = {
            "name": f"{proj_title} Generated Suite",
            "module": self.module
            or (self.profile.domain if self.profile else "default"),
            "tests": tests_data,
        }
        return yaml.dump(coll_dict, sort_keys=False)


def build_ai_generation_prompt(
    spec: OpenAPISpec,
    profile: ProjectProfile,
    module: str | None = None,
) -> tuple[str, str]:
    """Construct structured system and user prompts for LLM test generation.

    Args:
        spec: Normalized OpenAPISpec model.
        profile: ProjectProfile domain rules and context.
        module: Optional module filter.

    Returns:
        (system_prompt, user_prompt) tuple.
    """
    system_prompt = (
        "You are an expert automated API testing and security engineer. "
        "Your task is to generate multi-step business-logic, authorization (IDOR), "
        "and state-transition test suites for the ApiScope testing framework.\n\n"
        "OUTPUT FORMAT REQUIREMENTS:\n"
        "- Respond ONLY with a valid YAML document conforming to the ApiScope schema.\n"
        "- Do NOT wrap your output in markdown prose or explanations.\n"
        "- The root of the YAML must contain: 'name', 'module', and 'tests'.\n"
        "- Each test in 'tests' MUST have: 'name', 'module', 'tags', "
        "'source: ai-generated', 'status: proposed', 'request' (with 'method' "
        "and 'url'), 'extract' (dict), and 'assertions' (non-empty list).\n"
        "- Every test MUST include at least one deterministic assertion\n"
        "  (e.g., {'type': 'status', 'equals': 200} or "
        "{'type': 'status', 'in': [400, 409]}).\n"
        "- Use '{{base_url}}' for API root and variable references like "
        "'{{user_1_token}}'.\n"
    )

    endpoints = spec.filter_by_module(module) if module else list(spec.endpoints)
    endpoints_summary = [
        f"- {ep.method} {ep.path} (module: {ep.module}, auth: {ep.requires_auth})"
        for ep in endpoints[:30]
    ]

    roles_summary = [
        f"- Role '{r.role_name}': token '{r.token_variable}', idor: {r.idor_check}"
        for r in profile.roles
    ]

    states_summary = [
        f"- State Rule on {s.entity}: endpoint '{s.endpoint}', "
        f"invalid_state: '{s.invalid_state_name}', expected: {s.expected_status_range}"
        for s in profile.state_transitions
    ]

    invariants_summary = [
        f"- Invariant '{b.rule_id}': type '{b.rule_type}', endpoint '{b.endpoint}', "
        f"first_status: {b.first_expected_status}, "
        f"repeat: {b.repeat_expected_status_range}"
        for b in profile.business_invariants
    ]

    user_prompt = (
        f"Project: {profile.project_name}\n"
        f"Domain: {profile.domain}\n"
        f"Module Filter: {module or 'ALL'}\n\n"
        f"API Endpoints ({len(endpoints)} total):\n"
        + "\n".join(endpoints_summary)
        + "\n\n"
        "Security & Role Rules:\n"
        + ("\n".join(roles_summary) if roles_summary else "None")
        + "\n\n"
        "Invalid State Transitions:\n"
        + ("\n".join(states_summary) if states_summary else "None")
        + "\n\n"
        "Business Invariants:\n"
        + ("\n".join(invariants_summary) if invariants_summary else "None")
        + "\n\n"
        "Generate a comprehensive ApiScope YAML test suite targeting these "
        "business rules, IDOR checks, and invalid state transitions."
    )

    return system_prompt, user_prompt


class AiGeneratorService:
    """Orchestrates LLM prompts, output validation, and human approvals."""

    __test__ = False

    def __init__(
        self,
        provider: LLMProviderProtocol | None = None,
        config: LLMConfig | None = None,
    ) -> None:
        if provider is not None:
            self._provider = provider
        elif config is not None:
            if config.provider == "profile-fallback":
                self._provider = ProfileDrivenSynthesisProvider()
            else:
                self._provider = LiveHttpLLMProvider(config=config)
        else:
            cfg = LLMConfig.from_env()
            if cfg.provider == "profile-fallback":
                self._provider = ProfileDrivenSynthesisProvider()
            else:
                self._provider = LiveHttpLLMProvider(config=cfg)

    def generate_ai_suite(
        self,
        spec_path: str | Path,
        profile_path: str | Path,
        output_path: str | Path,
        module: str | None = None,
        base_dir: Path | None = None,
    ) -> TestCollection:
        """Generate, validate, and persist an AI-generated test suite.

        Enforces governance: sets source='ai-generated' and status='proposed'.
        Guarantees that tests with 0 assertions are strictly rejected.

        Args:
            spec_path: Path to OpenAPI specification file.
            profile_path: Path to Project Profile YAML file.
            output_path: Destination path for generated YAML suite.
            module: Optional module filter.
            base_dir: Optional base directory to confine file paths.

        Returns:
            Validated TestCollection loaded from the generated file.

        Raises:
            PathTraversalError: If any path escapes base_dir.
            DefinitionError: If generated YAML is invalid or violates rules.
        """
        spec_p = Path(spec_path)
        ref_spec = str(spec_path).replace("\\", "/")
        if "../" in ref_spec or ref_spec.startswith("../") or ".." in spec_p.parts:
            raise PathTraversalError(
                f"Forbidden directory traversal sequence detected in '{spec_path}'."
            )
        eff_spec_base = (
            base_dir
            if base_dir is not None
            else (spec_p.parent if spec_p.is_absolute() else Path.cwd())
        )
        safe_spec = validate_safe_path(base_dir=eff_spec_base, ref_path=spec_path)

        prof_p = Path(profile_path)
        ref_prof = str(profile_path).replace("\\", "/")
        if "../" in ref_prof or ref_prof.startswith("../") or ".." in prof_p.parts:
            raise PathTraversalError(
                f"Forbidden directory traversal sequence detected in '{profile_path}'."
            )
        eff_prof_base = (
            base_dir
            if base_dir is not None
            else (prof_p.parent if prof_p.is_absolute() else Path.cwd())
        )
        safe_profile = validate_safe_path(base_dir=eff_prof_base, ref_path=profile_path)

        out_p = Path(output_path)
        ref_out = str(output_path).replace("\\", "/")
        if "../" in ref_out or ref_out.startswith("../") or ".." in out_p.parts:
            raise PathTraversalError(
                f"Forbidden directory traversal sequence detected in '{output_path}'."
            )
        eff_out_base = (
            base_dir
            if base_dir is not None
            else (out_p.parent if out_p.is_absolute() else Path.cwd())
        )
        safe_output = validate_safe_path(base_dir=eff_out_base, ref_path=output_path)

        spec = load_openapi_spec(safe_spec)
        profile = load_project_profile(safe_profile, base_dir=base_dir)

        # Inject context into ProfileDrivenSynthesisProvider if applicable
        if isinstance(self._provider, ProfileDrivenSynthesisProvider):
            self._provider.profile = profile
            self._provider.spec = spec
            self._provider.module = module

        system_prompt, user_prompt = build_ai_generation_prompt(
            spec=spec, profile=profile, module=module
        )

        raw_llm_output = self._provider.generate_yaml(system_prompt, user_prompt)

        # Clean markdown code fences if present (e.g. ```yaml ... ```)
        cleaned_yaml = raw_llm_output.strip()
        if cleaned_yaml.startswith("```"):
            lines = cleaned_yaml.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned_yaml = "\n".join(lines).strip()

        try:
            data = yaml.safe_load(cleaned_yaml)
        except Exception as err:
            raise DefinitionError(
                f"Failed to parse LLM response as YAML: {err}"
            ) from err

        if not isinstance(data, dict):
            raise DefinitionError(
                "LLM response must be a valid YAML dictionary mapping."
            )

        raw_tests = data.get("tests", [])
        if not isinstance(raw_tests, list) or not raw_tests:
            raise DefinitionError("LLM response contains no test definitions.")

        if len(raw_tests) > MAX_AI_GENERATED_TESTS:
            raise DefinitionError(
                f"Generated test count ({len(raw_tests)}) exceeds limit "
                f"of {MAX_AI_GENERATED_TESTS}."
            )

        validated_tests: list[TestDefinition] = []
        for idx, t_dict in enumerate(raw_tests):
            if not isinstance(t_dict, dict):
                raise DefinitionError(f"Test at index {idx} must be a dictionary.")

            # Enforce governance requirements (FR-52, FR-54)
            t_dict["source"] = "ai-generated"
            t_dict["status"] = "proposed"
            if not t_dict.get("module"):
                t_dict["module"] = module or data.get("module") or profile.domain

            test_def = parse_test_definition(t_dict)

            # Reject tests with 0 assertions
            if not test_def.assertions:
                raise DefinitionError(
                    f"AI-generated test '{test_def.name}' must define at least "
                    f"one deterministic assertion."
                )

            validated_tests.append(test_def)

        suite_name = data.get("name", f"{profile.project_name} AI Suite")
        collection_module = module or data.get("module", profile.domain)

        yaml_content = dump_tests_to_yaml(
            tests=validated_tests,
            collection_name=str(suite_name),
            module=str(collection_module),
        )

        safe_output.parent.mkdir(parents=True, exist_ok=True)
        safe_output.write_text(yaml_content, encoding="utf-8")

        return load_collection(safe_output, base_dir=base_dir)

    def approve_suite(
        self,
        suite_path: str | Path,
        output_path: str | Path | None = None,
        test_names: Sequence[str] | None = None,
        base_dir: Path | None = None,
    ) -> TestCollection:
        """Approve proposed AI test definitions and write approved suite.

        Promotes test status from PROPOSED to APPROVED.

        Args:
            suite_path: Path to the test collection YAML file.
            output_path: Optional output path (updates in-place if None).
            test_names: Optional test names to approve. If None, approves all.
            base_dir: Optional base directory to confine file paths.

        Returns:
            Updated TestCollection model instance.

        Raises:
            PathTraversalError: If paths escape base_dir.
            DefinitionError: If suite cannot be parsed.
        """
        suite_p = Path(suite_path)
        ref_suite = str(suite_path).replace("\\", "/")
        if "../" in ref_suite or ref_suite.startswith("../") or ".." in suite_p.parts:
            raise PathTraversalError(
                f"Forbidden directory traversal sequence detected in '{suite_path}'."
            )
        eff_suite_base = (
            base_dir
            if base_dir is not None
            else (suite_p.parent if suite_p.is_absolute() else Path.cwd())
        )
        safe_suite = validate_safe_path(base_dir=eff_suite_base, ref_path=suite_path)

        if output_path is not None:
            out_p = Path(output_path)
            ref_out = str(output_path).replace("\\", "/")
            if "../" in ref_out or ref_out.startswith("../") or ".." in out_p.parts:
                raise PathTraversalError(
                    f"Forbidden directory traversal sequence in '{output_path}'."
                )
            eff_out_base = (
                base_dir
                if base_dir is not None
                else (out_p.parent if out_p.is_absolute() else Path.cwd())
            )
            target_path = validate_safe_path(
                base_dir=eff_out_base, ref_path=output_path
            )
        else:
            target_path = safe_suite

        collection = load_collection(safe_suite, base_dir=base_dir)

        approved_tests: list[TestDefinition] = []
        name_filter = set(test_names) if test_names is not None else None

        for t in collection.tests:
            if name_filter is None or t.name in name_filter:
                updated = TestDefinition(
                    name=t.name,
                    module=t.module,
                    request=t.request,
                    tags=list(t.tags),
                    extract=dict(t.extract),
                    assertions=list(t.assertions),
                    source=t.source,
                    status=DefinitionStatus.APPROVED,
                    file_path=target_path,
                )
                approved_tests.append(updated)
            else:
                approved_tests.append(t)

        yaml_content = dump_tests_to_yaml(
            tests=approved_tests,
            collection_name=collection.name,
            module=collection.module,
        )

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(yaml_content, encoding="utf-8")

        return load_collection(target_path, base_dir=base_dir)
