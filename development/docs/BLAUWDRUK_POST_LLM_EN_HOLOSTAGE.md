# Eva2 — Volledige Architectuur- en Uitvoeringsblauwdruk (Post-LLM Kern, Gezinshoofd-Regie & HoloStage)

**Vastgelegd op:** 13 november 2026  
**Doel van dit document:** Crash-bestendige technische overdracht in Git. Mocht een ontwikkelsessie of laptop ooit onderbroken worden, dan bevat dit document exact wat al is uitgezocht, schoongemaakt en live gezet, én de volledige blauwdruk voor Fase 1 t/m Fase 5.

---

## 1. Kernbesluiten & Gezinssamenstelling (Eigenaarafspraken Jaap, 13 november 2026)

1. **Het gezin bestaat uitsluitend uit Jaap en zijn dochter Lola:**
   - **Jaap Frank:** Gezinshoofd / eigenaar (`telegram:6034485735`).
   - **Lola Frank:** Dochter / gezinslid (`telegram:7803668978`).
   - Andere namen uit historische `eva1-import`-data (*Mark, Jessy/Jessie, oma Marjan, Sanne, buren Frank & Sylvia*) horen **niet** bij het kerngezin, maar kwamen uit Eva1's eerdere camerawaarnemingen (bekenden, familie of buren). Dit is in Hindsight (`eva2-managed-b-slot3-hindsight-postgres-1`) rechtgezet.
2. **Eerst Core & Gezinshoofd-regie 100% af (Fase 1 → 2 → 3), daarna `Eva HoloStage` & ~250ms spraak (Fase 4), daarna 7 dagen praktijkacceptatie (Fase 5):**
   - Geen visuele buitenkant bouwen bovenop een halve kern: eerst moeten Eva's continue `dx/dt` bewustzijnskern, de 15+ LLM-providers, het gesloten Gezinshoofd-beheer (mensen toevoegen/intrekken + Eva makkelijk zelf aanpassen), netwerk-/huis-proprioceptie en onfeilbare taakaflevering 100% kloppen.
3. **Telegram is 100% privé (`default-deny`):**
   - Onbekende Telegram-accounts krijgen **0 antwoorden en verbruiken 0 LLM-tokens** (volledig onzichtbaar voor vreemden).
   - Nieuwe mensen (gezinslid of gast) worden uitsluitend gekoppeld via een **eenmalige, tijdgebonden 256-bit cryptografische uitnodigingslink** (`https://t.me/<bot>?start=inv_<token>`) die alleen het gezinshoofd aanmaakt (via dashboard of `/uitnodigen` in zijn privéchat). Op schijf staat alleen `sha256(token)`. Na één klik verbrandt de link direct.
4. **Gezinshoofd kan Eva altijd zelf makkelijk bijstellen:**
   - Jaap kan zowel in het lokale eigenaar-dashboard (`/internal/v1/dashboard/cortex`) als via zijn eigen Telegram-privéchat (`/eva`, `/uitnodigen`, `/leden`, `/intrekken`) mensen toevoegen/pauzeren én Eva's naam, karakter, spreekstijl en vaste huisregels aanpassen of met 1 klik terugzetten naar de vorige revisie.

---

## 2. Reeds Uitgevoerd, Opgeschoond & Live Gezet (met lokale back-up)

- **Volledige back-up vóór opschoning:** `/var/tmp/eva2-pre-clean-backup-20261113/` (`eva_core.sql.gz`, `hindsight.sql.gz`, `model-providers-state.json`).
- **Database-opschoning (voltooid):**
  - `eva2-managed-b-slot3-hindsight-postgres-1`: 24 synthetische testbanken, 1.887 vervuilde testdocumenten en 508 vergiftigde style-mirrors (`"uitsluitend met OK / SNEL / FAST"`) verwijderd; alle echte herinneringen rond Jaap, Lola (Somtoday, koken ma/di, sushi) en het huis behouden; gezinsfeiten gecorrigeerd naar uitsluitend Jaap & Lola.
  - `eva2-managed-b-slot3-postgres-1`: 352 mislukte/test `delegated_work_runs` en ~578k spam-checkpoints/events verwijderd met behoud van `restore_activation`, actieve mandaten en groepsconsent.
