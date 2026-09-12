# AU-008 — onafhankelijke technische eindcontrole

Besluit: **GO voor de begrensde technische oplevering**. Geen concrete resterende blocker gevonden.

- Reviewer: `/root/au008_final_review`.
- Implementatie: `e96b2054d4d40e5c502907e24c53fc25a553bbe0`.
- Basis: `f89c3dd034c9e27c953c1fbbb278781cceea3d29`.
- Bewijscommit: `610c23671548bee536072a01ddaca1a5029f5ae9`.
- Bewijs: `eva2/evidence/product/AU-008-current-review-delivery-2026-09-12.json`.
- SHA-256 van dat bewijs: `b0b3471edd1d99302c87f93089be55f7eee7803d1779f0d8f59c65de7c9fa73a`.
- Live gecombineerde image: `sha256:4a5fa7c1116d07f6037b9a02f82ba8f6c30d703e80bbf8c84a30fbf108ca8710`.

## Beoordeelde uitkomst

De eigenaar ziet de laatste opgeslagen private initiatiefbeoordeling, inclusief tijd, stilte of een blokkade voordat een initiatief wordt aangenomen. De bestaande uitgevoerde bijdrage blijft afzonderlijk zichtbaar. De wijziging bouwt geen nieuwe cyclus, verhoogt geen budget en schrijft bij het ophalen geen nieuw initiatief.

Ik heb de implementatiediff en omliggende autorisatie- en sensory-eventcode gelezen. De projectie gebruikt de bestaande eigenaarbinding en tenant-/installatiescope, koppelt de actuele checkpointrevisie aan het juiste event en controleert de payloadhash. De UI onderscheidt een beoordeling van een resultaat, toont budgetweigering als wachten en verwijdert eerder getoonde private inhoud bij geweigerde of ongeldige antwoorden.

De zes gerichte PostgreSQL-controles dekken de relevante uitkomsten, eigenaarscheiding, actuele binding en corrupte bewijsdata. Het bestaande browserbewijs dekt de nieuwe statusweergave en foutpaden. De testloghash is gecontroleerd; ik heb de tests niet herhaald. Het bestaande werkruimtedocument is 866 bytes en heeft de vastgelegde SHA-256 `568e859f3f0e0d8dd09209b67e3b70e96cb57762f3fc9722851b1f32d2b1b1b1`.

## Live bewijs

De definitieve bewijsset bevat de geslaagde activering van alleen core en worker en de eigenaar-API plus werkelijke browserreadback van 2026-09-12 om 16:52 UTC. De API toont de opgeslagen budgetblokkade van 15:50:23 UTC, met een afzonderlijke ophaaltijd. Het reeds afgeronde initiatief blijft aanwezig; de artifact-API levert dezelfde 620 bytes en SHA-256 als voor de activering. Dit API-artifact en het volledige werkruimtedocument zijn afzonderlijk benoemd in het bewijs.

De readback meldt nul muterende browserrequests, nul modelaanroepen en nul paginafouten. De activeringsreceipt meldt gezonde services, ingeschakeld achtergrondwerk, behouden configuratie en niet herstarte geheugen-, database- en spraakcontainers. Ik heb deze opgeslagen receipts beoordeeld; ik heb zelf geen runtimeaanroepen, databasewijzigingen, modelaanroepen of herstarts uitgevoerd.

## Acceptatiegrens

AU-008 is technisch gebouwd, getest en live voor deze oplevering. **IN-02 blijft wachten op het oordeel van de eigenaar.** Deze GO bewijst geen herhaald gezinsnut, algemene initiatiefkwaliteit of voorsprong op concurrenten. GE-01 en GE-02 blijven eerder geaccepteerd. Er is geen nieuw hoofdonderdeel nodig om deze technische oplevering af te sluiten.
