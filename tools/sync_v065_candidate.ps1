#requires -Version 5.1
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$repo = "https://github.com/Ricekillerr/ai-clothing-prompt-workbench.git"
$branch = "release/v0.6.5-stabilization"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$work = Join-Path $env:TEMP "PromptAtelier_V065_FromV064_$stamp"
$proxy = "http://127.0.0.1:7897"
$useProxy = $false

function Stop-Sync([string]$Message) {
    Write-Host "[停止] $Message" -ForegroundColor Red
    Write-Host "没有推送候选源码。临时工作区：$work" -ForegroundColor Yellow
    throw $Message
}
function Invoke-Git([string[]]$GitArgs) {
    if ($useProxy) { & git -c "http.proxy=$proxy" -C $work @GitArgs }
    else { & git -C $work @GitArgs }
    if ($LASTEXITCODE -ne 0) { Stop-Sync "Git 命令失败：git $($GitArgs -join ' ')" }
}
function Invoke-Python([string[]]$PythonArgs) {
    Push-Location $work
    try {
        & python @PythonArgs
        if ($LASTEXITCODE -ne 0) { Stop-Sync "Python 检查失败：python $($PythonArgs -join ' ')" }
    } finally { Pop-Location }
}

try {
    Write-Host "===== Prompt Atelier V0.6.4 -> V0.6.5 源码同步 =====" -ForegroundColor Cyan
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Stop-Sync "未找到 Git for Windows。" }
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Stop-Sync "未找到 python 命令。" }
    if ([string]::IsNullOrWhiteSpace((& git config --global --get user.name 2>$null)) -or
        [string]::IsNullOrWhiteSpace((& git config --global --get user.email 2>$null))) {
        Stop-Sync "Git 提交者身份尚未配置。"
    }
    python --version
    if ($LASTEXITCODE -ne 0) { Stop-Sync "Python 无法运行。" }

    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = "选择现有 Prompt Atelier V0.6.4 程序文件夹（不是 ZIP，也不是桌面快捷方式）"
    $dialog.ShowNewFolderButton = $false
    if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { Stop-Sync "未选择 V0.6.4 程序文件夹。" }
    $source = $dialog.SelectedPath
    $appSource = Join-Path $source "ai_prompt_workbench.py"
    $manifestSource = Join-Path $source "update_manifest.json"
    $versionSource = Join-Path $source "installed_version.txt"
    foreach ($p in @($appSource,$manifestSource,$versionSource,(Join-Path $source "index.html"),(Join-Path $source "updater.py"))) {
        if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { Stop-Sync "所选目录缺少必要文件：$(Split-Path -Leaf $p)" }
    }
    $localVersion = (Get-Content -LiteralPath $versionSource -Raw -Encoding UTF8).Trim()
    if ($localVersion -ne "0.6.4") { Stop-Sync "所选目录版本应为 0.6.4，实际为 $localVersion；为防止覆盖错误版本已停止。" }
    Write-Host "已确认本地基线：V0.6.4；$source" -ForegroundColor Green

    New-Item -ItemType Directory -Path $work -Force | Out-Null
    try { $proxyReady = Test-NetConnection 127.0.0.1 -Port 7897 -InformationLevel Quiet -WarningAction SilentlyContinue } catch { $proxyReady = $false }
    if ($proxyReady) { $useProxy = $true; Write-Host "检测到本地代理。" -ForegroundColor DarkGray }

    Write-Host "[1/5] 克隆隔离验收分支……" -ForegroundColor Cyan
    if ($useProxy) { & git -c "http.proxy=$proxy" clone --branch $branch --single-branch $repo $work }
    else { & git clone --branch $branch --single-branch $repo $work }
    if ($LASTEXITCODE -ne 0) { Stop-Sync "无法克隆 GitHub 验收分支。" }

    Write-Host "[2/5] 仅同步程序源码与发布文件；跳过数据库、图片、密钥和用户配置……" -ForegroundColor Cyan
    $manifest = Get-Content -LiteralPath $manifestSource -Raw -Encoding UTF8 | ConvertFrom-Json
    $files = @($manifest.files | Where-Object { $_ })
    foreach ($required in @("ai_prompt_workbench.py","index.html","updater.py","启动AI服装提示词工作台.bat")) {
        if ($files -notcontains $required) { $files += $required }
    }
    foreach ($name in $files) {
        if ([IO.Path]::IsPathRooted($name) -or $name.Split("/") -contains ".." -or $name.Split("\") -contains "..") {
            Stop-Sync "更新清单含有不安全路径：$name"
        }
        $from = Join-Path $source $name
        if (-not (Test-Path -LiteralPath $from -PathType Leaf)) { Stop-Sync "V0.6.4 缺少清单要求的发布文件：$name" }
        $to = Join-Path $work $name
        $parent = Split-Path -Parent $to
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        Copy-Item -LiteralPath $from -Destination $to -Force
    }
    Copy-Item -LiteralPath $manifestSource -Destination (Join-Path $work "update_manifest.json") -Force

    $appPath = Join-Path $work "ai_prompt_workbench.py"
    $appText = [IO.File]::ReadAllText($appPath,[Text.Encoding]::UTF8)
    if ($appText -notmatch '(?m)^APP_VERSION\s*=\s*["'']0\.6\.4["'']\s*$') { Stop-Sync "主程序 APP_VERSION 不是明确的 0.6.4，拒绝自动升版。" }
    if ($appText -notmatch '(?m)^GITHUB_BRANCH\s*=\s*["''][^"'']+["'']\s*$') { Stop-Sync "主程序更新分支标记缺失或格式异常。" }
    $appText = [regex]::Replace($appText,'(?m)^APP_VERSION\s*=\s*["'']0\.6\.4["'']\s*$','APP_VERSION = "0.6.5"')
    $appText = [regex]::Replace($appText,'(?m)^GITHUB_BRANCH\s*=\s*["''][^"'']+["'']\s*$','GITHUB_BRANCH = "'+$branch+'"')
    [IO.File]::WriteAllText($appPath,$appText,([Text.UTF8Encoding]::new($false)))
    Set-Content -LiteralPath (Join-Path $work "installed_version.txt") -Value "0.6.5" -Encoding UTF8

    Write-Host "[3/5] 重建版本清单与 SHA-256……" -ForegroundColor Cyan
    $hashCode = @'
import hashlib, json
from pathlib import Path
root=Path(".")
p=root/"update_manifest.json"
m=json.loads(p.read_text(encoding="utf-8"))
files=list(dict.fromkeys(m.get("files",[])+["ai_prompt_workbench.py","index.html","updater.py","启动AI服装提示词工作台.bat"]))
for name in files:
    f=root/name
    if not f.is_file(): raise SystemExit(f"发布文件缺失：{name}")
m["version"]="0.6.5"
m["files"]=files
m["sha256"]={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files}
p.write_text(json.dumps(m,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(f"已生成 {len(files)} 个发布文件的 SHA-256。")
'@
    Invoke-Python @("-c",$hashCode)

    Write-Host "[4/5] 执行源码、回归和哈希检查……" -ForegroundColor Cyan
    Invoke-Python @("-m","compileall","-q",".")
    $tests = @'
import unittest
suite=unittest.defaultTestLoader.discover("tests",pattern="test_*.py")
n=suite.countTestCases()
if not n: raise SystemExit("未发现回归测试")
print(f"发现 {n} 项回归测试。")
r=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if r.wasSuccessful() else 1)
'@
    Invoke-Python @("-c",$tests)
    $verify = @'
import hashlib,json,re
from pathlib import Path
root=Path(".")
app=(root/"ai_prompt_workbench.py").read_text(encoding="utf-8")
m=json.loads((root/"update_manifest.json").read_text(encoding="utf-8"))
assert re.search(r'^APP_VERSION\s*=\s*"0\.6\.5"',app,re.M)
assert re.search(r'^GITHUB_BRANCH\s*=\s*"release/v0\.6\.5-stabilization"',app,re.M)
assert m["version"]=="0.6.5"
for name in m["files"]:
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==m["sha256"][name],name
print(f"版本、更新通道及 {len(m['files'])} 项哈希全部一致。")
'@
    Invoke-Python @("-c",$verify)

    Write-Host "[5/5] 提交并推送隔离验收分支……" -ForegroundColor Cyan
    Invoke-Git @("add","--all")
    Invoke-Git @("diff","--cached","--check")
    Invoke-Git @("commit","-m","Sync existing V0.6.4 source as V0.6.5 release candidate")
    Invoke-Git @("push","origin",$branch)
    Write-Host "[成功] V0.6.4 源码已同步为 V0.6.5 候选版本；GitHub Actions 将自动验收。" -ForegroundColor Green
    Write-Host "本地原始目录未被修改；用户数据库、图片和配置未被复制。" -ForegroundColor Green
    Remove-Item -LiteralPath $work -Recurse -Force
}
catch {
    Write-Host "[失败] $($_.Exception.Message)" -ForegroundColor Red
    if (Test-Path $work) { Write-Host "临时工作区保留在：$work" -ForegroundColor Yellow }
    exit 1
}