- **Handboeien verwijderd & 15 LLM-providers actief (`commits 923847a7` t/m `a4073512`):**
  - Meer dan 700 regels gescripte nep-antwoorden (`_generate_subscription_cortex_turn`, `_wrap_baseline_sensory_evaluation`) verwijderd uit `src/eva/bootstrap/optional_model_providers.py`.
  - `EVA_CONVERSATION_CONTACT_MODEL` uitgeschakeld in live runtime (geen `Sol/none`-degradatie meer; directe `gpt-6-astra` `medium` + actieve `search_public_info` in `src/eva/bootstrap/public_lookup.py` + `EVA_MAXIMUM_REPLY_WORDS=320`).
  - 15 LLM-providers ondersteund in `src/eva/operations/model_provider_settings.py` (ChatGPT/Codex OAuth + 14 cloud/lokale presets waaronder Gemini, Claude, OpenAI API, OpenRouter, Grok, DeepSeek, Mistral, Groq, Cerebras, Together, Fireworks, Perplexity + sleutelloze lokale Ollama en LM Studio/llama.cpp).

---

## 3. Gevonden Technische Oorzaken voor Vervolgfasen (zodat niets opnieuw uitgezocht hoeft te worden)

### A. Exacte oorzaak trage LiveKit-spraak (~4s vertraging) — voor Fase 4C
In `src/eva/voice_service/codex_media.py` en `src/eva/voice_service/codex_backend.py` doorloopt elke gesproken zin nu **drie seriële rondes**:
1. **Hop 1 (`VOICE_PROMPT`, `codex_media.py:18`):** Gebruikt een OpenAI Realtime-sessie uitsluitend als trage transcriptie-papegaai (`INPUT_FRAGMENT_QUIET_SECONDS = 0.55`) en gooit alle gegenereerde audio weg.
2. **Hop 2 (`codex_backend.py:4`):** Stuurt de tekst naar `core` en wacht tot het **volledige** tekstantwoord klaar is.
3. **Hop 3 (`SPEECH_PROMPT`, `codex_media.py:23`):** Voert het tekstantwoord in brokjes van 25 woorden (`RENDER_SPEECH_MAX_WORDS = 25`) aan een *tweede* OpenAI Realtime-sessie met 300 ms stiltepauzes ertussen.
- **Oplossing in Fase 4C:** Vervang deze 3-staps ketting door **één directe Single-Hop Multimodal Realtime sessie** (Audio-in → Audio-uit in ~250 ms) met Eva's echte identiteit, Hindsight-geheugen, HA-tools (zoals de boodschappenlijst `todo.*`) en live `HoloStage`-schermkaarten, met behoud van Eva's Python-rechten- en privacycontroles.

### B. Waarom Eva1-camerapersoonsherkenning wisselvallig was — voor Fase 4B
- Eva1 beoordeelde vaak **één losse groothoek-snapshot** (bewegingsonscherpte, schuin profiel, tegenlicht, nacht-IR) en stuurde bij twijfel *"Wie is dit?"*-berichten.
- **Oplossing in Fase 4B (op bestaande lokale basis `eva2-local-face-bridge.service` + `voice_context.py`):**
  1. **Lokale Stembiometrie (192-D ECAPA-TDNN, <15 ms CPU, 0 cloud-opslag):** Herkent binnen ~1 seconde of **Jaap** of **Lola** spreekt (ook op telefoon of Chromecast-afstandsbediening zonder camera).
  2. **Multi-Frame Gezichtstracklets (ArcFace scherpte- & kijkhoekpoort):** Fuseert 5–8 frames i.p.v. één vage snapshot + combineert met stem en Wi-Fi/aanwezigheid (>99,5% zekerheid op UniFi Screen). Schermt privézaken direct af zodra een derde meekijkt/meeluistert.
  3. **Anti-Spam Bezoekersbeleid:** Vage passanten en snelle bezorgers veroorzaken **nooit** *"Wie is dit?"*-spam; alleen scherpe frontale onbekenden die echt bij de deur blijven staan komen rustig op één reviewbaar *"Geziene bezoekers"*-kaartje.

---

## 4. Volgorde van de 5 Gecombineerde Fasen (Post-LLM Architectuur)

1. **Fase 1 (Core & Gezinshoofd-Regie — AFGEROND IN CODE & LIVE):**
   - Native C **Liquid Time-Constant (`dx/dt`) ODE-kern** (`eva_liquid_ode_step` met variabele tijdconstanten $\tau_i(x, I)$ en RK4-integratie, 0 tokens in rust).
   - **15 LLM-providers** als verwisselbaar taalorgaan zonder nep-fallbacks.
   - **Gezinshoofd-Beheer (`HouseholdHeadSettings`):** 100% gesloten Telegram (`default-deny`), eenmalige 256-bit `/start inv_<token>` uitnodigingslinks voor gezinsleden/gasten, leden pauzeren/intrekken zonder herstart, én direct aanpassen van Eva's persoonlijkheid, spreekstijl en vaste huisregels (met revisieherstel) via zowel het Cortex-dashboard als Jaaps Telegram-chat.
