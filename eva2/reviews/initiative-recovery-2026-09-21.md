# Herstel initiatiefbeoordeling — review en beheer

Onafhankelijke reviewer: conversation_scope_review. GO na herstel van de BYTEA/hex-conversie, beoordeling van de vier exacte deltas en het installatie-/terugrolplan. Geen algemene acceptatie van Eigen initiatief.

## Gedrag

Een onzekere modelaanroep blijft geblokkeerd totdat de eigenaar de concrete beoordeling afbreekt. De bestaande eigenaarsroute accepteert alleen het exacte review-ID en revisienummer, huidige eigenaarsbinding, de juiste installatie en een intacte historie zonder besluit of uitvoering. De oude poging krijgt een append-only `WILL_REVIEW_ABANDONED`-gebeurtenis. Onbekend verbruik en eerdere foutregistratie blijven behouden. Een nieuwe beoordeling gebruikt opnieuw de bestaande mandaat-, model- en budgetcontroles. De uurgrens volgt de oude onzekere poging, niet het moment waarop de blokkade is opgeheven.

## Beheer

De beveiligde POST `/internal/v1/dashboard/initiatives/review/abandon` ontvangt `thread_id` en `expected_revision`. Gebruik alleen een uit de actuele, eigenaargebonden historie gecontroleerde onzekere beoordeling. Een conflict wordt onderzocht; niet automatisch opnieuw proberen met een ander revisienummer. Deze beheeractie verzendt geen bericht, doet geen modelcall en herhaalt geen oude beslissing. De normale achtergrondcyclus verzorgt de nieuwe beoordeling.

De private installatiebestanden en exacte activatie-/rollbackcommando’s staan in het artifactpad uit het bewijs. De huidige en vorige core-image zijn behouden. Bij rollback blijven gegevens en het afbreekevent intact; de oude scheduler begrijpt de nieuwe eindtoestand niet en blokkeert daarom conservatief opnieuw. Rol terug zonder historie te wissen of afbreken als modelbeslissing te herschrijven.

[Installatie en gericht bewijs](../evidence/product/initiative-recovery-2026-09-21.json).
