@echo off
setlocal
cd /d "%~dp0"

where py.exe >nul 2>nul
if %errorlevel%==0 (
    py -3 networksweep.py %*
    goto :end
)

where python.exe >nul 2>nul
if %errorlevel%==0 (
    python networksweep.py %*
    goto :end
)

echo Python 3 was not found in PATH.
echo Install Python 3.8 or newer, then run this file again.
exit /b 1

:end
if not %errorlevel%==0 pause
