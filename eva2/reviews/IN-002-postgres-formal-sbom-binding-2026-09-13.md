# IN-002: formele PostgreSQL-bewijsbinding, geen vrijgave

De bestaande Eva2SbomBinding/v1 is nu volledig ingevuld en met de bestaande JSON-schema en URI-formatcontrole gevalideerd. Image-, SPDX-, image-licenseinventory- en vulnerabilityScan-hashes verwijzen naar echte bij elkaar horende artifacts. Het gaat om hetzelfde PostgreSQL16.13/amd64-image en archief als de geslaagde gastproef; er is geen nieuwe versie gekozen of slot gepromoveerd.

De gepinde Trivy0.74.0 heeft via zijn bestaande OCI-downloader een verse database van de officiële ghcr.io/aquasecurity/trivy-db:2 gehaald. Eerste poging had onvoldoende128MiB tmpfs; de afzonderlijk gelogde retry met512MiB slaagde. Metadata UpdatedAt13september07:13UTC en NextUpdate14september07:13UTC waren geldig ten tijde van de scan. Zowel DB als metadata zijn gehasht vóór de netwerkloze scan en bleven erna gelijk. Geen onafhankelijke cryptografische upstreamsignatureclaim; de bestaande downloader en de verkregen bytes zijn expliciet gebonden.

De bestaande algemene trivy_command en finding_counts leveren **110 HIGH en18 CRITICAL**. Dat zijn scannerbevindingen, geen uitspraak over exploitbaarheid; unieke aantallen en pakketten staan in de scan-summary. Deze image is dus niet security- of releasevrijgegeven. De149 imagepackages zijn allemaal in de image-specifieke licentie-inventaris opgenomen met SPDX-bronhash. Onbekende velden blijven onbekend:26 licenseDeclared en127 licenseConcluded. Geen juridische beoordeling gesimuleerd.

De oude RB006 whole-candidate gate heeft een letterlijk gepinde historische cache-authorityhash. Die is niet veranderd, en de nieuwe cache is daar niet ten onrechte doorheen gestuurd. De bestaande algemene scanner levert hier nieuwe actuele artifacts; voor de volledige releaseketen moet die cacheautoriteit afzonderlijk gereviewd worden vernieuwd, naast het oplossen/beoordelen van de imagebevindingen en de licentievragen. De geldige bewijsbinding maakt de kwetsbare image niet automatisch geschikt.

Exact bewijs: ../evidence/product/IN-002-postgres-formal-sbom-binding-2026-09-13.json. Geen productie- of platformmutatie, geen nieuwe eigenaarinput. Onafhankelijke review staat open.
