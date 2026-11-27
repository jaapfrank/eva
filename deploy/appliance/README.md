# Eva 1-Klik Karakter-, Contact- & Appliance Installer

Deze map bevat de complete out-of-the-box installatie-, kluis- en 3D-contactomgeving voor Eva:
1. **Immersieve 3D "Eerste Ontwaking" & 6-Staps Kennismakings-Wizard (`GET /` op `:8765`)**:
   - **Automatische Lokale Koppeling:** open je `http://127.0.0.1:8765/pair` rechtstreeks op de laptop zelf, dan herkent Eva de lokale pc automatisch zonder dat je een koppelcode hoeft over te typen (apparaten op wifi gebruiken wel de veilige `EVA-XXXX`-koppelcode of QR-scan).
   - **6-Staps Begeleide Wizard + Fullscreen 3D `eva.glb`:** loop stap voor stap door **1. AI-Brein** (1-klik ChatGPT/Codex of 14 andere modellen), **2. Jij & Gezin** (`Jaap` & `Lola`), **3. Lokale 64-D Stemherkenning** (met live stemtest!), **4. Karakter & Huisafspraken** (met directe `[↩ Ongedaan maken]`-knop), **5. Koppelingen, AES-256 Kluis & Lola QR** (Home Assistant, UniFi Netwerk + UniFi Protect in 1 klik, Agenda, E-mail en eigen API's) en **6. Klaar voor Levend Contact**.
   - **Zelfbouwende Vaardigheden (`build_and_run_capability`):** vraag je Eva iets waar nog geen standaardkoppeling voor bestaat, dan zoekt ze zelf documentatie op en schrijft, controleert (AST + sandbox) en bewaart ze een nieuwe Python-vaardigheid.
   - **Persoonlijke 1-Scan Welkomstkaart voor Lola & Gasten (`GET /welkom/{token}`):** genereer een eigen QR-code voor Lola (`gezin`) of bezoek (`gast`). Op haar telefoon begroet 3D Eva haar bij naam, leert haar stem & wensen kennen en opent haar eigen `Eva Pocket`.
2. **Volledige 6-Tab Systeemconfigurator onder `GET /beheer` ("⚙ Motorkap")**:
   - Voor geavanceerd beheer: Telegram `@BotFather` 1-scan koppeling zonder gebruikers-ID's, maandplafond en geheimvrije `.json`-profielexport/-import.

---

## A. Maandag: Installeren & Onboarden op je Nieuwe Laptop

### Optie 1 — Nieuwe laptop draait Windows 11 (aanbevolen 1-klik)
1. Zet deze `eva`-map op je nieuwe laptop (via `git clone -b feat/immersive-eva-onboarding https://github.com/jaapfrank/eva.git` of door de map te kopiëren).
2. Dubbelklik op **[`Installeer-Eva-Op-Nieuwe-Laptop.cmd`](../../Installeer-Eva-Op-Nieuwe-Laptop.cmd)**.
   - Staat WSL2 + Ubuntu 24.04 nog niet aan op de nieuwe laptop? Dan zet het script dit automatisch aan (herstart eenmalig als Windows daarom vraagt en dubbelklik nog één keer).
   - Op Windows 11 activeert het script automatisch `networkingMode=mirrored` zodat Lola's welkomst-QR en Eva Pocket direct vanaf je telefoon op jullie thuis-wifi werken.
   - Zodra de installatie klaar is, springt je browser vanzelf open in de **3D Kennismakings-Wizard** (`http://127.0.0.1:8765/pair` → `/`)!
3. Dagelijks openen na installatie: dubbelklik op **[`Start-Eva-Installer.cmd`](../../Start-Eva-Installer.cmd)**.

### Optie 2 — Nieuwe laptop of mini-pc draait rechtstreeks Ubuntu 24.04 LTS
Voer in de terminal één commando uit:
```bash
curl -fsSL https://raw.githubusercontent.com/jaapfrank/eva/feat/immersive-eva-onboarding/deploy/appliance/firstboot-install-eva.sh | sudo bash
```
Open daarna `http://127.0.0.1:8765/pair` in je browser.

### Optioneel: Je huidige karakter & gezinsinstellingen meenemen (`.json`)
- Open op deze laptop `http://127.0.0.1:8765/beheer` → tabblad **5. Activeren** → klik op **Exporteer profiel (.json)** en klik op de nieuwe laptop op **Importeer profiel (.json)** (of doorloop op maandag lekker vers de 6-staps 3D Wizard!).

---

## B. Headless USB / Hybride ISO voor een losse HP Pro Mini 400 G9
Genereer de hybride UEFI+GPT autoinstall-ISO (let op: deze optie wist de doel-pc volledig voor gebruik als dedicated mini-server):
```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\appliance\build-eva-usb.ps1 -DownloadUbuntuIso
```
Schrijf `deploy\appliance\eva-usb-output\eva2-hp-mini-autoinstall.iso` met **Rufus** naar een USB-stick en gebruik **[`Zoek-Eva.cmd`](../../Zoek-Eva.cmd)** om het kastje op je thuisnetwerk automatisch te vinden.
