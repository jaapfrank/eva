# Vier bestaande toolrijen hersteld; opstartguard geïnstalleerd

Na onafhankelijke goedkeuring van `6761694e` plus `cc732bac` is eerst de exact gepinde bootstrapguard geïnstalleerd en gecontroleerd. Vervolgens is het private manifest met SHA256 `862bcfb933f4d92ee01c84126f2027221dc741fbda532aec644006787118cbd5` één keer uitgevoerd: vier matches en vier UPDATEs in één SQLite `BEGIN IMMEDIATE`-transactie, uitsluitend `team_id` en `updated_at`.

Alle 53 API-toolrijen zijn na herstel exact gelijk aan de pre-incidentmomentopname. De volledige duurzame tooltabel is gelijk aan de actuele beforeimage met uitsluitend de acht goedgekeurde veldcorrecties. De overige vijf resourcetabellen zijn behouden, uitgezonderd de gewone gatewayvelden `updated_at` en `last_seen`. Daarmee zijn de vier waargenomen onbedoelde teamtoewijzingen hersteld. Er wordt geen volledige database-rollback geclaimd.

De gateway is healthy op dezelfde image `sha256:daf923c247fd569df5f8c9ee418cbc259fade564f78e0a0852937139a181c4dd`, met bronhash `92a5d8e88a467778e1cb715cd84e5b5214ca14d51170dbd6454432e89e04df4b`. De oorspronkelijke endpointpolicy is behouden. De tien overige bestaande containers en de gestopte AU-006-provider hebben dezelfde ID, image en starttijd als vóór dit herstel. Core-mandaat, connectorregistry, catalogus en readiness zijn behouden.

De herstelcontrole gebruikte verse core-quiescence vóór recreate en vóór de transactie. De daadwerkelijke uitsluiting van concurrerende SQLite-schrijvers zat in `BEGIN IMMEDIATE`, met timeout nul en zonder retry. Private beforeimages, volledige rijcontroles, resolved Compose-configuraties, geïnstalleerde bron en afterimages zijn met hashes bewaard in de bijbehorende JSON-evidence. Er was één herstelpoging en geen onzekere uitkomst.

## Actuele deploymentbinding

De bestaande Compose-projectnaam blijft `eva2-workspace-provider`. De oorspronkelijke drie Compose-bestanden en `provider.env` blijven gebruikt. De aanvullende, geïnstalleerde override is:

`/var/lib/eva2/workspace-provider/au006-bootstrap-guard/compose.preserve-resources.json`

Deze voegt uitsluitend de read-only mount toe van:

`/var/lib/eva2/workspace-provider/au006-bootstrap-guard/bootstrap_db.py`

naar `/app/mcpgateway/bootstrap_db.py`. Bron en override zijn root-owned. De standaardmodus is `preserve`; de legacy-migratiemodus is niet ingesteld.

Toekomstige gatewayrecreates moeten deze override behouden. De eerdere base-only configuratie is niet langer een veilige automatische terugweg: zij verwijdert de guard en kan de null-teamrijen opnieuw herverdelen. De oude fase-1-retryscript/rollbackcombinatie mag dus niet ongewijzigd worden hergebruikt. Een volgende endpointpolicywijziging vereist opnieuw een begrensde configuratievergelijking tegen deze actuele guardbinding.

## AU-006 blijft gepauzeerd

Er is geen nieuwe registratie-POST uitgevoerd. De nieuwe gatewayregistratie ontbreekt, de nieuwe provider staat uit, het eigen journal is leeg en beide doelbestanden ontbreken. Sealer, core-opt-in en documentworker zijn niet gestart. De uitgevoerde toestemming gold uitsluitend de opstartguard en het herstel van deze vier bestaande rijen. Registratie en fase 2 vergen een afzonderlijke beslissing.
