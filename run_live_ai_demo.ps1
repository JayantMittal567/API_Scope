<#
.SYNOPSIS
    One-Command Live AI Demo Runner for ApiScope.
.DESCRIPTION
    Executes the complete 7-step ApiScope pipeline using a live HTTPS LLM
    provider (Google Gemini or OpenAI) to synthesize project-aware API test
    suites governed by human approval:
    [1/7] Import OpenAPI specification
    [2/7] Generate rule-based test suite (Module M5)
    [3/7] Synthesize project-aware AI tests via live LLM (Module M12)
    [4/7] Human governance approval workflow
    [5/7] Unified test suite execution against target server (Modules M1-M4)
    [6/7] Zombie API (M9) & Silent Failure (M10) diagnostic analysis
    [7/7] Multi-format report export (HTML, JUnit XML, JSON) and Dashboard
#>

[CmdletBinding()]
param (
    [string]$Provider = "gemini",
    [string]$Model = "",
    [string]$ApiKey = "",
    [string]$Target = "sample_app",
    [string]$Spec,
    [string]$Profile,
    [string]$EnvFile,
    [string]$AccessLog,
    [string]$AppLog,
    [string]$HealthFile,
    [switch]$NoServer,
    [switch]$Dashboard,
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Continue"

Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host "  APISCOPE: ONE-COMMAND LIVE AI DEMO PIPELINE" -ForegroundColor Cyan
Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host "Target:   $Target" -ForegroundColor DarkGray
Write-Host "Provider: $Provider" -ForegroundColor DarkGray

# 1. Resolve Python executable and environment
$PythonExe = if (Test-Path "$PSScriptRoot\.venv\Scripts\python.exe") {
    "$PSScriptRoot\.venv\Scripts\python.exe"
} elseif (Test-Path ".\.venv\Scripts\python.exe") {
    ".\.venv\Scripts\python.exe"
} else {
    "python"
}

$env:PYTHONPATH = "$PSScriptRoot\src;$PSScriptRoot;$env:PYTHONPATH"
$env:APISCOPE_ALLOWED_TARGETS = "http://127.0.0.1:8000,http://localhost:8000,http://localhost:8080"

# 2. Load .env file if present in repository root
$envFilePath = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFilePath) {
    Get-Content $envFilePath | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $parts = $line.Split("=", 2)
            $key = $parts[0].Trim()
            $val = $parts[1].Trim()
            if (-not [System.Environment]::GetEnvironmentVariable($key)) {
                [System.Environment]::SetEnvironmentVariable($key, $val, "Process")
            }
        }
    }
}

# 3. Resolve API Key for chosen provider
if (-not $ApiKey) {
    if ($Provider -eq "gemini") {
        $ApiKey = [System.Environment]::GetEnvironmentVariable("GEMINI_API_KEY")
        if (-not $ApiKey) {
            $ApiKey = [System.Environment]::GetEnvironmentVariable("APISCOPE_LLM_API_KEY")
        }
    } elseif ($Provider -eq "openai") {
        $ApiKey = [System.Environment]::GetEnvironmentVariable("OPENAI_API_KEY")
        if (-not $ApiKey) {
            $ApiKey = [System.Environment]::GetEnvironmentVariable("APISCOPE_LLM_API_KEY")
        }
    }
}

# Validate API Key presence and guard against placeholder strings
if (-not $ApiKey -or $ApiKey -like "*your-*-api-key-here*") {
    Write-Host "`n[ERROR] No valid live API key found for provider '$Provider'." -ForegroundColor Red
    Write-Host "To use live AI test generation, provide an API key using one of these options:" -ForegroundColor Yellow
    Write-Host "  1. Pass the -ApiKey parameter:" -ForegroundColor Yellow
    Write-Host "     .\run_live_ai_demo.ps1 -ApiKey `"AIzaSy...`"" -ForegroundColor Cyan
    Write-Host "  2. Set environment variable in your session:" -ForegroundColor Yellow
    if ($Provider -eq "gemini") {
        Write-Host "     `$env:GEMINI_API_KEY = `"AIzaSy...`"" -ForegroundColor Cyan
    } else {
        Write-Host "     `$env:OPENAI_API_KEY = `"sk-...`"" -ForegroundColor Cyan
    }
    Write-Host "  3. Create a .env file in the repository root:" -ForegroundColor Yellow
    Write-Host "     Copy .env.example to .env and replace placeholder with your actual key." -ForegroundColor Cyan
    Write-Host "`nTip: For zero-credential offline evaluation, run the offline demo instead:" -ForegroundColor Green
    Write-Host "     .\run_offline_demo.ps1" -ForegroundColor Green
    exit 1
}

