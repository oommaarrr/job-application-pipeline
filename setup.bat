@echo off
rem One-shot setup for Windows. Safe to re-run: every step checks first.
rem Double-click it, or run it in a terminal. The steps live in setup.py.
setlocal
cd /d "%~dp0"
call scripts\find-python.bat
if errorlevel 1 (
  pause
  exit /b 1
)
%PYEXE% setup.py %*
set "CODE=%ERRORLEVEL%"
echo.
pause
exit /b %CODE%
