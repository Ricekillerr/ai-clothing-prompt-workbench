Set-StrictMode -Version Latest

function Get-FileSha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hash.ComputeHash($stream)).Replace('-', '') }
    finally { $stream.Dispose(); $hash.Dispose() }
}

function Assert-NetworkDisconnected {
    # Failure to enumerate is a failure of the gate, not evidence of isolation.
    $adapters = @(Get-NetAdapter -IncludeHidden -ErrorAction Stop)
    $up = @($adapters | Where-Object { $_.Status -eq 'Up' -and $_.InterfaceDescription -notmatch '(?i)loopback' })
    $routes = @(Get-NetRoute -PolicyStore ActiveStore -ErrorAction Stop | Where-Object {
        $_.DestinationPrefix -in @('0.0.0.0/0', '::/0')
    })
    if ($up.Count -or $routes.Count) { throw 'Offline gate failed: active network adapter or default route.' }
}

function Assert-CleanMachine {
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'A Windows x64 VM is required.' }
    Assert-NetworkDisconnected
    $system = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop
    $identity = "$($system.Manufacturer) $($system.Model)"
    if (-not $system.HypervisorPresent -and $identity -notmatch '(?i)virtual|vmware|qemu|kvm|xen|parallels') {
        throw 'VM identity could not be established; physical hosts do not pass this gate.'
    }
    foreach ($name in @('python.exe', 'python3.exe', 'py.exe', 'ffmpeg.exe')) {
        if (Get-Command $name -ErrorAction SilentlyContinue) { throw "Unexpected system command: $name" }
    }
    foreach ($key in @('HKCU:\Software\Python', 'HKLM:\Software\Python', 'HKLM:\Software\WOW6432Node\Python',
                       'HKCU:\Software\Python\PyLauncher', 'HKLM:\Software\Python\PyLauncher')) {
        if (Test-Path $key -ErrorAction Stop) { throw 'System Python registry entry found.' }
    }
    foreach ($uninstall in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
                             'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
                             'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall')) {
        if (Test-Path $uninstall -ErrorAction Stop) {
            $programs = @(Get-ItemProperty -Path (Join-Path $uninstall '*') -ErrorAction Stop)
            foreach ($program in $programs) {
                $display = $program.PSObject.Properties['DisplayName']
                if ($display -and $display.Value -match '(?i)^(Python( Launcher)?\b|FFmpeg\b|Anaconda\b|Miniconda\b)') {
                    throw 'A system or user Python, launcher or FFmpeg installation was found.'
                }
            }
        }
    }
    $appx = @(Get-AppxPackage -AllUsers -ErrorAction Stop | Where-Object { $_.Name -match '(?i)^PythonSoftwareFoundation\.Python' })
    if ($appx.Count) { throw 'A Python Microsoft Store package is installed.' }
    $patterns = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python*'), (Join-Path $env:APPDATA 'Python*'),
        (Join-Path $env:ProgramFiles 'Python*'), (Join-Path ${env:ProgramFiles(x86)} 'Python*'),
        (Join-Path $env:SystemDrive 'Python*'), (Join-Path $env:SystemDrive 'ffmpeg*'),
        (Join-Path $env:ProgramFiles 'ffmpeg*'), (Join-Path ${env:ProgramFiles(x86)} 'ffmpeg*'),
        (Join-Path $env:LOCALAPPDATA 'Programs\ffmpeg*'), (Join-Path $env:SystemDrive 'tools\ffmpeg*'),
        (Join-Path $env:SystemDrive 'tools\Python*'), (Join-Path $env:ProgramData 'chocolatey\bin\ffmpeg*'),
        (Join-Path $env:USERPROFILE 'scoop\apps\python*'), (Join-Path $env:USERPROFILE 'scoop\apps\ffmpeg*'),
        (Join-Path $env:USERPROFILE 'Anaconda*'), (Join-Path $env:USERPROFILE 'Miniconda*'),
        (Join-Path $env:ProgramData 'chocolatey\lib\python*'), (Join-Path $env:ProgramData 'chocolatey\lib\ffmpeg*'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages\Python*'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages\Gyan.FFmpeg*'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\python*.exe'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\py.exe'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\ffmpeg.exe'),
        (Join-Path $env:SystemRoot 'py.exe'), (Join-Path $env:SystemRoot 'System32\python*.exe'),
        (Join-Path $env:SystemRoot 'System32\ffmpeg.exe')
    )
    foreach ($pattern in $patterns) {
        try { $found = @(Get-Item -Path $pattern -Force -ErrorAction Stop) }
        catch [System.Management.Automation.ItemNotFoundException] { $found = @() }
        if ($found.Count) {
            throw 'A common system or user dependency location or execution alias exists.'
        }
    }
    foreach ($name in @('AI_Prompt_Asset_Workbench', 'AI_Prompt_Asset_Workbench_v2')) {
        if (Test-Path -LiteralPath (Join-Path $env:USERPROFILE $name) -ErrorAction Stop) {
            throw 'User profile contains workbench data; use a fresh disposable profile.'
        }
    }
}

function Assert-CandidateMatchesArchive {
    param([string]$CandidateDirectory, [string]$CandidateZip, [string]$ExpectedSha256)
    $actual = Get-FileSha256 $CandidateZip
    if ($actual -ine $ExpectedSha256) { throw 'Candidate ZIP SHA-256 mismatch.' }
    $root = [IO.Path]::GetFullPath($CandidateDirectory).TrimEnd('\') + '\'
    $rootItem = Get-Item -LiteralPath $CandidateDirectory -Force -ErrorAction Stop
    for ($item = $rootItem; $null -ne $item; $item = $item.Parent) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Candidate directory or ancestor is a link.' }
    }
    $disk = @(Get-ChildItem -LiteralPath $CandidateDirectory -Force -Recurse -ErrorAction Stop)
    if (@($disk | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) {
        throw 'Candidate contains a link or junction.'
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($CandidateZip)
    $seen = @{}
    try {
        foreach ($entry in $zip.Entries) {
            if ($entry.FullName.EndsWith('/') -or $entry.FullName.EndsWith('\')) { continue }
            $name = $entry.FullName.Replace('/', '\')
            if ($name.Contains(':') -or $name.StartsWith('\') -or @($name.Split('\') | Where-Object { $_ -in @('', '.', '..') }).Count) {
                throw 'Candidate ZIP contains an unsafe path.'
            }
            $path = [IO.Path]::GetFullPath((Join-Path $CandidateDirectory $name))
            if (-not $path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) -or $seen.ContainsKey($name)) {
                throw 'Candidate ZIP contains an unsafe or duplicate path.'
            }
            $seen[$name] = $true
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Candidate extracted file is missing.' }
            $hasher = [Security.Cryptography.SHA256]::Create()
            $stream = $entry.Open()
            try { $expected = [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '') }
            finally { $stream.Dispose(); $hasher.Dispose() }
            if ((Get-FileSha256 $path) -ne $expected) { throw 'Candidate extracted file hash mismatch.' }
        }
        if ($seen.Count -ne @($disk | Where-Object { -not $_.PSIsContainer }).Count) { throw 'Candidate has extra files.' }
    } finally { $zip.Dispose() }
}
