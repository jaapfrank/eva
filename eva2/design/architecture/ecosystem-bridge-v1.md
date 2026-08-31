# Ecosysteembrug v1

Status: ontwerp na teamreview, nog niet geïmplementeerd of live toegelaten.

## Doel

Eva gebruikt bewezen ecosystemen van OpenClaw, Hermes en Agent Zero zonder haar
eigen identiteit, geheugen, toestemming, eventtijdlijn of bewijscontrole uit
handen te geven. Zij mag bovendien niet vastzitten aan connectoren die tijdens
de productbouw al bekend waren: een gebruiker moet vanuit de Eva-chat een nieuw
legitiem kanaal of een nieuwe dienst kunnen laten onderzoeken, voorbereiden,
koppelen en activeren zonder de Living Core te herschrijven.

```text
Telegram / Web / dynamisch gekoppelde kanalen / Home Assistant events
                   |
                   v
        Eva Living Core + policy
          |       |        |
          |       |        +--> PostgreSQL event store
          |       +-----------> Hindsight recall/retain/reflect
          |
          +--> Capability broker
                 |       |        |
                 |       |        +--> Agent Zero sidecar (API/A2A)
                 |       +-----------> MCP servers
                 +-------------------> OpenClaw Gateway sidecar
                                                |
                                plugins / automations / nodes
```

De pijl terug naar Eva bevat alleen een getypeerd providerresultaat. Eva beslist
zelf of dat resultaat voldoende bewijs is en formuleert zelf het antwoord.

## Dependency-first volgorde

De brug begint niet meer bij nieuwe Eva-adapters. Voor algemene agentloops,
voice, korte subagents en usagegegevens wordt eerst Eva's bestaande exacte
Pydantic AI-pin benut. DBOS is de standaard voor generiek starten, wachten en
hervatten. MCP is de normale providerdraad; Microsoft Playwright MCP is de
eerste browserkandidaat. Een extra chatkanaal begint bij een gepinde
OpenClaw-plugin of officiële provider-SDK.

Eigen connector-RPC blijft alleen waar Eva's strengere call-start- en
ambiguïteitsgrens aantoonbaar niet via MCP kan worden behouden. Er komt geen
tweede scheduler, browserengine, voiceplatform of channelgateway zonder een
vastgelegde mislukte dependencyproef. Exacte selectie-, licentie-, update- en
rollbackregels staan in `../../OPEN_SOURCE_EN_DEPENDENCYKEUZES.md`.

## Autoriteit

| Gegeven of beslissing | Enige autoriteit |
|---|---|
| Persona, toon en drijfveren | Eva IdentityPack |
| Feitelijke tijdlijn, retries en idempotency | Eva PostgreSQL |
| Langetermijnherinneringen en observaties | Eva Hindsight-bank |
| Toestemming en resourcebinding | Eva policy + CapabilityManifest |
| Toolbeschikbaarheid | Externe catalogus, gefilterd door Eva |
| Externe uitvoering | Sidecar of MCP-server |
| Succesclaim | Eva na onafhankelijke readback |
| Gebruikersantwoord | Eva Expression-laag |

Een sidecar is dus geen tweede brein, geen geheugenautoriteit en geen eigenaar
van een duurzame gebruikersopdracht.

## Adaptieve connectorcreatie

Telegram-first is een oplevervolgorde, geen architecturale bovengrens. Als een
persoon bijvoorbeeld WhatsApp in plaats van Telegram wil gebruiken, voert Eva
vanuit de chat deze gesloten cyclus uit:

1. Eva legt doel, eigenaar/persoon, privé- of groepsgebruik, alleen ontvangen of
   ook verzenden, media, looptijd, budget en stopmogelijkheid vast.
2. Zij raadpleegt actuele officiële documentatie en kiest eerst een bestaande
   standaardroute: MCP, een gepinde Gateway, webhook/API of een geïsoleerde
   transportprovider. Een store of vooraf ingebakken adapter is niet vereist.
3. Ontbreekt een passende route, dan maakt Eva on the fly in quarantaine de
   kleinst mogelijke connectoradapter plus gesloten `ConnectorManifest`. Deze
   code importeert de Living Core niet en krijgt geen secrets tijdens ontwerp of
   tests.
4. Contract-, schema-, permission-, privacy-, timeout-, replay-, crash- en
   nonleaktests draaien tegen een afgeschermde provider- of contractsandbox.
