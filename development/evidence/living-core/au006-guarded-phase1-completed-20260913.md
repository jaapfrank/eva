# Private providerregistratie voltooid; core-admissie blijft uit

Na de nieuwe onafhankelijke fase-1-GO op de geoptimaliseerde controles (`bd26e97f`) is de exacte endpointpolicy toegepast met behoud van de opstartguard. Eén POST heeft één private gateway en twee private tools geregistreerd. Werkelijke gateway-ID: `83690bd3da2e425f8e376ebbb1e69df8`. De toegewezen tool-IDs en geselecteerde catalogusbindings staan in de JSON; er zijn geen fixture-IDs gebruikt.

Gatewayaantal ging van 5 naar 6, toolaantal van 53 naar 55. Alle 53 bestaande API-toolrijen zijn exact ongewijzigd. De bestaande duurzame rijen over zes resourcetabellen zijn eveneens behouden, behalve de gewone gatewayheartbeatvelden `updated_at` en `last_seen`. Alleen de toegewezen gateway en twee tools zijn toegevoegd. Beide nieuwe tools zijn private; hun volledige inputSchema en beschrijving zijn exact gelijk aan de voorbereide native metadata, inclusief de eigen providerbinding in het schema.

De gateway is healthy op dezelfde image, met guardhash `92a5d8e88a467778e1cb715cd84e5b5214ca14d51170dbd6454432e89e04df4b`. De enige policytoevoeging is `http://eva2-document-workspace-au006:9000/mcp` op `172.30.0.5`. De nieuwe provider draait op de beoordeelde image `0ef19e69…`; rootfs, workspace, journal en config zijn read-only.

Core-mandaat, connectorregistry en statische catalogus zijn onveranderd. De nieuwe SQL-coreconnector ontbreekt en de nieuwe raw tools worden niet aan de core aangeboden. De tien overige bestaande containers hebben dezelfde ID, image en starttijd. Core-readiness is goed, het eigen journal is leeg en beide doelbestanden ontbreken. Er is geen modelcall, documenteffect, sealer, core-opt-in, documentworker of DELETE uitgevoerd.

## Evidence en actuele basis

Volledige private preflight, source-/confighashes, API-/SQLite-beforeimages, resolved kandidaat/rollback, één POST-uitkomst, werkelijk geselecteerde receipt en afterimages staan in `phase1-guarded-registration-execution` onder de activatiestage. Het JSON-bewijs bindt elk bestand aan zijn SHA256. De receipt is `registration-receipt.json` in die nieuwe private locatie; historische fase-1-pogingen zijn niet overschreven.

De actieve gatewaycommand bevat de oorspronkelijke Compose-bestanden, de verplichte `/var/lib/eva2/workspace-provider/au006-bootstrap-guard/compose.preserve-resources.json` en de exacte endpointpolicy-override uit de activatiestage. Een policyrollback houdt altijd de preserve-override. Base-only recreate blijft ongeldig. Registratie wordt niet verwijderd bij een configuratierollback; toegewezen IDs blijven behouden.

## Nog niet geactiveerd

Dit voltooit uitsluitend de private metadataregistratie. De provider blijft read-only beschikbaar voor review. `ACTIVATION-BLOCKED.json` blijft aanwezig met de status dat sealen en fase 2 afzonderlijke toestemming vereisen. Er is geen claim dat het documentproject via gewone chat al bruikbaar is. Daarvoor ontbreken de beoordeelde hostseal en runtime-activering; eigenaaracceptatie blijft ook apart.
