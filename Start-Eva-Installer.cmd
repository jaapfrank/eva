@echo off
setlocal
title Eva Setup, Karakter ^& Contact Wizard
echo Eva achtergrondomgeving wakker maken en openhouden...

REM Houd WSL2 op de achtergrond actief zodat Windows de Eva-services niet na 15 sec slaapstand stopt
powershell.exe -NoProfile -WindowStyle Hidden -Command "if (-not (Get-CimInstance Win32_Process -Filter \"Name='wsl.exe'\" | Where-Object { $_.CommandLine -match 'sleep infinity' })) { Start-Process -FilePath 'wsl.exe' -ArgumentList '-e','sleep','infinity' -WindowStyle Hidden }"

echo Services controleren...
wsl.exe -u root -e bash -lc "systemctl start eva2-local-gateway.service eva2-appliance-wizard.service eva2-local-core.service eva2-local-worker.service eva2-local-owner.service 2>/dev/null || systemctl start eva2-appliance-wizard.service"

echo Wachten tot poort 8765 gereed is...
for /l %%i in (1,1,25) do (
  curl.exe -fsS --max-time 2 http://127.0.0.1:8765/internal/v1/appliance/ping >nul 2>nul && goto :ready
  timeout /t 1 /nobreak >nul
)

:ready
set "EVA_URL="
for /f "usebackq delims=" %%U in (`wsl.exe -u root -e python3 -c "import pathlib; p=pathlib.Path('/var/lib/eva2-appliance/pairing-link.private.txt'); txt=p.read_text().splitlines() if p.is_file() else []; hit=[l.split('=',1)[1].strip() for l in txt if l.startswith('local_url=')] + [l.strip() for l in txt if l.startswith('http')]; print(hit[0] if hit else '')" 2^>nul`) do (
  if not "%%U"=="" set "EVA_URL=%%U"
)
if defined EVA_URL goto :open

for /f "usebackq delims=" %%T in (`wsl.exe -u root -e head -n 1 /var/lib/eva2-local/secrets/dashboard-token 2^>nul`) do (
  if not "%%T"=="" set "EVA_URL=http://127.0.0.1:8765/pair#%%T"
)

:open
if "%EVA_URL%"=="" (
  set "EVA_URL=http://127.0.0.1:8765/"
)
echo Eva is bereikbaar! Browser openen...
start "" "%EVA_URL%"
