# Eva leren en persoonsmodel v1

Bijgewerkt: 25 augustus 2026.

## Productbelofte

Eva moet merkbaar met haar mensen meegroeien. Zij leert niet alleen losse
feitjes, maar bouwt per geverifieerde persoon een corrigeerbaar beeld op van
voorkeuren, gewoonten, relaties, terugkerende doelen, communicatiestijl en wat
eerder wel of niet hielp. Dat persoonsmodel blijft gescheiden van andere
personen en van gedeeld groepsgeheugen.

Eva mag voor procedurele groei niet afhankelijk zijn van een skillstore of een
vooraf gevulde catalogus. Als een nieuwe taak daarom vraagt, moet zij binnen het
chatmandaat zelf on the fly een kleine skillkandidaat kunnen ontwerpen,
uitproberen, corrigeren, versieeren en later hergebruiken. Een externe store,
`SKILL.md`-bundle of MCP-server is hoogstens optionele input; Eva blijft ook
zonder zo'n bron nieuwe werkwijzen kunnen maken.

Groei mag Eva niet steeds duurder, trager of eigenwijzer maken. Iedere
herinnering en procedure heeft daarom herkomst, audience, tijd, vertrouwen,
laatste gebruik, correctiestatus en een retentie- of vervaldatum. De hot path
krijgt vaste context- en tokenbudgetten; oude of weinig bruikbare kennis wordt
samengevat, naar cold storage verplaatst of verwijderd zonder een correctie of
belangrijk actief doel stil te verliezen.

## Eerlijke huidige stand

- Persoonsgebonden feiten, voorkeuren en expliciete correcties worden nu via
  PostgreSQL en Hindsight onthouden en per persoon/project geïsoleerd.
- Recall heeft configureerbare niveaus, 2.000–4.000 contexttokens en 20–100
  retrieval-nodes. Retain en reflectie lopen buiten de gespreks-hot-path.
- Native M01 bewees via de definitieve Telegramconnector tegen een lokale Bot
  API-contractprovider dat één voorkeur bij een latere webbeurt van dezelfde
  persoon bleef en nul geheugen van een tweede persoon lekte. Dit was geen
  Telegramnetwerkproef. Native I02 bewees één begrensde, zelfgekozen
  repositoryverbetering met tests en readback.
- Dit is nog **geen** volledige Hermes-achtige gesloten leercyclus. Eva maakt
  en verbetert nog niet zelfstandig duurzame procedurele skills op basis van
  herhaald gebruik, met automatische evaluatie, promotie en rollback.
- Eva traint haar modelgewichten niet zelf. Een beter extern basismodel is een
  providerupgrade; dat mag niet als Eva's eigen geleerde ervaring worden
  geclaimd.
- Honcho 3.0.12 is voor LG01 een optionele self-hosted persoonsmodelkandidaat,
  naast en niet in plaats van Hindsight. Alleen de afgeleide, corrigeerbare
  representation/peer-cardfunctie is relevant; Honcho krijgt geen identiteit,
  consent of geheugenautoriteit en mag zonder latencybewijs niet iedere beurt
  een extra reasoningcall doen. Zie
  `design/product/tiered-memory-and-person-model-v1.md`.

## Drie soorten groei

| Laag | Wat Eva mag leren | Promotiegrens |
|---|---|---|
| Persoonlijk/episodisch | Feiten, voorkeuren, correcties, relaties, toon, terugkerende doelen en nuttige eerdere uitkomsten | Automatisch na geverifieerde bron en audiencecontrole; een relatie-afleiding blijft een onbevestigde private kandidaat en wordt nooit identiteit of toegangsautoriteit |
| Procedureel | On the fly een nieuwe herbruikbare werkwijze of optionele `SKILL.md` maken wanneer de taak die nog niet kent; bestaande of externe skills mogen alleen als input dienen | Eerst quarantaine, provenance, permissiondiff, held-out tests, security-, latency- en tokenbudget; alleen laag risico mag binnen een vooraf chatvastgelegd leermandaat automatisch promoveren |
| Product/runtime | Code, dependencies, IdentityPack, policy, permissies, schema of release | Nooit stil in de draaiende kern; kandidaatbranch/image, onafhankelijke tests/review, ondertekende promotie en directe rollback |

De leercyclus mag nooit zelf haar eigen mandaat, eigenaar, identity-trustroot,
privacygrenzen, financiële limieten of toolpermissies verruimen. De eigenaar kan
die grenzen wel bewust via de Eva-chat wijzigen; dat is een nieuwe
geautoriseerde beslissing, geen gevolg van zelfleren.
Leren mag evenmin principals, account- of connectorbindings, groepslidmaatschap,
audiences, consentgrants, eigenaarschap of permissions aanmaken of wijzigen.
Een herinnerde of afgeleide relatie geeft dus nooit toegang tot iemands data of
tools.

