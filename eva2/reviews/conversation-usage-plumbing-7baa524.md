# Onafhankelijke review — conversation usage plumbing `7baa524`

Datum: 25 augustus 2026

Reviewer: Curie the 2nd, onafhankelijke verificatie- en releaselead

Beoordeeld checkpoint: `7baa524360bf88ee4d79e097c00c1ec69133196e`

Basis: `365e530546183a8be2bf600a116e5241d04d0f00`

## Conclusie

**GO voor DF02a0.** Dit checkpoint voegt uitsluitend een gesloten vorm voor
door een modeladapter gerapporteerde usage toe, aggregeert die over de calls
van een gespreksbeurt en bewaart haar bij het duurzame antwoord. De huidige
productieadapter rapporteert nog geen usage en blijft daarom expliciet
`unmeasured`. Er is geen provider-, tool-, budget- of autoriteitsgedrag
toegevoegd of gewijzigd.

## Scope

De review omvatte exact vier gewijzigde paden:

- `src/eva/application/conversation.py`;
- `src/eva/adapters/postgres/conversation_journal.py`;
- `tests/application/test_conversation_usage.py`;
- `tests/postgres/test_conversation_usage_journal.py`.

De diff tegen de genoemde basis bevat 479 toevoegingen en twee verwijderingen.
`git diff --check` is groen. Er zijn geen provideradapter, dependency,
migratie, runtimeconfiguratie, toolgrant of budgetinstelling gewijzigd.

## Semantiek

- `ConversationModelUsage.unmeasured()` bewaart voor iedere waarde `None`.
  Onbekende usage wordt nergens als nul voorgesteld.
- `measured` vereist een positieve modelpoging en niet-negatieve gehele
  toolcall- en tokenwaarden; booleans, missende velden, extra velden en
  negatieve waarden worden geweigerd.
- Een lege aggregatie of één onbekende call maakt de volledige beurt
  `unmeasured`. Alleen wanneer iedere bijdrage gemeten is, worden pogingen,
  toolcalls en tokens opgeteld.
- Een normale beurt bewaart één usagebijdrage. Wanneer na een actie een tweede
  modelcall volgt, worden beide bijdragen volgens dezelfde gesloten regel
  geaggregeerd.
- Een oud duurzaam antwoord zonder `model_usage` wordt als `unmeasured`
  hersteld, nooit als nul.
- Een malformed duurzaam usageobject levert `ConversationConflict`; het wordt
  niet genegeerd of gerepareerd.
- Bytegelijke antwoordreplay bevat usage. Dezelfde tekst met gewijzigde usage
  wordt daarom als een verschillend duurzaam antwoord geweigerd.

## Productie- en autoriteitsgrens

De productie-modeladapter construeert nog steeds een `ConversationModelResult`
zonder `usage=` en krijgt daardoor de expliciete standaard `unmeasured`.
DF02a0 leest nog geen providermetadata en handhaaft geen token- of
kostenbudget. Het nieuwe object is observationele data, geen tweede
budgetautoriteit en geen toestemming om extra model- of toolcalls uit te
voeren.

Wanneer een toekomstige adapter intern retries, correctiecalls of meerdere
providerpogingen uitvoert, moet die adapter alle werkelijk gerapporteerde
bijdragen correct leveren. Dit checkpoint claimt die providerwiring niet.

## Herhaalde regressies

Uitgevoerd op exact `7baa524`:

```text
TMPDIR=/tmp PYTHONPATH=src python3 -m pytest -q \
  tests/application/test_conversation.py \
  tests/application/test_conversation_usage.py \
  tests/postgres/test_conversation_usage_journal.py
```

Resultaat: `22 passed`.

Daarna zijn alle testpaden met `conversation` in hun bestandsnaam samen
herhaald, inclusief model-, authlease-, action- en journaldelen.

Resultaat: `69 passed`.

```text
TMPDIR=/tmp python3 -m ruff check \
  src/eva/application/conversation.py \
  src/eva/adapters/postgres/conversation_journal.py \
  tests/application/test_conversation_usage.py \
  tests/postgres/test_conversation_usage_journal.py
```

Resultaat: `All checks passed`.

## Echte wegwerp-PostgreSQL-proef

De echte journalroute is afzonderlijk uitgevoerd tegen een lege,
netwerkloze PostgreSQL `16.13` (`server_version_num=160013`) op een tijdelijk
`tmpfs`-volume en Unix-socket. De actuele Alembic-keten is tot `head`
opgebouwd en na de proef teruggedraaid; de container en tijdelijke socket zijn
verwijderd.

De proef bewees:

- gemeten usage wordt gecommit en na dispose/recreate van de engine exact
  teruggelezen;
- bytegelijke replay blijft idempotent;
- replay met één gewijzigd inputtoken wordt geweigerd;
- een werkelijk opgeslagen legacy-reply zonder usage herstelt als
  `unmeasured` met `None`-tokens;
- een werkelijk opgeslagen reply met negatieve gemeten tokens faalt gesloten
  als `ConversationConflict`.

## Nonclaims

Dit checkpoint bewijst geen providerextractie, OpenTelemetry-export,
prijsberekening, budgethandhaving, Pydantic AI-vertical, live Telegram- of
managed-B-route. Het maakt alleen correcte usage-plumbing en duurzame opslag
mogelijk zonder onbekende metingen als nul voor te stellen.

## Eindoordeel

Commit `7baa524` mag als het smalle DF02a0-usagecheckpoint worden geïntegreerd.
Een volgende slice moet providerusage afzonderlijk aansluiten en bewijzen;
budgetbesluiten mogen pas daarna op volledig gemeten, herleidbare data steunen.
