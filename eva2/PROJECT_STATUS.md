# Eva 2 — actuele voortgang

Laatst bijgewerkt: 2026-08-25

Dit is het korte meekijkdocument. Ik werk het bij na een afgeronde verticale
stap en uiterlijk bij de volgende uurupdate. De technische waarheid blijft in
[`design/architecture/living-core-v1.md`](design/architecture/living-core-v1.md);
de korte lijst met mogelijkheden, gaten en concurrentiestand staat in
[`EVA2_MOGELIJKHEDEN_EN_GATEN.md`](EVA2_MOGELIJKHEDEN_EN_GATEN.md); de verplichte
volledige productvergelijking en hergebruikroute staat in
[`CONCURRENTIE_EN_HERGEBRUIK.md`](CONCURRENTIE_EN_HERGEBRUIK.md); de verplichte
oude-naar-nieuwe behoudgate voor spontane huisaanwezigheid staat in
[`OUDE_EVA_VS_EVA2.md`](OUDE_EVA_VS_EVA2.md); de verplichte
OpenBot-vergelijking en autonomie-afvinklijst staan in
[`OPENBOT_VERGELIJKING.md`](OPENBOT_VERGELIJKING.md); de verplichte
full-duplex voice-mode, kanaalkeuze en latencygates staan in
[`design/product/conversational-voice-v1.md`](design/product/conversational-voice-v1.md);
de harde code-, dependency-, image- en reactietijdbudgetten staan in
[`design/product/performance-and-complexity-v1.md`](design/product/performance-and-complexity-v1.md);
het corrigeerbare persoonsmodel, de groeicyclus en de geheugen-, token- en
kostenbudgetten staan in
[`design/product/learning-and-person-model-v1.md`](design/product/learning-and-person-model-v1.md);
de eenvoudige installatie, lokale koppelwizard, `SOUL.md`/`MEMORY.md`-grens en
verplaats-/updateroute staan in
[`design/product/installer-and-onboarding-v1.md`](design/product/installer-and-onboarding-v1.md);
de documentatiekaart, componentregistratie, bewijsstatus en optimalisatiegrens
staan in [`DOCUMENTATIE_EN_ONDERHOUD.md`](DOCUMENTATIE_EN_ONDERHOUD.md);
de verplichte
teamvolgorde staat in [`TEAM_WERKWIJZE.md`](TEAM_WERKWIJZE.md);
de controleerbare live receipts staan in
[`evidence/living-core/`](evidence/living-core/).

## Nu

- Bezig: MC03b2b, de al eenmalig geclaimde read buiten de databasetransactie
  via de bestaande providergrens uitvoeren en de waarneming of exacte fout
  duurzaam teruglezen. Effectstappen en het vrijgeven van afhankelijke stappen
  blijven daarna aparte opleverstukken.
- Nieuw vastgelegd productdoel: ChatGPT-achtige full-duplex voice met streaming
  STT/TTS, onderbreken en dezelfde Eva-context. De voorkeur is `Bel Eva` in een
  Telegram Mini App; Home Assistant Assist/Wyoming is de lokale fallback. De
  huidige kern is Python 3.12. Rust is alleen een mogelijke geïsoleerde
  audio-dataplane wanneer metingen bewijzen dat dit nodig is; er loopt geen
  Rust-herschrijving.
- Omvang/reactiviteit PC00: Git-checkpoint `35e2c0d…` bindt 51.928 regels in
  133 productbestanden aan een exact bronmanifest; de dirty werkboomobservatie
  lag tijdens ontwerp op 53.428 niet-gegenereerde regels. V1 heeft een harde grens van 75.000
  handgeschreven productregels, maximaal twaalf directe coredependencies en
  p95-doelen voor acknowledgement, eerste inhoud en voice-barge-in. PC01-PC06
  maken teller, hot-path, sidecars, eindmeting en degradatiebewijs af.
