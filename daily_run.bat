@echo off
chcp 65001 >nul
cd /d "%~dp0"
py scripts\daily_run.py %*
echo.
echo 全流程完成，按任意键关闭...
pause >nul
