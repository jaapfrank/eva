@echo off
setlocal
title Eva — 1-Klik Installatie op Nieuwe Laptop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\appliance\install-eva-wsl.ps1"
if errorlevel 1 (
    echo.
    echo Er trad een melding op tijdens het voorbereiden. Lees de tekst hierboven even na.
    pause
)
