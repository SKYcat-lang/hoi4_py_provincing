@echo off
REM HOI4 Province Painter - One-click runner
REM Creates .venv on first run, installs deps, then launches the app.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3 -m venv .venv
    if errorlevel 1 python -m venv .venv
    if errorlevel 1 (
        echo Failed to create venv. Install Python 3.10+ from https://www.python.org/downloads/
        pause
        exit /b 1
    )
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Failed to install dependencies.
        pause
        exit /b 1
    )
)

.venv\Scripts\python.exe main.py
if errorlevel 1 pause
