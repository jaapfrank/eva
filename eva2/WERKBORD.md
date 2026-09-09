# Eva 2 — uitvoerbaar werkbord

Laatst bijgewerkt: 2026-09-09.

Doel: [Eva als zelfstandig lerende digitale huisgenoot](PROJECTDOEL.md).
Gebouwd, getest, live en door jou geaccepteerd zijn verschillende stappen.

VB-001: uitvoerbare speakerkandidaat aangesloten op bestaande HA/Eva-adapters. 146 gerichte controles groen; onafhankelijke review vond en bevestigde reparaties voor afsluiten en verdwijnende STT-resultaten. Echte woonkamer- en pipelinebinding read-only gecontroleerd. SP-01 en SP-02 blijven geaccepteerd.
[Actueel bewijs](evidence/product/VB-001-speaker-service-composition-2026-09-09.json).

Afspeelstatus verbinden aan de native huishoudelijke gespreksgeschiedenis, zodat gegenereerde maar onderbroken tekst niet als volledig gehoord geldt. Daarna duurzame dienst-/speakerinrichting, bruikbaar herstel en complete SP-03-praktijkproef.

## Nu

| Volgorde | Kaart | Resultaat | Status |
|---:|---|---|---|
| 138 | `VB-001` — Full-duplex voice leveren | Met Eva2 praten via de bestaande Telegram- of Home Assistant-aansluiting, waarbij Eva hoorbaar onderbroken kan worden en het gesprek daarna doorgaat. | 🟡 BEZIG |

Klaar betekent:

- De gevraagde doorlopende spraakroute naar Eva2 werkt op het gekozen kanaal inclusief hoorbaar onderbreken en hervatten; p95 stoplatentie maximaal250ms gemeten. Eva1-aansluitingen mogen worden hergebruikt, maar zonder bewezen onderbreking op het echte kanaal is VB-001 niet af. Browserproeven of losse spraakberichten sluiten dit niet af.
- Persoonsbinding, privacy, gesprekcontinuiteit, toolstopgrenzen en herstel zijn getest; complete gebruikersroute veilig live met onafhankelijke beoordeling. Menselijke ervaringsacceptatie wordt apart vastgelegd.

<details>
<summary>Technische uitvoering: bestanden en controles</summary>

### Exact werk voor `VB-001`

