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

cd /d "%~dp0"

echo Starting ThreadBear...
echo.

%PYTHON311% flask_chat_app.py
pause
