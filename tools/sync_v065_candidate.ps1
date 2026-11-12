#requires -Version 5.1
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$repo = "https://github.com/Ricekillerr/ai-clothing-prompt-workbench.git"
$branch = "release/v0.6.5-stabilization"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$work = Join-Path $env:TEMP "PromptAtelier_V065_Sync_$stamp"
$stage = Join-Path $work "candidate"
$proxy = "http://127.0.0.1:7897"
$useProxy = $false

function Stop-Sync([string]$Message) {
    Write-Host "[停止] $Message" -ForegroundColor Red
    Write-Host "没有推送未通过的候选包。临时工作区：$work" -ForegroundColor Yellow
    throw $Message
}
function Invoke-Git([string[]]$GitArgs) {
    if ($useProxy) {
        & git -c "http.proxy=$proxy" -C $work @GitArgs
    } else {
        & git -C $work @GitArgs
    }
    if ($LASTEXITCODE -ne 0) { Stop-Sync "Git 命令失败：git $($GitArgs -join ' ')" }
}
function Invoke-Python([string[]]$PythonArgs) {
    Push-Location $work
    try {
        & python @PythonArgs
        if ($LASTEXITCODE -ne 0) { Stop-Sync "Python 检查失败：python $($PythonArgs -join ' ')" }
    } finally {
        Pop-Location
    }
}

