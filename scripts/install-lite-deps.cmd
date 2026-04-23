@echo off
setlocal

echo ========================================
echo  Imok Lite - Dependency Installer
echo ========================================
echo.

set SCRIPT_DIR=%~dp0
set PS1=%SCRIPT_DIR%install-lite-deps.ps1

if not exist "%PS1%" (
  echo install-lite-deps.ps1 not found: %PS1%
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
set EXIT_CODE=%ERRORLEVEL%

if not "%EXIT_CODE%"=="0" (
  echo.
  echo Installation failed with exit code %EXIT_CODE%.
  pause
  exit /b %EXIT_CODE%
)

echo.
echo Installation complete.
pause
exit /b 0