- Leren/persoonsmodel LG00: Eva's bestaande persoonsgebonden Hindsight-geheugen,
  correcties, M01-continuiteit en I02-verbetering zijn van een volledige
  Hermes-achtige leercyclus onderscheiden. LG01-LG07 maken een inzichtelijk,
  corrigeerbaar persoonsmodel, token-/kostenreceipts, fysieke geheugenquota,
  store-onafhankelijke on-the-fly skillcreatie, skillquarantaine, evaluatie,
  promotie en rollback af. Tot die tijd is Eva niet
  aantoonbaar een agent die procedureel steeds beter wordt.
- Installer/onboarding IN00: technisch bestaan schema-installatie,
  managed-B-manifest, A/B-beheer, offline imagebouw en portable export, maar er
  is nog geen eenvoudige setupwizard of bewezen restore op een andere computer.
  IN01-IN08 maken één lokale wizard, IdentityPack/`SOUL.md`, Telegram, Home
  Assistant, e-mail, secretinvoer, fresh install, restore en update af.
- Adaptieve connectoren AC01-AC06: Telegram blijft de eerste V1-bewijsroute,
  maar niet de productgrens. Eva moet vanuit de chat een ontbrekend legitiem
  kanaal zoals WhatsApp kunnen contracteren, via actuele officiële documentatie
  een provider kiezen, zo nodig een kleine adapter in quarantaine maken en die
  na tests en least-privilege koppeling zonder core-rebuild activeren. Dit werkt
  nu nog niet.
- Documentatiebeheer DG01-DG06: mogelijkheden, gaten, architectuur, reviews en
  het mechanische padinventaris bestaan al, maar semantisch componentownership
  en de labels `complete`/`optimized_for_<metric>` worden nog niet repositorybreed
  machine-afgedwongen. Het nieuwe onderhoudscontract maakt die open grens en de
  benodigde checker/eindsnapshot expliciet.
- Laatste afgeronde productcheckpoint: OpenBot `v0.0.4` en de actuele gepinde
  documentatie zijn onafhankelijk geïnventariseerd. OpenBot documenteert een
  veel breder computer-, browser-, takeover- en beheeroppervlak, maar levert via
  AG-UI een agent aan en bewijst geen Eva-achtige gezinsidentiteit, fysieke
  readback of huispresence. `OB00` is klaar; `AU01-AU08` maken de ontbrekende
  echte computerautonomie nu zichtbaar en afvinkbaar.
- Laatste afgeronde kleine checkpoint: MC03b2a maakt na een actuele hercontrole
  precies één duurzame `ACTION_CHAIN_READ_PROVIDER_CALL_STARTED`-claim. Alleen
  de eerste caller krijgt het exacte private RPC-pakket; concurrency en een
  nieuwe database-engine krijgen daarna geen callable bytes meer. De eerste
  review vond nog een publieke reconstructiehelper; die is verwijderd en de
  herreview gaf `GO`. Checkpoint `376112f` voegt netto 446 productregels toe;
  32 ketentests en 24 tijdlijntests zijn groen. Er is nog geen providerproces
  gestart en geen waarneming opgeslagen; zie
  `reviews/action-chain-read-call-claim-mc03b2a.md`.
- De eerdere run `resident-long-2026-08-24-a` is na review teruggezet naar
  shakedown. Hij verzamelt echte HA-events, maar telt niet als acceptatierun.
- Eerstvolgend: MC03b2b voert alleen de eenmalig geclaimde RPC buiten de
  databasetransactie uit en legt succes, exacte fout of ambigu resultaat
  duurzaam vast; een onafhankelijke reviewer controleert ook die slice.
- Productie A: actief en niet gewijzigd.
- Actuele B: `eva2-living-core:cross-channel-v53-20260824`, core ready,
  duurzame-doelenworker running met nul herstarts.
- Eindacceptatie is uitgebreid maar nog niet gestart: naast core 15/15 en
  native 9/9 moeten echte multi-persoon-, multi-beurt-, multi-tool-,
  crashherstel-, self-improvement-, presence- en autonome computerproeven groen
  zijn, plus de echte voice-mode V01-V07. De norm staat in
  [`design/validation/lab-36-final-acceptance-addendum-v1.md`](design/validation/lab-36-final-acceptance-addendum-v1.md).

