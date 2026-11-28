<#
.SYNOPSIS
  1-Klik Eva Installer voor een Windows 11 / Windows 10 laptop (WSL2 Ubuntu 24.04).
.DESCRIPTION
  Richt automatisch WSL2, Ubuntu 24.04 met systemd, vmIdleTimeout=-1 (zodat WSL nooit na 8 sec in slaap valt),
  thuisnetwerk-bereikbaarheid (mirrored networking op Windows 11), Docker, Python 3.12, Eva's lokale MCP-kluis
  en de 3D Kennismakings-Wizard in.
#>
param(
    [string]$DistroName = "",
    [switch]$SkipFirewall
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  Eva — 1-Klik Installatie & 3D Onboarding (Windows Laptop)" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object Security.Principal.WindowsPrincipal($id)
    return $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# 1. Stel .wslconfig in (vmIdleTimeout=-1 tegen insluimeren + mirrored networking op Windows 11)
$osBuild = [Environment]::OSVersion.Version.Build
$wslConfigPath = Join-Path $env:USERPROFILE ".wslconfig"
$wslConfigChanged = $false
if (-not (Test-Path $wslConfigPath)) {
    if ($osBuild -ge 22621) {
        $cfgBody = "[wsl2]`r`nvmIdleTimeout=-1`r`nnetworkingMode=mirrored`r`ndnsTunneling=true`r`nautoProxy=true`r`n"
    } else {
        $cfgBody = "[wsl2]`r`nvmIdleTimeout=-1`r`n"
    }
    [System.IO.File]::WriteAllText($wslConfigPath, $cfgBody, [System.Text.UTF8Encoding]::new($false))
    $wslConfigChanged = $true
    Write-Host "[✓] .wslconfig ingericht (vmIdleTimeout=-1 zodat Eva op de achtergrond wakker blijft)." -ForegroundColor Green
} else {
    $rawCfg = Get-Content $wslConfigPath -Raw
    if ($rawCfg -notmatch "vmIdleTimeout") {
        Add-Content -Path $wslConfigPath -Value "`r`nvmIdleTimeout=-1"
        $wslConfigChanged = $true
    }
    if ($osBuild -ge 22621 -and $rawCfg -notmatch "networkingMode") {
        Add-Content -Path $wslConfigPath -Value "`r`nnetworkingMode=mirrored"
        $wslConfigChanged = $true
    }
}

# 2. Controleer of WSL2 + Ubuntu al aanwezig zijn
$wslExe = Join-Path $env:SystemRoot "System32\wsl.exe"
if (-not (Test-Path $wslExe)) {
    throw "wsl.exe niet gevonden. Gebruik Windows 10 (2004+) of Windows 11."
}

$installedDistros = @()
try {
    $rawList = & $wslExe --list --quiet 2>$null
    if ($LASTEXITCODE -eq 0 -and $rawList) {
        $installedDistros = @(
            $rawList | ForEach-Object { ($_ -replace "`0", "").Trim() } | Where-Object { $_ -ne "" -and $_ -notmatch "^docker-desktop" }
        )
    }
} catch {
    $installedDistros = @()
}

if ($installedDistros.Count -eq 0) {
    Write-Host "[1/5] WSL2 + Ubuntu 24.04 is nog niet geïnstalleerd op deze laptop." -ForegroundColor Yellow
    if (-not (Test-IsAdmin)) {
        Write-Host "      Bevestig het Windows-beheerdersvenster (UAC) om WSL2 + Ubuntu automatisch aan te zetten..." -ForegroundColor Yellow
        $argList = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
        Start-Process powershell.exe -Verb RunAs -ArgumentList $argList -Wait
        exit 0
    }
    & $wslExe --install -d Ubuntu-24.04
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Green
    Write-Host "  Bijna klaar met stap 1! Als Windows om een herstart vraagt:" -ForegroundColor Green
    Write-Host "  Herstart je laptop eenmalig en dubbelklik daarna nog één keer" -ForegroundColor Green
    Write-Host "  op Installeer-Eva-Op-Nieuwe-Laptop.cmd om Eva af te ronden!" -ForegroundColor Green
    Write-Host "================================================================" -ForegroundColor Green
    Read-Host "Druk op Enter om te sluiten"
    exit 0
}

if (-not $DistroName) {
    $preferred = $installedDistros | Where-Object { $_ -match "^Ubuntu" } | Select-Object -First 1
    $DistroName = if ($preferred) { $preferred } else { $installedDistros[0] }
}
Write-Host "[1/5] WSL2 Linux-omgeving gevonden: $DistroName" -ForegroundColor Green

# 3. Optioneel: open Windows Firewall poorten 8765 en 8766 voor Privé/Thuisnetwerk
if (-not $SkipFirewall -and (Test-IsAdmin)) {
    $ruleName = "Eva 3D Onboarding & Pocket (Thuisnetwerk)"
    $existingRule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if (-not $existingRule) {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort @(8765, 8766) -Profile Private | Out-Null
        Write-Host "[✓] Windows Firewall geopend voor poorten 8765 (HTTP) en 8766 (HTTPS Hoofdscherm) op jouw thuisnetwerk." -ForegroundColor Green
    }
}

# 4. Zorg dat systemd aanstaat in WSL2 (/etc/wsl.conf)
Write-Host "[2/5] Controleren of systemd actief is in $DistroName..." -ForegroundColor Cyan
$pid1 = (& $wslExe -d $DistroName -u root -- sh -c "ps -p 1 -o comm= 2>/dev/null || true").Trim()
if ($pid1 -ne "systemd" -or $wslConfigChanged) {
    Write-Host "      systemd & netwerkinstellingen activeren en WSL2 kort herstarten..." -ForegroundColor Yellow
    & $wslExe -d $DistroName -u root -- sh -c "grep -q 'systemd=true' /etc/wsl.conf 2>/dev/null || printf '\n[boot]\nsystemd=true\n' >> /etc/wsl.conf"
    & $wslExe --shutdown
    Start-Sleep -Seconds 3
}

# Start een volledig losgekoppelde achtergrond-keepalive zodat WSL2 nooit stopt als dit venster sluit!
$vbsPath = Join-Path $env:TEMP "eva-wsl-keepalive.vbs"
$vbsContent = "Set ws = CreateObject(""WScript.Shell"")`r`nws.Run ""`"$wslExe`" -d $DistroName -u root -- sleep infinity"", 0, False`r`n"
[System.IO.File]::WriteAllText($vbsPath, $vbsContent, [System.Text.Encoding]::ASCII)
Start-Process -FilePath "wscript.exe" -ArgumentList "`"$vbsPath`"" | Out-Null

# 5. Synchroniseer Eva-map naar /opt/eva2 (ext4), strip altijd CRLF (\r) en draai firstboot-install-eva.sh
Write-Host "[3/5] Eva-broncode klaarzetten in snelle Linux-opslag (/opt/eva2)..." -ForegroundColor Cyan
$wslRepoPath = (& $wslExe -d $DistroName -u root -- wslpath -a ($RepoRoot -replace "\\", "/")).Trim()

$syncScript = @'
set -euo pipefail
mkdir -p /opt/eva2
tar -C "__WSL_REPO__" \
  --exclude='.git/objects/pack/*.tmp' \
  --exclude='deploy/appliance/eva-usb-output' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='*.egg-info' \
  -cf - . | tar -C /opt/eva2 -xf -
find /opt/eva2 -type f \( \
  -name '*.sh' -o -name '*.py' -o -name '*.mjs' -o -name '*.js' -o -name '*.cjs' \
  -o -name '*.yml' -o -name '*.yaml' -o -name '*.tmpl' -o -name '*.service' -o -name 'Dockerfile*' \
\) -exec sed -i 's/\r$//' {} +
chmod +x /opt/eva2/deploy/appliance/firstboot-install-eva.sh
'@.Replace("__WSL_REPO__", $wslRepoPath).Replace("`r`n", "`n")
$syncB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($syncScript))
& $wslExe -d $DistroName -u root -- bash -lc "echo $syncB64 | base64 -d | tr -d '\r' | bash"
if ($LASTEXITCODE -ne 0) {
    throw "Synchroniseren van Eva-broncode naar /opt/eva2 is mislukt (code $LASTEXITCODE)."
}

