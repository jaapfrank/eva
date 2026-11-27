@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-eva-usb.ps1" -DownloadUbuntuIso -WriteUsbDrive
pause
