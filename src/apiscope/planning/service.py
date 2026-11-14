"""Test Planning Service orchestrating deterministic and AI test planning (Task 18).

Coordinates contract normalization, deterministic baseline test generation,
optional budget-aware Gemini reasoning, deduplication, caching, and serialization.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from apiscope.contract.models import CanonicalContract, ContractQualityReport
from apiscope.contract.normalizer import ContractNormalizer
from apiscope.contract.service import ContractAnalysisService
from apiscope.core.exceptions import DefinitionError
from apiscope.core.logging_config import get_logger
from apiscope.generators.ai_generator import LLMConfig
from apiscope.intelligence.models import ProjectIntelligenceReport
from apiscope.planning.ai_planner import GeminiTestPlanner
from apiscope.planning.cache import PlanningCache
from apiscope.planning.deterministic_planner import DeterministicTestPlanner
from apiscope.planning.models import (
    AIBudgetConfig,
    AIUsageRecord,
    TestPlan,
    TestScenario,
)

logger = get_logger(__name__)


class TestPlanningService:
    """Orchestrates comprehensive, evidence-backed API test planning."""

    __test__ = False

    def __init__(
        self,
        contract_service: ContractAnalysisService | None = None,
        cache: PlanningCache | None = None,
    ) -> None:
        """Initialize TestPlanningService with analysis services and cache."""
        self.contract_service = contract_service or ContractAnalysisService()
        self.cache = cache or PlanningCache()

    def plan_tests(
        self,
        spec_path: str | Path | None = None,
        contract_path: str | Path | None = None,
        intelligence_path: str | Path | None = None,
        repo_path: str | Path | None = None,
        enable_ai: bool = False,
        provider_type: str = "gemini",
        api_key: str | None = None,
        model_name: str | None = None,
        budget: AIBudgetConfig | None = None,
    ) -> TestPlan:
        """Generate comprehensive TestPlan from contract and intelligence.

        Combines deterministic baseline scenarios with optional AI proposals.

        Args:
            spec_path: Path to OpenAPI spec (YAML or JSON).
            contract_path: Optional path to pre-computed ContractQualityReport.
            intelligence_path: Optional path to ProjectIntelligenceReport.
            repo_path: Optional path to local source repository.
            enable_ai: If True, invoke Gemini reasoning within budget.
            provider_type: LLM provider ("gemini" or "openai").
            api_key: Optional provider API key.
            model_name: Optional model override.
            budget: Optional budget guardrails.

        Returns:
            Structured, versioned TestPlan.
        """
        # Step 1: Load or analyze contract
        contract: CanonicalContract
        quality_report: ContractQualityReport | None = None

        if contract_path is not None:
            c_p = Path(contract_path)
            if not c_p.exists():
                raise DefinitionError(
                    f"Contract quality report file not found: '{contract_path}'"
                )
            with open(c_p, encoding="utf-8") as f:
                c_data = (
                    yaml.safe_load(f)
                    if c_p.suffix in (".yaml", ".yml")
                    else json.load(f)
                )
            raw_spec = (
                c_data
                if "paths" in c_data
                else self.contract_service._load_spec_file(spec_path)
                if spec_path
                else {}
            )
            contract = ContractNormalizer(raw_spec).normalize()
        elif spec_path is not None:
            quality_report = self.contract_service.analyze_contract(
                spec_path=spec_path,
                repo_path=repo_path,
                intelligence_path=intelligence_path,
            )
            raw_spec = self.contract_service._load_spec_file(spec_path)
            contract = ContractNormalizer(raw_spec).normalize()
            # Inherit operation readiness evaluated in report
            op_readiness_map = {
                f"{o.method} {o.path}": (o.readiness, o.readiness_reasons)
                for o in quality_report.operations
            }
            for op in contract.operations:
                sig = f"{op.method} {op.path}"
                if sig in op_readiness_map:
                    op.readiness, op.readiness_reasons = op_readiness_map[sig]
        else:
            raise DefinitionError("Either --spec or --contract path must be provided.")

        # Step 2: Load Project Intelligence if available
        intel_report: ProjectIntelligenceReport | None = None
        if intelligence_path is not None:
            i_p = Path(intelligence_path)
            if not i_p.exists():
                raise DefinitionError(
                    f"Project intelligence file not found: '{intelligence_path}'"
                )
            with open(i_p, encoding="utf-8") as f:
                i_data = (
                    yaml.safe_load(f)
                    if i_p.suffix in (".yaml", ".yml")
                    else json.load(f)
                )
            intel_report = ProjectIntelligenceReport.from_dict(i_data)

        # Step 3: Deterministic Test Planning Baseline
        det_planner = DeterministicTestPlanner(contract, intel_report)
        scenarios, untestable_ops, missing_prereqs = det_planner.plan()

        # Step 4: Optional Gemini AI Planning
        ai_usage = AIUsageRecord()
        if enable_ai:
            if api_key is not None and not api_key.strip():
                llm_config = LLMConfig(
                    provider=provider_type or "gemini",
                    api_key="",
                    model=model_name or "",
                )
            else:
                llm_config = LLMConfig.from_env(
                    provider=provider_type,
                    api_key=api_key,
                    model=model_name,
                )
            ai_planner = GeminiTestPlanner(
                config=llm_config,
                budget=budget,
                cache=self.cache,
            )
            ai_scenarios, ai_usage = ai_planner.plan_project_scenarios(
                contract=contract,
                intelligence=intel_report,
            )

            # Deduplicate proposals by scenario title and target endpoints
            seen_signatures: set[str] = {
                f"{s.title.lower()}::{','.join(sorted(s.target_endpoints))}"
                for s in scenarios
            }
            for ai_scn in ai_scenarios:
                eps_str = ",".join(sorted(ai_scn.target_endpoints))
                ai_sig = f"{ai_scn.title.lower()}::{eps_str}"
                if ai_sig not in seen_signatures:
                    seen_signatures.add(ai_sig)
                    scenarios.append(ai_scn)
        else:
            ai_usage.status = "SKIPPED"
            ai_usage.details = "AI planning disabled (offline deterministic mode)."

        # Step 5: Compute summary metrics
        summary = self._compute_plan_summary(scenarios, contract)

        plan_id = (
            f"plan_{contract.title.lower().replace(' ', '_')}_"
            f"{abs(hash(contract.title + contract.version)) % 100000}"
        )

        return TestPlan(
            plan_id=plan_id,
            title=contract.title,
            version=contract.version,
            contract_ref=str(spec_path or contract_path or ""),
            intelligence_ref=str(intelligence_path or ""),
            summary=summary,
            scenarios=scenarios,
            missing_prerequisites=missing_prereqs,
            untestable_operations=untestable_ops,
            ai_usage=ai_usage,
        )

    def _compute_plan_summary(
        self, scenarios: list[TestScenario], contract: CanonicalContract
    ) -> dict[str, Any]:
        """Compute aggregated metrics across planned scenarios."""
        by_cat: dict[str, int] = {}
        by_prio: dict[str, int] = {}
        by_origin: dict[str, int] = {}
        by_approval: dict[str, int] = {}

        for s in scenarios:
            by_cat[s.category.value] = by_cat.get(s.category.value, 0) + 1
            by_prio[s.priority.value] = by_prio.get(s.priority.value, 0) + 1
            by_origin[s.origin.value] = by_origin.get(s.origin.value, 0) + 1
            by_approval[s.approval_status.value] = (
                by_approval.get(s.approval_status.value, 0) + 1
            )

        return {
            "total_scenarios": len(scenarios),
            "total_contract_operations": len(contract.operations),
            "scenarios_by_category": by_cat,
            "scenarios_by_priority": by_prio,
            "scenarios_by_origin": by_origin,
            "approval_summary": by_approval,
        }

    def save_plan(self, plan: TestPlan, out_path: str | Path) -> None:
        """Serialize TestPlan to YAML or JSON."""
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = plan.to_dict()

        if target.suffix.lower() == ".json":
            with open(target, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        else:
            with open(target, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, sort_keys=False)

    def load_plan(self, plan_path: str | Path) -> TestPlan:
        """Load TestPlan from YAML or JSON file."""
        target = Path(plan_path)
        if not target.exists():
            raise DefinitionError(f"Test plan file not found: '{plan_path}'")

        with open(target, encoding="utf-8") as f:
            data = (
                json.load(f) if target.suffix.lower() == ".json" else yaml.safe_load(f)
            )

        return TestPlan.from_dict(data)