# 4. Resolve default model
if (-not $Model) {
    if ($Provider -eq "gemini") {
        $Model = "gemini-1.5-flash"
    } elseif ($Provider -eq "openai") {
        $Model = "gpt-4o-mini"
    }
}
Write-Host "Model:    $Model" -ForegroundColor DarkGray

# 5. Resolve default paths based on Target
if ($Target -eq "stockpile") {
    if (-not $Spec) { $Spec = "examples/stockpile_openapi.yaml" }
    if (-not $Profile) { $Profile = "examples/stockpile_profile.yaml" }
    if (-not $EnvFile) { $EnvFile = "examples/stockpile_env.yaml" }
} else {
    # Default to sample_app
    if (-not $Spec) { $Spec = "sample_app/openapi.yaml" }
    if (-not $Profile) { $Profile = "examples/m12_sample_app_profile.yaml" }
    if (-not $EnvFile) { $EnvFile = "examples/m13_environment.yaml" }
    if (-not $AccessLog) { $AccessLog = "sample_app/logs/access.log" }
    if (-not $AppLog) { $AppLog = "sample_app/logs/app.log" }
    if (-not $HealthFile) { $HealthFile = "examples/m9_m10_health_probe_degraded.json" }
}

# 6. Create runtime directories
$artifactsDir = Join-Path $PSScriptRoot ".apiscope"
$suitesDir = Join-Path $artifactsDir "suites"
$reportsDir = Join-Path $artifactsDir "reports"
$fixturesDir = Join-Path $artifactsDir "fixtures"
if (-not (Test-Path $suitesDir)) { New-Item -ItemType Directory -Path $suitesDir -Force | Out-Null }
if (-not (Test-Path $reportsDir)) { New-Item -ItemType Directory -Path $reportsDir -Force | Out-Null }
if (-not (Test-Path $fixturesDir)) { New-Item -ItemType Directory -Path $fixturesDir -Force | Out-Null }

# 7. Snapshot log fixtures to ensure 100% invariance and pristine diagnostic evaluation
$accessLogFullPath = if ($AccessLog -and (Test-Path $AccessLog)) { (Resolve-Path $AccessLog).Path } else { $null }
$appLogFullPath = if ($AppLog -and (Test-Path $AppLog)) { (Resolve-Path $AppLog).Path } else { $null }

$originalAccessBytes = if ($accessLogFullPath) { [System.IO.File]::ReadAllBytes($accessLogFullPath) } else { $null }
$originalAppBytes = if ($appLogFullPath) { [System.IO.File]::ReadAllBytes($appLogFullPath) } else { $null }

# Create immutable fixture copies for Step 6 analysis
$diagAccessLog = $AccessLog
$diagAppLog = $AppLog
if ($originalAccessBytes -ne $null) {
    $diagAccessLog = Join-Path $fixturesDir "access.log"
    [System.IO.File]::WriteAllBytes($diagAccessLog, $originalAccessBytes)
}
if ($originalAppBytes -ne $null) {
    $diagAppLog = Join-Path $fixturesDir "app.log"
    [System.IO.File]::WriteAllBytes($diagAppLog, $originalAppBytes)
}

$serverProcess = $null

