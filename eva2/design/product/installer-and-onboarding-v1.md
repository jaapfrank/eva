# Eva installer en onboarding v1

Bijgewerkt: 25 augustus 2026.

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
- Er is **geen** gebruiksvriendelijke productinstaller, lokale setupwizard of
  volledig connector-onboardingpad. De platformbasis markeert
  `installerExists` nog expliciet als `false`.
- Telegram, Home Assistant en AgentMail hebben afzonderlijke adapters of
  deploymentdelen, maar de gebruiker moet configuratie en secrets nu nog als
  operator voorbereiden. Generieke IMAP/SMTP- of OAuth-emailonboarding is niet
  aanwezig.

## `SOUL.md`, `MEMORY.md` en Eva's echte autoriteiten

Er bestaan nu geen letterlijke `SOUL.md`- of `MEMORY.md`-runtimebestanden.

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
- [ ] IN03 — Identity-onboarding met staged `SOUL.md`-import/export,
  IdentityPack-diff, ondertekening, activatie en rollback.
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
