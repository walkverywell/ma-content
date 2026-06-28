@echo off
chcp 65001 >nul
echo M^&A 内容仪表盘启动中...
cd /d %~dp0
py scripts\dashboard.py
pause