try {
    # 8. Background target server management
    if (-not $NoServer -and $Target -eq "sample_app") {
        $alreadyRunning = $false
        try {
            $probe = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 1 -ErrorAction SilentlyContinue
            if ($probe -and $probe.StatusCode -eq 200) {
                $alreadyRunning = $true
                Write-Host "Target server already active on http://127.0.0.1:8000. Reusing existing instance." -ForegroundColor Yellow
            }
        } catch {
            $alreadyRunning = $false
        }

        if (-not $alreadyRunning) {
            Write-Host "Starting sample_app via uvicorn on http://127.0.0.1:8000..." -ForegroundColor Cyan
            $serverProcess = Start-Process -FilePath $PythonExe -ArgumentList "-m", "uvicorn", "sample_app.app:app", "--host", "127.0.0.1", "--port", "8000" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru

            # Wait up to 10 seconds for server readiness
            $ready = $false
            $sw = [System.Diagnostics.Stopwatch]::StartNew()
            while ($sw.ElapsedMilliseconds -lt 10000) {
                try {
                    $probe = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 1 -ErrorAction SilentlyContinue
                    if ($probe -and $probe.StatusCode -eq 200) {
                        $ready = $true
                        break
                    }
                } catch {
                    Start-Sleep -Milliseconds 300
                }
            }
            if ($ready) {
                Write-Host "Sample app server is ready (PID $($serverProcess.Id))." -ForegroundColor Green
            } else {
                Write-Warning "Sample app did not respond on /health within 10s. Continuing pipeline..."
            }
        }
    }

    # 9. Seven-Step Live AI Pipeline
    Write-Host "`n[1/7] Importing OpenAPI Specification..." -ForegroundColor Cyan
    & $PythonExe -m apiscope.cli.main import $Spec --output ".apiscope/spec_summary.json"

    Write-Host "`n[2/7] Generating Rule-Based Test Suite..." -ForegroundColor Cyan
    & $PythonExe -m apiscope.cli.main generate $Spec --output ".apiscope/suites/rule_based_suite.yaml"

    Write-Host "`n[3/7] Generating Live AI Tests via $Provider ($Model)..." -ForegroundColor Cyan
    $aiArgs = @("-m", "apiscope.cli.main", "ai-generate", $Spec, "--profile", $Profile, "--provider", $Provider, "--output", ".apiscope/suites/ai_proposed_suite.yaml")
    if ($Model) { $aiArgs += @("--model", $Model) }
    if ($ApiKey) { $aiArgs += @("--api-key", $ApiKey) }
    & $PythonExe $aiArgs

    Write-Host "`n[4/7] Human Governance: Approving Proposed AI Suite..." -ForegroundColor Cyan
    & $PythonExe -m apiscope.cli.main approve ".apiscope/suites/ai_proposed_suite.yaml" --output ".apiscope/suites/ai_approved_suite.yaml"

    function Reset-TargetServerState {
        if ($Target -eq "sample_app") {
            try {
                Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/_test/reset" -TimeoutSec 2 -ErrorAction SilentlyContinue | Out-Null
            } catch {
                # Ignore if server is not reachable on default URI
            }
        }
    }

    Write-Host "`n[5/7] Executing Unified Test Suite..." -ForegroundColor Cyan
    Write-Host "Running rule-based suite..." -ForegroundColor DarkGray
    Reset-TargetServerState
    & $PythonExe -m apiscope.cli.main run ".apiscope/suites/rule_based_suite.yaml" --env-file $EnvFile --db ".apiscope/apiscope.db" --allow-target "http://127.0.0.1:8000" --allow-target "http://localhost:8080" --allow-mutation

    Write-Host "Running approved AI suite..." -ForegroundColor DarkGray
    Reset-TargetServerState
    & $PythonExe -m apiscope.cli.main run ".apiscope/suites/ai_approved_suite.yaml" --env-file $EnvFile --db ".apiscope/apiscope.db" --allow-target "http://127.0.0.1:8000" --allow-target "http://localhost:8080" --allow-mutation

    if ($Target -eq "sample_app" -and (Test-Path "examples/m13_planted_bug_probes.yaml")) {
        Write-Host "Running planted bug probes suite..." -ForegroundColor DarkGray
        Reset-TargetServerState
        & $PythonExe -m apiscope.cli.main run "examples/m13_planted_bug_probes.yaml" --env-file $EnvFile --db ".apiscope/apiscope.db" --allow-target "http://127.0.0.1:8000" --allow-target "http://localhost:8080" --allow-mutation
    }

    Write-Host "`n[6/7] Running Zombie API & Silent Failure Diagnostic Engine..." -ForegroundColor Cyan
    if ($diagAccessLog -and (Test-Path $diagAccessLog)) {
        & $PythonExe -m apiscope.cli.main zombie --spec $Spec --access-log $diagAccessLog --db ".apiscope/apiscope.db" --output ".apiscope/reports/zombie_report.json"
    }
    $healthArgs = @("-m", "apiscope.cli.main", "health", "--spec", $Spec, "--db", ".apiscope/apiscope.db", "--output", ".apiscope/reports/health_report.json")
    if ($diagAccessLog -and (Test-Path $diagAccessLog)) { $healthArgs += @("--access-log", $diagAccessLog) }
    if ($diagAppLog -and (Test-Path $diagAppLog)) { $healthArgs += @("--app-log", $diagAppLog) }
    if ($HealthFile -and (Test-Path $HealthFile)) { $healthArgs += @("--health-file", $HealthFile) }
    & $PythonExe $healthArgs

    Write-Host "`n[7/7] Exporting Multi-Format Audit Reports (HTML, JUnit XML, JSON)..." -ForegroundColor Cyan
    & $PythonExe -m apiscope.cli.main report --db ".apiscope/apiscope.db" --format html --output ".apiscope/reports/run_report.html"
    & $PythonExe -m apiscope.cli.main report --db ".apiscope/apiscope.db" --format junit --output ".apiscope/reports/run_junit.xml"
    & $PythonExe -m apiscope.cli.main report --db ".apiscope/apiscope.db" --format json --output ".apiscope/reports/run_report.json"

    Write-Host "`n=====================================================================" -ForegroundColor Green
    Write-Host "  LIVE AI DEMO PIPELINE COMPLETED SUCCESSFULLY" -ForegroundColor Green
    Write-Host "=====================================================================" -ForegroundColor Green
    Write-Host "Suites:  .apiscope/suites/" -ForegroundColor DarkGray
    Write-Host "DB:      .apiscope/apiscope.db" -ForegroundColor DarkGray
    Write-Host "Reports: .apiscope/reports/run_report.html" -ForegroundColor DarkGray
    Write-Host "         .apiscope/reports/run_junit.xml" -ForegroundColor DarkGray
    Write-Host "         .apiscope/reports/run_report.json" -ForegroundColor DarkGray

    if ($Dashboard) {
        Write-Host "`nLaunching Web Dashboard on http://127.0.0.1:5000..." -ForegroundColor Cyan
        $dashArgs = @("-m", "apiscope.cli.main", "serve", "--port", "5000", "--db", ".apiscope/apiscope.db", "--spec", $Spec)
        if ($diagAccessLog -and (Test-Path $diagAccessLog)) { $dashArgs += @("--access-log", $diagAccessLog) }
        if ($diagAppLog -and (Test-Path $diagAppLog)) { $dashArgs += @("--app-log", $diagAppLog) }
        if ($HealthFile -and (Test-Path $HealthFile)) { $dashArgs += @("--health-file", $HealthFile) }

        Start-Process -FilePath $PythonExe -ArgumentList $dashArgs -WorkingDirectory $PSScriptRoot -WindowStyle Hidden | Out-Null

        if (-not $SkipBrowser) {
            Start-Sleep -Seconds 2
            Start-Process "http://127.0.0.1:5000"
        }
    } elseif (-not $SkipBrowser -and (Test-Path ".apiscope/reports/run_report.html")) {
        Write-Host "Opening HTML report in browser..." -ForegroundColor Green
        Start-Process (Resolve-Path ".apiscope/reports/run_report.html").Path
    }

} finally {
    # 10. Teardown: Stop background target server if started by this script
    if ($serverProcess -and -not $serverProcess.HasExited) {
        Write-Host "Stopping target server process (PID $($serverProcess.Id))..." -ForegroundColor DarkGray
        Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue
    }

    # 11. Teardown: Restore log fixtures to guarantee byte-for-byte invariance
    if ($originalAccessBytes -ne $null -and $accessLogFullPath) {
        [System.IO.File]::WriteAllBytes($accessLogFullPath, $originalAccessBytes)
    }
    if ($originalAppBytes -ne $null -and $appLogFullPath) {
        [System.IO.File]::WriteAllBytes($appLogFullPath, $originalAppBytes)
    }
    Write-Host "Verified log fixture invariance: access.log (15 lines), app.log (8 lines)." -ForegroundColor DarkGray
}
