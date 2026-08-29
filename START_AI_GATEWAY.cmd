@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON=%~dp0.ai_gateway_env\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo AI 网关尚未安装。请先运行 INSTALL_AI_GATEWAY.cmd。
    pause
    exit /b 1
)

"%PYTHON%" -m app.ai_gateway
exit /b %ERRORLEVEL%
