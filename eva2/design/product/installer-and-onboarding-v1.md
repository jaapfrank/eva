# Eva installer en onboarding v1

Bijgewerkt: 27 augustus 2026.

## Productbelofte

Eva moet op een andere computer te installeren zijn zonder dat de gebruiker
Compose-YAML, JSON-bindings of env-bestanden handmatig hoeft te schrijven. Eén
ondertekend installatiepakket start een lokale wizard, stelt begrijpelijke
vragen, bewaart secrets buiten repository en image, voert preflights uit en
bewijst iedere gekozen koppeling afzonderlijk. De wizard toont startopties,
maar is geen vaste connectorcatalogus: vanuit de chat kan Eva later via
`AC01-AC06` ook een niet-vooraf-ingebakken kanaal voorbereiden en toevoegen.

De eerste beoogde producttargets zijn een eigen Linux/amd64-machine en Windows
met WSL2 en een lokale Docker/Compose-engine. Geen van beide is nu al een
ondersteund installatieplatform: de platformlock markeert de targets nog als
`unvalidated`/`unsupported`. Ieder platform, inclusief later `arm64` of macOS,
krijgt pas het label ondersteund nadat dezelfde fresh-install-, update- en
restoreproef daarop werkelijk groen is. De huidige offline kandidaat heeft
uitsluitend de buildconstraint `linux/amd64`.

## Eerlijke huidige stand

- Er zijn een fail-closed schema-installer, een managed-B-render/preflight,
  private file-secretcontracten, een A/B-operator-CLI, offline imagebouw en
  afzonderlijke componentproeven. De fresh-installreceipt bouwde een verse
  PostgreSQL/core op maar hergebruikte een bestaande externe Hindsight-service
  en modelautoriteit; zij bewijst geen volledige lege managed stack of adoptie.
- Er is een canonieke, gesaneerde portable exportdirectory die reeds
  versleutelde objectpayloads kan meenemen. Een nieuwe, geteste transportlaag
  versleutelt nu de **hele** gevalideerde export als één bestand en herstelt
  hem pas na wachtwoord- én integriteitscontrole. Een verkeerd wachtwoord of
  gewijzigde byte publiceert geen halve restore. Een kleine operator-CLI leest
  het wachtwoord uitsluitend uit een owner-private `0600`-file, nooit uit een
  argument of receipt. Dit is nog geen complete gebruikersproef of
  database-import op een tweede computer.
- Een directe `INSERT` van de portable records is bewust **verboden**. Vier
  secretdragende recordtransformaties vervangen credential- en
  connectorverwijzingen door een onomkeerbare, geredigeerde variant. Dat is
  nodig zodat een transportbestand geen werkend providersecret bevat. IN07
  vereist daarom een eigen restore-adapter: persoonsgegevens, gesprekken en
  geheugen komen terug in een lege doelinstallatie; connectoren krijgen de
  status “opnieuw koppelen”; open provideracties worden niet hervat totdat een
  nieuwe, beperkte autorisatie en readback bestaan.
