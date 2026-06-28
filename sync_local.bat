@echo off
chcp 65001 >nul
cd /d "%~dp0"
py scripts\sync_local.py %*
echo.
echo 同步完成，按任意键关闭...
pause >nul
