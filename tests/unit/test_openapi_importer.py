"""Unit tests for OpenAPI 3 importer and normalized models.

Covers: FR-19, NFR-02, NFR-04, NFR-06.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apiscope.core.exceptions import DefinitionError, PathTraversalError
from apiscope.generators.openapi_importer import (
    EndpointSpec,
    OpenAPISpec,
    load_openapi_spec,
    parse_openapi_spec,
)


def test_load_openapi_yaml_and_json_with_utf8_bom(tmp_path: Path) -> None:
    """Covers: FR-19
    Verify loading both YAML and JSON OpenAPI specifications, including UTF-8 BOM,
    and verifying OpenAPISpec, EndpointSpec, module, requires_auth, and deprecated.
    """
    yaml_content = """openapi: "3.0.1"
info:
  title: "Inventory API"
  version: "2.1.0"
servers:
  - url: "https://api.inventory.example.com/v1"
paths:
  /items:
    get:
      tags:
        - "Warehouse Stock"
      summary: "List stock items"
      deprecated: true
      security:
        - bearerAuth: []
      responses:
        "200":
          description: "Success"
          content:
            application/json:
              schema:
                type: "array"
                items:
                  type: "string"
components:
  securitySchemes:
    bearerAuth:
      type: "http"
      scheme: "bearer"
