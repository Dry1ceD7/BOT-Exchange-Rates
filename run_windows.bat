@echo off
echo Starting BOT Exchange Rate Processor...

:: Launch the CustomTkinter GUI asynchronously using pythonw (No-Console executable)
:: This prevents the black command prompt window from staying open in the background.
if exist venv\Scripts\pythonw.exe (
    start "" venv\Scripts\pythonw.exe main.py
) else (
    start "" pythonw main.py
)

:: Close this launcher window immediately
exit
