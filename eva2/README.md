# Eva 2

**[Open het projectdashboard](http://127.0.0.1:44712)** voor het visuele overzicht.

Volg het werk in **[WERKBORD.md](WERKBORD.md)**: wat nu gebeurt, wat klaar is
en welke gebruikersfuncties nog ontbreken. Dit is de enige uitvoerwachtrij.

Het [projectdoel](PROJECTDOEL.md) beschrijft de volledige ambitie.
[PROJECT_STATUS.md](PROJECT_STATUS.md) bewaart historische livecontroles en
productvergelijkingen. De actuele status en de bijbehorende gedateerde
bewijslinks staan in het werkbord; historische controles bepalen de werkvolgorde niet.
Gebruik [DOCUMENTATIE_EN_ONDERHOUD.md](DOCUMENTATIE_EN_ONDERHOUD.md) als documentatie-ingang.

De broncode staat in `src/eva/`, tests in `tests/` en installatiecode in
`deploy/`. Ontwerp, eerdere beoordelingen en bewijs staan respectievelijk in
`design/`, `reviews/` en `evidence/`. Je hoeft die niet door te nemen om de
voortgang te volgen.

<details>
<summary>Developer packaging: first WSL candidate</summary>

From this directory, stage the current Git-controlled source with immutable,
locally inspected Linux amd64 images:

```bash
python3 -m tools.stage_installer_package   --source-root /absolute/source/eva2   --target-root /absolute/new/package   --runtime-image sha256:EXACT_LOCAL_CORE_IMAGE_ID   --postgres-image sha256:EXACT_LOCAL_POSTGRES_IMAGE_ID   --hindsight-image sha256:EXACT_LOCAL_HINDSIGHT_IMAGE_ID
sudo python3 /absolute/new/package/start-installer.py
```

The launcher uses bundled Python3.12.14 and automatically selects the WSL
candidate lock. It keeps the invoking sudo owner's existing model authority;
on this host the registered Eva lease producer identifies the original source.
A fresh computer falls back to that owner's native Codex login. Refresh authority
is neither packaged nor copied into the new target. Existing comparison accounts
are not consulted.

Confirm the profile, verify the existing model route and confirm resource choices
in the ordinary wizard. For this first development text target, explicitly skip
Telegram, Home Assistant, email and the adaptive connector; their setup remains
O1.2. Choose a language matching the verified identity pack. Select **Installatie
uitvoeren**, then **Open de nieuwe Eva en begin een gesprek**. No target graph,
Docker IDs, port, token, DSN or settings file must be handwritten by the owner.
Sudo is currently required for the new runtime-owned files; operator permissions
are checked before provisioning.

The local development receipt binds actual source bytes and inspected images;
it does not manufacture production signatures or validated platform support.
Without all three image flags, ordinary public release support remains blocked.
New storage and namespace keep the existing installation's data separate. The
fresh core exposes only a loopback owner port; restore mode retains its closed
network. The wizard presents text through its existing owner session and the
core's bound credential. Voice and optional provider setup are not claimed by
this installation slice.

A failed transition retains its preparation receipt and exact trusted launch
graph for inspection; never automatically recreate the databases or restart the
whole installation. The dated O1.1 evidence discloses the actual targeted repairs,
streamed response, durable readback and remaining release limitations.

</details>

<details>
<summary>Historische technische toelichting en eerdere ontwikkelcontroles</summary>

Onderstaande tekst beschrijft eerdere ontwikkelfasen. Versies, routes en
statusclaims daarin zijn geen actuele installatie-instructies. Gebruik voor
de huidige stand het werkbord en de daarin gekoppelde gedateerde bewijsstukken.

Dit is de nieuwe, connectorneutrale Eva-runtime. Deze map deelt geen uitvoerbare code,
schema-eigenaarschap, secrets of processtate met Eva 1.

Tranche 4A blijft de immutable contract-, platform- en supply-chainbasis. De
huidige bronboom bevat daarnaast de minimale tranches 4B en 4C voor
PostgreSQL-atomiciteit:

- strikte, versiegebonden contracten en gegenereerde JSON Schema's;
- vaste Python-packagegrenzen;
- numerieke budgetten en een exact platformdesign;
- een hash-locked dependency- en supply-chaindesign;
- drie lineaire Alembic-revisions voor uitsluitend `eva_core.tenants`,
  `installations`, `episodes` en `episode_events`;
- een pure `accepted -> planning`-transition en een connection-injected
  SQLAlchemy Core-repository zonder eigen transactiebeheer;
- een hash-gebonden DBOS 2.30.0+eva.1 sync-datasource-overlay, een
  pre-migrated/no-DDL opener en één gesloten workflow-composition;
- echte PostgreSQL 16.13-proeven voor één fysieke head/event/result-commit,
  begrensde `40001`/`40P01`-pogingen, COMMIT-ambiguïteit, route-uitval en
  fresh-process recovery.

Tranche 4C bewijst uitsluitend die historische synchrone datasource- en
database-atomiciteitsslice. De async DBOS-datasource is niet ondersteund. Die
4C-receipts claimen daarom op zichzelf geen werkende agent, installer,
connector, modelintegratie, gebruikersgedrag of productieservice.

De bronboom bevat inmiddels wél de latere, afzonderlijk bewezen Living Core
onder `src/eva/`, de volledige B-stack onder `deploy/living-core/` en de actuele
live receipts onder `evidence/living-core/`. De enige actuele technische
waarheid over die runtime, haar bestandseigenaarschap en de nog open verticale
stappen staat in
[`design/architecture/living-core-v1.md`](design/architecture/living-core-v1.md).
Geen van die latere resultaten verruimt achteraf de claims van een ouder
4A/4B/4C-receipt.

De actuele B kan de onderhouden LocalTuya-bron als afzonderlijke, bewijsgebonden
stap registreren. Dat pad vereist een actueel plan, de letterlijke bron
`xZetsubou/hass-localtuya`, release `2026.7.0`, een kortlevende goedkeuring en
een onafhankelijke HACS-readback. Repositoryregistratie installeert geen pakket
en migreert geen apparaat; die effecten houden elk hun eigen toestemming.

De vijftien LAB-36-cases met primair track `core_live` hebben nu ieder een
hashgebonden live-B-bewijs en nul kritieke fouten in
[`lab36-core-live-evidence-index-v39-2026-08-24.json`](evidence/living-core/lab36-core-live-evidence-index-v39-2026-08-24.json).
`tools/check_lab36_core_live_evidence.py` controleert de catalogusdekking en
iedere receiptbyte fail-closed. Dit is de volledige core-live-slice, niet de
hele native-live- of zevendaagse resident-slice en dus geen algemene 36-case
productiescore. Native P03 en X01 zijn inmiddels afzonderlijk live groen: de
fan-out bewaart deeluitkomsten per ontvanger en hervat na een echte corecrash
met providerreadback zonder een al geslaagd effect opnieuw uit te voeren. Zeven
aanvullende native cases zijn daarna eveneens live gesloten; de native-live-
slice is compleet. De twaalf echte zevendaagse resident-cases blijven open.

De definitieve Telegramingang is een afzonderlijk, opt-in proces onder het
Compose-profiel `telegram-native`. Zij vereist een nieuw Eva 2-bottoken en een
strikt `EvaTelegramPrincipalBindings/v1`-bestand zoals
`deploy/living-core/telegram-principal-bindings.example.json`. Iedere binding
koppelt één Telegram-user plus toegestane chat(s) aan één unieke interne
principal. Het profiel mag niet tegelijk met Eva A op hetzelfde bottoken
long-pollen. Ingress, antwoordvoorbereiding, deliveryclaim en Telegramreceipt
worden append-only vastgelegd; een onzeker verzendvenster wordt niet blind
herhaald.

Actuele research is een afzonderlijke capability en vertraagt gewone chat niet.
Alleen bij een expliciete actuele vergelijking roept Eva de ingebouwde
webzoektool aan. Iedere benoemde optie moet een bijpassende primaire HTTPS-bron
hebben voordat PostgreSQL het Markdown-artefact commit. Het nieuwste document
kan binnen dezelfde tenant/installatie/principal-scope opnieuw worden
gedownload via `/internal/v1/research/latest/download`; de `ETag` is de SHA-256
van de werkelijke bytes.

Gespreks- en geheugenrequests mogen daarnaast een optionele `project_id`
dragen. Hindsight bewaart algemeen persoonsgeheugen in de bestaande
installatiebank en projectgeheugen in een afzonderlijke deterministische bank;
persoontags blijven binnen beide grenzen verplicht. Een projectbeurt leest dus
algemeen geheugen plus exact dat ene project, terwijl een gewone beurt nooit
projectgeheugen ziet. PostgreSQL bewaart dezelfde scope append-only bij ingress,
reply en async retain, zodat herstel na een crash niet naar een andere persoon
of ander project kan verschuiven.

Een beeldcorrectie is nog smaller gebonden. Alleen een beurt met een gevalideerde
`observation_binding` kan de gesloten identiteitscorrectie-action aanbieden. De
combinatie van mediahash en onderwerpregio krijgt een eigen Hindsight-bank en de
naam komt uitsluitend uit een expliciete gebruikerscorrectie, nooit uit
gezichtsinferentie. Een andere foto, regio of een ongebonden Voice PE-bericht kan
die naam daardoor niet erven.

Voor begrensd Markdown-werk bewaart Eva bovendien volledige taakcheckpoints in
de PostgreSQL-tijdlijn. Een deiktisch vervolg zoals “ga hiermee door” kan alleen
de direct actieve taak uit hetzelfde gesprek vervolgen; een oudere globale taak
wordt niet als terugval gebruikt. Ieder vervolg moet andere gevalideerde bytes
en een nieuwe SHA-256 opleveren. Een scopewijziging en afronding blijven als
afzonderlijke events zichtbaar en replay voert het model of werk niet opnieuw
uit.

Een optionele gemounte werkruimte voegt veilige tekstbatchverwerking toe. Eva
kan daarin maximaal twintig UTF-8-tekstbestanden naar Markdown omzetten. De
connector weigert absolute paden, escapes en symlinks, commit vóór schrijven
het volledige invoer- en verwachte uitvoermanifest en leest ieder resultaat
afzonderlijk terug. Daardoor blijven geslaagde bestanden bestaan als één
bestemming faalt, en kan Eva precies die ene blocker benoemen zonder een valse
alles-of-nietsclaim.

Een documenttaak mag bovendien pas `completed` worden als iedere Markdownsectie
daadwerkelijk inhoud bevat. De volledige bytes blijven in het laatste
PostgreSQL-checkpoint. Na procesvervanging wordt dus niet opnieuw begonnen:
Eva krijgt exact het onafgemaakte document, vult alleen het resterende werk aan
en commit een nieuwe hash. Een reply-replay leest daarna het bestaande antwoord
zonder tweede model- of artefactactie.

Tijdelijke fouten in de bestandsconnector krijgen maximaal drie duurzame
pogingen met één seconde afstand. Alleen expliciet tijdelijke foutklassen mogen
terugkomen. Iedere poging staat vóór de volgende in PostgreSQL; reeds
geverifieerde outputs worden op een retry alleen opnieuw gelezen. Na herstel
volgt één volledig resultaat, en na de derde blijvende fout één nauwkeurig
begrensde blocker met behoud van de overige bestanden.

Voor een gerichte tekstedit bestaat daarnaast een aparte validatiegrens. Eva
vervangt één uniek tekstfragment in één werkbestand, nadat een strikt
declaratief focused-testmanifest eerst groen is. Ze leest de edit terug en
behoudt haar alleen als de test groen blijft. Bij testfalen wordt uitsluitend
die ene edit atomisch teruggezet, draait dezelfde test opnieuw en bewijzen
voor/na-hashes dat ander werk bytegelijk bleef. Werkruimtecode of shellcommando's
worden daarbij niet uitgevoerd.

Veiligheid blijft een dunne grens rond echte risico's, geen tweede bestuurssysteem. Lezen,
redeneren, plannen en uitleggen krijgen geen onnodige toestemmingslagen. Alleen externe of
moeilijk omkeerbare handelingen, zoals schrijven naar accounts, publiceren, betalen, verwijderen
en credentialgebruik, passeren de bijbehorende begrensde effectroute.

## Lokale 4A-controle

De drie controles draaien vanuit de repositoryroot met een externe Pythonomgeving die exact
overeenkomt met `eva2/release/gate-toolchain.lock.json`:

```bash
<sealed-gate-python> eva2/tools/run_gate.py --gate CONTRACT-01
<sealed-gate-python> eva2/tools/run_gate.py --gate PLATFORM-DESIGN-01
<sealed-gate-python> eva2/tools/run_gate.py --gate SUPPLY-DESIGN-01
<sealed-gate-python> eva2/tools/finalize_4a_evidence.py
```

De runner installeert niets en gebruikt geen Eva 1-data, database, service of externe connector.
Een ontbrekende of afwijkende toolchain levert `not_proven` op in plaats van een groen resultaat.

## Tranche 4B PostgreSQL-gate

De lokale pure, contract-, architectuur- en statische tests starten geen database,
container, service of netwerkprovisioning. De PostgreSQL-integratietests vereisen
expliciet `EVA2_TEST_POSTGRES_DSN`. De URL moet wijzen naar een vooraf gemaakte,
lege, disposable PostgreSQL 16.13-database waarvan de naam begint met
`eva2_4b_disposable_`; de testrol moet de vier NOLOGIN-groepsrollen mogen maken.

```bash
PYTHONPATH=eva2/src python3 -m pytest -q -s \
  eva2/tests/domain \
  eva2/tests/postgres/test_4b_static_boundaries.py

EVA2_TEST_POSTGRES_DSN='postgresql+psycopg://.../eva2_4b_disposable_run1' \
PYTHONPATH=eva2/src python3 -m pytest -q -s eva2/tests/postgres
```

Een ontbrekende DSN, verkeerde serverversie, niet-lege database of niet-disposable
databasenaam faalt de integratiegate. Er is geen skip-naar-groen. De gate oefent
fresh create, stapsgewijze upgrade, een uitsluitend pre-traffic downgrade,
re-upgrade, RLS, privileges, repositorycommit/rollback en frameworkonafhankelijk
lezen. Een operationele rollback na verkeer en de DBOS-crashmatrix horen niet bij
4B.

Het bevroren 4B-bewijs staat in
[`postgresql-core-gate.json`](evidence/4b/postgresql-core-gate.json):
38 PostgreSQL-proeven en 139 gecombineerde pinned-environmentproeven zijn groen.
Het receipt claimt uitdrukkelijk nog geen DBOS-, model-, connector- of
gebruikersgedrag.

## Tranche 4C PostgreSQL/DBOS-gate

De 4C-gate draait in de exact bevroren Python 3.12.14-omgeving met de 4A/4B
requirements-locks en vervangt daarin uitsluitend de stock DBOS-wheel door de
hash-gebonden `2.30.0+eva.1` sync-datasource-overlay. De 41 proeven gebruiken
een lege disposable PostgreSQL 16.13-database en bewijzen alleen de hierboven
afgebakende database-atomiciteit, begrensde retry en crashrecovery.

Het bevroren resultaat staat in
[`postgresql-durability-gate.json`](evidence/4c/postgresql-durability-gate.json).
Het claimt geen async datasource, connector-, model-, effect-,
gebruikers- of productiegedrag.

</details>
