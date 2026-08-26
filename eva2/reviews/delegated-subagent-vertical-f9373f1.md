# Onafhankelijke review — Eva kan één onderzoeker veilig aansturen

Datum: 26 augustus 2026.

Beoordeelde broncommit:
`f9373f19e1b19adad554264ea24543521ae741df`.

## Besluit

**GO voor `tested` als lokale kandidaat.** Eva heeft nu niet alleen een losse
subagentproef. De complete smalle lijn bestaat: een geverifieerde eigenaar kan
vanuit Eva's privéchat één afgeschermde onderzoekstaak starten, de duurzame
status bekijken en de taak ook tijdens de modelcall stoppen. De taak draait in
een apart workerproces en komt terug op hetzelfde PostgreSQL-werkbord.

De worker gebruikt bestaande communitysoftware: Pydantic AI `2.32.1`, de
officiële OpenAI-SDK `3.3.1`, Psycopg `3.3.4` en SQLAlchemy `2.0.52`. Eva bouwt
dus geen eigen modelclient, agentmotor of taakdatabase. Haar eigen code bewaakt
alleen wie de taak gaf, welk budget geldt, of een call al begon, wat de status
is en wat zij eerlijk aan de eigenaar mag terugmelden.

Dit is nog geen productie- of gebruikersclaim. De echte Telegramnetwerkroute,
een externe providercall en managed B zijn niet gebruikt.

## Wat nu werkelijk af is

- Eén toolvrije onderzoekstaak kan via de gewone Eva-commandolaag duurzaam
  worden gestart, gevolgd en gestopt.
- Twee workers starten samen nooit tweemaal dezelfde modelcall.
- Na herstart wordt een begonnen modelcall niet blind herhaald.
- Stoppen tijdens de officiële-SDK-call annuleert de lokale call, bewaart de
  stop en levert het late resultaat niet aan Eva.
- Stop versus succes en stop versus fout hebben één racevrije winnaar.
- Dezelfde stopopdracht krijgt vóór en na workerafronding bytegelijk antwoord.
- Na een harde procesdood zegt het bord eerlijk `contact kwijt` en `onzeker`.
- De worker heeft een eigen smalle image van 86,6 MB met 24 hashgebonden wheels,
  draait als `65532:65532`, read-only en zonder extra Linuxrechten.
- De echte image-healthcheck controleert de private modellease, database en
  schemahead `4e_0004` zonder een providercall te maken.
- Het echte residentcommando werd gezond, stopte op SIGTERM met exitcode nul,
  schreef geen loginhoud en liet geen workercontainer achter.

## De eerdere NO-GO en de reparatie

De eerste review vond vier blockers. Stoppen en afronden konden elkaar door een
omgekeerde lockvolgorde blokkeren; dezelfde stopopdracht kon na afronding anders
klinken; PostgreSQL liet een onmogelijke stoptijdlijn toe; en een verdwenen
worker werd na een stopverzoek nog actief genoemd.

De herreview heeft alle vier oorspronkelijke proeven opnieuw uitgevoerd. Beide
racepaden nemen nu eerst de werkbordrij en daarna de subagentrij. PostgreSQL
weigert afronding vóór het stopmoment. De tekst is toestand-onafhankelijk en een
verlopen lease wordt `contact_lost / ambiguous`. Het besluit veranderde daarop
van NO-GO naar GO.

## Onafhankelijk herhaald

- 7 command- en installatieschematests;
- 28 PostgreSQL-, proces-, race- en lokale officiële-SDK-tests;
- 1 aparte migratie- en downgradeproef;
- gerichte Ruff-controle;
- extra afgedwongen raceproeven met zowel stop als terminale uitkomst als
  winnaar.
- De image-auditor vergeleek 769 contextbestanden met een eigen Git-archive,
  184 verpakte bronbestanden met die context en 2.910 wheelbestanden met hun
  RECORD-hashes. De 24 requirements, 24 wheels en vier Python-basislagen sloten
  exact. De beperkte healthcheck gaf exitcode nul en maakte geen subagentcall.

De hoofdproef voegde daar 40 componenttests, dezelfde 28 verse
PostgreSQL-tests, de aparte migratieproef en vier image-/installatietests aan
toe. De precieze hashes, containergrenzen en nonclaims staan in
`evidence/cockpit/delegated-subagent-vertical-f9373f1-test-receipt.json`.

## Wat nog open is

- één eigenaar-goedgekeurde externe provider-canary met een klein budget;
- een heropenbaar buildreceipt plus commitlabel, SBOM, kwetsbaarheidsscan en
  imagehandtekening; de huidige lokale image-inhoud is wel bytegelijk
  gecontroleerd, maar het buildlog is leeg;
- één echte Telegramnetwerkproef op de kandidaat, niet alleen de bronroute;
- opname in een nieuwe beheerde B-release na schema-, backup-, SBOM-, scan- en
  rollbackbewijs;
- meerdere gelijktijdige onderzoekers, tooltoegang en interactieve korte
  Harness-subagents;
- eigenaar-authenticatie en schrijfbediening voor het visuele werkbord;
- gebruikersacceptatie.

De eerstvolgende stap blijft daarom binnen ditzelfde project: de lokale image-
en bewijsbinding afronden en pas daarna een expliciet toegestane echte canary.
