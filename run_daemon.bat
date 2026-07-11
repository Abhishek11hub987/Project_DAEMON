@echo off
REM ===========================================================
REM D.A.E.M.O.N. one-click launcher (Windows)
REM Uses the project venv if it exists, otherwise system Python.
REM ===========================================================

setlocal
pushd "%~dp0"

set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo.
echo [DAEMON] Using interpreter: %PY%
echo.

"%PY%" quickstart.py %*
set RC=%ERRORLEVEL%

popd
endlocal & exit /b %RC%
