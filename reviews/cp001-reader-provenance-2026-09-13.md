# CP-001 gepinde owner-reader snapshotreceipt

Code `695567fcb41a9b6fc480ac3bd101cfd20b8710ce`.
Afgestemde runtimeownership uitsluitend people_memory_overview.py: bestaande
snapshot-lokale ownerquery/check mechanisch verplaatst naar require_private_owner_binding.
De geheugenreader gebruikt dezelfde queryvoorwaarden, provider/channelcheck, aliases,
transactie en scopes als voorheen. Geen authrechten, routes, schema of providerwijziging.
Overige bestanden zijn benchmarkreader/CLI, test en README.

## Kleinste daadwerkelijke provenancelevering

OwnerSnapshotReader is een niet-geïnstalleerde benchmarkseam. Zij vereist een
geconfigureerde bestaande DashboardAccess-token; de development-uitzondering met
lege token is hier niet toegestaan. De actuele owner/binding/status/retentiecontrole
wordt hergebruikt in dezelfde REPEATABLE READ / READ ONLY-transactie als het journal.
Readerconfiguratie bepaalt tenant/install/owner/conversation/message; callerinvoer
levert geen scope-, eventtype- of rangefilters. Alleen de expliciete challenge komt
van de afnemer, ter binding aan diens bedoelde leesopdracht.

De reader selecteert alle rijen die de vaste requestbinding dragen, controleert
bestaande payloadhashes en maakt daaruit de bekende directe eventprojectie. De
receipt bindt PG-snapshotcursor, rijcount, per-thread sequencereeks, scopehash,
volledige rijsethash, exporthash en bron-/configuratiemetadata. Signing volgt pas na
succesvol afsluiten van de read-onlytransactie. Non-secret receiptmetadata bevat geen
prompttekst of owner-ID in leesbare vorm; de volledige private rijen staan alleen
in de expliciete lokale bundle, nooit in Git.

Ondertekening/verificatie gebruikt de bestaande EvaCanonicalJSON-encoder en
verify_ed25519_signature-adapter met de bestaande cryptography-bouwsteen, zonder
zelf geschreven cryptografie. De afzonderlijke operator-trustfile pint key, context,
scopehash en challenge. Een embedded public_key/boolean is geen trustroot. De CLI
controleert signature, inhoud, selectie, range en context; daarna volgt de bestaande
native accountingpreflight. Zie README voor --reader-bundle/--reader-trust.

## Wat dit wel en niet bewijst

Een geldige receipt bewijst alleen binding aan de gepinde readerkey en exacte inhoud
plus metadata. Zij bewijst niet vanzelf dat die key een betrouwbare live installatie
vertegenwoordigt, dat de opgegeven image/config betrouwbaar is, of dat providers,
helpers en geheugen volledig gedekt zijn. In deze lokale proef is image_digest null;
image_binding en productauthenticiteit blijven unknown. Een niet-null image zou
slechts gepinde metadata zijn, geen zelfstandig imageattestaat.

Een oude geldige snapshot blijft een geldige oude snapshot wanneer nieuw werk
verschijnt. Challenge/cursor zijn gebonden, geen automatische freshness/replayservice.
De readerquery omvat alle matching rows op haar transactiesnapshot; dat is geen
claim van volledigheid van alle providerwerk of events zonder die requestbinding.
Server quiescence/live-admission blijft ongewijzigd unknown/false, CLI exit2.
Nog ontbrekend voor productprovenance: gecontroleerde readerkey-provisioning en
betrouwbare live source/image/configbinding; geen self-submitted vlag kan dit vervangen.

## Isolated PG-bewijs

86 gerichte tests op immutable codecheckpoint plus Ruff/diffcheck. De nieuwe proef
gebruikt echte PG, bestaande ownerauth/binding, bestaande native journal en echte
Ed25519 signing met een tijdelijke in-memory sleutel. De testoperator pint de public
key apart. Ontbrekende/verkeerde token en verkeerde/ontbrekende/verlopen binding worden
geweigerd. Een andere eigenaar binnen dezelfde tenant komt niet in de snapshot.
Toegestane snapshot bevat 3 echte rows, waarvan 2 directe model-events. Een later
journalrecord verandert de ondertekende snapshot niet; een nieuwe read bevat 4 rows.
Truncatie, gewijzigde challenge/config en aanvallersleutel worden geweigerd.
De CLI verifieert de toegestane receipt en houdt live_enabled false.

De bestaande owner-memory-proef met alias/paginatie/erasure/revocation is tevens
groen: toegestaan en geweigerd gedrag bleef behouden. De fixture gebruikt haar
bestaande historische autoriteitsclock 2026-08-26 en synthetische personen; dit is
geen actuele liveowneracceptatie. Geen private signing key is opgeslagen.

Artifact `/home/administrator/project/.eva2-artifacts/cp001-reader-provenance-20260913`
bevat uitsluitend synthetische private bundle, aparte public trustfile, CLI-uitvoer,
JUnit/tests, verifier en brongebonden receipt. Acht bronbestanden gecontroleerd.
Artifactmap is privaat (0700); geen persoonsdataexport naar publieke Git.
PG16.13 network none/tmpfs/private Unixsocket, na fixture nul eva-schema's en container
verwijderd/gecontroleerd. Geen livecalls, nieuwe endpoint of automatische deploy.
Onafhankelijke review vóór integratie; centrale beheerder houdt het werkbord bij.