## Afgerond en live bewezen

- De eerdere drie-case concurrentenmeting is eerlijk begrensd en uitgebreid met
  een 22-categorie productmatrix en hergebruikarchitectuur voor ClawHub,
  OpenClaw Gateway/nodes, MCP, Hermes en Agent Zero. De onafhankelijke reviewer
  gaf PASS. De fail-closed gate blijft rood zolang niet iedere categorie tegen
  iedere concurrent met een gehashte volledige `won`-receipt is bewezen.
- LAB-36 core-live: 15/15.
- De draaiende v53-B-bron is onafhankelijk vastgelegd zonder secrets of
  containerwijzigingen: 377 bestanden, 363 gelijk, 8 afwijkend en 6 alleen in
  de werkboom. Dit is herkomstbewijs, geen release-freeze of live-acceptatie.
- Historisch native bewezen: P03, X01, A01, A02, A03, I02, F03, F04 en M01.
  Deze receipts zijn verdeeld over v43, v49/v50, v52 en v53; één bevroren
  eindkandidaat moet de volledige 9/9-regressie nog doorlopen.
- A01/A02: zelfstandig wachten, tussenvraag verwerken en daarna precies één
  effect plus levering.
- A03: extern bewijs opnieuw lezen en inhoudelijk pivotten.
- F03: externe blocker begrensd naar `needs_help`, zonder verkeerd effect.
- F04: cancellation en delivery failure technisch en menselijk onderscheiden.
- I02: zonder voorgeschreven fix drie echte fixtureproblemen gewogen, zelf de
  uitgeschakelde transient retry gekozen, één patch behouden en gerichte
  readback plus behoud van ongerelateerd werk bewezen.
- M01: een voorkeur kwam via de definitieve Telegramconnectorcode tegen een
  lokale Bot API-contractprovider binnen en werd
  later via een nieuwe webconversatie voor dezelfde vertrouwde persoon
  herinnerd; een tegengestelde voorkeur van een tweede persoon lekte niet mee.
- Regressie uit de echte F04-proef: terminale `delivery_failed`-staat veroorzaakte
  een worker-herstartlus; hersteld, getest en als v50 live gezet.

## Nog open in deze acceptatiefase

- Core-live: 15/15 opnieuw op dezelfde bevroren eindimage.
- Native: 9/9 opnieuw op één bevroren eindimage.
- Multi-persoonidentiteit: broncode ondersteunt meerdere accounts per persoon,
  tweezijdig koppelen en replayveilige groepsconsent. De beheerde B-deploy en
  echte multi-persoon/groepstest ontbreken nog; WhatsApp is niet v1.
- Eindaddendum: MX01-MX06 moet na de 15/15 en 9/9 op dezelfde image slagen:
  parallelle personen, groepsprivacy, meerbeurts redeneren, toolketen met
  readback, crash midden in een keten en begrensde self-improvement.
- Resident-track: een nieuwe officiële zevendaagse run op die eindimage; de
  huidige v53-shakedown heeft nul geslaagde residentcases.
- Presence-behoud: de bestaande spontane deurbel-, intercom- en speakerbeleving
  van Eva A moet via P01-P05 op Eva2 terugkomen. Brede multimedia is niet V1,
  maar deze minimale bestaande huisaanwezigheid blokkeert de overgang wel.
- Conversationele voice: V01-V07 staan open voor full-duplex audio, barge-in,
  lage p95-latency, Home Assistant-fallback en de voorkeurroute `Bel Eva` via
  een Telegram Mini App. Voice notes tellen niet als deze gate; native bellen
  door een Bot API-bot is niet beschikbaar.
- Omvang en snelheid: PC00 is vastgelegd; PC01-PC06 staan open voor een
  commitgebonden budgetchecker, begrenzing van grote hot-pathbestanden,
  streaming tekstrespons, aparte sidecarbudgetten en een eindmeting met minstens
  200 tekstbeurten. Boven 75.000 eigen productregels is de V1-gate rood.
