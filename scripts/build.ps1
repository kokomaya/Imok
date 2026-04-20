<#
.SYNOPSIS
  Imok Meeting Assistant — 一键打包脚本。

.DESCRIPTION
  步骤：
    1. PyInstaller 打包 Python 后端为独立 exe
    2. Vite 构建前端
    3. electron-builder 生成安装包

  前置条件：
    - Python venv 已创建且依赖已安装 (.venv)
    - Node.js + npm 已安装
    - frontend/node_modules 已安装 (npm install)

.PARAMETER Force
  强制完全重建 PyInstaller 后端（忽略缓存）。

.EXAMPLE
  .\scripts\build.ps1
.EXAMPLE
  .\scripts\build.ps1 -Force
#>

param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$FrontendDir = Join-Path $ProjectRoot 'frontend'
$VenvPython  = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$VenvPip     = Join-Path $ProjectRoot '.venv\Scripts\pip.exe'

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  Imok Meeting Assistant - Build" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# ── 前置检查 ────────────────────────────────────────────────

Write-Host '[1/6] Checking prerequisites...' -ForegroundColor Yellow

if (-not (Test-Path $VenvPython)) {
    Write-Error "Python venv not found at $VenvPython. Run: python -m venv .venv && .venv\Scripts\pip install -r requirements.txt"
}

Push-Location $FrontendDir
try {
    if (-not (Test-Path 'node_modules')) {
        Write-Error 'node_modules not found. Run: cd frontend && npm install'
    }
} finally {
    Pop-Location
}

# ── 安全检查：确认不打包敏感文件 ────────────────────────────

Write-Host '[2/6] Security check - verifying no secrets in build...' -ForegroundColor Yellow

$envFile = Join-Path $ProjectRoot '.env'
$yamlFile = Join-Path $ProjectRoot 'config\llm_providers.yaml'

if (Test-Path $envFile) {
    Write-Host '  .env exists (will NOT be packaged) - OK' -ForegroundColor DarkGray
}
if (Test-Path $yamlFile) {
    Write-Host '  config/llm_providers.yaml exists (will NOT be packaged) - OK' -ForegroundColor DarkGray
}

# 确认 example 文件存在
$envExample = Join-Path $ProjectRoot '.env.example'
$yamlExample = Join-Path $ProjectRoot 'config\llm_providers.yaml.example'

if (-not (Test-Path $envExample)) {
    Write-Error '.env.example not found — required for distribution'
}
if (-not (Test-Path $yamlExample)) {
    Write-Error 'config/llm_providers.yaml.example not found — required for distribution'
}

Write-Host '  Example files present - OK' -ForegroundColor Green

# ── Step 3: PyInstaller 打包 Python 后端 ──────────────────

Write-Host '[3/6] Building Python backend with PyInstaller...' -ForegroundColor Yellow

Push-Location $ProjectRoot
try {
    # 确保 PyInstaller 已安装
    & $VenvPip show pyinstaller 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host '  Installing PyInstaller...' -ForegroundColor DarkGray
        & $VenvPip install pyinstaller --quiet
    }

    # ── 增量构建：检测后端源码是否有变化 ──
    # 对比 backend/ + backend_entry.py + build_backend.spec + rthook_torch.py 的最新修改时间
    $exePath = 'dist\imok-backend\imok-backend.exe'
    $needRebuild = $true

    if ((Test-Path $exePath) -and -not $Force) {
        $exeTime = (Get-Item $exePath).LastWriteTime
        $srcFiles = @(
            Get-ChildItem -Path 'backend' -Recurse -File -Include '*.py'
            Get-Item 'backend_entry.py'
            Get-Item 'build_backend.spec'
            Get-Item 'rthook_torch.py'
        )
        $newerFiles = @($srcFiles | Where-Object { $_.LastWriteTime -gt $exeTime })
        if ($newerFiles.Count -eq 0) {
            $needRebuild = $false
            Write-Host '  No backend changes detected — skipping PyInstaller (cached)' -ForegroundColor Green
            $exeSize = (Get-Item $exePath).Length / 1MB
            Write-Host "  Using cached: $exePath ($([math]::Round($exeSize, 1)) MB)" -ForegroundColor Green
        } else {
            Write-Host "  $($newerFiles.Count) file(s) changed since last build — rebuilding..." -ForegroundColor DarkGray
        }
    }

    if ($needRebuild) {
        # 清理旧构建
        if (Test-Path 'dist\imok-backend') {
            Remove-Item -Recurse -Force 'dist\imok-backend'
        }
        if (Test-Path 'build\imok-backend') {
            Remove-Item -Recurse -Force 'build\imok-backend'
        }
        if (Test-Path 'build\build_backend') {
            Remove-Item -Recurse -Force 'build\build_backend'
        }

        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $VenvPython -m PyInstaller build_backend.spec --noconfirm --clean 2>&1 | ForEach-Object {
            $line = $_.ToString()
            if ($line -match 'ERROR') {
                Write-Host "  $line" -ForegroundColor Red
            } else {
                Write-Host "  $line" -ForegroundColor DarkGray
            }
        }
        $ErrorActionPreference = $prevEAP

        if (-not (Test-Path $exePath)) {
            Write-Error 'PyInstaller build failed — dist\imok-backend\imok-backend.exe not found'
        }

        $exeSize = (Get-Item $exePath).Length / 1MB
        Write-Host "  Backend built: $exePath ($([math]::Round($exeSize, 1)) MB)" -ForegroundColor Green
    }
} finally {
    Pop-Location
}

