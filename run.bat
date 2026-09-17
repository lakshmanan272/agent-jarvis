@echo off
setlocal
cd /d "%~dp0"

rem First run creates the virtual environment.
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv || goto :nopython
    ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
    echo Installing dependencies. This takes a minute the first time.
)

rem Then sync dependencies on every launch. pip is a no-op in about a second
rem when everything is satisfied, and installing only on the first run was a
rem real bug: a dependency added later never reached the virtual environment,
rem so openWakeWord was installed while the app went on reporting
rem "openwakeword not installed" and the feature silently did nothing.
rem
rem Caching this on a hash, and before that on timestamps, was tried and
rem produced a bug each time -- once never installing, once always. A second
rem per launch is worth more than the cleverness.
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet --disable-pip-version-check || goto :nodeps

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
