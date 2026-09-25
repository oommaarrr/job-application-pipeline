@echo off
rem Start Job Pipeline automatically at login (optional). No admin rights needed.
rem   install-agent.bat            install and start it now
rem   install-agent.bat --remove   stop it and stop starting at login
setlocal
cd /d "%~dp0.."
if not exist "scraper\.venv\Scripts\python.exe" (
  echo Not set up yet. Run setup.bat first.
  pause
  exit /b 1
)
"scraper\.venv\Scripts\python.exe" scripts\install-agent.py %*
pause
