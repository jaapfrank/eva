@echo off
setlocal
title Eva UniFi Connect Display APK Builder
echo ================================================================
echo   Eva Hoofdscherm APK bouwen voor UniFi Connect Display (21.5")
echo ================================================================
echo.
wsl.exe -u root -e bash -lc "cd \"$(wslpath -a '%~dp0deploy/unifi-display-apk')\" && sed -i 's/\r$//' build-apk.sh && chmod +x build-apk.sh && bash ./build-apk.sh"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] Bouwen mislukt. Zorg dat WSL2 en Docker actief zijn.
    pause
    exit /b 1
)
echo.
echo [OK] Het bestand staat klaar in:
echo      %~dp0deploy\unifi-display-apk\eva-unifi-connect-display.apk
echo.
explorer.exe /select,"%~dp0deploy\unifi-display-apk\eva-unifi-connect-display.apk"
pause