- `eva2/src/eva/application/voice.py`
- `eva2/src/eva/contracts/voice.py`
- `eva2/tests/application/test_voice_playback.py`
- `eva2/tests/contract/test_voice.py`
- `eva2/design/product/conversational-voice-v1.md`
- `eva2/src/eva/adapters/connectors/home_assistant_voice.py`
- `eva2/tests/connectors/test_home_assistant_voice.py`
- `eva2/src/eva/adapters/api/dashboard_voice.py`
- `eva2/tests/api/test_dashboard_voice.py`
- `eva2/src/eva/adapters/api/app.py`
- `eva2/src/eva/adapters/api/dashboard_voice_client.py`
- `eva2/src/eva/adapters/api/dashboard_conversation.py`
- `eva2/tests/api/test_dashboard_voice_client.cjs`
- `eva2/src/eva/bootstrap/dashboard_voice.py`
- `eva2/src/eva/bootstrap/runtime.py`
- `eva2/tests/bootstrap/test_dashboard_voice_bootstrap.py`
- `eva2/release/managed-b-release-input-authority-v1.json`
- `eva2/release/eva-cockpit-evidence-index-v1.json`
- `eva2/tools/managed_b_candidate/release_inputs.py`
- `eva2/tools/run_managed_b_required_tests.py`
- `eva2/tests/operations/test_managed_b_release_inputs.py`
- `eva2/tests/operations/test_run_managed_b_required_tests.py`
- `eva2/deploy/living-core/dashboard-voice.compose.yml`
- `eva2/tools/eva2_quiescent_cutover.py`
- `eva2/tools/managed_b_render.py`
- `eva2/tests/operations/test_eva2_quiescent_cutover.py`
- `eva2/tests/operations/test_managed_b_render.py`
- `eva2/tools/plan_home_assistant_voice_bridge.py`
- `eva2/tests/operations/test_plan_home_assistant_voice_bridge.py`
- `eva2/deploy/home-assistant/voice-pe-interruption.yaml`
- `eva2/deploy/home-assistant/README.md`
- `eva2/design/architecture/component-catalog-v1.json`
- `eva2/deploy/home-assistant/custom_components/eva2_voice/conversation.py`
- `eva2/deploy/home-assistant/custom_components/eva2_voice/__init__.py`
- `eva2/deploy/home-assistant/custom_components/eva2_voice/client.py`
- `eva2/deploy/home-assistant/custom_components/eva2_voice/config_flow.py`
- `eva2/deploy/home-assistant/custom_components/eva2_voice/manifest.json`
- `eva2/deploy/home-assistant/custom_components/eva2_voice/strings.json`
- `eva2/tests/operations/test_ha_voice_native_client.py`
- `eva2/src/eva/adapters/postgres/voice_requests.py`
- `eva2/src/eva/adapters/postgres/operational_timeline.py`
- `eva2/tests/postgres/test_operational_timeline.py`
- `eva2/src/eva/adapters/api/native_voice.py`
- `eva2/tests/api/test_native_voice.py`
- `eva2/src/eva/application/memory_audience.py`
- `eva2/src/eva/application/conversation.py`
- `eva2/src/eva/application/memory.py`
- `eva2/src/eva/adapters/projection/hindsight.py`
- `eva2/tests/application/test_memory_audience.py`
- `eva2/tests/projection/test_hindsight.py`
- `eva2/src/eva/application/cancellation.py`
- `eva2/src/eva/adapters/model/pydantic_conversation.py`
- `eva2/src/eva/bootstrap/native_voice.py`
- `eva2/src/eva/adapters/postgres/external_conversation_tools.py`
- `eva2/tests/application/test_cancellation.py`
- `eva2/tests/bootstrap/test_native_household_responder.py`
- `eva2/tests/model/test_pydantic_conversation_model.py`
- `eva2/src/eva/bootstrap/household_voice.py`
- `eva2/tests/bootstrap/test_household_voice_authority.py`
- `eva2/deploy/living-core/household-voice.compose.yml`
- `eva2/release/gate-toolchain-roots.in`
- `eva2/release/gate-toolchain.requirements.lock`
- `eva2/release/gate-toolchain.lock.json`
- `eva2/release/gate-toolchain-license-inventory.json`
- `eva2/tools/render_household_voice_ingress.py`
- `eva2/tools/verify_household_voice_ingress.py`
- `eva2/deploy/home-assistant/eva2-voice-ingress.service`
- `eva2/deploy/home-assistant/allow-native-voice-wsl.ps1`
- `eva2/deploy/home-assistant/native-voice-ingress.md`
- `eva2/tests/bootstrap/test_runtime_settings.py`
- `eva2/deploy/living-core/managed-b.compose.yml`
- `eva2/deploy/living-core/runtime.env.example`
- `eva2/tests/tools/test_persist_delegated_work_activation.py`
- `eva2/experiments/native-duplex-v1/reply_lane.py`
- `eva2/experiments/native-duplex-v1/README.md`
- `eva2/tests/operations/test_native_duplex_reply_lane.py`
- `eva2/evidence/product/VB-001-duplex-reply-coordination-2026-09-09.json`
- `eva2/evidence/product/VB-001-ha-speech-onset-2026-09-09.json`
- `eva2/experiments/native-duplex-v1/local_onset.py`
- `eva2/experiments/native-duplex-v1/silero_onnx.py`
- `eva2/experiments/native-duplex-v1/prepare_vad.py`
- `eva2/experiments/native-duplex-v1/vad-upstream.json`
- `eva2/experiments/native-duplex-v1/vad-requirements.lock`
- `eva2/experiments/native-duplex-v1/LICENSE.pipecat`
- `eva2/experiments/native-duplex-v1/LICENSE.silero`
- `eva2/tests/operations/test_native_duplex_local_onset.py`
- `eva2/evidence/product/VB-001-early-speech-onset-2026-09-09.json`
- `eva2/evidence/product/VB-001-microvad-onset-comparison-2026-09-09.json`
- `eva2/evidence/product/VB-001-silero-speech-onset-2026-09-09.json`
- `eva2/experiments/native-duplex-v1/playback_stop_barrier.h`
- `eva2/experiments/native-duplex-v1/speaker-stop.patch`
- `eva2/experiments/native-duplex-v1/prepare_speaker.py`
- `eva2/experiments/native-duplex-v1/verify_speaker_stop.py`
- `eva2/experiments/native-duplex-v1/speaker-upstream.json`
- `eva2/experiments/native-duplex-v1/LICENSE.va_pipecat`
- `eva2/experiments/native-duplex-v1/speaker-output.example.yaml`
- `eva2/tests/operations/test_native_duplex_speaker_stop.py`
- `eva2/evidence/product/VB-001-speaker-stop-candidate-2026-09-09.json`
- `eva2/experiments/native-duplex-v1/speaker_wire.py`
- `eva2/experiments/native-duplex-v1/ha_pcm.py`
- `eva2/experiments/native-duplex-v1/speaker-wire-v1.md`
- `eva2/tests/operations/test_native_duplex_speaker_wire.py`
- `eva2/tests/operations/test_native_duplex_ha_pcm.py`
- `eva2/evidence/product/VB-001-ha-pcm-wire-2026-09-09.json`
- `eva2/experiments/native-duplex-v1/eva_output_protocol.h`
- `eva2/experiments/native-duplex-v1/eva_output_driver.cpp`
- `eva2/experiments/native-duplex-v1/speaker-wire.patch`
- `eva2/experiments/native-duplex-v1/verify_speaker_wire.py`
- `eva2/tests/fixtures/native_duplex_speaker_host.h`
- `eva2/tests/fixtures/native_duplex_speaker_host.cpp`
- `eva2/tests/operations/test_native_duplex_speaker_protocol.py`
- `eva2/evidence/product/VB-001-firmware-wire-candidate-2026-09-09.json`
- `eva2/experiments/native-duplex-v1/speaker_endpoint.py`
- `eva2/experiments/native-duplex-v1/speaker-auth.patch`
- `eva2/experiments/native-duplex-v1/websocket-no-redirect.patch`
- `eva2/experiments/native-duplex-v1/websocket-upstream.json`
- `eva2/experiments/native-duplex-v1/LICENSE.esp_websocket_client`
- `eva2/experiments/native-duplex-v1/verify_speaker_auth.py`
- `eva2/tests/operations/test_native_duplex_speaker_endpoint.py`
- `eva2/evidence/product/VB-001-authenticated-speaker-candidate-2026-09-09.json`
- `eva2/experiments/native-duplex-v1/microphone_session.py`
- `eva2/tests/operations/test_native_duplex_microphone_session.py`
- `eva2/evidence/product/VB-001-continuous-microphone-session-2026-09-09.json`
- `eva2/experiments/native-duplex-v1/speaker_input.py`
- `eva2/experiments/native-duplex-v1/speaker_session.py`
- `eva2/tests/operations/test_native_duplex_speaker_input.py`
- `eva2/evidence/product/VB-001-authenticated-microphone-route-2026-09-09.json`
- `eva2/experiments/native-duplex-v1/eva_input_driver.cpp`
- `eva2/experiments/native-duplex-v1/eva_input_protocol.h`
- `eva2/experiments/native-duplex-v1/eva_microphone_source.h`
- `eva2/experiments/native-duplex-v1/prepare_native_controls.py`
- `eva2/experiments/native-duplex-v1/speaker-input.patch`
- `eva2/tests/operations/test_native_duplex_input_firmware.py`
- `eva2/evidence/product/VB-001-native-input-firmware-2026-09-09.json`
- `eva2/experiments/native-duplex-v1/speaker_service.py`
- `eva2/tests/operations/test_native_duplex_speaker_service.py`
- `eva2/evidence/product/VB-001-speaker-service-composition-2026-09-09.json`

