# Eva2 — actueel werk (compacte projectie)

Automatisch uit development/design/product/execution-workboard-v1.json; geen tweede statusbron.

## Nu

**E4.16 · Zelf fouten ontdekken, duurzaam repareren en verbeteringen blijven toepassen** — in_progress

Volgende stap: Twee concrete live productiedefecten rond lopende werkcorrecties en ingehaalde E4.16-zelfreparaties zijn opgelost, onafhankelijk gereviewd (VERDICT: APPROVED), lockstep geïnstalleerd in core/worker/evaluator (4 OCI-lagen, check_delegated_runtime_profiles compatible=true) en live geverifieerd: (1) eigenaarcorrecties (revise_digital) op lopende digitale taken wachten op STOP en hervatten eenmalig gecontroleerd als work:eva-resume:... (prior_outcome_unknown=True), terwijl niet-hervatbare ongeplande resumes naar SCHEDULED_WORK_BLOCKED overgaan zonder TypeError-crash of vals SCHEDULED_WORK_ADMITTED, en (2) RuntimeRepairTargets herkent ingehaalde gemengde callstacks (tot 8 frames), PostgresSelfRepair sluit ingehaalde queued en rustende terminal repair_investigating-incidenten af als repair_superseded zonder lopende host-handoffs (RUNTIME_REPAIR_REQUEST_ADMITTED) te doorkruisen (live bewezen: 9ea9f79b, be9b63cb en 1c8a4f53 -> repair_superseded/completed), en PostgresOwnerInitiatives toont afgeronde/ingehaalde ouder- en kind-reparatiekaarten als done (can_stop=False) met behoud van F2.1 (rel-f4d4b55de9093346-4c8e1ed17e1c65e9). Volg nu het volgende echte autonome hersteldoel én de lopende 7-daagse gezinswaarneming (F2.1).

## Voortgang

**83/103** geregistreerde deeltaken afgerond; 1 door eigenaar ingetrokken en 19 open. Geen percentage product- of gezinsacceptatie.

| Uitkomst | Stand | Nog niet bewezen |
| --- | --- | --- |
| Persoonlijkheid | Door jou geaccepteerd · M3.3 | M3.3 en HUE1–4 behouden. Samenhangend dagelijks gebruik en finale gezinsacceptatie. |
| Geheugen | Geheugenbasis geïnstalleerd · E4.11 technische opschoning afgerond | Technische opschoning binnen E4.11 afgerond en live geverifieerd (memory=up_to_date, hindsight=ready). F2.1/F2.2 blijven uitsluitend gezinsacceptatie en meerdaags nut. |
| Eigen initiatief | Beoordelingsroute vrijgemaakt · gekoppeld aan Bio-Homeostatische Cortex | Afgebroken beoordelingen gesloten via eigenaarroute (unresolved_review=null) en gekoppeld aan actief abonnements-LLM. Dagelijks passend initiatief en meerdaags nut blijven open. |
| Zelfstandig werk | Werkend en geïnstalleerd · gezinsgebruik open | Bediening en afgebakende onderhoudsfase technisch afgerond. Samenhangend dagelijks gezinsgebruik en jouw acceptatie blijven F2.1/F2.2. |

## Prioriteitsvolgorde (gegenereerd)