## Gesloten groeicyclus

1. Een echte ervaring krijgt een duurzame bron-, persoon-, audience-, taak-,
   uitkomst- en kostenreceipt.
2. Eva onderscheidt een persoonlijk feit, een mogelijke voorkeur, een
   correctie, een procedurele les en een mogelijke productbug. Twijfel blijft
   zichtbaar kandidaat.
3. Als geen passende procedure bestaat, ontwerpt Eva on the fly de kleinst
   mogelijke procedurele kandidaat. Die gaat naar een geïsoleerde,
   content-addressed quarantaine zonder secrets of nieuwe permissies; het
   ontbreken van een skillstore is geen blocker.
4. De kandidaat draait tegen vooraf verzegelde succesvolle, mislukte,
   privacy-, crash- en held-out gevallen. De bestaande versie draait tegelijk
   als baseline.
5. Promotie vereist aantoonbaar betere taakuitkomst zonder regressie in
   waarheid, privacy, latency, tokens, kosten, codeomvang of crashherstel.
6. De actieve versie, evidence en geldigheidsduur worden duurzaam vastgelegd.
   Eva kan direct naar de vorige versie terug en een gebruiker kan haar in de
   chat stoppen, corrigeren of een geleerde regel laten vergeten.
7. Na echt gebruik meet Eva opnieuw. Een skill die verslechtert, veroudert of
   nauwelijks wordt gebruikt gaat terug naar staged, wordt vervangen of
   vervalt; skills stapelen niet onbeperkt op.

## Persoonsmodel en privacy

- Iedere recall bindt vóór selectie aan de geverifieerde persoon en expliciete
  audience: privé, exact groepspubliek of een begrensd project.
- Eva bewaart onderscheid tussen `door persoon gezegd`, `extern waargenomen`,
  `door Eva afgeleid` en `door persoon gecorrigeerd`. Een afleiding wordt nooit
  stil als letterlijk gebruikersfeit gepresenteerd.
- Nieuw bewijs mag een oude voorkeur verzwakken; een expliciete correctie
  supersedeert de oude projectie maar bewaart de auditbare historie.
- Groepsgesprekken bouwen geen verborgen privéprofiel van andere aanwezigen.
  Na intrekking bereikt groepsgeheugen de context niet meer.
- De gebruiker kan in de chat vragen wat Eva over hem/haar gebruikt, iets
  corrigeren, privé maken, delen, laten verlopen of vergeten. Het antwoord
  toont een begrijpelijke samenvatting en lekt geen interne secrets of gegevens
  van anderen.
- `Vergeet dit` maakt de inhoud niet alleen onzichtbaar in de actuele projectie:
  dezelfde inhoud wordt niet-opvraagbaar verwijderd uit Hindsight, actieve
  PostgreSQL-projecties, hot/cold-samenvattingen, caches en procedurele
  kandidaten. Alleen een niet-inhoudelijke tombstone met scope, tijd en
  erasure-status blijft om replay of herintroductie te blokkeren. Back-ups
  volgen een verzegelde retentie-/vervalroute en mogen gewiste inhoud na restore
  niet opnieuw actief maken.

De eerste uitvoerbare deelgrens staat nu: voor één exact, al bewaard semantisch
Hindsight-document kan de juiste persoon een duurzame verwijdering claimen. Een
verkeerde persoon en een dubbele claim worden geweigerd; na procesuitval wordt
dezelfde providerpoging hervat en pas na providerbevestiging volgt de
niet-inhoudelijke tombstone. Dit bewijst nog niet `Vergeet dit` als complete
gebruikersfunctie: het PostgreSQL-gespreksjournal, hot/cold-samenvattingen,
caches, procedurekandidaten, back-ups en eigenaar-geauthenticeerde chatroute
blijven afzonderlijk open.

## Geheugen-, token- en kostenbudget

De exacte eindwaarden worden op de bevroren image gemeten; tot die tijd zijn
dit fail-closed ontwerpgrenzen:

| Budget | V1-grens |
|---|---:|
| Hindsight-recallcontext per gewone beurt | configureerbaar 2.000–4.000 tokens; nooit erboven |
| Retrieval-nodes per recall/reflection | configureerbaar 20–100; dezelfde bovengrens voor low/mid/high |
| Totale modelinput gewone tekstbeurt | p95 maximaal 12.000 tokens, hard maximaal 16.000 |
| Modeloutput gewone tekstbeurt | standaard maximaal 1.200 tokens; expliciete documenttaken krijgen een apart taakbudget |
| Persoonsprofielsamenvatting in hot context | maximaal 1.000 tokens per beurt |
| Duurzaam geheugengebruik | per persoon, groep, project en installatie een exact byte-/recordquotum in LG03; `unlimited` is op de eindimage niet geldig |
| Achtergrondreflectie | eigen dagbudget per persoon en installatie; nul werk in tijd tot eerste antwoordinhoud |
| Groei van vaste probe-input na dag 1 | maximaal 5% over de zevendaagse run |
| Onverklaarde token-/kostengroei tegenover baseline | maximaal 20% p95; daarboven geen promotie |

Tokenmeting bewaart per modelcall provider/model, route, input-, cached-input-,
reasoning- en outputtokens, latency en geschatte kosten. Conversatieinhoud of
geheugen hoeft daarvoor niet in een kostenreceipt te staan. Ontbrekende usage
wordt `unknown` en telt niet als nul. Ontbrekende of onbekende token-, kosten-,
latency- of opslagusage weigert automatische promotie en maakt LG07 rood.
Dashboard en chat tonen eigen totalen per dag, taaksoort en provider. Alleen een
expliciet daarvoor geautoriseerde beheerder ziet geaggregeerde installatietotalen;
Eva toont nooit benoemde of herleidbare usage van een andere persoon.

Compactie is inhoudelijk gecontroleerd: Eva vergelijkt vóór/na of actieve
doelen, expliciete correcties, belangrijke relaties, consent en open blockers
behouden zijn. Een kleinere prompt is geen succes wanneer betekenis verdwijnt.
De huidige installatie is nu zonder inhoud of identifiers als PostgreSQL-
aggregaten gemeten. De kandidaat handhaaft daarop harde bronrecord- en
UTF-8-bytequota per persoon, groep, project en installatie, ook bij races,
herstart en verwijderen. LG03 blijft rood totdat ook fysieke database- en
vectorindexgroei, cold-compaction en betekenisbehoud aantoonbaar begrensd zijn.

## Kleine opleverstukken

- [x] LG00 — Hermes' officiële groeiclaim, Eva's huidige geheugen/I02-bewijs,
  de ontbrekende procedurele cyclus en deze nonclaims eerlijk vastgelegd.
- [ ] LG01 — Duurzame persoonsmodelprojectie met bronsoort, vertrouwen,
  correctie, laatste gebruik, audience en verval; chatroute voor inzien,
  corrigeren en volledige erasure met non-content tombstone. Geen geleerde
  relatie mag identiteit, binding, consent, eigenaarschap of toegang wijzigen.
  De exacte semantische Hindsight-documentverwijdering is herstartveilig getest;
  de volledige keten en gebruikersroute blijven open.
- [ ] LG02 — Per call en per taak token-, latency- en kostenreceipt plus
  dashboard/chat-totalen; ontbrekende providerusage blijft `unknown`.
- [ ] LG03 — Bronrecord- en UTF-8-bytequota zijn getest per persoon, groep,
  project en installatie; fysieke opslagquota, cold-compaction en
  betekenisbehoudtest blijven open. Geen onbegrensde profiel- of contextgroei.
- [ ] LG04 — Zelf on the fly een procedurele skillkandidaat maken wanneer geen
  passende werkwijze bestaat, en die vanuit echte ervaring naar quarantaine
  brengen met exacte provenance, digest, permissiondiff en geen secrettoegang;
  geen store of vooraf bestaande skill is vereist.
- [ ] LG05 — Baseline/held-out evaluatie en automatische lage-risicopromotie
  uitsluitend binnen chatmandaat; overige kandidaten blijven staged.
- [ ] LG06 — Versie, gebruiksfeedback, degradatiedetectie, veroudering,
  stopknop en éénstapsrollback van iedere geleerde procedure.
- [ ] LG07 — Zevendaagse persoons- en skillproef bewijst betere herhaaltaken,
  correcte personenscheiding, nul stille drift en groene geheugen-, token-,
  kosten-, latency- en codebudgetten.

LG01-LG06 sluiten vóór freeze; LG07 draait op dezelfde eindimage als de andere
zevendaagse acceptatieproeven.

## Primaire Hermes-bronnen

- Hermes-documentatie, gesloten leercyclus en persoonsmodel:
  <https://github.com/NousResearch/hermes-agent/blob/main/website/docs/index.mdx>
- Hermes README, skillcreatie en verbetering tijdens gebruik:
  <https://github.com/NousResearch/hermes-agent/blob/main/README.md>

Deze bronnen bewijzen Hermes' gedocumenteerde productfunctie. Wij hebben die
volledige leercyclus niet zelf live tegen Eva uitgevoerd.