Echte controles:

- `pytest -q tests/application/test_voice_playback.py`
- `pytest -q tests/contract/test_voice.py`
- `python3 tools/check_current_workboard.py`
- `pytest -q tests/connectors/test_home_assistant_voice.py`
- `pytest -q tests/api/test_dashboard_voice.py`
- `pytest -q tests/bootstrap/test_dashboard_voice_bootstrap.py`
- `pytest -q tests/bootstrap/test_runtime_settings.py`
- `pytest -q tests/operations/test_managed_b_release_inputs.py tests/operations/test_run_managed_b_required_tests.py`
- `pytest -q tests/operations/test_eva2_quiescent_cutover.py`
- `pytest -q tests/operations/test_managed_b_render.py`
- `pytest -q tests/operations/test_plan_home_assistant_voice_bridge.py`
- `pytest -q tests/operations/test_ha_voice_native_client.py`
- `pytest -q tests/postgres/test_operational_timeline.py`
- `pytest -q tests/api/test_native_voice.py`
- `pytest -q tests/application/test_memory_audience.py tests/projection/test_hindsight.py tests/application/test_conversation.py`
- `pytest -q tests/application/test_cancellation.py tests/bootstrap/test_native_household_responder.py tests/model/test_pydantic_conversation_model.py`
- `pytest -q tests/bootstrap/test_household_voice_authority.py`
- `pytest -q tests/operations/test_native_duplex_reply_lane.py tests/operations/test_ha_voice_native_client.py tests/connectors/test_home_assistant_voice.py`
- `pytest -q tests/operations/test_native_duplex_local_onset.py`
- `pytest -q tests/operations/test_native_duplex_speaker_stop.py`
- `pytest -q tests/operations/test_native_duplex_speaker_wire.py tests/operations/test_native_duplex_ha_pcm.py tests/operations/test_native_duplex_reply_lane.py`
- `pytest -q tests/operations/test_native_duplex_speaker_protocol.py tests/operations/test_native_duplex_speaker_stop.py`
- `pytest -q tests/operations/test_native_duplex_speaker_endpoint.py tests/operations/test_native_duplex_speaker_wire.py`
- `pytest -q tests/operations/test_native_duplex_microphone_session.py tests/operations/test_native_duplex_reply_lane.py tests/connectors/test_home_assistant_voice.py tests/operations/test_native_duplex_speaker_endpoint.py tests/operations/test_native_duplex_speaker_wire.py`
- `pytest -q tests/operations/test_native_duplex_speaker_input.py tests/operations/test_native_duplex_speaker_wire.py tests/operations/test_native_duplex_speaker_endpoint.py tests/operations/test_native_duplex_reply_lane.py tests/operations/test_native_duplex_microphone_session.py tests/connectors/test_home_assistant_voice.py`
- `pytest -q tests/operations/test_native_duplex_input_firmware.py tests/operations/test_native_duplex_speaker_protocol.py tests/operations/test_native_duplex_speaker_stop.py`
- `pytest -q tests/operations/test_native_duplex_speaker_service.py tests/connectors/test_home_assistant_voice.py`

