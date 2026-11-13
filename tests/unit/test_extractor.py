"""Unit tests for M4 Extractor component.

Tests response payload extraction using JSONPath, type preservation, boundary
enforcement, and robust error containment.
"""

import json

from apiscope.core.extractor import MAX_EXTRACT_BODY_BYTES, Extractor
from apiscope.core.models import HttpResponse


def test_extract_scalar_types_preserves_native_types() -> None:
    """Covers: FR-16

    Verify that Extractor preserves exact Python primitive types
    (int, float, bool, str).
    """
    extractor = Extractor()
    payload = {
        "user_id": 12345,
        "score": 98.75,
        "is_active": True,
        "name": "ApiScope Tester",
    }
    response = HttpResponse(
        status_code=200,
        headers={"content-type": "application/json"},
        body=json.dumps(payload),
    )

    rules = {
        "uid": "$.user_id",
        "user_score": "$.score",
        "active": "$.is_active",
        "username": "$.name",
    }

    extracted, errors = extractor.extract_all(response, rules)

    assert errors == []
    assert extracted["uid"] == 12345
    assert isinstance(extracted["uid"], int) and not isinstance(extracted["uid"], bool)
    assert extracted["user_score"] == 98.75
    assert isinstance(extracted["user_score"], float)
    assert extracted["active"] is True
    assert isinstance(extracted["active"], bool)
    assert extracted["username"] == "ApiScope Tester"
    assert isinstance(extracted["username"], str)

    # Test single-expression protocol method
    assert extractor.extract(response, "$.user_id") == 12345


def test_extract_nested_dict_and_list() -> None:
    """Covers: FR-16

    Verify that Extractor preserves complex container structures (dict and list).
    """
    extractor = Extractor()
    payload = {
        "data": {
            "profile": {"department": "Engineering", "level": 4},
            "tags": ["python", "testing", "fastapi"],
        }
    }
    response = HttpResponse(
        status_code=200,
        headers={"content-type": "application/json"},
        body=json.dumps(payload),
    )

    rules = {
        "profile_obj": "$.data.profile",
        "first_tag": "$.data.tags[0]",
        "all_tags": "$.data.tags",
    }

    extracted, errors = extractor.extract_all(response, rules)

    assert errors == []
    assert extracted["profile_obj"] == {"department": "Engineering", "level": 4}
    assert isinstance(extracted["profile_obj"], dict)
    assert extracted["first_tag"] == "python"
    assert extracted["all_tags"] == ["python", "testing", "fastapi"]
    assert isinstance(extracted["all_tags"], list)


def test_extract_multiple_matches_returns_first_match() -> None:
    """Covers: FR-16

    Verify that when a JSONPath expression matches multiple items,
    the first match is returned.
    """
    extractor = Extractor()
    payload = {
        "items": [
            {"id": "item-001", "price": 10},
            {"id": "item-002", "price": 20},
            {"id": "item-003", "price": 30},
        ]
    }
    response = HttpResponse(
        status_code=200,
        headers={"content-type": "application/json"},
        body=json.dumps(payload),
    )

    rules = {"first_id": "$.items[*].id"}
    extracted, errors = extractor.extract_all(response, rules)

    assert errors == []
    assert extracted["first_id"] == "item-001"


def test_extract_missing_path_records_error_and_omits_key() -> None:
    """Covers: FR-16, NFR-02

    Verify that non-matching JSONPath queries record an extraction error
    and omit the variable.
    """
    extractor = Extractor()
    payload = {"valid": "ok"}
    response = HttpResponse(
        status_code=200,
        headers={"content-type": "application/json"},
        body=json.dumps(payload),
    )

    rules = {
        "good": "$.valid",
        "missing_var": "$.non_existent_field.value",
    }
    extracted, errors = extractor.extract_all(response, rules)

    assert extracted == {"good": "ok"}
    assert "missing_var" not in extracted
    assert len(errors) == 1
    assert "matched no values in response" in errors[0]
    assert "missing_var" in errors[0]


def test_extract_invalid_json_body_records_error_without_crash() -> None:
    """Covers: FR-16, NFR-02

    Verify that empty or malformed JSON response bodies gracefully record
    errors without crashing.
    """
    extractor = Extractor()

    # Empty body
    empty_resp = HttpResponse(status_code=200, body="")
    extracted_empty, errors_empty = extractor.extract_all(
        empty_resp, {"target": "$.id"}
    )
    assert extracted_empty == {}
    assert len(errors_empty) == 1
    assert "Response body is not valid JSON" in errors_empty[0]

    # Non-JSON HTML/text body
    html_resp = HttpResponse(
        status_code=500,
        body="<html><body>Internal Server Error</body></html>",
    )
    extracted_html, errors_html = extractor.extract_all(
        html_resp, {"token": "$.auth.token"}
    )
    assert extracted_html == {}
    assert len(errors_html) == 1
    assert "Response body is not valid JSON for extraction of 'token'" in errors_html[0]


def test_extract_body_exceeding_max_bytes_limit_fails_gracefully() -> None:
    """Covers: FR-16, NFR-02

    Verify that payloads exceeding MAX_EXTRACT_BODY_BYTES abort extraction
    gracefully without crash.
    """
    extractor = Extractor()
    oversized_size = MAX_EXTRACT_BODY_BYTES + 1024
    response = HttpResponse(
        status_code=200,
        body="large_body_stub",
        size_bytes=oversized_size,
    )

    extracted, errors = extractor.extract_all(response, {"token": "$.token"})
    assert extracted == {}
    assert len(errors) == 1
    assert (
        f"exceeds maximum extraction limit of {MAX_EXTRACT_BODY_BYTES} bytes"
        in errors[0]
    )


def test_extract_status_code_and_header_rules() -> None:
    """Covers: FR-16

    Verify that Extractor supports non-body extraction expressions
    ('status', 'header.<name>').
    """
    extractor = Extractor()
    response = HttpResponse(
        status_code=204,
        headers={"ETag": "W/12345", "X-Custom": "custom-value"},
        body="",
    )

    rules = {
        "status_val": "status",
        "etag_val": "headers.ETag",
        "custom_val": "header.x-custom",
        "missing_h": "header.missing-key",
    }

    extracted, errors = extractor.extract_with_errors(response, rules)

    assert extracted["status_val"] == 204
    assert extracted["etag_val"] == "W/12345"
    assert extracted["custom_val"] == "custom-value"
    assert "missing_h" not in extracted
    assert len(errors) == 1
    assert "Header 'missing-key' for variable 'missing_h' not found" in errors[0]


def test_extract_invalid_jsonpath_syntax_records_error() -> None:
    """Covers: FR-16, NFR-02

    Verify that malformed JSONPath syntax is captured as an extraction error.
    """
    extractor = Extractor()
    response = HttpResponse(
        status_code=200,
        headers={"content-type": "application/json"},
        body='{"valid": 1}',
    )

    extracted, errors = extractor.extract_all(response, {"broken": "$[syntax error"})
    assert extracted == {}
    assert len(errors) == 1
    assert "Invalid JSONPath expression" in errors[0]