try {
    Write-Host "===== Prompt Atelier V0.6.5 候选源码同步 =====" -ForegroundColor Cyan
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Stop-Sync "未找到 Git for Windows。" }
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Stop-Sync "未找到 python 命令。" }
    if ([string]::IsNullOrWhiteSpace((& git config --global --get user.name 2>$null)) -or
        [string]::IsNullOrWhiteSpace((& git config --global --get user.email 2>$null))) {
        Stop-Sync "Git 尚未配置提交者姓名/邮箱，请先在 GitHub Desktop 完成配置。"
    }
    python --version
    if ($LASTEXITCODE -ne 0) { Stop-Sync "Python 无法运行。" }

    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = "选择已存在的 Prompt Atelier V0.6.5 稳定性验收候选包"
    $dialog.Filter = "Prompt Atelier V0.6.5 候选包 (*.zip)|*.zip|所有 ZIP 文件 (*.zip)|*.zip"
    $dialog.Multiselect = $false
    if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { Stop-Sync "未选择候选 ZIP。" }
    $zipPath = $dialog.FileName
    Write-Host "候选包：$zipPath" -ForegroundColor Green

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        $entries = @($archive.Entries | Where-Object { -not [string]::IsNullOrWhiteSpace($_.Name) })
        if ($entries.Count -lt 9) { Stop-Sync "ZIP 文件内容不完整。" }
        foreach ($entry in $entries) {
            $normalized = $entry.FullName.Replace("\", "/")
            if (-not $normalized.StartsWith("PromptAtelier_V0.6.5/") -or $normalized.Contains("../")) {
                Stop-Sync "ZIP 包含非预期路径：$($entry.FullName)"
            }
        }
        $versionEntry = $archive.GetEntry("PromptAtelier_V0.6.5/installed_version.txt")
        if (-not $versionEntry) { Stop-Sync "候选包缺少 installed_version.txt。" }
        $reader = New-Object System.IO.StreamReader($versionEntry.Open(), [System.Text.Encoding]::UTF8)
        try { $candidateVersion = $reader.ReadToEnd().Trim() } finally { $reader.Dispose() }
        if ($candidateVersion -ne "0.6.5") { Stop-Sync "候选包版本应为 0.6.5，实际为 $candidateVersion。" }
    } finally {
        $archive.Dispose()
    }

    New-Item -ItemType Directory -Path $work -Force | Out-Null
    $proxyReady = $false
    try { $proxyReady = Test-NetConnection 127.0.0.1 -Port 7897 -InformationLevel Quiet -WarningAction SilentlyContinue } catch {}
    if ($proxyReady) { $useProxy = $true; Write-Host "检测到本地代理 127.0.0.1:7897。" -ForegroundColor DarkGray }

    Write-Host "[1/5] 克隆隔离发布分支……" -ForegroundColor Cyan
    if ($useProxy) {
        & git -c "http.proxy=$proxy" clone --branch $branch --single-branch $repo $work
    } else {
        & git clone --branch $branch --single-branch $repo $work
    }
    if ($LASTEXITCODE -ne 0) { Stop-Sync "无法克隆发布分支；请检查 GitHub 登录与网络。" }

    Write-Host "[2/5] 解压候选包并修正更新通道……" -ForegroundColor Cyan
    Expand-Archive -LiteralPath $zipPath -DestinationPath $stage -Force
    $candidateRoot = Join-Path $stage "PromptAtelier_V0.6.5"
    if (-not (Test-Path (Join-Path $candidateRoot "ai_prompt_workbench.py"))) { Stop-Sync "候选包目录结构不符合预期。" }

    $appPath = Join-Path $candidateRoot "ai_prompt_workbench.py"
    $appText = [System.IO.File]::ReadAllText($appPath, [System.Text.Encoding]::UTF8)
    $channelPattern = '(?m)^GITHUB_BRANCH\s*=\s*"main"\s*$'
    $channelMatches = [regex]::Matches($appText, $channelPattern)
    if ($channelMatches.Count -ne 1) { Stop-Sync "主程序更新通道标记不是唯一的预期值，拒绝自动修改。" }
    $appText = [regex]::Replace($appText, $channelPattern, 'GITHUB_BRANCH = "' + $branch + '"')
    [System.IO.File]::WriteAllText($appPath, $appText, (New-Object System.Text.UTF8Encoding($false)))

    Get-ChildItem -LiteralPath $candidateRoot -File -Recurse | ForEach-Object {
        $relative = $_.FullName.Substring($candidateRoot.Length).TrimStart([char[]]@("\", "/"))
        $target = Join-Path $work $relative
        $parent = Split-Path -Parent $target
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        Copy-Item -LiteralPath $_.FullName -Destination $target -Force
    }

    Write-Host "[3/5] 重算更新清单并执行候选包验收……" -ForegroundColor Cyan
    $hashCode = 'import json,hashlib,pathlib; p=pathlib.Path("."); m=json.loads((p/"update_manifest.json").read_text(encoding="utf-8")); m["sha256"]["ai_prompt_workbench.py"]=hashlib.sha256((p/"ai_prompt_workbench.py").read_bytes()).hexdigest(); (p/"update_manifest.json").write_text(json.dumps(m,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")'
    Invoke-Python @("-c", $hashCode)
    Invoke-Python @("-m", "compileall", "-q", ".")
    Invoke-Python @("-m", "unittest", "-v", "test_hardening")
    Invoke-Python @("quality_check.py")
    $hashCheck = 'import json,hashlib,pathlib,sys; p=pathlib.Path("."); m=json.loads((p/"update_manifest.json").read_text(encoding="utf-8")); bad=[n for n in m["files"] if not (p/n).is_file() or hashlib.sha256((p/n).read_bytes()).hexdigest().lower()!=m["sha256"][n].lower()]; print("SHA-256 核验：",len(m["files"]),"项；失败：",bad); sys.exit(bool(bad))'
    Invoke-Python @("-c", $hashCheck)
    $verify = 'import re,pathlib,json; s=pathlib.Path("ai_prompt_workbench.py").read_text(encoding="utf-8"); m=json.loads(pathlib.Path("update_manifest.json").read_text(encoding="utf-8")); assert re.search(r''^APP_VERSION\s*=\s*"0\.6\.5"'',s,re.M); assert re.search(r''^GITHUB_BRANCH\s*=\s*"release/v0\.6\.5-stabilization"'',s,re.M); assert m["version"]=="0.6.5"; print("版本、更新通道与清单一致。")'
    Invoke-Python @("-c", $verify)

    Write-Host "[4/5] 提交候选源码（不触碰用户数据库和参考图片）……" -ForegroundColor Cyan
    $candidateFiles = @(
        "ai_prompt_workbench.py","index.html","updater.py","启动AI服装提示词工作台.bat",
        "storage.py","collector.py","case_matcher.py","ai_analyzer.py",
        "installed_version.txt","update_manifest.json","windows_acceptance.py",
        "quality_check.py","工程质量检查.py","test_hardening.py",
        "CHANGELOG_V0.6.5.txt","README_审查候选包.txt",
        "使用说明_AI服装提示词资产工作台_V0.6.2.txt","质量检查工具说明.txt",
        "run_quality_check.bat","运行工程质量检查.bat","运行Windows一次性验收.bat"
    )
    Invoke-Git (@("add","--") + $candidateFiles)
    Invoke-Git @("diff","--cached","--check")
    Invoke-Git @("commit","-m","Sync reviewed Prompt Atelier V0.6.5 candidate [payload-synced]")
    Write-Host "[5/5] 推送到隔离验收分支……" -ForegroundColor Cyan
    Invoke-Git @("push","origin",$branch)

    Write-Host "[成功] V0.6.5 候选源码已同步到 GitHub。" -ForegroundColor Green
    Write-Host "分支：$branch" -ForegroundColor Green
    Write-Host "提交：$(& git -C $work rev-parse HEAD)" -ForegroundColor Green
    Write-Host "接下来 GitHub Actions 将自动执行发布门禁。" -ForegroundColor Cyan
    Remove-Item -LiteralPath $work -Recurse -Force
}
catch {
    Write-Host "[失败] $($_.Exception.Message)" -ForegroundColor Red
    if (Test-Path $work) { Write-Host "临时工作区已保留：$work" -ForegroundColor Yellow }
    exit 1
}
