"""Unified Guided End-to-End Workflow & Project Testing Orchestration Service (Task 26).

Orchestrates existing ApiScope services into a reliable, stage-aware pipeline supporting
offline analysis/planning, dry-run previews, and controlled, policy-gated execution.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from apiscope.analysis.endpoint_inventory import (
    EndpointInventoryReport,
    EndpointInventoryService,
)
from apiscope.analysis.silent_failure_service import (
    SilentFailureReport,
    SilentFailureService,
    load_run_file,
)
from apiscope.compiler.models import CompilationReport
from apiscope.compiler.service import TestPlanCompiler
from apiscope.contract.models import CanonicalContract, ContractQualityReport
from apiscope.contract.normalizer import ContractNormalizer
from apiscope.contract.service import ContractAnalysisService
from apiscope.core.contracts import RequestExecutorProtocol
from apiscope.core.exceptions import ConfigurationError, DefinitionError
from apiscope.core.executor import RequestExecutor, TargetAccessPolicy
from apiscope.core.logging_config import get_logger
from apiscope.core.results import RunResult
from apiscope.core.run_service import RunService
from apiscope.core.safety import ExecutionMode, ExecutionSafetyPolicy
from apiscope.definitions.collection import TestCollection
from apiscope.definitions.environment import Environment
from apiscope.execution.models import WorkflowExecutionResult
from apiscope.execution.service import WorkflowExecutionService
from apiscope.fixtures.models import FixturePreparationReport
from apiscope.fixtures.service import FixtureService
from apiscope.generators.openapi_importer import OpenAPISpec, load_openapi_spec
from apiscope.generators.service import test_definition_to_dict
from apiscope.intelligence.models import ProjectIntelligenceReport
from apiscope.intelligence.service import ProjectIntelligenceService
from apiscope.investigation.models import FailureInvestigationReport
from apiscope.investigation.service import FailureInvestigationService
from apiscope.orchestration.models import (
    ExecutionPreview,
    GuidedWorkflowOptions,
    GuidedWorkflowResult,
    StageExecutionRecord,
    StageStatus,
    WorkflowManifest,
    WorkflowStage,
)
from apiscope.planning.cache import PlanningCache
from apiscope.planning.models import ScenarioOrigin, TestPlan
from apiscope.planning.service import TestPlanningService
from apiscope.trust.models import DiagnosticVerdict, TrustAssessmentReport
from apiscope.trust.service import TrustAssessmentService

logger = get_logger("apiscope.orchestration.service")


def compute_file_sha256(path: str | Path) -> str:
    """Compute hex SHA-256 digest of a local file."""
    p = Path(path)
    if not p.is_file():
        return ""
    hasher = hashlib.sha256()
    hasher.update(p.read_bytes())
    return hasher.hexdigest()


class GuidedWorkflowService:
    """Orchestrates comprehensive, guided end-to-end API testing workflows."""

    __test__ = False

    def __init__(
        self,
        intelligence_service: ProjectIntelligenceService | None = None,
        contract_service: ContractAnalysisService | None = None,
        planning_service: TestPlanningService | None = None,
        compiler_service: TestPlanCompiler | None = None,
        fixture_service: FixtureService | None = None,
        workflow_execution_service: WorkflowExecutionService | None = None,
        run_service: RunService | None = None,
        investigation_service: FailureInvestigationService | None = None,
        silent_failure_service: SilentFailureService | None = None,
        inventory_service: EndpointInventoryService | None = None,
        trust_service: TrustAssessmentService | None = None,
        executor: RequestExecutorProtocol | None = None,
        planning_cache: PlanningCache | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Initialize GuidedWorkflowService with pluggable service components."""
        self.intelligence_service = intelligence_service or ProjectIntelligenceService()
        self.contract_service = contract_service or ContractAnalysisService()
        self.planning_service = planning_service or TestPlanningService()
        self.compiler_service = compiler_service
        self.fixture_service = fixture_service
        self.workflow_execution_service = workflow_execution_service
        self.run_service = run_service
        self.investigation_service = (
            investigation_service or FailureInvestigationService()
        )
        self.silent_failure_service = silent_failure_service or SilentFailureService()
        self.inventory_service = inventory_service or EndpointInventoryService()
        self.trust_service = trust_service or TrustAssessmentService()
        self.executor = executor
        self.planning_cache = planning_cache or PlanningCache()
        self._clock = clock or (lambda: datetime.now(UTC))

    def run_guided_workflow(
        self, options: GuidedWorkflowOptions
    ) -> GuidedWorkflowResult:
        """Execute the complete guided API testing pipeline in dependency order.

        Args:
            options: Configuration options specifying spec, repo, target, and flags.

        Returns:
            GuidedWorkflowResult containing manifest, preview, and artifacts.
        """
        now_dt = self._clock()
        wf_id = f"wf-{now_dt.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"

        spec_path = Path(options.spec_path)
        if not spec_path.is_file():
            raise ConfigurationError(
                f"OpenAPI specification file not found: {options.spec_path}"
            )
        spec_hash = compute_file_sha256(spec_path)

        repo_path = Path(options.repo_path) if options.repo_path else None
        repo_hash = (
            compute_file_sha256(repo_path)
            if repo_path and repo_path.is_file()
            else None
        )

        output_dir = (
            Path(options.output_dir)
            if options.output_dir
            else Path(".apiscope/guided") / wf_id
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        manifest = WorkflowManifest(
            workflow_id=wf_id,
            created_at=now_dt.isoformat(),
            spec_path=str(spec_path),
            spec_hash=spec_hash,
            repo_path=str(repo_path) if repo_path else None,
            repo_hash=repo_hash,
            target_origin=options.target_origin,
            environment_name=options.environment_name,
            is_isolated_env=options.is_isolated_env,
            allow_mutation=options.allow_mutation,
            allow_destructive=options.allow_destructive,
            ai_enabled=options.enable_ai,
            model_name=options.ai_model,
        )

        for stg in WorkflowStage:
            manifest.stage_records[stg.value] = StageExecutionRecord(stage=stg)

        # Context containers across stages
        intel_report: ProjectIntelligenceReport | None = None
        contract_report: ContractQualityReport | None = None
        canonical_contract: CanonicalContract | None = None
        parsed_spec: OpenAPISpec | None = None
        test_plan: TestPlan | None = None
        compiled_collection: TestCollection | None = None
        compilation_report: CompilationReport | None = None
        prepared_collection: TestCollection | None = None
        fixture_report: FixturePreparationReport | None = None
        preview: ExecutionPreview | None = None
        active_run: RunResult | None = None
        active_workflow: WorkflowExecutionResult | None = None
        inv_report: FailureInvestigationReport | None = None
        silent_report: SilentFailureReport | None = None
        inventory_report: EndpointInventoryReport | None = None
        trust_report: TrustAssessmentReport | None = None

        def _record_stage(
            stage: WorkflowStage,
            status: StageStatus,
            summary: str,
            duration: float,
            warnings: list[str] | None = None,
            errors: list[str] | None = None,
            artifacts: list[str] | None = None,
            details: dict[str, Any] | None = None,
        ) -> None:
            now_iso = self._clock().isoformat()
            rec = manifest.stage_records[stage.value]
            rec.status = status
            rec.finished_at = now_iso
            rec.duration_ms = duration
            rec.summary = summary
            if warnings:
                rec.warnings.extend(warnings)
                manifest.warnings.extend(warnings)
            if errors:
                rec.errors.extend(errors)
                manifest.errors.extend(errors)
            if artifacts:
                rec.artifact_paths.extend(artifacts)
            if details:
                rec.details.update(details)

        # -------------------------------------------------------------
        # STAGE 1: PROJECT_INSPECTION
        # -------------------------------------------------------------
        stg = WorkflowStage.PROJECT_INSPECTION
        t0 = time.perf_counter()
        manifest.stage_records[stg.value].started_at = self._clock().isoformat()
        manifest.stage_records[stg.value].status = StageStatus.RUNNING
        stg_warns: list[str] = []

        try:
            if (
                options.existing_intelligence_file
                and Path(options.existing_intelligence_file).is_file()
            ):
                raw_i = json.loads(
                    Path(options.existing_intelligence_file).read_text(encoding="utf-8")
                )
                intel_report = ProjectIntelligenceReport.from_dict(raw_i)
                stg_warns.append(
                    "Reused pre-computed ProjectIntelligenceReport from disk"
                )
            else:
                intel_report = self.intelligence_service.analyze_target(
                    spec_path=spec_path,
                    repo_path=repo_path,
                    enable_ai=options.enable_ai,
                    provider_type=options.ai_provider,
                    api_key=options.ai_api_key,
                    model_name=options.ai_model,
                )
                if repo_path is None:
                    stg_warns.append(
                        "No source repository provided; inspection ran in spec-only mode"
                    )
                if options.enable_ai and not (
                    options.ai_api_key
                    or "gemini" in (options.ai_provider or "").lower()
                ):
                    stg_warns.append(
                        "AI requested but no API key configured; proceeded deterministically"
                    )

            i_file = output_dir / "project_intelligence_report.json"
            i_file.write_text(
                json.dumps(intel_report.to_dict(), indent=2), encoding="utf-8"
            )
            manifest.artifact_manifest[stg.value] = i_file.name

            _record_stage(
                stage=stg,
                status=StageStatus.COMPLETED_WITH_WARNINGS
                if stg_warns
                else StageStatus.COMPLETED,
                summary=f"Discovered {len(intel_report.endpoints)} endpoint(s), {len(intel_report.modules)} module(s)",
                duration=(time.perf_counter() - t0) * 1000,
                warnings=stg_warns,
                artifacts=[i_file.name],
                details={
                    "endpoints_count": len(intel_report.endpoints),
                    "modules_count": len(intel_report.modules),
                },
            )
        except Exception as err:
            logger.exception("Error during PROJECT_INSPECTION")
            _record_stage(
                stage=stg,
                status=StageStatus.FAILED,
                summary=f"Project inspection failed: {err}",
                duration=(time.perf_counter() - t0) * 1000,
                errors=[str(err)],
            )

        # -------------------------------------------------------------
        # STAGE 2: CONTRACT_ANALYSIS
        # -------------------------------------------------------------
        stg = WorkflowStage.CONTRACT_ANALYSIS
        t0 = time.perf_counter()
        manifest.stage_records[stg.value].started_at = self._clock().isoformat()
        manifest.stage_records[stg.value].status = StageStatus.RUNNING
        stg_warns = []

        try:
            if (
                options.existing_contract_file
                and Path(options.existing_contract_file).is_file()
            ):
                raw_c = json.loads(
                    Path(options.existing_contract_file).read_text(encoding="utf-8")
                )
                contract_report = ContractQualityReport.from_dict(raw_c)
                stg_warns.append("Reused pre-computed ContractQualityReport from disk")
            else:
                contract_report = self.contract_service.analyze_contract(
                    spec_path=spec_path,
                    repo_path=repo_path,
                    intelligence_path=intel_report,
                )

            raw_spec = self.contract_service._load_spec_file(spec_path)
            canonical_contract = ContractNormalizer(
                raw_spec, intelligence_report=intel_report
            ).normalize()
            parsed_spec = load_openapi_spec(spec_path)

            c_file = output_dir / "contract_quality_report.json"
            c_file.write_text(
                json.dumps(contract_report.to_dict(), indent=2), encoding="utf-8"
            )
            manifest.artifact_manifest[stg.value] = c_file.name

            readiness_counts = {"READY": 0, "NEEDS_FIXTURES": 0, "BLOCKED": 0}
            for op in contract_report.operations:
                r_val = getattr(op.readiness, "value", str(op.readiness))
                if r_val in readiness_counts:
                    readiness_counts[r_val] += 1

            if contract_report.findings:
                stg_warns.append(
                    f"Contract audit surfaced {len(contract_report.findings)} quality finding(s)"
                )

            _record_stage(
                stage=stg,
                status=StageStatus.COMPLETED_WITH_WARNINGS
                if stg_warns
                else StageStatus.COMPLETED,
                summary=f"Normalized {len(canonical_contract.operations)} canonical operation(s); readiness: {readiness_counts}",
                duration=(time.perf_counter() - t0) * 1000,
                warnings=stg_warns,
                artifacts=[c_file.name],
                details={
                    "operations_count": len(canonical_contract.operations),
                    "readiness": readiness_counts,
                },
            )
        except Exception as err:
            logger.exception("Error during CONTRACT_ANALYSIS")
            _record_stage(
                stage=stg,
                status=StageStatus.FAILED,
                summary=f"Contract analysis failed: {err}",
                duration=(time.perf_counter() - t0) * 1000,
                errors=[str(err)],
            )

        # -------------------------------------------------------------
        # STAGE 3: TEST_PLANNING
        # -------------------------------------------------------------
        stg = WorkflowStage.TEST_PLANNING
        t0 = time.perf_counter()
        manifest.stage_records[stg.value].started_at = self._clock().isoformat()
        manifest.stage_records[stg.value].status = StageStatus.RUNNING
        stg_warns = []

        try:
            if (
                options.existing_plan_file
                and Path(options.existing_plan_file).is_file()
            ):
                raw_p = json.loads(
                    Path(options.existing_plan_file).read_text(encoding="utf-8")
                )
                test_plan = TestPlan.from_dict(raw_p)
                stg_warns.append("Reused pre-computed TestPlan from disk")
            else:
                test_plan = self.planning_service.plan_tests(
                    spec_path=spec_path,
                    repo_path=repo_path,
                    intelligence_path=str(
                        output_dir / "project_intelligence_report.json"
                    )
                    if intel_report
                    else None,
                    enable_ai=options.enable_ai,
                    provider_type=options.ai_provider,
                    api_key=options.ai_api_key,
                    model_name=options.ai_model,
                    budget=options.ai_budget,
                )

            p_file = output_dir / "test_plan.json"
            p_file.write_text(
                json.dumps(test_plan.to_dict(), indent=2), encoding="utf-8"
            )
            manifest.artifact_manifest[stg.value] = p_file.name

            det_count = sum(
                1
                for s in test_plan.scenarios
                if s.origin == ScenarioOrigin.DETERMINISTIC
            )
            ai_count = sum(
                1 for s in test_plan.scenarios if s.origin == ScenarioOrigin.AI_PROPOSED
            )
            needs_review_count = sum(
                1
                for s in test_plan.scenarios
                if s.approval_status.value.upper() in ("PROPOSED", "UNVERIFIED")
            )

            if ai_count > 0:
                stg_warns.append(
                    f"{ai_count} AI proposed scenario(s) require human review before authorized execution"
                )

            stg_status = (
                StageStatus.NEEDS_REVIEW
                if needs_review_count > 0
                else (
                    StageStatus.COMPLETED_WITH_WARNINGS
                    if stg_warns
                    else StageStatus.COMPLETED
                )
            )

            _record_stage(
                stage=stg,
                status=stg_status,
                summary=f"Synthesized {len(test_plan.scenarios)} scenario(s) ({det_count} deterministic, {ai_count} AI)",
                duration=(time.perf_counter() - t0) * 1000,
                warnings=stg_warns,
                artifacts=[p_file.name],
                details={
                    "total_scenarios": len(test_plan.scenarios),
                    "deterministic_count": det_count,
                    "ai_count": ai_count,
                    "needs_review_count": needs_review_count,
                },
            )
        except Exception as err:
            logger.exception("Error during TEST_PLANNING")
            _record_stage(
                stage=stg,
                status=StageStatus.FAILED,
                summary=f"Test planning failed: {err}",
                duration=(time.perf_counter() - t0) * 1000,
                errors=[str(err)],
            )

        # -------------------------------------------------------------
        # STAGE 4: TEST_COMPILATION
        # -------------------------------------------------------------
        stg = WorkflowStage.TEST_COMPILATION
        t0 = time.perf_counter()
        manifest.stage_records[stg.value].started_at = self._clock().isoformat()
        manifest.stage_records[stg.value].status = StageStatus.RUNNING
        stg_warns = []

        try:
            if test_plan is None:
                raise DefinitionError(
                    "Test compilation cannot proceed without a valid TestPlan"
                )

            compiler = self.compiler_service or TestPlanCompiler(
                contract=canonical_contract
            )
            compiled_collection, compilation_report = compiler.compile_plan(
                plan=test_plan,
                spec_path=spec_path,
                include_proposed=False,
            )

            comp_rep_file = output_dir / "compilation_report.json"
            comp_rep_file.write_text(
                json.dumps(compilation_report.to_dict(), indent=2), encoding="utf-8"
            )
            suite_file = output_dir / "compiled_suite.yaml"
            coll_dict: dict[str, Any] = {
                "name": compiled_collection.name,
                "module": compiled_collection.module,
                "tags": list(compiled_collection.tags),
                "tests": [
                    test_definition_to_dict(t) for t in compiled_collection.tests
                ],
            }
            suite_file.write_text(
                yaml.safe_dump(coll_dict, sort_keys=False), encoding="utf-8"
            )
            manifest.artifact_manifest[stg.value] = comp_rep_file.name

            if compilation_report.blocked_count > 0:
                stg_warns.append(
                    f"{compilation_report.blocked_count} scenario(s) blocked during compilation "
                    f"({compilation_report.needs_review_count} unapproved AI hypotheses)"
                )

            compiled_mods = {t.module for t in compiled_collection.tests if t.module}
            _record_stage(
                stage=stg,
                status=(
                    StageStatus.COMPLETED_WITH_WARNINGS
                    if stg_warns
                    else StageStatus.COMPLETED
                ),
                summary=f"Compiled {len(compiled_collection.tests)} executable test definition(s) across {len(compiled_mods)} module(s)",
                duration=(time.perf_counter() - t0) * 1000,
                warnings=stg_warns,
                artifacts=[comp_rep_file.name, suite_file.name],
                details={
                    "compiled_tests": len(compiled_collection.tests),
                    "compiled_scenarios": compilation_report.compiled_count,
                    "blocked_scenarios": compilation_report.blocked_count,
                },
            )
        except Exception as err:
            logger.exception("Error during TEST_COMPILATION")
            _record_stage(
                stage=stg,
                status=StageStatus.FAILED,
                summary=f"Test compilation failed: {err}",
                duration=(time.perf_counter() - t0) * 1000,
                errors=[str(err)],
            )

        # -------------------------------------------------------------
        # STAGE 5: FIXTURE_PREPARATION
        # -------------------------------------------------------------
        stg = WorkflowStage.FIXTURE_PREPARATION
        t0 = time.perf_counter()
        manifest.stage_records[stg.value].started_at = self._clock().isoformat()
        manifest.stage_records[stg.value].status = StageStatus.RUNNING
        stg_warns = []

        try:
            if test_plan is None:
                raise DefinitionError("Fixture preparation requires a valid TestPlan")

            fixture_svc = self.fixture_service or FixtureService(
                contract=canonical_contract
            )
            prepared_collection, fixture_report = fixture_svc.prepare_tests(
                plan=test_plan,
                spec_path=spec_path,
                fixture_file=options.fixture_file,
                static_fixtures=options.static_fixtures,
                allow_mutating_setup=options.allow_mutation and options.is_isolated_env,
                include_proposed=False,
            )

            fix_rep_file = output_dir / "fixture_preparation_report.json"
            fix_rep_file.write_text(
                json.dumps(fixture_report.to_dict(), indent=2), encoding="utf-8"
            )
            prep_suite_file = output_dir / "prepared_suite.yaml"
            prep_dict: dict[str, Any] = {
                "name": prepared_collection.name,
                "module": prepared_collection.module,
                "tags": list(prepared_collection.tags),
                "tests": [
                    test_definition_to_dict(t) for t in prepared_collection.tests
                ],
            }
            prep_suite_file.write_text(
                yaml.safe_dump(prep_dict, sort_keys=False), encoding="utf-8"
            )
            manifest.artifact_manifest[stg.value] = fix_rep_file.name

            if fixture_report.missing_fixtures:
                stg_warns.append(
                    f"{len(fixture_report.missing_fixtures)} unresolved entity reference(s) detected; dependent tests will be deferred"
                )

            _record_stage(
                stage=stg,
                status=(
                    StageStatus.COMPLETED_WITH_WARNINGS
                    if stg_warns
                    else StageStatus.COMPLETED
                ),
                summary=f"Resolved {len(fixture_report.resolved_fixtures)} fixture(s); prepared {len(fixture_report.planned_workflows)} stateful workflow(s)",
                duration=(time.perf_counter() - t0) * 1000,
                warnings=stg_warns,
                artifacts=[fix_rep_file.name, prep_suite_file.name],
                details={
                    "workflows_count": len(fixture_report.planned_workflows),
                    "resolved_fixtures": len(fixture_report.resolved_fixtures),
                    "unresolved_fixtures": len(fixture_report.missing_fixtures),
                },
            )
        except Exception as err:
            logger.exception("Error during FIXTURE_PREPARATION")
            _record_stage(
                stage=stg,
                status=StageStatus.FAILED,
                summary=f"Fixture preparation failed: {err}",
                duration=(time.perf_counter() - t0) * 1000,
                errors=[str(err)],
            )

        # -------------------------------------------------------------
        # STAGE 6: EXECUTION_PREVIEW (Dry-Run: Zero Network Calls)
        # -------------------------------------------------------------
        stg = WorkflowStage.EXECUTION_PREVIEW
        t0 = time.perf_counter()
        manifest.stage_records[stg.value].started_at = self._clock().isoformat()
        manifest.stage_records[stg.value].status = StageStatus.RUNNING
        stg_warns = []

        try:
            domain_modules = (
                sorted({t.module for t in compiled_collection.tests if t.module})
                if compiled_collection
                else []
            )
            if not domain_modules and intel_report:
                domain_modules = [m.name for m in intel_report.modules]

            readiness_summary = (
                contract_report.readiness_summary if contract_report else {}
            )

            readonly_ops = 0
            mutating_ops = 0
            if canonical_contract:
                for op in canonical_contract.operations:
                    if op.method.upper() in ("GET", "HEAD", "OPTIONS"):
                        readonly_ops += 1
                    else:
                        mutating_ops += 1

            planned_setups: list[str] = []
            planned_cleanups: list[str] = []
            if fixture_report:
                for wf in fixture_report.planned_workflows:
                    planned_setups.extend(f"{s.method} {s.url}" for s in wf.setup_steps)
                    planned_cleanups.extend(
                        f"{c.method} {c.url}" for c in wf.cleanup_steps
                    )

            policy_restrictions: list[str] = []
            auth_blockers: list[str] = []

            if not options.target_origin:
                policy_restrictions.append(
                    "No target origin specified; execution disabled by default"
                )
                auth_blockers.append(
                    "Target origin missing (pass --target-origin to authorize)"
                )
            else:
                policy_restrictions.append(
                    f"Target origin restricted to exact origin: {options.target_origin}"
                )

            if not options.allow_mutation:
                policy_restrictions.append(
                    "ExecutionSafetyPolicy is in strict READ_ONLY mode; mutating requests will be blocked"
                )
                if mutating_ops > 0 and options.execute:
                    auth_blockers.append(
                        f"{mutating_ops} mutating operation(s) require --allow-mutation"
                    )
            else:
                if not options.is_isolated_env:
                    policy_restrictions.append(
                        "Environment isolation unconfirmed; mutations fenced"
                    )
                    auth_blockers.append(
                        "Mutation authorization requires --confirm-isolated-env flag"
                    )

            missing_prereqs: list[str] = []
            if fixture_report and fixture_report.missing_fixtures:
                missing_prereqs.extend(
                    str(r) for r in fixture_report.missing_fixtures[:5]
                )

            rec_actions: list[str] = []
            if auth_blockers:
                rec_actions.append(
                    f"Resolve authorization blockers before execution: {'; '.join(auth_blockers[:2])}"
                )
            if missing_prereqs:
                rec_actions.append(
                    "Configure static test fixtures via --fixture-file or provide database seed"
                )
            if not rec_actions:
                rec_actions.append(
                    "Authorized for controlled execution against target environment"
                )

            is_authorized = (
                bool(options.target_origin)
                and len(auth_blockers) == 0
                and (options.execute and not options.preview_only)
            )

            preview = ExecutionPreview(
                project_name=intel_report.project.name
                if intel_report
                else "API Project",
                domain_modules=domain_modules,
                contract_readiness_summary=readiness_summary,
                total_scenarios_planned=len(test_plan.scenarios) if test_plan else 0,
                deterministic_scenarios_count=sum(
                    1
                    for s in test_plan.scenarios
                    if s.origin == ScenarioOrigin.DETERMINISTIC
                )
                if test_plan
                else 0,
                ai_scenarios_count=sum(
                    1
                    for s in test_plan.scenarios
                    if s.origin == ScenarioOrigin.AI_PROPOSED
                )
                if test_plan
                else 0,
                scenarios_needing_review=sum(
                    1
                    for s in test_plan.scenarios
                    if s.approval_status.value.upper() in ("PROPOSED", "UNVERIFIED")
                )
                if test_plan
                else 0,
                compiled_test_count=len(compiled_collection.tests)
                if compiled_collection
                else 0,
                fixture_readiness_summary={
                    "resolved": len(fixture_report.resolved_fixtures)
                    if fixture_report
                    else 0,
                    "unresolved": len(fixture_report.missing_fixtures)
                    if fixture_report
                    else 0,
                },
                planned_setup_actions=planned_setups[:5],
                planned_cleanup_actions=planned_cleanups[:5],
                readonly_operations_count=readonly_ops,
                mutating_operations_count=mutating_ops,
                policy_restrictions=policy_restrictions,
                missing_prerequisites=missing_prereqs,
                estimated_requests=len(compiled_collection.tests)
                if compiled_collection
                else 0,
                recommended_next_actions=rec_actions,
                target_origin=options.target_origin,
                is_execution_authorized=is_authorized,
                authorization_blockers=auth_blockers,
            )

            prev_file = output_dir / "execution_preview.json"
            prev_file.write_text(
                json.dumps(preview.to_dict(), indent=2), encoding="utf-8"
            )
            manifest.artifact_manifest[stg.value] = prev_file.name

            _record_stage(
                stage=stg,
                status=StageStatus.COMPLETED,
                summary=f"Synthesized dry-run preview (0 HTTP requests issued); execution authorized: {is_authorized}",
                duration=(time.perf_counter() - t0) * 1000,
                artifacts=[prev_file.name],
                details=preview.to_dict(),
            )
        except Exception as err:
            logger.exception("Error during EXECUTION_PREVIEW")
            _record_stage(
                stage=stg,
                status=StageStatus.FAILED,
                summary=f"Execution preview failed: {err}",
                duration=(time.perf_counter() - t0) * 1000,
                errors=[str(err)],
            )

        # -------------------------------------------------------------
        # STAGE 7: CONTROLLED_EXECUTION
        # -------------------------------------------------------------
        stg = WorkflowStage.CONTROLLED_EXECUTION
        t0 = time.perf_counter()
        manifest.stage_records[stg.value].started_at = self._clock().isoformat()

        if options.existing_run_file and Path(options.existing_run_file).is_file():
            # Ingest recorded run evidence directly without performing live requests
            active_run = load_run_file(options.existing_run_file)
            manifest.run_id = active_run.run_id
            _record_stage(
                stage=stg,
                status=StageStatus.COMPLETED,
                summary=f"Ingested pre-recorded execution results from '{Path(options.existing_run_file).name}' ({active_run.total_tests} test(s))",
                duration=(time.perf_counter() - t0) * 1000,
                details={
                    "run_id": active_run.run_id,
                    "total_tests": active_run.total_tests,
                },
            )
        elif options.preview_only or not options.execute:
            _record_stage(
                stage=stg,
                status=StageStatus.SKIPPED,
                summary="Execution skipped; workflow was invoked in dry-run/preview-only mode",
                duration=(time.perf_counter() - t0) * 1000,
            )
        elif not preview or not preview.is_execution_authorized:
            blocker_msg = (
                "; ".join(preview.authorization_blockers)
                if preview
                else "Execution unauthorized"
            )
            _record_stage(
                stage=stg,
                status=StageStatus.BLOCKED,
                summary=f"Execution blocked by safety policy: {blocker_msg}",
                duration=(time.perf_counter() - t0) * 1000,
                errors=[blocker_msg],
            )
        else:
            manifest.stage_records[stg.value].status = StageStatus.RUNNING
            try:
                safety_pol = (
                    ExecutionSafetyPolicy(
                        mode=ExecutionMode.PERMIT_ALL,
                        allow_mutations=True,
                        allow_destructive=options.allow_destructive,
                        allowed_environments={options.environment_name},
                    )
                    if options.allow_mutation and options.is_isolated_env
                    else ExecutionSafetyPolicy.read_only()
                )

                target_pol = TargetAccessPolicy(
                    allowed_targets=[options.target_origin],
                    allowed_hosts=[
                        options.target_origin.split("://")[-1].split(":")[0]
                    ],
                    allow_private_networks=True,
                )

                exec_inst = self.executor or RequestExecutor(policy=target_pol)

                target_env = Environment(
                    name=options.environment_name,
                    variables={"base_url": options.target_origin},
                )

                # Execute stateful workflows if present, else execute collection
                if fixture_report and fixture_report.planned_workflows:
                    wf_svc = (
                        self.workflow_execution_service
                        or WorkflowExecutionService(
                            executor=exec_inst,
                            safety_policy=safety_pol,
                            clock=self._clock,
                        )
                    )
                    workflow_results, active_run = wf_svc.execute_workflows(
                        workflows=fixture_report.planned_workflows,
                        target_origin=options.target_origin,
                        environment=target_env,
                        allow_mutation=options.allow_mutation,
                        confirm_isolated_env=options.is_isolated_env,
                        static_fixtures=options.static_fixtures,
                    )
                else:
                    run_svc = self.run_service or RunService(
                        executor=exec_inst,
                        safety_policy=safety_pol,
                        clock=self._clock,
                    )
                    active_run = run_svc.run_collection(
                        collection=prepared_collection or compiled_collection,
                        environment=target_env,
                    )

                if active_run:
                    manifest.run_id = active_run.run_id
                    run_file = output_dir / "run_results.json"
                    run_file.write_text(
                        json.dumps(active_run.to_dict(), indent=2), encoding="utf-8"
                    )
                    manifest.artifact_manifest[stg.value] = run_file.name

                    has_failures = (
                        active_run.failed_tests > 0 or active_run.error_tests > 0
                    )
                    exec_status = (
                        StageStatus.COMPLETED_WITH_WARNINGS
                        if has_failures
                        else StageStatus.COMPLETED
                    )

                    _record_stage(
                        stage=stg,
                        status=exec_status,
                        summary=f"Executed {active_run.total_tests} test(s) ({active_run.passed_tests} passed, {active_run.failed_tests} failed, {active_run.skipped_tests} skipped)",
                        duration=(time.perf_counter() - t0) * 1000,
                        artifacts=[run_file.name],
                        details={
                            "run_id": active_run.run_id,
                            "passed": active_run.passed_tests,
                            "failed": active_run.failed_tests,
                            "skipped": active_run.skipped_tests,
                        },
                    )
            except Exception as err:
                logger.exception("Error during CONTROLLED_EXECUTION")
                _record_stage(
                    stage=stg,
                    status=StageStatus.FAILED,
                    summary=f"Controlled execution failed: {err}",
                    duration=(time.perf_counter() - t0) * 1000,
                    errors=[str(err)],
                )

        # -------------------------------------------------------------
        # STAGE 8: FAILURE_INVESTIGATION
        # -------------------------------------------------------------
        stg = WorkflowStage.FAILURE_INVESTIGATION
        t0 = time.perf_counter()
        manifest.stage_records[stg.value].started_at = self._clock().isoformat()

        if active_run is None:
            _record_stage(
                stage=stg,
                status=StageStatus.SKIPPED,
                summary="Failure investigation skipped; no execution results available",
                duration=(time.perf_counter() - t0) * 1000,
            )
        else:
            try:
                inv_report = self.investigation_service.investigate_run(
                    run_result=active_run,
                    contract=canonical_contract,
                    fixture_report=fixture_report,
                )
                inv_file = output_dir / "failure_investigation_report.json"
                inv_file.write_text(
                    json.dumps(inv_report.to_dict(), indent=2), encoding="utf-8"
                )
                manifest.artifact_manifest[stg.value] = inv_file.name

                _record_stage(
                    stage=stg,
                    status=StageStatus.COMPLETED,
                    summary=f"Diagnosed {len(inv_report.findings)} failure finding(s) across 9 diagnostic categories",
                    duration=(time.perf_counter() - t0) * 1000,
                    artifacts=[inv_file.name],
                    details={"findings_count": len(inv_report.findings)},
                )
            except Exception as err:
                logger.exception("Error during FAILURE_INVESTIGATION")
                _record_stage(
                    stage=stg,
                    status=StageStatus.FAILED,
                    summary=f"Failure investigation failed: {err}",
                    duration=(time.perf_counter() - t0) * 1000,
                    errors=[str(err)],
                )

        # -------------------------------------------------------------
        # STAGE 9: SILENT_FAILURE_AND_COVERAGE_DIAGNOSTICS
        # -------------------------------------------------------------
        stg = WorkflowStage.SILENT_FAILURE_AND_COVERAGE_DIAGNOSTICS
        t0 = time.perf_counter()
        manifest.stage_records[stg.value].started_at = self._clock().isoformat()

        try:
            silent_report = self.silent_failure_service.analyze(
                run_result=active_run,
                contract=canonical_contract or parsed_spec,
                module=options.module_filter,
            )
            sil_file = output_dir / "silent_failure_report.json"
            sil_file.write_text(
                json.dumps(silent_report.to_dict(), indent=2), encoding="utf-8"
            )

            inventory_report = self.inventory_service.analyze(
                spec=parsed_spec or canonical_contract,
                repo_path=repo_path,
                run_result=active_run,
                test_plan=test_plan.to_dict() if test_plan else None,
                test_collection=compiled_collection,
                module_filter=options.module_filter,
            )
            inv_file = output_dir / "endpoint_inventory_report.json"
            inv_file.write_text(
                json.dumps(inventory_report.to_dict(), indent=2), encoding="utf-8"
            )

            manifest.artifact_manifest[stg.value] = sil_file.name

            _record_stage(
                stage=stg,
                status=StageStatus.COMPLETED,
                summary=(
                    f"Audited {len(silent_report.signals)} silent signal(s); "
                    f"coverage: {inventory_report.coverage_summary.passed_operations_count} passed / "
                    f"{inventory_report.coverage_summary.total_documented_operations} documented operation(s)"
                ),
                duration=(time.perf_counter() - t0) * 1000,
                artifacts=[sil_file.name, inv_file.name],
                details={
                    "silent_signals": len(silent_report.signals),
                    "documented_ops": inventory_report.coverage_summary.total_documented_operations,
                    "passed_ops": inventory_report.coverage_summary.passed_operations_count,
                },
            )
        except Exception as err:
            logger.exception("Error during SILENT_FAILURE_AND_COVERAGE_DIAGNOSTICS")
            _record_stage(
                stage=stg,
                status=StageStatus.FAILED,
                summary=f"Diagnostics failed: {err}",
                duration=(time.perf_counter() - t0) * 1000,
                errors=[str(err)],
            )

        # -------------------------------------------------------------
        # STAGE 10: TRUST_ASSESSMENT
        # -------------------------------------------------------------
        stg = WorkflowStage.TRUST_ASSESSMENT
        t0 = time.perf_counter()
        manifest.stage_records[stg.value].started_at = self._clock().isoformat()

        try:
            trust_report = self.trust_service.assess(
                run_result=active_run,
                spec=parsed_spec,
                canonical_contract=canonical_contract,
                repo_path=repo_path,
                workflow_result=active_workflow,
                investigation_report=inv_report,
                silent_report=silent_report,
                inventory_report=inventory_report,
                fixture_report=fixture_report,
                compilation_report=compilation_report,
                module_filter=options.module_filter,
            )

            tr_json_file = output_dir / "trust_assessment_report.json"
            tr_json_file.write_text(trust_report.to_json(indent=2), encoding="utf-8")
            tr_yaml_file = output_dir / "trust_assessment_report.yaml"
            tr_yaml_file.write_text(trust_report.to_yaml(), encoding="utf-8")
            manifest.artifact_manifest[stg.value] = tr_json_file.name

            critical_findings = (
                trust_report.verdict_counts.get(
                    DiagnosticVerdict.VERIFIED_CONTRACT_MISMATCH.value, 0
                )
                + trust_report.verdict_counts.get(
                    DiagnosticVerdict.VERIFIED_SEMANTIC_MISMATCH.value, 0
                )
                + trust_report.verdict_counts.get(
                    DiagnosticVerdict.SUSPECTED_APPLICATION_DEFECT.value, 0
                )
            )

            tr_status = (
                StageStatus.COMPLETED_WITH_WARNINGS
                if critical_findings > 0
                else StageStatus.COMPLETED
            )

            _record_stage(
                stage=stg,
                status=tr_status,
                summary=(
                    f"Consolidated defensible verdicts: {len(trust_report.findings)} primary finding(s), "
                    f"{trust_report.grouped_symptoms_count} grouped symptom(s); "
                    f"{trust_report.coverage.passed_operations_count}/{trust_report.coverage.total_documented_operations} verified"
                ),
                duration=(time.perf_counter() - t0) * 1000,
                artifacts=[tr_json_file.name, tr_yaml_file.name],
                details={
                    "verdicts": trust_report.verdict_counts,
                    "findings_count": len(trust_report.findings),
                },
            )
        except Exception as err:
            logger.exception("Error during TRUST_ASSESSMENT")
            _record_stage(
                stage=stg,
                status=StageStatus.FAILED,
                summary=f"Trust assessment failed: {err}",
                duration=(time.perf_counter() - t0) * 1000,
                errors=[str(err)],
            )

        # -------------------------------------------------------------
        # STAGE 11: REPORT_EXPORT
        # -------------------------------------------------------------
        stg = WorkflowStage.REPORT_EXPORT
        t0 = time.perf_counter()
        manifest.stage_records[stg.value].started_at = self._clock().isoformat()

        # Derive next recommended actions
        next_actions: list[str] = []
        if preview and preview.authorization_blockers:
            next_actions.extend(preview.authorization_blockers)
        if trust_report and trust_report.remaining_verification_gaps:
            next_actions.extend(trust_report.remaining_verification_gaps[:3])
        if not next_actions:
            next_actions.append("All requested stages completed cleanly")
        manifest.next_actions = next_actions

        # Determine overall workflow status
        has_failed = any(
            r.status == StageStatus.FAILED for r in manifest.stage_records.values()
        )
        is_blocked = (
            manifest.stage_records[WorkflowStage.CONTROLLED_EXECUTION.value].status
            == StageStatus.BLOCKED
        )
        has_warnings = any(
            r.status in (StageStatus.COMPLETED_WITH_WARNINGS, StageStatus.NEEDS_REVIEW)
            for r in manifest.stage_records.values()
        )

        if has_failed:
            manifest.overall_status = StageStatus.FAILED.value
        elif is_blocked:
            manifest.overall_status = StageStatus.BLOCKED.value
        elif has_warnings:
            manifest.overall_status = StageStatus.COMPLETED_WITH_WARNINGS.value
        else:
            manifest.overall_status = StageStatus.COMPLETED.value

        manifest.finished_at = self._clock().isoformat()

        manifest_file_json = output_dir / "workflow_manifest.json"
        manifest_file_json.write_text(manifest.to_json(indent=2), encoding="utf-8")
        manifest_file_yaml = output_dir / "workflow_manifest.yaml"
        manifest_file_yaml.write_text(manifest.to_yaml(), encoding="utf-8")

        _record_stage(
            stage=stg,
            status=StageStatus.COMPLETED,
            summary=f"Exported workflow manifest and {len(manifest.artifact_manifest)} artifact(s) to '{output_dir}'",
            duration=(time.perf_counter() - t0) * 1000,
            artifacts=[manifest_file_json.name, manifest_file_yaml.name],
        )

        # Compute exit code
        exit_code = 0
        if has_failed:
            exit_code = 1
        elif options.strict:
            critical_count = 0
            if trust_report:
                critical_count = (
                    trust_report.verdict_counts.get(
                        DiagnosticVerdict.VERIFIED_CONTRACT_MISMATCH.value, 0
                    )
                    + trust_report.verdict_counts.get(
                        DiagnosticVerdict.VERIFIED_SEMANTIC_MISMATCH.value, 0
                    )
                    + trust_report.verdict_counts.get(
                        DiagnosticVerdict.SUSPECTED_APPLICATION_DEFECT.value, 0
                    )
                )
            if critical_count > 0 or is_blocked or has_warnings:
                exit_code = 2

        return GuidedWorkflowResult(
            manifest=manifest,
            preview=preview,
            intelligence_report=intel_report,
            contract_report=contract_report,
            test_plan=test_plan,
            compilation_report=compilation_report,
            fixture_report=fixture_report,
            run_result=active_run,
            workflow_result=active_workflow,
            investigation_report=inv_report,
            silent_report=silent_report,
            inventory_report=inventory_report,
            trust_report=trust_report,
            is_success=(exit_code == 0),
            exit_code=exit_code,
        )
