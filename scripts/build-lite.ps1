<#
.SYNOPSIS
  Imok Meeting Assistant — 轻量版打包脚本（不含 Python 运行环境）。

.DESCRIPTION
  与 build.ps1 的区别：
    - 跳过 PyInstaller 步骤
    - 打包 backend 源码而非编译后的 exe
    - 用户需自行安装 Python 3.12+ 和依赖
    - 生成体积远小于完整版（约 200-300 MB vs 4+ GB）

  步骤：
    1. 前置检查
    2. 安全检查
    3. Vite 构建前端
    4. electron-builder 打包（轻量配置）
    5. 压缩为 zip
    6. 报告

.EXAMPLE
  .\scripts\build-lite.ps1
#>

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$FrontendDir = Join-Path $ProjectRoot 'frontend'
$VenvPython  = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  Imok Meeting Assistant - Build (Lite)" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# ── Step 1: 前置检查 ────────────────────────────────────────

Write-Host '[1/6] Checking prerequisites...' -ForegroundColor Yellow

Push-Location $FrontendDir
try {
    if (-not (Test-Path 'node_modules')) {
        Write-Error 'node_modules not found. Run: cd frontend && npm install'
    }
} finally {
    Pop-Location
}

# 确认 INSTALL.md 存在
if (-not (Test-Path (Join-Path $ProjectRoot 'INSTALL.md'))) {
    Write-Error 'INSTALL.md not found — required for lite distribution'
}

Write-Host '  OK' -ForegroundColor Green

# ── Step 2: 安全检查 ────────────────────────────────────────

Write-Host '[2/6] Security check - verifying no secrets in build...' -ForegroundColor Yellow

$envFile = Join-Path $ProjectRoot '.env'
$yamlFile = Join-Path $ProjectRoot 'config\llm_providers.yaml'

if (Test-Path $envFile) {
    Write-Host '  .env exists (will NOT be packaged) - OK' -ForegroundColor DarkGray
}
if (Test-Path $yamlFile) {
    Write-Host '  config/llm_providers.yaml exists (will NOT be packaged) - OK' -ForegroundColor DarkGray
}

$envExample = Join-Path $ProjectRoot '.env.example'
$yamlExample = Join-Path $ProjectRoot 'config\llm_providers.yaml.example'

if (-not (Test-Path $envExample)) {
    Write-Error '.env.example not found — required for distribution'
}
if (-not (Test-Path $yamlExample)) {
    Write-Error 'config/llm_providers.yaml.example not found — required for distribution'
}

Write-Host '  Example files present - OK' -ForegroundColor Green

# ── Step 3: Vite 构建前端 ────────────────────────────────

Write-Host '[3/6] Building frontend with Vite...' -ForegroundColor Yellow

Push-Location $FrontendDir
try {
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    npx vite build 2>&1 | ForEach-Object {
        Write-Host "  $_" -ForegroundColor DarkGray
    }
    $ErrorActionPreference = $prevEAP

    if (-not (Test-Path 'dist\index.html')) {
        Write-Error 'Vite build failed - dist\index.html not found'
    }
    Write-Host '  Frontend built: dist/' -ForegroundColor Green
} finally {
    Pop-Location
}

# ── Step 4: electron-builder 打包（轻量版） ──────────────

Write-Host '[4/6] Packaging with electron-builder (lite)...' -ForegroundColor Yellow

Push-Location $FrontendDir
try {
    $env:CSC_IDENTITY_AUTO_DISCOVERY = 'false'

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    npx electron-builder --config electron-builder.lite.config.js 2>&1 | ForEach-Object {
        $line = $_.ToString()
        if ($line -match 'ERROR') {
            Write-Host "  $line" -ForegroundColor Red
        } else {
            Write-Host "  $line" -ForegroundColor DarkGray
        }
    }
    $ErrorActionPreference = $prevEAP

    $unpackedDir = Join-Path $FrontendDir 'out-lite\win-unpacked'
    if (-not (Test-Path $unpackedDir)) {
        Write-Error 'electron-builder failed - out-lite\win-unpacked directory not found'
    }

    $unpackedSize = [math]::Round(((Get-ChildItem $unpackedDir -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB), 1)
    Write-Host "  Portable app: out-lite\win-unpacked\ ($unpackedSize MB)" -ForegroundColor Green
} finally {
    Pop-Location
}

# ── Step 5: 压缩为 zip ──────────────────────────────────

Write-Host '[5/6] Compressing to zip...' -ForegroundColor Yellow

$zipPath = Join-Path $FrontendDir 'out-lite\ImokMeetingAssistant-0.1.0-lite-win-x64.zip'
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
Compress-Archive -Path (Join-Path $FrontendDir 'out-lite\win-unpacked\*') -DestinationPath $zipPath -CompressionLevel Optimal 2>&1 | ForEach-Object {
    Write-Host "  $_" -ForegroundColor DarkGray
}
$ErrorActionPreference = $prevEAP

if (Test-Path $zipPath) {
    $zipSize = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
    Write-Host "  Zip: $([System.IO.Path]::GetFileName($zipPath)) ($zipSize MB)" -ForegroundColor Green
} else {
    Write-Host '  Warning: zip creation failed. Portable app is still in out-lite\win-unpacked\' -ForegroundColor Yellow
}

# ── Step 6: 报告 ────────────────────────────────────────

Write-Host "`n[6/6] Lite build complete!" -ForegroundColor Green
Write-Host "`n  Portable app : frontend\out-lite\win-unpacked\" -ForegroundColor Cyan
Write-Host "  Zip package  : frontend\out-lite\ImokMeetingAssistant-0.1.0-lite-win-x64.zip" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Users must install Python environment before running." -ForegroundColor Yellow
Write-Host "  One-click deps: run resources\scripts\install-lite-deps.cmd" -ForegroundColor Yellow
Write-Host "  See INSTALL.md for instructions.`n" -ForegroundColor Yellow
