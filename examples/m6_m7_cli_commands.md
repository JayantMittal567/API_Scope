# ApiScope CLI Commands Reference (Modules M6 & M7)

This document provides Windows PowerShell command recipes for initializing projects, executing test suites, persisting historical run telemetry, and generating multi-format reports.

---

## 1. Display Version

```powershell
python -m apiscope.cli.main version
# Output: ApiScope version 0.1.0
```

---

## 2. Initialize Starter Project (`apiscope init`)

Initializes starter configuration files (`apiscope.env.yaml` and `sample_suite.yaml`) in the specified directory:

```powershell
# Initialize in current directory
python -m apiscope.cli.main init

# Initialize in a target subfolder
python -m apiscope.cli.main init --dir .\my_api_tests

# Overwrite existing configuration files safely
python -m apiscope.cli.main init --dir .\my_api_tests --force
```

---

## 3. Execute Test Suites (`apiscope run`)

Runs API test collections with secret masking, variable substitution, and SQLite telemetry persistence:

```powershell
# Basic execution of a test suite
python -m apiscope.cli.main run .\examples\m4_chaining_suite.yaml

# Execution with custom environment and SQLite history database
python -m apiscope.cli.main run .\examples\m4_chaining_suite.yaml `
  --env-file .\examples\m6_m7_demo_env.yaml `
  --env demo `
  --db .\.apiscope\history.db

# Module and tag filtering
python -m apiscope.cli.main run .\examples\m4_chaining_suite.yaml `
  --module orders `
  --tag chaining

# Data-driven execution with CSV parameterization
python -m apiscope.cli.main run .\examples\m4_chaining_suite.yaml `
  --data-file .\examples\m4_orders_data.csv

# Generating export reports on completion (JSON, JUnit XML, or HTML)
python -m apiscope.cli.main run .\examples\m4_chaining_suite.yaml `
  --output .\reports\latest_run.html `
  --format html

python -m apiscope.cli.main run .\examples\m4_chaining_suite.yaml `
  --output .\reports\junit.xml `
  --format junit

# Enforcing strict mode (treat warnings as exit code 2)
python -m apiscope.cli.main run .\examples\m4_chaining_suite.yaml --strict
```

---

## 4. Query and Export Reports (`apiscope report`)

Generates formatted reports from historical test runs persisted in SQLite:

```powershell
# Print JSON report of the most recent test run to stdout
python -m apiscope.cli.main report --db .\.apiscope\history.db --format json

# Export latest run to a standalone HTML report
python -m apiscope.cli.main report `
  --db .\.apiscope\history.db `
  --format html `
  --output .\reports\report.html

# Export a specific historical run to JUnit XML for CI/CD integration
python -m apiscope.cli.main report `
  --db .\.apiscope\history.db `
  --run-id "<run-id-uuid>" `
  --format junit `
  --output .\reports\junit-results.xml
```

---

## 5. Exit Code Reference (FR-26)

| Exit Code | Meaning | Condition |
|:---:|:---|:---|
| `0` | **Success** | All tests passed (or passed with non-fatal warnings without `--strict`). |
| `1` | **Test Failure** | At least one test failed, errored, or skipped. |
| `2` | **Strict Warning** | All tests passed, but warnings were detected and `--strict` was specified. |
| `3` | **Tool / Configuration Error** | Missing files, malformed YAML, path traversal attempt, 0 matching tests, or database error. |