- De eerste decoder en writer voor die adapter bestaan nu: zij accepteren
  kolompreserverende records met de exacte contract-, hash- en primary-keybinding
  uit de export. Een geredigeerde **connector** is de ene veilige uitzondering:
  hij komt alleen terug als “opnieuw koppelen”, met een nieuwe onbruikbare
  plaatshouder in plaats van het oude secret. De action-chain weigert zo'n
  koppeling vóór er een intent, providercall of retry ontstaat; dit is op een
  verse PostgreSQL 16.13-database uitgevoerd. Geredigeerde model-, read- en
  effectpogingen blijven hard blokkeren totdat hun geschiedenis apart en veilig
  is vertaald. Oude modelcalls worden als niet-hervatbare `ambiguous`
  auditgeschiedenis teruggezet; afgeronde read- en effectpogingen bewaren hun
  eindstatus maar krijgen alleen een nieuwe onbruikbare credentialmarkering.
  De restore-admission eist bovendien een serialiseerbare
  transactie, één exclusieve herstel-lock en nul rijen in alle 39 canonieke
  tabellen. De writer controleert de volledige archiefset vóór de eerste
  targetwrite, gebruikt een savepoint en draait die writes bij een databasefout
  terug. Zie
  `evidence/living-core/portable-column-restore-disposable-postgres-2026-08-27.json`
  en
  `evidence/living-core/portable-connector-reauthorization-restore-2026-08-27.json`
  en
  `evidence/living-core/portable-model-terminal-restore-postgres-2026-08-27.json`.
  Dezelfde tijdelijke PostgreSQL-proef is nu ook groen voor een al afgeronde
  read- en effectpoging: hun audituitkomst en result-hash blijven bewaard,
  maar de oude credential wordt uitsluitend een onbruikbare markering. Zie
  `evidence/living-core/portable-terminal-attempt-restore-postgres-2026-08-27.json`.
  Het archief neemt actieve versleutelde objectbytes al mee en valideert hun
  envelope, hash en metadata. De databasewriter weigert echter vóór de eerste
  doelquery zodra zo'n object nog niet via een aparte, geverifieerde CAS- én
  sleutelmigratie kan worden neergezet. Dat voorkomt een ogenschijnlijk groen
  herstel met onleesbare bestanden. De ontvangende installatie kan nu wel één
  lokale, private keyring lezen: alleen exact passende tenant-, installatie- en
  sleutel-ID's uit een niet-gelinkt `0600`-bestand worden geaccepteerd. De key
  zit nooit in het archive. De eigenlijke bron→doel-sleuteloverdracht, CAS-write
  en tweede-machineproef blijven open stappen; zie
  `evidence/living-core/private-object-keyring-2026-08-27.json`.
- Vóór een actief versleuteld object ooit hersteld mag worden, controleert de
  doelinstallatie nu alleen-lezen de originele archiefhash, het vaste CAS-pad,
  de versleutelde byte, metadata, binding en doelkey. Een verkeerde key of
  gewijzigde byte stopt vóór een CAS- of databasewrite. Die controle blijft
  werken als Eva later ongerelateerde exportvelden toevoegt; een geldig oud,
  verzegeld archief wordt daardoor niet onleesbaar. Dit is nog **geen**
  sleuteloverdracht, objectkopie, databaserecovery of tweede-machinebewijs; zie
  `evidence/living-core/portable-object-target-key-preflight-2026-08-27.json`.
- Na die controle kan de ontvangende installatie het object nu ook één keer
  veilig in haar eigen private opslag zetten. Eerst wordt een klein, gefsynct
  herstelbewijs lokaal bewaard; na een crash hervat Eva precies die tijdelijke
  byte of leest zij de al geplaatste byte opnieuw terug. Een verkeerd object of
  een verkeerde key maakt geen doelmap, database-entry of tweede kopie. De
  opslagroute schrijft bewust nog **geen** `ObjectRef` in PostgreSQL: een aparte
  databasekoppeling moet straks alleen een voltooid opslagbewijs toelaten. Zie
  `evidence/living-core/portable-object-cas-materialization-2026-08-27.json`.
- Die databasekoppeling is nu gesloten: een actieve `ObjectRef` wordt vóór de
  eerste SQL-query afgewezen tenzij exact dezelfde archiefregel én ciphertext
  aantoonbaar in de **geconfigureerde** doelopslag zijn geplaatst. Daarna is
  één regel in een wegwerp-PostgreSQL geschreven en teruggelezen. Een bewijs
  voor een andere doelmap wordt geweigerd. Dit is een afgerond
  object-opslag→ObjectRef-bouwblok, maar nog geen complete 39-tabellenrestore,
  tweede-machineproef of sleuteloverdracht; zie
  `evidence/living-core/portable-objectref-cas-bound-postgres-restore-2026-08-27.json`.