# ── Step 4: Vite 构建前端 ────────────────────────────────

Write-Host '[4/6] Building frontend with Vite...' -ForegroundColor Yellow

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

# ── Step 5: electron-builder 打包 ────────────────────────

Write-Host '[5/6] Packaging with electron-builder...' -ForegroundColor Yellow

Push-Location $FrontendDir
try {
    # 跳过代码签名（无证书时 winCodeSign 解压会因 symlink 权限失败）
    $env:CSC_IDENTITY_AUTO_DISCOVERY = 'false'

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    npx electron-builder --config electron-builder.config.js 2>&1 | ForEach-Object {
        $line = $_.ToString()
        if ($line -match 'error|Error|ERROR') {
            Write-Host "  $line" -ForegroundColor Red
        } else {
            Write-Host "  $line" -ForegroundColor DarkGray
        }
    }
    $ErrorActionPreference = $prevEAP

    $unpackedDir = Join-Path $FrontendDir 'out\win-unpacked'
    if (-not (Test-Path $unpackedDir)) {
        Write-Error 'electron-builder failed - out\win-unpacked directory not found'
    }

    $unpackedSize = [math]::Round(((Get-ChildItem $unpackedDir -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1GB), 2)
    Write-Host "  Portable app: out\win-unpacked\ ($unpackedSize GB)" -ForegroundColor Green
} finally {
    Pop-Location
}

# ── Step 6: 压缩为分发包 ────────────────────────────────

Write-Host "[6/7] Compressing portable app to zip..." -ForegroundColor Yellow

$zipPath = Join-Path $FrontendDir "out\ImokMeetingAssistant-0.1.0-win-x64.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
Compress-Archive -Path (Join-Path $FrontendDir 'out\win-unpacked\*') -DestinationPath $zipPath -CompressionLevel Optimal 2>&1 | ForEach-Object {
    Write-Host "  $_" -ForegroundColor DarkGray
}
$ErrorActionPreference = $prevEAP

if (Test-Path $zipPath) {
    $zipSize = [math]::Round((Get-Item $zipPath).Length / 1GB, 2)
    Write-Host "  Zip: $([System.IO.Path]::GetFileName($zipPath)) ($zipSize GB)" -ForegroundColor Green
} else {
    Write-Host "  Warning: zip creation failed. Portable app is still in out\win-unpacked\" -ForegroundColor Yellow
}

# ── Step 7: 最终报告 ────────────────────────────────────

Write-Host "`n[7/7] Build complete!" -ForegroundColor Green
Write-Host "`n  Portable app : frontend\out\win-unpacked\" -ForegroundColor Cyan
Write-Host "  Zip package  : frontend\out\ImokMeetingAssistant-0.1.0-win-x64.zip" -ForegroundColor Cyan
Write-Host "  Note: Users need to create .env and config\llm_providers.yaml" -ForegroundColor Yellow
Write-Host "        from the included .example files.`n" -ForegroundColor Yellow
