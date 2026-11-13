@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    py -3.11 -m venv .venv
    if errorlevel 1 goto fail
    .venv\Scripts\python.exe -m pip install -e .
    if errorlevel 1 goto fail
)
.venv\Scripts\python.exe -m geotrace.app %*
if errorlevel 1 goto fail
exit /b 0
:fail
echo Could not start GeoTrace. Python 3.11 and internet are required for first setup.
pause
exit /b 1
