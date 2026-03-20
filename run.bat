@echo off
setlocal DisableDelayedExpansion

echo =======================================================
echo BOT Exchange Rate Processor - Windows Setup ^& Launcher
echo =======================================================

:: --------------------------------------------------------------------------
:: MANDATE 4: VENV-Free Global Compatibility
:: This application runs on the global system Python installation.
:: No virtual environment is created or activated.
:: --------------------------------------------------------------------------

:: Verify Python is available
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [FATAL] Python is not installed or not in your PATH.
    echo         Please install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

:: Install/upgrade dependencies globally
echo [INFO] Synchronizing dependencies (global system Python)...
python -m pip install --upgrade pip >nul 2>&1
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [FATAL] Dependency installation failed. Please check your internet connection.
    echo         If you see a PermissionError, run this script as Administrator.
    pause
    exit /b 1
)

echo.
echo [SUCCESS] Environment fully configured!
echo [INFO] Launching the BOT_Exrate Interface...

:: Start the application invisibly (No command prompt left active)
start "" pythonw.exe main.py

exit
