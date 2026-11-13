"""Safe YAML loader and schema validator for ApiScope test definitions.

Enforces structural contracts, validates HTTP methods and body formats, prevents
arbitrary code execution through safe loading, and reports descriptive errors.
"""

from pathlib import Path
from typing import Any

import yaml

from apiscope.core.exceptions import DefinitionError
from apiscope.core.models import BodyType, HttpMethod
from apiscope.definitions.models import (
    DefinitionSource,
    DefinitionStatus,
    TestDefinition,
    TestRequestDefinition,
)


def parse_test_definition(
    data: Any,
    file_path: Path | None = None,
) -> TestDefinition:
    """Parse and validate a dictionary into a TestDefinition model.

    Args:
        data: Raw dictionary parsed from YAML.
        file_path: Optional path to the source file for error context.

    Returns:
        Validated TestDefinition instance.

    Raises:
        DefinitionError: If required fields are missing or data types are invalid.
    """
    origin = f" in '{file_path}'" if file_path else ""

    if not isinstance(data, dict):
        raise DefinitionError(
            f"Test definition{origin} is empty or not a valid dictionary mapping."
        )

    # 1. Validate required root fields: name, module, request
    name = data.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        raise DefinitionError(f"Missing or invalid required field 'name'{origin}.")
    name = name.strip()

    module = data.get("module")
    if not module or not isinstance(module, str) or not module.strip():
        raise DefinitionError(f"Missing or invalid required field 'module'{origin}.")
    module = module.strip()

    req_data = data.get("request")
    if not req_data or not isinstance(req_data, dict):
        raise DefinitionError(f"Missing or invalid required field 'request'{origin}.")

    # 2. Validate required request fields: method, url
    method_raw = req_data.get("method")
    if not method_raw or not isinstance(method_raw, str) or not method_raw.strip():
        raise DefinitionError(
            f"Missing required field 'request.method' in test '{name}'{origin}."
        )
    try:
        method = HttpMethod.from_string(method_raw)
    except Exception as err:
        raise DefinitionError(
            f"Unsupported HTTP method '{method_raw}' in test '{name}'{origin}: {err}"
        ) from err

    url = req_data.get("url")
    if not url or not isinstance(url, str) or not url.strip():
        raise DefinitionError(
            f"Missing or invalid required field 'request.url' in test '{name}'{origin}."
        )
    url = url.strip()

    # 3. Validate optional request fields
    headers = req_data.get("headers", {})
    if headers is None:
        headers = {}
    if not isinstance(headers, dict):
        raise DefinitionError(
            f"'request.headers' must be a dictionary in test '{name}'{origin}."
        )

    params = req_data.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise DefinitionError(
            f"'request.params' must be a dictionary in test '{name}'{origin}."
        )

    body = req_data.get("body")
    body_type_raw = req_data.get("body_type")
    if body_type_raw is not None:
        if not isinstance(body_type_raw, str):
            raise DefinitionError(
                f"'request.body_type' must be a string in test '{name}'{origin}."
            )
        try:
            body_type = BodyType.from_string(body_type_raw)
        except Exception as err:
            raise DefinitionError(
                f"Unsupported body type '{body_type_raw}' in test '{name}'{origin}: "
                f"{err}"
            ) from err
    else:
        # Default body type: JSON if dict/list, RAW if string, else JSON
        body_type = BodyType.RAW if isinstance(body, str) else BodyType.JSON

    timeout = req_data.get("timeout")
    if timeout is not None:
        try:
            timeout = float(timeout)
            if timeout <= 0:
                raise ValueError
        except (ValueError, TypeError):
            raise DefinitionError(
                f"'request.timeout' must be a positive number in test '{name}'{origin}."
            ) from None

    max_retries = req_data.get("max_retries")
    if max_retries is not None:
        try:
            max_retries = int(max_retries)
            if max_retries < 0:
                raise ValueError
        except (ValueError, TypeError):
            raise DefinitionError(
                f"'request.max_retries' must be a non-negative integer "
                f"in '{name}'{origin}."
            ) from None

    follow_redirects = req_data.get("follow_redirects")
    if follow_redirects is not None and not isinstance(follow_redirects, bool):
        raise DefinitionError(
            f"'request.follow_redirects' must be a boolean in test '{name}'{origin}."
        )

    retry_non_idempotent = req_data.get("retry_non_idempotent")
    if retry_non_idempotent is not None and not isinstance(retry_non_idempotent, bool):
        raise DefinitionError(
            f"'request.retry_non_idempotent' must be a boolean in test "
            f"'{name}'{origin}."
        )

    request_def = TestRequestDefinition(
        method=method,
        url=url,
        headers=headers,
        params=params,
        body=body,
        body_type=body_type,
        timeout=timeout,
        max_retries=max_retries,
        follow_redirects=follow_redirects,
        retry_non_idempotent=retry_non_idempotent,
    )

    # 4. Validate tags, extract, assertions
    tags = data.get("tags", [])
    if tags is None:
        tags = []
    if not isinstance(tags, list):
        raise DefinitionError(
            f"'tags' must be a list of strings in test '{name}'{origin}."
        )
    tags = [str(t).strip() for t in tags if str(t).strip()]

    extract = data.get("extract", {})
    if extract is None:
        extract = {}
    if not isinstance(extract, dict):
        raise DefinitionError(
            f"'extract' must be a dictionary in test '{name}'{origin}."
        )
    extract = {str(k): str(v) for k, v in extract.items()}

    assertions = data.get("assertions", [])
    if assertions is None:
        assertions = []
    if not isinstance(assertions, list):
        raise DefinitionError(f"'assertions' must be a list in test '{name}'{origin}.")

    # 5. Validate source and status
    source_raw = data.get("source", "handwritten")
    if not isinstance(source_raw, str):
        raise DefinitionError(f"'source' must be a string in test '{name}'{origin}.")
    try:
        source = DefinitionSource.from_string(source_raw)
    except ValueError as err:
        raise DefinitionError(
            f"Invalid source '{source_raw}' in test '{name}'{origin}: {err}"
        ) from err

    status_raw = data.get("status", "approved")
    if not isinstance(status_raw, str):
        raise DefinitionError(f"'status' must be a string in test '{name}'{origin}.")
    try:
        status = DefinitionStatus.from_string(status_raw)
    except ValueError as err:
        raise DefinitionError(
            f"Invalid status '{status_raw}' in test '{name}'{origin}: {err}"
        ) from err

    return TestDefinition(
        name=name,
        module=module,
        request=request_def,
        tags=tags,
        extract=extract,
        assertions=assertions,
        source=source,
        status=status,
        file_path=file_path,
    )


def load_test_definition(file_path: str | Path) -> TestDefinition:
    """Safely load and parse an API test definition from a YAML file.

    Args:
        file_path: Path to the YAML test file.

    Returns:
        Validated TestDefinition model.

    Raises:
        DefinitionError: If the file is missing, empty, malformed, or invalid.
    """
    path = Path(file_path)
    if not path.is_file():
        raise DefinitionError(f"Test definition file not found: '{file_path}'")

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as err:
        raise DefinitionError(f"Error reading file '{file_path}': {err}") from err

    try:
        data = yaml.safe_load(content)
    except Exception as err:
        raise DefinitionError(f"Malformed YAML in '{file_path}': {err}") from err

    return parse_test_definition(data, file_path=path)
