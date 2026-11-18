"""Unit tests for independent ground-truth benchmark manifest (Task 28)."""

from __future__ import annotations

from pathlib import Path

from apiscope.benchmarking.manifest import (
    create_default_benchmark_manifest,
    export_benchmark_manifest,
    load_benchmark_manifest,
)
from apiscope.benchmarking.models import (
    BenchmarkCase,
    CleanControlCategory,
    PlantedDefectCategory,
)


def test_manifest_creation_and_counts() -> None:
    """Verify manifest creation contains exactly 32 cases (16 defects, 16 clean controls)."""
    manifest = create_default_benchmark_manifest()
    assert manifest.manifest_version == "1.0.0"
    assert manifest.total_cases_count == 32
    assert len(manifest.defect_cases) == 16
    assert len(manifest.clean_cases) == 16


def test_manifest_defect_categories_coverage() -> None:
    """Verify all 16 planted defect categories A through P are represented."""
    manifest = create_default_benchmark_manifest()
    defect_categories = {c.category for c in manifest.defect_cases}
    for cat in PlantedDefectCategory:
        assert cat.value in defect_categories


def test_manifest_clean_categories_coverage() -> None:
    """Verify all 16 clean control categories are represented."""
    manifest = create_default_benchmark_manifest()
    clean_categories = {c.category for c in manifest.clean_cases}
    for cat in CleanControlCategory:
        assert cat.value in clean_categories


def test_manifest_case_lookup() -> None:
    """Verify case lookup by ID succeeds for valid cases and returns None for unknown."""
    manifest = create_default_benchmark_manifest()
    case_a = manifest.get_case("BM-DEF-A01")
    assert case_a is not None
    assert case_a.case_id == "BM-DEF-A01"
    assert case_a.is_defect is True
    assert case_a.category == PlantedDefectCategory.A_RESPONSE_SCHEMA_VIOLATION.value

    case_cln = manifest.get_case("BM-CLN-01")
    assert case_cln is not None
    assert case_cln.is_defect is False

    assert manifest.get_case("NON_EXISTENT") is None


def test_manifest_serialization_roundtrip(tmp_path: Path) -> None:
    """Verify manifest serialization to and from JSON/YAML preserves structure."""
    manifest = create_default_benchmark_manifest()

    json_path, yaml_path = export_benchmark_manifest(manifest, tmp_path)
    assert json_path.is_file()
    assert yaml_path.is_file()

    loaded_json = load_benchmark_manifest(json_path)
    assert loaded_json.manifest_version == manifest.manifest_version
    assert loaded_json.total_cases_count == 32
    assert len(loaded_json.defect_cases) == 16

    loaded_yaml = load_benchmark_manifest(yaml_path)
    assert loaded_yaml.manifest_version == manifest.manifest_version
    assert loaded_yaml.total_cases_count == 32
    assert len(loaded_yaml.clean_cases) == 16


def test_manifest_case_to_dict_and_back() -> None:
    """Verify BenchmarkCase dictionary conversion preserves all fields."""
    case = BenchmarkCase(
        case_id="BM-TEST-01",
        title="Test Case",
        target_operation="GET /test",
        method="GET",
        path="/test",
        service_context="test_service",
        is_defect=True,
        category="A_TEST",
        intended_behaviour="Work properly",
        planted_behaviour="Fails deliberately",
        expected_verdict="VERIFIED_CONTRACT_MISMATCH",
        expected_confidence="VERIFIED",
        evidence_necessary="Evidence required",
        prerequisite_state="None",
        expected_observable_symptoms="Error symptom",
        required_execution_permissions="READ_ONLY",
        expected_detectable=True,
        known_limitations="None",
    )
    d = case.to_dict()
    assert d["case_id"] == "BM-TEST-01"
    assert d["is_defect"] is True
    reconstructed = BenchmarkCase.from_dict(d)
    assert reconstructed.case_id == case.case_id
    assert reconstructed.target_operation == case.target_operation
