# Eva 2 tegenover OpenBot

Bijgewerkt: 25 augustus 2026.

## Welke OpenBot

- Met **OpenBot** bedoelen we hier
  [`CopilotKit/OpenBot`](https://github.com/CopilotKit/OpenBot): het nieuwe
  platform voor AI-collega's met ieder een eigen browsercomputer.
- We bedoelen niet de smartphone-robot van `ob-f/OpenBot` en ook niet de
  kleinere `meetopenbot/openbot`-harness.
- De actuele openbare release is `v0.0.4`, commit
  `6826e11afd52f03c30af2d873203792acad95f63`. De documentatie-inventaris is
  daarnaast gecontroleerd op commit
  `d293f2331bd5ff9ba4ad17af6ac94570a157d26d`.
- OpenBot noemt zichzelf expliciet **alpha**. Onderstaande OpenBot-kolom is dus
  documentatiebewijs, geen door ons uitgevoerde producttest.

## Korte conclusie

- OpenBot ligt nu duidelijk voor op **browser- en computergebruik**: per Bot
  een eigen Chromium, loginprofiel, werkmap en shell, met meekijken en
  menselijke overname.
- OpenBot heeft ook een veel verder dashboard voor coworkers, skills, MCP,
  credentials, policies en audit.
- OpenBot is niet automatisch een slimmer of levender brein. De eigenlijke
  agent komt via AG-UI binnen; de officiële documentatie bewijst geen
  langdurige doelen, self-improvement, Telegramgezin, Home Assistant,
  deurbel-/speakerpresence of fysieke readback.
- Eva heeft bewezen of ontworpen eigenschappen die niet in OpenBots openbare
  documentatie staan: persoons- en groepsgeheugen, huisintegratie, bewijs na
  een fysieke actie en herstel zonder een effect blind te herhalen. Dat is geen
  algemene voorsprongclaim; Eva's toolketen en browsercomputer zijn nog niet af.
- Om goed te concurreren moet Eva dus OpenBots praktische computerautonomie
  evenaren, terwijl Eva eigenaar blijft van identiteit, geheugen, mandaat,
  policy, ledger, readback en eindantwoord.

## Vergelijking die je zelf kunt lezen

| Onderdeel | Eva 2 nu | OpenBot gedocumenteerd | Wat Eva moet doen |
|---|---|---|---|
| Autonoom doorwerken | Duurzame plannen, continuation-leases en één voorbereide read zijn bewezen; algemene provideruitvoering ontbreekt nog | Een meegebrachte AG-UI-agent kan zelfstandig browser-, bestand-, shell- en MCP-calls voorstellen | Eén chatmandaat duurzaam omzetten in plan → act → observeer → herplan, tot doel, blocker of stopgrens |
| Browser en websites | Nog geen interactieve browsercomputer | Per Bot een eigen Chromium, browserprofiel en live scherm; gVisor-isolatie is optioneel | Geïsoleerde browserprovider toevoegen met doel-, site-, actie-, tijd- en budgetbinding |
| Bestanden en shell | Begrensde eigen bestandstools; geen vrije computerwerkplek | Eigen workspace en shell per Bot | Alleen een taakwerkmap aanbieden; commando's blijven getypeerd, begrensd en volledig gelogd |
| Accounts en logins | Ontworpen in B01/E01, nog niet gebouwd | Persistent browserprofiel; mens kan login en 2FA tijdelijk overnemen | Veilige secretinvoer, blijvende sessie, chatmandaat, stopknop en overnamepad bouwen |
| Policy vóór actie | Fail-closed Eva-policy en intentledger; volledige keten nog open | Gateway beslist en schrijft audit vóór doorsturen; lege of kapotte policy weigert | Iedere browser/toolactie eerst als Eva-intent/attempt vastleggen; providerpolicy is een extra laag |
| Herstel na crash | Exacte read/effect/readbacktoestanden ontworpen; MC03b/c en MX05 open | Audit en duurzame threads aanwezig; geen gelijkwaardige no-duplicate-effectclaim gevonden | Crash na externe call als `ambiguous` behandelen en nooit blind dezelfde actie herhalen |
| Bewijs van resultaat | Sterke PostgreSQL-tijdlijn en fysieke HA-readback | Acties en fouten in audit; live scherm en activityweergave | Naast audit altijd providerresultaat of echte doeltoestand teruglezen |
| Menselijke controle | Chatmandaat en stopgrenzen gepland; geen browserovername-UI | Live meekijken, hulpvraag, overnemen en teruggeven | Dashboard krijgt live status, pauze, stop en tijdelijke overname zonder goedkeuring per normale stap |
| Geheugen en identiteit | Hindsight, persoon/project, privé/groep en actuele consent grotendeels gebouwd | Coworkerrol en duurzame channels/threads via CopilotKit Intelligence | Eva's eigen geheugen behouden; OpenBot of een browserprovider krijgt dit niet als tweede brein |
| Dashboard en skills | D01 en IdentityPack-preview bestaan; editor, skills, credentials en computers ontbreken | Uitgebreide routes voor agents, skills, plugins, credentials, computers, boundaries en audit | Dashboard in kleine D-slices uitbreiden met skills, tools, mandaten, computers en audit |
| Huis en fysieke aanwezigheid | HA-readback bestaat; oude deurbel-/speakerpresence moet via P01-P05 terug | Geen officiële HA-, Telegram-, deurbel- of speakerclaim gevonden | MC03c/MX04-MX05 en P01-P05 blijven onderscheidende Eva-doelen en harde V1-gates |
| Productafhankelijkheid | Eigen PostgreSQL/Hindsight/runtime plus gekozen modelprovider | Vereist volgens de quickstart een CopilotKit Intelligence-project/licentie en modelkey; Intelligence kan self-hosted | Voor hergebruik eerst licentie, self-hosting, protocolstabiliteit en uitvalsgedrag toetsen; Eva mag niet van een tweede geheugenauthority afhangen |
| Productstatus | Pre-freeze; delen live bewezen op verschillende images | Expliciet alpha en actief in ontwikkeling | Geen algemene winnaar claimen vóór dezelfde echte eindtaken zijn uitgevoerd |

## Wat “echte autonomie” voor Eva betekent

Echte autonomie betekent niet dat Eva onbeperkt alles mag. Het betekent dat jij
in de chat één duidelijk mandaat kunt geven en dat Eva daarna niet voor iedere
normale tussenstap opnieuw toestemming vraagt.

Een mandaat bevat minimaal:

- het gewenste resultaat;
- toegelaten sites, accounts, apparaten en tools;
- tijd-, geld-, order-, privacy- en verliesgrenzen waar relevant;
- wat Eva zelfstandig mag beslissen;
- wanneer zij moet waarschuwen, pauzeren of hulp vragen;
- een directe stopknop.

Binnen dat mandaat mag Eva zelfstandig plannen, informatie ophalen, formulieren
invullen, tools gebruiken, resultaten controleren, fouten herstellen en haar
plan aanpassen. Buiten het mandaat stopt zij. Na een crash hervat zij vanuit de
duurzame tijdlijn en herhaalt zij geen mogelijk uitgevoerde externe actie zonder
reconcile of readback.

## Autonomie-afvinkbord

Ieder vak is een afzonderlijk Git-checkpoint met gerichte tests en onafhankelijke
review.

- [x] `OB00` — relevante OpenBot, versie, claims, nonclaims en Eva-gaten uit
  officiële bronnen vastgelegd.
- [ ] `AU01` — chatmandaat als duurzaam contract: exacte owner-principal, doel,
  scope, budget, looptijd, revision, intrekking en stopvoorwaarden.
- [ ] `AU02` — algemene runner als drie kleine delen:
  - [ ] `AU02a/MC03b` — één voorbereide read autoriseren, uitvoeren en duurzaam
    observeren zonder dubbele providercall;
  - [ ] `AU02b/MC03c` — één effect met readback sluiten en pas dan de volgende
    afhankelijke stap vrijgeven;
  - [ ] `AU02c` — na een waarneming begrensd herplannen of wachten zonder
    opdrachtspecifiek script.
- [ ] `AU03` — één geïsoleerde browsercomputer met eigen taakwerkmap en profiel;
  geen direct netwerk naar Eva's database of secretopslag. Providerkeuze,
  commit/imagepin, restrictive policy en een credentialvrije read-only proef
  gaan aan de eerste start vooraf; OpenBots single-adminstand is niet toegestaan
  in een gedeelde of productieroute.
- [ ] `AU04` — de eerste browserroute en haar afgeschermde downloadbestand gaan
  door Eva's eigen grant-, policy-, attempt-, audit- en readbackgrens. Shell,
  MCP en brede bestandstoegang volgen later als afzonderlijke providers achter
  hetzelfde contract.
- [ ] `AU05` — dashboard toont live scherm, actuele stap, gebruikte tools,
  receipts en kosten zonder secretinhoud; eigenaar kan pauzeren, stoppen,
  overnemen en teruggeven en de duurzame audit blijft na reload beschikbaar.
- [ ] `AU06` — echte meerstapstaak met een vooraf geautoriseerd testaccount op
  een afgeschermde website: login, navigatie, formulier, download/bestand en
  controle van de eindtoestand. Echte accountcreatie, voorwaarden, betaling,
  KYC en financiële acties blijven mogelijk als latere B01/E01-gates binnen
  een chatmandaat, maar tellen niet als deze eerste browserproef.
- [ ] `AU07` — crash na mogelijke externe actie: nul blinde herhaling, eerlijke
  `ambiguous`/reconcile-route en aantoonbare hervatting; ook crash na observation
  maar vóór delivery maakt geen tweede call of dubbel bericht.
- [ ] `AU08` — vooraf verzegelde zevendaagse praktijktest met aantallen cases,
  toegelaten sites, tijd-/geldbudget, cooldown, waarschuwingsgrens en receiptset;
  nuttige voortgang, geen scopelek en expliciete gebruikersacceptatie.

`AU01`, `AU02`, `AU04` en `AU07` sluiten aan op MC03/MX04/MX05. `AU03`, `AU05`
en `AU06` zijn de browser-/dashboardachterstand tegenover OpenBot. AU01-AU07
sluiten technisch vóór de freeze, maar worden voor eindbewijs allemaal opnieuw
op de bevroren image gereceipt. AU08 start daarna op diezelfde image.

## Hergebruiken zonder twee breinen

- OpenBot kan later als gepinde, geïsoleerde browser-/computerprovider worden
  onderzocht, of als referentie voor Eva's dashboard en human-takeover.
- Een proef begint read-only, gebruikt geen echte credentials en schrijft eerst
  Eva's eigen intent en attempt.
- OpenBot mag niet zelfstandig Eva's persona, Hindsight, gezinsdata, policy of
  eindantwoord beheren.
- Alleen het computer-/browseroppervlak mag als capability-sidecar worden
  onderzocht. OpenBots AG-UI-agent, threads en policy-eigenaarschap worden niet
  Eva's runner of orchestrator.
- De meegeleverde OpenBot-startpolicy is configureerbaar en staat volgens de
  documentatie standaard breed open (`allow: ["true"]`). Eva moet dus haar
  eigen fail-closed grens houden, ook als OpenBot zelf een tweede policylaag
  toepast.
- Een verse clone gebruikt volgens de README standaard
  `OPENBOT_SINGLE_USER=true`, waardoor alle requests één administrator zijn.
  Die ontwikkelstand is verboden voor een gedeelde of productieroute.
- Een hergebruikbesluit volgt pas na een vastgepinde sidecarproef met crash en
  receipt; documentatie alleen is daarvoor niet genoeg.

## Primaire bronnen

- OpenBot README en status: <https://github.com/CopilotKit/OpenBot/tree/d293f2331bd5ff9ba4ad17af6ac94570a157d26d>
- Architectuur en action gateway: <https://github.com/CopilotKit/OpenBot/blob/d293f2331bd5ff9ba4ad17af6ac94570a157d26d/docs/architecture.md>
- Coworkers en AG-UI: <https://github.com/CopilotKit/OpenBot/blob/d293f2331bd5ff9ba4ad17af6ac94570a157d26d/docs/coworkers.md>
- Release `v0.0.4`: <https://github.com/CopilotKit/OpenBot/releases/tag/v0.0.4>
- Gepinde releasecommit: <https://github.com/CopilotKit/OpenBot/commit/6826e11afd52f03c30af2d873203792acad95f63>