5. Alleen ontbrekende account- of credentialstappen worden via chat en lokale
   eenmalige secretinvoer afgehandeld. Eva mag binnen een vastgelegd mandaat de
   browserroute gebruiken, maar omzeilt geen platformvoorwaarden, CAPTCHA,
   identiteitseisen of ontbrekende providerondersteuning.
6. Activatie registreert een content-addressed capability en kanaalbinding
   buiten de core-image. Ingress bindt eerst de externe afzender aan een
   geverifieerde Eva-persoon; uitgang gebruikt Eva's deliveryledger. Een echte
   heen-en-terugproef, stopknop en rollback sluiten de koppeling.

Zo kan het connectoroppervlak tijdens gebruik groeien terwijl identiteit,
geheugen, consent, toolauthority en eindantwoord bij Eva blijven. “Alles
mogelijk” betekent hier: iedere dienst met een legitieme browser-, API-,
webhook-, MCP- of Gatewayroute kan kandidaat worden; het is geen belofte dat Eva
een gesloten of automatisering-verbiedend platform kan forceren.

## Zelf diensten en mogelijkheden ontdekken

Een dienst hoeft niet vooraf bij naam in Eva te staan. Het blijvende brede
Eigenaarsmandaat uit de installatie geeft Eva hiervoor doorlopende bevoegdheid
binnen het huishouden, het lokale netwerk en bewust gekoppelde accounts. Zij mag
zonder nieuwe bevestiging per vondst of actie zelf:

1. met bestaande onderhoudbare discoveryprotocollen zoals mDNS/DNS-SD en SSDP
   inventariseren welke diensten zichzelf publiceren;
2. zo nodig een beheerste host-/poortcontrole doen, zonder exploit-,
   wachtwoord- of kwetsbaarheidsscan;
3. product, versie en gepubliceerde endpoints herkennen en actuele officiële
   documentatie raadplegen;
4. de werkelijk beschikbare MCP-, OpenAPI-, SDK- en HTTP-routes vergelijken;
5. de kleinste route kiezen die het doel kan uitvoeren; MCP heeft voorkeur als
   het voldoende bereik en bewijs levert, maar is geen hardgecodeerde winnaar;
6. alleen wanneer een dienst menselijke authenticatie afdwingt de bestaande
   browsersessie tijdelijk aan de eigenaar overdragen voor login, 2FA, CAPTCHA
   of wettelijk vereiste persoonlijke verklaring en daarna zelfstandig vanaf
   hetzelfde checkpoint doorgaan. Eva automatiseert of omzeilt zulke externe
   menscontroles niet en gebruikt daarvoor geen CAPTCHA-oplosdienst;
7. de gevonden tools tijdens runtime aan het agentframework aanbieden, zonder
   nieuwe Eva-kerncode of een herbouw van de core-image;
8. een actie uitvoeren en via een onafhankelijk gevonden readpad controleren.

Voorbeeld: bij `het is koud in de overkapping` leidt Eva zelf het gewenste
resultaat af, ontdekt zij via de gekoppelde dienst welke actuele mogelijkheden
en apparaten bestaan en kiest zij de unieke passende verwarming. Er bestaat
geen Eva-regel met `koud`, `heater`, `switch`, Home Assistant of UniFi. Bij
meerdere aannemelijke doelen, ontbrekende rechten of een risicovolle actie
vraagt Eva wel door.

Het brede installatiemandaat is wél de blijvende toestemming om gevonden
diensten binnen de leefomgeving te gebruiken. Eva hoeft daarvoor niet per tool
of effect terug naar de eigenaar. Intern houdt zij credentials gescheiden en
geeft zij een provider of subagent niet méér technisch bereik dan nodig; dat is
implementatie-isolatie, geen menselijke bevestigingsstap. De eigenaar behoudt
één directe pauze-/stop- en intrekkingsmogelijkheid.

De huidige runtime voldoet hier nog niet aan. Hij heeft een generieke
MCP-proefgrens, maar de live providerlaag bevat nog vooraf gemaakte Home
Assistant- en e-mailtools. Die code telt als te verwijderen migratieschuld.

### Capability-discoverygates

- [ ] CD01 — Eén blijvend breed Eigenaarsmandaat tijdens installatie voor het
  huishouden, lokale netwerk en gekoppelde accounts, met meldbeleid en directe
  stop maar zonder bevestiging per tool of actie.
- [ ] CD02 — mDNS/DNS-SD/SSDP-discovery via onderhouden dependencies; optionele
  begrensde poortcontrole zonder exploitgedrag.
- [ ] CD03 — Dienstherkenning plus actuele officiële documentatie- en
  endpointvergelijking, zonder uitvoer of secret.
