$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'windows_clean_vm_gate.ps1')
$script:adapterUp = $false
$script:defaultRoute = $false
$script:registryPresent = $false
$script:aliasPresent = $false
$script:commandPresent = $false
$script:packagePresent = $false
$script:locationPresent = $false
$script:ipv6Route = $false
$script:queryFails = $false
function Get-NetAdapter { param([switch]$IncludeHidden, $ErrorAction) if ($script:queryFails) { throw 'query unavailable' }; if ($script:adapterUp) { [pscustomobject]@{ Status='Up'; InterfaceDescription='ethernet' } } }
function Get-NetRoute { param($PolicyStore, $ErrorAction) if ($script:defaultRoute) { [pscustomobject]@{ DestinationPrefix='0.0.0.0/0' } }; if ($script:ipv6Route) { [pscustomobject]@{ DestinationPrefix='::/0' } } }
function Get-CimInstance { param($ClassName, $ErrorAction) [pscustomobject]@{Manufacturer='Test'; Model='Virtual Machine'; HypervisorPresent=$true} }
function Get-Command { param($Name, $ErrorAction) if ($script:commandPresent) { return 'python.exe' } }
function Get-AppxPackage { param([switch]$AllUsers, $ErrorAction) if ($script:packagePresent) { [pscustomobject]@{ Name='PythonSoftwareFoundation.Python.3.12' } } }
function Test-Path { param($Path, $LiteralPath, $ErrorAction) if ($Path -like 'HK*') { return $script:registryPresent }; return $false }
function Get-ChildItem { param($Path, $ErrorAction) if ($Path -like 'HK*' -and $script:registryPresent) { return 'registered' } }
function Get-Item { param($Path, [switch]$Force, $ErrorAction) if ($script:aliasPresent -and $Path -like '*WindowsApps*') { return 'alias' }; if ($script:locationPresent -and $Path -like '*ffmpeg*') { return 'ffmpeg' } }
function Must-Fail([scriptblock]$Action, [string]$Label) {
    $failed = $false
    try { & $Action } catch { $failed = $true }
    if (-not $failed) { throw "Expected the gate to reject $Label" }
}
Assert-CleanMachine
$script:adapterUp = $true; Must-Fail { Assert-CleanMachine } 'active adapter'; $script:adapterUp = $false
$script:defaultRoute = $true; Must-Fail { Assert-CleanMachine } 'default route'; $script:defaultRoute = $false
$script:ipv6Route = $true; Must-Fail { Assert-CleanMachine } 'IPv6 default route'; $script:ipv6Route = $false
$script:registryPresent = $true; Must-Fail { Assert-CleanMachine } 'registered Python'; $script:registryPresent = $false
$script:aliasPresent = $true; Must-Fail { Assert-CleanMachine } 'execution alias'; $script:aliasPresent = $false
$script:commandPresent = $true; Must-Fail { Assert-CleanMachine } 'system command'; $script:commandPresent = $false
$script:packagePresent = $true; Must-Fail { Assert-CleanMachine } 'Store Python'; $script:packagePresent = $false
$script:locationPresent = $true; Must-Fail { Assert-CleanMachine } 'common FFmpeg installation'; $script:locationPresent = $false
$script:queryFails = $true; Must-Fail { Assert-CleanMachine } 'unavailable network query'
Write-Host 'Clean VM gate fault-injection checks passed.'
