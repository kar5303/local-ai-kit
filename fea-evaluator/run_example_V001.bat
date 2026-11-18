@echo off
chcp 65001 >nul
set PYTHONUTF8=1
call "%~dp0find_python.bat" || (pause & exit /b 1)
cd /d "%~dp0example"
echo === V-001 example validation ===
%PY% ..\fea_eval.py Analysis-1.inp Analysis-1.frd fea_config.json
echo.
type "Analysis-1_評估結果\report.md"
echo.
pause
