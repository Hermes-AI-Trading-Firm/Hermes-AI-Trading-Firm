# Hermes — Register Windows Task Scheduler Jobs
#
# Creates three scheduled tasks under the \Hermes\ folder in Task Scheduler:
#
#   Hermes-Daily-Import-Score      Daily   06:00  --stages import,score
#   Hermes-Weekly-Score-Report     Sunday  07:00  --stages score,report
#   Hermes-Monthly-Full-Pipeline   1st     08:00  --stages import,score,report
#
# Usage (run once from an elevated PowerShell prompt):
#   cd C:\Users\ebo13\Hermes-AI-Trading-Firm
#   powershell -ExecutionPolicy Bypass -File research\scheduler\register_tasks.ps1
#
# Re-running is safe — existing tasks are replaced.
# To remove all Hermes tasks: Unregister-ScheduledTask -TaskPath \Hermes\ -Confirm:$false

#Requires -RunAsAdministrator

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------

# PSScriptRoot = research\scheduler\  →  parent × 2 = project root
$ProjectRoot = (Get-Item $PSScriptRoot).Parent.Parent.FullName
$Runner      = Join-Path $ProjectRoot "research\scheduler\run_pipeline.py"

# Prefer the project .venv if it exists; fall back to PATH python
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $Python = $VenvPython
} else {
    $Python = (Get-Command python -ErrorAction SilentlyContinue)?.Source
    if (-not $Python) {
        Write-Error "python.exe not found. Activate your virtual environment or add Python to PATH."
        exit 1
    }
}

Write-Host ""
Write-Host "Hermes Task Scheduler Setup"
Write-Host "  Project root : $ProjectRoot"
Write-Host "  Python       : $Python"
Write-Host "  Runner       : $Runner"
Write-Host ""

# ---------------------------------------------------------------------------
# Helper: build and register one task
# ---------------------------------------------------------------------------

function Register-HermesTask {
    param(
        [string]$TaskName,
        [string]$Description,
        [string]$Stages,
        [object]$Trigger
    )

    $action = New-ScheduledTaskAction `
        -Execute $Python `
        -Argument "`"$Runner`" --stages $Stages" `
        -WorkingDirectory $ProjectRoot

    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
        -StartWhenAvailable `
        -RunOnlyIfNetworkAvailable:$false `
        -MultipleInstances IgnoreNew

    $principal = New-ScheduledTaskPrincipal `
        -UserId $env:USERNAME `
        -LogonType Interactive `
        -RunLevel Highest

    $task = New-ScheduledTask `
        -Action   $action `
        -Trigger  $Trigger `
        -Settings $settings `
        -Principal $principal `
        -Description $Description

    Register-ScheduledTask `
        -TaskName $TaskName `
        -TaskPath "\Hermes\" `
        -InputObject $task `
        -Force | Out-Null

    Write-Host "  [OK] \Hermes\$TaskName"
}

# ---------------------------------------------------------------------------
# 1. Daily — 06:00 — import + score
#    Bring in new NT8 exports and re-score all strategies.
# ---------------------------------------------------------------------------

$dailyTrigger = New-ScheduledTaskTrigger -Daily -At "06:00"

Register-HermesTask `
    -TaskName   "Hermes-Daily-Import-Score" `
    -Description "Daily 06:00 — import NT8 exports + re-score all strategies" `
    -Stages     "import,score" `
    -Trigger    $dailyTrigger

# ---------------------------------------------------------------------------
# 2. Weekly — Sunday 07:00 — score + report
#    Refresh scores and generate weekly firm summary for human review.
# ---------------------------------------------------------------------------

$weeklyTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "07:00"

Register-HermesTask `
    -TaskName   "Hermes-Weekly-Score-Report" `
    -Description "Weekly Sunday 07:00 — refresh scores + generate strategy reports for weekly review" `
    -Stages     "score,report" `
    -Trigger    $weeklyTrigger

# ---------------------------------------------------------------------------
# 3. Monthly — 1st of month 08:00 — full pipeline
#    Comprehensive pass: import → score → report for monthly human review session.
# ---------------------------------------------------------------------------

$monthlyTrigger = New-ScheduledTaskTrigger -Monthly -DaysOfMonth 1 -At "08:00"

Register-HermesTask `
    -TaskName   "Hermes-Monthly-Full-Pipeline" `
    -Description "Monthly 1st 08:00 — full pipeline: import + score + report for monthly human review" `
    -Stages     "import,score,report" `
    -Trigger    $monthlyTrigger

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "Registered tasks:"
Get-ScheduledTask -TaskPath "\Hermes\" | Format-Table TaskName, State -AutoSize

Write-Host "Done. Open Task Scheduler and look under \Hermes\ to verify."
Write-Host "Logs will appear in: $ProjectRoot\logs\"
Write-Host ""