</details>

## Daarna

Deze onderwerpen zijn alleen mijlpalen. Een mijlpaal wordt pas uitvoerbaar
nadat zij als eerstvolgende kleine kaart exacte bestanden en tests heeft.

- [ ] `VB-002` — Passende aanwezigheid leveren: Eva zoekt spontaan contact via de beschikbare speakers en deurbel, met herkenbare identiteit en gevoel voor het moment. Gezin beoordeelt plezier, betrokkenheid en hinder; onderbreken en stop werken.
- [ ] `AU-005` — Passend initiatief en kansen bewijzen: Meer gewaardeerd eigen initiatief dan Bottie: humor, nieuwsgierigheid, zelfgekozen onderzoek, creatieve bijdragen en kansen. Eva begint en vervolgt zelf werk, voert binnen bestaand mandaat uit en controleert het resultaat. Gezin beoordeelt eigenheid, plezier, nut en hinder; alleen meer berichten telt niet als winst.
- [ ] `CO-008` — Autonoom geheugen in gezinsgesprekken bewijzen: De oorspronkelijke brede geheugenuitkomst blijft open: relevante kennis zelfstandig kiezen, gewijzigde en tijdelijke informatie goed gebruiken in gewone gesprekken en langere gezinspraktijk. Geen handmatig geheugenbeheer; familie beoordeelt continuïteit en vertrouwen.
- [ ] `AU-001` — Eigenaarmandaat uitvoeren: Eva handelt binnen het afgesproken mandaat zonder steeds opnieuw te vragen.
- [ ] `TL-003` — Onbekende connector zelfstandig toevoegen: Eva kiest onderhouden software uit documentatie en bewijst de adapter.
- [ ] `VB-003` — Veilige browsercomputer kiezen: Eén onderhouden browsermotor ondersteunt accounts, meekijken en overname.
- [ ] `VB-004` — Browserherstel bewijzen: Een crash herhaalt geen externe mutatie blind.
- [ ] `AU-002` — Duurzame hulpagent bedienen: Starten, volgen, stoppen en hervatten werken via de gebruikersroute; hoofd- en hulpagent delen één provideradapter.
- [ ] `UX-001` — Eigenaar-dashboard leveren: Een onderhouden adminframework toont status, mensen, geheugen, kosten en bewijs.
- [ ] `UX-002` — Kanban bedienbaar maken: Taken zijn te starten, volgen en stoppen met bewijs per kaart.
- [ ] `AU-004` — Zelf tools en skills leren maken: Een ongeziene digitale taak leren uit documentatie; zelf een tool of skill maken, testen en bij een andere taak hergebruiken zonder nieuwe taakspecifieke kerncode. Fouten en menselijke hulp meetellen.
- [ ] `AU-006` — Project zelfstandig tot resultaat brengen: Een meerdaags cross-domainproject plannen, uitvoeren, bijsturen en na herstart hervatten met zichtbare kosten en stop. Een bedrijfspilot kan als afgesproken project; geen echte betalingen, publicatie of verplichtingen zonder passend mandaat.
- [ ] `AU-003` — Eigen onderdelen beoordelen en verbeteren: Eva ontdekt een overbodig of zwak onderdeel, vergelijkt onderhouden upstream, maakt een geïsoleerde verbetering en bewijst voor/na voordeel en terugrol. Alleen gemeten winst mag promoveren; geen zelfverruiming van rechten.
- [ ] `CO-004` — Optioneel geheugen inzien: Alleen een optionele inzagefunctie; geen voorwaarde voor dagelijks gebruik. Begonnen bronwerk blijft bewaard, niet afgevinkt of live gezet.
- [ ] `CO-005` — Optioneel expliciet vergeten of corrigeren: Alleen op uitdrukkelijk verzoek kunnen corrigeren of vergeten. Geen onderhoudstaak voor Jaap en geen blokkade voor autonoom geheugen.
- [ ] `IN-001` — Fresh install voor gebruikers: Een gewone gebruiker installeert Eva zonder terminal of configuratiebestanden.
- [ ] `IN-002` — Naar andere computer verplaatsen: Identiteit en data herstellen; connectoren veilig opnieuw koppelen.
- [ ] `IN-003` — Consumentenupdates leveren: Renovate stelt onderhouden dependencies voor; updates doorlopen dezelfde poort en rollen verliesloos terug.
- [ ] `CP-001` — Volledige productmatrix opnieuw uitvoeren: De tussentijdse gelijke proeven worden op de gezamenlijke releasekandidaat en actuele concurrenten als één geheel herhaald.
- [ ] `CP-002` — Cross-domainwinst aantonen: Herhaalde ongeziene gesprekken en handelingen toetsen de vooraf vastgelegde drempels tegen alle drie concurrenten. Resultaat, menselijk oordeel, hulp, tijd, kosten en herstel bepalen de uitkomst; winst is niet verondersteld.
- [ ] `CP-003` — Officiële benchmarks uitvoeren: AgentBench, GAIA en OSWorld gebruiken officiële scorers en trajecten.
- [ ] `FN-001` — Eindkandidaat bevriezen: Image, configuratie en bewijsset zijn onveranderlijk gebonden; daarna volgen de releasepoort en pas na veilige installatie de eindpraktijkproeven.
- [ ] `RB-007` — Verplichte testgroepen uitvoeren: Pas na functionele productkaarten slaagt iedere testgroep tegen dezelfde kandidaat.
- [ ] `RB-008` — Wegwerpstack testen: De kandidaat migreert, antwoordt, onthoudt, herstart en ruimt volledig op.
- [ ] `RB-009` — Bewijsset onafhankelijk beoordelen: Een onafhankelijke reviewer controleert de volledige releaseketen.
- [ ] `RB-010` — Actief en reserveslot beheren: Alleen groen werk gaat naar het reserveslot; controleer het daarna en wissel de route. Bij problemen blijft het huidige slot actief.
- [ ] `FN-002` — Computerherstart bewijzen: De veilig geïnstalleerde eindkandidaat komt zonder handmatige reparatie terug met dezelfde gegevens en zonder dubbele acties.
- [ ] `FN-003` — Zeven dagen praktijk en eindacceptatie bewijzen: Een ongewijzigde kandidaat doorstaat zeven dagen gezinspraktijk. De volledige bewijsset, vergelijking en menselijke beoordeling zijn groen vóór vervangbeslissing; geraakt bewijs wordt na relevante wijzigingen opnieuw uitgevoerd.