Write-Host "[4/5] Eva services, Docker, Python 3.12, Kluis & 3D Stage installeren (eerste keer ~3-6 min)..." -ForegroundColor Cyan
Write-Host "      Tip: je kunt ondertussen al meekijken op http://127.0.0.1:8765 !" -ForegroundColor DarkCyan
& $wslExe -d $DistroName -u root -- bash -lc "sed -i 's/\r$//' /opt/eva2/deploy/appliance/firstboot-install-eva.sh && EVA_REPO_DIR=/opt/eva2 bash /opt/eva2/deploy/appliance/firstboot-install-eva.sh"
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[!] De installatie in WSL2 meldde een fout (code $LASTEXITCODE). Laatste service-logs:" -ForegroundColor Red
    & $wslExe -d $DistroName -u root -- bash -lc "journalctl -u eva2-appliance-wizard.service -n 25 --no-pager || true"
    throw "firstboot-install-eva.sh is niet volledig afgerond."
}

# 6. Verifieer dat poort 8765 écht vanuit Windows bereikbaar is (via 127.0.0.1 of WSL IP fallback)!
Write-Host "[5/5] Verifiëren dat 3D Eva vanuit Windows bereikbaar is op poort 8765..." -ForegroundColor Cyan
$wslIp = (((& $wslExe -d $DistroName -u root -- hostname -I 2>$null) -join " ") -split "\s+")[0].Trim()
$candidateHosts = @("127.0.0.1")
if ($wslIp -and $wslIp -ne "127.0.0.1") {
    $candidateHosts += $wslIp
}

$workingHost = ""
for ($i = 0; $i -lt 30; $i++) {
    foreach ($h in $candidateHosts) {
        try {
            $ping = Invoke-RestMethod -Uri "http://${h}:8765/internal/v1/appliance/ping" -TimeoutSec 2
            if ($ping.eva_appliance -eq $true -and $ping.phase -ne "bootstrapping") {
                $workingHost = $h
                break
            }
        } catch {}
    }
    if ($workingHost) { break }
    Start-Sleep -Seconds 1
}

if (-not $workingHost) {
    Write-Host ""
    Write-Host "[!] Eva draait nog niet bereikbaar op poort 8765 vanuit Windows. Diagnose:" -ForegroundColor Red
    & $wslExe -d $DistroName -u root -- bash -lc "systemctl status eva2-appliance-wizard.service --no-pager -l || true"
    throw "Poort 8765 werd niet op tijd bereikbaar."
}

$openUrl = "http://${workingHost}:8765/pair"
Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  Eva draait actief en is geverifieerd!" -ForegroundColor Green
Write-Host "  De 3D Kennismakings-Wizard wordt nu geopend op:" -ForegroundColor Green
Write-Host "  $openUrl" -ForegroundColor White
Write-Host "================================================================" -ForegroundColor Green
Start-Process $openUrl
