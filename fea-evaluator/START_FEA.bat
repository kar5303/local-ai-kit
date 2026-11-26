@echo off
rem Double-click to start: shows the FEA menu (1 input folder / 2 current PrePoMax model / 3 check STEP names).
rem Also accepts arguments, e.g. START_FEA.bat --current
call "%~dp0find_python.bat" || (pause & exit /b 1)
%PY% -X utf8 "%~dp0mcp\auto_analyze.py" %*
if errorlevel 1 pause
