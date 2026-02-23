@echo off
echo Starting AI Chat Application...
echo.

REM Force Python 3.11
set PYTHON311="C:\Users\Josh\AppData\Local\Programs\Python\Python311\python.exe"

if not exist %PYTHON311% (
    echo ERROR: Python 3.11 not found:
    echo   %PYTHON311%
    pause
    exit /b 1
)

echo Using Python interpreter:
echo   %PYTHON311%
echo.

REM Check if Ollama is available
where ollama >nul 2>&1
if errorlevel 1 (
    echo WARNING: Ollama not found in PATH
    echo.
) else (
    echo Checking if Ollama is running...
    curl -s http://localhost:11434/api/tags >nul 2>&1
    if errorlevel 1 (
        echo Starting Ollama...
        start /B ollama serve >nul 2>&1
        timeout /t 3 >nul
    ) else (
        echo Ollama already running.
    )
)

cd /d "%~dp0"

echo Starting ThreadBear...
echo.

%PYTHON311% flask_chat_app.py
pause