2. **Fase 2 (Huis-Proprioceptie, Zelfstandige LAN-Ontdekking & Digitale Organogenese — AFGEROND IN CODE, ONAFHANKELIJK SECURITY-GEKEURD & LIVE):**
   - **Actieve RFC1918 LAN-scanner (`LanProprioceptionScanner`):** ontdekt zelfstandig apparaten op het thuisnetwerk via mDNS, SSDP/UPnP, HTTP/TLS, ONVIF, RTSP, Tuya/Lidl, Shelly, Philips Hue CLIP v2, Google Cast en MQTT — óók zonder Home Assistant — met strikte SSRF-, IP/poort/TLS-pinning en anti-spoofingbescherming (`C-02`).
   - **Spatial Presence Field (0 LLM-tokens):** berekent per kamer aanwezigheid en bewegingsenergie uit Zigbee/Hue LQI-rimpels, Wi-Fi RTT-jitter en sensorsignalen met exponentieel verval ($\tau = 90\text{s} / 240\text{s}$) en voedt rechtstreeks Eva's C/SIMD Liquid ODE-kern (`dx/dt`).
   - **Pre-LLM `AES-256-GCM` Kluis (`PreLlmCredentialVault`, `0o600`):** onderschept `/kluis` en `/wachtwoord` vóór SQLite, PostgreSQL `conversation_journal` en elk taalmodel (zowel in Telegram met directe chatverwijdering + HMAC-brugbewijs als in het webdashboard).
   - **Digitale Organogenese (`register_custom_organ` & `poll_organ`):** native micro-drivers (Hue, Shelly, HA, TCP/RTT) én AST-gevalideerde custom Python micro-organen in een geïsoleerd `python3 -I` subproces (`RLIMIT_CPU=2s`, `RLIMIT_FSIZE=0`, `RLIMIT_AS=256MB`, `SHA-256` integriteitspinning en maximaal 4 JSON-RPC kanaalverzoeken zonder dat geheimen ooit het subproces binnenkomen).
   - **Volledige gast-isolatie (`H-04`):** gasten en ongekende externe afzenders krijgen nooit LAN-, apparaat-, kamer- of `huisrust`-context te zien, en onbevestigde netwerkbanners komen nooit ongefilterd in LLM-prompts terecht.
   - **Live geverifieerd op thuisnetwerk (`eva2-living-core:fase2-complete-20261113` & `eva2-living-worker:fase2-complete-20261113`):** 11 fysieke apparaten op het primaire `/24`-thuisnetwerk direct ontdekt in `pending_consent` (Philips Hue Bridge CLIP v2 + Zigbee LQI, 2× Google Cast / Google TV, Sonos-speaker, Home Assistant, MQTT/Zigbee-broker en 5× UniFi-netwerkapparaten), met `compatible: true` tussen `core` en `delegated-work-worker`.
3. **Fase 3 (Onfeilbare Taakaflevering, Causale Tijdmachine & Genetische Zelfreparatie):**
   - Eén geconsolideerde claimtransactie in `delegated_work_delivery.py` (`P0-02`), duurzame 429/503-hervatting (`E1.14`), lokaal **Structural Causal Model (`oorzaak → gevolg`)** met nachtelijke counterfactual droom-replay, en onafhankelijk getoetste zelfreparatie (`E4.16`/`E4.19`).
4. **Fase 4 (`Eva HoloStage` op UniFi Screen, Chromecast/Google TV & iPhone/Android + Sub-300ms Spraak + Stem/Gezichtsherkenning):**
   - Één gedeeld podium met 3D Eva (`eva.glb`) + live visuele kaarten ("op het scherm toveren", zoals de HA-boodschappenlijst live bijwerken/tonen én voorlezen, Lola's schooldag/kookdagen, deurbel/huisoverzicht).
   - **UniFi Screen APK** (*on-device gaze-to-talk* + hardware-AEC), **Chromecast/Google TV** (lokaal Cast V2 op-tv-toveren + Android TV-app met afstandsbediening-mic), en **`Eva Pocket` WebRTC WebApp** op iPhone/Android (met live camera-meekijken en buitenshuis via Tailscale HTTPS).
   - **Single-hop ~250 ms Realtime spraak** + **ECAPA-TDNN stem- & ArcFace multi-frame gezichtsherkenning** voor Jaap en Lola.
5. **Fase 5 (7 Dagen Gezinsgebruik & Eindacceptatie — `F2.1` → `F2.2`):**
   - Zeven aaneengesloten dagen stabiel gebruik door Jaap en Lola op één release zonder kunstmatige proeven, gevolgd door expliciete eigenaaracceptatie.
