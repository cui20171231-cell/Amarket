@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "TUNNEL_CLIENT=%~dp0AI_Tunnel\tunnel-client.exe"
if not exist "%TUNNEL_CLIENT%" (
    echo 找不到隧道程序：%TUNNEL_CLIENT%
    pause
    exit /b 1
)
if not defined CONTROL_PLANE_API_KEY (
    echo 未读取到 CONTROL_PLANE_API_KEY。
    pause
    exit /b 1
)
if not defined CONTROL_PLANE_TUNNEL_ID (
    echo 未读取到 CONTROL_PLANE_TUNNEL_ID。
    pause
    exit /b 1
)

"%TUNNEL_CLIENT%" run --control-plane.tunnel-id "%CONTROL_PLANE_TUNNEL_ID%" --mcp.server-url "http://127.0.0.1:2091/mcp"
exit /b %ERRORLEVEL%
