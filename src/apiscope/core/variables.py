"""Variable resolution engine for template substitution across test definitions.

Provides recursive variable replacement across strings, nested dictionaries,
lists, headers, query parameters, and request payloads while preserving data types
and preventing mutation of original definitions.
"""

import copy
import re
from collections.abc import Mapping
from typing import Any

from apiscope.core.contracts import VariableResolverProtocol
from apiscope.core.exceptions import VariableResolutionError

# Matches {{variable_name}} with optional surrounding whitespace
_VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")
_EXACT_VARIABLE_PATTERN = re.compile(r"^\s*\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}\s*$")


class VariableResolver(VariableResolverProtocol):
    """Engine for resolving templated variables within data structures."""

    def resolve(self, template: str, context: Mapping[str, Any]) -> str:
        """Substitute variable expressions in a template string using context.

        Args:
            template: The string containing variable expressions.
            context: Key-value mapping of available variables.

        Returns:
            The string with resolved variable values.

        Raises:
            VariableResolutionError: If a referenced variable is missing from context.
        """
        return self.resolve_string(template, context)

    def resolve_string(self, text: str, context: Mapping[str, Any]) -> str:
        """Resolve all {{variable}} placeholders within a string to string values.

        Args:
            text: Input string potentially containing {{var}} placeholders.
            context: Mapping of variable names to values.

        Returns:
            String with all placeholders replaced.

        Raises:
            VariableResolutionError: If a variable is missing in context.
        """
        if not isinstance(text, str) or "{{" not in text:
            return text

        def _replacer(match: re.Match[str]) -> str:
            var_name = match.group(1)
            if var_name not in context:
                raise VariableResolutionError(
                    f"Missing required variable: '{var_name}'"
                )
            val = context[var_name]
            return str(val) if val is not None else ""

        return _VARIABLE_PATTERN.sub(_replacer, text)

    def resolve_value(self, value: Any, context: Mapping[str, Any]) -> Any:
        """Resolve variables while preserving native types (int, bool, float) if exact.

        Args:
            value: Value of any type to resolve.
            context: Variable context mapping.

        Returns:
            Resolved value preserving original data types when possible.

        Raises:
            VariableResolutionError: If a referenced variable is missing.
        """
        if isinstance(value, str):
            if "{{" not in value:
                return value

            # Check if string is an exact single variable (e.g. "{{count}}")
            exact_match = _EXACT_VARIABLE_PATTERN.match(value)
            if exact_match:
                var_name = exact_match.group(1)
                if var_name not in context:
                    raise VariableResolutionError(
                        f"Missing required variable: '{var_name}'"
                    )
                return context[var_name]

            # Otherwise, perform substring replacement
            return self.resolve_string(value, context)

        if isinstance(value, dict):
            resolved_dict: dict[str, Any] = {}
            for k, v in value.items():
                resolved_key = (
                    self.resolve_string(k, context) if isinstance(k, str) else k
                )
                resolved_dict[resolved_key] = self.resolve_value(v, context)
            return resolved_dict

        if isinstance(value, list):
            return [self.resolve_value(item, context) for item in value]

        if isinstance(value, tuple):
            return tuple(self.resolve_value(item, context) for item in value)

        # Non-string primitives (int, float, bool, None) remain unchanged
        return value

    def resolve_data(self, data: Any, context: Mapping[str, Any]) -> Any:
        """Deeply resolve a data structure without mutating the original input.

        Args:
            data: Arbitrary data structure (dict, list, primitive).
            context: Mapping of variable names to values.

        Returns:
            New data structure with all variables resolved.
        """
        cloned = copy.deepcopy(data)
        return self.resolve_value(cloned, context)
