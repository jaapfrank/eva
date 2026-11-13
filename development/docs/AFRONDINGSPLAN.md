# Eva — gecombineerd afrondingsplan

**Eén bron voor actuele uitvoering en bewezen voortgang:** [execution-workboard-v1.json](../design/product/execution-workboard-v1.json), met `currentWork` en `audit.deliveryPlan`. [WERKBORD.md](WERKBORD.md) is een projectie, geen tweede status. Op instructie van de eigenaar (13 november 2026) zijn de losse P0/P1/P2-stappen en de out-of-the-box producteisen samengevoegd tot **vijf gecombineerde afrondingsfasen (Fase 1 t/m Fase 5)**, met volledig behoud van alle 103 geregistreerde deeltaken, veiligheidscriteria en oorspronkelijke bewijseisen.

## Uitgangspositie — GitHub, 5 november 2026 (exact behouden ijkpunt)

- **83/103** kleine taken technisch afgerond; **20 open**: 9 bezig, 10 gepland, 1 geblokkeerd (waarvan `E2.6` door eigenaar ingetrokken → 19 open). Slechts **4/24** oorspronkelijke uitkomsten afgesloten. Getelde deeltaken zijn geen percentage productgereedheid.
- **E4.16 staat op `in_progress`**. De eerdere begrensde correcties en compact overzicht zijn **geïnstalleerd en onafhankelijk teruggelezen**. Lokaal is ook de begrensde 429-hervatting geïnstalleerd; de opvolger stopte op HTTP400 zonder exact operatiebewijs. Volledig autonome foutafhandeling en later hergebruik zijn **niet** bewezen. Geen werk dubbel uitvoeren.
- **E4.18 is technisch afgesloten.** Dat zegt niet dat álle actieve dubbele code verdwenen is. De **110 importgrensschendingen** zijn de **oude meting van 13 oktober**; op het werkbord zijn inmiddels 169 werkmapsignalen gemeten en actieve ontvangst-/onderzoeksgrenzen hersteld.
- Productproblemen die deze gecombineerde fasen oplossen: stijve/ambtelijke gesprekshouding, beperkte 3-provider LLM-keuze, te technische kanaal-/persoonskoppeling, afhankelijkheid van vooraf ingericht Home Assistant i.p.v. zelfstandige netwerkontdekking, provider-503/429 bij lopend werk, dubbele afleverclaims in `delegated_work_delivery.py`, en openstaande full-duplex spraak- en gezinsacceptatie (`F2.1`/`F2.2`). `P2.1` blijft geparkeerd.

---

## De 5 Gecombineerde Afrondingsfasen (Post-LLM Sci-Fi Architectuur)

> **Kernprincipe voorbij standaard LLM-agents (OpenClaw / Hermes / OpenCode):** Een LLM is bij Eva **uitsluitend het spraak-/taalorgaan (Broca/Wernicke)**, nooit het brein zelf. Eva leeft continu tussen berichten door via niet-LLM wiskunde in C/SIMD en Python: een **Liquid Time-Constant (`dx/dt`) toestandsmotor**, **fysieke huis-proprioceptie**, **zelf-compilerende 2ms micro-reflexen (Digitale Organogenese)**, een **causale tijdmachine met droom-replay** en **Zero-UI anticipatie**.

### Fase 1 — Continue Liquid-Tijdskern, 15+ LLM-providers & strikt gesloten 1-klik uitnodiging
*Combineert continue niet-LLM bewustzijnsstaat + natuurlijke gesprekshouding (`M3.3`/`E1.10`/`E1.13`), 15-provider LLM-catalogus (`E2.5`) en gesloten cryptografische gezinskoppeling (`O1.1`/`E2.1`/`E2.2`).*

| Onderdeel | Concrete realisatie en eindcontrole |
| --- | --- |
| **1A · Continue Liquid ODE-kern (`dx/dt`) & menselijk doorpakken** | Eva's staat evolueert continu in echte tijd (`dx/dt` in C/SIMD, 0 LLM-tokens in rust) i.p.v. dood stilvallen tussen berichten. Geen nep-fallbacks of `Sol/none`-lobotomie: gesprekken draaien op `gpt-6-astra` (`medium`) met directe Hindsight-herinnering en pakken door tot echt resultaat. |
| **1B · 15+ LLM-catalogus als verwisselbaar taalorgaan** | 1-klik ondersteuning voor 15 providers (ChatGPT/Codex OAuth, Gemini, Claude, OpenAI API, OpenRouter, xAI Grok, DeepSeek, Mistral, Groq, Cerebras, Together, Fireworks, Perplexity + sleutelloze lokale Ollama en LM Studio/llama.cpp zonder GPU-plicht). |
| **1C · Strikt privé Telegram (`default-deny`) & eenmalige cryptografische uitnodigingslink** | **Geen open voordeur op Telegram:** onbekende Telegram-accounts worden op poortniveau direct genegeerd (0 antwoorden, 0 tokens, onzichtbaar voor buitenstaanders). Alleen de eigenaar vraagt in chat om een eenmalige 256-bit uitnodigingslink (`t.me/<bot>?start=<token>`, kort geldig, single-use) voor bijv. een gezinslid of gast; één klik koppelt direct met gescheiden privacygrens zonder technisch agent-/principal-gedoe. |

