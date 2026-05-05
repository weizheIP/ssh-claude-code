#requires -Version 5.1
<#
.SYNOPSIS
  安装 WinFsp + sshfs-win (Windows)。
  用于 ssh-claude-code 的文件树挂载功能。挂载是可选的, 不影响 remote_* 工具。

.DESCRIPTION
  优先通过 Chocolatey/Winget 安装。若两者均不可用,则从官方 GitHub Releases
  下载 MSI 静默安装。需要管理员权限。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install-sshfs-win.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

function Info($msg)  { Write-Host "[i] $msg" -ForegroundColor Green }
function Warn($msg)  { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Fail($msg)  { Write-Host "[x] $msg" -ForegroundColor Red; exit 1 }

# ── 管理员检查 ────────────────────────────────────────────────
$identity   = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal  = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin    = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Fail "请以管理员身份运行 PowerShell 后重新执行。"
}

# ── 已安装检测 ────────────────────────────────────────────────
function Test-WinFsp {
    Test-Path "C:\Program Files\WinFsp"  -PathType Container -ErrorAction SilentlyContinue
}
function Test-SshfsWin {
    (Test-Path "C:\Program Files\SSHFS-Win\bin\sshfs.exe") -or
    (Test-Path "C:\Program Files (x86)\SSHFS-Win\bin\sshfs.exe")
}

if ((Test-WinFsp) -and (Test-SshfsWin)) {
    Info "WinFsp 与 sshfs-win 都已安装"
    exit 0
}

# ── 包管理器路径 ──────────────────────────────────────────────
$haveChoco  = (Get-Command choco  -ErrorAction SilentlyContinue) -ne $null
$haveWinget = (Get-Command winget -ErrorAction SilentlyContinue) -ne $null

if ($haveChoco) {
    Info "使用 Chocolatey 安装"
    if (-not (Test-WinFsp))   { choco install winfsp   -y --no-progress }
    if (-not (Test-SshfsWin)) { choco install sshfs-win -y --no-progress }
    Info "完成"
    exit 0
}

if ($haveWinget) {
    Info "使用 winget 安装"
    if (-not (Test-WinFsp))   { winget install --id WinFsp.WinFsp     -e --silent --accept-source-agreements --accept-package-agreements }
    if (-not (Test-SshfsWin)) { winget install --id SSHFS-Win.SSHFS-Win -e --silent --accept-source-agreements --accept-package-agreements }
    Info "完成"
    exit 0
}

# ── 直接下载 MSI ──────────────────────────────────────────────
Warn "未检测到 Chocolatey/winget — 直接下载官方安装包"

$tmp = Join-Path $env:TEMP "ssh-claude-code-deps"
New-Item -Path $tmp -ItemType Directory -Force | Out-Null

function Install-Msi($url, $name) {
    $msi = Join-Path $tmp "$name.msi"
    Info "下载 $name: $url"
    Invoke-WebRequest -Uri $url -OutFile $msi -UseBasicParsing
    Info "静默安装 $name"
    $p = Start-Process msiexec.exe -ArgumentList "/i `"$msi`" /qn /norestart" -Wait -PassThru
    if ($p.ExitCode -ne 0) { Fail "$name 安装失败 (exit=$($p.ExitCode))" }
}

if (-not (Test-WinFsp)) {
    Install-Msi "https://github.com/winfsp/winfsp/releases/latest/download/winfsp.msi" "winfsp"
}
if (-not (Test-SshfsWin)) {
    Install-Msi "https://github.com/winfsp/sshfs-win/releases/latest/download/sshfs-win-x64.msi" "sshfs-win"
}

Info "完成。请重新启动 Claude Code 以使新挂载能力生效。"
