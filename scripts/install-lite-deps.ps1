<#
.SYNOPSIS
  One-click dependency installer for Imok Lite package.

.DESCRIPTION
  Run this script from the packaged resources folder to:
    1. Ensure Python is available
    2. Create .venv (if missing)
    3. Upgrade pip tooling
    4. Install requirements.txt

  Optional: install CUDA PyTorch wheel with -Cuda cu121 (or cu124, etc.)

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\install-lite-deps.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\install-lite-deps.ps1 -Cuda cu121
#>

[CmdletBinding()]
param(
    [string]$Cuda = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

Write-Host ''
Write-Host '========================================' -ForegroundColor Cyan
Write-Host ' Imok Lite - Dependency Installer' -ForegroundColor Cyan
Write-Host '========================================' -ForegroundColor Cyan
Write-Host ''

function Resolve-ResourcesRoot {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $candidateRoots = @(
        (Split-Path -Parent $scriptDir),
        (Get-Location).Path
    )

    foreach ($root in $candidateRoots) {
        if (Test-Path (Join-Path $root 'requirements.txt')) {
            return $root
        }
    }

    throw "requirements.txt not found. Run this script from the app resources folder."
}

$resourcesRoot = Resolve-ResourcesRoot
Set-Location $resourcesRoot

Write-Host "Working directory: $resourcesRoot" -ForegroundColor DarkGray

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    throw 'Python not found in PATH. Please install Python 3.12+ and enable "Add Python to PATH".'
}

$pyVersion = (& python --version 2>&1).ToString().Trim()
Write-Host "Python: $pyVersion" -ForegroundColor Green

$venvPython = Join-Path $resourcesRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Host 'Creating virtual environment (.venv)...' -ForegroundColor Yellow
    & python -m venv .venv
}

if (-not (Test-Path $venvPython)) {
    throw '.venv creation failed. .venv\\Scripts\\python.exe was not created.'
}

Write-Host 'Upgrading pip/setuptools/wheel...' -ForegroundColor Yellow
& $venvPython -m pip install --upgrade pip setuptools wheel

Write-Host 'Installing requirements.txt...' -ForegroundColor Yellow
& $venvPython -m pip install -r requirements.txt

if ($Cuda -ne '') {
    Write-Host "Installing CUDA PyTorch wheel: $Cuda" -ForegroundColor Yellow
    & $venvPython -m pip install torch torchaudio --index-url "https://download.pytorch.org/whl/$Cuda"
}

Write-Host ''
Write-Host 'Dependency installation completed.' -ForegroundColor Green
Write-Host "Virtual env python: $venvPython" -ForegroundColor Cyan
Write-Host ''
Write-Host 'Next step:' -ForegroundColor Yellow
Write-Host '  1) Configure .env and config/llm_providers.yaml as needed' -ForegroundColor Yellow
Write-Host '  2) Launch Imok Meeting Assistant.exe' -ForegroundColor Yellow
Write-Host ''
