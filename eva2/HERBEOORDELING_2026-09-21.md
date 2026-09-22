# EVA 2 — herbeoordeling en eerste gezinsoplevering

> **Ingehaald door het eigenaarbesluit van 22 september 2026:** behoud wat werkt
> en ga verder met bestaande wensen. Onderstaande vereenvoudiging, GZ-001,
> nulmeting en urenraming zijn onderzoeksvoorstellen, geen actieve bouwopdracht.
> Verandering vereist een concreet aangetoond tekort aan de eigenaarseisen.
> Zie [PROJECTDOEL.md](PROJECTDOEL.md) en [WERKBORD.md](WERKBORD.md).

Datum: 21 september 2026. Opdrachtgever: Jaap. Status: onderzoek en ontwerp;
de [onafhankelijke eindreview](reviews/reassessment-final-review-2026-09-21.md)
legt het technische oordeel afzonderlijk vast. Dit document is een
besluitonderbouwing en opleverspecificatie, geen tweede werkbord.

## Uitkomst

**Aanbevolen basis: vereenvoudig de bestaande Eva rond Pydantic AI, PostgreSQL,
Hindsight en LiveKit, en maak één samenhangende gezinsuitkomst af.** Behoud haar
persoonsbinding, geheugenhistorie en gecontroleerde acties. Breng algemene
model-, tool-, spraak- en browserfuncties onder bij onderhouden bouwstenen.
Stop met het uitbreiden van vooraf geprogrammeerde demotaken in de kern.

Een volledige overstap op OpenClaw, Hermes of Agent Zero is serieus overwogen.
Hun grotere aanbod maakt hen waardevolle vergelijkingskandidaten, maar bewijst
niet dat een verhuizing Eva's samenhang, gegevensbehoud, huisroutes en snelheid
verbetert. De aanbeveling is een architectuurkeuze op basis van code,
projectbewijs en actuele upstreamdocumentatie; **geen gemeten overwinning**.
Een volwaardige agent mag later de uitvoerder vervangen wanneer dezelfde
proeven voordeel aantonen. Het huidige framework krijgt geen vrijstelling.

## Wat het onderzoek onderscheidt

- De initiële inventaris, inclusief genegeerde Markdown, staat in
  [de leesregistratie](evidence/reassessment/reading-register-2026-09-21.json).
  Alle 317 projecteigen Markdownbestanden zijn inhoudelijk gelezen. Bij één
  gegenereerde inventaris zijn exacte dubbele tabelcellen één keer gelezen;
  de registratie koppelt alle oorspronkelijke rijen aan die volledige inhoud.
  Vijf extra gevonden genegeerde bestanden zijn upstreamdocumentatie en expliciet
  uitgesloten. Volledige lezing betekent geen volledige heruitvoering van bewijs.
- [Architectuuronderzoek](reviews/reassessment-architecture-2026-09-21.md)
  koppelt ontwerp aan actuele code en statische controles.
- [Bewijs- en procesonderzoek](reviews/reassessment-evidence-2026-09-21.md)
  onderscheidt synthetische proeven, historische livewaarneming en acceptatie.
- [Upstreammomentopname](evidence/reassessment/upstream-2026-09-21.json)
  bevat geobserveerde releases, exacte commits, licentiemetadata en een
  begrensde advisorycontrole. Nieuwe versies zijn niet geïnstalleerd.
- De bestaande kern antwoordde op `/readyz` achter poort 44710; de
  dashboardaanmelding gaf de verwachte toegangsgrens. Dat bewijst bereikbaarheid,
  niet dat alle productfuncties opnieuw zijn getest.

De werkboom bevat al omvangrijke staged en unstaged wijzigingen en ongetrackte
bestanden. HEAD alleen beschrijft haar inhoud niet. Dit onderzoek bewaart die
toestand en bindt nieuwe bevindingen aan bestandshashes; het maakt geen
releaseclaim of nieuwe commits over andermans werk.

## Waarom het project blijft terugkomen op hetzelfde werk

