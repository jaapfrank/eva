@echo off
setlocal
title Eva Netwerk-Radar (Zoek Eva op thuisnetwerk)
cd /d "%~dp0"

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -m eva.bootstrap.find_appliance_cli %*
) else (
    set "PYTHONPATH=%~dp0src"
    python -m eva.bootstrap.find_appliance_cli %*
)

if errorlevel 1 (
    echo.
    pause
)
