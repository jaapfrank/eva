# Gateway-opstartguard en vier gerichte rijcorrecties — review

Dit pakket is voorbereid en getest, niet uitgerold. De vier onbedoelde teamwijzigingen staan nog in de bestaande gatewaydatabase. De oorspronkelijke endpointpolicy blijft actief, de nieuwe AU-006-provider staat uit en de herpoging bevat nul registratie-POSTs.

## Bron en wijziging

`experiments/contextforge-gateway-v1/preserve-resource-assignments.pin.json` bindt de patch aan de exacte gateway-image en originele/gepatchte `bootstrap_db.py`-SHA256. De patch verandert uitsluitend het begin van `bootstrap_resource_assignments`: standaard `preserve`, expliciete legacybewerking via `EVA_MCP_RESOURCE_ASSIGNMENT_MODE=migrate-orphans`, ongeldige waarde geeft een fout. Schema, beheerder, standaardrollen en teamnormalisatie blijven hun bestaande paden volgen. Dit is geen algemene claim dat alle bootstraphelpers gegevens ongemoeid laten.

De bestaande OR-null-selectie wordt niet aangepast of als betrouwbare detectie van onbeheerde resources gebruikt. De beheerde start slaat die legacyherverdeling geheel over. Een legacy-migratie is een afzonderlijke handeling met eigen beoordeling; deze patch maakt haar niet automatisch veilig.

## Bewijs

De echte gepinde image draaide met netwerk `none`, alleen een read-only bronmount en tijdelijke fixturedata. De proef start een lege SQLite-database, controleert beheerder/team/rollen, maakt 30 resources over zes klassen en doorloopt de echte schema-at-head- en skip-migrations-opstartpaden. Alle resourcekolommen blijven gelijk, inclusief eigenaar, team, private/public, naamconflicten en ontbrekende federation_source. Een afzonderlijke kopie van de werkelijke metadata met nullable visibility bewijst behoud van legacy-nullwaarden via de post-migration-helper.

De expliciete legacyoptie is op fixtures zonder naamconflicten uitgevoerd. Bij een eerdere collisionproef liep de ongewijzigde legacycode tegen een integriteitsfout aan; dit pakket claimt geen reparatie van dat pad. Het behouden van conflicterende namen in `preserve` is wel getest.

De CAS-proef bewijst read-only vergelijking, exact vier wijzigingen, behoud van een vijfde rij en gevoelige velden, weigering van een verouderd manifest, volledige rollback wanneer de vierde UPDATE faalt en weigering bij een andere actieve SQLite-schrijver zonder retry. Het echte private manifest is ook read-only tegen de live database gecontroleerd: vier matches, nul writes.

## Vier rijen en herstelgrenzen

Het private manifest staat onder `phase1-retry-execution/four-row-repair-manifest.json` in de activatiestage. De hash staat in het bijbehorende JSON-bewijs. Het bevat volledige actuele SQLite-rijen, de exacte tools-schemaomschrijving en per rij uitsluitend `team_id=null` en de oorspronkelijke `updated_at` als herstelwaarden.

De API-beforeimages bevatten alle door de bootstrap geschreven gevoelige toolvelden. Vergelijking vóór/na/live bevestigt ongewijzigde owner_email, visibility, name, original_name, custom_name, custom_name_slug en federation_source. Alleen team_id en updated_at moeten worden teruggezet. De afgeleide API-waarde `team` volgt uit team_id. Er is geen volledige SQLite-beforeimage van vóór de recreate; het plan verzint geen herstelwaarden voor andere kolommen of tabellen.

## Voorgestelde uitvoering na afzonderlijke goedkeuring

1. Bind de gepatchte bron read-only in uitsluitend de bestaande gatewayservice, met exact huidige image en `--project-name eva2-workspace-provider`, plus originele configbestanden. Verifieer voor de recreate alle bron-/confighashes, huidige policy, vier actuele rijbeelden, gestopte AU-006-provider en gecoördineerd gatewayvenster. Dit plan activeert de nieuwe endpointpolicy niet.
2. Recreate uitsluitend de gateway en controleer de exacte bronhash, default preserve, oorspronkelijke policy, readiness en alle bestaande zichtbare gateway/toolvelden. Bij afwijking geen rijcorrectie en geen registratie. Terugkeer naar de oude bootstrapbron kan de ongewenste herverdeling opnieuw uitvoeren; een code-rollback mag daarom niet als gegevensrollback worden gepresenteerd.
3. Voer `repair_resource_assignments.py` met het gereviewde manifest en expliciete SHA uit tegen de bestaande SQLite-database. De uitvoerstand verkrijgt met `BEGIN IMMEDIATE` een exclusieve SQLite-schrijftransactie; timeout nul voorkomt wachten/retry. Schema, afwezigheid van tooltriggers en alle kolommen van de vier actuele rijen moeten exact overeenkomen. Elke UPDATE vergelijkt alle kolommen; totaal vier, anders rollback. De gehele tooltabel wordt vóór commit vergeleken met uitsluitend de twee toegestane kolomwijzigingen per doelrij. Geen DELETE/cascade, geen brede restore.
4. Controleer buiten de transactie API en directe database-readback, behoud van overige rijen, core-mandaat/registry/catalogus, readiness en tien overige containers. Leg codecorrectie en gegevensherstel apart vast. Pas daarna afzonderlijk beslissen over AU-006-policy/registratie. Sealer, core-opt-in en documentworker blijven buiten deze scope.

De CLI is standaard read-only. `--apply` vereist de manifest-SHA, maar technische flags vervangen geen uitvoeringsgoedkeuring of controle van de gedeployde guard. Die operationele voorwaarden blijven verantwoordelijkheid van de coördinator.
