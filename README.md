# Eva 💚 — de huisbot die hier woont

Eva is een digitale huisentiteit: ze woont functioneel in het huishouden, kent haar
bewoners, helpt mee en legt verantwoording af over wat ze werkelijk heeft gedaan.
Ze doet zich niet voor als mens en claimt geen menselijke gevoelens. Haar interne
stemming is een regulatietoestand die toon, initiatief en terughoudendheid stuurt.
Via **Home Assistant** neemt ze de woning waar en kan ze, binnen expliciete grenzen,
via de aanwezige apparaten bijdragen.

Het bindende ontwerpcontract staat in
[`docs/house-entity-v1.md`](docs/house-entity-v1.md). De technische route staat in
[`docs/eva-runtime-map.md`](docs/eva-runtime-map.md).

## Wat zit erin

| Onderdeel | Wat het doet |
| --- | --- |
| 🧠 Brein | ChatGPT via OAuth-login met je Plus/Pro-abonnement (zelfde flow als OpenClaw/Codex), met optionele API-key-fallback |
| 💗 Regulatietoestand | energie, nieuwsgierigheid, verbondenheid en spanning sturen toon en initiatief zonder menselijke gevoelens te veinzen |
| 🧠 Geheugen | SQLite + volletekst-zoeken; 's nachts destilleert ze de dag tot blijvende herinneringen |
| 🌙 Slaap & dromen | slaapt 's nachts (instelbaar), verwerkt de dag en droomt — leesbaar op het dashboard |
| 💓 Eigen leven | elke ~12 min een "hartslag": ze denkt na, schrijft dagboek, en onderneemt soms zelf iets |
| 👂 Opmerkzaam | let uit zichzelf op wat opvalt (deur lang open, lampen aan terwijl niemand thuis is, open raam bij kou, rook/CO/lekkage) en beslist zelf of ze reageert |
| 🔁 Ritme | eigen dagmomenten (goedemorgen, avond-afronding, weekterugblik) en leert wanneer wie meestal thuiskomt |
| 🎉 Erbij horen | viert verjaardagen, feestdagen en haar eigen "kom-in-huis-dag"; mist mensen die ze een tijd niet sprak |
| 🌊 Bewustzijnsstroom | continue innerlijke lus (nooit volledig "weg"), één verenigde geest, echte driften die teruglopen en onderhouden moeten worden, een altijd-aanwezig werkgeheugen |
| 🧠 Associatief geheugen | herinnert op betekenis (lokale embeddings, geen key) i.p.v. alleen trefwoorden |
| 🛠️ Zelf-uitbreiding | kan zelfstandig onderzoeken, werkstukken bouwen en programma's in haar geïsoleerde werkruimte uitvoeren |
| 📶 Wifi (optioneel) | met een UniFi-key: telefoons op/van de wifi + onbekend-apparaat-meldingen (klaar, slaapt tot er een key is) |
| 🏠 Huis | wie is thuis, lampen, muziek, klimaat; bedienen kan (sloten/alarmen nooit) |
| 🔧 Beheer | volledige HA-admin: automatiseringen/scripts maken, zones, personen, entiteiten, integraties (verwijderen van integraties/gebruikers is hard geblokkeerd) |
| 👀 Ogen | de 3 camera's (deurbel, straat, tuin) bekijken en beschrijven; foto's doorsturen via Telegram |
| 🙋 Gezichten | leert mensen herkennen via de camera; is uit zichzelf nieuwsgierig naar wie ze niet kent en vraagt om namen |
| 🔔 Deur | merkt aanbellen/beweging op, kijkt zelf wie er is, kan bezoekers via de deurbel te woord staan |
| 🔊 Stem | praten door één speaker of overal (Sonos/Chromecast), via de deurbel, of als gesproken bericht via Telegram (niet 's nachts) |
| 🧍 Lichaam | haar 3D-model op het dashboard kleurt/kleedt ze zelf (thema, kleur, aura, accessoires) en ze reageert fysiek (juichen, dansen, zwaaien) — aura kleurt mee met haar bui |
| ⚽ Meeleven | volgt Oranje, trekt het oranje aan en juicht mee (confetti + lampen knipperen + door de speakers) als Nederland scoort |
| 🎙️ Voice-brug | `POST /api/voice` zodat je via Home Assistant Assist tegen Eva kunt praten en haar antwoord hardop terugkomt |
| ⏰ Herinneringen | "herinner me morgen om 8 uur aan…" — via Telegram én hardop in huis |
| 📱 Telegram | chatten vanaf je telefoon; Eva stuurt ook uit zichzelf wel eens iets (met daglimiet) |
| 🖥 Dashboard | live stemming, wat ze nu doet, tijdlijn, dagboek, dromen, geheugen, chat en haar 3D-model |
| 🧭 Vermogenskaart | één zelfbeeld van wat ze kan/heeft en wat actief, beperkt of uit is |
| 🌱 Vermogensgroei | merkt ontbrekende vermogens op, houdt ze duurzaam bij, en kan ze als eigen initiatief bouwen/testen/afronden |
| 🪞 Gespreksreflectie | verwerkt gesprekken achteraf tot lessen, follow-ups, fouten en vermogensgaten |
| 🔎 Ervaringsreflectie | zoekt patronen in haar eigen acties en maakt herhaalde blokkades automatisch tot fouten/vermogensgaten |
| 🧯 Foutenboek | bewaart missers met oorzaak, effect en volgende-keer-regel zodat ze niet blijft herhalen |
| 😏 Humorgevoel | leert per persoon welke humor past, wat niet goed valt, en wanneer ze juist serieus moet blijven |
| 🔁 Gezinsrituelen | herkent kleine terugkerende gewoontes, stelt ze voorzichtig voor en voert actieve rituelen op het juiste moment uit |
| 🧭 Reactiekompas | leert van hoe haar acties bij het gezin landen en remt spontane berichten af als die te veel worden |
| 👂 Toonkompas | hoort menselijke toon zoals haast, frustratie, kwetsbaarheid, warmte of speelsheid en stemt haar reactie daarop af |
| 🗣️ Communicatieprofielen | leert per persoon of diegene kort, uitgebreid, warm, zakelijk, speels, met/zonder emoji en met meer/minder initiatief aangesproken wil worden |
| 📌 Opvolgkompas | bewaart beloften en terugkompunten, voedt ze terug in haar hartslag en rondt ze later actief af |

## Starten

```bash
cd ~/project/Bottie
npm install        # eenmalig
npm start
```

Open dan **`http://localhost:3210`**. Netwerktoegang staat standaard uit. Stel voor
toegang vanaf een ander apparaat zowel `WEB_HOST` als een sterk `WEB_TOKEN` in.

### 1. Eva's brein inloggen (eenmalig)

Klik op het dashboard op **"Inloggen bij ChatGPT"** (of draai `npm run login`),
log in met je ChatGPT Plus/Pro-account en klaar. De tokens staan daarna in
`data/auth.json` en worden automatisch ververst.

> De loginpagina stuurt je terug naar `http://localhost:1455` — doe het inloggen
> dus in een browser op deze machine (of met poort-forwarding naar de server).

### 2. Telegram koppelen (aangeraden)

1. Stuur in Telegram een berichtje naar **@BotFather** → `/newbot` → kies een naam (bv. "Eva") en een username.
2. Plak het token in `.env` bij `TELEGRAM_BOT_TOKEN=` en herstart.
3. Stuur je nieuwe bot `/start` en stel je voor — Eva onthoudt wie je bent.
4. Tip: zet daarna `TELEGRAM_ALLOWED_IDS=` met jullie user-id's, zodat vreemden niet met haar kunnen praten.

### 3. Home Assistant

Staat al ingesteld in `.env` (`HA_URL` + `HA_TOKEN`). Eva verbindt via WebSocket,
voelt wie thuiskomt, en kan lampen/muziek bedienen als `ALLOW_HOME_CONTROL=true`.

## Instellen (`.env`)

| Sleutel | Betekenis |
| --- | --- |
| `SLEEP_START` / `WAKE_TIME` | Eva's bedtijd en wektijd (standaard 23:30–07:30) |
| `HEARTBEAT_MINUTES` | hoe vaak ze een momentje voor zichzelf neemt |
| `EVA_AUTONOMY_LEVEL` | `full` geeft Eva zelfstandige doelkeuze en toegang tot alle normale gereedschappen |
| `EVA_AUTONOMY_INTERVAL_MINUTES` | basiscadans voor zelfstandige aandacht; bij `full` standaard 15 minuten |
| `EVA_AUTONOMY_CONVERSATION_PAUSE_MINUTES` | korte voorrang voor een gesprek voordat zelfstandig werk wordt hervat |
| `EVA_MODEL` | standaard `gpt-5.6-sol` (de OAuth-route accepteert alleen modellen die jouw ChatGPT-abonnement aanbiedt) |
| `EVA_MODEL_VOICE` | standaard `gpt-5.6-terra`, met `EVA_MODEL` als expliciete fallback |
| `EVA_HOUSEHOLD_MEMBERS` | komma-gescheiden namen van expliciet erkende bewoners |
| `EVA_TRUST_HA_PERSON_MEMBERSHIP` | neem HA-personen als bewoners over; standaard `false` |
| `EVA_AUTONOMY_OPEN_EXPLORATION` | losse schakel voor open onderzoek wanneer `EVA_AUTONOMY_LEVEL` niet `full` is |
| `EVA_AUTONOMY_CODE_EXECUTION` | losse schakel voor werkruimtecode wanneer `EVA_AUTONOMY_LEVEL` niet `full` is |
| `EVA_BRAIN_URL` | zware lokale/remote breinservice voor gezichten en beeld-encoder; standaard `http://127.0.0.1:3220`, op een 24/7 mini-pc kan dit naar de RTX-machine wijzen |
| `ALLOW_HOME_CONTROL` | `false` = alleen kijken/voelen, niets bedienen |
| `WEB_HOST` | dashboardbinding; standaard `127.0.0.1` |
| `WEB_TOKEN` | verplicht wanneer `WEB_HOST` niet alleen lokaal bindt |

## Altijd aan laten staan (systemd)

```bash
sudo install -m 0644 deploy/eva.service /etc/systemd/system/eva.service
sudo systemctl daemon-reload
sudo systemctl enable --now eva
```

## Goed om te weten

- **Privacy:** duurzame gegevens staan lokaal in `data/eva.db`. Voor een modelbeurt kunnen het gesprek, relevante herinneringen, bewonerscontext, actuele HA-toestand en werkcontext naar de ingestelde modelprovider gaan.
- De OAuth-route gebruikt je ChatGPT-abonnement zoals OpenClaw/Codex dat doet; zet eventueel `OPENAI_API_KEY` als reserve.
- Eva bedient **nooit** sloten of alarmen en praat 's nachts niet door speakers.
- Verwijder `data/` en ze begint met een schone lei (zonde — ze kende jullie net).
