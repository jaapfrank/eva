# Dashboardkeuzes en verwerking door Codex

De enige besluitbron is `eva2/design/product/optional-scope-decisions.json` in de gedeelde repository. Het dashboard leest deze bij iedere verversing. De algemene werklijst blijft in `execution-workboard-v1.json`; deze wordt niet door een klik herschreven.

`decisions[optionId]` bevat `decision`, `decisionId` en `updatedAt`. Besluiten zijn `undecided`, `included` of `excluded`. Ontbrekend is onbeslist. Opnemen maakt uitvoering vereist, niet uitgevoerd of geaccepteerd. Afwijzen verwijdert de optie uit actieve stappen en teller; geschiedenis en heropenen blijven beschikbaar.

`events[]` bevat ieder werkelijk klikbesluit, met uniek `id`, `optionId`, `decision`, `previousDecision`, `createdAt`, `actor: owner` en `processing`. Nieuwe events hebben `processing.status: pending`. Een herhaalde request-ID maakt geen tweede event.

## Coördinator

1. Lees pending events en de actuele `decisions[optionId].decisionId`. Een ouder event kan inmiddels zijn vervangen. Geen uitvoerwerk starten voor afgewezen of heropende opties.
2. Claim het event vóór verwerking: `pending → acknowledged`. De eerste claim wint; dezelfde of een andere verwerker kan niet nogmaals claimen.
3. Voor de actuele opgenomen optie: koppel de bestaande uitvoeringstaak en zet `acknowledged → queued`. Gebruik de event-ID om dubbel starten te voorkomen. De helper weigert queue voor een vervangen besluit of niet-opgenomen optie.
4. Zet na verwerking `queued → done`, of `acknowledged → done` als alleen administratie nodig is. `done` betekent verwerkt besluit, niet automatisch afgeronde productkaart of menselijke acceptatie. Die bewijzen blijven in de werkbron.

Gebruik dezelfde bestandslock en revisiecontrole via:

```text
node eva2/experiments/react-admin-dashboard-v1/process-scope-decision.mjs EVENT_ID EXPECTED_STATUS NEW_STATUS COORDINATOR_ID [TASK_ID]
```

De verwerking bevat `consumer`, `updatedAt` en eventueel `taskId`. Schrijf dit bestand niet buiten deze helper om. De helper voor dashboardklikken gebruikt hetzelfde lockprotocol, schrijft atomisch en weigert een achterhaalde bronrevisie. Een afgewezen optie opnieuw openen maakt een nieuw onbeslist event; het oude besluit blijft bewaard.

## Toegang en bewijs

Tijdelijk kan het lokale projectdashboard met `EVA_PROJECT_LOCAL_OWNER=1` zonder aanmelding keuzes opslaan. Deze modus vereist een echte loopbackverbinding, een lokale Host, exact dezelfde Origin en JSON. De dienst blijft op 127.0.0.1 gebonden. De pagina toont dan geen tokenveld. Verwijder de instelling om aanmelding weer te vereisen. Deze instelling verandert geen core- of stemaanmelding.

Zonder deze instelling vereist POST `/scope-decisions` het bestaande eigenaar-dashboardtoken, dat tegen de actieve core wordt gecontroleerd. Een onbeschermde of afwijkende core-statusroute geeft geen schrijfbevoegdheid. De herkomst moet exact overeenkomen met de lokale dashboardhost. Het token blijft uitsluitend in het geheugen van de geopende pagina; geen localStorage of besluitbestand bevat het token.

`verify-scope-decisions.mjs` gebruikt een tijdelijke besluitbron en test daadwerkelijk de knoppen, refresh, teller, afwijzen, heropenen, autorisatieafwijzing, request-idempotentie en exclusief claimen. Er worden geen testbesluiten in de productiebron aangemaakt.
