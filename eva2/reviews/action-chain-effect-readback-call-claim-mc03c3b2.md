# MC03c3b2 — eenmalige call-start voor effectcontrole

Bijgewerkt: 25 augustus 2026.

## Status

- Implementatie: `implemented` en `tested` op `bfea6f4`.
- Onafhankelijke review: **open**. Dit dossier is review-input en geen eigen
  goedkeuring.
- Live bewijs: niet uitgevoerd. Productie A, kandidaat B, netwerk en Home
  Assistant zijn niet benaderd of gewijzigd.
- Optimalisatie: alleen codeomvang is gemeten; latency, geheugen en kosten zijn
  `unmeasured`.

## Waarom deze stap bestaat

Na autorisatie mogen twee workers of een procesherstart niet allebei dezelfde
fysieke controle uitvoeren. MC03c3b2 zet daarom vóór enig providerproces één
onomkeerbare call-startmarker in PostgreSQL. Alleen de transactie die die marker
daadwerkelijk commit, krijgt het uitvoerbare providerpakket terug.

Een tweede caller of herstart ziet `already_started` en ontvangt geen process
spec, manifest, initialisatie of invoke. Een achtergelaten call-start wordt dus
niet blind herhaald; latere reconciliatie moet eerst vaststellen wat buiten Eva
is gebeurd.

## Gebonden waarheid

Vlak vóór de call-start worden opnieuw gecontroleerd en gelockt:

- het geaccepteerde effect, de nog `running` planstap en de afzonderlijke
  `effect_verify`-intent;
- de geautoriseerde readattempt, capabilitygrant en credentialgrant;
- activation, providerbinding, policy, scope, manifest en deadline;
- de gepinde providerregistry, releaseprovenance, executable en sandbox;
- het oorspronkelijke requestpayload en de volledige eigen readbacktijdlijn.

Het vrijgegeven invoke gebruikt verplicht de gereserveerde RPC-methode
`effect.readback`, niet de algemene methode `resource.read`. De gedeelde
leesdispatcher ondersteunt ditzelfde protocol nu zonder zijn bestaande gewone
leesroute of publieke `__all__`-oppervlak te veranderen.

## Bestanden en omvang

| Bestand | Rol |
|---|---|
| `src/eva/operations/action_chain_effect_readback_dispatcher.py` | Revalideert, commit één call-start en retourneert alleen aan de eerste caller het sealed providerpakket |
| `src/eva/operations/action_chain_effect_readback_authorizer.py` | Deelt de exact voorbereide requestbinding met dispatcher en autorisator |
| `src/eva/operations/action_chain_read_dispatcher.py` | Levert gedeelde release- en exchangebouwstenen en kiest voor gereserveerde reads de exacte RPC-methode |
| `tests/postgres/test_action_chain_effect_readback_dispatcher.py` | Echte PostgreSQL-proeven voor eerste claim, replay, concurrency, deadline, intrekking en rollback |

- Netto productgroei van `bfea6f4`: 368 regels.
- Nieuwe dispatcher: 360 regels; ieder productbestand blijft onder 800 regels.
- Geen schema, migratie of dependency toegevoegd.

## Uitgevoerde controles

- Vijf gerichte call-claimcases op een verse tijdelijke `postgres:16.13` met
  verplichte `eva2_action_chain_disposable_`-prefix.
- 54 brede action-chainchecks op een tweede verse PostgreSQL 16.13-database.
- 24 operationele tijdlijnchecks op een derde verse PostgreSQL 16.13-database
  met verplichte `eva2_living_disposable_`-prefix.
- Eerste caller krijgt exact `claimed` plus process spec, manifest, initialize
  en `effect.readback`-invoke; een nieuwe engine krijgt `already_started` en
  viermaal `None` voor callable materiaal.
- Twee gelijktijdige callers produceren exact één call-start-event en precies
  één uitvoerbaar pakket.
- Verlopen deadline en ingetrokken activation produceren nul call-starts.
- Een geforceerde call-start-insertfout rolt de marker terug en geeft de caller
  geen returnwaarde; na verwijdering van de trigger kan één nieuwe claim slagen.
- In alle gevallen blijven fysieke `effect_readbacks` nul en de effectstap
  `running`.
- Ruff, compileall en `git diff --check` zijn groen; alle tijdelijke containers
  en databases zijn verwijderd.

De repositorybrede architectuurchecker blijft op de huidige brede werkboom rood
met 19 import-boundary-overtredingen in andere bestaande en gelijktijdig
gewijzigde componenten. De nieuwe dispatcher staat niet in die lijst. Deze
slice claimt daarom geen groene repositorybrede architectuurgate.

## Bewijsgrenzen

- De dispatcher geeft providerbytes terug maar start zelf geen subprocess.
- Er is geen netwerkcall, Home Assistant-call of providerresultaat opgeslagen.
- Er is nul `EffectReadback/v1`, nul fysieke doelmatch en nul `STATE_VERIFIED`.
- Effectstap, continuation en dependencies blijven gesloten.
- De testprovider en provideracceptatie zijn synthetisch.
- Er is geen onafhankelijke review, B-deploy, Telegramproef of A/B-adoptie.

## Open vervolg

1. Een executor start uitsluitend het eerste geclaimde sealed pakket en bewaart
   succes, mismatch, afwijzing, rate-limit, protocolfout of ambiguïteit zonder
   tweede proces.
2. Een resultaatstore schrijft de exacte waarneming en `EffectReadback/v1`
   atomair en is corruption-sensitive bij herstart.
3. Alleen een verse exacte doelmatch schrijft `STATE_VERIFIED` en voltooit
   effect, stap, continuation en dependencies atomair.
4. Onafhankelijke review en daarna een echte Telegram-B → Home Assistant-proef
   met crash na effectacceptatie en vóór readbackcommit.
