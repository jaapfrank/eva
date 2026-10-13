# Integratiecontrole EVA 2 — 12 september 2026

## Oordeel

Er is betekenisvol bewijs voor afzonderlijke ketens, maar nog geen gezamenlijke
regressiepoort die catalogusgroei, gelijktijdig spraak/geheugen/werk en herstel
op dezelfde samengestelde kandidaat controleert. Losse groene onderdelen en een
bereikbaar dashboard bewijzen dus niet dat EVA als geheel betrouwbaar werkt.
Dit is een read-only audit; geen modelaanroepen, runtimewijzigingen of nieuwe
gebruikersacceptatie. Alleen dit reviewdocument is toegevoegd.

## Wat daadwerkelijk gecontroleerd is

- PROJECTDOEL en WERKBORD gelezen: geheugen is geaccepteerd; overige toegestane
  ontwikkeling mag doorgaan naast open eigenaarproeven.
- TL-003 bewijscommit `9a6c4bb6` onafhankelijk gecontroleerd: één normale HTTP-beurt,
  vooraf afwezig endpoint, zeven geverifieerde stappen, exact opgeslagen bestand
  van 2465 bytes en teruglezen, één eindantwoord. Bekende GitMCP-aanbieder, bestaand
  gesprek en niet-uitgevoerd codevoorbeeld blijven expliciete beperkingen.
- `node --test eva2/tests/voice_service/client-lifecycle.test.cjs` opnieuw uitgevoerd:
  **58 geslaagd, 0 mislukt**, circa 344 ms. DOM/SDK-grenzen zijn gestubd: dit is
  geen fysieke telefoon-, echo- of schermvergrendelingsproef.
- `node --test eva2/deploy/chrome-bridge/gate.test.mjs eva2/deploy/chrome-bridge/stale-socket.test.mjs`
  opnieuw uitgevoerd: **6 geslaagd, 0 mislukt**, circa 157 ms. De uitgebreide
  Chromium-uitvalproef is als bestaand bewijs gelezen, niet opnieuw uitgevoerd.
- `mcp_gateway.py` en connector-tests geïnspecteerd: cataloguscontrole doet één
  `GET /tools`. Registratie gebruikt elders expliciet
  `include_pagination=false&limit=0`. Geen regressie voor een catalogus van 53
  tools/paginering aangetroffen in deze gedeelde gateway-tests. Dit ondersteunt
  de gemelde foutoorzaak, maar is geen onafhankelijke herhaling van het live-incident.
  Coördinator meldt inmiddels kandidaat `de689de95007c69a93397be022c9f2b2654c0af5`
  met review-GO en 124 gerichte controles; dat lopende herstel niet dupliceren.
  Deze audit heeft die geïsoleerde kandidaat niet opnieuw getest.
- Bestaande zware poort `tools/run_managed_b_required_tests.py` geïnspecteerd:
  bindt kandidaat, testbron, wheelhouse en browserharnas; bevat PostgreSQL-,
  portable restore-, action-chain-, browser- en living-storegroepen. Niet gedraaid:
  deze audit wijzigt geen databases en heeft geen volledige releasekandidaat ontvangen.

## Herbruikbaar bewijs en grenzen

| Bestaande keten | Bewijs | Wat nog niet door dit bewijs volgt |
| --- | --- | --- |
| Gewone opdracht → onderzoek → nieuwe connector → opslaan → teruglezen | `evidence/product/TL-003-unassisted-2026-09-12.json` | Geen onbekende aanbieder, herstart na registratie of algemene voorsprong |
| Telegram/API → twee helpers → aparte stop → correct antwoord en replay | `evidence/product/AU-002-concurrent-stop-mixed-2026-09-12.json`, livebewijs met dezelfde datum | 74 gerichte tests gebruiken gecontroleerd model en in-memory stores; geen garantie voor stop ná de laatste statuscontrole |
| Voice → echte relay → kernantwoord → sessie opruimen/herstarten | `evidence/product/voice-client-lifecycle-2026-09-12.json` | Live offline-event is gesimuleerd met netwerk nog beschikbaar; geen fysieke netwerkschakeling of gelijktijdige geheugenbewerking |
| Browseractie → transport-/procesverlies → oude toestemming geweigerd | `evidence/product/chrome-inflight-recovery-2026-09-12.json` en `chrome-proxy-recovery-2026-09-12.json` | Geen persoonlijke Chrome-acceptatie; losse herstelproef is geen gezamenlijke hostherstartproef |
| Kern/worker-update met behoud van andere services | `evidence/product/AU-002-concurrent-stop-live-2026-09-12.json` | Bewijst containeridentiteiten/readiness bij activatie, niet alle functies na groei van de gatewaycatalogus |
| Projectbron → desktop/mobiele status en proeflinks | `experiments/react-admin-dashboard-v1/verify-project.cjs` | Presentatiecontrole, geen bewijs dat een productfunctie werkt |