### Fase 2 — Huis-Proprioceptie, LAN-ontdekking (óók zonder HA) & Digitale Organogenese (2ms reflexen)
*Combineert ruimtelijke netwerk-/sensorfusie (`E1.6`/`I2.1`), veilige wachtwoordkluis + apparaatadoptie (`E2.8`/`E1.12`) en zelf-compilerende micro-drivers (`E4.15`).*

| Onderdeel | Concrete realisatie en eindcontrole |
| --- | --- |
| **2A · Actieve LAN-ontdekking & Fysieke Huis-Proprioceptie (incl. Hue/Zigbee & Wi-Fi RF-veld)** | Eva verkent binnen mandaat zelf het LAN (mDNS, SSDP/UPnP, ONVIF, RTSP, Tuya/Lidl-camera's, Shelly, Hue, Chromecast/Sonos, Matter én optioneel HA) en fuseert LAN/Wi-Fi-signaalrimpel, Hue/Zigbee LQI/RSSI-variatie, stroomsignaturen en sensoren tot een lokale **Spatial Presence Field** (voelen wáár in huis beweging of afwijking is, zonder camera-spionage of LLM-tokens). |
| **2B · Toestemming & pre-LLM wachtwoordkluis** | Vindt Eva een apparaat, dan vraagt ze in gewone taal of ze mag koppelen. Wachtwoorden of sleutels worden via een 1-tik kluisveld of **vóór het taalmodel onderschepte Telegram-invoer** (met directe `deleteMessage`) rechtstreeks in Eva's versleutelde kluis opgeslagen — nooit zichtbaar voor een cloud-LLM of geheugenlog. |
| **2C · Digitale Organogenese (van trage LLM naar 2ms native reflex)** | Ontdekt Eva een onbekend protocol of terugkerende actieketen, dan gebruikt ze de LLM **éénmalig als architect** om een getoetste, deterministische micro-driver/reflex (`E4.15` / `E1.12`) te genereren. Daarna voert Eva die bediening uit in **milliseconden met 0 LLM-tokens** (echt spiergeheugen i.p.v. trage JSON-toolcalls zoals bij OpenClaw/Hermes). |

### Fase 3 — Causale Tijdmachine, onfeilbare taakaflevering & autonome zelfevolutie (`P0` + `P1-01` + `P2-02`)
*Combineert causale gebeurtenisgraaf, `P0-02` (`E4.18`-nazorg), `P1-01` (`E1.14`) en `P0-01`/`P0-03` (`E4.16`/`E4.19`).*

| Onderdeel | Concrete realisatie en eindcontrole |
| --- | --- |
| **3A · Eén onfeilbare taakafleverroute (`P0-02`)** | Consolideer de drie overlappende claimtransacties in `delegated_work_delivery.py`, houd kanaaladapters dun en bewaar exact-once aflevering, STOP en intrekking zonder `ambiguous`-restanten. |
| **3B · Causale Gebeurtenisgraaf & Counterfactual Droom-Ontdekking** | Bouw een echt lokaal **Structural Causal Model (`oorzaak → gevolg`)** over huis-, energie-, netwerk- en gezinsgebeurtenissen. Tijdens nachtelijke droom-consolidatie speelt Eva tijdslijnen tegenfeitelijk af om verborgen oorzaken en dreigende storingen wiskundig te bewijzen (geen statistische LLM-gokken) én overleeft ze provider-429/503 duurzaam (`E1.14`). |
| **3C · Onafhankelijk getoetste zelfreparatie & genetische zelfverbetering (`E4.16`, `E4.19`)** | Scheid validatie van dispatch in `reviewed_component_application.py` met onafhankelijke reviewerherkomst en rollback, zodat Eva echte fouten en trage paden zelfstandig ontdekt, repareert, installeert en aantoonbaar hergebruikt. |

### Fase 4 — Alomtegenwoordige Eva (`HoloStage`: UniFi, Chromecast & iPhone/Android), Sub-300ms Spraak & Zero-UI (`P1-02` + `P2-01` + `P2-03`)
*Combineert voorspellende voorbereiding (`P1-02`, `E1.6–E1.13`, `E4.11`, `AU-008`), alomtegenwoordige scherm-/telefoonaanwezigheid en sub-300ms full-duplex spraak (`C3.5`, `C3.4`, `C4.1`, `C3.6`, `C4.3`).*

| Onderdeel | Concrete realisatie en eindcontrole |
| --- | --- |
| **4A · Zero-UI Voorbereiding & Bio-Sociale Co-Regulatie** | Eva zet op basis van haar causale wereldmodel en veranderingspoort oplossingen stil klaar *vóór* je erom vraagt (*Anticipatory Staged Reality*), past timing, toon en informatiedichtheid aan op je gemeten cognitieve druk, en bewaart alleen duurzame kernkennis (`E4.11`). |
| **4B · `Eva HoloStage` op UniFi Screen, Chromecast/Google TV & iPhone/Android + Lokale Stem- & Gezichtsherkenning** | Één gedeeld visueel en vocaal podium voor het gezin (**uitsluitend Jaap en Lola**): **(1) UniFi Screen APK** met *on-device gaze-to-talk*, hardware-AEC en touchscreen-kaarten; **(2) Chromecast / Google TV** waarbij Eva zichzelf lokaal via Cast V2 op elk tv-scherm kan toveren én via Android TV-APK luistert naar de Bluetooth-microfoon op de tv-afstandsbediening; **(3) iPhone & Android (`Eva Pocket`)** als Full-Screen WebRTC spraak-, camera- & kaarten-app (buitenshuis veilig via Tailscale HTTPS); **(4) Lokale Biometrische Stem- & Multi-Frame Gezichtsherkenning** (192-D ECAPA-TDNN stemprofiel + ArcFace scherpte-/kijkhoek-trackletfusie, 0 cloud-biometrie) zodat Eva Jaap en Lola direct herkent aan stem én beeld, persoonscontext/privacy bewaakt en op buitencamera's nooit *"Wie is dit?"*-spam stuurt bij vage passanten. |
| **4C · Sub-300ms Full-Duplex Spraak zonder trage LiveKit/HA-waterval** | Vervang de trage seriële keten (*wachten op volledige HA-STT stilte → volledig LLM-antwoord → volledige TTS*) door een directe audiostream met **Native Speech-to-Speech Realtime** (OpenAI Realtime / Gemini Live Audio) én **speculatieve eerste-zinsdeel streaming + 2ms spiergeheugen-reflexen** voor tekst-LLMs, met behoud van zachte beginwoorden (`C3.5`), vlot ritme (`C3.4`), directe barge-in onderbreking (`C4.1`) en zelfherstel (`C3.6`). |

### Fase 5 — Symbiotische Co-Evolutie, 7 dagen praktijkbewijs & eindacceptatie (`P1-05` + `P2-04`)
*Combineert `P1-05`, `F2.1` en `F2.2`.*

| Onderdeel | Concrete realisatie en eindcontrole |
| --- | --- |
| **5A · Parallelle praktijkwaarneming & groeiende gezinsintuïtie (`P1-05`)** | Directe waarneming van echt gebruik op de geïnstalleerde release waarbij Eva een onkopieerbare, gedeelde gezinsintuïtie opbouwt die geen los taalmodel bezit. |
| **5B · Zeven dagen gezinsgebruik & eindoordeel (`F2.1` → `F2.2`)** | Zeven aaneengesloten dagen op één stabiele release; eerlijke meting van proactieve waarde, spierherinnering-snelheid, nul-token-rust, fouten en kosten, gevolgd door eigenaaracceptatie (`F2.2`). |

---

## Randvoorwaarden & behouden afspraken

- **Nu niet blokkerend (`P3`):** `E2.6` (WhatsApp met eigen nummer) blijft door eigenaar ingetrokken; `C4.3` (telefoonspraak) en `P2.1` (externe benchmarkvergelijking) blijven geparkeerd en blokkeren Fase 1–5 niet.
- **Geen tweede brein of stille kosten:** Eén kern, geheugen, planner en rechtenmodel; geen tweede permanente schaduwdaemon, geen ongevraagde betaalde fallback en geen verzwakking van kind-/groepsprivacy (`P1-04`) of importgrenzen (`P1-03` / `P1-06`). De volledige technische overdracht en architectuurblauwdruk staat permanent in [BLAUWDRUK_POST_LLM_EN_HOLOSTAGE.md](BLAUWDRUK_POST_LLM_EN_HOLOSTAGE.md); concrete bestandsingrepen staan in [VEREENVOUDIGING.md](VEREENVOUDIGING.md); de vorige plantekst blijft exact bewaard in het [integratiearchief](../archive/history/20261105-technical-finalization/before/development/docs/AFRONDINGSPLAN.md) en [opruimarchief](../archive/history/20261105-documentatie-opruiming/AFRONDINGSPLAN.md).
- **Werkwijze:** **Gecombineerde uitkomst → exacte oorzaak/ontwerp → gerichte bouw → positieve én negatieve tests → onafhankelijke review → live installatie + core/worker-profielcontrole → menselijk gebruik → pas dan status bijwerken.**
