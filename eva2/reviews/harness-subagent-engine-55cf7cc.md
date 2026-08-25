# Onafhankelijke herreview — Harness-subagentengine

Datum: 26 augustus 2026

Kandidaatcommit: `55cf7cc2c403c20c6571e230f13c9ffe89a9ed5b`

Basiscommit: `00482a3` met begrenzingsfixes in `bbe688d` en `55cf7cc`

Uitkomst: **GO, uitsluitend als geïsoleerde toolvrije/effectvrije enginekandidaat**

## Wat hiermee is beslist

Eva hoeft de generieke subagentmotor niet zelf te bouwen. Pydantic AI `2.34.0`
en Harness `0.24.0` maken en draaien de child, scheiden diens context, tellen
requests/tokens, begrenzen delegatiecalls en leveren een coöperatieve async-
time-out. Eva's dunne laag bepaalt alleen wie de child is en welke gesloten
grenzen gelden.

Dit is nog geen Eva-runtimefunctie. PostgreSQL-handoff, taaktoestemming,
child-usage-receipt, harde processkill, crashherstel, tools, externe effecten en
Kanban blijven buiten deze kandidaat.

## Exacte dependencygrens

- productbasis: 38 distributies;
- kandidaat: 42 distributies;
- gewijzigde bestaande packages: `pydantic-ai-slim` en `pydantic-graph` van
  `2.32.1` naar `2.34.0`;
- toegevoegd: `pydantic-ai-harness==0.24.0`, `httpx==0.28.1`,
  `httpcore==1.0.9` en `certifi==2026.7.22`;
- exacte hashes staan in
  `experiments/harness-subagents-v1/requirements.lock`;
- Eva's productlock en productie A/B zijn niet gewijzigd.

## Wat de onafhankelijke reviewer opnieuw heeft aangevallen

- een echte parenttool bereikt de child niet;
- een disk-agentdefinitie wordt niet ontdekt;
- de child krijgt geen gedeelde capability of recursieve delegatie;
- een vooraf geconfigureerde `Agent` kan niet als verborgen model worden
  binnengesmokkeld;
- lege, dubbele, ongeldige en niet-string namen worden geweigerd;
- callable, list, object, lege en meer dan 4.000 tekens lange instructies
  worden vóór childconstructie geweigerd;
- booleans, `NaN`, oneindige of te ruime budgetten worden geweigerd;
- inputtokens worden vóór de providercall geteld;
- de callgrens stopt een tweede delegatie;
- een trage async-child wordt coöperatief geannuleerd;
- een onverwachte childcrash wordt niet als tooltekst aan het parentmodel
  aangeboden.

De afsluitende run was groen voor **22/22 tests**, Ruff, `pip check` en de
diffcontrole. De adversariële instructietest bevestigde dat de childconstructor
nul keer werd aangeroepen.

## Bewuste nonclaims

- Een Harness-time-out kan blokkerende synchrone code niet hard doden.
- Childgebruik verschijnt niet automatisch als Eva's duurzame parentreceipt.
- Een ongesaneerde crash bereikt nog Eva's caller; foutredactie staat open.
- De kandidaat is geen processandbox en voert nog geen tool of extern effect
  uit.
- Er is geen echte modelprovider, PostgreSQL, secret, netwerk of productie-B
  gebruikt.
- Runtime-subagents, Kanban en multi-workercrashherstel zijn niet af.

## Vervolg dat wel Eva-specifiek blijft

De kleinste volgende slice leest één toegestane, duurzame taak uit PostgreSQL,
maakt daar één toolvrije Harness-child van, bewaart de child-usage en het
gesaneerde resultaat bij dezelfde taak en bewijst herstart vóór en na de
resultaatcommit. Pas daarna mogen tools, parallelle workers of dashboardwrites
worden onderzocht.
