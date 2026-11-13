"""Data-driven test file loader for CSV and JSON datasets.

Provides safe loading, path traversal enforcement, row boundary limits, and
intelligent value type coercion (preserving leading-zero identifiers) for
parameterized test execution.
"""

import csv
import json
import re
from pathlib import Path
from typing import Any

from apiscope.core.exceptions import DefinitionError, PathTraversalError
from apiscope.definitions.collection import validate_safe_path

MAX_DATA_ROWS: int = 1000

# Strict numeric regexes that disallow leading zeros on multi-digit numbers
_INT_PATTERN = re.compile(r"^-?(?:0|[1-9]\d*)$")
_FLOAT_PATTERN = re.compile(r"^-?(?:0|[1-9]\d*)\.\d+$")


def _coerce_csv_value(raw: Any) -> int | float | bool | str:
    """Coerce string values from CSV into native Python types.

    Coerces boolean strings ('true'/'false' case-insensitive), strict integers,
    and strict floats while strictly preserving leading-zero numeric strings
    (e.g., '0042', '007', '-01') as strings.

    Args:
        raw: Raw value parsed from CSV.

    Returns:
        Coerced value as int, float, bool, or str.
    """
    if raw is None:
        return ""
    if not isinstance(raw, str):
        return raw

    stripped = raw.strip()

    if stripped.lower() == "true":
        return True
    if stripped.lower() == "false":
        return False

    if _INT_PATTERN.match(stripped):
        return int(stripped)

    if _FLOAT_PATTERN.match(stripped):
        return float(stripped)

    return raw


def load_data_rows(
    file_path: str | Path,
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Load parameterization rows from a CSV or JSON file.

    Enforces path traversal defenses via validate_safe_path, opens files using
    'utf-8-sig' to seamlessly handle UTF-8 BOM, validates file structure, and
    enforces MAX_DATA_ROWS limit.

    Args:
        file_path: Relative or absolute path to the data file (.csv or .json).
        base_dir: Optional base directory to restrict path resolution within.

    Returns:
        List of dictionaries where each dictionary represents one parameter row.

    Raises:
        PathTraversalError: If file_path escapes the allowed base directory.
        DefinitionError: If the file is missing, unsupported, malformed, or
            exceeds row limits.
    """
    # 1. Path safety validation
    path_obj = Path(file_path)
    if base_dir is not None:
        target_path = validate_safe_path(Path(base_dir), file_path)
    else:
        # Use parent or working directory to check for traversal tokens
        ref_str = str(file_path).strip().replace("\\", "/")
        if (
            "../" in ref_str
            or ref_str.startswith("../")
            or ref_str == ".."
            or "/../" in ref_str
            or ref_str.endswith("/..")
            or ".." in path_obj.parts
        ):
            raise PathTraversalError(
                f"Forbidden directory traversal sequence detected in '{file_path}'."
            )
        target_path = path_obj.resolve()

    # 2. Extension validation
    ext = target_path.suffix.lower()
    if ext not in (".csv", ".json"):
        raise DefinitionError(
            f"Unsupported data file extension '{ext}'. Allowed: .csv, .json"
        )

    # 3. Read file with utf-8-sig
    try:
        with open(target_path, encoding="utf-8-sig") as f:
            content = f.read()
    except FileNotFoundError:
        raise DefinitionError(f"Data file not found: {file_path}") from None
    except OSError as err:
        raise DefinitionError(f"Cannot read data file '{file_path}': {err}") from err

    # 4. JSON loading
    if ext == ".json":
        try:
            data = json.loads(content)
        except json.JSONDecodeError as err:
            raise DefinitionError(
                f"Malformed JSON in data file '{file_path}': {err}"
            ) from err

        if not isinstance(data, list):
            raise DefinitionError(
                f"JSON data file '{file_path}' must contain a list of objects."
            )
        if len(data) == 0:
            raise DefinitionError(
                f"JSON data file '{file_path}' must contain at least one data row."
            )

        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                raise DefinitionError(
                    f"JSON data file '{file_path}' row {idx} must be a dictionary."
                )

        if len(data) > MAX_DATA_ROWS:
            raise DefinitionError(
                f"Data file contains {len(data)} rows, "
                f"exceeding maximum limit of {MAX_DATA_ROWS}"
            )

        return data

    # 5. CSV loading
    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        raise DefinitionError(
            "CSV data file must contain a header row and at least one data row"
        )

    reader = csv.DictReader(lines)
    if not reader.fieldnames or len(reader.fieldnames) == 0:
        raise DefinitionError(
            "CSV data file must contain a header row and at least one data row"
        )

    rows: list[dict[str, Any]] = []
    for row in reader:
        coerced_row = {k: _coerce_csv_value(v) for k, v in row.items() if k is not None}
        rows.append(coerced_row)

    if len(rows) == 0:
        raise DefinitionError(
            "CSV data file must contain a header row and at least one data row"
        )

    if len(rows) > MAX_DATA_ROWS:
        raise DefinitionError(
            f"Data file contains {len(rows)} rows, "
            f"exceeding maximum limit of {MAX_DATA_ROWS}"
        )

    return rows