- De restore leest een portable archief nu ook correct zoals het werkelijk is
  opgeslagen: canonieke JSON sorteert objectvelden alfabetisch. Eva controleert
  daarom de **exacte veldenset**, primaire sleutel en rijhash, terwijl alleen de
  verzegelde transform bepaalt in welke volgorde de databasekolommen worden
  geschreven. Een oude reader-fixture met onderling verschillende test-ID's
  blijft terecht vóór de eerste SQL-query geweigerd; die is geen echte
  databaseback-up. Zie
  `evidence/living-core/portable-canonical-key-order-restore-2026-08-27.json`.
- Een schone PostgreSQL-installatie is nu ook helemaal tot de actuele Eva-
  schemaversie opgebouwd en heeft alle 39 hersteldoelen gecontroleerd. Een
  volledig **leeg** archief wordt daarin transactioneel toegelaten. Dat bewijst
  de verse installatie en de complete doelbasis, niet dat er al een echte
  niet-lege 39-tabellenback-up is teruggezet. Een leeg archief laadt bovendien
  geen objectcryptografie; die dependency blijft alleen bij echte versleutelde
  objecten nodig. Zie
  `evidence/living-core/portable-fresh-full-schema-empty-restore-2026-08-27.json`.
- Dezelfde route is nu ook met een **niet-leeg**, volledig synthetisch
  bronarchief doorlopen naar een tweede nieuwe PostgreSQL-database. Identiteit,
  gesprek, bericht, geheugen en bewijs kwamen terug; de portable exportrol zag
  uitsluitend de juiste tenant en installatie. De tijdelijke databases zijn na
  afloop verwijderd. Dit is nog geen tweede fysieke machine of herinvoer van
  secrets/connectors. Alembic gaf bij de driftcontrole haar bekende waarschuwing
  over cyclische foreign keys; de drie herstelchecks waren groen, maar die
  waarschuwing blijft onderhoudswerk. Zie
  `evidence/living-core/portable-nonempty-cross-database-restore-2026-08-27.json`.
- De export zelf weigert voortaan een moment waarop Eva nog externe werk kan
  uitvoeren: een gereserveerde of geautoriseerde model-/read-/effectcall,
  actieve planstap, geclaimde aandachtstaak of nog niet afgeleverde melding
  maakt geen portable backup. Daardoor kan een herstelde Eva nooit uit een
  backup verdergaan met een oude provideractie. Doelen die alleen wachten mogen
  blijven bestaan; zij bevatten op dat moment geen uitvoerbare externe call.
  Dit is op de hele 39-tabellenroute met een tijdelijke PostgreSQL 16.13-proef
  gecontroleerd; zie
  `evidence/living-core/portable-quiescent-external-work-2026-08-27.json`.
- Er is **geen** gebruiksvriendelijke productinstaller, lokale setupwizard of
  volledig connector-onboardingpad. De platformbasis markeert
  `installerExists` nog expliciet als `false`.
- Telegram, Home Assistant en AgentMail hebben afzonderlijke adapters of
  deploymentdelen, maar de gebruiker moet configuratie en secrets nu nog als
  operator voorbereiden. Generieke IMAP/SMTP- of OAuth-emailonboarding is niet
  aanwezig.

## `SOUL.md`, `MEMORY.md` en Eva's echte autoriteiten

Er bestaan geen vrij wijzigbare `SOUL.md`- of `MEMORY.md`-runtimebestanden.
De lokale wizard kan een kleine `SOUL.md` exporteren, als voorstel omzetten
naar een gebonden staged IdentityPack-diff, privé bewaren, buiten de
Eva-runtime ondertekenen en na een aparte bevestiging atomair activeren of
één versie terugzetten. De privésleutel komt nooit in de browser of runtime.

| Bekend patroon | Eva-equivalent | Waarom |
|---|---|---|
| `SOUL.md` | Ondertekend, versieerbaar `IdentityPack/v1` | Naam, waarden, temperament, humor, toon, grenzen en initiatief zijn structureel valideerbaar; een prompt of plugin kan Eva's identiteit niet stil overschrijven |
| `MEMORY.md` | PostgreSQL-tijdlijn plus persoons-/projectgescopeerd Hindsight | Eén plat bestand kan concurrerende updates, herkomst, correcties, consent, meerdere personen, erasure en crashherstel niet betrouwbaar dragen |

