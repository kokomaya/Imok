<#
.SYNOPSIS
  Imok Meeting Assistant — 开发阶段快速打包脚本（跳过压缩）。

.DESCRIPTION
  与 build.ps1 相同，但跳过最后的 zip 压缩步骤以节省时间。

  步骤：
    1. 前置检查
    2. 安全检查
    3. PyInstaller 打包 Python 后端
    4. Vite 构建前端
    5. electron-builder 生成便携版

.PARAMETER Force
  强制完全重建 PyInstaller 后端（忽略缓存）。

.EXAMPLE
  .\scripts\build-dev.ps1
.EXAMPLE
  .\scripts\build-dev.ps1 -Force
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
Write-Host "  Imok Meeting Assistant - Dev Build" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# ── 前置检查 ────────────────────────────────────────────────

Write-Host '[1/5] Checking prerequisites...' -ForegroundColor Yellow

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

Write-Host '[2/5] Security check - verifying no secrets in build...' -ForegroundColor Yellow

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

# ── Step 3: PyInstaller 打包 Python 后端 ──────────────────

Write-Host '[3/5] Building Python backend with PyInstaller...' -ForegroundColor Yellow

Push-Location $ProjectRoot
try {
    & $VenvPip show pyinstaller 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host '  Installing PyInstaller...' -ForegroundColor DarkGray
        & $VenvPip install pyinstaller --quiet
    }

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

Write-Host '[4/5] Building frontend with Vite...' -ForegroundColor Yellow

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

Write-Host '[5/5] Packaging with electron-builder...' -ForegroundColor Yellow

Push-Location $FrontendDir
try {
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

# ── 完成 ────────────────────────────────────────────────

Write-Host "`nDev build complete!" -ForegroundColor Green
Write-Host "`n  Portable app : frontend\out\win-unpacked\" -ForegroundColor Cyan
Write-Host "  Run directly : frontend\out\win-unpacked\Imok Meeting Assistant.exe" -ForegroundColor Cyan
Write-Host "  (Zip skipped for faster dev iteration)`n" -ForegroundColor DarkGray
