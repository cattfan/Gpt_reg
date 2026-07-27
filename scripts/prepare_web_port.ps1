[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 65535)]
    [int]$Port,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedPython
)

$ErrorActionPreference = "Stop"
$expected = (Resolve-Path -LiteralPath $ExpectedPython).Path
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1

if ($null -eq $listener) {
    exit 0
}

$process = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $listener.OwningProcess)
$parent = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $process.ParentProcessId) `
    -ErrorAction SilentlyContinue
$command = [string]$process.CommandLine
$isWebCommand = $command -match "(?i)-m\s+gpt_reg(?:\.cli)?\s+web(?:\s|$)"
$processMatches = [string]$process.ExecutablePath -ieq $expected
$parentMatches = $null -ne $parent -and [string]$parent.ExecutablePath -ieq $expected

if (-not ($isWebCommand -and ($processMatches -or $parentMatches))) {
    throw "Port $Port is used by another application (PID $($process.ProcessId))."
}

Write-Host "Stopping old Gpt_reg PID $($process.ProcessId) on port $Port"
Stop-Process -Id $process.ProcessId -Force
Start-Sleep -Milliseconds 300

if ($parentMatches -and (Get-Process -Id $parent.ProcessId -ErrorAction SilentlyContinue)) {
    Stop-Process -Id $parent.ProcessId -Force
}

$deadline = [DateTime]::UtcNow.AddSeconds(5)
do {
    Start-Sleep -Milliseconds 100
    $remaining = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($null -eq $remaining) {
        exit 0
    }
} while ([DateTime]::UtcNow -lt $deadline)

throw "Gpt_reg PID $($process.ProcessId) did not release port $Port within 5 seconds."
