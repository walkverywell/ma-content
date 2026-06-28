@echo off
cd /d %~dp0
start "" http://localhost:8899
py -B scripts\dashboard.py