Runtimebinding in gelezen bewijs: kern/worker `542597c…`, media `bf214823…`.
Dat is historische bewijsbinding, geen in deze audit opnieuw geïnspecteerde live-status.
Een nieuwere runtime moet zijn eigen actuele manifest/readiness krijgen.

## Drie ontbrekende regressies, in volgorde

1. **Dynamische registratie → catalogusgroei → verse runtime-opbouw.** Sluit aan op
   het reeds lopende pagineringsherstel `de689de9`; geen tweede reparatie starten.
   Het aanvullende ketenbewijs moet via de normale registratieactie een catalogus
   van onder naar boven de paginagrens brengen en daarna de runtime vers opbouwen.
   Breid waar nog nodig de bestaande gatewayfixture uit
   met minstens 53 tools en een benodigde verzegelde tool op een latere pagina.
   Test zowel de werkelijke ContextForge-responsvorm als onvolledige pagina's,
   gewijzigde schemahash en duplicaten. Bouw de gewone runtime opnieuw op vanuit
   dezelfde registratiegegevens en voer één geldige read uit. Onvolledigheid moet
   een specifieke fout geven; nooit een groen readiness-signaal met ontbrekende tools.
   Voeg een kleine geïsoleerde gateway-integratieproef toe als contracttest, geen
   nieuwe gateway of groot framework.
2. **Eén persoon: spraak, geheugenwijziging en achtergrondwerk tegelijk.** Hergebruik
   voice-, owner-memory- en helperfixtures. Start werk, wijzig een synthetische
   voorkeur via de bestaande geheugenbediening, onderbreek alleen de audio en
   beëindig de mediasessie terwijl het werk doorloopt. Nieuwe beurt gebruikt de
   gecorrigeerde voorkeur; het werkresultaat is eenmaal opgeslagen en opvraagbaar;
   expliciete helperstop raakt uitsluitend de gekozen helper. Voeg een tweede
   persoon als negatieve scopecontrole toe. Eerst deterministisch met echte
   API/application/services en geïsoleerde PostgreSQL, daarna één begrensde
   gebruikersrouteproef op de gezamenlijke kandidaat als technisch bewijs ontbreekt.
3. **Herstel van de gezamenlijke installatie.** Hergebruik Chrome-herstel en de
   bestaande release-/restoreharnassen in een geïsoleerde kandidaat. Laat gateway-IP
   veranderen en kern/worker opnieuw starten terwijl een browseractie wacht en
   een helper een duurzame status heeft. Bewijs: geen late browserklik of hergebruik
   van grant/nonce, geen dubbel helperresultaat, geheugen behouden, voice/microfoon
   start niet vanzelf, owner- en projectstatus weerspiegelen dezelfde uitkomst.
   Geen echte eigenaarbrowser of productie-WSL afsluiten voor een dagelijkse test.

## Praktische poorten

**Per wijziging:** bestaande gerichte tests plus één relevante ketenregressie uit
bovenstaande drie. Bind het resultaat aan exact broncommit en kandidaatimage.
Daarna beperkte live-readback van de daadwerkelijk gewijzigde route. Markeer
gebouwd, getest, live en eigenaaracceptatie afzonderlijk.

**Dagelijkse integratiekandidaat, of na samenvoegen van meerdere componenten:**
hergebruik een vaste kleine selectie van gateway-startup, helperconcurrentie,
voice-lifecycle, geheugen-scope en browser-hersteltests. Laat deze draaien op één
versiemanifest met geïsoleerde stores en gecontroleerd model. Voeg read-only
catalogus/readiness/schema-/versiecontrole van de doelinstallatie toe. Dashboardtest
controleert uitsluitend dat deze uitslagen correct zichtbaar worden. Richtwaarde
is minuten, geen duizenden herhalingen; geen nieuwe modelcalls zonder concrete
onzekerheid die alleen een echte gebruikersroute kan oplossen. Dit is een voorstel,
nog geen ingeplande of geïmplementeerde dagelijkse automatisering.

**Volledige release:** gebruik de bestaande `run_managed_b_required_tests.py` met
actuele `release/managed-b-release-input-authority-v1.json`, gepinde dependencies,
exact kandidaatimage en geïsoleerde database-/restoregroepen. Controleer eerst of
de inventaris de huidige componenten dekt; historische vaste aantallen (162
uitvoerbare items, 20 ondersteuning, 7 groepen) zijn geen bewijs van actuele dekking.
Neem de drie ketens expliciet op waar relevant, plus herstel/terugweg en benodigde
fysieke eigenaarproeven. Alleen een geslaagde poort op die complete kandidaat mag
als releasebewijs gelden; losse overlay-GO's mogen die claim niet vervangen.

Geen nieuwe optionele scope of volledige testsuite voor iedere tekstwijziging.
Eerst regressie 1 afronden: juist catalogusgroei toont waarom de gezamenlijke
startcontrole meer waarde heeft dan opnieuw alle afzonderlijke demo's uitvoeren.
