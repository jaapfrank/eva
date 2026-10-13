# Hé Eva — korte lokale opnameproef

Open **http://127.0.0.1:55119** op deze Windows-laptop in Chrome of Edge.
Werkt de link niet? Dubbelklik op:
`E:\Projecten\wakeword\collector\Start-opnameproef.cmd`.
Node.js is al op deze computer aanwezig; geen installatie of abonnement nodig.

## Voor Jaap

1. Zet de laptop op een vaste plek en kies **Begin de proef**. Sta de microfoon
   toe wanneer de browser erom vraagt.
2. Neem één getoonde zin op. Wacht op **Spreek nu**; de opname stopt vanzelf
   na maximaal zes seconden. Spreek gewoon, niet extra hard.
3. Alleen de getoonde zin goed uitgesproken? Kies **Goed uitgesproken — bewaar
   en verder**. Bij verspreken of iemand anders op de achtergrond: **Opnieuw**.
   Terugluisteren kan met de afspeelknop, maar is niet verplicht.
4. Halverwege ga je van dichtbij naar je normale praatplek. Vul de geschatte
   afstand tot de laptopmicrofoon in. De laptop blijft staan.
5. Na dertig zinnen is de proef klaar. Zeg tegen de coördinatietaak:
   **Mijn wakewordproef is klaar.** Er hoeft niets verstuurd te worden.

Pauzeren kan altijd. De microfoon gaat uit; bevestigde opnames blijven bewaard.
Kies later **Ga verder met mijn proef**. Een herstart van de lokale server
verwijdert de opnames niet. Blijf dezelfde browser en dezelfde lokale URL gebruiken.

Het zijn tien bedoelde oproepen en twintig gewone niet-oproepen, verdeeld over
beide posities. Geen privégesprekken opnemen. De pagina neemt nooit vanzelf een
volgende zin op. Alle audio blijft lokaal in
`E:\Projecten\wakeword\recordings\<sessie-id>` als WAV plus `manifest.json`.
Deze sessie blijft apart voor beoordeling en wordt niet automatisch trainingsdata.

## Wat dit bewijst en wat nog ontbreekt

De browserrecorder is technisch getest met een kunstmatige microfoon, inclusief
stoppen, bewaren, pauzeren, hervatten en de volledige dertig-zinnenroute.
De echte Jaapproef en betere wakewordherkenning zijn hiermee niet bewezen.
De laptopmicrofoon is niet de Voice PE-microfoon. Er is geen nieuwe LAN- of
telefoontoegang geopend; de mobiele layouttest bewijst alleen schermindeling.

Vergelijk dezelfde bestanden en hashes met het oorspronkelijke model en een
vooraf bevroren kandidaat. Kies geen drempel op deze aparte proefset. De korte
proef is een selectietest; zeldzame dagelijkse valse activaties, echte speaker-
echo en hardware-installatie hebben later eigen bewijs nodig.

Voor installatie ontbreken nog de originele apparaat-YAML/modelbron en een
geverifieerde werkende firmware-/USB-herstelroute. HA-beheertoegang is inmiddels
beschikbaar: de op12september gelezen ESPHome-bouwer bevat geen configuraties,
en /config/esphome bevat alleen beheerbestanden en secrets.yaml. Niets is gewijzigd.

## Lokale objectieve vergelijking

Met de bestaande WSL-trainingsomgeving, zonder nieuwe packages, upload of playback:

    /home/administrator/wakeword/.venv/bin/python /home/administrator/project/Eva2/src/tools/wakeword_capture/compare_session.py --session /mnt/e/Projecten/wakeword/recordings/SESSIE-ID --candidate /pad/naar/bevroren-kandidaat.json --output /pad/naar/nieuwe-vergelijking.json

De baseline is standaard het oorspronkelijke `hey_eva.json`. Het programma
controleert labels, WAV-formaat en hashes, rapporteert gemiste positieve oproepen
per positie en geactiveerde negatieve clips. De poort blijft: minstens50% minder
valse clips, minstens90% oproepherkenning en hoogstens5procentpunt recallverlies
per positie. Bij nul fouten in de baseline is een halvering niet aantoonbaar.
Onvolledige sessies slagen nooit voor de poort. Een geslaagde selectietest geeft
nooit automatisch toestemming voor installatie.

De huidige afgeleverde server draait als beheerde Node-processessie. Er is geen
Windowsdienst, taakplanner of automatische start toegevoegd. Als de processessie
of computer stopt, start de dubbelklikhelper hem opnieuw en opent de browser.
Sluit bij handmatig starten het commandovenster om die server te stoppen.

Ontwikkelcontroles: `node --test server.test.mjs`; de twee browser-QA-scripts
gebruiken de bestaande gebundelde Playwright en uitsluitend een kunstmicrofoon.

Hergebruik: bestaande Eva-browseraudioaanpak uit `dashboard_voice_client.py`
(AudioWorklet, PCM16, expliciete mediatrack-lifecycle), zonder wijziging aan de
voicekern. Node-standaardbibliotheek voor lokale opslag; bestaande microWakeWord
voor evaluatie. Geen bestaande gelabelde collector aangetroffen.

Bronnen: [getUserMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia)
en [AudioContext-sampleRate](https://developer.mozilla.org/en-US/docs/Web/API/AudioContext/AudioContext).
