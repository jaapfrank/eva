<#
.SYNOPSIS
Maak een headless Eva Appliance USB-pakket + vooraf gegenereerde koppel-link voor de HP Pro Mini 400 G9.

.DESCRIPTION
1. Genereert vooraf op deze laptop een unieke koppelcode (EVA-XXXX-XXXX-XXXX), zet alleen de gesalte
   SHA-256 hash in de USB-seed (pairing.json), en maakt op deze laptop het klikbare bestand
   Open-Eva-Setup.html zodat je na opstarten met 1 klik zonder scherm koppelt via http://eva.local:8765.
2. Schrijft een compleet cloud-init autoinstall seed-pakket naar .\eva-usb-output\.
3. Als een Ubuntu Server 24.04 Live ISO wordt opgegeven (of automatisch gedownload met -DownloadUbuntuIso),
   wordt via WSL (xorriso) één volledig schermvrije bootable ISO gebouwd die automatisch na 5 seconden
   start zonder menu-keuze en na installatie de pc uitschakelt (poweroff).
4. Optioneel (-WriteUsbDrive) schrijft veilig naar een verwijderbare USB-stick na expliciete bevestiging.
#>

[CmdletBinding()]
param(
    [string]$IsoPath = "",
    [switch]$DownloadUbuntuIso,
    [string]$OutputDir = "",
    [string]$WifiSsid = "",
    [string]$WifiPassword = "",
    [string]$TargetUsbDriveLetter = "",
    [switch]$WriteUsbDrive
)

$ErrorActionPreference = "Stop"
if (-not $OutputDir) {
    $OutputDir = Join-Path $PSScriptRoot "eva-usb-output"
}
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)

function New-EvaPairingCode {
    $chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789".ToCharArray()
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $bytes = New-Object byte[] 12
    $rng.GetBytes($bytes)
    $groups = for ($g = 0; $g -lt 3; $g++) {
        $groupChars = for ($i = 0; $i -lt 4; $i++) {
            $chars[$bytes[$g * 4 + $i] % $chars.Length]
        }
        -join $groupChars
    }
    return "EVA-" + ($groups -join "-")
}

function Get-Sha256Hex([string]$text) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $hash = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($text))
    $hex = $hash | ForEach-Object { $_.ToString("x2") }
    return -join $hex
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$seedDir = Join-Path $OutputDir "eva-seed"
New-Item -ItemType Directory -Force -Path $seedDir | Out-Null

# 1. Genereer eenmalige koppelcode + gesalte hash
$pairingCode = New-EvaPairingCode
$saltBytes = New-Object byte[] 16
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($saltBytes)
$saltHexParts = $saltBytes | ForEach-Object { $_.ToString("x2") }
$saltHex = -join $saltHexParts
$codeHash = Get-Sha256Hex "$saltHex`:$pairingCode"
$nowUtc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

$pairingObj = [ordered]@{
    contract    = "EvaAppliancePairing/v1"
    salt        = $saltHex
    code_sha256 = $codeHash
    created_at  = $nowUtc
}
$pairingJson = ($pairingObj | ConvertTo-Json -Compress) + "`n"
[System.IO.File]::WriteAllText((Join-Path $seedDir "pairing.json"), $pairingJson, [System.Text.UTF8Encoding]::new($false))

# Kopieer firstboot-install-eva.sh met exacte LF-regeleinden
$firstbootSrc = Join-Path $PSScriptRoot "firstboot-install-eva.sh"
$firstbootLf = ([System.IO.File]::ReadAllText($firstbootSrc, [System.Text.UTF8Encoding]::new($false))).Replace("`r`n", "`n")
[System.IO.File]::WriteAllText((Join-Path $seedDir "firstboot-install-eva.sh"), $firstbootLf, [System.Text.UTF8Encoding]::new($false))

