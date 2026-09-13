# IN-002 — gecontroleerd herstel via de installatiewizard

## Gebruikersproef (na plaatsing op een geïsoleerde doelinstallatie)

1. Open de bestaande lokale installatiewizard met de door de beheerder verstrekte sessielink.
2. Kies bij **Reservekopie herstellen** het versleutelde portable-bestand (maximaal 64 MiB).
3. Vul het wachtwoord van die reservekopie in en kies **Controleer reservekopie**.
4. Bekijk de gevonden gegevens en verbindingen waarvoor opnieuw aanmelden nodig is.
5. Kies **Herstel gecontroleerde reservekopie**. De bevestiging is tien minuten geldig en eenmaal bruikbaar.
6. Controleer de herstelde identiteit en gegevens; meld de vermelde verbindingen opnieuw aan.

Het bestand en wachtwoord worden alleen naar de bestaande loopback-wizard gestuurd. Het wachtwoord wordt niet opgeslagen. De server bewaart gecontroleerde bytes tijdelijk in een privémap; opnieuw controleren, aflopen bij uitvoering en afsluiten ruimen deze staging op. Sluit de wizard wanneer de proef klaar is.

## Technische samenstelling

De bestaande wizard krijgt een neutrale geïnjecteerde `InstallerRestorePort`. De operatorlauncher `python -m tools.installer_restore_wizard --restore-config /absoluut/privaat/restore.json` hergebruikt de bestaande wizardopties, bootstrap, preflight, portable-transport, volledige reader, decoder, objectmaterialisatie, keyring en herstelautoriteit. Geen runtime-import van tools en geen eigen archiefcodec.

De privéconfiguratie bevat exact `dsn`, `authority`, `workspace`, `objects`, `keyring` (absoluut pad of null) en `traffic_closed: true`. De database is vooraf als lege, verkeerloze doelinstallatie voorbereid; workspace bestaat als eigenaar-only map. Bronherstelautoriteit en eventuele bestaande `EvaObjectKeyring/v1` worden afzonderlijk geleverd en nooit uit de upload afgeleid. Het HTTP-verzoek kan de doelconfiguratie niet aanpassen. Geheimen staan niet in commandoregelargumenten. De bestaande preflight moet ready zijn; er is geen bypass.

De decoder controleert alle rijen vóór bevestiging; uitvoering leest bytes en herstelautoriteit opnieuw. SERIALIZABLE plus de bestaande exclusieve herstelcontrole weigert bestaande canonieke data. Het bestaande objectjournaal blijft leidend bij materialisatie; een databasefout kan reeds geauthenticeerde CAS-objecten achterlaten, maar er worden geen half ingevoegde databasegegevens gecommit.

## Grenzen

Dit is de gebruikersroute voor het bestaande canonieke portable-contract. Externe diensten (zoals Hindsight), runtimeprocessen, installatiehoofdsleutels en credentialgeheimen zijn geen archiefinhoud. Dit bewijst geen complete computerverhuizing van alle draaiende diensten. Een archief met objecten zonder afzonderlijk beschikbare sleutels wordt geweigerd. Een bestand boven 64 MiB wordt expliciet geweigerd. Herstel van sleutels, fysieke installatie en Jaaps acceptatie zijn niet namens hem afgevinkt.

De technische proef gebruikt uitsluitend synthetische data en wegwerp-PostgreSQL. Er is geen live herstel of uitrol uitgevoerd.

## Aanvullende objectproef

Een volledig synthetisch archief met versleuteld document is via dezelfde wizard naar een lege PostgreSQL-doelinstallatie hersteld. De doelbytes worden geauthenticeerd en ontsleuteld; plaintextlengte en SHA-256 moeten overeenkomen. Connectoren blijven `reauthorization_required`.

De invoegvolgorde volgt de bestaande niet-uitstelbare catalogusafhankelijkheden; uitstelbare cycli worden bij COMMIT gecontroleerd. Nullable JSON-kolommen worden als SQL NULL gebonden wanneer de archiefwaarde null is. Geen databaseconstraint wordt gewijzigd of uitgeschakeld.

Bij intrekking vóór uitvoeren ontstaan geen doelobjecten of databaserijen. Bij intrekking ná objectmaterialisatie blijft alleen het bestaande private CAS-object met zijn materialisatiejournaal achter, zonder databaserijen of actieve ObjectRef; de bevestiging en tijdelijke archiefstaging zijn verwijderd. Dat is nadrukkelijk geen atomaire terugdraaiing van database én bestanden. Herhalen met het verbruikte token verandert niets. Gecontroleerd beheer van achtergebleven CAS-objecten blijft bij de bestaande operatorgrens.

De nieuwe testvariant herstelt inconsistenties in historische synthetische vectoren uitsluitend in een eigen fixture: genormaliseerde rij/payload-identiteiten, retentie-deadline en confirmation-delivery-ingress. De oorspronkelijke vectoren en hun afwijzingstests blijven intact.
