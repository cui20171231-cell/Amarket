@echo off
chcp 65001 >nul
echo 即将请求 Windows 管理员权限，执行 N100 第一阶段优化。
echo 脚本不会重启系统，不会停止 Docker、ClickHouse、采集、网关或隧道。
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell.exe -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File ""D:\Amarket\N100系统优化记录\执行第一阶段-管理员.ps1""' -Wait"
echo.
echo 管理员脚本已结束，可关闭此窗口。
pause
