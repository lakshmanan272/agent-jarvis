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
