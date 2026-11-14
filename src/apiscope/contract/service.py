"""Contract Analysis Service orchestrating normalization and quality audit.

Coordinates ContractNormalizer, ContractQualityAnalyzer, and ContractReconciler
to produce a versioned ContractQualityReport with operational readiness.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from apiscope.contract.models import (
    ContractQualityReport,
    FindingSeverity,
    OperationReadiness,
)
from apiscope.contract.normalizer import ContractNormalizer
from apiscope.contract.quality_analyzer import ContractQualityAnalyzer
from apiscope.contract.reconciler import ContractReconciler
from apiscope.core.exceptions import ConfigurationError, DefinitionError
from apiscope.intelligence.models import ProjectIntelligenceReport
from apiscope.intelligence.source_scanner import (
    DiscoveredSourceFindings,
    SourceCodeScanner,
)


class ContractAnalysisService:
    """Orchestrates contract normalization, quality audit, and reconciliation."""

    def __init__(
        self,
        normalizer: ContractNormalizer | None = None,
        quality_analyzer: ContractQualityAnalyzer | None = None,
        reconciler: ContractReconciler | None = None,
    ) -> None:
        """Initialize service with optional pre-configured analyzers."""
        self._normalizer = normalizer
        self.quality_analyzer = quality_analyzer or ContractQualityAnalyzer()
        self.reconciler = reconciler or ContractReconciler()

    def analyze_contract(
        self,
        spec_path: str | Path | dict[str, Any],
        repo_path: str | Path | None = None,
        intelligence_path: str | Path | ProjectIntelligenceReport | None = None,
    ) -> ContractQualityReport:
        """Analyze API contract, audit quality, and reconcile with source tree.

        Args:
            spec_path: Path to OpenAPI YAML/JSON file or pre-parsed dict.
            repo_path: Optional path to local source repository.
            intelligence_path: Optional path to ProjectIntelligenceReport.

        Returns:
            Populated versioned ContractQualityReport.
        """
        # 1. Load raw specification
        raw_spec: dict[str, Any]
        if isinstance(spec_path, dict):
            raw_spec = spec_path
        else:
            raw_spec = self._load_spec_file(spec_path)

        # 2. Load optional intelligence report
        intel_report: ProjectIntelligenceReport | None = None
        if isinstance(intelligence_path, ProjectIntelligenceReport):
            intel_report = intelligence_path
        elif intelligence_path is not None:
            intel_report = self._load_intelligence_file(intelligence_path)

        # 3. Normalize specification into CanonicalContract
        normalizer = self._normalizer or ContractNormalizer(
            raw_spec=raw_spec, intelligence_report=intel_report
        )
        contract = normalizer.normalize()

        # 4. Perform deterministic quality analysis
        self.quality_analyzer.analyze(contract)

        # 5. Optional source reconciliation
        source_reconciliation: dict[str, Any] = {}
        if repo_path is not None:
            scanner = SourceCodeScanner(repo_path)
            findings = scanner.scan()
            source_reconciliation = self.reconciler.reconcile(contract, findings)
        elif (
            intel_report is not None
            and intel_report.project.source_language != "unknown"
        ):
            # Reconstruct findings from intelligence report if available
            findings = DiscoveredSourceFindings(
                source_language=intel_report.project.source_language,
                roles=intel_report.auth.roles,
            )
            source_reconciliation = self.reconciler.reconcile(contract, findings)

        # 6. Evaluate test-generation readiness per operation
        readiness_counts: dict[str, int] = {
            OperationReadiness.READY.value: 0,
            OperationReadiness.PARTIAL.value: 0,
            OperationReadiness.BLOCKED.value: 0,
        }

        for op in contract.operations:
            self._evaluate_operation_readiness(op)
            readiness_counts[op.readiness.value] += 1

        total_ops = len(contract.operations)
        blocked_count = readiness_counts[OperationReadiness.BLOCKED.value]
        partial_count = readiness_counts[OperationReadiness.PARTIAL.value]

        if total_ops == 0 or blocked_count > (total_ops / 2):
            overall_readiness = OperationReadiness.BLOCKED
        elif blocked_count > 0 or partial_count > 0:
            overall_readiness = OperationReadiness.PARTIAL
        else:
            overall_readiness = OperationReadiness.READY

        # 7. Summarize metrics
        ops_by_method: dict[str, int] = {}
        for op in contract.operations:
            ops_by_method[op.method] = ops_by_method.get(op.method, 0) + 1

        findings_summary: dict[str, int] = {
            FindingSeverity.HIGH.value: 0,
            FindingSeverity.MEDIUM.value: 0,
            FindingSeverity.LOW.value: 0,
            FindingSeverity.INFO.value: 0,
        }
        for f in contract.findings:
            findings_summary[f.severity.value] = (
                findings_summary.get(f.severity.value, 0) + 1
            )

        # Schema reconciliation (accounting for component schemas vs extracted entities)
        schema_reconciliation = {
            "declared_component_schemas_count": len(contract.schemas),
            "component_schema_names": sorted(contract.schemas.keys()),
        }
        if intel_report is not None:
            schema_reconciliation["intelligence_extracted_entities_count"] = len(
                intel_report.entities
            )
            intel_entity_names = {e.name for e in intel_report.entities}
            comp_names = set(contract.schemas.keys())
            schema_reconciliation["inferred_structural_entities"] = sorted(
                intel_entity_names - comp_names
            )

        return ContractQualityReport(
            title=contract.title,
            version=contract.version,
            total_operations=total_ops,
            operations_by_method=ops_by_method,
            modules_count=len(contract.modules),
            component_schemas_count=len(contract.schemas),
            readiness_summary=readiness_counts,
            overall_readiness=overall_readiness,
            findings_summary=findings_summary,
            findings=list(contract.findings),
            operations=list(contract.operations),
            source_reconciliation=source_reconciliation,
            schema_reconciliation=schema_reconciliation,
        )

    def _evaluate_operation_readiness(self, op: Any) -> None:
        """Classify operational test-generation readiness based on findings."""
        high_findings = [f for f in op.findings if f.severity == FindingSeverity.HIGH]
        medium_findings = [
            f for f in op.findings if f.severity == FindingSeverity.MEDIUM
        ]

        if high_findings:
            op.readiness = OperationReadiness.BLOCKED
            op.readiness_reasons = [f"Blocked: {f.description}" for f in high_findings]
        elif medium_findings:
            op.readiness = OperationReadiness.PARTIAL
            op.readiness_reasons = [
                f"Partial: {f.description}" for f in medium_findings
            ]
        else:
            op.readiness = OperationReadiness.READY
            op.readiness_reasons = [
                "Sufficient evidence for deterministic test synthesis"
            ]

    @staticmethod
    def _load_spec_file(path: str | Path) -> dict[str, Any]:
        """Load OpenAPI YAML or JSON file securely."""
        spec_p = Path(path).resolve()
        if not spec_p.is_file():
            raise DefinitionError(f"OpenAPI specification not found: '{path}'")

        try:
            with open(spec_p, encoding="utf-8-sig") as f:
                content = f.read()
            data = yaml.safe_load(content)
            if not isinstance(data, dict):
                raise DefinitionError(
                    f"OpenAPI specification must parse to a dictionary: '{path}'"
                )
            return data
        except yaml.YAMLError as exc:
            raise DefinitionError(
                f"Malformed YAML in OpenAPI specification: {exc}"
            ) from exc

    @staticmethod
    def _load_intelligence_file(path: str | Path) -> ProjectIntelligenceReport:
        """Load ProjectIntelligenceReport YAML or JSON file."""
        intel_p = Path(path).resolve()
        if not intel_p.is_file():
            raise ConfigurationError(f"Project Intelligence report not found: '{path}'")

        try:
            with open(intel_p, encoding="utf-8-sig") as f:
                content = f.read()
            data = yaml.safe_load(content)
            if not isinstance(data, dict):
                raise ConfigurationError(
                    f"Invalid Project Intelligence report format: '{path}'"
                )
            # Reconstruct ProjectIntelligenceReport using factory
            return ProjectIntelligenceReport.from_dict(data)
        except Exception as exc:
            raise ConfigurationError(
                f"Failed to load Project Intelligence report: {exc}"
            ) from exc

    @staticmethod
    def save_report(report: ContractQualityReport, output_path: str | Path) -> None:
        """Persist ContractQualityReport to YAML or JSON file."""
        out_p = Path(output_path).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)

        if out_p.suffix.lower() == ".json":
            with open(out_p, "w", encoding="utf-8") as f:
                f.write(report.to_json())
        else:
            with open(out_p, "w", encoding="utf-8") as f:
                f.write(report.to_yaml())
