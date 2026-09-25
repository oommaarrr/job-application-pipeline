@echo off
rem Start Job Pipeline automatically at login (optional). No admin rights needed.
rem   install-agent.bat            install and start it now
rem   install-agent.bat --remove   stop it and stop starting at login
setlocal
cd /d "%~dp0.."
call scripts\find-python.bat venv-ok
if errorlevel 1 (
  pause
  exit /b 1
)
%PYEXE% scripts\install-agent.py %*
pause
