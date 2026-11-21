# Run only in a disposable, disconnected Windows x64 VM with a fresh profile.
param(
    [Parameter(Mandatory = $true)][string]$CandidateDirectory,
    [Parameter(Mandatory = $true)][string]$CandidateZip
)
$ErrorActionPreference = 'Stop'
$gate = Join-Path $PSScriptRoot 'windows_clean_vm_gate.ps1'
if (-not (Test-Path -LiteralPath $gate -PathType Leaf)) { throw 'Clean VM gate is missing.' }
. $gate
Assert-CleanMachine
$candidate = (Resolve-Path -LiteralPath $CandidateDirectory -ErrorAction Stop).Path
$archive = (Resolve-Path -LiteralPath $CandidateZip -ErrorAction Stop).Path
Assert-CandidateMatchesArchive -CandidateDirectory $candidate -CandidateZip $archive `
    -ExpectedSha256 'c94f169616c2c93356bc5e03e7bbe94d13dd51b0b24937f0fc0eb11a680f5e54'
$python = Join-Path $candidate 'runtime\python.exe'
$smoke = Join-Path $PSScriptRoot 'windows_candidate_smoke.py'
if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or -not (Test-Path -LiteralPath $smoke -PathType Leaf)) {
    throw 'Candidate runtime or smoke script is missing.'
}
$monitor = Start-Job -ArgumentList $gate -ScriptBlock {
    param($GatePath)
    $ErrorActionPreference = 'Stop'
    . $GatePath
    while ($true) { Assert-NetworkDisconnected; Start-Sleep -Seconds 1 }
}
try {
    Start-Sleep -Seconds 2
    if ($monitor.State -ne 'Running') { throw 'Network monitoring could not start.' }
    & $python -I $smoke $candidate
    if ($LASTEXITCODE -ne 0) { throw "Candidate smoke test failed: $LASTEXITCODE" }
    if ($monitor.State -ne 'Running') { throw 'Network isolation monitoring failed during the test.' }
    Assert-NetworkDisconnected
} finally {
    Stop-Job -Job $monitor -ErrorAction SilentlyContinue
    Remove-Job -Job $monitor -Force -ErrorAction SilentlyContinue
}
Write-Host 'Offline clean VM candidate smoke test passed.'