- [x] CD04 — Runtime toolontdekking via het gekozen agent-/MCP-framework, met
  twee onbekende synthetische providers en nul nieuwe Eva-dispatchcode. De
  actuele Pydantic AI `2.36.0`-proef is 5/5 groen: impliciet actueel doel,
  afzonderlijke readback, historisch en voorkeur zonder effect, dubbelzinnig
  doel met vervolgvraag en een tweede providersoort met dezelfde agentcode. De
  minimale client is daarnaast uit 69 gehashte wheels zonder bouwnetwerk
  gemaakt, heeft een CycloneDX-SBOM, scant met de database van 31 augustus 0
  hoog/0 kritisch en ontdekte een tool uit een afzonderlijk providerproces.
  Pydantic AI's publieke `process_tool_call`-grens onderschepte een tool vóór
  provider-I/O; Eva's duurzame ledger kan dus de uitvoerende autoriteit blijven.
- [ ] CD05 — Eén echte lokale dienst koppelen via minimale authenticatie,
  effect, onafhankelijke readback en herstart zonder dubbel uitvoeren.
- [ ] CD06 — Dezelfde image koppelt een tweede dienst van een andere soort;
  daarna zijn overlappende providerspecifieke Eva-adapters verwijderd.

### Adaptieve-connectorgates

- [ ] AC01 — Chatmandaat en kanaalcontract met eigenaar, personen/groepen,
  ingress/egress/media, permissies, budget, looptijd en stopknop.
- [ ] AC02 — Actuele officiële discovery en providerkeuze; standaardadapter
  gaat vóór nieuwe code en iedere bron/versie/digest wordt vastgelegd.
- [ ] AC03 — On-the-fly adapter en `ConnectorManifest` in quarantaine, zonder
  core-import, secret, packagehook of stil verkregen permissie.
- [ ] AC04 — Sandbox- en held-out tests voor schema, privacy, replay, timeout,
  crash, rate limit en secret-nonleak; onafhankelijke promotiebeslissing.
- [ ] AC05 — Lokale credentialinvoer, geverifieerde persoonsbinding en echte
  read-only/receiveproef vóór uitgaande rechten.
- [ ] AC06 — Dynamische activatie zonder core-rebuild, echte heen-en-terugproef,
  cross-channel geheugenisolatie, observability, directe stop en rollback.

## Adapters

### `ClawHubSkillAdapter`

- Zoekt en downloadt alleen; activeert niets tijdens discovery.
- Bewaart publisher, slug, semver, bron-URL, digest, auditstatus en scantijd.
- Plaatst de volledige bundel inert in `eva2/forge/quarantine/`.
- Weigert `latest`, digestwissels, automatische installscripts en ontbrekende
  licentie/provenance.
- Pakt uitsluitend reguliere bestanden onder een nieuwe lege root uit en
  weigert absolute paden, `..`, symlinks, hardlinks, devices, sockets en FIFO's.
- Begrens gecomprimeerde en uitgepakte bytes, bestandenaantal,
  compressieverhouding, padlengte en uitpaktijd; voer geen packagehook,
  dependency-installer of meegeleverd script uit.
- Zet pas na review een `SKILL.md` om naar een Eva-procedure met expliciete
  toolmapping.

### `OpenClawGatewayAdapter`

- Praat alleen met een dedicated, gepinde Gateway in hetzelfde trustdomain.
- Gebruikt device identity en minimale scopes; een algemene operator-bearer is
  niet toegestaan voor normaal bedrijf.
- Leest `tools.effective` voor inventaris en gebruikt `tools.invoke` alleen na
  een Eva-grant.
- Companion nodes blijven officieel aan de Gateway gekoppeld. Eva gebruikt
  alleen `node.list`, `node.describe` en toegelaten `node.invoke`-calls.
- Concurrentencron mag een wake-up leveren, maar de jobstatus blijft een
  projectie van Eva's duurzame doel en nooit andersom.
- Een externe dry-run is geen bewijs dat een mutatie veilig is.

### `McpCapabilityAdapter`

- Is de voorkeursroute voor standaardtools die alle drie concurrenten al via
  MCP kunnen gebruiken.
- Importeert alleen getypeerde schemas, niet de persona of prompt van het andere
  framework.
- Past per server en per tool een allowlist, effectklasse, resourcepatroon,
  timeout, secretref en readbackstrategie toe.

### `AgentZeroSidecarAdapter`

- Is alleen kandidaat voor browser-, desktop- en artifacttaken waarvoor Agent
  Zero in de productbenchmark aantoonbaar beter presteert.
