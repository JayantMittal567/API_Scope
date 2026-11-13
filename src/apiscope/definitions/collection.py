"""Test collection loading, grouping, filtering, and directory traversal protection.

Provides multi-test suite organization allowing tests to be batched across
modules, filtered by tags, and safely loaded without allowing directory escape.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from apiscope.core.exceptions import DefinitionError, PathTraversalError
from apiscope.definitions.models import TestDefinition
from apiscope.definitions.yaml_loader import load_test_definition, parse_test_definition


def validate_safe_path(base_dir: Path, ref_path: str | Path) -> Path:
    """Validate that a referenced path does not escape the base directory.

    Rejects directory traversal sequences ('../', '..\\') and absolute paths
    that resolve outside the configured base directory.

    Args:
        base_dir: The allowed root directory.
        ref_path: Relative or absolute path string/Path to validate.

    Returns:
        The resolved Path safely inside base_dir.

    Raises:
        PathTraversalError: If the path contains traversal sequences or
            escapes the base directory.
    """
    ref_str = str(ref_path).strip()
    normalized = ref_str.replace("\\", "/")

    # Check for literal traversal indicators in the path string
    if (
        "../" in normalized
        or normalized.startswith("../")
        or normalized == ".."
        or "/../" in normalized
        or normalized.endswith("/..")
    ):
        raise PathTraversalError(
            f"Forbidden directory traversal sequence detected in '{ref_str}'."
        )

    p = Path(ref_str)
    if ".." in p.parts:
        raise PathTraversalError(
            f"Forbidden directory traversal sequence detected in '{ref_str}'."
        )

    base_resolved = base_dir.resolve()

    # Handle absolute paths or root-anchored paths (e.g. /outside on Windows)
    if p.is_absolute() or ref_str.startswith("/") or ref_str.startswith("\\"):
        target = p.resolve()
    else:
        target = (base_resolved / p).resolve()

    try:
        target.relative_to(base_resolved)
    except ValueError:
        raise PathTraversalError(
            f"Path '{ref_str}' escapes allowed base directory '{base_resolved}'."
        ) from None

    return target


@dataclass
class TestCollection:
    """Organized suite of API test definitions.

    Supports test discovery, tagging, module filtering, and ordered execution planning.
    """

    __test__ = False

    name: str
    module: str
    tags: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    tests: list[TestDefinition] = field(default_factory=list)
    base_dir: Path | None = None
    file_path: Path | None = None

    def __post_init__(self) -> None:
        """Validate and normalize collection metadata."""
        if not isinstance(self.name, str) or not self.name.strip():
            raise DefinitionError("Collection 'name' must be a non-empty string.")
        self.name = self.name.strip()

        if not isinstance(self.module, str) or not self.module.strip():
            raise DefinitionError("Collection 'module' must be a non-empty string.")
        self.module = self.module.strip()

        if self.tags is None:
            self.tags = []
        elif not isinstance(self.tags, list):
            raise DefinitionError("Collection 'tags' must be a list of strings.")
        self.tags = [str(t).strip() for t in self.tags if str(t).strip()]

        if self.test_files is None:
            self.test_files = []
        elif not isinstance(self.test_files, list):
            raise DefinitionError(
                "Collection 'test_files' must be a list of file paths."
            )

        if self.tests is None:
            self.tests = []

    def __len__(self) -> int:
        """Return the number of loaded tests in this collection."""
        return len(self.tests)

    def __iter__(self) -> Iterator[TestDefinition]:
        """Iterate over loaded test definitions."""
        return iter(self.tests)

    def __getitem__(self, index: int) -> TestDefinition:
        """Access loaded test by index."""
        return self.tests[index]

    def filter_by_module(self, module: str) -> list[TestDefinition]:
        """Filter loaded tests matching the specified module name.

        Args:
            module: Target module identifier (case-insensitive).

        Returns:
            List of TestDefinition instances belonging to the module.
        """
        target = module.strip().lower()
        return [t for t in self.tests if t.module.strip().lower() == target]

    def filter_by_tags(
        self,
        tags: Sequence[str] | str,
        match_all: bool = False,
    ) -> list[TestDefinition]:
        """Filter loaded tests matching one or all specified tags.

        Args:
            tags: A single tag or sequence of tags to match.
            match_all: If True, all tags must be present; if False, any tag matches.

        Returns:
            List of matching TestDefinition instances.
        """
        if isinstance(tags, str):
            search_tags = {tags.strip().lower()}
        else:
            search_tags = {str(t).strip().lower() for t in tags if str(t).strip()}

        if not search_tags:
            return list(self.tests)

        matched: list[TestDefinition] = []
        for test in self.tests:
            test_tags = {str(t).strip().lower() for t in test.tags}
            if match_all:
                if search_tags.issubset(test_tags):
                    matched.append(test)
            else:
                if not search_tags.isdisjoint(test_tags):
                    matched.append(test)
        return matched

    def filter_by_tag(self, tag: str) -> list[TestDefinition]:
        """Convenience method to filter loaded tests matching a single tag.

        Args:
            tag: Tag string to match.

        Returns:
            List of matching TestDefinition instances.
        """
        return self.filter_by_tags([tag])

    def load_all_tests(self) -> list[TestDefinition]:
        """Ensure all referenced test files are loaded into the tests collection.

        Returns:
            List of loaded TestDefinition instances.

        Raises:
            DefinitionError: If base_dir is missing or a referenced test file
                is missing or invalid.
            PathTraversalError: If a test file reference violates directory boundaries.
        """
        if self.tests and not self.test_files:
            return self.tests

        if self.base_dir is None and self.test_files:
            raise DefinitionError(
                f"Cannot load tests for collection '{self.name}': base_dir is not set."
            )

        loaded: list[TestDefinition] = list(self.tests)
        loaded_names = {t.name for t in loaded}
        for ref_str in self.test_files:
            safe_path = validate_safe_path(self.base_dir, ref_str)
            if not safe_path.is_file():
                raise DefinitionError(
                    f"Test file '{ref_str}' referenced in collection '{self.name}' "
                    f"does not exist: '{safe_path}'."
                )
            test_def = load_test_definition(safe_path)
            if test_def.name not in loaded_names:
                loaded.append(test_def)
                loaded_names.add(test_def.name)

        self.tests = loaded
        return self.tests


def parse_collection(
    data: Any,
    file_path: Path | None = None,
    base_dir: Path | None = None,
    load_tests: bool = True,
) -> TestCollection:
    """Parse and validate collection data dictionary.

    Args:
        data: Dictionary parsed from collection YAML.
        file_path: Optional path to collection file for error context.
        base_dir: Base directory for resolving test references. If None and file_path
                  is given, file_path.parent is used.
        load_tests: Whether to load referenced TestDefinition files immediately.

    Returns:
        Validated TestCollection instance.

    Raises:
        DefinitionError: If required fields are missing or data is malformed.
        PathTraversalError: If any referenced test path attempts directory traversal.
    """
    origin = f" in '{file_path}'" if file_path else ""

    if not isinstance(data, dict):
        raise DefinitionError(
            f"Collection definition{origin} is empty or not a valid dictionary mapping."
        )

    name = data.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        raise DefinitionError(f"Missing or invalid required field 'name'{origin}.")
    name = name.strip()

    module = data.get("module")
    if not module or not isinstance(module, str) or not module.strip():
        raise DefinitionError(f"Missing or invalid required field 'module'{origin}.")
    module = module.strip()

    tags = data.get("tags", [])
    if tags is None:
        tags = []
    if not isinstance(tags, list):
        raise DefinitionError(
            f"'tags' must be a list of strings in collection '{name}'{origin}."
        )
    clean_tags = [str(t).strip() for t in tags if str(t).strip()]

    test_files_raw = data.get("tests", [])
    if test_files_raw is None:
        test_files_raw = []
    if not isinstance(test_files_raw, list):
        raise DefinitionError(
            f"'tests' must be a list of file paths or test definitions "
            f"in collection '{name}'{origin}."
        )

    inline_tests: list[TestDefinition] = []
    test_files: list[str] = []
    for item in test_files_raw:
        if isinstance(item, dict):
            inline_tests.append(parse_test_definition(item, file_path=file_path))
        elif isinstance(item, TestDefinition):
            inline_tests.append(item)
        elif isinstance(item, str) and item.strip():
            test_files.append(item.strip())
        else:
            raise DefinitionError(
                f"'tests' items must be file paths or test definitions "
                f"in collection '{name}'{origin}."
            )

    effective_base_dir = base_dir
    if effective_base_dir is None and file_path is not None:
        effective_base_dir = file_path.parent

    collection = TestCollection(
        name=name,
        module=module,
        tags=clean_tags,
        test_files=test_files,
        tests=inline_tests,
        base_dir=effective_base_dir,
        file_path=file_path,
    )

    if load_tests and test_files:
        if effective_base_dir is None:
            raise DefinitionError(
                f"Base directory required to load tests in collection '{name}'{origin}."
            )
        collection.load_all_tests()

    return collection


def load_collection(
    file_path: str | Path,
    base_dir: Path | None = None,
) -> TestCollection:
    """Safely load and validate a TestCollection from a YAML file.

    Loads the collection specification and resolves all referenced test files.

    Args:
        file_path: Path to the collection YAML file.
        base_dir: Optional base directory. If None, file_path.parent is used.

    Returns:
        Validated TestCollection with loaded test definitions.

    Raises:
        DefinitionError: If collection file is missing, empty, malformed,
            or referenced tests are missing.
        PathTraversalError: If any referenced test path attempts directory traversal.
    """
    path = Path(file_path)
    if not path.is_file():
        raise DefinitionError(f"Collection file not found: '{file_path}'")

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as err:
        raise DefinitionError(
            f"Error reading collection file '{file_path}': {err}"
        ) from err

    try:
        data = yaml.safe_load(content)
    except Exception as err:
        raise DefinitionError(
            f"Malformed YAML in collection '{file_path}': {err}"
        ) from err

    effective_base_dir = Path(base_dir) if base_dir is not None else path.parent
    return parse_collection(
        data=data,
        file_path=path,
        base_dir=effective_base_dir,
        load_tests=True,
    )
