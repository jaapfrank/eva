# Documentatie-afstemming — 12 september 2026

Begrensde read-only audit van primaire ingangen, eigenaarbesluiten en geselecteerde
beheercontracten. Alleen dit reviewdocument geschreven. Geen runtime-, model-,
update- of werkbordmutatie; geen actuele upstream-/CVE-webcontrole.

## Samenvatting

De nieuwste hoofdlijn is consistent: geheugen GE-01/GE-02 is geaccepteerd,
IN-02 blijft ter beoordeling, en overige reeds gewenste technische opleveringen
mogen doorgaan. De voornaamste verwarring zit in oudere documenten die nog
“actueel” of “huidige stand” zeggen terwijl hun componentkeuzes en bewijs ouder
zijn. Niet alle historische contractclaims moeten bijgewerkt worden: hun
herkomst en beperkingen moeten juist behouden blijven.

## Primaire bron per onderwerp

| Onderwerp | Leidende bron |
|---|---|
| Werkwijze, kwaliteit, kleine opleveringen, OSS-hergebruik | Root AGENTS.md, inclusief eigenaarbesluit 12 september |
| Productvisie, scope en recente afwijking van oudere volgorderegel | eva2/PROJECTDOEL.md, nieuwste besluiten bovenaan |
| Uitvoering en acceptatiestatus | eva2/design/product/execution-workboard-v1.json; WERKBORD.md en dashboard zijn weergaven |
| Opnemen/afwijzen/heropenen van optionele scope | eva2/design/product/optional-scope-decisions.json; uitsluitend gedocumenteerde helper verwerkt events |
| Gebruikersproeven | Actuele dashboardproef plus gedateerde eigenaarproefdocumenten en scopegebonden bewijs; geen acceptatie afleiden uit technical/live |
| Geïnstalleerde versies en beheer | Gedateerde live-receipts en betrokken deploy-README/config; historisch PROJECT_STATUS.md is geen live inventaris |
| Componentupdatevoorstellen | renovate.json en component-watch.json; configuratie bewijst niet dat een bot draait of een update veilig is |
| Build/SBOM/signatuur/intrekking | eva2/release/*-policy.json en bijbehorend actuele kandidaat-/releasebewijs; policy is geen uitvoerreceipt |
| Concurrentievergelijking | comparable-agent-benchmarks-v1.md plus native Inspect-bewijs; vergelijking-readiness-review maakt ontbrekend bewijs expliciet |

## Concrete verschillen en voorgestelde afhandeling

1. **README verwijst verkeerd naar actuele status.** eva2/README.md noemt
   PROJECT_STATUS.md “laatste livecontrole”, terwijl PROJECT_STATUS.md expliciet
   zegt geen tweede actuele runtimestatus bij te houden en vanaf 7 september
   historische controles toont. Verwijzing beschrijven als historische controles;
   actuele status bij de ene werkbron laten. Geen historische receipts wijzigen.
2. **OPEN_SOURCE_COMPONENTEN.md is van 1 september maar spreekt in huidige tijd.**
   Het noemt Pipecat eerste route/LiveKit alleen vergelijking, subagents bevroren,
   React-admin niet live en Pydantic2.36 als huidige eigenaar. De huidige
   werkbord-/voice-deploydocumenten tonen inmiddels LiveKit en werkende helper-,
   dashboard- en telefoonroutes. Dit is geen opdracht de architectuur terug te
   draaien: markeer oude beslisstand en verwijs per onderdeel naar actueel bewijs.
3. **component-watch is geen actuele inventaris.** checkedAt=31augustus en
   reviewValidUntil=30september maken de opgesomde versies niet actueel. Het noemt
   Pydantic2.36, PlaywrightMCP0.0.79 en oudere concurrentreleases; de september-4
   vergelijkingssnapshot bevat al nieuwere OpenClaw/Hermesversies. Scheid
   “laatst onderzocht”, “geïnstalleerd”, “kandidaat” en “nieuwste beschikbaar”.
   Een nieuwe versiecheck is aparte uitvoering; deze audit claimt die niet.
4. **Subagentontwerp heeft verouderde huidige stand.** delegation-and-workboard-v1.md
   is gedateerd 26augustus en noemt de route providerloos/niet live. De huidige
   AU-002-proeven en het werkbord tonen een live hulpagentroute. Behoud het
   ontwerp, label de toenmalige stand historisch en verwijs naar actueel bewijs.
5. **Twee betekenissen van werkbord.** dashboard-v1.md beschrijft producttaken als
   PostgreSQL-projectie; het projectontwikkelbord gebruikt execution-workboard-v1.json.
   Dit zijn verschillende gegevenssoorten. Documenteer het onderscheid om geen
   tweede actuele ontwikkelwachtrij of directe product-DB-writes te introduceren.
6. **Autonomiedocument noemt bredere betaal-/KYCmogelijkheden.** Dat is een
   productbelofte binnen afzonderlijk verleende scope, geen huidig mandaat.
   Jaaps actuele kosteloze opdracht en persoonsgrenzen blijven leidend; brede
   toekomsttekst mag geen betaling, accountrecht of verklaring autoriseren.
   De historische “actuele officiële Godmode”-beschrijving is geen actuele
   upstreambevinding van deze audit en hoort een controledatum te houden.
7. **Lokale links blijven localhost gebruiken.** README, PROJECT_STATUS,
   WERKBORD en voiceproef noemen localhost. Dit is niet algemeen fout, maar de
   eerdere Windows/IPv6-bereikbaarheidsfout maakt 127.0.0.1 een betere concrete
   lokale link waar die listener alleen IPv4 bindt. Geen telefoonlink naar het
   laptop-loopbackadres maken; telefoon gebruikt de bestaande HA/HTTPS-ingang.
8. **Releasepolicies bevatten bewust oude false-claims.** sbom-policy noemt
   format_selected_artifacts_absent, signature-policy keys_and_signatures_absent,
   build-policy no_build_executed en revocation-policy lists_absent. Zij kunnen
   ontwerp/foundationclaims zijn, niet huidige volledige productstatus. Niet
   blind true zetten op basis van een recente losse imageproef. Leg een helder
   ingangsoverzicht met policy versus per-artifact-receipt vast; controleer
   ontbrekende releasebewijzen bij een complete releasekandidaat.
9. **Installatiebelofte is nog geen ondersteund platform.** installer-and-onboarding
   noemt Linux/amd64 en Windows/WSL expliciet unvalidated/unsupported tot volledige
   fresh-install/update/restoreproef. Recente lokale reparaties en Composeupdates
   bewijzen niet ineens een universeel gebruikersinstallatiepakket. Oude
   restpunten eerst tegen actuele receipts controleren, niet stil verwijderen.

## OSS, updates en security: concrete betekenis van de bestaande afspraken

AGENTS eist eerst hergebruik van bestaande en onderhouden bouwstenen. Het eist
geen willekeurige vermindering van regels code. De oudere componentenlijst legt
sterkere nadruk op gemeten vervanging/verwijdering van overlap; lees dat binnen
het nieuwere criterium eenvoudig, betrouwbaar, functioneel en testbaar.

Renovate staat in de repositoryroot: voorstellen met dependencyDashboardApproval,
automerge=false, gepinde versies/digests en beperkte PRfrequentie. Een echte botrun
is in deze audit niet aangetoond. De configuratie activeert geen npm-manager,
terwijl React-admin en Chrome npm-lockfiles hebben. Dat is een concreet te
beoordelen dekkingsgat, geen bewijs dat updates daarvan onmogelijk zijn:
custom component-watch dekt releasemeldingen, maar niet automatisch de volledige
transitieve npm-lockfile. Vendor/build/evidence worden bewust genegeerd; vendored
LiveKit/HAassets vragen daarom aantoonbaar onderhoud in hun eigen fetch/lockroute.

De voice-deploy-README is duidelijker actueel: gepinde bron/clientchecksums,
requirementslock, licenties/notices en expliciet dat een geslaagde bouw geen
schone scan is. De beschreven securitydependencywijzigingen en oude afgewezen
binary blijven herleidbaar; deze audit heeft geen nieuwe kwetsbaarheidsscan gedaan.

De releasecontracten eisen onder meer SHA256-/licentiebinding en scan per
artifact, offline signatuurcontrole vóór extractie/start, sleutelrollen en
intrekking. Revocation-policy onderscheidt nieuwe activatie/update (verse lijst)
van herstart/rollback van eerder geverifieerde installatie; bekende intrekking
blokkeert altijd. Een nieuwe sourcehash of een gezonde service vervangt dat
bewijs niet. Andersom is een oud policyveld geen automatisch crashherstelverbod.

## Gelezen bestanden en afbakening

Volledig of inhoudelijk kerncontract gelezen:
- AGENTS.md; README.md.
- eva2/PROJECTDOEL.md: actuele besluiten/kwaliteitsafspraken bovenaan en eerder
  gelezen volledige-visiefragmenten; niet iedere historische paragraaf opnieuw.
- eva2/WERKBORD.md: actuele kop en afzonderlijke spraak/helper/wakeword/browserdelen;
  geen volledige oude kaartgeschiedenis opnieuw.
- eva2/README.md: primaire links en begin van gemarkeerde historische sectie.
- eva2/PROJECT_STATUS.md: primaire bronverklaring en eerste historische controles.
- OPEN_SOURCE_COMPONENTEN.md: hoofdregel, keuzetabel, overlap-/regelmetingsecties.
- renovate.json en component-watch.json volledig.
- eva2/design/product/{owner-autonomy-mode-v1,installer-and-onboarding-v1,
  delegation-and-workboard-v1,dashboard-v1}.md: openingsontwerp, huidige-standclaims,
  mandaat-/oplevergrenzen; niet alle latere historische bewijsdetails.
- eva2/experiments/react-admin-dashboard-v1/SCOPE-DECISIONS.md: contract,
  eventverwerking, exclusief claimen en lokale toegang.
- eva2/design/product/optional-scope-decisions.json: actuele CO-004/CO-005included;
  execution-workboard-v1.json: bronstructuur en benoemde memory/qualityauditvelden.
- eva2/deploy/voice-session/README.md: bouw/herkomst/configuratie en telefoonstatus.
- eva2/design/product/VOICE-owner-trial-2026-09-12.md volledig.
- eva2/release/{sbom-policy,signature-policy,revocation-policy}.json volledig;
  build-policy.json hoofdeisen/status.
- Eerder in deze auditopdracht: vergelijkingsprotocol, InspectREADME en
  CP/EV-bewijs; zie comparison-readiness-2026-09-12.md voor exacte reikwijdte.

Niet gelezen: alle historische testlogs, private gesprekken/geheugenpayloads,
secrets, iedere vendor/node_modulesfile, alle architectuur-/schema-/platformlocks,
alle oude releasereceipts of externe actuele repositories/CVEfeeds. release/README.md
bestaat niet; dit is een ontbrekende navigatie-ingang, geen ontbrekend contract.
De opgesomde verschillen zijn gerichte documentatiebevindingen; geen algemene
releasegoedkeuring of bewering dat alle bronbestanden beoordeeld zijn.