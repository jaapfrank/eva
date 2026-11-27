# Eva Hoofdscherm voor Ubiquiti UniFi Connect Display (`UC-Display-SE-21`)

Dit pakket ontgrendelt jouw **UniFi Connect Display (`UC-Display-SE-21` / `Keuken display`)** als het centrale **Gezins-Hoofdscherm** van Eva voor jou en Lola — met werkende **ingebouwde microfoon, speakers, hardware echo-onderdrukking (AEC), 3D lip-sync, lokale stemherkenning én live camera-meekijken**.

---

## Waarom standaard browsers of gewone apps vastlopen op een UniFi Connect Display

Op een `UC-Display` / `UC-Display-SE-21` (Android 9.0 `arm64-v8a`) gelden drie strikte blokkades van Ubiquiti en Chromium:

1. **UniFi Connect *Website Mode* blokkeert de microfoon:** De standaard ingebouwde Website-modus van UniFi Connect geeft `getUserMedia` geen toegang tot de microfoon.
2. **Strikte APK-permissiefilter in *Android App Mode*:** Ubiquiti blokkeert sideloaded APK's zodra die verboden Android-groepen aanvragen (`CAMERA`, `STORAGE`, `CONTACTS`, `PHONE`, `SMS`, `SENSORS`) of *Google Mobile Services (GMS)* gebruiken. **De `RECORD_AUDIO` (Microfoon)-permissie staat in *Android App Mode* echter officieel WEL open!**
3. **Geen ingebouwde vrije Android-camera + HTTPS-eis op LAN:** De 21.5" `UC-Display` heeft geen vrij toegankelijke Android-camera in de Connect-sandbox, én Android WebView schakelt `navigator.mediaDevices` volledig uit op onversleutelde `http://10.0.20.x`-adressen.

---

## Hoe Eva dit nu 100% oplost

1. **Speciaal gebouwde `eva-unifi-connect-display.apk` (0 GMS, 0 verboden permissies):**
   - Vraagt uitsluitend de door UniFi Connect toegestane rechten aan (`INTERNET`, `RECORD_AUDIO`, `MODIFY_AUDIO_SETTINGS`, `WAKE_LOCK`).
   - Zet `AudioManager.MODE_IN_COMMUNICATION` (hardware echo-onderdrukking van het scherm) automatisch aan.
   - Keurt WebRTC-microfoontoegang (`gpt-realtime-2.1`, `gpt-realtime-2.1-mini` en `gemini-3.8-live`) zonder pop-up goed via `PermissionRequest.grant()`.
   - Vertrouwt Eva's lokale **HTTPS-poort (`:8766`)** op je eigen thuisnetwerk (`10.x.x.x` / `192.168.x.x`), terwijl publiek internet strikt beveiligd blijft.
   - **5× tikken linksboven** op het keukenscherm opent op elk moment het instelvenster om het IP-adres aan te passen (handig wanneer Eva maandag op je nieuwe laptop/server draait!).

2. **Camera ("Eva's Ogen" bij het Keukenscherm) & Scherm Wekken:**
   - In Eva's 3D-scherm klik je bovenaan op **`🖥️ Hoofdscherm`**. Daar koppel je met één klik een **Home Assistant / UniFi Protect camera** (bijv. een compacte UniFi G4 Instant / G5 Flex in de keuken, of je G4 Doorbell Pro / G5 camera's).
   - Zowel via de **`📷`-knop** op het keukenscherm als midden in een live gesprek (*"Eva, kijk eens op de camera!"* of *"Wie staat er bij de voordeur?"*) pakt Eva live een HD-snapshot en vertelt ze direct wat of wie ze ziet.
   - Via Home Assistant (`notify.keuken_scherm` / `ha-unifi-connect`) kan Eva het keukenscherm bovendien automatisch wekken bij beweging in de keuken.

---

## Installatie in 3 korte stappen

1. **Bouw de APK met 1 klik:**
   Dubbelklik in de hoofdmap van Eva op **`Bouw-Eva-UniFi-Display-APK.cmd`** (of draai `./build-apk.sh` in WSL2). Dit levert `deploy/unifi-display-apk/eva-unifi-connect-display.apk` op.
2. **Upload in UniFi Connect:**
   Open **UniFi Connect** op je Cloud Key / UniFi Console → klik op **Keuken Display (`UC-Display-SE-21`)** → kies **Display Mode: Android App** → **Upload APK** en selecteer `eva-unifi-connect-display.apk`.
3. **Koppel éénmalig met 365-dagen sessie:**
   Klik op je pc in Eva (`http://127.0.0.1:8765/`) bovenaan op **`🖥️ Hoofdscherm`** → **`Bewaar & Maak 1-Jaar Hoofdscherm-URL`** en vul die `https://<ip>:8766/pair?kiosk=1#EVA-DISP-...` URL éénmalig in op het keukenscherm.
