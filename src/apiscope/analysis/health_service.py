"""Module health verdict calculation and endpoint coverage evaluation service.

Implements Module M8: computes functional pass rates, contract conformance,
negative/boundary testing, authentication checks, latency metrics, and OpenAPI
endpoint coverage to determine evidence-based 4-state health verdicts.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from apiscope.analysis.health_models import (
    CategoryCheckStats,
    HealthVerdict,
    ModuleHealthReport,
)
from apiscope.analysis.path_normalizer import extract_url_path, match_path_to_spec
from apiscope.core.exceptions import ConfigurationError
from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.generators.openapi_importer import OpenAPISpec

DEFAULT_COVERAGE_THRESHOLD_PCT: float = 80.0
DEFAULT_LATENCY_TOLERANCE_FACTOR: float = 1.5


class HealthService:
    """Application service for computing module health verdicts and coverage."""

    def __init__(
        self,
        clock: Callable[[], datetime] | None = None,
        default_coverage_threshold_pct: float = DEFAULT_COVERAGE_THRESHOLD_PCT,
        latency_tolerance_factor: float = DEFAULT_LATENCY_TOLERANCE_FACTOR,
    ) -> None:
        """Initialize the HealthService.

        Args:
            clock: Optional clock callable returning timezone-aware UTC datetime.
            default_coverage_threshold_pct: Default minimum endpoint coverage % (0-100).
            latency_tolerance_factor: Tolerance multiplier for p95 baseline regression.
        """
        self._clock = clock or (lambda: datetime.now(UTC))
        self._default_coverage_threshold = default_coverage_threshold_pct
        self._latency_tolerance_factor = latency_tolerance_factor

    def evaluate_module(
        self,
        module: str,
        run_result: RunResult,
        openapi_spec: OpenAPISpec | None = None,
        coverage_threshold_pct: float | None = None,
        baseline_p95_ms: float | None = None,
        zombie_count: int = 0,
        zombie_evidence: list[str] | None = None,
        silent_failure_count: int = 0,
        silent_failure_evidence: list[str] | None = None,
    ) -> ModuleHealthReport:
        """Evaluate the comprehensive health of a specific module.

        Args:
            module: Target module name to evaluate.
            run_result: Aggregated test run execution result.
            openapi_spec: Optional imported OpenAPI specification.
            coverage_threshold_pct: Optional custom coverage threshold percentage.
            baseline_p95_ms: Optional baseline p95 latency in ms for drift detection.
            zombie_count: Count of detected zombie endpoints.
            zombie_evidence: Optional list of zombie detection evidence messages.
            silent_failure_count: Count of detected silent failures.
            silent_failure_evidence: Optional list of silent failure evidence messages.

        Returns:
            Evidence-backed ModuleHealthReport.

        Raises:
            ConfigurationError: If coverage_threshold_pct is not between 0.0 and 100.0.
        """
        threshold = (
            coverage_threshold_pct
            if coverage_threshold_pct is not None
            else self._default_coverage_threshold
        )
        if not (0.0 <= threshold <= 100.0):
            raise ConfigurationError("Coverage threshold must be between 0.0 and 100.0")

        module_clean = module.strip()
        module_lower = module_clean.lower()
        module_cases = [
            c for c in run_result.results if c.module.strip().lower() == module_lower
        ]

        # 1. Test case counts
        total_tests = len(module_cases)
        passed_tests = sum(1 for c in module_cases if c.status == CaseStatus.PASSED)
        failed_tests = sum(1 for c in module_cases if c.status == CaseStatus.FAILED)
        error_tests = sum(1 for c in module_cases if c.status == CaseStatus.ERROR)
        skipped_tests = sum(1 for c in module_cases if c.status == CaseStatus.SKIPPED)

        # 2. Assertion counts across all cases in this module
        total_assertions = 0
        passed_assertions = 0
        for case in module_cases:
            for a in case.assertion_results:
                total_assertions += 1
                passed = (
                    a.get("passed", False)
                    if isinstance(a, dict)
                    else getattr(a, "passed", False)
                )
                if passed:
                    passed_assertions += 1

        pass_rate_pct = (
            100.0
            if total_assertions == 0
            else round(passed_assertions / total_assertions * 100.0, 2)
        )

        # 3. Endpoint coverage calculation
        spec_endpoints_total = 0
        spec_endpoints_tested = 0
        untested_endpoints: list[str] = []
        if openapi_spec is not None:
            module_endpoints = openapi_spec.filter_by_module(module_clean)
            spec_endpoints_total = len(module_endpoints)
            all_spec_paths = sorted(
                set(openapi_spec.path_methods.keys())
                | {ep.path for ep in openapi_spec.endpoints}
            )

            tested_operations: set[tuple[str, str]] = set()
            for case in module_cases:
                path = extract_url_path(case.url)
                matched_template = match_path_to_spec(path, all_spec_paths)
                if matched_template is not None:
                    tested_operations.add((case.method.upper(), matched_template))

            for ep in module_endpoints:
                op_key = (ep.method.upper(), ep.path)
                if op_key in tested_operations:
                    spec_endpoints_tested += 1
                else:
                    untested_endpoints.append(f"{ep.method.upper()} {ep.path}")

            untested_endpoints.sort()

            if spec_endpoints_total == 0:
                endpoint_coverage_pct = 100.0 if total_tests > 0 else 0.0
            else:
                endpoint_coverage_pct = round(
                    spec_endpoints_tested / spec_endpoints_total * 100.0, 2
                )
        else:
            endpoint_coverage_pct = 100.0 if total_tests > 0 else 0.0

        # 4. Category breakdowns
        functional_stats = self._calc_functional_stats(module_cases)
        negative_boundary_stats = self._calc_negative_boundary_stats(module_cases)
        auth_stats = self._calc_auth_stats(module_cases)
        contract_stats = self._calc_contract_stats(module_cases)

        # 5. Latency metrics & drift
        latencies = [c.elapsed_ms for c in module_cases if c.elapsed_ms is not None]
        avg_latency_ms = round(sum(latencies) / len(latencies), 2) if latencies else 0.0
        if latencies:
            sorted_lat = sorted(latencies)
            p95_idx = int(math.ceil(0.95 * len(sorted_lat))) - 1
            p95_idx = max(0, min(p95_idx, len(sorted_lat) - 1))
            p95_latency_ms = round(sorted_lat[p95_idx], 2)
        else:
            p95_latency_ms = 0.0

        latency_regression = False
        if baseline_p95_ms is not None and baseline_p95_ms > 0:
            latency_regression = p95_latency_ms > (
                baseline_p95_ms * self._latency_tolerance_factor
            )

        # 6. Deterministic Verdict Decision Table (FR-32)
        failures: list[str] = []
        warnings: list[str] = []
        incomplete_reason = ""

        # Step 1: Check INCOMPLETE
        if total_tests == 0:
            verdict = HealthVerdict.INCOMPLETE
            if openapi_spec is not None and spec_endpoints_total == 0:
                incomplete_reason = (
                    f"No endpoints or tests found for module '{module_clean}'"
                )
            else:
                incomplete_reason = "No test cases executed for module"
        elif (
            openapi_spec is not None
            and spec_endpoints_total > 0
            and endpoint_coverage_pct < threshold
        ):
            verdict = HealthVerdict.INCOMPLETE
            incomplete_reason = (
                f"Endpoint coverage {endpoint_coverage_pct:.1f}% is below threshold "
                f"{threshold:.1f}%"
            )
        elif any(not c.verified for c in module_cases):
            verdict = HealthVerdict.INCOMPLETE
            incomplete_reason = "One or more test cases have no assertions (unverified)"
        # Step 2: Check UNHEALTHY
        elif any(c.status != CaseStatus.PASSED for c in module_cases):
            verdict = HealthVerdict.UNHEALTHY
            for c in module_cases:
                if c.status != CaseStatus.PASSED:
                    desc = f"{c.test_name}: {c.status.value}"
                    if c.error_message:
                        desc += f" - {c.error_message}"
                    elif c.skip_reason:
                        desc += f" - {c.skip_reason}"
                    failures.append(desc)
        # Step 3: Check HEALTHY_WITH_WARNINGS
        elif silent_failure_count > 0 or zombie_count > 0 or latency_regression:
            verdict = HealthVerdict.HEALTHY_WITH_WARNINGS
            if silent_failure_count > 0:
                warnings.append(f"{silent_failure_count} silent failure(s) detected")
                if silent_failure_evidence:
                    warnings.extend(silent_failure_evidence)
            if zombie_count > 0:
                warnings.append(f"{zombie_count} zombie endpoint(s) detected")
                if zombie_evidence:
                    warnings.extend(zombie_evidence)
            if latency_regression and baseline_p95_ms is not None:
                max_tolerated = baseline_p95_ms * self._latency_tolerance_factor
                warnings.append(
                    f"Latency regression detected: p95 {p95_latency_ms:.1f} ms "
                    f"exceeds baseline {baseline_p95_ms:.1f} ms * "
                    f"{self._latency_tolerance_factor:.1f} ({max_tolerated:.1f} ms)"
                )
        # Step 4: Otherwise HEALTHY
        else:
            verdict = HealthVerdict.HEALTHY

        # 7. Evidence & Summary Construction (FR-33)
        evidence: list[str] = []
        if openapi_spec is not None:
            evidence.append(
                f"{module_clean}: {spec_endpoints_tested}/{spec_endpoints_total} "
                f"endpoints tested ({endpoint_coverage_pct:.1f}% coverage)"
            )
        else:
            evidence.append(
                f"{module_clean}: {total_tests} test cases executed "
                "(no OpenAPI spec provided)"
            )

        evidence.append(
            f"Verified against {total_assertions} checks "
            f"({passed_assertions}/{total_assertions} assertions passed) "
            f"with {endpoint_coverage_pct:.1f}% endpoint coverage"
        )
        evidence.append(
            f"Test cases: {passed_tests}/{total_tests} passed "
            f"(functional: {functional_stats.passed}/{functional_stats.total}, "
            f"negative/boundary: {negative_boundary_stats.passed}/"
            f"{negative_boundary_stats.total}, "
            f"auth: {auth_stats.passed}/{auth_stats.total})"
        )
        lat_evidence = (
            f"Latency: avg {avg_latency_ms:.1f} ms, p95 {p95_latency_ms:.1f} ms"
        )
        if baseline_p95_ms is not None:
            lat_evidence += f" (baseline p95: {baseline_p95_ms:.1f} ms)"
        evidence.append(lat_evidence)
        evidence.append(
            f"Signals: {zombie_count} zombies, {silent_failure_count} silent failures"
        )

        # Construct summary adhering to strict wording rules
        if verdict == HealthVerdict.HEALTHY:
            summary = (
                f"Module '{module_clean}' is HEALTHY: verified against "
                f"{total_assertions} checks with {endpoint_coverage_pct:.1f}% "
                "endpoint coverage."
            )
        elif verdict == HealthVerdict.HEALTHY_WITH_WARNINGS:
            if silent_failure_count > 0:
                summary = (
                    f"Module '{module_clean}' PASS WITH WARNINGS: APIs healthy, "
                    f"internal issue suspected; verified against {total_assertions} "
                    f"checks with {endpoint_coverage_pct:.1f}% endpoint coverage."
                )
            else:
                summary = (
                    f"Module '{module_clean}' is HEALTHY WITH WARNINGS: "
                    f"verified against {total_assertions} checks with "
                    f"{endpoint_coverage_pct:.1f}% endpoint coverage; "
                    f"{len(warnings)} warning condition(s) detected."
                )
        elif verdict == HealthVerdict.UNHEALTHY:
            summary = (
                f"Module '{module_clean}' is UNHEALTHY: verified against "
                f"{total_assertions} checks with {endpoint_coverage_pct:.1f}% "
                f"endpoint coverage; {failed_tests + error_tests + skipped_tests} "
                "test case(s) failed/errored/skipped."
            )
        else:  # INCOMPLETE
            summary = (
                f"Module '{module_clean}' evaluation INCOMPLETE: {incomplete_reason}; "
                f"verified against {total_assertions} checks with "
                f"{endpoint_coverage_pct:.1f}% endpoint coverage."
            )

        return ModuleHealthReport(
            module=module_clean,
            verdict=verdict,
            run_id=run_result.run_id,
            evaluated_at=self._clock(),
            total_tests=total_tests,
            passed_tests=passed_tests,
            failed_tests=failed_tests,
            error_tests=error_tests,
            skipped_tests=skipped_tests,
            total_assertions=total_assertions,
            passed_assertions=passed_assertions,
            pass_rate_pct=pass_rate_pct,
            spec_endpoints_total=spec_endpoints_total,
            spec_endpoints_tested=spec_endpoints_tested,
            endpoint_coverage_pct=endpoint_coverage_pct,
            untested_endpoints=untested_endpoints,
            coverage_threshold_pct=threshold,
            functional_stats=functional_stats,
            contract_stats=contract_stats,
            negative_boundary_stats=negative_boundary_stats,
            auth_stats=auth_stats,
            avg_latency_ms=avg_latency_ms,
            p95_latency_ms=p95_latency_ms,
            baseline_p95_ms=baseline_p95_ms,
            latency_regression=latency_regression,
            zombie_count=zombie_count,
            silent_failure_count=silent_failure_count,
            summary=summary,
            evidence=evidence,
            warnings=warnings,
            failures=failures,
        )

    def evaluate_all_modules(
        self,
        run_result: RunResult,
        openapi_spec: OpenAPISpec | None = None,
        coverage_threshold_pct: float | None = None,
        baseline_p95_ms: float | None = None,
        zombie_findings: Sequence[Any] | None = None,
        silent_signals: Sequence[Any] | None = None,
    ) -> list[ModuleHealthReport]:
        """Evaluate all distinct modules discovered in execution results and spec.

        Args:
            run_result: Aggregated test run execution result.
            openapi_spec: Optional imported OpenAPI specification.
            coverage_threshold_pct: Optional custom coverage threshold percentage.
            baseline_p95_ms: Optional baseline p95 latency in ms for drift detection.
            zombie_findings: Optional sequence of ZombieFinding records.
            silent_signals: Optional sequence of SilentFailureSignal records.

        Returns:
            List of ModuleHealthReport instances sorted by module name.
        """
        modules: set[str] = set()
        for case in run_result.results:
            clean_m = case.module.strip()
            if clean_m:
                modules.add(clean_m)

        if openapi_spec is not None:
            for ep in openapi_spec.endpoints:
                clean_m = ep.module.strip()
                if clean_m:
                    modules.add(clean_m)

        sorted_modules = sorted(modules)
        reports: list[ModuleHealthReport] = []
        for m in sorted_modules:
            m_lower = m.lower()
            m_zombies = (
                [f for f in zombie_findings if f.module.lower() == m_lower]
                if zombie_findings
                else []
            )
            m_silents = (
                [s for s in silent_signals if s.module.lower() == m_lower]
                if silent_signals
                else []
            )
            reports.append(
                self.evaluate_module(
                    module=m,
                    run_result=run_result,
                    openapi_spec=openapi_spec,
                    coverage_threshold_pct=coverage_threshold_pct,
                    baseline_p95_ms=baseline_p95_ms,
                    zombie_count=len(m_zombies),
                    zombie_evidence=(
                        [f.details for f in m_zombies] if m_zombies else None
                    ),
                    silent_failure_count=len(m_silents),
                    silent_failure_evidence=[
                        f"{s.description}: {s.evidence}" for s in m_silents
                    ]
                    if m_silents
                    else None,
                )
            )
        return reports

    @staticmethod
    def _calc_functional_stats(cases: list[CaseResult]) -> CategoryCheckStats:
        """Calculate statistics for functional test cases (not negative or boundary)."""
        func_cases = [
            c
            for c in cases
            if not any(t.lower() in ("negative", "boundary") for t in c.tags)
        ]
        total = len(func_cases)
        passed = sum(1 for c in func_cases if c.status == CaseStatus.PASSED)
        failed = total - passed
        pass_rate = 100.0 if total == 0 else round(passed / total * 100.0, 2)
        return CategoryCheckStats(
            category="functional",
            total=total,
            passed=passed,
            failed=failed,
            pass_rate_pct=pass_rate,
        )

    @staticmethod
    def _calc_negative_boundary_stats(
        cases: list[CaseResult],
    ) -> CategoryCheckStats:
        """Calculate statistics for negative and boundary test cases."""
        nb_cases = [
            c
            for c in cases
            if any(t.lower() in ("negative", "boundary") for t in c.tags)
        ]
        total = len(nb_cases)
        passed = sum(1 for c in nb_cases if c.status == CaseStatus.PASSED)
        failed = total - passed
        pass_rate = 100.0 if total == 0 else round(passed / total * 100.0, 2)
        return CategoryCheckStats(
            category="negative_boundary",
            total=total,
            passed=passed,
            failed=failed,
            pass_rate_pct=pass_rate,
        )

    @staticmethod
    def _calc_auth_stats(cases: list[CaseResult]) -> CategoryCheckStats:
        """Calculate statistics for authentication and authorization test cases."""
        auth_cases = [
            c
            for c in cases
            if any(t.lower() == "auth" for t in c.tags) or "auth" in c.test_name.lower()
        ]
        total = len(auth_cases)
        passed = sum(1 for c in auth_cases if c.status == CaseStatus.PASSED)
        failed = total - passed
        pass_rate = 100.0 if total == 0 else round(passed / total * 100.0, 2)
        return CategoryCheckStats(
            category="auth",
            total=total,
            passed=passed,
            failed=failed,
            pass_rate_pct=pass_rate,
        )

    @staticmethod
    def _calc_contract_stats(cases: list[CaseResult]) -> CategoryCheckStats:
        """Calculate statistics for contract conformance assertions (status, schema)."""
        contract_types = {"status", "json_schema", "schema"}
        total = 0
        passed = 0
        for case in cases:
            for a in case.assertion_results:
                rule_type = (
                    a.get("rule_type") or a.get("assertion_type", "")
                    if isinstance(a, dict)
                    else getattr(a, "rule_type", getattr(a, "assertion_type", ""))
                )
                if str(rule_type).lower() in contract_types:
                    total += 1
                    is_passed = (
                        a.get("passed", False)
                        if isinstance(a, dict)
                        else getattr(a, "passed", False)
                    )
                    if is_passed:
                        passed += 1

        failed = total - passed
        pass_rate = 100.0 if total == 0 else round(passed / total * 100.0, 2)
        return CategoryCheckStats(
            category="contract",
            total=total,
            passed=passed,
            failed=failed,
            pass_rate_pct=pass_rate,
        )
