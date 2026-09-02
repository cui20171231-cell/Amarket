@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "VENV=%~dp0.ai_gateway_env"
if not exist "%VENV%\Scripts\python.exe" (
    py -3.11 -m venv "%VENV%"
    if errorlevel 1 exit /b 1
)

"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check --upgrade pip
if errorlevel 1 exit /b 1
"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -e "%~dp0"
if errorlevel 1 exit /b 1
"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -r "%~dp0requirements-ai-gateway.txt"
exit /b %ERRORLEVEL%