De wizard mag wel een mensvriendelijke `SOUL.md` importeren of exporteren. Een
import wordt eerst een staged IdentityPack-draft, toont een diff en wordt pas na
validatie en ondertekening actief. De runtime leest nooit rechtstreeks een
vrij wijzigbare `SOUL.md`.

Een `MEMORY.md` is alleen een expliciete, gescopeerde export voor de persoon die
de gegevens mag zien. Hij is geen runtimeautoriteit en bevat standaard geen
groepsgegevens, secrets, verborgen inferenties of gegevens van anderen. Import
gaat via dezelfde provenance-, audience-, correctie- en erasuregrenzen als een
nieuwe herinnering; een bestand kan nooit principals, bindings, consent of
permissions aanmaken.

## Vragen in de lokale wizard

1. Installatienaam, eigenaar, taal, tijdzone en ondersteund platform.
2. Eva's naam, toon, waarden, initiatief en grenzen; optioneel een bestaande
   `SOUL.md` als staged draft importeren.
3. Modelprovider en authenticatieroute, met een bereik-/kostenproef zonder het
   secret te tonen of loggen.
4. Telegram optioneel koppelen: BotFather-token via eenmalige lokale invoer,
   privéaccount pairen, testbericht, daarna optioneel extra personen en groepen.
5. Home Assistant optioneel koppelen: interne URL, apart token, eerst
   alleen-lezen inventaris, expliciete entiteiten/domeinen en pas daarna
   afzonderlijke effectrechten.
6. E-mail optioneel koppelen: in v1 eerst één gepinde providerroute en read-only
   mailboxproef. Lezen, verzenden, verwijderen en bijlagen zijn afzonderlijke
   capabilities; “e-mail gekoppeld” betekent niet automatisch verzendrecht.
7. Voice, speaker/deurbel en browsercomputer alleen tonen wanneer de
   bijbehorende V-/P-/AU-gates in die release bestaan.
8. `Ander kanaal of dienst` laat de gebruiker het doel beschrijven en start na
   onboarding de adaptieve-connectorgate; de wizard doet niet alsof iedere
   externe provider bij installatie al ondersteund is.
9. Geheugen-, token-, kosten- en achtergrondbudgetten, bewaartermijnen,
   back-uplocatie en een directe stop-/herstelroute bevestigen.

Iedere connector start met de kleinste permissie, toont wat Eva wel en niet kan
en sluit af met een echte read-only proef en gesaneerde receipt. Een mislukte
koppeling blokkeert alleen die capability; zij maakt de hele installatie niet
stil half-geconfigureerd.

## Secret- en netwerkgrens

- De wizard draait lokaal en bindt alleen aan loopback. Secretvelden gaan niet
  via Telegram, een cloud-dashboard, logs, analytics of een modelprompt.
- Loopback is niet vanzelf vertrouwd: de launcher maakt een kortlevend,
  eenmalig bootstraptoken; de wizard valideert exact `Host` en `Origin`, gebruikt
  CSRF-bescherming, zet geen CORS open en accepteert na afronding geen oude
  sessie. Secretresponses zijn `no-store` en de browser bewaart ze niet in
  local/session storage of autofill.
- Secrets worden rechtstreeks als vooraf geprovisioneerde private files of via
  een ondersteunde lokale secretstore opgeslagen; nooit als gewone env-waarde,
  in `SOUL.md`, `MEMORY.md`, database-export of supportbundle.
- De installer toont alleen aanwezigheid, scope, laatste verificatie en een
  secretreferentie. Verwijderen of roteren vereist een expliciete connectorstap.
- Een koppeling wordt pas `ready` na providerreadback. Bij onzekerheid blijft
  de status `unknown` of `needs_help`, nooit groen op basis van alleen een 2xx.

## Installeren, verplaatsen, bijwerken en verwijderen

