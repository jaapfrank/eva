@echo off
echo Eva WSL2-achtergrondomgeving netjes afsluiten om geheugen vrij te maken...
wsl.exe --shutdown
echo Eva is gepauzeerd. Start opnieuw via Start-Eva-Installer.cmd.
timeout /t 2 >nul
