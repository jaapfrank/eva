# Eva — gericht eenvoudiger maken

Dit is een **plan**, niet al geïmplementeerde code. Doel: minder duplicatie en beter diagnoseerbare fouten, zonder verlies van privacy, oorspronkelijke ontvanger, stopgedrag, idempotentie of herstel.

## Prioriteit 1 — afleverklaims

In [`delegated_work_delivery.py`](../../src/eva/adapters/postgres/delegated_work_delivery.py) staan circa 636 regels. Drie routes regelen een claim: `claim_next` (samenvatting), `_claim_artifact_next` (opgevraagd bestand na samenvatting) en `_claim_document_next` (documentuitvoer). Ze herhalen controle op huidige bestemming, claim/lease, locks, checkpoints en markering als ambigu. Ze hebben **verschillende selectievoorwaarden** die niet mogen verdwijnen.

1. **Karakteriseer** alle drie met bestaande Postgres-tests: concurrente claims, verlopen lease, intrekking, gewijzigde ontvanger, crash/herstart, onzeker verzendresultaat en geen dubbele actie.
2. **Maak één interne transactiefunctie** voor dezelfde claim-/ambigu-/checkpointstappen. Houd drie kleine selectors voor verschillende autorisatie- en resultaatvoorwaarden. Geen nieuwe generieke queue of platform.
3. **Maak koppelingen expliciet.** Vervang toegang tot `self._work._engine`, `_scope` en `_load_locked` door een beperkte publieke scoped-transactieinterface. Zet Telegram-ontvangerresolutie in een kanaaladapter; bewaar de bestaande privé-bescherming.
4. **Maak stille fouten zichtbaar.** De brede `except ...: continue` in de kandidaatloops moet een begrensde foutcode en metric geven, zonder privé-inhoud te loggen. Behoud veilige skips tot de nieuwe tests hun gedrag bevestigen.

## Prioriteit 2 — gecontroleerde release

[`reviewed_component_application.py`](../../src/eva/operations/reviewed_component_application.py) telt circa 512 regels, maar heeft een **andere** taak dan aflevering: release- en hashverificatie, ontvangen updaterbewijs, precies één dispatch en herstel naar baseline. Dit mechanisme niet in de delivery-claimklasse schuiven.

Scheid pure release-/receiptvalidatie van persistente wachtrij en dispatcher. Bewaar bestandsrechten, fsync, lock, onzekere toestand, controle van draaiende beelden en rollback. Een niet-lege `reviewer`-string identificeert een label, geen werkelijk onafhankelijke beoordelaar: bind review aan controleerbare herkomst en gescheiden toetsbevoegdheid, met menselijke steekproef waar nodig.

## Prioriteit 3 — grote monolieten alleen met concreet probleem (P1-06)

Een broninventaris toont **`pydantic_conversation.py` ca. 4.971 regels**, **`conversation_actions.py` ca. 3.581** en **`sensory_events.py` ca. 2.456**. Dit zijn reële onderhoudsrisico's, maar grootte alleen bewijst geen fout of duplicatie.

1. Maak per bestand een functie-/verantwoordelijkhedenkaart en meet huidige importgrenzen met `src/tools/check_architecture.py`; **110 schendingen uit 13 oktober is geen actuele telling**.
2. Inspecteer specifieke overlappingen: modelcontext/receiptvorming versus providertransport; action-dispatch versus stores; eventretentie versus initiatief-/leveringsclaims.
3. Als een fout of dubbel eigenaarschap is aangetoond: extraheer één pure validatie- of selectiefunctie tegelijk naar een bestaande passende module; behoud interface en traceerbaar gedrag.
4. Voer huidige model-, aandacht-, PostgreSQL-, STOP- en privacyregressies uit en lees de werkelijk geïnstalleerde versie terug. **Geen refactor alleen om een lager regelaantal te krijgen**.

## Acceptatie en volgorde

- Draai [Postgres-deliverytests](../../development/tests/postgres/test_delegated_delivery_store.py), [afleverwerkertests](../../development/tests/operations/test_delegated_work_delivery.py) en [releaseapplicationtests](../../development/tests/operations/test_reviewed_component_application.py).
- Doe een geïnstalleerde proef voor elke aflevervorm, plus herstel na herstart. **Geen dubbele verzending, geen verkeerde persoon, geen onbevestigde voltooiing, behoud van STOP.**
- Meet vóór/na duplicatie, foutdiagnose, uitvoertijd en regressies. Minder regels alléén is geen succes.
- Pas dit gericht toe als het een aantoonbare blokkade of onderhoudsrisico voorkomt. Laat grotere model- en spraakmodules ongemoeid zonder concreet foutbewijs.

**Niet doen:** alle stores herschrijven, opnieuw infrastructuur kiezen, bestaande releaseveiligheid schrappen of de gezinsacceptatie uitstellen om code cosmetisch te verkleinen.