- Installatie gebruikt één content-addressed, ondertekend releasebundle met
  gepinde images, schema's, platformmatrix en installerhash. Geen packagehook of
  willekeurige downloadcode mag tijdens discovery uitvoeren.
- Fresh install maakt een nieuwe installatie-identiteit, database, Hindsight-
  namespace, IdentityPack-trustbinding en private directories; herhaald starten
  is idempotent.
- Verplaatsen gebruikt een versleutelde portable export plus een afzonderlijk
  restorepad. Secrets worden niet meegedragen en moeten op het nieuwe systeem
  opnieuw worden ingevoerd. De oude installatie wordt niet automatisch gewist.
- Update gebruikt de bestaande A/B-route: nieuwe versie staged, migraties
  gecontroleerd, canary groen, verkeer bewust omgezet en éénstapsrollback
  beschikbaar.
- Verwijderen toont exact welke containers, volumes, secrets en back-ups geraakt
  worden. Standaard wordt eerst een herstelbare export gemaakt en niets buiten
  de gekozen installatie verwijderd.

## Kleine opleverstukken

- [x] IN00 — Bestaande installatieonderdelen, ontbrekende productwizard,
  `SOUL.md`/`MEMORY.md`-equivalenten en connectorvragen eerlijk vastgelegd.
- [ ] IN01 — Ondertekend installatiebundle en ondersteunde-platformmatrix; één
  launcher opent een loopback-wizard zonder handmatige YAML/JSON/env-edit.
- [ ] IN02 — Wizardstatus en hervatten na herstart; iedere vraag, keuze en
  connector blijft afzonderlijk staged/ready/blocked en auditbaar.
  De identiteitsdeelroute is nu duurzaam: stage, ondertekening, activatie en
  rollback worden na een nieuwe server én nieuwe browsersessie exact hervat.
  Ook een procesuitval tussen de echte bestandswissel en de statuswrite wordt
  zonder tweede wissel hersteld. De overige installervragen en connectoren
  hebben deze statusmachine nog niet; daarom blijft IN02 als geheel open.
- [x] IN03 — Identity-onboarding met staged `SOUL.md`-import/export,
  IdentityPack-diff, ondertekening, activatie en rollback.
  Na de eerdere read-only voorstelweergave doorliep een echte lokale
  loopback-HTTP-proef export, preview, private opslag, ondertekening, activatie
  en directe rollback. Vier opeenvolgende echte loopback-serverprocessen
  gebruikten dezelfde content-addressed draft en kandidaat; oude, gewijzigde
  of onbevestigde invoer wordt geweigerd. Dit is nog geen volledige
  consumenteninstaller of productiewijziging.
- [ ] IN04 — Telegram-, Home Assistant- en één e-mailproviderwizard met
  least-privilege scopes, echte read-only proeven en capability-permissions,
  plus een ingang naar AC01 voor een niet-vooraf-ingebakken kanaal.
- [ ] IN05 — Lokale one-time secretinvoer, private file/secretstore,
  kortlevende wizardauthenticatie, rotatie/verwijdering en expliciete
  nonleak-/CSRF-/DNS-rebindingtests.
- [ ] IN06 — Fresh install op twee schone computers doorloopt schema, services,
  readiness, Telegramgesprek, Home Assistant-read en herstart zonder handmatige
  bron- of configuratiebewerking.
- [ ] IN07 — Als geheel versleutelde transportexport plus restore op een
  andere computer behoudt IdentityPack, toegestane personen, gesprekken en
  geheugen; secrets worden opnieuw ingevoerd en gewiste data komt niet terug.
- [ ] IN08 — A/B-update, rollback, diagnose, supportbundle en recoverable
  uninstall zijn door een niet-ontwikkelaar uitgevoerd en geaccepteerd.

IN01-IN08 zijn productgates. De bestaande schema- en deploymentproeven leveren
bruikbare operatorcomponenten, maar vormen samen nog geen ondersteunde volledige
installatie en bewijzen niet dat een gewone gebruiker Eva al gemakkelijk op een
andere computer kan installeren.