<details>
<summary>Afgeronde en eerdere uitvoerkaarten met bewijs</summary>

## Uitvoerkaarten en bewijs

Afgevinkt betekent alleen: de vastgelegde kaart is afgerond, niet het hele product.

- [x] `GOV-001` — Klein en controleerbaar werkbord afdwingen (done).
  [Bewijs](evidence/product/GOV-001-execution-workboard-tests-2026-09-01.json).
- [x] `GOV-002` — Bordovergangen statusvast maken (done).
  [Bewijs](evidence/product/GOV-002-status-stable-workboard-tests-2026-09-01.json).
- [x] `RB-001` — Verzegelde basisimages offline bruikbaar maken op een verse bouwer (done).
  [Bewijs](evidence/product/RB-001-sealed-oci-layout-empty-builders-2026-09-01.json).
- [x] `RB-002` — Nieuwe bootstrapcode verzegelen (done).
  [Bewijs](evidence/product/RB-002-bootstrap-release-authority-2026-09-01.json).
- [x] `RB-003` — Kandidaat op bouwer A maken (done).
  [Bewijs](evidence/product/RB-003-builder-a-offline-admission-2026-09-01.json).
- [x] `RB-004` — Kandidaat op bouwer B maken (done).
  [Bewijs](evidence/product/RB-004-builder-b-offline-admission-2026-09-01.json).
