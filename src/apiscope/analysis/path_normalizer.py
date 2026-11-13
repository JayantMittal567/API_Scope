"""Path normalization and OpenAPI path template matching utilities.

Provides deterministic URL path extraction, OpenAPI parameter template regex
compilation, static-priority route matching, and heuristic variable normalization.
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Sequence


def extract_url_path(url_or_path: str) -> str:
    """Extract and normalize the URL path component from a URL or raw path string.

    Strips leading environment variable templates (e.g. {{base_url}}), query
    parameters, and URL fragments. Guarantees a leading slash and strips any
    trailing slashes (except for the root path '/').

    Args:
        url_or_path: Concrete URL or path string.

    Returns:
        Normalized path string starting with '/' without trailing slash.
    """
    clean_str = str(url_or_path).strip()
    # Strip leading template variables like {{base_url}} or {{ base_url }}
    clean_str = re.sub(r"^\{\{\s*[a-zA-Z0-9_-]+\s*\}\}", "", clean_str).strip()

    parts = urllib.parse.urlsplit(clean_str)
    path = parts.path
    if not path:
        # Fallback if urlsplit did not treat clean_str as having a path
        raw_path = clean_str.split("?")[0].split("#")[0]
        if not raw_path.startswith("http://") and not raw_path.startswith("https://"):
            path = raw_path
        else:
            path = "/"

    if not path.startswith("/"):
        path = "/" + path

    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return path


def compile_spec_path_regex(template_path: str) -> re.Pattern[str]:
    """Compile an OpenAPI template path into an anchored matching regex.

    Converts parameterized segments like '{order_id}' into '[^/]+'.

    Args:
        template_path: OpenAPI path template (e.g. '/orders/{order_id}/cancel').

    Returns:
        Compiled anchored regular expression.
    """
    clean_template = template_path.strip()
    if len(clean_template) > 1 and clean_template.endswith("/"):
        clean_template = clean_template.rstrip("/")
    if not clean_template.startswith("/"):
        clean_template = "/" + clean_template

    pattern_parts: list[str] = []
    last_idx = 0
    for match in re.finditer(r"\{[a-zA-Z0-9_-]+\}", clean_template):
        literal = clean_template[last_idx : match.start()]
        pattern_parts.append(re.escape(literal))
        pattern_parts.append(r"[^/]+")
        last_idx = match.end()

    pattern_parts.append(re.escape(clean_template[last_idx:]))
    regex_str = "^" + "".join(pattern_parts) + "$"
    return re.compile(regex_str)


def _spec_path_specificity(path: str) -> tuple[int, int, int]:
    """Calculate specificity score for sorting template paths.

    Higher scores indicate higher specificity:
    - Number of literal (non-parameter) segments (descending)
    - Total character length of literal segments (descending)
    - Number of parameter segments (ascending)
    """
    clean = path.strip("/")
    segments = clean.split("/") if clean else []
    literal_segs = 0
    literal_len = 0
    param_segs = 0

    for seg in segments:
        if seg.startswith("{") and seg.endswith("}"):
            param_segs += 1
        else:
            literal_segs += 1
            literal_len += len(seg)

    return (literal_segs, literal_len, -param_segs)


def match_path_to_spec(concrete_path: str, spec_paths: Sequence[str]) -> str | None:
    """Match a concrete URL path against a collection of OpenAPI spec path templates.

    Prioritizes exact static path matches before parameterized templates to prevent
    literal routes (e.g. '/products/recommendations') from being swallowed by
    parameterized templates (e.g. '/products/{product_id}').

    Args:
        concrete_path: The request path to match (e.g. '/products/recommendations').
        spec_paths: Sequence of OpenAPI path templates.

    Returns:
        Matching template path from spec_paths, or None if no match is found.
    """
    norm_concrete = extract_url_path(concrete_path)

    # 1. Exact static matches first (case-sensitive as per HTTP specification)
    for sp in spec_paths:
        norm_sp = extract_url_path(sp)
        if "{" not in norm_sp and norm_concrete == norm_sp:
            return sp

    # 2. Parameterized templates sorted by specificity descending
    parameterized = [sp for sp in spec_paths if "{" in sp]
    parameterized.sort(key=_spec_path_specificity, reverse=True)

    for sp in parameterized:
        pattern = compile_spec_path_regex(sp)
        if pattern.match(norm_concrete):
            return sp

    return None


def normalize_unmatched_path(concrete_path: str) -> str:
    """Apply heuristic normalization to dynamic path segments without an OpenAPI spec.

    Replaces purely numeric segments (/users/123 -> /users/{id}), UUID segments
    (/items/550e8400... -> /items/{id}), hex/object IDs, and prefixed IDs
    (ord_12345 -> {id}) with '{id}'.

    Args:
        concrete_path: Concrete URL or path string.

    Returns:
        Heuristically normalized template path.
    """
    norm_path = extract_url_path(concrete_path)
    if norm_path == "/":
        return "/"

    segments = norm_path.strip("/").split("/")
    uuid_regex = re.compile(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )
    hex_id_regex = re.compile(r"^[0-9a-fA-F]{24,}$")
    prefixed_id_regex = re.compile(r"^[a-zA-Z]{2,}[_-][0-9a-zA-Z]+$")

    normalized_segments: list[str] = []
    for seg in segments:
        if (
            seg.isdigit()
            or uuid_regex.match(seg)
            or hex_id_regex.match(seg)
            or (prefixed_id_regex.match(seg) and any(c.isdigit() for c in seg))
        ):
            normalized_segments.append("{id}")
        else:
            normalized_segments.append(seg)

    return "/" + "/".join(normalized_segments)
