# MC03c0 — duurzame effectvoorbereiding

Bijgewerkt: 25 augustus 2026.

## Status

- Implementatie: `implemented` en `tested`.
- Onafhankelijke review: **open**; dit document is de review-input, geen eigen
  goedkeuring.
- Live bewijs: niet uitgevoerd; A en B zijn niet gewijzigd en Home Assistant is
  niet aangeroepen.
- Optimalisatie: alleen codeomvang is gecontroleerd. Latency, geheugen en kosten
  zijn `unmeasured`.

## Waarom deze component bestaat

Na een bewezen read mag alleen de daardoor vrijgegeven effectstap verder. Deze
slice zet zo'n exacte lease atomair om in één `EffectIntent/v1`, voordat enige
provideractie mogelijk is. Modelargumenten, actuele catalogus, resource,
activation, binding, policy, manifest, deadline, payloadhash en een stabiele
idempotency key blijven zo aan dezelfde planstap gebonden.

## Bestanden en rollen

| Bestand | Rol |
|---|---|
| `src/eva/adapters/postgres/action_chain_effects.py` | Enige MC03c0-writer: hercontroleert lease en actuele authority en schrijft intent, stepprojectie, attention en beide tijdlijnevents in één transactie |
| `src/eva/adapters/postgres/action_chain_effect_validation.py` | Herstart- en corruptievalidator voor intent, authority, lease-events en de bewijsgrens dat nog geen attempt/receipt/readback bestaat |
| `src/eva/adapters/postgres/action_chain_ledger.py` | Herkent de voorbereide effectstatus en delegeert de inhoudelijke validatie; blijft netto één regel kleiner dan vóór MC03c0 |
| `tests/postgres/test_action_chain_effects.py` | Echte PostgreSQL 16.13-proeven voor succes, replay, concurrency, verlopen/verkeerde lease, authority-intrekking, rollback, herstart en corruptie |

## Exacte checkpoints en bewijs

- `d3cc6d7` — herstartvalidator.
- `f00ce8c` — atomaire effectvoorbereiding en ledgerintegratie.
- `92be4b2` — zes gerichte PostgreSQL-tests.
- `4f0a8f4` — ledgergroei teruggebracht van netto `+37` naar netto `-1` regel
  ten opzichte van `0cc7033`; nieuwe productbestanden zijn 554 en 384 regels.
- Gerichte slice: `6 passed`.
- Volledige geselecteerde action-chainregressie: `23 passed`.
- Operationele tijdlijn: `24 passed`.
- Runtime-contractkaart: 76 bestanden groen. De bestaande, ongerelateerde
  repository-inventoryfout `GITIGNORE_CONTRACT_DRIFT` blijft open.

## Bewuste nonclaims en vervolg

MC03c0 schrijft geen `EffectAttempt`, providerreceipt, effectreadback of
`TOOL_PREPARED` en doet geen netwerk- of Home Assistant-I/O. MC03c1 moet de
bestaande dispatchgrens hergebruiken; MC03c2 moet de provideruitkomst en fysieke
readback duurzaam verwerken; pas daarna mag de effectstap slagen en een volgende
dependency vrijkomen. Het volledige vak MC03c blijft daarom open.
