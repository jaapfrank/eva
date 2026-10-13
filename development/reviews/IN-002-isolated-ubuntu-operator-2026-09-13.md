# IN-002: echte geïsoleerde Ubuntu-operatorproef

De eerste provisioningstap is uitgevoerd. Dit vervangt de eerdere constatering dat lokaal nog geen passende gast beschikbaar was. Het is geen supported-platformverklaring of volledige hersteloplevering. Bewijs: `../evidence/product/IN-002-isolated-ubuntu-operator-2026-09-13.json`. Onafhankelijke review is nog nodig.

## Primaire distributiebewijzen

- [Ubuntu 24.04.4 SHA256SUMS](https://releases.ubuntu.com/24.04.4/SHA256SUMS) en bijbehorende `.gpg`: geldige handtekening via bestaande Ubuntu-keyring. Server-ISO24.04.4 staat met SHA256 `e907d92eeec9df64163a7e454cbc8d7755e8ddc7ed42f99dbc80c40f1a138433` vermeld; de grote ISO is niet gedownload.
- De gebruikte [vaste Ubuntu cloudimage-release20260209](https://cloud-images.ubuntu.com/releases/noble/release-20260209/) heeft eveneens geldige SHA256SUMS-handtekening. De gedownloade amd64-schijf (628.322.816bytes) matcht `2f9acc20a381cde6fa678672e20a1408d2b3f346531b9026df2a935c37d2de20`.
- [Docker Noble InRelease](https://download.docker.com/linux/ubuntu/dists/noble/InRelease) is cryptografisch gecontroleerd met de officiële Docker-sleutel. De hash van [Packages](https://download.docker.com/linux/ubuntu/dists/noble/stable/binary-amd64/Packages) staat daarin. Zowel docker-ce als docker-ce-cli `5:29.7.2-1~ubuntu.24.04~noble` zijn aanwezig, gedownload en op de geregistreerde hashes gecontroleerd. containerd.io `2.3.5-1~ubuntu.24.04~noble` komt uit dezelfde gesigneerde index; dat is een expliciete gastdependency, geen wijziging van productpins.

Beide gevraagde upstream-pins bestaan dus. Er is geen aanleiding ze wegens vermeende onbeschikbaarheid te vervangen. Alle manifests, signatures, geselecteerde packagevelden en downloadhashes staan duurzaam onder de stage `upstream/`.

## Uitvoering en isolatie

Een eigen QEMU-toolimage is gebouwd vanuit de bestaande digestgepinde Alpine3.22-basis met diens gesigneerde packagevoorziening. Het resultaat is vastgezet op `sha256:a664c7040f2cc02103fb07849a8f04a55527000b9e0db745ac09943c4d4928fa`; volledige geïnstalleerde toolversies blijven behouden. Alleen deze toolcontainer gebruikt `/dev/kvm`; hij is niet privileged, draait als1000:1000, met alle capabilities verwijderd, read-only root en uitsluitend een nieuwe stagebind. Geen host-Docker-socket, productievolume of gepubliceerde poort.

De gast kreeg een eigen20GiB qcow2-overlay, 2vCPU en4GiB RAM (toolcontainer5GiB). Cloud-init installeerde de gehashte Dockerpakketten in de gast en startte uitsluitend die eigen daemon. Python3.12.14 komt uit de bestaande gepinde officiële Pythonimage; het bestaande operatorpackage is onveranderd hergebruikt. Er is geen hostupgrade uitgevoerd: de hostdaemon meldt na afloop nog29.1.3. Alle drie gaststarts zijn afgesloten; containerbewijzen en schijf zijn behouden voor review.

## Werkelijk resultaat

Echte hostdetectie meldt **Ubuntu24.04.4 LTS, amd64, Python3.12.14, Docker29.7.2**. Alle44 package-distributies, imports en verplichte assets kloppen. De normale launcher is gestart, bootstrap geeft204 en de echte herstelpagina opent. Herstelvoorbereiding geeft terecht409; de fictieve, niet-bereikbare database en autoriteitslocatie zijn niet gebruikt, de testwerkmap blijft leeg.

De eerste twee consoleproeven liepen tegen een20s-timeout bij het lezen van de URL uit een gebufferde Python-pipe. De tweede legde tevens echte preflight vast en leverde geen stderrfout. De derde gebruikte uitsluitend `python -u` voor hetzelfde launcherproces in het testharnas: de console-URL kwam door en de HTTP-proef slaagde. Beide mislukkingen blijven in de logs staan. Dit is een noodzakelijke harnesscorrectie, geen verstopte productpatch of claim over productlatency.

## Wat nu concreet ontbreekt

De actuele preflight blijft op drie velden geblokkeerd. `_target` vergelijkt letterlijk “Ubuntu Server24.04.4 LTS” met echte metadata “Ubuntu24.04.4 LTS”. Daardoor wordt ook de enginecheck niet aan het gekozen target gekoppeld, ondanks de werkelijke29.7.2-server. Dit labelprobleem is nu aangetoond op een authentieke Ubuntu-server-cloudimage. De volgende kleine bronwijziging kan de herkenning op betrouwbare ID/versiegegevens baseren, met negatieve variant-/versietests; die wijziging is hier niet uitgevoerd.

Onafhankelijk daarvan blijft release_support geblokkeerd: lock nog selected_for_validation, beide tier1-bewijzen ontbreken, OCI-records nog onopgelost. De normale preflight, PlatformLock-contract en lock in het package zijn bytegelijk aan de huidige bron. Geen metadata of ready-receipt is gefabriceerd. Een herkenningsfix mag deze releasegrens niet passeren. Volledige herstelproef via de normale launcher volgt pas na echte releasecontractvervulling; arm64 en volledige release blijven afzonderlijk werk.