| Bevinding | Controleerbare basis | Gevolg en besluit |
|---|---|---|
| Verschillende documenten geven verschillende uitvoeropdrachten | TEAM_WERKWIJZE noemt oude agentnamen en schrijfboom; historische productgates kiezen andere voicevolgorde dan de live LiveKit-route | Recente opdracht bovenaan primaire documenten; oude tekst expliciet historische context; uitsluitend het bestaande werkbord bepaalt uitvoering |
| Technische deelresultaten staan los van bruikbare eindketens | AU-006 bewijst een voorbereide documentketen; latere dynamische invoer is een andere kandidaat | Geen algemene autonomieclaim uit de demo; volgende oplevering gebruikt eigen inhoud en een ongeziene variant via de gewone gebruikersroute |
| Een eindvoorwaarde kan gaandeweg veranderen | AU-004 ging van conversieresultaat naar zoeken, bron lezen en procedurehergebruik in opeenvolgende proeven | Leg resultaat én wat precies als leren telt vooraf vast; succesvolle conversie en bewezen hergebruik blijven verschillende uitkomsten |
| Microreviews en bewijsstappen veroorzaken veel coördinatie | Lange CP-001- en AU-004/AU-006-historie zonder afgeronde algemene vergelijking | Review één betekenisvolle wijziging inclusief integratie; alleen gerichte herreview na concrete bevinding |
| Modulair ontwerp is niet overal gehandhaafd | Actuele architectuurchecker meldt 25 bevindingen; componentcatalogus meldt 97 bevindingen | Herstel geraakte grenzen bij oplevering; geen bredere allowlists om groen te worden; complete kandidaat moet haar totale open schuld verklaren |
| Opgeleverd initiatief blijft de primaire bouwkaart | AU-008 heeft technische afronding en open IN-02-oordeel | Bewaar eigenaarsoordeel apart; laat het geen onbedoelde algemene ontwikkelblokkade zijn |
| Verbruik bewijst geen waarde | AU-004 A/B: 17 modelcalls en 200.441 invoertokens voor een kleine conversieproef, volgens historische receipts | Meet calls, context, wachttijd en hulp per gebruikersresultaat; herhaal alleen met een nieuwe hypothese |

Dit is geen bewijs dat niets werkt. GE-01/GE-02 en bevestigde streaming en
laptoponderbreking blijven geaccepteerd binnen hun bestaande scope. Het probleem
is de sprong van zulke onderdelen naar dagelijks samenhangend gebruik.

## Drie basisrichtingen

| Richting | Sterkte | Belangrijkste nadeel voor deze opdracht | Keuze |
|---|---|---|---|
| Huidige basis zonder herziening afmaken | Minste directe migratie, bestaand geheugen en herstel behouden | Neemt koppelingen, specifieke demopaden en onderhoudsschuld mee | Afgewezen als ongewijzigde aanpak |
| Bestaande basis gericht vereenvoudigen | Behoud van geaccepteerde routes, vervangbare adapters en één overgang per verantwoordelijkheid | Vereist aantoonbare verwijdering van overlap en discipline tegen nieuwe kernuitzonderingen | Aanbevolen |
| Volledig op een agentplatform overstappen | Meer kant-en-klare tools, vaardigheden en gebruikersfuncties | Nieuw integratie-, privacy-, geheugen-, huis- en herstelwerk; voordelen voor Eva nog niet gemeten | Geen huidige migratie; behouden als uitdager in dezelfde proeven |

### Actuele concurrenten

Releases hieronder zijn op de onderzoeksdatum via officiële GitHub-API's
waargenomen en naar commits opgelost. Documentatie van `main` kan verder zijn
dan de release: zulke mogelijkheden zijn documentatieclaims, geen geteste
eigenschappen van de gepinde versie.

