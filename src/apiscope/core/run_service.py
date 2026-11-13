"""Run service orchestrating multi-step test suites and request chaining.

Coordinates VariableResolver, RequestExecutor, Extractor, and AssertionEngine
to execute test collections with fault tolerance, runtime secret protection,
deterministic dependency skip logic, and per-row dataset parameterization.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apiscope.definitions.collection import TestCollection
    from apiscope.definitions.environment import Environment

from apiscope.core.assertions.engine import AssertionEngine
from apiscope.core.contracts import (
    AssertionEngineProtocol,
    ExtractorProtocol,
    RequestExecutorProtocol,
    VariableResolverProtocol,
)
from apiscope.core.exceptions import (
    DefinitionError,
)
from apiscope.core.executor import RequestExecutor, _sanitize_url
from apiscope.core.extractor import Extractor
from apiscope.core.logging_config import get_logger, redact_sensitive_text
from apiscope.core.results import CaseResult, CaseStatus, RunResult
from apiscope.core.variables import VariableResolver
from apiscope.definitions.environment import Environment
from apiscope.definitions.models import DefinitionStatus

_VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")

_SECRET_KEYWORDS = (
    "token",
    "secret",
    "password",
    "api_key",
    "apikey",
    "authorization",
    "passwd",
    "credential",
)


def _is_secret_key(key: str) -> bool:
    """Check if a variable or header key name implies sensitive credential data."""
    k_lower = key.lower()
    return any(secret_term in k_lower for secret_term in _SECRET_KEYWORDS)


def _find_referenced_variables(data: Any) -> set[str]:
    """Recursively discover all {{var_name}} references in strings, dicts, and lists."""
    found: set[str] = set()
    if isinstance(data, str):
        for match in _VARIABLE_PATTERN.finditer(data):
            found.add(match.group(1))
    elif isinstance(data, dict):
        for k, v in data.items():
            if isinstance(k, str):
                found |= _find_referenced_variables(k)
            found |= _find_referenced_variables(v)
    elif isinstance(data, (list, tuple)):
        for item in data:
            found |= _find_referenced_variables(item)
    return found


class RunService:
    """Application service for orchestrating automated API test runs."""

    def __init__(
        self,
        executor: RequestExecutorProtocol | None = None,
        resolver: VariableResolverProtocol | None = None,
        assertion_engine: AssertionEngineProtocol | None = None,
        extractor: ExtractorProtocol | None = None,
        clock: Callable[[], datetime] | None = None,
        id_generator: Callable[[], str] | None = None,
        correlation_id_generator: Callable[[], str] | None = None,
        max_run_duration_seconds: float = 300.0,
    ) -> None:
        """Initialize RunService with pluggable execution dependencies.

        Args:
            executor: Request executor conforming to RequestExecutorProtocol.
            resolver: Variable resolver conforming to VariableResolverProtocol.
            assertion_engine: Assertion engine conforming to AssertionEngineProtocol.
            extractor: Response extractor conforming to ExtractorProtocol.
            clock: Time provider returning timezone-aware UTC datetime.
            id_generator: Generator returning unique run ID string.
            correlation_id_generator: Generator returning unique correlation ID string.
            max_run_duration_seconds: Hard ceiling on suite run duration in seconds.
        """
        self._executor = executor or RequestExecutor()
        self._resolver = resolver or VariableResolver()
        self._assertion_engine = assertion_engine or AssertionEngine()
        self._extractor = extractor or Extractor()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_generator = id_generator or (lambda: str(uuid.uuid4()))
        self._correlation_id_generator = correlation_id_generator or (
            lambda: f"corr-{uuid.uuid4().hex[:12]}"
        )
        self._max_run_duration_seconds = max_run_duration_seconds
        self.logger = get_logger("apiscope.services.run_service")

    def _get_secret_values(
        self,
        known_secrets: set[str],
        runtime_vars: dict[str, Any],
    ) -> list[str]:
        """Collect all active raw secret string values for output masking."""
        values: list[str] = []
        for k, v in runtime_vars.items():
            if (k in known_secrets or _is_secret_key(k)) and v is not None:
                str_v = str(v).strip()
                if str_v:
                    values.append(str_v)
        # Sort longest first to avoid partial string replacements
        values.sort(key=len, reverse=True)
        return values

    def _mask_text(self, text: str, secret_values: list[str]) -> str:
        """Mask explicit secret strings and regex credential patterns in text."""
        if not text:
            return text
        sanitized = text
        for s in secret_values:
            if s and s in sanitized:
                sanitized = sanitized.replace(s, "***")
        return redact_sensitive_text(sanitized)

    def _mask_url(
        self,
        url: str,
        known_secrets: set[str],
        runtime_vars: dict[str, Any],
    ) -> str:
        """Sanitize URL userinfo and mask query params containing secret values."""
        clean_url = _sanitize_url(url)
        secret_values = self._get_secret_values(known_secrets, runtime_vars)
        return self._mask_text(clean_url, secret_values)

    def _mask_headers(
        self,
        headers: dict[str, str],
        known_secrets: set[str],
        runtime_vars: dict[str, Any],
    ) -> dict[str, str]:
        """Return a copy of headers with sensitive keys and secret values masked."""
        secret_values = self._get_secret_values(known_secrets, runtime_vars)
        masked: dict[str, str] = {}
        for k, v in headers.items():
            k_lower = k.lower()
            if _is_secret_key(k) or k_lower in (
                "authorization",
                "cookie",
                "set-cookie",
                "proxy-authorization",
            ):
                masked[k] = "***"
            else:
                masked[k] = self._mask_text(str(v), secret_values)
        return masked

    def _mask_extracted(
        self,
        extracted: dict[str, Any],
        known_secrets: set[str],
        runtime_vars: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a copy of extracted variables with sensitive values masked."""
        secret_values = self._get_secret_values(known_secrets, runtime_vars)
        masked: dict[str, Any] = {}
        for k, v in extracted.items():
            if (
                k in known_secrets
                or _is_secret_key(k)
                or isinstance(v, str)
                and any(s == v for s in secret_values if len(s) >= 4)
            ):
                masked[k] = "***"
            else:
                masked[k] = v
        return masked

    def _mask_assertion_dict(
        self,
        res_dict: dict[str, Any],
        known_secrets: set[str],
        runtime_vars: dict[str, Any],
    ) -> dict[str, Any]:
        """Mask sensitive strings in assertion result dictionaries."""
        secret_values = self._get_secret_values(known_secrets, runtime_vars)
        cleaned = dict(res_dict)

        if "message" in cleaned and isinstance(cleaned["message"], str):
            cleaned["message"] = self._mask_text(cleaned["message"], secret_values)

        for field_name in ("actual", "expected"):
            val = cleaned.get(field_name)
            if isinstance(val, str):
                cleaned[field_name] = self._mask_text(val, secret_values)

        return cleaned

    def run_collection(
        self,
        collection: TestCollection,
        environment: Environment | None = None,
        module: str | None = None,
        tags: list[str] | None = None,
        data_rows: list[dict[str, Any]] | None = None,
        data_file: str | Path | None = None,
        base_dir: Path | None = None,
        include_proposed: bool = False,
    ) -> RunResult:
        """Execute a test collection across environments and parameter datasets.

        Args:
            collection: The TestCollection containing tests to execute.
            environment: Optional Environment providing base variable bindings.
            module: Optional module filter to run only matching tests.
            tags: Optional tags filter to run tests matching at least one tag.
            data_rows: Optional explicit list of parameter dictionaries.
            data_file: Optional path to .csv or .json data file for parameterization.
            base_dir: Optional base directory for data_file traversal validation.
            include_proposed: Whether to execute tests with status 'proposed'
                (default False).

        Returns:
            Aggregated RunResult containing structured CaseResult objects.

        Raises:
            DefinitionError: If arguments conflict, datasets exceed limits, or
                data files are malformed.
        """
        from apiscope.definitions.data_loader import MAX_DATA_ROWS, load_data_rows

        # 1. Validate data source arguments
        if data_rows is not None and data_file is not None:
            raise DefinitionError("Specify either data_rows or data_file, not both")

        if data_file is not None:
            effective_base = base_dir if base_dir is not None else collection.base_dir
            data_rows = load_data_rows(data_file, base_dir=effective_base)

        if data_rows is not None:
            if len(data_rows) == 0:
                raise DefinitionError("data_rows must not be empty when provided")
            if len(data_rows) > MAX_DATA_ROWS:
                raise DefinitionError(
                    f"data_rows contains {len(data_rows)} rows, "
                    f"exceeding maximum limit of {MAX_DATA_ROWS}"
                )

        # 2. Filter tests
        if len(collection.tests) == 0 and len(collection.test_files) > 0:
            collection.load_all_tests()

        tests_to_run = list(collection.tests)
        if module is not None and module.strip():
            target_module = module.strip().lower()
            tests_to_run = [
                t for t in tests_to_run if t.module.strip().lower() == target_module
            ]

        if tags is not None and len(tags) > 0:
            tag_set = {t.strip().lower() for t in tags if t.strip()}
            tests_to_run = [
                t
                for t in tests_to_run
                if any(t_tag.strip().lower() in tag_set for t_tag in t.tags)
            ]

        # 3. Setup execution metrics
        started_at = self._clock()
        run_id = self._id_generator()
        env_name = environment.name if environment else None

        # Build execution sequence list
        if data_rows is None:
            sequences: list[tuple[int | None, dict[str, Any]]] = [(None, {})]
        else:
            sequences = [(idx, dict(row)) for idx, row in enumerate(data_rows)]

        all_case_results: list[CaseResult] = []

        # 4. Execute test sequences with strict row variable isolation
        for row_index, row_vars in sequences:
            # Construct fresh runtime variable context for this sequence
            runtime_vars: dict[str, Any] = {}
            if environment is not None:
                runtime_vars.update(environment.variables)
            runtime_vars.update(row_vars)

            known_secrets: set[str] = set()
            if environment is not None:
                known_secrets.update(environment.secrets)
            for k in runtime_vars:
                if _is_secret_key(k):
                    known_secrets.add(k)

            failed_chain_variables: set[str] = set()

            for test_def in tests_to_run:
                # 4.0. Governance enforcement on proposed tests (FR-54)
                if (
                    test_def.status == DefinitionStatus.PROPOSED
                    and not include_proposed
                ):
                    for var in test_def.extract:
                        if var not in runtime_vars:
                            failed_chain_variables.add(var)
                    all_case_results.append(
                        CaseResult(
                            test_name=test_def.name,
                            module=test_def.module,
                            tags=list(test_def.tags),
                            status=CaseStatus.SKIPPED,
                            verified=False,
                            method=str(
                                test_def.request.method.value
                                if hasattr(test_def.request.method, "value")
                                else test_def.request.method
                            ),
                            url=test_def.request.url,
                            status_code=None,
                            elapsed_ms=None,
                            request_headers={},
                            response_headers={},
                            assertion_results=[],
                            extracted_variables={},
                            extraction_errors=[],
                            error_message=None,
                            skip_reason=(
                                "Skipped because test status is 'proposed' "
                                "(approve via 'apiscope approve' or "
                                "pass --include-proposed)"
                            ),
                            row_index=row_index,
                            response_body="",
                        )
                    )
                    continue

                # 4.1. Run duration ceiling enforcement
                current_time = self._clock()
                if (
                    current_time - started_at
                ).total_seconds() > self._max_run_duration_seconds:
                    for var in test_def.extract:
                        if var not in runtime_vars:
                            failed_chain_variables.add(var)
                    all_case_results.append(
                        CaseResult(
                            test_name=test_def.name,
                            module=test_def.module,
                            tags=list(test_def.tags),
                            status=CaseStatus.SKIPPED,
                            verified=False,
                            method=str(test_def.request.method),
                            url=test_def.request.url,
                            status_code=None,
                            elapsed_ms=None,
                            request_headers={},
                            response_headers={},
                            assertion_results=[],
                            extracted_variables={},
                            extraction_errors=[],
                            error_message=None,
                            skip_reason=(
                                f"Run duration exceeded maximum limit "
                                f"of {self._max_run_duration_seconds}s"
                            ),
                            row_index=row_index,
                        )
                    )
                    continue

                # 4.2. Discover variables and check upstream chaining failures
                needed_vars = (
                    _find_referenced_variables(test_def.request.url)
                    | _find_referenced_variables(test_def.request.headers)
                    | _find_referenced_variables(test_def.request.params)
                    | _find_referenced_variables(test_def.request.body)
                )

                missing_vars = [v for v in needed_vars if v not in runtime_vars]
                if missing_vars:
                    # Check if missing variable was an upstream extraction failure
                    chained_missing = [
                        v for v in missing_vars if v in failed_chain_variables
                    ]
                    if chained_missing:
                        skip_var = chained_missing[0]
                        for var in test_def.extract:
                            if var not in runtime_vars:
                                failed_chain_variables.add(var)
                        all_case_results.append(
                            CaseResult(
                                test_name=test_def.name,
                                module=test_def.module,
                                tags=list(test_def.tags),
                                status=CaseStatus.SKIPPED,
                                verified=False,
                                method=str(test_def.request.method),
                                url=self._mask_url(
                                    test_def.request.url, known_secrets, runtime_vars
                                ),
                                status_code=None,
                                elapsed_ms=None,
                                request_headers={},
                                response_headers={},
                                assertion_results=[],
                                extracted_variables={},
                                extraction_errors=[],
                                error_message=None,
                                skip_reason=(
                                    f"Skipped because upstream chained variable "
                                    f"'{{{{{skip_var}}}}}' was not extracted"
                                ),
                                row_index=row_index,
                            )
                        )
                        continue
                    else:
                        # Undefined variable not part of failed upstream chain
                        missing_var = missing_vars[0]
                        err_msg = f"Missing required variable: '{missing_var}'"
                        for var in test_def.extract:
                            if var not in runtime_vars:
                                failed_chain_variables.add(var)
                        all_case_results.append(
                            CaseResult(
                                test_name=test_def.name,
                                module=test_def.module,
                                tags=list(test_def.tags),
                                status=CaseStatus.ERROR,
                                verified=False,
                                method=str(test_def.request.method),
                                url=self._mask_url(
                                    test_def.request.url, known_secrets, runtime_vars
                                ),
                                status_code=None,
                                elapsed_ms=None,
                                request_headers={},
                                response_headers={},
                                assertion_results=[],
                                extracted_variables={},
                                extraction_errors=[],
                                error_message=err_msg,
                                skip_reason=None,
                                row_index=row_index,
                            )
                        )
                        continue

                # 4.3. Convert to HttpRequest with RAW variable context
                runtime_env = Environment(
                    name=env_name or "runtime",
                    variables=dict(runtime_vars),
                    secrets=set(known_secrets),
                )

                http_request = test_def.to_http_request(
                    environment=runtime_env,
                    resolver=self._resolver,
                )

                # Ensure X-Correlation-ID trace header is set (FR-39, NFR-06)
                existing_corr = next(
                    (
                        v
                        for k, v in http_request.headers.items()
                        if k.lower() == "x-correlation-id"
                    ),
                    None,
                )
                if existing_corr is not None:
                    corr_id = existing_corr
                else:
                    corr_id = self._correlation_id_generator()
                    http_request.headers["X-Correlation-ID"] = corr_id

                # 4.4. Execute HTTP Request via RequestExecutor
                try:
                    response = self._executor.execute(http_request)
                except Exception as exc:
                    sanitized_err = self._mask_text(
                        str(exc), self._get_secret_values(known_secrets, runtime_vars)
                    )
                    for var in test_def.extract:
                        if var not in runtime_vars:
                            failed_chain_variables.add(var)
                    all_case_results.append(
                        CaseResult(
                            test_name=test_def.name,
                            module=test_def.module,
                            tags=list(test_def.tags),
                            status=CaseStatus.ERROR,
                            verified=False,
                            method=http_request.method.value
                            if hasattr(http_request.method, "value")
                            else str(http_request.method),
                            url=self._mask_url(
                                http_request.url, known_secrets, runtime_vars
                            ),
                            status_code=None,
                            elapsed_ms=None,
                            request_headers=self._mask_headers(
                                http_request.headers, known_secrets, runtime_vars
                            ),
                            response_headers={},
                            assertion_results=[],
                            extracted_variables={},
                            extraction_errors=[],
                            error_message=sanitized_err,
                            skip_reason=None,
                            row_index=row_index,
                            correlation_id=corr_id,
                        )
                    )
                    continue

                # 4.5. Assertion Evaluation
                assertion_summary = self._assertion_engine.evaluate(
                    response,
                    test_def.assertions,
                )

                # 4.6. Response Extraction
                if hasattr(self._extractor, "extract_all"):
                    extracted_dict, extraction_errors = self._extractor.extract_all(
                        response,
                        test_def.extract,
                    )
                else:
                    extracted_dict = {}
                    extraction_errors = []
                    for v_name, v_expr in test_def.extract.items():
                        extracted_val = self._extractor.extract(response, v_expr)
                        if extracted_val is not None:
                            extracted_dict[v_name] = extracted_val

                # 4.7. Outcome Assessment
                if not assertion_summary.verified:
                    status = CaseStatus.FAILED
                    error_message = "Test has no assertions (unverified)"
                elif not assertion_summary.success:
                    status = CaseStatus.FAILED
                    error_message = assertion_summary.message or "Assertions failed"
                elif extraction_errors:
                    status = CaseStatus.FAILED
                    error_message = "; ".join(extraction_errors)
                else:
                    status = CaseStatus.PASSED
                    error_message = None

                # 4.8. Context Propagation (RAW values passed forward only if PASSED)
                if status == CaseStatus.PASSED:
                    for k, v in extracted_dict.items():
                        runtime_vars[k] = v
                        if _is_secret_key(k) or k in known_secrets:
                            known_secrets.add(k)
                else:
                    for var in test_def.extract:
                        if var not in runtime_vars:
                            failed_chain_variables.add(var)

                # 4.9. Assemble Masked CaseResult
                serialized_assertions = [
                    self._mask_assertion_dict(
                        r.to_dict() if hasattr(r, "to_dict") else dict(r),
                        known_secrets,
                        runtime_vars,
                    )
                    for r in assertion_summary.results
                ]

                resp_body_str = response.body or "" if hasattr(response, "body") else ""
                masked_body = self._mask_text(
                    resp_body_str,
                    self._get_secret_values(known_secrets, runtime_vars),
                )[:4096]

                all_case_results.append(
                    CaseResult(
                        test_name=test_def.name,
                        module=test_def.module,
                        tags=list(test_def.tags),
                        status=status,
                        verified=assertion_summary.verified,
                        method=http_request.method.value
                        if hasattr(http_request.method, "value")
                        else str(http_request.method),
                        url=self._mask_url(
                            http_request.url, known_secrets, runtime_vars
                        ),
                        status_code=response.status_code,
                        elapsed_ms=response.elapsed_ms,
                        request_headers=self._mask_headers(
                            http_request.headers, known_secrets, runtime_vars
                        ),
                        response_headers=self._mask_headers(
                            response.headers, known_secrets, runtime_vars
                        ),
                        assertion_results=serialized_assertions,
                        extracted_variables=self._mask_extracted(
                            extracted_dict, known_secrets, runtime_vars
                        ),
                        extraction_errors=list(extraction_errors),
                        error_message=self._mask_text(
                            error_message,
                            self._get_secret_values(known_secrets, runtime_vars),
                        )
                        if error_message
                        else None,
                        skip_reason=None,
                        row_index=row_index,
                        response_body=masked_body,
                        correlation_id=corr_id,
                    )
                )

        # 5. Compile aggregate RunResult
        finished_at = self._clock()
        duration_ms = max(0.0, (finished_at - started_at).total_seconds() * 1000.0)

        passed_count = sum(1 for r in all_case_results if r.status == CaseStatus.PASSED)
        failed_count = sum(1 for r in all_case_results if r.status == CaseStatus.FAILED)
        error_count = sum(1 for r in all_case_results if r.status == CaseStatus.ERROR)
        skipped_count = sum(
            1 for r in all_case_results if r.status == CaseStatus.SKIPPED
        )

        return RunResult(
            run_id=run_id,
            environment_name=env_name,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            total_tests=len(all_case_results),
            passed_tests=passed_count,
            failed_tests=failed_count,
            error_tests=error_count,
            skipped_tests=skipped_count,
            results=all_case_results,
        )
