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
| Model | OpenAI via OAuth, met optionele API-keyfallback |
| Kanalen | dashboard, Telegram en Home Assistant Assist gebruiken dezelfde agentroute |
| Huis | actuele HA-status lezen en toegestane lampen, schakelaars, media, klimaat en covers bedienen; sloten en alarmen zijn uitgesloten |
| Voice PE | antwoorden en gerichte omroepen via expliciet toegestane Assist Satellites |
| Robot | Valetudo-status, kaart, camera en begrensde beweging via één robotintegratie |
| Camera en gezichten | deurbel/tuin/straat beoordelen, bruikbare gezichten leren en foto's via Telegram bezorgen |
| Geheugen | lokale SQLite-opslag, gespreksarchief en full-text retrieval |
| Gezin | expliciete bewonersrollen, aanwezigheid en compacte relationele feedback |
| Autonomie | bij `full` iedere 15 minuten aandacht en minimaal 120 minuten zelfstandig dagbudget; Eva kiest binnen dat budget zelf doelen |
| Duurzaam werk | doelen, episodes, checkpoints, leases en bewijsgebonden afronding |
| Werkruimte | bestanden maken/lezen en programma's geïsoleerd uitvoeren; de werkruimte zelf is runtime-output, geen tweede bronrepository |
| Capabilities | skills/add-ons kunnen worden gestaged, getest en checksumgebonden geactiveerd; npm-pakketten worden nog niet automatisch first-class tools |
| Parallel onderzoek | begrensde webonderzoekstromen; dit is nog geen algemene subagent-runtime |
| Herinneringen | plannen, tonen en annuleren met bezorging via de actieve kanalen |
| Dashboard | chat, status, tijdlijn, geheugen en 3D-avatar |

De gewenste Entity/Turn/Capability/Learning/Worker/Attention/Delivery-contracten
staan in de runtimekaart met hun echte status. Alleen Attention is grotendeels
aanwezig; de overige contracten zijn gedeeltelijk of gepland en worden hier dus
niet als afgeronde features verkocht.

## Starten

```bash
cd ~/project/Bottie
npm install        # eenmalig
npm start
```

Open dan **`http://localhost:3210`**. Netwerktoegang staat standaard uit. Stel voor
toegang vanaf een ander apparaat zowel `WEB_HOST` als een sterk `EVA_WEB_TOKEN` in.

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
| `EVA_WEB_TOKEN` | verplicht wanneer `WEB_HOST` niet alleen lokaal bindt |

## Altijd aan laten staan (systemd)

```bash
sudo install -m 0644 deploy/eva.service /etc/systemd/system/eva.service
sudo systemctl daemon-reload
sudo systemctl enable --now eva
```

Systemd is de enige ondersteunde deploymentroute. De eerdere halve Dockerroute
is verwijderd: die miste de Python-brain/GPU, de user-systemd-sandbox voor
werkruimteprogramma's en live-pariteit met deze installatie.

De lokale beeldbrain heeft een eigen user-unit en dependency-lock:

```bash
python3 -m venv data/runtime/eva-brain-venv
data/runtime/eva-brain-venv/bin/python -m pip install -r eva_brain/requirements.lock
install -m 0644 deploy/eva-brain.service ~/.config/systemd/user/eva-brein.service
systemctl --user daemon-reload
systemctl --user enable --now eva-brein
```

## Goed om te weten

- **Privacy:** duurzame gegevens staan lokaal in `data/eva.db`. Voor een modelbeurt kunnen het gesprek, relevante herinneringen, bewonerscontext, actuele HA-toestand en werkcontext naar de ingestelde modelprovider gaan.
- De OAuth-route gebruikt je ChatGPT-abonnement zoals OpenClaw/Codex dat doet; zet eventueel `OPENAI_API_KEY` als reserve.
- Eva bedient **nooit** sloten of alarmen en praat 's nachts niet door speakers.
- Verwijder `data/` en ze begint met een schone lei (zonde — ze kende jullie net).
