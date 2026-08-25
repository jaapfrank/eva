# MC03c1b — eenmalige effect-callclaim

Bijgewerkt: 25 augustus 2026.

## Status

- Implementatie: `implemented` en `tested` op `106d2ec`.
- Onafhankelijke review: **open**; dit document is review-input en geen eigen
  goedkeuring.
- Live bewijs: niet uitgevoerd. Er is geen providerproces gestart, Home
  Assistant is niet aangeroepen en A en B zijn niet gewijzigd.
- Optimalisatie: codeomvang is gecontroleerd; latency, geheugen en kosten zijn
  `unmeasured`.

## Waarom deze component bestaat

Na MC03c0 bestaat één voorbereid effectintent en na MC03c1a één duurzaam
geautoriseerde poging. Provider-I/O mag pas beginnen nadat een afzonderlijke
transactie de actuele plan-, catalogus-, release-, resource-, activation-,
binding-, policy-, manifest- en deadlineauthority opnieuw heeft gecontroleerd
en precies één call-startmarker heeft vastgelegd. Alleen de worker die deze
marker commit krijgt het private proces- en RPC-materiaal.

Daardoor kunnen twee workers of een procesherstart niet allebei callable bytes
krijgen. De provider ziet bovendien Eva's bestaande duurzame idempotency key
zowel als RPC-ID als connector-request-ID; een providerspecifiek verborgen veld
is niet nodig.

## Bestanden en rollen

| Bestand | Rol |
|---|---|
| `src/eva/operations/action_chain_effect_dispatcher.py` | Hercontroleert vlak vóór I/O alle authority, commit één call-startmarker en geeft uitsluitend aan de eerste claim het sealed proces- en RPC-materiaal |
| `src/eva/operations/action_chain_effect_authority.py` | Bouwt de exacte connectoraanroep met de duurzame effect-idempotency key als zichtbare RPC- en request-ID |
| `src/eva/adapters/postgres/action_chain_effect_validation.py` | Reconstrueert bij herstart de authorization- en call-startstates en weigert afwijkende request-, event-, release- of authoritybytes |
| `src/eva/adapters/postgres/action_chain_ledger.py` | Laat het nieuwe append-only call-startevent door de gesloten action-chainprojectie valideren |
| `tests/postgres/test_action_chain_effect_dispatcher.py` | Echte PostgreSQL 16.13-proeven voor concurrency, herstart/replay, deadline en transactie-rollback zonder providerproces |

## Exact checkpoint en bewijs

- `106d2ec` — effect-callclaim, herstartvalidatie en drie gerichte
  PostgreSQL-proeven.
- Gerichte MC03c1b-slice: `3 passed`.
- Geselecteerde action-chainregressie: `56 passed`.
- Afzonderlijke operationele-tijdlijnregressie: `24 passed`.
- Ruff, compileall en `git diff --check`: groen.
- Runtime-contractkaart: 76 bestanden groen. De bestaande, ongerelateerde
  repository-inventoryfout `GITIGNORE_CONTRACT_DRIFT` blijft open.
- Netto productgroei in `106d2ec`: 564 regels. De nieuwe dispatcher telt 527
  regels; er is geen nieuwe dependency toegevoegd en het bestaande ledger bleef
  netto gelijk.

## Bewezen gedrag

- Twee gelijktijdige workers krijgen samen precies één `claimed` en één
  `already_started`.
- Alleen `claimed` bevat proces-, manifest-, initialize- en invokegegevens.
- Een nieuwe engine na herstart krijgt geen callable bytes terug.
- Een verlopen deadline schrijft geen call-startevent.
- Een geforceerde eventfout rolt de hele call-starttransactie terug; een latere
  retry kan daarna exact eenmaal claimen.
- De tests blokkeren `subprocess.Popen`; deze slice voert dus aantoonbaar geen
  providerproces uit.

## Bewuste nonclaims en vervolg

MC03c1b bewaart geen providerantwoord, receipt of readback en zet een effectstap
niet op `succeeded`. De call-startmarker bewijst evenmin dat een extern effect
is gebeurd. MC03c2 moet de geclaimde call buiten de databasetransactie uitvoeren
en iedere definitieve, geweigerde of ambigue uitkomst duurzaam vastleggen.
Daarna zijn afzonderlijke fysieke readback, effectafronding en
dependencyvrijgave nodig. Het volledige vak MC03c blijft daarom open.