- [x] `RB-005` — Beide builds vergelijken (done).
  [Bewijs](evidence/product/RB-005-two-builder-reproducibility-2026-09-01.json).
- [x] `RB-006` — SBOM en kwetsbaarheden controleren (done).
  [Bewijs](evidence/product/RB-006-candidate-artifact-analysis-2026-09-01.json).
- [x] `GOV-003` — Eva2-werk tot één zichtbare schrijfboom begrenzen (done).
  [Bewijs](evidence/product/GOV-003-canonical-workspace-containment-tests-2026-09-02.json).
- [x] `CP-000` — Open-source evaluatiemotor voorselecteren (done).
  [Bewijs](evidence/product/CP-000-open-source-eval-engine-selection-2026-09-02.json).
- [x] `RR-001` — Hermetische kandidaatbron herstellen (done).
  [Bewijs](evidence/product/RR-001-hermetic-candidate-source-2026-09-02.json).
- [x] `RR-002` — Nieuwe schone kandidaat op twee verse bouwers maken (done).
- [x] `EV-001` — Inspect AI-evalomgeving en bruggen bouwen (done).
  [Bewijs](evidence/product/EV-001-inspect-ai-bridge-probe-2026-09-04.json).
  [Bewijs](evidence/product/EV-001-inspect-ai-bridge-probe-v2-2026-09-04.json).
- [x] `EV-002` — Kleine vierweg-gespreksnulmeting uitvoeren (done).
  [Bewijs](evidence/product/EV-002-controlled-baseline-result-2026-09-04.json).
- [x] `CO-001` — Telegram toont Eva's antwoordvorming live (done).
  [Bewijs](evidence/product/CO-001-telegram-live-drafts-contract-2026-09-04.json).
- [x] `UX-000` — Grip op Eva: één visueel dashboard (done).
  [Bewijs](evidence/product/UX-000-project-dashboard-2026-09-07.json).
- [x] `RT-001` — Zelfstandige runtime veilig herstellen (done).
  [Bewijs](evidence/product/RT-001-autonomous-admission-wait-2026-09-07.json).
- [x] `EV-003` — Eerlijke succescriteria vastzetten (done).
  [Bewijs](evidence/product/EV-003-comparative-acceptance-protocol-2026-09-08.json).