| Kandidaat | Waargenomen release | Relevante kracht | Wat vóór vervanging bewezen moet worden |
|---|---|---|---|
| OpenClaw | v2026.9.5, 19 september | Breed kanaal-/apparaatoppervlak, geheugen en automatisering | Gezinsisolatie en gegevensmigratie: gedeelde sessies zijn volgens upstream geen harde persoonsgrens |
| Hermes | v2026.9.21, 21 september | Algemene tooluitvoering en zelf beheerde vaardigheden; externe geheugenproviders waaronder Hindsight | Dezelfde Eva-persoonsgrenzen, continuïteit en stop/herstel; profielen zijn niet vanzelf het gedeelde gezinsmodel |
| Agent Zero | v2.12, 9 september | Dockerwerkplek, browser, projecten, tools en plugins | Persoonlijke huis-/stemervaring en dezelfde effectcontrole; een werkplek alleen vervangt die niet |

Bronnen: [OpenClaw-release](https://github.com/openclaw/openclaw/releases/tag/v2026.9.5),
[vertrouwensmodel](https://docs.openclaw.ai/concepts/multi-user),
[geheugen](https://docs.openclaw.ai/concepts/memory);
[Hermes-release](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.21),
[geheugen](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory/),
[skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/),
[veiligheidsmodel](https://github.com/NousResearch/hermes-agent/blob/main/SECURITY.md);
[Agent Zero-release](https://github.com/agent0ai/agent-zero/releases/tag/v2.12),
[productdocumentatie](https://github.com/agent0ai/agent-zero/blob/main/docs/README.md).

OpenClaw en Agent Zero vermelden MIT in de gelezen release-LICENSE, hoewel de
GitHub-classificatie `NOASSERTION` teruggaf. Hermes-metadata vermeldt MIT;
de exacte tag-LICENSE-download werd begrensd door HTTP429. Dat wordt niet
verborgen achter een volledig goedgekeurde licentieclaim. De bekeken
OpenClaw-advisories bevatten concrete gepatchte versies. De lege eerste
advisorypagina's van Hermes/Agent Zero bewijzen geen afwezigheid van kwetsbaarheden.
Geen van deze momentopnamen is een complete vulnerabilityscan of supportgarantie.

| Criterium | Eva gericht vereenvoudigen | OpenClaw / Hermes / Agent Zero |
|---|---|---|
| Intelligente taakuitvoering | Gewone toolroute aanwezig; voorbereide documentplannen zijn een concrete beperking | Algemene agenttools en vaardigheden gedocumenteerd; dezelfde ongeziene gezinstaak niet gemeten |
| Snelheid en middelengebruik | Recente kleine gespreksproef 7,9–12,3 s; historische tokens bekend; geen actuele p95 of maandkosten | Geen vergelijkbare actuele meting op dezelfde hardware, modellen en taken; geen snelheidsrangorde |
| Geheugen en stem | Geaccepteerd geheugen en bestaande LiveKit-/huisintegratie behouden; samenhang nog te bewijzen | OpenClaw heeft kanaal/geheugenfuncties, Hermes profielen en geheugenproviders, Agent Zero projectgeheugen; Eva's fysieke stem- en gezinsketen vraagt integratiebewijs |
| Modulariteit en uitbreiden | Bestaande ports bruikbaar, 25 architectuurbevindingen vereisen gerichte reparatie | Plugins/tools bieden uitbreiding; de host wordt een extra contract- en upgradegrens voor Eva |
| Onderhoud en beveiliging | Minder vervangingswerk maar eigen code en grenzen blijven onderhoud; bestaande scan/updatevoorzieningen controleren | Recente releases en gepubliceerd beleid zijn signalen; geen bewezen lagere beheerlast of volledige veiligheid |
| Migratielast | Voorlopig 14–28 actieve agenturen voor de eerste keten, te herijken na nulmeting | Niet betrouwbaar geraamd: export/import identiteit/geheugen, mandaat, huis, stem en herstel eerst ontwerpen; geen verzonnen urenvergelijking |

De doorslag is behoud van bewezen persoons- en huiscontinuïteit met een kleinere
vervangingsgrens, niet een claim dat Pydantic intelligenter is. Een geïsoleerde
platformproef is nu niet uitgevoerd: upstreamcapaciteit alleen lost de gemeten
integratiegaten niet op. Wanneer GZ-001A aantoont dat de algemene uitvoerroute
fundamenteel ontbreekt, gaat de kleine Hermes-vergelijking vóór verdere eigen
plannerbouw. Een uitslag die minder hulp en beheer vraagt zonder verlies op de
harde grenzen kan deze aanbeveling veranderen.

### Keuzes per bouwsteen en vergelijking met oudere besluiten

| Verantwoordelijkheid | Besluit | Verschil met eerdere plannen |
|---|---|---|
| Agent/model/toolloop | Pydantic AI Slim en officiële model-SDK behouden; algemene loop één eigenaar | Geen nieuwe handgeschreven agentmotor of standaard extra Harness/ander platform; eerst concrete ontbrekende functie aantonen |
| Persoonsbinding, mandaat, actiecontrole | Eva-applicatiecontracten behouden en van transport losmaken | Noodzakelijke eigen semantiek; geen doel op zichzelf om iedere algemene handeling van extra bewijsinfrastructuur te voorzien |
| Geheugen | PostgreSQL voor gebeurtenissen/gezag; Hindsight voor semantisch geheugen | Geaccepteerde functie behouden; Honcho/Mnemosyne/Mem0 niet toevoegen zonder benoemd tekort en gemeten winst |
| Spraak | Bestaande LiveKit-route afronden; HA/audio-adapters aansluiten op dezelfde Eva | Oudere verplichte Pipecat-/Pydantic-Realtime-bake-offs vervallen als standaardvolgorde; opnieuw vergelijken bij gemeten tekort |
| Browser | Playwright als motor; gedeelde eigenaarstab en geïsoleerde taakbrowser onderscheiden | Twee gebruiksvormen zijn niet automatisch dubbele verantwoordelijkheid; gedeelde policy/actiecontrole hoort wel één implementatie te hebben |
| Tools | MCP en bestaande officiële providers; catalogusontdekking en protocol bij upstream waar passend | Oude SDK-afwijzing voor vier vaste POSTs bewijst niets over een groeiende algemene catalogus; heropen alleen die bredere transportrol |
| Achtergrondwerk | Eén duurzame taakstatus met stop, resultaat en hervatten; huidige opslag voorlopig behouden | DBOS-fork niet uitbreiden. Temporal/DBOS/Restate alleen beproeven wanneer ze scheduling/retry werkelijk overnemen; ze bewijzen geen fysiek exactly-once-effect |
| Dashboard en beheer | Bestaande React-admin en werkbord gebruiken | Geen nieuwe cockpit of observabilityplatform bouwen om deze herbeoordeling te presenteren |

Waargenomen upstream: Pydantic AI 2.46.0, LiveKit Agents 1.8.2, Hindsight
0.10.1, Playwright MCP 0.0.82, MCP Python SDK 2.2.0, DBOS 3.0.0,
Temporal Python 1.33.0 en Pipecat 1.11.0. Dit zijn onderzoeksversies,
**geen opdracht alle pins te upgraden**. Exacte commits en releasedata staan in
de upstreammomentopname. De bestaande bron pint onder andere Pydantic AI 2.37.0.

[Pydantic documenteert meerdere duurzame backends](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/);
dat ondersteunt verwisselbaarheid, niet een verplichte workflowmigratie.
[LiveKit ondersteunt onderbreking en turnafhandeling](https://docs.livekit.io/agents/logic/turns/);
Eva hoeft die media-infrastructuur niet opnieuw te bouwen.

Volgende onderhoudscontrole: uiterlijk 5 oktober 2026 en altijd vóór een nieuwe
kandidaatbuild; eigenaar is de projectcoördinator. Dan worden exacte betrokken
pins, advisories, ondersteunde versies en upgrade/terugweg gecontroleerd.

## Modulaire doelarchitectuur en migratie

1. **Kanalen:** Telegram, telefoon/laptopspraak en huisapparaten vertalen inbreng
   naar bestaande gespreksscope. Geen eigen geheugen, planner of succesbesluit.
2. **Eva-applicatie:** identiteit, relevante persoonscontext, gesprek, initiatief
   en taakbesluit. Geen imports naar concrete providers of bootstrap.
3. **Uitvoering:** één algemene agent/toolroute met taakgebonden mandaat,
   stop/cancel en resultaat. Lang werk blokkeert de volgende gespreksbeurt niet.
4. **Geheugen en taakopslag:** bestaande application-ports met PostgreSQL- en
   Hindsight-adapters; teruggevonden tekst kan geen rechten verruimen.
5. **Externe mogelijkheden:** MCP, browser, bestanden en HA als vervangbare
   adapters/processen. Verificatie is proportioneel aan het echte effect.
6. **Compositie/beheer:** bootstrap maakt adapters en injecteert ports;
   dashboard projecteert dezelfde opgeslagen status en meetgegevens.

Gebruik bestaande publieke contracten. Nodige interfaceveranderingen worden
beperkt tot: een gedeelde gespreks-/taakverwijzing tussen stem en chat, een
algemeen status/resultaat/stop-contract voor achtergrondwerk, en transportneutrale
dependency-injectie waar adapters nu bootstrap importeren. Geen nieuwe wire- of
databaseschema's vastleggen voordat de bestaande equivalenten zijn gecontroleerd.

Vervang per gebruikersketen, niet per framework in één grote sprong. Maak eerst
een reproduceerbare bron-/imagesnapshot; bewaar datakopie en vorige werkende
artifact. Bewijs pariteit in isolatie, schakel eenmaal om en verwijder daarna
alleen de aantoonbaar vervangen uitvoerroute. Geen dual-write van persoonsgeheugen.
Stop een migratie bij verlies van identiteit, historie, privacy, stopbaarheid of
herstel. Werkende terugweg wordt vóór promotie beproefd.

## Eerste bouwgoal GZ-001 — een avond met Eva, morgen verder

**Jaap kan via tekst en stem met dezelfde Eva een echte gezinsmiddag voorbereiden,
tijdens haar onderzoek blijven praten, een voorkeur corrigeren en de volgende
dag verdergaan. Eva onthoudt wat relevant is, neemt passend vervolginitiatief en
levert een bruikbaar voorstel met gecontroleerde informatie.** Bestaande
belangrijke huisfuncties blijven werken. Dit is één samenhangende gebruikersketen.

Standaardsituatie: een gezinsuitje met locatie, datum, interesses en praktische
grenzen die Jaap in het gesprek geeft. Geen aankoop, boeking of bericht aan derden.
Een datum of voorkeur wordt niet vooraf in de kern geprogrammeerd. Een andere
gezinsactiviteit moet via dezelfde route werken. Deze eerste oplevering vervangt
Bottie nog niet en claimt geen voltooide algemene autonomie.
Een geslaagd gezinsvoorstel bewijst ook geen algemene planning of zelfstandig
nieuwe skills bouwen. Die ambities blijven afzonderlijk vervolgwerk.

### Vaste eindvoorwaarden

- Gewone privéchat en bestaande laptop-/telefoonroute gebruiken dezelfde identiteit
  en bevoegde herinneringen. Geaccepteerde geheugenfuncties krijgen gerichte
  integratieregressie, geen nieuwe losse geheugenacceptatie.
- Een gesprek van minimaal tien minuten bevat eigen advies, tegenargument,
  correctie en verwijzende vervolgvraag. Jaap beoordeelt warmte, eigenheid en nut.
- Eva onderzoekt echte actuele opties, levert bronlinks en een concreet voorstel,
  en vermeldt ontbrekende gegevens. Minstens één ongeziene variant slaagt zonder
  taakspecifieke kerncode of operatorvoorbereid documentplan.
- Tijdens onderzoek kan Jaap onderbreken, een andere vraag stellen en expliciet
  stoppen. Onderbreken van audio en stoppen van werk zijn onderscheiden acties.
- De volgende dag hervat Eva het voorstel vanuit de gecorrigeerde context zonder
  opnieuw alles uit te vragen. Eén relevante aanleiding wordt gebruikt voor
  passend vervolg; een stiltegeval levert geen onnodig bericht op.
- Een bestaande speaker-/huisroute en deurbel-/stiltegedrag krijgen een gerichte
  behoudcontrole tegen Bottie. Aanwezige Bottie-functies blijven beschikbaar zolang
  Eva2 die niet gelijkwaardig heeft bewezen; geen dubbele spontane uitvoerder.
- Onbekende spreker krijgt geen privaat geheugen. Cross-person regressies gebruiken
  synthetische profielen; een echte tweede persoon wordt pas met diens bestaande
  of expliciet verleende toegang beproefd.
- Resultaat en taakstatus blijven na een geïsoleerde herstart beschikbaar; er
  ontstaat geen dubbele actie of bericht. Productie wordt niet voor een proef gecrasht.

### Snelheid, intelligentie en verbruik

Behoud de bestaande PC/V-richting: gewone korte antwoorden p95 eerste
betekenisvolle inhoud ≤2 s en compleet ≤5 s; hoorbaar onderbreken p95 ≤250 ms.
Een ontvangstzin telt niet als betekenisvol antwoord. Onderzoek mag langer duren,
met korte eerlijke terugmelding en bruikbare tussentijdse gesprekservaring.

Meet voor deze tussenoplevering 30 korte beurten en 20 onderbrekingen, met cold/warm
apart, fouten in de noemer en ruwe tijdpunten. Dat is beperkt opleverbewijs; de
bestaande volledige releasepoort behoudt haar grotere steekproef en duurproef.
Niet-gehaalde grenzen blijven rood en worden niet achteraf verruimd. Recente
gewone antwoorden van circa 7,9–12,3 seconden tonen een wezenlijke onzekerheid
tegenover deze doelen, geen bewezen p95. Na de nulmeting volgt hoogstens één
gerichte profiel-/verbeteringsronde. Vereist de grens een ander model, fundamentele
architectuurwissel of meer werk dan de opleverband, dan volgt direct een
herbesluit met meetbewijs; geen eindeloze optimalisatiecyclus.
Meet context, model/toolcalls, retries en gebruik per resultaat.
Geen zelfbedacht dagelijks runtimequotum en geen onbekende geldkosten als nul.

Intelligentie wordt beoordeeld op correct begrip, bruikbare afweging, verwerken
van gewijzigde eisen, zelfstandige toolkeuze, foutcorrectie en resultaatkwaliteit.
Een snel maar oppervlakkig of onjuist antwoord slaagt niet.

### Uitvoervolgorde en eindpunt

| Stap | Afgebakend resultaat | Raming actieve agenttijd, geen kalenderbelofte |
|---|---|---|
| GZ-001A | Reproduceerbare huidige basis en één nulmeting van de hele gebruikersketen; benodigde fixes exact bekend | 2–4 uur |
| GZ-001B | Geraakte modulegrenzen herstellen en gesprek/geheugen/stem/achtergrondresultaat verbinden | 6–12 uur |
| GZ-001C | Ongeziene variant, behouden huisroutes, stop/herstel en prestatiecontrole; gerichte reparatie | 4–8 uur |
| GZ-001D | Gebundelde onafhankelijke review, één geïntegreerde kandidaat en werkende terugweg | 2–4 uur |
| Gebruikersproef | Tien minuten in een eerste sessie en kort vervolg op de volgende dag; fysiek luisteren en gezinswaarde | Twee afspraken, afzonderlijk van agenttijd |

Totaal voorlopig 14–28 actieve agenturen. Geen omgerekende credits: die verhouding
is onbekend. Na A wordt de band met de nulmeting aangescherpt. Blijkt de benodigde
wijziging buiten deze uitkomst te vallen of de band wezenlijk te overschrijden,
dan volgt één concreet herbesluit met oorzaak en alternatief; geen stille nieuwe
bouwstroom. Maximaal twee uitvoerders naast de coördinator, reviews binnen die
bezetting. Een herhaling vereist een wijziging, nieuwe hypothese of ontbrekend bewijs.

Technisch klaar: de objectief toetsbare keten op één kandidaat, gerichte tests en
onafhankelijke review groen, installatie/readback/terugweg bewezen, gebruiksinstructie
beschikbaar. Een gescripte technische variant toetst de gesprek-/vervolgketen;
warmte, eigenheid, fysieke luisterervaring en gezinswaarde worden pas door Jaap
beoordeeld en zijn geen door de ontwikkelaar invulbare technische vink.
Eigenaaracceptatie blijft apart; een open luisteroordeel blokkeert
onafhankelijk technisch vervolgwerk niet. Nieuwe platformfeatures, extra kanalen,
algemene skillgeneratie, meerdaagse bedrijven en officiële publieke benchmarks
blijven zichtbaar vervolgwerk. De zeven dagen eindpraktijk horen bij de complete
vervangkandidaat, niet bij iedere kleine reparatie.

## Eerlijke vergelijking: vóór uitvoering vastzetten

Het bestaande [EV-003-protocol](design/product/comparable-agent-benchmarks-v1.md)
blijft de enige norm voor algemene concurrentiewinst: zes families, twaalf
ongeziene gevallen per familie, drie herhaalblokken en de bestaande vooraf
vastgelegde drempels, waaronder minimaal 85% taakslagen en tien procentpunt
voorsprong op ieder van de drie concurrenten. Geen nieuwe scorer of parallel
benchmarkprogramma maken; gebruik de bestaande Inspect-route.

Leg binnen dat protocol de gevraagde gedragingen vast: gesprek/correctie,
uitgesteld geheugen, stem tijdens werk, initiatief/stilte, ongeziene taak,
vaardigheidshergebruik en stop/herstel met huis-/persoonsgrenzen. Het zijn
uitwerkingen van de bestaande families. Voor de eerste ontwikkelvergelijking
geldt de bestaande kleine grens: twee cases, ieder tweemaal. Kies het
gecorrigeerde gezinsvoorstel en stoppen/hervatten daarvan; één variant blijft
onbekend voor de bouwer. Bind manifest, verwachte uitkomst, model, rechten,
tools, versies en beoordelaar vooraf. De GZ-001-prestatiesteekproef meet Eva
zelf en wordt niet automatisch voor iedere concurrent herhaald.

Native productproeven en gelijk-modelproeven blijven gescheiden. Begin met
Bottie als dagelijkse ondergrens en Hermes als kandidaat voor algemene taakuitvoering;
OpenClaw en Agent Zero blijven vereist vóór een algemene concurrentiewinstclaim.
Een niet beschikbare capability of onmogelijke opstelling wordt als zodanig
gerapporteerd, niet als nulscore die een kunstmatige winst oplevert.

Per taak: uitkomst geslaagd/mislukt, correctheid en bruikbaarheid, menselijke
interventies, duur, model/toolcalls, gemeten tokens en kosten of `onbekend`,
herstel en privacy. Nul privacylekken, dubbele effecten en gefingeerde successen
is een harde voorwaarde. Rapporteer per categorie; geen zelfverzonnen totaalscore.
Een voorsprong vraagt herhaalde betere gebruikersuitkomsten zonder verlies op
die harde voorwaarden. Deze audit voert geen nieuwe modelvergelijking uit.

Dezelfde gevallen krijgen vóór de eerste run onderstaande controlevelden.
Een beperkte ontwikkelproef kiest daaruit alleen de relevante gevallen; ontbrekende
categorieën blijven expliciet niet gemeten. Deze tabel vervangt EV-003 niet.

| Gedrag | Vooraf vast te zetten opdracht en slaagcriterium |
|---|---|
| Gesprek en geheugen | Voorkeur bespreken, één correctie geven, later in een nieuwe sessie relevant terugvragen; actuele correctie gebruikt, oude waarde niet als actueel gepresenteerd en geen persoonslek |
| Stem tijdens werk | Dezelfde inhoudelijke vraag terwijl onderzoek loopt, spreken onderbreken en doorpraten; onderzoek/status behouden en de vastgelegde latencygrenzen gemeten |
| Initiatief | Eén geldige aanleiding en één stiltegeval aanbieden; werkelijk gecontroleerd resultaat bij de eerste, geen onnodige melding bij de tweede |
| Ongeziene opdracht | Nieuwe gezinsactiviteit met niet vooraf gecodeerde eisen; resultaat voldoet aan alle vooraf vastgelegde eisen, bronnen bestaan, geen operatorplan of verzonnen voltooiing |
| Leren | Eén werkwijze opdoen en in een andere ongeziene opdracht toepassen; opgeslagen werkwijze wordt aantoonbaar opgehaald en juist gebruikt, geen rechtstreekse tweede instructie van operator |
| Stop en herstel | Stop op vooraf gekozen grens, geïsoleerd onderbreken en hervatten; bewaarde status/resultaten intact, geen dubbele effecten en onzekere effecten eerst teruggelezen |
| Huis en persoonsgrenzen | Bestaande representatieve huisactie plus deurbel/stilte en onbekende spreker; bestaand gedrag behouden en nul ongeoorloofde gegevensoverdracht |

Leg ook het toegestane mandaat, gelijke hulpmiddelen, startdata, beoordelaar,
ruwe tijdpunten, interventies en runtimegebruik vast. Onderhoudslast wordt als
werkelijke installatie-/herstelhandelingen en bestede tijd geregistreerd,
niet afgeleid uit sterren of commitfrequentie. Gezinsbeleving staat naast deze
metingen en krijgt geen door de ontwikkelaar ingevuld cijfer.

## Uitvoering van deze herbeoordeling

De coördinator onderzocht primaire documentatie, upstream en de live projectbron.
Twee begrensde opdrachten verdeelden architectuur/historie en bewijs/proces;
hun rapporten registreren leeswijze en uitgevoerde controles. De gebundelde
eindreview gebruikt dezelfde bezetting. Er zijn geen Eva-modelproeven,
concurrentinstallaties of deployments uitgevoerd. Ontwikkelverbruik wordt apart
geregistreerd; een toolteller is geen factuur of bekende creditprijs.
De [verbruiksregistratie](evidence/reassessment/assessment-usage-2026-09-21.json)
bevat een tussentijdse ruwe goalteller en de grenzen van die meetbaarheid.

Het bestaande werkbord bevat GZ-001 als eerste geplande oplevering en de
herbeoordeling in zijn actuele toelichting. De oude AU-008-reviewkaart bewaart
haar bestaande commitbewijzen en open menselijke beoordeling. Die kaart is
geen nieuwe bouwopdracht. Promotie van GZ-001 naar een implementatiekaart gebeurt
bij de bouwstart met concrete bestandsgrenzen en tests; hier wordt geen
uitgevoerde implementatie of nieuwe release verzonnen. Ook het lokale dashboard
leest de nieuwe planning en actuele concurrentversies uit dezelfde JSON-bron.

## Proberen zodra GZ-001 technisch beschikbaar is

Gebruik de bestaande privé-Telegramchat met `/eva2` en daarna *Praten met Eva*
in Home Assistant of de [laptoproute](http://localhost:44716).
Vraag: “Help me een gezinsmiddag voorbereiden”, geef echte voorkeuren, corrigeer
er één en stel tijdens onderzoek een andere vraag. Vraag de volgende dag naar
het voorstel. Verwacht één samenhangende Eva, een bruikbaar gecontroleerd resultaat
en de bijgewerkte voorkeur. Deze instructie is een geplande proef, geen claim dat
de volledige nieuwe keten nu al werkt.
