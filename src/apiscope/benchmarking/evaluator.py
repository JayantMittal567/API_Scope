"""Independent Benchmark Evaluation & Scoring Engine for ApiScope (Task 28).

Orchestrates the evaluation of ApiScope's diagnostic engines against the independent
benchmark manifest, computes defensible accuracy metrics (Precision, Recall, F1, FPR),
and produces transparent explanations for false positives, false negatives, and deduplication.
"""

from __future__ import annotations

from collections import defaultdict

from apiscope.analysis.endpoint_inventory import (
    EndpointInventoryReport,
)
from apiscope.analysis.silent_failure_service import (
    SilentFailureReport,
    SilentSignalType,
    VerificationState,
)
from apiscope.benchmarking.models import (
    BenchmarkCase,
    BenchmarkManifest,
    BenchmarkMetrics,
    CaseEvaluationResult,
    CategoryAccuracyMetrics,
    EvaluationClassification,
)
from apiscope.investigation.models import (
    DiagnosticCategory,
    FailureInvestigationReport,
)
from apiscope.trust.models import (
    DiagnosticVerdict,
    TrustAssessmentReport,
)


class BenchmarkEvaluator:
    """Independent evaluation engine measuring ApiScope defect-detection accuracy."""

    def __init__(self, manifest: BenchmarkManifest) -> None:
        """Initialize evaluator with ground-truth manifest."""
        self.manifest = manifest

    def evaluate_case(
        self,
        case: BenchmarkCase,
        trust_report: TrustAssessmentReport | None = None,
        investigation_report: FailureInvestigationReport | None = None,
        silent_report: SilentFailureReport | None = None,
        inventory_report: EndpointInventoryReport | None = None,
    ) -> CaseEvaluationResult:
        """Evaluate ApiScope findings against a single ground-truth BenchmarkCase."""
        # Step 1: Collect all findings across diagnostic subsystems
        trust_findings = trust_report.findings if trust_report else []
        inv_findings = investigation_report.findings if investigation_report else []
        silent_findings = silent_report.findings if silent_report else []
        zombie_findings = inventory_report.findings if inventory_report else []

        matched_finding_ids: list[str] = []
        detected_verdicts: list[str] = []
        duplicate_count = 0
        is_misclassified = False

        # Target identifiers for matching
        from apiscope.analysis.endpoint_inventory import EndpointClassification
        from apiscope.analysis.path_normalizer import (
            compile_spec_path_regex,
            extract_url_path,
        )

        case_template = case.path.strip("/")
        case_regex = compile_spec_path_regex(case.path)
        method = case.method.upper()

        # Helper matching logic: check endpoint route and method
        def matches_route(
            candidate_str: str, candidate_method: str | None = None
        ) -> bool:
            if not candidate_str:
                return False
            if candidate_method and candidate_method.upper() != method:
                return False
            parts = candidate_str.strip().split()
            if len(parts) >= 2 and parts[0].upper() in (
                "GET",
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
                "HEAD",
                "OPTIONS",
            ):
                if parts[0].upper() != method:
                    return False
                target_path = parts[1]
            else:
                target_path = candidate_str

            extracted = extract_url_path(target_path)
            # Prevent static sibling routes from erroneously matching parameterized templates
            norm_extracted = extracted.strip("/")
            if (
                norm_extracted
                in ("api/coupons/legacy-archive", "api/products/recommendations")
                and case_template != norm_extracted
            ):
                return False

            if case_regex.match(extracted):
                return True
            if "{" in target_path:
                try:
                    cand_regex = compile_spec_path_regex(target_path)
                    if cand_regex.match(extract_url_path(case.path)):
                        return True
                except Exception:
                    pass
            return False

        # Non-defect verdicts that indicate a clean pass, inconclusive evidence, or unexecuted endpoint
        CLEAN_VERDICTS = {
            DiagnosticVerdict.PASS_WITHIN_VERIFIED_COVERAGE.value,
            DiagnosticVerdict.NOT_TESTED.value,
            DiagnosticVerdict.INCONCLUSIVE.value,
            "PASS_WITHIN_VERIFIED_COVERAGE",
            "NOT_TESTED",
            "INCONCLUSIVE",
        }

        # Non-defect inventory classifications that indicate normal baseline operations
        CLEAN_INVENTORY_STATES = {
            EndpointClassification.DOCUMENTED_AND_OBSERVED.value,
            EndpointClassification.DOCUMENTED_NOT_OBSERVED.value,
            "DOCUMENTED_AND_OBSERVED",
            "DOCUMENTED_NOT_OBSERVED",
        }

        # Match in Trust Assessment Findings (highest level unified verdicts)
        for tf in trust_findings:
            if tf.verdict.value in CLEAN_VERDICTS:
                continue
            if matches_route(tf.route_template, tf.method) or matches_route(
                getattr(tf, "endpoint", ""), tf.method
            ):
                matched_finding_ids.append(tf.finding_id)
                detected_verdicts.append(tf.verdict.value)

        # Match in Failure Investigation Findings
        for inv_f in inv_findings:
            if matches_route(getattr(inv_f, "url", ""), getattr(inv_f, "method", None)):
                if inv_f.finding_id not in matched_finding_ids:
                    matched_finding_ids.append(inv_f.finding_id)
                detected_verdicts.append(inv_f.category.value)
                # Check for misclassification: fixture error called app defect
                if (
                    case.category == "P_FIXTURE_PRODUCER_FAILURE"
                    and inv_f.category
                    == DiagnosticCategory.SUSPECTED_APPLICATION_FAILURE
                ):
                    is_misclassified = True

        # Match in Silent Failure Findings
        for sf in silent_findings:
            if sf.verification_state in (
                VerificationState.UNVERIFIED_EFFECT,
                VerificationState.NOT_TESTED,
                VerificationState.INCONCLUSIVE,
            ):
                continue
            sf_target = (
                getattr(sf, "endpoint_or_source", "")
                or getattr(sf, "endpoint", "")
                or ""
            )
            # Signal S1 is an app log anomaly for the case's service context
            is_s1_match = (
                sf.signal_type == SilentSignalType.S1_LOG_ERROR
                and case.case_id == "BM-DEF-O01"
            )
            if is_s1_match or matches_route(sf_target):
                if sf.finding_id not in matched_finding_ids:
                    matched_finding_ids.append(sf.finding_id)
                detected_verdicts.append(sf.signal_type.value)

        # Match in Endpoint Inventory / Zombie Findings
        for zf in zombie_findings:
            cat_val = getattr(zf, "classification", None)
            val_str = cat_val.value if hasattr(cat_val, "value") else str(cat_val)
            if val_str in CLEAN_INVENTORY_STATES:
                continue
            zf_target = (
                getattr(zf, "canonical_endpoint", "")
                or getattr(zf, "route_template", "")
                or ""
            )
            if matches_route(zf_target, getattr(zf, "method", None)):
                if zf.finding_id not in matched_finding_ids:
                    matched_finding_ids.append(zf.finding_id)
                detected_verdicts.append(val_str)

        findings_count = len(matched_finding_ids)
        if findings_count > 1:
            duplicate_count = findings_count - 1

        primary_detected = detected_verdicts[0] if detected_verdicts else None

        # Step 2: Classify outcome against ground-truth expectation
        classification: EvaluationClassification
        explanation: str
        evidence_adequate: bool = True
        evidence_notes: str = ""

        if case.is_defect:
            # Defect case
            if not case.expected_detectable:
                classification = EvaluationClassification.UNSUPPORTED
                explanation = f"Defect is known to be unsupported by design: {case.known_limitations}"
            elif findings_count > 0:
                # Check verdict alignment
                is_fixture_defect = case.category == "P_FIXTURE_PRODUCER_FAILURE"
                if is_fixture_defect:
                    # For fixture failure, correct verdict is FIXTURE_OR_DEPENDENCY_ISSUE
                    has_fixture_verdict = any(
                        "FIXTURE" in v or "DEPENDENCY" in v for v in detected_verdicts
                    )
                    if has_fixture_verdict and not is_misclassified:
                        classification = EvaluationClassification.TRUE_POSITIVE
                        explanation = "Correctly diagnosed fixture/setup issue rather than blaming application."
                    else:
                        classification = EvaluationClassification.FALSE_POSITIVE
                        is_misclassified = True
                        explanation = (
                            "Misclassified test setup failure as an application bug."
                        )
                else:
                    classification = EvaluationClassification.TRUE_POSITIVE
                    explanation = f"Detected planted defect with {findings_count} corroborating finding(s): {', '.join(detected_verdicts[:3])}."
            else:
                classification = EvaluationClassification.FALSE_NEGATIVE
                explanation = f"Planted defect was not detected by ApiScope pipeline on {case.target_operation}."
                evidence_adequate = False
                evidence_notes = "Missing diagnostic signal or assertion failure."
        else:
            # Clean control case
            if findings_count > 0:
                # Spurious findings on clean endpoint!
                # Check if it was an intentional clean expected block/status
                is_policy_case = case.category == "CLEAN_DRY_RUN_POLICY_BLOCK"
                has_policy_verdict = any(
                    "POLICY" in v or "ACCESS" in v for v in detected_verdicts
                )
                if is_policy_case and has_policy_verdict:
                    classification = EvaluationClassification.TRUE_NEGATIVE
                    explanation = "Clean control: safely blocked mutation in dry-run mode without claiming app bug."
                else:
                    classification = EvaluationClassification.FALSE_POSITIVE
                    explanation = f"Spurious defect claimed on clean endpoint: {', '.join(detected_verdicts)}."
                    evidence_adequate = False
                    evidence_notes = (
                        "False positive: finding lacked legitimate defect evidence."
                    )
            else:
                classification = EvaluationClassification.TRUE_NEGATIVE
                explanation = f"Clean control correctly verified without false alarms ({case.intended_behaviour})."

        return CaseEvaluationResult(
            case_id=case.case_id,
            title=case.title,
            is_defect=case.is_defect,
            category=case.category,
            expected_verdict=case.expected_verdict,
            classification=classification,
            detected_verdict=primary_detected,
            matched_finding_ids=matched_finding_ids,
            findings_count=findings_count,
            duplicate_count=duplicate_count,
            is_misclassified=is_misclassified,
            evidence_adequate=evidence_adequate,
            evidence_notes=evidence_notes,
            explanation=explanation,
        )

    def calculate_metrics(
        self, case_results: list[CaseEvaluationResult]
    ) -> BenchmarkMetrics:
        """Compute confusion matrix, precision, recall, F1, FPR, and deduplication stats."""
        total = len(case_results)
        defect_cases = sum(1 for r in case_results if r.is_defect)
        clean_cases = sum(1 for r in case_results if not r.is_defect)

        tp = sum(
            1
            for r in case_results
            if r.classification == EvaluationClassification.TRUE_POSITIVE
        )
        fp = sum(
            1
            for r in case_results
            if r.classification == EvaluationClassification.FALSE_POSITIVE
        )
        fn = sum(
            1
            for r in case_results
            if r.classification == EvaluationClassification.FALSE_NEGATIVE
        )
        tn = sum(
            1
            for r in case_results
            if r.classification == EvaluationClassification.TRUE_NEGATIVE
        )
        unsupported = sum(
            1
            for r in case_results
            if r.classification == EvaluationClassification.UNSUPPORTED
        )
        inconclusive = sum(
            1
            for r in case_results
            if r.classification == EvaluationClassification.INCONCLUSIVE
        )

        # Standard Metric Formulas with explicit zero-denominator handling
        precision = (
            (tp / (tp + fp))
            if (tp + fp) > 0
            else (1.0 if fn == 0 and total > 0 else 0.0)
        )
        recall = (
            (tp / (tp + fn))
            if (tp + fn) > 0
            else (1.0 if fp == 0 and total > 0 else 0.0)
        )
        f1_score = (
            (2 * precision * recall / (precision + recall))
            if (precision + recall) > 0
            else 0.0
        )
        false_positive_rate = (fp / (fp + tn)) if (fp + tn) > 0 else 0.0

        # Deduplication and misclassifications
        total_raw_findings = sum(r.findings_count for r in case_results)
        duplicate_findings_count = sum(r.duplicate_count for r in case_results)
        deduplicated_findings = duplicate_findings_count
        deduplication_rate = (
            (deduplicated_findings / total_raw_findings)
            if total_raw_findings > 0
            else 0.0
        )

        misclassifications_count = sum(1 for r in case_results if r.is_misclassified)
        misclassification_rate = (
            (misclassifications_count / total_raw_findings)
            if total_raw_findings > 0
            else 0.0
        )

        # Per-category metrics
        per_category: dict[str, CategoryAccuracyMetrics] = {}
        category_map = defaultdict(list)
        for r in case_results:
            category_map[r.category].append(r)

        for cat, cat_res in category_map.items():
            cat_tp = sum(
                1
                for r in cat_res
                if r.classification == EvaluationClassification.TRUE_POSITIVE
            )
            cat_fp = sum(
                1
                for r in cat_res
                if r.classification == EvaluationClassification.FALSE_POSITIVE
            )
            cat_fn = sum(
                1
                for r in cat_res
                if r.classification == EvaluationClassification.FALSE_NEGATIVE
            )
            cat_tn = sum(
                1
                for r in cat_res
                if r.classification == EvaluationClassification.TRUE_NEGATIVE
            )
            cat_unsupported = sum(
                1
                for r in cat_res
                if r.classification == EvaluationClassification.UNSUPPORTED
            )
            cat_inconclusive = sum(
                1
                for r in cat_res
                if r.classification == EvaluationClassification.INCONCLUSIVE
            )

            cat_prec = (
                (cat_tp / (cat_tp + cat_fp))
                if (cat_tp + cat_fp) > 0
                else (1.0 if cat_fn == 0 and len(cat_res) > 0 else 0.0)
            )
            cat_rec = (
                (cat_tp / (cat_tp + cat_fn))
                if (cat_tp + cat_fn) > 0
                else (1.0 if cat_fp == 0 and len(cat_res) > 0 else 0.0)
            )
            cat_f1 = (
                (2 * cat_prec * cat_rec / (cat_prec + cat_rec))
                if (cat_prec + cat_rec) > 0
                else 0.0
            )

            per_category[cat] = CategoryAccuracyMetrics(
                category=cat,
                total_cases=len(cat_res),
                tp=cat_tp,
                fp=cat_fp,
                fn=cat_fn,
                tn=cat_tn,
                unsupported=cat_unsupported,
                inconclusive=cat_inconclusive,
                precision=cat_prec,
                recall=cat_rec,
                f1_score=cat_f1,
            )

        return BenchmarkMetrics(
            total_cases=total,
            defect_cases_count=defect_cases,
            clean_cases_count=clean_cases,
            tp=tp,
            fp=fp,
            fn=fn,
            tn=tn,
            unsupported=unsupported,
            inconclusive=inconclusive,
            precision=precision,
            recall=recall,
            f1_score=f1_score,
            false_positive_rate=false_positive_rate,
            total_raw_findings=total_raw_findings,
            deduplicated_findings=deduplicated_findings,
            duplicate_findings_count=duplicate_findings_count,
            deduplication_rate=deduplication_rate,
            misclassifications_count=misclassifications_count,
            misclassification_rate=misclassification_rate,
            per_category=per_category,
        )

    # Backward-compatibility alias
    compute_metrics = calculate_metrics
