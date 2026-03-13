@echo off
echo Starting AI Chat Application...
echo.

cd /d "%~dp0"

REM Try python from PATH first
where python >nul 2>&1
if %errorlevel%==0 (
    echo Using Python from PATH
    python flask_chat_app.py
) else (
    echo ERROR: Python not found in PATH.
    echo Please install Python 3.10+ and ensure it is added to your PATH.
    pause
    exit /b 1
)
pause