"""
    # Write YAML file with UTF-8 BOM
    yaml_file = tmp_path / "spec_bom.yaml"
    bom_bytes = b"\xef\xbb\xbf" + yaml_content.encode("utf-8")
    yaml_file.write_bytes(bom_bytes)

    spec_yaml = load_openapi_spec(yaml_file, base_dir=tmp_path)
    assert isinstance(spec_yaml, OpenAPISpec)
    assert spec_yaml.title == "Inventory API"
    assert spec_yaml.version == "2.1.0"
    assert spec_yaml.openapi_version == "3.0.1"
    assert len(spec_yaml.endpoints) == 1
    assert "Warehouse Stock" not in spec_yaml.endpoints[0].module  # slugified
    assert spec_yaml.endpoints[0].module == "warehouse-stock"
    assert spec_yaml.endpoints[0].deprecated is True
    assert spec_yaml.endpoints[0].requires_auth is True
    assert spec_yaml.endpoints[0].success_status == 200

    # Test JSON spec loading
    json_data = {
        "openapi": "3.1.0",
        "info": {"title": "Users Service", "version": "1.0.0"},
        "paths": {
            "/api/v1/users": {
                "post": {
                    "summary": "Create user",
                    "responses": {"201": {"description": "Created"}},
                }
            }
        },
    }
    json_file = tmp_path / "spec.json"
    json_file.write_text(json.dumps(json_data), encoding="utf-8")

    spec_json = load_openapi_spec(json_file, base_dir=tmp_path)
    assert spec_json.title == "Users Service"
    assert spec_json.version == "1.0.0"
    assert spec_json.openapi_version == "3.1.0"
    assert len(spec_json.endpoints) == 1
    # Fallback module extraction from path after stripping /api/v1
    assert spec_json.endpoints[0].module == "users"
    assert spec_json.endpoints[0].deprecated is False
    assert spec_json.endpoints[0].requires_auth is False
    assert spec_json.endpoints[0].success_status == 201

    # Verify to_dict and filter_by_module methods
    spec_dict = spec_json.to_dict()
    assert spec_dict["title"] == "Users Service"
    filtered = spec_json.filter_by_module("users")
    assert len(filtered) == 1
    assert isinstance(filtered[0], EndpointSpec)
    assert filtered[0].to_dict()["module"] == "users"


def test_resolve_local_component_refs_for_schemas_params_and_bodies() -> None:
    """Covers: FR-19
    Verify local $ref pointers (#/components/schemas/..., #/components/parameters/...)
    are recursively resolved.
    """
    raw_spec = {
        "openapi": "3.0.0",
        "info": {"title": "Ref Test API", "version": "1.0.0"},
        "paths": {
            "/widgets/{widget_id}": {
                "parameters": [{"$ref": "#/components/parameters/WidgetId"}],
                "put": {
                    "requestBody": {"$ref": "#/components/requestBodies/WidgetBody"},
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Widget"}
                                }
                            },
                        }
                    },
                },
            }
        },
        "components": {
            "parameters": {
                "WidgetId": {
                    "name": "widget_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "integer"},
                }
            },
            "requestBodies": {
                "WidgetBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Widget"}
                        }
                    },
                }
            },
            "schemas": {
                "BaseEntity": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}},
                },
                "Widget": {
                    "type": "object",
                    "properties": {
                        "base": {"$ref": "#/components/schemas/BaseEntity"},
                        "name": {"type": "string"},
                    },
                },
            },
        },
    }

    spec = parse_openapi_spec(raw_spec)
    assert len(spec.endpoints) == 1
    ep = spec.endpoints[0]
    assert len(ep.parameters) == 1
    assert ep.parameters[0].name == "widget_id"
    assert ep.parameters[0].location == "path"

    assert ep.request_body_required is True
    assert ep.request_body_schema is not None
    assert "properties" in ep.request_body_schema
    # Verify nested ref BaseEntity was resolved
    base_prop = ep.request_body_schema["properties"]["base"]
    assert base_prop["type"] == "object"
    assert "id" in base_prop["properties"]

    assert ep.response_schema is not None
    assert "properties" in ep.response_schema


def test_circular_or_deep_ref_raises_definition_error() -> None:
    """Covers: FR-19, NFR-02
    Verify cyclic or excessively deep $ref references raise DefinitionError rather
    than crashing with RecursionError.
    """
    circular_spec = {
        "openapi": "3.0.0",
        "info": {"title": "Circular API", "version": "1.0.0"},
        "paths": {
            "/nodes": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/NodeA"}
                                }
                            }
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "NodeA": {
                    "type": "object",
                    "properties": {"b": {"$ref": "#/components/schemas/NodeB"}},
                },
                "NodeB": {
                    "type": "object",
                    "properties": {"a": {"$ref": "#/components/schemas/NodeA"}},
                },
            }
        },
    }

    with pytest.raises(DefinitionError) as exc_info:
        parse_openapi_spec(circular_spec)
    assert "Circular or excessively deep $ref detected" in str(exc_info.value)

    # Broken pointer test
    broken_spec = {
        "openapi": "3.0.0",
        "info": {"title": "Broken API", "version": "1.0.0"},
        "paths": {
            "/items": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/NonExistent"
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        "components": {"schemas": {}},
    }
    with pytest.raises(DefinitionError) as broken_exc:
        parse_openapi_spec(broken_spec)
    assert "Unsupported or unresolvable $ref" in str(broken_exc.value)


def test_invalid_openapi_version_or_missing_paths_raises_definition_error() -> None:
    """Covers: FR-19, NFR-02
    Verify non-OpenAPI 3 documents (Swagger 2.0) or invalid structures are rejected.
    """
    swagger_spec = {
        "swagger": "2.0",
        "info": {"title": "Legacy API", "version": "1.0"},
        "paths": {},
    }
    with pytest.raises(DefinitionError) as exc_ver:
        parse_openapi_spec(swagger_spec)
    assert "Unsupported or missing OpenAPI version" in str(exc_ver.value)

    missing_paths = {
        "openapi": "3.0.2",
        "info": {"title": "No Paths API", "version": "1.0"},
        "paths": {},
    }
    with pytest.raises(DefinitionError) as exc_paths:
        parse_openapi_spec(missing_paths)
    assert "Missing or invalid 'paths'" in str(exc_paths.value)

    not_a_dict = "not a dictionary mapping"
    with pytest.raises(DefinitionError) as exc_dict:
        parse_openapi_spec(not_a_dict)
    assert "OpenAPI specification must be a dictionary mapping" in str(exc_dict.value)


def test_openapi_file_size_limit_and_path_traversal_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Covers: FR-19, NFR-04
    Verify PathTraversalError on traversal attempts and DefinitionError when
    file size exceeds MAX_OPENAPI_FILE_BYTES.
    """
    # Traversal sequence '../'
    with pytest.raises(PathTraversalError):
        load_openapi_spec("../outside.yaml", base_dir=tmp_path)

    valid_spec = tmp_path / "valid.yaml"
    valid_spec.write_text(
        "openapi: '3.0.0'\n"
        "info:\n"
        "  title: T\n"
        "  version: '1'\n"
        "paths:\n"
        "  /t:\n"
        "    get:\n"
        "      responses:\n"
        "        '200':\n"
        "          description: ok\n",
        encoding="utf-8",
    )

    # Missing file
    with pytest.raises(DefinitionError) as exc_missing:
        load_openapi_spec(tmp_path / "does_not_exist.yaml", base_dir=tmp_path)
    assert "OpenAPI spec file not found" in str(exc_missing.value)

    # Size limit check
    large_spec = tmp_path / "large.yaml"
    large_spec.write_text("dummy", encoding="utf-8")
    monkeypatch.setattr(
        "apiscope.generators.openapi_importer.MAX_OPENAPI_FILE_BYTES", 2
    )

    with pytest.raises(DefinitionError) as exc_size:
        load_openapi_spec(large_spec, base_dir=tmp_path)
    assert "exceeds maximum size" in str(exc_size.value)


def test_openapi_external_ref_and_malformed_yaml(tmp_path: Path) -> None:
    """Covers: FR-19, NFR-02
    Verify external refs and malformed YAML raise DefinitionError.
    """
    ext_spec = {
        "openapi": "3.0.0",
        "info": {"title": "Ext Ref", "version": "1.0"},
        "paths": {
            "/ext": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "https://example.com/schemas/User.json"
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
    }
    with pytest.raises(DefinitionError) as exc_ext:
        parse_openapi_spec(ext_spec)
    assert "Unsupported or unresolvable $ref" in str(exc_ext.value)

    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text(":\n  invalid: [yaml\n", encoding="utf-8")
    with pytest.raises(DefinitionError) as exc_yaml:
        load_openapi_spec(bad_yaml)
    assert "Malformed YAML/JSON" in str(exc_yaml.value)

    missing_title = {
        "openapi": "3.0.0",
        "info": {"title": "", "version": ""},
        "paths": {"/t": {"get": {"responses": {"200": {"description": "ok"}}}}},
    }
    with pytest.raises(DefinitionError) as exc_info:
        parse_openapi_spec(missing_title)
    assert "non-empty 'title' and 'version'" in str(exc_info.value)
