@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "VENV_PY=%ROOT%.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo Andromeda is not installed yet.
    echo Run install.bat first.
    pause
    exit /b 1
)

pushd "%ROOT%" >nul 2>&1
if errorlevel 1 (
    echo Failed to enter launch directory: %ROOT%
    pause
    exit /b 1
)

"%VENV_PY%" "%ROOT%andromeda.py" %*
set "EXIT_CODE=%ERRORLEVEL%"

popd

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Andromeda exited with code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%