- [x] `UP-001` — Sterke oplossingen van concurrenten hergebruiken (done).
  [Bewijs](evidence/product/UP-001-phase-aware-streaming-2026-09-08.json).
- [x] `CO-007` — Menselijke gesprekroute compleet maken (done).
  [Bewijs](evidence/product/CO-007-live-answer-stream-2026-09-08.json).
- [x] `CO-006` — Geheugenbroncorrectie veilig in gebruik nemen (done).
  [Bewijs](evidence/product/CO-006-autonomous-memory-2026-09-08.json).
- [x] `TL-001` — Algemene toolroute meerdere stappen laten uitvoeren (done).
  [Bewijs](evidence/product/TL-001-candidate-build-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-conversation-chain-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-durable-model-usage-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-final-candidate-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-native-poller-stop-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-owner-stop-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-provider-stop-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-real-model-chain-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-runtime-serialization-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-storage-foundation-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-telegram-stop-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-turn-limit-2026-09-08.json).
- [x] `TL-002` — Handelingen via twee providers echt afmaken (done).
  [Bewijs](evidence/product/TL-002-completion-2026-09-08.json).
- [ ] `VB-001` — Full-duplex voice leveren (in_progress).

</details>

## Technische review blijft open

- [ ] `CO-003` — CO-003 blijft onder onafhankelijke review, niet afgevinkt of live. RT-001 herstelt nu de verschillende eigenaars- en geheugenscopes en herlaadt de privékoppeling per pagina. Een verse PostgreSQL-proef en een alleen-lezen proef met twee echte bronpagina's plus geweigerde andere eigenaar zijn groen; het oorspronkelijke CO-003-bewijs blijft historisch bewaard.
  [Bestaand bewijs](evidence/product/CO-003-owner-memory-sources-2026-09-07.json).

## Menselijke acceptatie blijft open

- [ ] `CO-002` — CO-002 en CO-007: Jaap meldde dat de gesprekken echt beter zijn; verdere gezinsbeoordeling van warmte, eigen oordeel en continuïteit blijft open. De antwoordstream is op 8 september technisch geïnstalleerd en door Telegram bevestigd. Er is geen nieuwe menselijke beurt of zichtbare animatiebeoordeling verzonnen; het oude incident wordt hier niet opnieuw geopend.

<details>
<summary>Werkafspraken en machineleesbare bron</summary>

## Vaste regels

- Afgeronde uitvoerkaarten staan eerst, daarna exact één primaire kaart, daarna alleen wachtrijkaarten.
- Alleen de primaire uitvoerkaart is nu uitvoerbaar; na afronding wordt precies één volgende productmijlpaal eerst tot een kleine kaart uitgewerkt.
- Een kaart noemt exacte bestanden, eigenaren en echte testcommando's.
- Een bouwer keurt zijn eigen taak niet goed; klaar vereist bewijs en onafhankelijke GO.
- Paden blijven in eva2 (of de twee gegenereerde inventory-uitgangen); tijdelijke data gaat via mktemp naar /tmp.
- /home/administrator/project/.eva2-artifacts is alleen een beheerd artifactarchief en vereist een expliciete kaart met eigenaar, doel, exact retentiepad en opruimmoment.
- Releaseherstel wordt compact met zijn evidence- en reviewreferenties afgesloten; daarna volgt productwerk volgens de vastgelegde meetcadans.
- Productcadans: kleine nulmeting → maintained upstream kiezen → bouwen/hergebruiken → gerichte functietest → passende praktijkproef → dezelfde nameting; pas een complete installatiekandidaat doorloopt de zware releasepoort.
- Een functionele kaart verwijdert alleen de code die zij binnen haar vooraf genoemde paden werkelijk vervangt; breder opruimwerk mag de gebruikersfunctie niet opslokken.
- De actief/reserveslot-installatie (voorheen A/B genoemd) is geen functietest of experiment: het werkende slot blijft actief terwijl pas één complete releasekandidaat eenmaal in het reserveslot wordt gecontroleerd en daarna eventueel actief gemaakt; kleine ontwikkelwijzigingen krijgen geen A/B-cyclus.

Machineleesbare bron:
[`design/product/execution-workboard-v1.json`](design/product/execution-workboard-v1.json).

</details>