1. Fase 1 · Continue Liquid-Tijdskern (dx/dt), 15+ LLM-catalogus als verwisselbaar taalorgaan & strikt privé Telegram (default-deny, 0 antwoorden aan vreemden, uitsluitend eenmalige 256-bit eigenaar-uitnodigingslink zonder agent/principal-gedoe) (M3.3, E1.10, E1.13, E2.1, E2.2, E2.5, O1.1).
2. Fase 2 · Fysieke Huis-Proprioceptie (Spatial Presence Field incl. Hue/Zigbee & Wi-Fi bewegingsveld), actieve LAN-ontdekking (óók zonder Home Assistant, zoals Lidl/Tuya-, ONVIF- of RTSP-camera's), pre-LLM wachtwoordkluis & Digitale Organogenese (éénmalige LLM-synthese naar 2ms deterministische micro-drivers zonder LLM-tokens) (E1.6, E1.12, E2.8, E4.15).
3. Fase 3 · Causale Tijdmachine & Counterfactual Droom-Ontdekking (oorzaak->gevolg graaf i.p.v. LLM-gokken), één onfeilbare taakafleverroute in delegated_work_delivery.py, duurzame 429/503-overleving (E1.14) & genetische zelfreparatie (E4.16, E4.19; E4.18 technisch behouden).
4. Fase 4 · Alomtegenwoordige Eva HoloStage (UniFi Screen APK met gaze-to-talk, Chromecast/Google TV op-het-scherm-toveren + afstandsbediening-mic, iPhone/Android Eva Pocket met live camera + Tailscale HTTPS én gedeelde HA-boodschappenlijst/gezinskaarten), Lokale Stem- & Multi-Frame Gezichtsherkenning voor Jaap & Lola (ECAPA-TDNN + ArcFace zonder 'wie is dit?'-spam), Sub-300ms Single-Hop Spraak & Zero-UI Anticipatie (P1-02, E1.6–E1.12, E4.11, AU-008 + C3.5→C3.4→C4.1→C3.6, C4.3; P1-03/P1-04/P1-06 grenzen behouden).
5. Fase 5 · Symbiotische Co-Evolutie, parallelle gezinswaarneming, zeven dagen echt gezinsgebruik & expliciete eigenaaracceptatie (P1-05, F2.1 → F2.2; P2.1 en C4.3 geparkeerd; E2.6 door eigenaar ingetrokken).

## Open taken

| Taak | Status |
| --- | --- |
| C3.4 · Natuurlijk spreekritme zonder onbedoelde pauzes | planned |
| C3.5 · Natuurlijke beurtgrenzen en behoud van zachte beginwoorden | planned |
| C3.6 · Speaker herstelt na tijdelijke verbindingsuitval | planned |
| C4.1 · Onderbreken op beide speakers | planned |
| C4.3 · Optioneel: stoppen en doorpraten op telefoon | geparkeerd |
| P2.1 · Vergelijking op eindkandidaat uitvoeren | geparkeerd |
| F2.1 · Zeven dagen gezinsgebruik | planned |
| F2.2 · Eindoordeel vastleggen | planned |
| E1.6 · Zelf relevante bronveranderingen aansluiten op eigen aandacht | Technisch klaar; praktijkbewijs open |
| E1.7 · Routines met betekenis, uitzonderingen en tegenbewijs leren | in_progress |
| E1.8 · Knelpunten vooraf herkennen vanuit mensen, huis en bedoelingen | in_progress |
| E1.10 · Een herkenbare eigenheid uit echte ervaringen en creatieve projecten ontwikkelen | in_progress |
| E1.11 · Een afgesproken gezinsverantwoordelijkheid zelfstandig blijven dragen | planned |
| E1.12 · Blijvende huisvoorzieningen samen adopteren, beheren en terugdraaien | in_progress |
| E1.14 · Afgesproken continuïteit bij modeluitval en herstart aantonen | in_progress |
| E4.11 · Alleen belangrijke kennis als blijvend geheugen bewaren | in_progress |
| E4.15 · Van zelfontdekte behoefte naar eigen uitvinding en later gebruik | in_progress |
| E4.16 · Zelf fouten ontdekken, duurzaam repareren en verbeteringen blijven toepassen | in_progress |
| E4.19 · Relevante updates zelfstandig ontdekken en beheerd toepassen | in_progress |

## Door eigenaar ingetrokken

Geen voltooide functie; oorspronkelijke criteria en bewijs blijven bewaard.

- E2.6 · WhatsApp met eigen Eva-nummer en live bellen — ingetrokken; geen handeling nodig.

## Van jou

Geen handmatige Google-inrichting door Codex. Eva moet eerst zelf de volledige aansluiting en geldige activatielink opleveren, of een echte ontbrekende voorwaarde aantonen.

## Details en historie

Alle oorspronkelijke eindvoorwaarden, bewijs, fouten en vervolgstappen staan in het [JSON-werkbord](../design/product/execution-workboard-v1.json).
De volgorde staat in [AFRONDINGSPLAN](AFRONDINGSPLAN.md).
Het oorspronkelijke werkbord staat in [development/archive/history/eva2/design/product/execution-workboard-v1.json](../archive/history/eva2/design/product/execution-workboard-v1.json).