# 2. Bouw zelfstandig Eva2 repo-archief in eva-seed\eva-repo.tar.gz (inclusief nieuwe wizard-code)
$repoTarPath = Join-Path $seedDir "eva-repo.tar.gz"
$wslRepoRoot = (wsl -e wslpath -u ($repoRoot.Replace('\', '/'))).Trim()
$wslRepoTar = (wsl -e wslpath -u ($repoTarPath.Replace('\', '/'))).Trim()
Write-Host "Eva2 repo-archief inpakken in $repoTarPath..." -ForegroundColor Cyan
$tarScript = @'
set -euo pipefail
tar -czf "__WSL_TAR__" \
  --exclude=".venv" \
  --exclude=".pytest_cache" \
  --exclude="__pycache__" \
  --exclude="*.pyc" \
  --exclude=".git/objects/pack/*.tmp" \
  --exclude="deploy/appliance/eva-usb-output" \
  --exclude="Open-Eva-HP-Mini-Setup.html" \
  -C "__WSL_ROOT__" .
'@.Replace("__WSL_TAR__", $wslRepoTar).Replace("__WSL_ROOT__", $wslRepoRoot).Replace("`r`n", "`n")
$tarB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($tarScript))
wsl -u jaapfrank -e bash -lc "echo $tarB64 | base64 -d > /tmp/eva-repo-tar.sh && bash /tmp/eva-repo-tar.sh </dev/null"
if ($LASTEXITCODE -ne 0) { throw "Inpakken van eva-repo.tar.gz via WSL mislukt." }
if (-not (Test-Path $repoTarPath) -or (Get-Item $repoTarPath).Length -lt 102400) {
    throw "eva-repo.tar.gz ontbreekt of is onverwacht klein."
}

# 3. Bouw autoinstall user-data en meta-data
$wifiBlock = ""
if ($WifiSsid.Trim()) {
    $escapedSsid = $WifiSsid.Trim().Replace('"', '\"')
    $escapedPass = $WifiPassword.Replace('"', '\"')
    $wifiBlock = @"
  wifis:
    all-wl:
      match:
        name: "wl*"
      dhcp4: true
      optional: true
      access-points:
        "$escapedSsid":
          password: "$escapedPass"
"@
}

$tmpl = [System.IO.File]::ReadAllText((Join-Path $PSScriptRoot "autoinstall-user-data.yaml.tmpl"), [System.Text.UTF8Encoding]::new($false))
$userData = $tmpl.Replace("__WIFI_BLOCK__", $wifiBlock).Replace("`r`n", "`n")
[System.IO.File]::WriteAllText((Join-Path $seedDir "user-data"), $userData, [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::WriteAllText((Join-Path $seedDir "meta-data"), "instance-id: eva-appliance-01`n", [System.Text.UTF8Encoding]::new($false))

# 4. Maak Open-Eva-Setup.html op de laptop (1-klik + live verbindingschecker naar http://eva.local:8765)
$setupHtml = @"
<!doctype html>
<html lang="nl">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Open Eva Setup · HP Pro Mini 400 G9</title>
<style>
body{font:16px/1.55 system-ui,-apple-system,Segoe UI,sans-serif;background:#07111c;color:#edf6ff;display:grid;min-height:100vh;place-items:center;margin:0;padding:1.5rem}
main{max-width:640px;width:100%;background:#102337;border:1px solid #2b4a66;border-radius:18px;padding:2rem;box-shadow:0 20px 50px rgba(0,0,0,.45)}
h1{margin:0 0 .5rem;font-size:1.55rem}
.radar{padding:.9rem 1rem;border-radius:12px;background:#081625;border:1px solid #2e5375;margin:.9rem 0}
.radar.ok{border-color:#2ea877;background:#09251c}
.bar{height:8px;background:#050e18;border-radius:999px;overflow:hidden;margin-top:.55rem;display:none}
.fill{height:100%;width:12%;background:linear-gradient(90deg,#2ea877,#62e7ff);transition:width .5s}
ol{margin:.9rem 0 1.2rem;padding-left:1.3rem;color:#cfe2f3}
li{margin-bottom:.45rem}
.actions{display:flex;flex-wrap:wrap;gap:.65rem;margin:1.1rem 0}
a.btn,button.btn{display:inline-block;padding:.82rem 1.2rem;border-radius:10px;border:0;background:#36be86;color:#042014;font-weight:700;font-size:.98rem;text-decoration:none;cursor:pointer}
a.btn.sec,button.btn.sec{background:#1c3d5c;color:#edf6ff;border:1px solid #3c658a}
code#pair-code-box{display:block;padding:.85rem;background:#081523;border:1px solid #2b4a66;border-radius:10px;font-size:1.25rem;letter-spacing:.1em;color:#8de8ff;text-align:center;margin:.7rem 0}
.fallback{margin-top:1.15rem;padding-top:1rem;border-top:1px solid #24405a}
.row{display:flex;gap:.5rem;margin-top:.45rem;flex-wrap:wrap}
input{flex:1;min-width:170px;padding:.65rem .8rem;border-radius:8px;border:1px solid #486b8a;background:#081523;color:#fff;font-size:.98rem}
small{color:#9fb9d0;display:block;margin-top:.75rem}
</style>
<main>
  <h1>Eva installeren &amp; verbinden (HP Pro Mini 400 G9)</h1>
  <div id="radar-box" class="radar">
    <strong id="radar-title">Live Netwerk-check: zoeken naar <code>eva.local:8765</code>…</strong>
    <div id="radar-detail" style="font-size:.9rem;color:#b7cde0;margin-top:.2rem">Zodra de HP Pro Mini na installatie opnieuw opstart, verschijnt hier vanaf seconde 10 live de installatievoortgang.</div>
    <div id="radar-bar" class="bar"><div id="radar-fill" class="fill"></div></div>
  </div>
  <ol>
    <li>Steek de <strong>USB-stick</strong> + <strong>netwerkkabel</strong> in de HP Pro Mini en zet hem aan. *(Tip: op een eigen laptop met adminrechten geeft het wegschrijven van <code>eva2-hp-mini-autoinstall.iso</code> via Rufus maximale HP UEFI-compatibiliteit.)*</li>
    <li>Ubuntu + Eva installeren zichzelf schermvrij (~5–10 min) en de pc <strong>schakelt zichzelf uit</strong> zodra het klaar is.</li>
    <li>Trek de USB-stick eruit en zet de HP Pro Mini weer aan. Dit scherm pikt Eva automatisch op!</li>
    <li>Vindt Windows <code>eva.local</code> niet? Dubbelklik op <strong><code>Zoek-Eva.cmd</code></strong> in de Eva-map om het hele thuisnetwerk in 1 seconde te scannen.</li>
  </ol>
  <div class="actions">
    <a id="main-open-btn" class="btn" href="http://eva.local:8765/pair#$pairingCode" target="_blank" rel="noopener">Open Eva Setup &amp; Contact</a>
    <a id="main-dash-btn" class="btn sec" href="http://eva.local:8765/internal/v1/dashboard" target="_blank" rel="noopener">Open Eva Dashboard</a>
  </div>
  <p style="margin-bottom:.25rem">Eenmalige koppelcode voor deze HP Pro Mini:</p>
  <code id="pair-code-box">$pairingCode</code>
  <div class="fallback">
    <strong>Of verbind rechtstreeks op IP-adres:</strong>
    <div class="row">
      <input id="ip-in" placeholder="bijv. 192.168.1.45" autocomplete="off">
      <button id="open-ip-setup" class="btn" type="button">Setup via IP</button>
      <button id="open-ip-dash" class="btn sec" type="button">Dashboard via IP</button>
    </div>
  </div>
  <small>Let op: de installatie wist de primaire interne SSD van de HP Pro Mini 400 G9. Bewaar dit bestand op je laptop.</small>
</main>
<script>
(() => {
  const code = document.getElementById('pair-code-box').textContent.trim();
  const ipIn = document.getElementById('ip-in');
  const box = document.getElementById('radar-box');
  const title = document.getElementById('radar-title');
  const detail = document.getElementById('radar-detail');
  const bar = document.getElementById('radar-bar');
  const fill = document.getElementById('radar-fill');
  function cleanIp() {
    return ipIn.value.trim().replace(/^https?:\/\//i, '').replace(/\/.*$/, '');
  }
  async function checkHost(h) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 1600);
    try {
      const r = await fetch('http://' + h + ':8765/internal/v1/appliance/ping', {signal: ctrl.signal, cache: 'no-store'});
      if (!r.ok) return null;
      const d = await r.json();
      return d && d.eva_appliance ? {host: h, data: d} : null;
    } catch { return null; } finally { clearTimeout(t); }
  }
  async function pollRadar() {
    const manual = cleanIp();
    const targets = manual ? [manual, 'eva.local'] : ['eva.local'];
    for (const t of targets) {
      const hit = await checkHost(t);
      if (hit) {
        const d = hit.data;
        const base = 'http://' + hit.host + ':8765';
        document.getElementById('main-open-btn').href = base + '/pair#' + encodeURIComponent(code);
        document.getElementById('main-dash-btn').href = base + '/internal/v1/dashboard';
        if (d.phase === 'bootstrapping') {
          box.classList.remove('ok');
          title.textContent = 'Eva gevonden op ' + hit.host + ' — eerste opstart bezig!';
          detail.textContent = d.step || 'Onderdelen worden geïnstalleerd…';
          bar.style.display = 'block';
          fill.style.width = (d.percent || 15) + '%';
        } else {
          box.classList.add('ok');
          bar.style.display = 'none';
          title.textContent = 'Eva is online op ' + hit.host + '! Klik op de groene knop hieronder.';
          detail.textContent = (d.installation_name || 'Eva') + ' staat klaar om te koppelen.';
        }
        return;
      }
    }
  }
  setInterval(pollRadar, 2500);
  pollRadar();
  document.getElementById('open-ip-setup').onclick = () => {
    const ip = cleanIp();
    if (!ip) { ipIn.focus(); return; }
    window.open('http://' + ip + ':8765/pair#' + encodeURIComponent(code), '_blank', 'noopener');
  };
  document.getElementById('open-ip-dash').onclick = () => {
    const ip = cleanIp();
    if (!ip) { ipIn.focus(); return; }
    window.open('http://' + ip + ':8765/internal/v1/dashboard', '_blank', 'noopener');
  };
})();
</script>
</html>
"@
$setupHtmlPath = Join-Path $OutputDir "Open-Eva-Setup.html"
$rootSetupHtmlPath = Join-Path $repoRoot "Open-Eva-HP-Mini-Setup.html"
[System.IO.File]::WriteAllText($setupHtmlPath, $setupHtml, [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::WriteAllText($rootSetupHtmlPath, $setupHtml, [System.Text.UTF8Encoding]::new($false))

Write-Host ""
Write-Host "=== EVA HEADLESS SEED KLAAR ===" -ForegroundColor Green
Write-Host "Seed-map:       $seedDir"
Write-Host "Koppelcode:     $pairingCode"
Write-Host "1-klik pagina:  $rootSetupHtmlPath"
Write-Host ""

# 5. Optioneel: Ubuntu Server 24.04.5 ISO ophalen + SHA-256 controleren
$ubuntuIsoUrl = "https://releases.ubuntu.com/24.04/ubuntu-24.04.5-live-server-amd64.iso"
$expectedIsoSha256 = "97f3d7ffb032c3eb3b23d2c8be9cc76e60c2c1f2c0146ba5ba9fe01cafae0fd8"

if ($DownloadUbuntuIso -and -not $IsoPath) {
    $IsoPath = Join-Path $OutputDir "ubuntu-24.04.5-live-server-amd64.iso"
    $needDownload = $true
    if (Test-Path $IsoPath) {
        $existingHash = (Get-FileHash -Path $IsoPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($existingHash -eq $expectedIsoSha256) {
            Write-Host "Ubuntu 24.04.5 ISO is al aanwezig en SHA-256 geverifieerd." -ForegroundColor Green
            $needDownload = $false
        } else {
            Write-Host "Bestaande ISO heeft afwijkende SHA-256; opnieuw downloaden..." -ForegroundColor Yellow
            Remove-Item -Force $IsoPath
        }
    }
    if ($needDownload) {
        Write-Host "Ubuntu Server 24.04.5 LTS ISO downloaden..." -ForegroundColor Cyan
        $partPath = "$IsoPath.part"
        if (Test-Path $partPath) { Remove-Item -Force $partPath }
        & curl.exe -fL --retry 3 --retry-delay 5 -o $partPath $ubuntuIsoUrl
        if ($LASTEXITCODE -ne 0) { throw "Download van Ubuntu Server 24.04.5 ISO is mislukt (curl exit $LASTEXITCODE)." }
        Move-Item -Force $partPath $IsoPath
        $actualHash = (Get-FileHash -Path $IsoPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -ne $expectedIsoSha256) {
            throw "SHA-256 controle van Ubuntu ISO mislukt! Verwacht $expectedIsoSha256 maar kreeg $actualHash"
        }
        Write-Host "Ubuntu 24.04.5 ISO SHA-256 geverifieerd." -ForegroundColor Green
    }
}

$grubCfgText = @'
set timeout=5
set default=0
menuentry "Installeer Eva Appliance Headless (HP Pro Mini)" {
    set gfxpayload=keep
    linux   /casper/vmlinuz quiet autoinstall ds=nocloud\;s=/cdrom/eva-seed/ fsck.mode=skip ---
    initrd  /casper/initrd
}
'@.Replace("`r`n", "`n") + "`n"

# 6A. Direct een al als FAT32 geformatteerde USB-schijf (bijv. D:) vullen zonder admin-rechten
if ($TargetUsbDriveLetter) {
    $letter = $TargetUsbDriveLetter.Trim().TrimEnd(':').TrimEnd('\').ToUpperInvariant()
    if ($letter -notmatch '^[A-Z]$' -or $letter -eq 'C') {
        throw "Ongeldige doel-schijfletter voor USB-stick: '$TargetUsbDriveLetter'"
    }
    if (-not $IsoPath -or -not (Test-Path $IsoPath)) {
        throw "Geef -DownloadUbuntuIso of een geldig -IsoPath op om de USB-stick te bouwen."
    }

    $vol = Get-Volume -DriveLetter $letter -ErrorAction Stop
    if ($vol.DriveType -ne 'Removable') {
        throw "Schijf ${letter}: is geen verwijderbare schijf (DriveType=$($vol.DriveType))."
    }
    if ($vol.FileSystem -ne 'FAT32') {
        throw "Schijf ${letter}: moet FAT32 zijn (huidig: $($vol.FileSystem))."
    }
    if ($vol.SizeRemaining -lt 3500000000) {
        throw "Schijf ${letter}: heeft te weinig vrije ruimte ($($vol.SizeRemaining) bytes; minimaal 3.5 GB vereist)."
    }

    $disk = Get-Partition -DriveLetter $letter | Get-Disk
    if ($disk.BusType -ne 'USB' -or $disk.IsBoot -or $disk.IsSystem) {
        throw "Veiligheidscheck geweigerd op schijf ${letter}: (BusType=$($disk.BusType), IsBoot=$($disk.IsBoot), IsSystem=$($disk.IsSystem))."
    }

    $stageWin = Join-Path $OutputDir "_usb_stage"
    if (Test-Path $stageWin) { Remove-Item -Recurse -Force $stageWin }
    New-Item -ItemType Directory -Force -Path $stageWin | Out-Null

    $wslIso = (wsl -e wslpath -u ($IsoPath.Replace('\', '/'))).Trim()
    $wslStage = (wsl -e wslpath -u ($stageWin.Replace('\', '/'))).Trim()

    Write-Host "Ubuntu ISO uitpakken in WSL voor USB-schijf ${letter}:..." -ForegroundColor Cyan
    $extractScript = @'
set -euo pipefail
if ! command -v 7z >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y p7zip-full
fi
WORK=$(mktemp -d /var/tmp/eva-usb-extract-XXXXXX)
trap 'chmod -R u+w "$WORK" 2>/dev/null || true; rm -rf "$WORK"' EXIT
7z x -y -o"$WORK" "__WSL_ISO__" >/dev/null </dev/null
find "$WORK" -type l -delete
rm -rf "$WORK/[BOOT]"
chmod -R u+w "$WORK"
cp -r --no-preserve=mode,ownership,timestamps "$WORK"/. "__WSL_STAGE__/"
'@.Replace("__WSL_ISO__", $wslIso).Replace("__WSL_STAGE__", $wslStage).Replace("`r`n", "`n")
    $extractB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($extractScript))
    wsl -u root -e bash -lc "echo $extractB64 | base64 -d > /tmp/eva-usb-extract.sh && bash /tmp/eva-usb-extract.sh </dev/null"
    if ($LASTEXITCODE -ne 0) { throw "Uitpakken van Ubuntu ISO in WSL mislukt." }

    # Kopieer eva-seed/* en headless GRUB-configuratie naar de staging-map
    $stageSeed = Join-Path $stageWin "eva-seed"
    New-Item -ItemType Directory -Force -Path $stageSeed | Out-Null
    Copy-Item -Path (Join-Path $seedDir "*") -Destination $stageSeed -Recurse -Force

    $stageGrubDir = Join-Path $stageWin "boot\grub"
    New-Item -ItemType Directory -Force -Path $stageGrubDir | Out-Null
    foreach ($gf in @("grub.cfg", "loopback.cfg")) {
        $gp = Join-Path $stageGrubDir $gf
        if (Test-Path $gp) { Set-ItemProperty -Path $gp -Name IsReadOnly -Value $false }
    }
    [System.IO.File]::WriteAllText((Join-Path $stageGrubDir "grub.cfg"), $grubCfgText, [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::WriteAllText((Join-Path $stageGrubDir "loopback.cfg"), $grubCfgText, [System.Text.UTF8Encoding]::new($false))

    Write-Host "Bestanden kopieren naar USB-stick ${letter}:\ ..." -ForegroundColor Cyan
    & robocopy.exe "$stageWin" "${letter}:\" /E /NFL /NDL /NJH /NJS /NP /R:2 /W:2 | Out-Null
    $rc = $LASTEXITCODE
    if ($rc -ge 8) {
        throw "robocopy naar USB-stick ${letter}:\ gaf foutcode $rc."
    }

    if (Get-Command Write-VolumeCache -ErrorAction SilentlyContinue) {
        Write-VolumeCache -DriveLetter $letter -ErrorAction SilentlyContinue
    }
    Remove-Item -Recurse -Force $stageWin -ErrorAction SilentlyContinue
    Write-Host "USB-stick klaar op ${letter}:\! Open na installatie: $rootSetupHtmlPath" -ForegroundColor Green
    exit 0
}

# 6B. Alternatief: bouw één geautomatiseerde headless ISO via WSL (xorriso + p7zip-full)
$finalIso = Join-Path $OutputDir "eva2-hp-mini-autoinstall.iso"
if ($IsoPath -and (Test-Path $IsoPath)) {
    Write-Host "Headless autoinstall-ISO bouwen via WSL2..." -ForegroundColor Cyan
    $wslIso = (wsl -e wslpath -u ($IsoPath.Replace('\', '/'))).Trim()
    $wslSeed = (wsl -e wslpath -u ($seedDir.Replace('\', '/'))).Trim()
    $wslOut = (wsl -e wslpath -u ($finalIso.Replace('\', '/'))).Trim()

    $buildScript = @'
set -euo pipefail
for pkg in xorriso p7zip-full; do
  if ! dpkg -s "$pkg" >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y xorriso p7zip-full
    break
  fi
done
WORK=$(mktemp -d /var/tmp/eva-iso-XXXXXX)
trap 'chmod -R u+w "$WORK" 2>/dev/null || true; rm -rf "$WORK"' EXIT
7z x -y -o"$WORK/iso" "__WSL_ISO__" >/dev/null </dev/null
chmod -R u+w "$WORK/iso"
mkdir -p "$WORK/iso/eva-seed"
cp -a "__WSL_SEED__"/* "$WORK/iso/eva-seed/"
cat > "$WORK/iso/boot/grub/grub.cfg" << 'GRUBEOF'
set timeout=5
set default=0
menuentry "Installeer Eva Appliance Headless (HP Pro Mini)" {
    set gfxpayload=keep
    linux   /casper/vmlinuz quiet autoinstall ds=nocloud\;s=/cdrom/eva-seed/ fsck.mode=skip ---
    initrd  /casper/initrd
}
GRUBEOF
cp -f "$WORK/iso/boot/grub/grub.cfg" "$WORK/iso/boot/grub/loopback.cfg"
xorriso -as mkisofs -r -V "EVA2_APPLIANCE" -o "__WSL_OUT__" \
  --grub2-mbr "$WORK/iso/[BOOT]/1-Boot-NoEmul.img" \
  -partition_offset 16 --mbr-force-bootable \
  -append_partition 2 28732ac11ff8d211ba4b00a0c93ec93b "$WORK/iso/[BOOT]/2-Boot-NoEmul.img" \
  -appended_part_as_gpt -iso_mbr_part_type a2a0d0ebe5b9334487c068b6b72699c7 \
  -c '/boot.catalog' -b '/boot/grub/i386-pc/eltorito.img' \
  -no-emul-boot -boot-load-size 4 -boot-info-table --grub2-boot-info \
  -eltorito-alt-boot -e '--interval:appended_partition_2:::' -no-emul-boot \
  "$WORK/iso" 2>&1 | tail -n 5
'@.Replace("__WSL_ISO__", $wslIso).Replace("__WSL_SEED__", $wslSeed).Replace("__WSL_OUT__", $wslOut).Replace("`r`n", "`n")

    $b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($buildScript))
    wsl -u root -e bash -lc "echo $b64 | base64 -d > /tmp/eva-iso-build.sh && bash /tmp/eva-iso-build.sh </dev/null"
    Write-Host "Headless ISO gereed: $finalIso" -ForegroundColor Green

    if ($WriteUsbDrive) {
        $usbDisks = Get-Disk | Where-Object { $_.BusType -eq 'USB' -and -not $_.IsBoot -and -not $_.IsSystem }
        if (-not $usbDisks) {
            Write-Warning "Geen verwijderbare USB-schijf gevonden. Gebruik Rufus of balenaEtcher om $finalIso op een USB-stick te zetten."
            exit 0
        }
        $usbDisks | Select-Object Number, FriendlyName, @{N="SizeGB";E={[math]::Round($_.Size/1GB,1)}} | Format-Table -AutoSize
        $num = Read-Host "Typ het Disk-nummer van de USB-stick die GEWIST mag worden (leeg = annuleren)"
        if ($num -match '^\d+$') {
            $chosen = $usbDisks | Where-Object { $_.Number -eq [int]$num }
            if (-not $chosen) { throw "Ongeldig USB-schijfnummer." }
            $confirm = Read-Host "LAATSTE CHECK: Disk $($chosen.Number) ($($chosen.FriendlyName)) wordt volledig gewist. Typ EXACT 'WIS USB' om door te gaan"
            if ($confirm -eq "WIS USB") {
                Write-Host "Schijf $($chosen.Number) wordt voorbereid..." -ForegroundColor Yellow
                Clear-Disk -Number $chosen.Number -RemoveData -Confirm:$false
                $part = New-Partition -DiskNumber $chosen.Number -UseMaximumSize -AssignDriveLetter
                Format-Volume -Partition $part -FileSystem FAT32 -NewFileSystemLabel "EVA2_INSTALL" -Confirm:$false | Out-Null
                Mount-DiskImage -ImagePath $finalIso -PassThru | Out-Null
                $isoDrive = (Get-DiskImage -ImagePath $finalIso | Get-Volume).DriveLetter + ":\"
                $usbDrive = $part.DriveLetter + ":\"
                Write-Host "Bestanden kopieren van $isoDrive naar $usbDrive (UEFI bootable)..."
                Copy-Item -Path "$isoDrive*" -Destination $usbDrive -Recurse -Force
                Dismount-DiskImage -ImagePath $finalIso | Out-Null
                Write-Host "USB-stick klaar op $usbDrive! Open na installatie $rootSetupHtmlPath" -ForegroundColor Green
            }
        }
    }
} else {
    Write-Host "Geen Ubuntu Server 24.04 ISO opgegeven." -ForegroundColor Yellow
    Write-Host "Gebruik: .\build-eva-usb.ps1 -DownloadUbuntuIso -TargetUsbDriveLetter D"
}
