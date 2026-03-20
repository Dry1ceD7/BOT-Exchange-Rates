@echo off
setlocal DisableDelayedExpansion

echo =======================================================
echo BOT Exchange Rate Processor - Windows Setup ^& Launcher
echo =======================================================

:: --------------------------------------------------------------------------
:: 3. Clean-Boot Failsafe: Validate Virtual Environment Integrity
:: --------------------------------------------------------------------------
if exist venv\ (
    if not exist venv\Scripts\python.exe (
        echo [WARNING] Corrupted virtual environment detected (missing python.exe).
        echo [INFO] Performing clean rebuild of the venv folder...
        rmdir /S /Q venv
    ) else if not exist venv\Scripts\activate.bat (
        echo [WARNING] Corrupted virtual environment detected (missing activate.bat).
        echo [INFO] Performing clean rebuild of the venv folder...
        rmdir /S /Q venv
    )
)

:: --------------------------------------------------------------------------
:: 1. Preserve the Virtual Environment (Creation Phase)
:: --------------------------------------------------------------------------
if not exist venv\ (
    echo [INFO] Initializing new Python isolated virtual environment...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [FATAL] Python 'venv' creation failed. Is Python 3.10+ installed and in your PATH?
        pause
        exit /b 1
    )
)

:: --------------------------------------------------------------------------
:: 2. Fix the Pip Install Command (Strictly NO --user flag)
:: --------------------------------------------------------------------------
echo [INFO] Activating Enterprise Virtual Environment...
call venv\Scripts\activate.bat

echo [INFO] Synchronizing dependencies into strict isolation (no user packages)...
python -m pip install --upgrade pip >nul 2>&1
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [FATAL] Dependency installation failed. Please check your internet connection.
    pause
    exit /b 1
)

echo.
echo [SUCCESS] Environment fully configured and hardened!
echo [INFO] Launching the BOT_Exrate Interface asynchronously...

:: Start the application invisibly (No command prompt left active)
start "" venv\Scripts\pythonw.exe main.py

exit
