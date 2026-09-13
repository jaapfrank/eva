# Nieuw fase-1-plan tegen de herstelde gatewaybasis

Voorstel, niet uitgevoerd en niet geautoriseerd. Dit vervangt de eerdere retry-/rollbackcombinatie. Het beperkte herstel is vastgelegd in `au006-four-row-repair-completed-20260913.*`; eerdere toestemming wordt niet als registratiegoedkeuring hergebruikt.

De uitkomst van deze fase is uitsluitend een werkelijk toegewezen private gatewayregistratie en de geselecteerde metadata van de eigen read-only provider. Geen connectoradmissie in de core, documenteffect, sealer, core-opt-in of documentworker.

## Exacte wijziging

De baseline gebruikt Compose-project `eva2-workspace-provider`, de bestaande drie configbestanden en envfile én `/var/lib/eva2/workspace-provider/au006-bootstrap-guard/compose.preserve-resources.json`. De kandidaat voegt uitsluitend `gateway-au006-endpoint.proposed.compose.json` uit de activatiestage toe. De resolved servicevergelijking bevat exact één wijziging: toevoeging aan de endpointpolicy van `http://eva2-document-workspace-au006:9000/mcp` met IP `172.30.0.5`.

De guardmount en image blijven behouden in kandidaat én rollback. De exacte argumentlijsten en SHA256-bindings staan in de JSON. Het adres is tijdens voorbereiding vrij; dit is geen reservering of bewijs dat het later nog vrij is.

## Uitvoering pas na nieuwe plan-GO

1. Controleer alle gebonden bron/config/manifest/imagehashes, actuele guardbron en preserve-modus, oorspronkelijke policy, gateway/core-readiness en de tien beschermde containerbindings. Vergelijk alle 53 API-toolrijen met de herstelde baseline; controleer core-mandaat/registry/catalogus, ontbrekende nieuwe connectorregistratie in API én directe SQLite-readtransactie. Verifieer lege eigen journal, oorspronkelijke workspace/journal-inodes, afwezige doelbestanden en vrije `172.30.0.5`. Stop bij iedere afwijking.
2. Maak een nieuwe private bewijslocatie `phase1-guarded-registration-execution` zonder oude uitvoeringsevidence te overschrijven. Start uitsluitend `document-workspace` met de bestaande registratieprofielconfig en vaste-IP-override. Controleer image, IP, binding en alle read-only data-/configmounts; haal alleen initialize/tools-list-metadata op en vergelijk met de gebonden native metadata. Geen effecttool uitvoeren.
3. Neem verse quiescence en een gecoördineerd gatewayvenster. Recreate uitsluitend de gateway met de JSON-kandidaatcommand plus `up -d --no-deps gateway`. Verifieer daarna image, gemounte guardhash/preserve, exacte kandidaatpolicy, metadataendpointbereikbaarheid, readiness en ongewijzigde bestaande toolrijen/gatewaybindings. Alleen gewone gatewayheartbeat-tijdstempels mogen verschillen. Controleer ook de zes duurzame resourcetabellen tegen de nieuwe private beforeimage.
4. Neem direct vóór registratie opnieuw de bestaande eigenaar-/mandaatcontrole. Gebruik het gebonden `phase1_register_private.py` via de bestaande registratieclient en private tokenbestanden: eerst exacte-owner find, dan maximaal één POST `/gateways` met de bestaande gebonden naam, endpoint, STREAMABLEHTTP/cache/private-body, daarna discovery. De nieuwe SQL-coreconnector moet afwezig blijven. Leg de werkelijk geretourneerde gateway-/tool-IDs en catalogusbinding privé vast; vul nooit IDs uit fixtures in.
5. Een onzeker of mislukt POST-antwoord geeft uitsluitend onafhankelijke readback en stop, zonder retry. Ook bij een onverwachte catalogus-/runtimewijziging: stop alleen de nieuwe provider en zet uitsluitend de endpointpolicy terug met de **guarded** rollbackcommand. Geen DELETE, cascade, brede catalogusrestore of base-only recreate. Eventueel toegewezen IDs blijven behouden en worden gerapporteerd; rollback van configuratie is geen claim dat een registratie ongedaan is gemaakt.
6. Na succes: before/after API- en SQLite-catalogusbewijs, werkelijke toegewezen IDs, geselecteerde tools/inputSchema-binding, core/registry/mandaatcatalogus ongewijzigd, nieuwe raw tools niet in de core, eigen journal leeg/doelbestanden afwezig, provider nog read-only, protected containers en readiness. De read-only provider mag blijven draaien voor beoordeling; er volgt geen automatische fase 2.

## Grenzen

De oudere `execute_reviewed_gateway_policy.py` en base-only rollback zijn niet bruikbaar voor deze fase. Elk gatewaycommand gebruikt de nieuwe guardbasis. Een momentopname met nul corelocks is geen algemene schrijfbarrière; afwijkingen tijdens readback stoppen deze uitvoering. Dit plan bevat geen reparatie-UPDATE: de vier rijcorrecties zijn al voltooid en worden niet herhaald.

De nieuwe fase heeft nul uitgevoerde POSTs. Een afzonderlijke nieuwe toestemming is nodig voor deze maximaal-één-POST-uitvoering, en daarna opnieuw voor eventueel sealen of fase 2.
