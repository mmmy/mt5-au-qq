@echo off
setlocal
chcp 65001 >nul
title MT5 AU QQ
cd /d "%~dp0"
if errorlevel 1 exit /b 1

rem Keep Python in this console: closing the window ends the backend process tree.
rem Prefer the project's virtual environment; never start a detached server.
if exist "%~dp0.venv\Scripts\python.exe" (
    set "MT5_PYTHON="%~dp0.venv\Scripts\python.exe""
    goto python_found
)
where py >nul 2>&1
if not errorlevel 1 (
    set "MT5_PYTHON=py -3"
    goto python_found
)
where python >nul 2>&1
if not errorlevel 1 (
    set "MT5_PYTHON=python"
    goto python_found
)
echo [ERROR] Python was not found. Install Python and the project dependencies first.
goto failed

:python_found
%MT5_PYTHON% -c "import importlib.util, sys; names=('uvicorn','fastapi','httpx','dotenv','MetaTrader5'); missing=[name for name in names if importlib.util.find_spec(name) is None]; print('Missing dependencies: '+', '.join(missing)) if missing else None; sys.exit(bool(missing))"
if errorlevel 1 (
    echo Install dependencies with:
    echo   %MT5_PYTHON% -m pip install -r requirements.txt
    goto failed
)
if /i "%~1"=="--check" (
    echo [OK] Python and dependencies are available. No server was started.
    exit /b 0
)

set "MT5_APP_HOST=0.0.0.0"
set "MT5_APP_PORT=80"
if not "%~1"=="" set "MT5_APP_HOST=%~1"
if not "%~2"=="" set "MT5_APP_PORT=%~2"
echo MT5 AU QQ is starting on %MT5_APP_HOST%:%MT5_APP_PORT%.
echo Close this window to stop the backend. Ctrl+C performs a graceful stop.
echo Existing positions and MT5 terminal windows are not closed automatically.
echo.
%MT5_PYTHON% -m uvicorn app.main:app --host "%MT5_APP_HOST%" --port "%MT5_APP_PORT%"
if errorlevel 1 goto failed
exit /b 0

:failed
if /i "%~1"=="--check" exit /b 1
echo.
echo [ERROR] Startup failed. Check the message above; an occupied port is not stopped automatically.
pause
exit /b 1