- Leren en persoonsmodel: LG01-LG07 staan open. Eva moet per geverifieerde
  persoon kunnen tonen, corrigeren en vergeten wat zij gebruikt; iedere
  modelcall krijgt token-, latency- en kostenusage, iedere geheugenscope een
  fysiek quotum en iedere geleerde procedure baseline/held-out-evaluatie,
  versie en rollback. De zevendaagse gate weigert profiel-, context-, token-,
  kosten- of latencygroei buiten de vastgelegde grenzen.
- Installatie/onboarding: IN01-IN08 staan open. De huidige operatoronderdelen
  zijn geen consumenteninstaller. V1 vereist een ondertekend pakket met lokale
  wizard, zonder handmatige YAML/JSON/env-edit, plus least-privilege koppeling
  van Telegram, Home Assistant en één e-mailprovider, restore op een andere
  computer en A/B-update/rollback.
- OpenBot/autonomie: `OB00` is klaar. `AU01-AU08` staan open voor een duurzaam
  chatmandaat, algemene plan-act-observeer-herplan-runner, één geïsoleerde
  browsercomputer, Eva's eigen policy/ledger/readback, live pauze/stop/overname,
  een echte afgeschermde webtaak, crashherstel en zeven dagen praktijkbewijs.
- Security: het HA-token van B staat nog als inspecteerbare container-env. Het
  moet worden ingetrokken, opnieuw uitgegeven en uitsluitend als file-secret
  worden gemount.
- Proactieve uitgang: zeven oude `expression_pending`-items blijven fail-closed;
  geen kanaal wordt geopend vóór TTL, policyversie en activation cutoff bestaan.
- Git: de worktree is echt Git, maar 27 gewijzigde en 103 untracked Eva2-paden
  hebben nog geen opgesplitste, onafhankelijk gecontroleerde checkpoints.
- Externe gates die op aparte configuratie of toestemming wachten: echt Eva 2
  Telegram-token/bindings, Stripe-testaccount, nieuw AgentMail-secret en de
  afzonderlijk goed te keuren LocalTuya-mutatiestappen.

## Laatste receipts en reviews

- [`live-b-native-a01-a02-durable-goals-v50-2026-08-24.json`](evidence/living-core/live-b-native-a01-a02-durable-goals-v50-2026-08-24.json)
- [`live-b-native-a03-f03-goal-pivot-blocker-v50-2026-08-24.json`](evidence/living-core/live-b-native-a03-f03-goal-pivot-blocker-v50-2026-08-24.json)
- [`live-b-native-f04-terminal-goals-v50-2026-08-24.json`](evidence/living-core/live-b-native-f04-terminal-goals-v50-2026-08-24.json)
- [`live-b-native-i02-self-chosen-repository-improvement-v52-2026-08-24.json`](evidence/living-core/live-b-native-i02-self-chosen-repository-improvement-v52-2026-08-24.json)
- [`live-b-native-m01-cross-channel-continuity-v53-2026-08-24.json`](evidence/living-core/live-b-native-m01-cross-channel-continuity-v53-2026-08-24.json)
- [`resident-long-run-v1-2026-08-24.json`](evidence/living-core/resident-long-run-v1-2026-08-24.json)
- [`live-b-v53-source-provenance-2026-08-24.json`](evidence/living-core/live-b-v53-source-provenance-2026-08-24.json)
- [`openbot-competitive-inventory-ob00.md`](reviews/openbot-competitive-inventory-ob00.md)
- [`voice-performance-product-gates-v1.md`](reviews/voice-performance-product-gates-v1.md)
- [`performance-complexity-pc00-2026-08-25.json`](evidence/product/performance-complexity-pc00-2026-08-25.json)

## Betekenis van status

- `bezig`: code en echte B-proef zijn nog niet allebei groen.
- `live bewezen`: ontwerp, code, gerichte tests, echte B-serviceproef en receipt
  zijn aanwezig.
- `wacht extern`: technisch pad blijft fail-closed tot de genoemde toestemming,
  credential of providerconfiguratie bestaat.
