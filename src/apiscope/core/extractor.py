"""Response value extraction engine supporting request chaining across test steps.

Implements ExtractorProtocol to extract native-typed values from HTTP response
bodies using JSONPath expressions, with payload size protection and detailed error
containment for downstream variable chaining.
"""

from collections.abc import Mapping
from typing import Any

import jsonpath_ng

from apiscope.core.contracts import ExtractorProtocol
from apiscope.core.logging_config import get_logger
from apiscope.core.models import HttpResponse

MAX_EXTRACT_BODY_BYTES: int = 5_242_880  # 5 MB


class Extractor(ExtractorProtocol):
    """Concrete response extractor extracting values using JSONPath expressions."""

    def __init__(self) -> None:
        """Initialize the Extractor with project logger."""
        self.logger = get_logger("apiscope.core.extractor")

    def extract(self, response: HttpResponse, expression: str) -> Any:
        """Extract a single value from an HTTP response using a query expression.

        Conforms to ExtractorProtocol.

        Args:
            response: Observed HTTP response.
            expression: Extraction locator (JSONPath, 'status', 'header.<name>').

        Returns:
            Extracted value (preserving native type), or None if not found.
        """
        results, _ = self.extract_all(response, {"_temp": expression})
        return results.get("_temp")

    def extract_with_errors(
        self,
        response: HttpResponse,
        rules: Mapping[str, str] | None,
    ) -> tuple[dict[str, Any], list[str]]:
        """Alias for extract_all returning extracted values and diagnostic errors."""
        return self.extract_all(response, rules)

    def extract_all(
        self,
        response: HttpResponse,
        rules: Mapping[str, str] | None,
    ) -> tuple[dict[str, Any], list[str]]:
        """Extract multiple values from an HTTP response given expression rules.

        Args:
            response: Observed HTTP response.
            rules: Mapping of variable names to extraction expressions.

        Returns:
            Tuple of (extracted_variables_dict, list_of_extraction_error_messages).
        """
        if not rules:
            return {}, []

        extracted: dict[str, Any] = {}
        errors: list[str] = []

        # Check for non-body rules first (e.g. status, header)
        body_rules: dict[str, str] = {}
        for var_name, expr in rules.items():
            clean_expr = str(expr).strip()
            expr_lower = clean_expr.lower()

            if expr_lower in ("status", "status_code"):
                extracted[var_name] = response.status_code
            elif expr_lower.startswith("headers.") or expr_lower.startswith("header."):
                header_key = clean_expr.split(".", 1)[1].strip()
                # Case-insensitive header lookup
                header_match = next(
                    (
                        v
                        for k, v in response.headers.items()
                        if k.lower() == header_key.lower()
                    ),
                    None,
                )
                if header_match is not None:
                    extracted[var_name] = header_match
                else:
                    errors.append(
                        f"Header '{header_key}' for variable "
                        f"'{var_name}' not found in response"
                    )
            else:
                body_rules[var_name] = clean_expr

        if not body_rules:
            return extracted, errors

        # Limit enforcement: check payload size before parsing body
        raw_bytes_len = (
            response.size_bytes
            if response.size_bytes > 0
            else len(response.body.encode("utf-8"))
            if response.body
            else 0
        )

        if raw_bytes_len > MAX_EXTRACT_BODY_BYTES:
            msg = (
                f"Response body size ({raw_bytes_len} bytes) exceeds maximum "
                f"extraction limit of {MAX_EXTRACT_BODY_BYTES} bytes"
            )
            self.logger.warning(msg)
            errors.append(msg)
            return extracted, errors

        # Check for empty body
        if not response.body or not response.body.strip():
            for var_name in body_rules:
                errors.append(
                    f"Response body is not valid JSON for extraction of '{var_name}'"
                )
            return extracted, errors

        # Parse JSON body once
        try:
            json_data = response.json()
        except Exception:
            for var_name in body_rules:
                errors.append(
                    f"Response body is not valid JSON for extraction of '{var_name}'"
                )
            return extracted, errors

        # Evaluate JSONPath for each rule
        for var_name, expr in body_rules.items():
            try:
                parsed_expr = jsonpath_ng.parse(expr)
            except Exception as parse_err:
                errors.append(
                    f"Invalid JSONPath expression '{expr}' for variable "
                    f"'{var_name}': {parse_err}"
                )
                continue

            try:
                matches = parsed_expr.find(json_data)
            except Exception as find_err:
                errors.append(
                    f"Error evaluating JSONPath '{expr}' for variable "
                    f"'{var_name}': {find_err}"
                )
                continue

            if not matches:
                errors.append(
                    f"JSONPath '{expr}' for variable '{var_name}' "
                    "matched no values in response"
                )
            else:
                # Extract first match preserving native Python type
                extracted[var_name] = matches[0].value

        return extracted, errors
