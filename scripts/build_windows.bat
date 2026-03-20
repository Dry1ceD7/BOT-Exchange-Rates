@echo off
echo =======================================================
echo Building BOT ExRate for Windows (No-Console Mode)
echo =======================================================

:: Compile the application into a single executable without a terminal window
pyinstaller --noconsole --onefile --windowed --name "BOT_ExRate" --icon assets/icon.ico main.py

echo.
echo Build complete. The executable is located in the 'dist' folder.
pause
