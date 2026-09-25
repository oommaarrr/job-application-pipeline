@echo off
rem One-shot setup for Windows. Safe to re-run: every step checks first.
rem Double-click it, or run it in a terminal. The steps live in setup.py.
setlocal
cd /d "%~dp0"
set "PYEXE="
where py >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE (
  where python >nul 2>nul && python -c "import sys" >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
  echo Python 3 is not installed. Get it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" in the installer, then run this again.
  pause
  exit /b 1
)
%PYEXE% setup.py %*
set "CODE=%ERRORLEVEL%"
echo.
pause
exit /b %CODE%
