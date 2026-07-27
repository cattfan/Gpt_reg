@echo off
REM Chi khoi dong Web UI (khong chay lai setup)
if /i not "%~1"=="__keepopen__" (
    cmd /k call "%~f0" __keepopen__
    exit /b
)

setlocal EnableExtensions
cd /d "%~dp0"

set "WEB_PORT=2023"
set "WEB_HOST=127.0.0.1"
set "PYTHON=.venv311\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo ERROR: Chua co .venv311 - chay setup.bat truoc.
    exit /b 1
)

"%PYTHON%" -c "import sys, fastapi, uvicorn, gpt_reg; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)"
if errorlevel 1 (
    echo ERROR: Venv khong hop le - chay setup.bat lai.
    exit /b 1
)

if not exist "gpt_reg\web\static\app\index.html" (
    echo ERROR: Chua co frontend build - chay setup.bat truoc.
    exit /b 1
)

"%PYTHON%" -m gpt_reg migrate
if errorlevel 1 (
    echo ERROR: Database migrate that bai.
    exit /b 1
)

REM Chi dung listener neu no la Python web process cua dung .venv311 trong workspace nay.
powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\prepare_web_port.ps1" -Port %WEB_PORT% -ExpectedPython "%CD%\%PYTHON%"
if errorlevel 1 (
    exit /b 1
)

echo.
echo ===============================================================
echo   Gpt_reg Web UI: http://%WEB_HOST%:%WEB_PORT%/
echo   KHONG DONG cua so nay khi dang dung UI.
echo   Ctrl+C de dung server.
echo ===============================================================
echo.

"%PYTHON%" -m gpt_reg web --host %WEB_HOST% --port %WEB_PORT%
set "SERVER_EXIT=%ERRORLEVEL%"
echo.
echo Server da dung ^(exit %SERVER_EXIT%^).
exit /b %SERVER_EXIT%
