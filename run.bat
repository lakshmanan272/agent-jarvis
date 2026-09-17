@echo off
setlocal
cd /d "%~dp0"

rem First run bootstraps a virtual environment and installs dependencies.
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv || goto :nopython
    echo Installing dependencies. This takes a minute the first time.
    ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :nodeps
)

rem Plain double-click, no arguments: launch silently via pythonw so nothing
rem but the Jarvis orb appears on screen -- no console window left behind.
if "%~1"=="" (
    start "" ".venv\Scripts\pythonw.exe" -m jarvis
    goto :eof
)

rem Called with arguments (a one-shot command, --list, --benchmark, etc.):
rem keep the console so the output is actually visible.
".venv\Scripts\python.exe" -m jarvis %*
goto :eof

:nopython
echo.
echo Python was not found on PATH. Install Python 3.10 or newer from python.org
echo and tick "Add python.exe to PATH" during setup.
pause
goto :eof

:nodeps
echo.
echo Dependency installation failed. Check the output above.
pause
