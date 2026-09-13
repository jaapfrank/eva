# AU-006: afgebroken herpoging en gericht herstelvoorstel

Status: geblokkeerd na gatewayrecreate, vóór iedere registratie-POST in deze herpoging. De oorspronkelijke endpointpolicy is teruggezet; de nieuwe provider is gestopt. Geen sealer, core-opt-in of documentworker gestart.

## Vastgesteld

De gateway bevat nog 5 gateways en 53 tools. Vier bestaande publieke CPython-tools veranderden in `teamId`, `team` en `updatedAt`. Hun team was vooraf null en is nu een persoonlijk team, ook in een onafhankelijke SQLite-readtransactie. Deze wijzigingen bleven na de configuratierollback aanwezig. Dit is geen volledige gegevensrollback.

De bestaande image bevat `mcpgateway/bootstrap_db.py`. `_run_post_migration_bootstrap` roept op regel 185 `bootstrap_resource_assignments` aan. Regel 709 selecteert resources zodra team, eigenaar óf zichtbaarheid null is; regels 769–778 schrijven beheerdersteam, beheerder en public en committen per rij. De startup-fastpath en `MCPGATEWAY_SKIP_MIGRATIONS` voeren deze helper ook uit. Dit codepad verklaart het waargenomen patroon; de logs van de inmiddels verwijderde eerste gerecreëerde container zijn niet behouden, dus er is geen afzonderlijke runtime-trace van die vier writes.

De tien overige bestaande containers hebben dezelfde ID, image en starttijd. Core-mandaat, connectorregistry en statische catalogus zijn ongewijzigd. De gateway is healthy op de oorspronkelijke image en policy. De nieuwe registratie ontbreekt in API en SQLite; er zijn geen toegewezen nieuwe IDs. Het eigen journal is leeg en beide doelbestanden ontbreken.

De eerste recreate-opdracht miste het bestaande Compose-project `eva2-workspace-provider` en liep vast op de bestaande containernaam. Er werd toen geen gateway vervangen. Na correctie naar het bestaande project vond de goedgekeurde recreate plaats; de vergelijking vóór POST faalde en de gerichte configuratierollback is uitgevoerd. De eerdere HTTP502-poging had één POST; deze herpoging had nul.

## Voorgesteld vervolg, nog niet uitgevoerd

1. Laat de eigenaar van de gateway-opstartcode een kleine expliciete guard voor resource-herverdeling voorbereiden en onafhankelijk beoordelen. De guard moet bestaande beheerde catalogusrijen bij herstart behouden; migraties, authenticatie en rollen blijven hun bestaande gedrag houden. Alleen `SKIP_MIGRATIONS` instellen lost dit niet op. Geen generieke uitschakeling van authenticatie of databasebootstrap.
2. Bewijs op een private databasekopie met de exacte image dat herstart de bestaande resource-/autoriteitsvelden behoudt, inclusief null-team met bestaande eigenaar en public/private zichtbaarheid. Inspecteer tevens niet in `/tools` zichtbare kolommen en andere resource-tabellen; het huidige bewijs dekt alleen de zichtbare gateway/toolrijen en de gerichte teamkolommen. Breng de vier rijcorrecties pas in een uitvoerbaar plan wanneer alle gewijzigde duurzame kolommen aan een betrouwbare baseline zijn gekoppeld.
3. Bereid een afzonderlijk, exact herstelplan voor met de vier tool-IDs uit `phase1-retry-abort-result.json`: alleen bewezen gewijzigde kolommen, compare-and-swap op de actuele waarden, alle vier correcties in één transactie, bij afwijkende rij/telling rollback. Geen delete, cascade of brede catalogusrestore. Een momentopname van nul actieve corelocks is geen exclusieve gateway-schrijfbarrière; het herstelplan moet een werkelijk gecoördineerd schrijfvenster aangeven.
4. Na afzonderlijke toestemming voor de guard en het gerichte herstel: readback van de volledige catalogus en duurzame kolommen, readiness en beschermd runtimebewijs. Pas daarna opnieuw een afzonderlijke beslissing over endpointpolicy en maximaal één registratie-POST. Een onzeker POST-antwoord leidt tot readback en stop, zonder automatische retry.

Dit document verleent geen uitvoeringsgoedkeuring. De oorspronkelijke beperkte fase-1-goedkeuring dekt geen reparatie van bestaande teamtoewijzingen of wijziging van gateway-opstartcode.
