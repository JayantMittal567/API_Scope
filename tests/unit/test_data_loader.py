"""Unit tests for M4 Data-Driven File Loader component.

Tests CSV and JSON parameter dataset loading, path safety enforcement,
Windows UTF-8 BOM compatibility, row limits, and value type coercion.
"""

from pathlib import Path

import pytest

from apiscope.core.exceptions import DefinitionError, PathTraversalError
from apiscope.definitions.data_loader import MAX_DATA_ROWS, load_data_rows


def test_load_json_rows_valid_preserves_types(tmp_path: Path) -> None:
    """Covers: FR-17

    Verify that valid JSON datasets load properly preserving native Python data types.
    """
    json_file = tmp_path / "valid_data.json"
    json_file.write_text(
        """[
        {"user_id": 101, "rate": 3.75, "active": true, "name": "Alice"},
        {"user_id": 102, "rate": 4.50, "active": false, "name": "Bob"}
    ]""",
        encoding="utf-8",
    )

    rows = load_data_rows(json_file)
    assert len(rows) == 2
    assert rows[0] == {"user_id": 101, "rate": 3.75, "active": True, "name": "Alice"}
    assert isinstance(rows[0]["user_id"], int)
    assert isinstance(rows[0]["rate"], float)
    assert isinstance(rows[0]["active"], bool)
    assert isinstance(rows[0]["name"], str)


def test_load_json_rows_utf8_bom_supported(tmp_path: Path) -> None:
    """Covers: FR-17

    Verify that JSON data files encoded with UTF-8 BOM (Windows standard) load cleanly.
    """
    bom_file = tmp_path / "bom_data.json"
    bom_content = b'\xef\xbb\xbf[{"sku": "A-1", "count": 5}]'
    bom_file.write_bytes(bom_content)

    rows = load_data_rows(bom_file)
    assert len(rows) == 1
    assert rows[0] == {"sku": "A-1", "count": 5}


def test_load_json_rows_invalid_syntax_or_non_list_raises_definition_error(
    tmp_path: Path,
) -> None:
    """Covers: FR-17

    Verify that malformed JSON or non-list root containers raise DefinitionError.
    """
    bad_syntax = tmp_path / "bad_syntax.json"
    bad_syntax.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(DefinitionError, match="Malformed JSON"):
        load_data_rows(bad_syntax)

    non_list = tmp_path / "non_list.json"
    non_list.write_text('{"key": "value"}', encoding="utf-8")
    with pytest.raises(DefinitionError, match="must contain a list of objects"):
        load_data_rows(non_list)


def test_load_json_rows_empty_list_or_non_dict_item_raises_definition_error(
    tmp_path: Path,
) -> None:
    """Covers: FR-17

    Verify that empty JSON arrays or arrays containing primitive items
    raise DefinitionError.
    """
    empty_list = tmp_path / "empty.json"
    empty_list.write_text("[]", encoding="utf-8")
    with pytest.raises(DefinitionError, match="must contain at least one data row"):
        load_data_rows(empty_list)

    primitive_items = tmp_path / "primitives.json"
    primitive_items.write_text('["hello", "world"]', encoding="utf-8")
    with pytest.raises(DefinitionError, match="must be a dictionary"):
        load_data_rows(primitive_items)


