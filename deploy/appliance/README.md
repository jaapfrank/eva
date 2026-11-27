# Eva 1-Klik Karakter-, Contact- & Appliance Installer

Deze map bevat de complete out-of-the-box installatie- en contactomgeving voor Eva:
1. **Immersieve 3D "Eerste Ontwaking" & Levend Contact (`GET /` op `:8765`)**:
   - **De Eerste Hartslag:** als er nog geen AI-model gekoppeld is, verschijnt één rustig ademend scherm waarmee je Eva met 1 klik wekt via ChatGPT/Codex (of één van de 14 andere cloud-/lokale modellen), inclusief automatische achtergrondaansturing.
   - **Fullscreen 3D `eva.glb` Ontmoeting & Spraak:** Eva stapt lokaal (zonder externe CDN's) in 3D naar voren met oogcontact, ademhaling, lip-sync en gesproken Nederlandse stem, leert lokaal 16 kHz stemafdrukken herkennen (`BiometricPresenceFusion`) en laat je live 4 karakterbollen proeven die tegelijk wegschrijven naar ondertekende `SOUL.md` én `HouseholdHeadSettings`.
   - **Omkeerbare "Ik heb je gehoord"-kaarten & Visite-modus:** zeg of typ in gewoon Nederlands wensen of grenzen (*"Na 21:00 uur alleen storen als het dringend is"*, *"Drogere humor graag"*) met directe **`[↩ Ongedaan maken]`**-knop, of roep *"Eva, we krijgen visite!"* om direct het privéschild (`BiometricPresenceFusion`) en de feestelijke gastvrouw-modus te activeren.
   - **Persoonlijke 1-Scan Welkomstkaart voor Lola & Gasten (`GET /welkom/{token}`):** genereer een eigen QR-code voor Lola (`gezin`) of bezoek (`gast`). Op haar eigen telefoon begroet 3D Eva haar bij naam, leert haar eigen wensen & stemgeluid kennen en opent direct haar eigen `Eva Pocket` — strikt gescheiden van het eigenaarsbeheer.
2. **Volledige 6-Tab Systeemconfigurator onder `GET /beheer` ("⚙ Motorkap")**:
   - Voor geavanceerd beheer: Telegram `@BotFather` 1-scan koppeling zonder gebruikers-ID's (`eva2-local-telegram.service`), Home Assistant / AgentMail modules, maandplafond en geheimvrije `.json`-profielback-ups.
3. **Geen "Black Box" bij Headless Installatie:**
   - **Vroege Bootstrap Status-Server op poort `8765`:** al 5 seconden na eerste opstart zie je een live Nederlandstalige voortgangsbalk terwijl Ubuntu, Docker en Python 3.12 op de achtergrond klaarzetten, waarna het scherm vanzelf doorschakelt naar de wizard.
   - **`Zoek-Eva.cmd` Netwerk-Radar:** scant in ~1 seconde je hele `/24`-thuisnetwerk op poort `8765` en opent Eva automatisch op IP, ook als Windows of je router `eva.local` niet oplost.

---

## A. Direct openen op deze Windows-laptop (WSL2)
- Dubbelklik op [`Start-Eva-Installer.cmd`](../../Start-Eva-Installer.cmd) om de lokale wizard direct gekoppeld te openen.
- Dubbelklik op [`Zoek-Eva.cmd`](../../Zoek-Eva.cmd) om een Eva-mini-pc op je thuisnetwerk automatisch te vinden.

---

## B. Headless USB / Hybride ISO voor de HP Pro Mini 400 G9

### Aanbevolen op je eigen laptop (met Administrator-rechten / Rufus)
Genereer de hybride UEFI+GPT autoinstall-ISO + bijbehorende `Open-Eva-HP-Mini-Setup.html`:
```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\appliance\build-eva-usb.ps1 -DownloadUbuntuIso
```
Schrijf vervolgens `deploy\appliance\eva-usb-output\eva2-hp-mini-autoinstall.iso` met **Rufus** (of `balenaEtcher`, of `-WriteUsbDrive` in een Administrator-PowerShell) naar je USB-stick. Een hybride GPT/UEFI ISO via Rufus start op HP Pro Mini G9-systemen met Secure Boot betrouwbaarder op dan een losse MBR-bestandskopie.

> **Belangrijk bij een HP Pro Mini waar nog een oud besturingssysteem op de SSD staat:** HP zet `Windows Boot Manager` standaard vóór USB in het UEFI-opstartmenu. Staat er nog een oud OS op de interne NVMe-SSD, kies dan éénmalig bij aanzetten via `F9` de USB-stick (of wis de SSD vooraf). Je hoort **twee korte piepjes** zodra de autoinstall écht vanaf USB is gestart, en **drie piepjes + uitschakelen** zodra Ubuntu klaar is en de USB eruit mag.

---

## C. 1-Commando Installatie op elke verse Ubuntu Server 24.04 / Raspberry Pi
Heb je Ubuntu Server 24.04 standaard (of via SSH) op de HP Pro Mini of Raspberry Pi gezet? Eén commando richt alles in inclusief live voortgang op `http://<ip>:8765`:
```bash
curl -fsSL https://raw.githubusercontent.com/jaapfrank/eva/main/deploy/appliance/firstboot-install-eva.sh | sudo bash
```
