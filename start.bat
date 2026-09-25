@echo off
rem Start Job Pipeline and open the dashboard (Windows).
rem Works straight after downloading: the first run installs and sets up
rem whatever is missing, asking first. Keep this window open while you use
rem it; close it or press Ctrl+C to stop.
setlocal
cd /d "%~dp0"
call scripts\find-python.bat venv-ok
if errorlevel 1 (
  pause
  exit /b 1
)
%PYEXE% start.py %*
if errorlevel 1 pause
