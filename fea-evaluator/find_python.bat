@echo off
rem Sets PY to a working Python (skips the Microsoft Store stub).
set PY=
for %%C in ("%USERPROFILE%\anaconda3\python.exe" "%USERPROFILE%\miniconda3\python.exe" "%LOCALAPPDATA%\anaconda3\python.exe" "C:\ProgramData\anaconda3\python.exe" "C:\ProgramData\Anaconda3\python.exe" "C:\ProgramData\miniconda3\python.exe") do (
  if not defined PY if exist %%C (%%C -c "import sys" >nul 2>nul && set PY=%%C)
)
if not defined PY for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
  if not defined PY if exist "%%D\python.exe" ("%%D\python.exe" -c "import sys" >nul 2>nul && set PY="%%D\python.exe")
)
if not defined PY (py -3 -c "import sys" >nul 2>nul && set PY=py -3)
if not defined PY (python -c "import sys" >nul 2>nul && set PY=python)
if not defined PY (
  echo [ERROR] No working Python found.
  echo Checked: anaconda3 / miniconda3 / Programs\Python / py / python
  exit /b 1
)
echo Python: %PY%
%PY% -c "import sys; print(sys.version)"
exit /b 0
