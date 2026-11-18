@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
python explore_api.py
echo.
pause
