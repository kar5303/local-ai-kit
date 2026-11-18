@echo off
chcp 65001 >nul
set PYTHONUTF8=1
call "%~dp0..\find_python.bat" || (pause & exit /b 1)
cd /d "%~dp0"
%PY% mcp_probe.py
echo.
pause
