# Onafhankelijke review — duurzame subagentproef en werkbord

Datum: 26 augustus 2026.

Beoordeelde implementation commit:
`c0317559e9ff5fd6eec590db78d4d143be8a5900`.

## Besluit

De drie vaste rollen geven **GO voor de smalle status `tested`**. Deze review,
de testreceipt en alle nieuwe cockpitclaims zijn exact hashgebonden; een schone
staged archive gaf de volledige gerichte reeks **95/95 groen**, waaronder de
dashboard- en evidencechecks **14/14 zonder uitzondering**.

Dit is geen `live_proven`, productie-, managed-B- of complete-subagentclaim.
Bewezen is één providerloze technische lijn: een toegelaten proeftaak wordt
duurzaam opgeslagen, geleased, precies één keer aan de Pydantic-testmotor
gegeven, duurzaam afgerond en als veldarme kaart in de read-only cockpit
getoond. Een herstart na `call_started` veroorzaakt geen tweede modelrun.

## Onafhankelijk beoordeeld en uitgevoerd

- De technische reviewer vond aanvankelijk één Ruff-fout (`E731`). Die is
  hersteld; de gerichte Ruff-run en `git diff --cached --check` zijn groen. De
  nieuwe subagent- en workboardpaden voegen geen package-boundary-overtreding
  toe. De repositorybrede checker blijft rood op oudere, buiten-scope imports.
- De productreviewer vond te brede woorden, één oude tegenstrijdige statusregel,
  een onjuiste kleur voor gestopt werk en ontbrekende browservalidatie. De
  cockpit spreekt nu over opgeslagen proeftaken, vertaalt workerstatussen,
  kleurt alleen goedgekeurd resultaat groen en controleert dezelfde
  resultaat-kolommapping als de server. Eigenaar-authenticatie staat zichtbaar
  als open grens vermeld.
- De release-reviewer bevestigde dat productlock `pydantic-ai-slim==2.32.1`
  blijft staan, terwijl Pydantic AI `2.34.0` en Harness `0.25.0` een aparte
  kandidaat blijven. De kandidaatlock heeft SHA-256 `f083fb1a…`; `pip check`
  is groen en 22 Harnesschecks lopen werkelijk op versie `0.25.0`. Migratie
  `4e_0003` is bewust geen nieuwe managed-B-
  authority; managed B blijft op `4e_0002` totdat portable-v2 en de beheerde
  release apart sluiten.
- Een schone Git-archive van de implementation commit gaf **94 geslaagd** met
  alleen de zelfreferentiële evidencebinding tijdelijk uitgesloten.
- De providerloze Pydantic/Harness-set gaf **54 geslaagd**; dezelfde
  productrunner en workboardcontracten gaven op Eva's huidige Pydantic
  `2.32.1`-lock **32 geslaagd**.
- Twee echte disposable PostgreSQL 16.13-proeven gaven **13 + 1 geslaagd**.
  Concurrency, scope/RLS, intrekking, crash vóór/na call-start, resultaat,
  usage, Kanbanprojectie, upgrade en downgrade zijn daarin geraakt.

De uitvoeringsdetails staan in
`evidence/cockpit/runtime-subagent-workboard-c031755-test-receipt.json`.

## Gesloten eerdere blockers

- De worker gebruikt vóór iedere modelrun een duurzame eenmalige call-start-
  fence. Een nieuwe worker krijgt na een onopgeloste call-start geen callable
  taakbytes.
- De actuele persoon-/bindingautoriteit wordt onder database-lock opnieuw
  gecontroleerd; intrekking kan niet ongemerkt langs een race glippen.
- Resultaat, usage, fout of ambiguïteit worden tegen gesloten contracten en
  hashes bewaard. Onbekend gebruik heet `unmeasured`, nooit stil nul.
- Het bord toont alleen vertrouwde displaylabels en beperkte statusvelden. Ruwe
  opdrachttekst, interne IDs en ruwe resultaten verlaten de projectie niet.
- De cockpit bevat geen hardgecodeerde voorbeeldkaarten. Ontbrekende data wordt
  als niet beschikbaar getoond.

## Bewuste open grenzen

- geen echte modelprovider, providernetwerk, providerrekening of provider-SLA;
- geen apart workerproces, processandbox of harde kill;
- geen spawn, stop, parenthervatting of bordwrite vanuit Eva-chat;
- geen eigenaar-authenticatie op de lokale cockpitroute;
- geen portable-v2-export, managed-B-schemareseal, B-deploy of livegebruik;
- geen user acceptance en geen bewijs dat WB01-WB07 als complete productgates
  gesloten zijn.

Zodra een van die uitkomsten wordt geclaimd, is een eigen verticale proef en
nieuwe onafhankelijke review verplicht.
