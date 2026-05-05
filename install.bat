@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
pushd "%ROOT%" >nul 2>&1
if errorlevel 1 (
    echo Failed to enter install directory: %ROOT%
    pause
    exit /b 1
)

if not exist "%ROOT%andromeda.py" (
    echo Could not find andromeda.py in:
    echo %ROOT%
    echo Run this installer from the Andromeda project folder.
    pause
    popd
    exit /b 1
)

set "PY_CMD="
where py >nul 2>&1
if not errorlevel 1 set "PY_CMD=py -3"

if not defined PY_CMD (
    where python >nul 2>&1
    if not errorlevel 1 set "PY_CMD=python"
)

if not defined PY_CMD (
    echo Python 3.10+ was not found.
    echo Install Python from https://www.python.org/downloads/windows/
    echo and make sure the launcher command ^("py" or "python"^) is available.
    pause
    popd
    exit /b 1
)

echo [1/4] Using Python launcher: %PY_CMD%

if not exist "%ROOT%.venv\Scripts\python.exe" (
    echo [2/4] Creating virtual environment...
    %PY_CMD% -m venv "%ROOT%.venv"
    if errorlevel 1 (
        echo Failed to create virtual environment.
        pause
        popd
        exit /b 1
    )
) else (
    echo [2/4] Virtual environment already exists.
)

set "VENV_PY=%ROOT%.venv\Scripts\python.exe"
echo [3/4] Upgrading pip...
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 (
    echo Failed to upgrade pip.
    pause
    popd
    exit /b 1
)

echo [4/4] Installing Python dependencies...
"%VENV_PY%" -m pip install pygame PyOpenGL
if errorlevel 1 (
    echo Failed to install one or more dependencies.
    pause
    popd
    exit /b 1
)

echo.
echo Installation complete.
echo Double-click andromeda_launcher.bat to start Andromeda.
echo You can also drag and drop an .stg file onto andromeda_launcher.bat.

popd
exit /b 0
