@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PYTHON=.venv311\Scripts\python.exe"

REM Python 3.11 bat buoc: 3.12+ chua co wheel curl_cffi/cffi -> HTTP phase + MFA hong.
if exist "%PYTHON%" (
    "%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)"
    if errorlevel 1 (
        echo ERROR: .venv311 ton tai nhung khong dung Python 3.11.
        exit /b 1
    )
    echo Reusing existing Python 3.11 venv: .venv311
) else (
    py -3.11 --version >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Chua cai Python 3.11 ^(py -3.11 khong chay duoc^).
        echo Tai: https://www.python.org/downloads/release/python-3119/
        exit /b 1
    )
    py -3.11 -m venv .venv311
    if errorlevel 1 exit /b 1
)

"%PYTHON%" -m pip install -U pip
if errorlevel 1 exit /b 1
"%PYTHON%" -m pip install -e .
if errorlevel 1 exit /b 1
"%PYTHON%" -m camoufox fetch official/152.0.4-beta.28
if errorlevel 1 exit /b 1
"%PYTHON%" -m camoufox set official/stable/152.0.4-beta.28
if errorlevel 1 exit /b 1

REM Node la bat buoc de build Vue/Tailwind va cho sentinel QuickJS cua reg HTTP.
where node >nul 2>&1
if errorlevel 1 (
    echo ERROR: Chua cai Node.js 22+ ^(can de build Web UI^).
    echo Tai: https://nodejs.org/
    exit /b 1
)
for /f "tokens=1 delims=." %%v in ('node -p "process.versions.node"') do set "NODE_MAJOR=%%v"
if %NODE_MAJOR% LSS 22 (
    echo ERROR: Node.js 22+ bat buoc, hien tai la v%NODE_MAJOR%.
    exit /b 1
)
pushd frontend
call npm ci
if errorlevel 1 (popd & exit /b 1)
call npm run test:run
if errorlevel 1 (popd & exit /b 1)
call npm run build
if errorlevel 1 (popd & exit /b 1)
popd

if not exist runtime mkdir runtime
"%PYTHON%" -m gpt_reg migrate
if errorlevel 1 exit /b 1
"%PYTHON%" test\smoke_root_imports.py
if errorlevel 1 exit /b 1
"%PYTHON%" test\run_all.py
if errorlevel 1 exit /b 1
"%PYTHON%" test\smoke_browser_launch.py
if errorlevel 1 exit /b 1
echo Setup complete. Run: start.bat
