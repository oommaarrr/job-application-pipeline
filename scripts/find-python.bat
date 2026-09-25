@echo off
rem Sets PYEXE to a working Python 3 for the .bat files, installing one first
rem if there is none (after asking). Used by start.bat and setup.bat.
set "PYEXE="
if exist "%~dp0..\scraper\.venv\Scripts\python.exe" if "%1"=="venv-ok" set "PYEXE="%~dp0..\scraper\.venv\Scripts\python.exe""
if defined PYEXE exit /b 0
where py >nul 2>nul && py -3 -c "import sys" >nul 2>nul && set "PYEXE=py -3"
if defined PYEXE exit /b 0
where python >nul 2>nul && python -c "import sys" >nul 2>nul && set "PYEXE=python"
if defined PYEXE exit /b 0
for %%V in (313 312 311) do if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set "PYEXE="%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe""
if defined PYEXE exit /b 0

echo Python is not installed. Job Pipeline needs it (Python 3.12, about 25 MB).
where winget >nul 2>nul
if errorlevel 1 goto :manual
choice /C YN /M "Install it now"
if errorlevel 2 goto :manual
winget install -e --id Python.Python.3.12 --source winget --scope user --accept-package-agreements --accept-source-agreements
rem A new install is not on this window's PATH yet, so use its known place.
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE="%LOCALAPPDATA%\Programs\Python\Python312\python.exe""
if defined PYEXE exit /b 0

:manual
echo Install Python from https://www.python.org/downloads/ and tick
echo "Add python.exe to PATH" in the installer, then run this again.
exit /b 1
