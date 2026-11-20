param([string]$Directory, [string]$Zip, [string]$Hash)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'windows_clean_vm_gate.ps1')
Assert-CandidateMatchesArchive -CandidateDirectory $Directory -CandidateZip $Zip -ExpectedSha256 $Hash
