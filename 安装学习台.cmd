@echo off
setlocal DisableDelayedExpansion
if not exist "%~dp0app\windows\install.ps1" (
    echo Please extract the entire ZIP, then run this file from the learning-hub folder.
    pause
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\windows\install.ps1"
if errorlevel 1 (
    echo Installation was not completed. Please read the installation guide.
    pause
    exit /b 1
)
exit /b 0
