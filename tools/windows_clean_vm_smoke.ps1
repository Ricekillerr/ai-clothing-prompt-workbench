# Run only in a disposable, offline Windows VM with a fresh user profile.
param([Parameter(Mandatory = $true)][string]$CandidateDirectory)
$ErrorActionPreference = 'Stop'
foreach ($name in @('python.exe', 'py.exe', 'ffmpeg.exe')) {
    if (Get-Command $name -ErrorAction SilentlyContinue) {
        throw "Clean VM check failed: $name is available on PATH."
    }
}
foreach ($key in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore', 'HKLM:\Software\WOW6432Node\Python\PythonCore')) {
    if ((Test-Path $key) -and (Get-ChildItem $key -ErrorAction SilentlyContinue)) {
        throw 'Clean VM check failed: a registered system Python installation was found.'
    }
}
$candidate = (Resolve-Path -LiteralPath $CandidateDirectory).Path
$python = Join-Path $candidate 'runtime\python.exe'
$smoke = Join-Path $PSScriptRoot 'windows_candidate_smoke.py'
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $smoke)) {
    throw 'Candidate runtime or smoke script is missing.'
}
Write-Host 'Confirm the VM network adapter is disconnected and the VM uses a disposable profile.'
& $python -I $smoke $candidate
if ($LASTEXITCODE -ne 0) { throw "Candidate smoke test failed: $LASTEXITCODE" }
Write-Host 'Clean VM candidate smoke test passed.'
