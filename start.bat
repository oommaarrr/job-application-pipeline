@echo off
rem Start Job Pipeline and open the dashboard (Windows).
rem Keep this window open while you use it. Press Ctrl+C to stop.
setlocal
cd /d "%~dp0"
if not exist "scraper\.venv\Scripts\python.exe" (
  echo Job Pipeline is not set up yet. Run setup.bat first.
  pause
  exit /b 1
)
"scraper\.venv\Scripts\python.exe" start.py %*
if errorlevel 1 pause
