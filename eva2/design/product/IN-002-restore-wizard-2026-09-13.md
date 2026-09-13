# IN-002 — gecontroleerd herstel via de installatiewizard

## Huidige beschikbaarheid

De normale operatorlauncher is **nog niet herstelklaar** op de huidige WSL-host. De echte preflight meldt vier blokkades; bovendien bevat de enige getrackte platformlock geen gevalideerd ondersteund target. De onderstaande gebruikersroute is daarom nog geen nu uitvoerbare proef. De API/PG-proeven gebruikten expliciet een synthetische ready-preflight en bewijzen de herstelfunctie achter die poort, niet de normale launcher.

Ook de bestaande offline Python- en two-builderbewijzen leveren geen ondersteunde release-lock. Eerst moet de platformvalidatie een echte gevalideerde lock met bewijs opleveren; daarna moet de ongewijzigde launcher op een lege geïsoleerde doelinstallatie worden beproefd. IN-002 blijft tot die tijd een technisch deelresultaat. Er is hiervoor geen nieuw wachtwoord of privéarchief van Jaap nodig.

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

## Begrensd voorstel voor eerste platformvalidatie (nog niet uitgevoerd)

De huidige lock (`363599c01b730bcc100a81503e12cc9e424c6c98d9b5646a18ed46d2e213fec9`) heeft `claim_state=selected_for_validation`; beide Ubuntu Server 24.04.4 LTS-targets (amd64/arm64) zijn `unvalidated`, `unsupported`, zonder `validation_receipt_sha256`. Alle achttien OCI-records missen repository, digest en SBOM en zijn `runnable=false`. `contracts/platform.py` vereist bij `validated` beide tier1-bewijzen én alle resolved OCI-records. Eén geslaagde amd64-proef mag die brede toestand dus niet veranderen.

**Eerste beperkte stap:** maak uitsluitend het feitelijke amd64 operator-runtimepad reproduceerbaar en bewijs dat de normale launcher met echte hostdetectie de verwachte blokkades meldt. Hergebruik de bestaande gepinde Python 3.12.14-basis `docker.io/library/python@sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134`, het bestaande offline-wheelproces en de nu gereviewde IN-002-source. Controleer eerst of de cached wheels de huidige requirements werkelijk dekken; het historische bewijs met 39 distributies is geen bewijs voor de actuele dependencyset. De daadwerkelijke operatorpackage moet ook de bestaande tools/portable-reader en schemas bevatten; een core-image met alleen runtime-src is onvoldoende.

**Ontbrekende controles:** exact dependency/source/schema-manifest en offline imports; echte Python/OS/Docker-detectie; opstarten van de ongewijzigde launcher en verwachte fail-closed HTTP-reactie zonder synthetische ready-receipt; vervolgens expliciete planning van het echte tier1-validatiepad. Controleer de OS-identificatie: de lock vergelijkt letterlijk “Ubuntu Server …”, inspect_host leest PRETTY_NAME “Ubuntu …”. Een eventuele normalisatie moet op betrouwbare OS-ID/versiegegevens steunen en mag release_support niet passeren. Een container met Python 3.12.14 verandert de bestaande host-Docker 29.1.3 niet in 29.7.2.

**Resources:** één wegwerp-amd64 operatorcontainer met read-only bron, private tmpfs en netwerk uit; bestaande gecachte basis/wheels; voor latere end-to-end herhaling uitsluitend een afzonderlijke lege PostgreSQL 16.13-doeldatabase en synthetische archive/key/authority. Voor echte versie-exacte tier1-validatie zijn afzonderlijke Ubuntu/Docker-29.7.2-testhosts of VM's nodig, uiteindelijk zowel amd64 als arm64; hun beschikbaarheid is nog niet bewezen. Geen huidige hostupgrade, live Docker-socket in een willekeurige container, privébackup of eigenaarwachtwoord nodig.

Deze eerste stap levert een concreet compatibiliteitsrapport en package op, geen supported-label. De normale herstelroute blijft dicht totdat het bestaande releasecontract eerlijk vervuld is. Dit is technisch releasewerk; het is geen blokkade die Jaap met een nieuwe keuze moet oplossen.