- Draait in een eigen container zonder Docker-socket, gezinsgeheugen of brede
  workspace-mount.
- A0 CLI en hostbrowser vereisen een afzonderlijke, kortlevende hostgrant.

Hermes-specifieke functies gebruiken bij voorkeur AgentSkills of MCP. Een hele
Hermes-runtime wordt alleen als sidecar toegelaten als een standaardadapter het
geteste voordeel niet kan leveren.

## Uitvoeringsprotocol

1. Resolveer één exacte `ResourceRef` en `CapabilityManifest`.
2. Controleer persoon, effectklasse, resource, doel, tijdvenster en budget.
3. Schrijf `TOOL_PREPARED` met requesthash en idempotency key.
4. Roep de sidecar aan met een harde timeout; blokkeer de eventloop niet.
5. Schrijf het letterlijke, secretvrije providerresultaat als onvertrouwd event.
6. Lees de werkelijke doeltoestand via een onafhankelijke bron terug.
7. Schrijf `STATE_VERIFIED`, `STATE_NOT_VERIFIED` of `NEEDS_HELP`.
8. Laat uitsluitend Eva's Expression-laag een menselijk antwoord maken.

Na drie herstartfouten op hetzelfde checkpoint gaat het checkpoint naar
`NEEDS_HELP`; Eva herhaalt het effect niet eindeloos.

## Verplichte isolatie

- Exacte image/packageversie en digest, geen automatische updates.
- Eigen OS-user, netwerkallowlist, read-only root filesystem waar mogelijk.
- Geen Docker-socket, productie-Home of onbegrensde projectmount.
- Eén secret per capability via file-secret; nooit Eva's volledige env.
- Uitgaande payloads bevatten geen Hindsightcontext of gezinsgegevens die de
  capability niet strikt nodig heeft.
- Camera, scherm, locatie, microfoon en economische acties vragen een aparte
  resourcegrant en kunnen nooit door een algemene skill worden verkregen.

## Bouw- en testvolgorde

1. De groene actuele Pydantic AI/MCP-client in één vervangingsdiff achter
   dezelfde duurzame ledger-, identiteit- en readbackgrens zetten. De 3.342
   gemeten overlappende regels zijn onderzoeksscope, geen automatische
   verwijderclaim; pas de werkelijke nettodiff beslist over promotie.
2. De native MCP-toolset met één echte dienst uitvoeren, zonder de oude model-
   of MCP-loop ernaast te behouden.
3. Een tweede echte providersoort met dezelfde image uitvoeren; daarna de
   overlappende providerspecifieke adapters verwijderen.
4. Read-only officiële Gatewaytool in een geïsoleerde B-sidecar, gevolgd door
   denial-, timeout-, restart- en identiteitsscheidingstest.
5. AC01-AC04 tegen een credentialvrije lokale kanaalcontractprovider.
6. ClawHub-importer die exact één optionele externe skill alleen in quarantaine
   plaatst; Eva's eigen skillcreatie blijft hiervan onafhankelijk.
7. Eén read-only companion-nodefunctie.
8. Eén automation als Eva-PostgreSQL-projectie.
9. Eén browser/desktoptaak na native productbenchmark.
10. AC05-AC06 voor één echt extra chatkanaal; pas daarna bredere muterende
   plugins, camera, locatie of commerce.

Iedere stap krijgt eerst architectuurreview, daarna implementatie en daarna een
andere agent die de echte B-uitvoering en receipts controleert.

## Voorgenomen bestandseigenaarschap

| Onderdeel | Bestand of map |
|---|---|
| Getypeerde capability en grant | `src/eva/contracts/capability.py` |
| Algemene broker en effectprotocol | `src/eva/application/capabilities.py` |
| OpenClaw Gateway RPC | `src/eva/adapters/connectors/openclaw_gateway.py` |
| MCP-bewijsclient, niet in de runtime | `tests/fixtures/mcp.py` |
| ClawHub discovery/quarantaine | `src/eva/adapters/connectors/clawhub.py`, `forge/quarantine/` |
| Agent Zero sidecar-RPC | `src/eva/adapters/connectors/agent_zero.py` |
| PostgreSQL-events | bestaande `adapters/postgres/operational_timeline.py` |
| Deployment | `deploy/ecosystem-sidecars/` |
| Contracttests | `tests/contracts/test_capability.py` |
| Live B-gates | `tools/check_ecosystem_bridge_live.py`, `evidence/ecosystem/` |

Deze paden zijn voorgenomen eigenaarschap, geen claim dat de bestanden al
bestaan of werken.
