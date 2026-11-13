"""Unit tests for path normalization and OpenAPI path template matching.

Covers: FR-31, FR-35, NFR-06.
"""

from apiscope.analysis.path_normalizer import (
    compile_spec_path_regex,
    extract_url_path,
    match_path_to_spec,
    normalize_unmatched_path,
)


def test_extract_url_path_strips_base_url_and_query_params() -> None:
    """Covers: FR-31, FR-35.

    Verifies extraction of normalized path component from varied URLs.
    """
    assert (
        extract_url_path("{{base_url}}/orders/101?status=active#details")
        == "/orders/101"
    )
    assert (
        extract_url_path("{{ base_url }}/products/v1-catalog/?page=2")
        == "/products/v1-catalog"
    )
    assert (
        extract_url_path("http://localhost:8000/api/v1/users/42") == "/api/v1/users/42"
    )
    assert extract_url_path("orders/items/") == "/orders/items"
    assert extract_url_path("/") == "/"
    assert extract_url_path("") == "/"
    assert extract_url_path("http://api.example.com") == "/"


def test_match_path_to_spec_prioritizes_static_path_over_parameterized_template() -> (
    None
):
    """Covers: FR-31, FR-35.

    Verifies that static paths match before parameterized templates to prevent
    routes like /products/recommendations being swallowed by {product_id}.
    """
    spec_paths = [
        "/products/{product_id}",
        "/products/recommendations",
        "/products/v1-catalog",
        "/products/{product_id}/reviews",
        "/orders/{order_id}",
    ]

    # Exact static routes match their specific endpoints
    assert (
        match_path_to_spec("/products/recommendations", spec_paths)
        == "/products/recommendations"
    )
    assert (
        match_path_to_spec("/products/v1-catalog", spec_paths) == "/products/v1-catalog"
    )
    rec_url = "http://localhost:8000/products/recommendations?sort=asc"
    assert match_path_to_spec(rec_url, spec_paths) == "/products/recommendations"

    # Dynamic ID routes match the parameterized template
    assert match_path_to_spec("/products/42", spec_paths) == "/products/{product_id}"
    assert (
        match_path_to_spec("/products/PROD-999", spec_paths) == "/products/{product_id}"
    )
    assert (
        match_path_to_spec("/products/42/reviews", spec_paths)
        == "/products/{product_id}/reviews"
    )

    # Unmatched routes return None
    assert match_path_to_spec("/customers/99", spec_paths) is None
    assert match_path_to_spec("/products/42/reviews/extra", spec_paths) is None


def test_normalize_unmatched_path_replaces_numeric_and_uuid_segments() -> None:
    """Covers: FR-35.

    Verifies heuristic normalization replaces numeric IDs and UUIDs with {id}.
    """
    assert normalize_unmatched_path("/users/123") == "/users/{id}"
    assert (
        normalize_unmatched_path("/items/550e8400-e29b-41d4-a716-446655440000")
        == "/items/{id}"
    )
    assert (
        normalize_unmatched_path("/items/550e8400-e29b-41d4-a716-446655440000/history")
        == "/items/{id}/history"
    )
    assert normalize_unmatched_path("/orders/ord_12345/cancel") == "/orders/{id}/cancel"
    assert (
        normalize_unmatched_path("/orders/abc123456789012345678901234")
        == "/orders/{id}"
    )
    assert normalize_unmatched_path("/static/about") == "/static/about"
    assert normalize_unmatched_path("/") == "/"


def test_compile_spec_path_regex_matching() -> None:
    """Covers: FR-31, FR-35.

    Verifies regex compilation for single and multi-parameter paths.
    """
    pattern = compile_spec_path_regex("/orgs/{org_id}/teams/{team_id}")
    assert pattern.match("/orgs/acme/teams/devs") is not None
    assert pattern.match("/orgs/123/teams/456") is not None
    assert pattern.match("/orgs/acme/teams/devs/members") is None
    assert pattern.match("/other/acme/teams/devs") is None
