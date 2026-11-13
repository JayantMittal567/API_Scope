"""Generator service and YAML serialization for rule-generated test collections.

Provides high-level orchestration for importing OpenAPI specifications,
generating test suites, serializing to YAML, and guaranteeing 100% round-trip
fidelity via TestCollection and TestDefinition loaders.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from apiscope.core.exceptions import DefinitionError
from apiscope.definitions.collection import (
    TestCollection,
    load_collection,
    validate_safe_path,
)
from apiscope.definitions.models import TestDefinition
from apiscope.generators.openapi_importer import (
    OpenAPISpec,
    load_openapi_spec,
)
from apiscope.generators.rule_generator import RuleBasedGenerator


def test_definition_to_dict(test_def: TestDefinition) -> dict[str, Any]:
    """Convert a TestDefinition model to a dictionary accepted by yaml_loader.

    Args:
        test_def: TestDefinition instance.

    Returns:
        Dictionary conforming to parse_test_definition schema.
    """
    method_val = (
        test_def.request.method.value
        if hasattr(test_def.request.method, "value")
        else str(test_def.request.method)
    )

    req_dict: dict[str, Any] = {
        "method": str(method_val).upper(),
        "url": test_def.request.url,
    }

    if test_def.request.headers:
        req_dict["headers"] = dict(test_def.request.headers)

    if test_def.request.params:
        req_dict["params"] = dict(test_def.request.params)

    if test_def.request.body is not None:
        req_dict["body"] = test_def.request.body

    if test_def.request.body_type is not None:
        body_type_val = (
            test_def.request.body_type.value
            if hasattr(test_def.request.body_type, "value")
            else str(test_def.request.body_type)
        )
        req_dict["body_type"] = str(body_type_val)

    if test_def.request.timeout is not None:
        req_dict["timeout"] = float(test_def.request.timeout)

    if test_def.request.max_retries is not None:
        req_dict["max_retries"] = int(test_def.request.max_retries)

    if test_def.request.follow_redirects is not None:
        req_dict["follow_redirects"] = bool(test_def.request.follow_redirects)

    if test_def.request.retry_non_idempotent is not None:
        req_dict["retry_non_idempotent"] = bool(test_def.request.retry_non_idempotent)

    source_val = (
        test_def.source.value
        if hasattr(test_def.source, "value")
        else str(test_def.source)
    )
    status_val = (
        test_def.status.value
        if hasattr(test_def.status, "value")
        else str(test_def.status)
    )

    data: dict[str, Any] = {
        "name": test_def.name,
        "module": test_def.module,
        "tags": list(test_def.tags),
        "source": str(source_val),
        "status": str(status_val),
        "request": req_dict,
    }

    if test_def.extract:
        data["extract"] = dict(test_def.extract)

    if test_def.assertions:
        data["assertions"] = list(test_def.assertions)

    return data


def dump_tests_to_yaml(
    tests: Sequence[TestDefinition],
    collection_name: str = "Generated Suite",
    module: str = "default",
) -> str:
    """Serialize a list of TestDefinitions to a valid collection YAML string.

    Args:
        tests: Sequence of TestDefinition models.
        collection_name: Name of the test collection.
        module: Primary module name for the collection.

    Returns:
        Formatted YAML string ready to write to disk.
    """
    collection_dict: dict[str, Any] = {
        "name": collection_name,
        "module": module,
        "tags": ["rule-generated"],
        "tests": [test_definition_to_dict(t) for t in tests],
    }
    return yaml.safe_dump(collection_dict, sort_keys=False)


class GeneratorService:
    """Application service for OpenAPI import and rule-based test generation."""

    def __init__(self, generator: RuleBasedGenerator | None = None) -> None:
        self.generator = generator or RuleBasedGenerator()

    def import_spec(
        self,
        spec_path: str | Path,
        output_path: str | Path | None = None,
        base_dir: Path | None = None,
    ) -> OpenAPISpec:
        """Load, validate, and optionally export normalized OpenAPI specification.

        Args:
            spec_path: Path to the OpenAPI YAML/JSON file.
            output_path: Optional file path to write normalized specification.
            base_dir: Base directory for path traversal validation.

        Returns:
            Normalized OpenAPISpec instance.
        """
        spec = load_openapi_spec(spec_path, base_dir=base_dir)

        if output_path is not None:
            out_p = Path(output_path)
            effective_base = (
                base_dir
                if base_dir is not None
                else (out_p.parent if out_p.is_absolute() else Path.cwd())
            )
            safe_out = validate_safe_path(base_dir=effective_base, ref_path=output_path)
            safe_out.parent.mkdir(parents=True, exist_ok=True)
            spec_dict = spec.to_dict()

            out_suffix = safe_out.suffix.lower()
            if out_suffix == ".json":
                safe_out.write_text(json.dumps(spec_dict, indent=2), encoding="utf-8")
            else:
                safe_out.write_text(
                    yaml.safe_dump(spec_dict, sort_keys=False), encoding="utf-8"
                )

        return spec

    def generate_from_spec(
        self,
        spec_path: str | Path,
        output_path: str | Path,
        module: str | None = None,
        base_dir: Path | None = None,
    ) -> TestCollection:
        """Generate test collection from an OpenAPI spec and save to YAML file.

        Args:
            spec_path: Path to the OpenAPI YAML/JSON file.
            output_path: Target path for the generated collection YAML.
            module: Optional module filter.
            base_dir: Base directory for path traversal validation.

        Returns:
            Loaded TestCollection guaranteed to pass round-trip validation.

        Raises:
            DefinitionError: If no tests are generated or specification is invalid.
        """
        spec = load_openapi_spec(spec_path, base_dir=base_dir)
        tests = self.generator.generate_tests(spec, module=module)

        if not tests:
            filter_msg = f" for module '{module}'" if module else ""
            raise DefinitionError(
                f"No tests generated from OpenAPI spec '{spec_path}'{filter_msg}."
            )

        out_p = Path(output_path)
        effective_base = (
            base_dir
            if base_dir is not None
            else (out_p.parent if out_p.is_absolute() else Path.cwd())
        )
        safe_out = validate_safe_path(base_dir=effective_base, ref_path=output_path)
        safe_out.parent.mkdir(parents=True, exist_ok=True)

        coll_name = f"{spec.title} Generated Suite"
        coll_module = (
            module
            if module
            else (spec.endpoints[0].module if spec.endpoints else "default")
        )

        yaml_content = dump_tests_to_yaml(
            tests, collection_name=coll_name, module=coll_module
        )
        safe_out.write_text(yaml_content, encoding="utf-8")

        # Load back to guarantee 100% round-trip fidelity
        collection = load_collection(safe_out, base_dir=safe_out.parent)
        return collection