def test_load_csv_rows_coerces_int_float_bool_and_preserves_leading_zeros(
    tmp_path: Path,
) -> None:
    """Covers: FR-17

    Verify that CSV loading coerces ints, floats, and booleans while strictly preserving
    leading-zero strings (e.g. '0042', '-01') as strings.
    """
    csv_file = tmp_path / "types.csv"
    csv_file.write_text(
        "sku,quantity,factor,in_stock,flag\n"
        "0042,42,3.14,true,FALSE\n"
        "007,-15,0.5,False,TRUE\n"
        "-01,0,10.0,true,false\n",
        encoding="utf-8",
    )

    rows = load_data_rows(csv_file)
    assert len(rows) == 3

    # Row 0
    assert rows[0]["sku"] == "0042"
    assert isinstance(rows[0]["sku"], str)
    assert rows[0]["quantity"] == 42
    assert isinstance(rows[0]["quantity"], int)
    assert rows[0]["factor"] == 3.14
    assert isinstance(rows[0]["factor"], float)
    assert rows[0]["in_stock"] is True
    assert isinstance(rows[0]["in_stock"], bool)
    assert rows[0]["flag"] is False
    assert isinstance(rows[0]["flag"], bool)

    # Row 1
    assert rows[1]["sku"] == "007"
    assert isinstance(rows[1]["sku"], str)
    assert rows[1]["quantity"] == -15
    assert rows[1]["factor"] == 0.5
    assert rows[1]["in_stock"] is False
    assert rows[1]["flag"] is True

    # Row 2
    assert rows[2]["sku"] == "-01"
    assert isinstance(rows[2]["sku"], str)
    assert rows[2]["quantity"] == 0
    assert rows[2]["factor"] == 10.0


def test_load_csv_rows_utf8_bom_supported(tmp_path: Path) -> None:
    """Covers: FR-17

    Verify that CSV files encoded with UTF-8 BOM load correctly without
    corrupting headers.
    """
    bom_file = tmp_path / "bom.csv"
    bom_content = b"\xef\xbb\xbfname,score\nAlice,100\n"
    bom_file.write_bytes(bom_content)

    rows = load_data_rows(bom_file)
    assert len(rows) == 1
    assert "name" in rows[0]
    assert rows[0]["name"] == "Alice"
    assert rows[0]["score"] == 100


def test_load_csv_rows_empty_or_header_only_raises_definition_error(
    tmp_path: Path,
) -> None:
    """Covers: FR-17

    Verify that empty CSV files or CSVs with only headers raise DefinitionError.
    """
    empty_file = tmp_path / "empty.csv"
    empty_file.write_text("", encoding="utf-8")
    with pytest.raises(
        DefinitionError, match="must contain a header row and at least one data row"
    ):
        load_data_rows(empty_file)

    header_only = tmp_path / "header_only.csv"
    header_only.write_text("id,name,role\n", encoding="utf-8")
    with pytest.raises(
        DefinitionError, match="must contain a header row and at least one data row"
    ):
        load_data_rows(header_only)


def test_load_data_rows_exceeding_max_rows_raises_definition_error(
    tmp_path: Path,
) -> None:
    """Covers: FR-17

    Verify that datasets exceeding MAX_DATA_ROWS (1000) raise DefinitionError.
    """
    large_csv = tmp_path / "large.csv"
    lines = ["id,value\n"] + [f"{i},val_{i}\n" for i in range(MAX_DATA_ROWS + 5)]
    large_csv.write_text("".join(lines), encoding="utf-8")

    with pytest.raises(
        DefinitionError, match=f"exceeding maximum limit of {MAX_DATA_ROWS}"
    ):
        load_data_rows(large_csv)


def test_load_data_rows_unsupported_extension_or_missing_file_raises_definition_error(
    tmp_path: Path,
) -> None:
    """Covers: FR-17

    Verify that unsupported file extensions and missing files raise DefinitionError.
    """
    txt_file = tmp_path / "data.txt"
    txt_file.write_text("hello", encoding="utf-8")
    with pytest.raises(DefinitionError, match="Unsupported data file extension"):
        load_data_rows(txt_file)

    missing_file = tmp_path / "does_not_exist.json"
    with pytest.raises(DefinitionError, match="Data file not found"):
        load_data_rows(missing_file)


def test_load_data_rows_path_traversal_raises_path_traversal_error(
    tmp_path: Path,
) -> None:
    """Covers: FR-17, NFR-04

    Verify that directory traversal attempts outside the base directory
    raise PathTraversalError.
    """
    sub_dir = tmp_path / "secure_zone"
    sub_dir.mkdir()
    outside_file = tmp_path / "outside.csv"
    outside_file.write_text("id,val\n1,outside\n", encoding="utf-8")

    with pytest.raises(PathTraversalError):
        load_data_rows("../outside.csv", base_dir=sub_dir)

    with pytest.raises(PathTraversalError):
        load_data_rows("..\\outside.csv", base_dir=sub_dir)
