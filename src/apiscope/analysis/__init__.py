"""Analysis Layer for ApiScope.

Houses specialized diagnostic services:
- Health monitoring, metrics computation, and 4-state health verdicts (M8).
- OpenAPI endpoint path matching and dynamic normalization.
- Zombie endpoint detection across UNUSED, UNDOCUMENTED, UNTESTED, and
  DEPRECATED-BUT-CALLED categories (M9).
- Silent failure detection across Signals S1 to S4 with automated secret
  redaction (M10).
"""

from apiscope.analysis.evaluator_experiment import (
    EvaluationExperimentReport,
    StrategyEvaluationMetrics,
    render_evaluation_markdown,
    run_planted_bug_experiment,
)
from apiscope.analysis.health_models import (
    CategoryCheckStats,
    HealthVerdict,
    ModuleHealthReport,
)
from apiscope.analysis.health_service import (
    DEFAULT_COVERAGE_THRESHOLD_PCT,
    DEFAULT_LATENCY_TOLERANCE_FACTOR,
    HealthService,
)
from apiscope.analysis.path_normalizer import (
    compile_spec_path_regex,
    extract_url_path,
    match_path_to_spec,
    normalize_unmatched_path,
)
from apiscope.analysis.silent_failure_service import (
    AppLogEntry,
    SilentFailureReport,
    SilentFailureService,
    SilentFailureSignal,
    SilentSignalType,
    load_app_log,
    parse_app_log,
)
from apiscope.analysis.zombie_service import (
    MAX_LOG_FILE_BYTES,
    MAX_LOG_LINES,
    AccessLogEntry,
    ZombieClass,
    ZombieFinding,
    ZombieReport,
    ZombieService,
    load_access_log,
    parse_access_log,
)

__all__ = [
    "DEFAULT_COVERAGE_THRESHOLD_PCT",
    "DEFAULT_LATENCY_TOLERANCE_FACTOR",
    "MAX_LOG_FILE_BYTES",
    "MAX_LOG_LINES",
    "AccessLogEntry",
    "AppLogEntry",
    "CategoryCheckStats",
    "EvaluationExperimentReport",
    "HealthService",
    "HealthVerdict",
    "ModuleHealthReport",
    "SilentFailureReport",
    "SilentFailureService",
    "SilentFailureSignal",
    "SilentSignalType",
    "StrategyEvaluationMetrics",
    "ZombieClass",
    "ZombieFinding",
    "ZombieReport",
    "ZombieService",
    "compile_spec_path_regex",
    "extract_url_path",
    "load_access_log",
    "load_app_log",
    "match_path_to_spec",
    "normalize_unmatched_path",
    "parse_access_log",
    "parse_app_log",
    "render_evaluation_markdown",
    "run_planted_bug_experiment",
]
