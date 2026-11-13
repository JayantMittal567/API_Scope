"""Generators Layer for ApiScope.

Provides capabilities for importing OpenAPI 3 specifications, generating
deterministic positive, negative, boundary, and AI-driven business-logic test suites,
and serializing collections to YAML.
"""

from apiscope.generators.ai_generator import (
    MAX_AI_GENERATED_TESTS,
    MAX_LLM_RESPONSE_BYTES,
    AiGeneratorService,
    LiveHttpLLMProvider,
    LLMConfig,
    LLMProviderProtocol,
    ProfileDrivenSynthesisProvider,
    build_ai_generation_prompt,
)
from apiscope.generators.openapi_importer import (
    MAX_GENERATED_TESTS,
    MAX_OPENAPI_FILE_BYTES,
    MAX_REF_DEPTH,
    EndpointSpec,
    OpenAPISpec,
    ParameterSpec,
    load_openapi_spec,
    parse_openapi_spec,
)
from apiscope.generators.project_profile import (
    MAX_PROFILE_FILE_BYTES,
    BusinessInvariantRule,
    ProjectProfile,
    RoleRule,
    StateTransitionRule,
    load_project_profile,
    parse_project_profile,
)
from apiscope.generators.rule_generator import (
    RuleBasedGenerator,
    synthesize_valid_value,
)
from apiscope.generators.service import (
    GeneratorService,
    dump_tests_to_yaml,
    test_definition_to_dict,
)

__all__ = [
    "MAX_AI_GENERATED_TESTS",
    "MAX_GENERATED_TESTS",
    "MAX_LLM_RESPONSE_BYTES",
    "MAX_OPENAPI_FILE_BYTES",
    "MAX_PROFILE_FILE_BYTES",
    "MAX_REF_DEPTH",
    "AiGeneratorService",
    "BusinessInvariantRule",
    "EndpointSpec",
    "GeneratorService",
    "LLMConfig",
    "LLMProviderProtocol",
    "LiveHttpLLMProvider",
    "OpenAPISpec",
    "ParameterSpec",
    "ProfileDrivenSynthesisProvider",
    "ProjectProfile",
    "RoleRule",
    "RuleBasedGenerator",
    "StateTransitionRule",
    "build_ai_generation_prompt",
    "dump_tests_to_yaml",
    "load_openapi_spec",
    "load_project_profile",
    "parse_openapi_spec",
    "parse_project_profile",
    "synthesize_valid_value",
    "test_definition_to_dict",
]
