# MC03c3a — duurzame effectreadbackvoorbereiding

Bijgewerkt: 25 augustus 2026.

## Status

- Implementatie: `implemented` en `tested`.
- Onafhankelijke review: **open**; dit dossier is review-input en geen eigen
  goedkeuring.
- Live bewijs: niet uitgevoerd; productie A en kandidaat B zijn niet gewijzigd
  en Home Assistant is niet aangeroepen.
- Optimalisatie: codeomvang is exact gecontroleerd. Latency, geheugen en kosten
  zijn `unmeasured`.

## Waarom deze component bestaat

Een provider-ACK bewijst dat een effect werd aangenomen, niet dat de fysieke
doeltoestand klopt. MC03c3a zet daarom uitsluitend een duurzaam geaccepteerd
effect om in één afzonderlijk geautoriseerde read-only verificatie-intent. De
schrijfbevoegdheid van het effect wordt nooit stil hergebruikt als
leesbevoegdheid.

De read-intent bindt effect, attempt, providerreceipt, providerresource,
requesthash, idempotency key, planstap, scope, policy, retention en deadline.
Intent en eigen operationele tijdlijn worden in één transactie geschreven en
bij iedere herstart opnieuw vanuit PostgreSQL gevalideerd.

## Bestanden en rollen

| Bestand | Rol |
|---|---|
| `src/eva/adapters/postgres/action_chain_effect_readback_validation.py` | Valideert geaccepteerd effectbewijs, afzonderlijke actuele readauthority, relationele manifest-/bindingkolommen, deterministische intent en volledige readbacktijdlijn |
| `src/eva/adapters/postgres/action_chain_effect_readbacks.py` | Enige MC03c3a-writer; lockt effect en stap, schrijft precies één `ReadIntent/v1` plus eerste tijdlijnevent en doet geen provider-I/O |
| `src/eva/adapters/postgres/action_chain_effect_result_validation.py` | Laat iedere terminale effectreload ook eventuele effectreadbackvoorbereiding valideren |
| `src/eva/adapters/postgres/action_chain_effect_validation.py` | Geeft plan- en stapbinding door aan de readbackvalidator |
| `tests/postgres/test_action_chain_effect_readbacks.py` | Echte PostgreSQL-proeven voor succes, herstart/replay, authority, concurrency, rollback en corruptie |

## Checkpoints en omvang

- `45cd285` — corruption-sensitive validator en authoritybinding, 405 regels.
- `4bc39a9` — atomaire voorbereiding, ledgerintegratie en tests; de nieuwe
  writer telt 249 productregels.
- Iedere implementatiecommit bleef netto onder 600 productregels.
- Er is geen dependency of schema toegevoegd; de bestaande `read_intents`- en
  operationele tijdlijntabellen worden hergebruikt.

## Uitgevoerde controles

- 7 gerichte MC03c3a-cases op een verse tijdelijke `postgres:16.13`-database
  met verplichte `eva2_action_chain_disposable_`-prefix.
- Alleen provideracceptatie met exact één attempt en receipt kan voorbereiden;
  een onbewezen effect schrijft nul intenten.
- Onafhankelijke `verification_read`-activation, manifest, scope, providerbinding
  en policy worden vóór insert en bij reload opnieuw gecontroleerd.
- Twee gelijktijdige writers leveren exact `prepared` en `already_prepared`, één
  intent en één tijdlijnevent.
- Een geforceerde fout bij het tijdlijnevent rolt intent en tijdlijn volledig
  terug.
- Een nieuwe engine leest exact dezelfde intent terug zonder nieuwe provideractie.
- Opzettelijke intenthash- en relationele manifestcorruptie falen bij reload of
  voorbereiding gesloten.
- 41 brede action-chainchecks en 24 operationele tijdlijnchecks groen op twee
  andere verse PostgreSQL 16.13-databases.
- Ruff, compileall en `git diff --check` groen.
- Runtime-contractkaart groen voor 76 gemapte bestanden. De repositorybrede
  architectuurcheck stopt alleen op de reeds bestaande, ongerelateerde
  `GITIGNORE_CONTRACT_DRIFT` (44 uitsluitingen en 2 negaties verwacht).
- Alle drie tijdelijke containers en databases zijn na de proeven verwijderd.

## Bewijsgrenzen

- Er is geen read-attempt geautoriseerd of gestart en geen providerproces,
  netwerk of Home Assistant aangeroepen.
- Er is nul `EffectReadback/v1`, nul `STATE_VERIFIED` en nul fysieke
  doeltoestand bewezen.
- Effectstap en continuation blijven `running`/gesloten; geen dependency is
  vrijgegeven.
- De testprovider en synthetische ACK bewijzen contractgedrag, geen echte
  apparaatverandering.
- Er is nog geen onafhankelijke review, B-deploy, Telegramnetwerkproef of
  A/B-adoptie.

## Open vervolg

1. Onafhankelijke review van MC03c0–MC03c3a en de open MC03b2b/MC03b3-reviews.
2. MC03c3b autoriseert en claimt precies één readbackcall via de bestaande
   sealed providergrens zonder provider-I/O in de autorisatietransactie.
3. Een aparte slice voert de read uit, bewaart `EffectReadback/v1` en behandelt
   fout, ambiguïteit, mismatch en deadline zonder het effect opnieuw uit te
   voeren.
4. Alleen een verse exacte match zet `STATE_VERIFIED`, effect en afhankelijkheden
   atomair op succes.
5. Echte Telegram-B → Home Assistant-proef plus crash na effect en vóór
   readback op één bevroren kandidaatimage.
