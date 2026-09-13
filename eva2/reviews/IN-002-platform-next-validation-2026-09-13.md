# IN-002: concrete platformgrens en volgende validatie

Dit is een beschikbaarheidsaudit op bron `1df730fdb8b08c17fbed56835d8f2c04864002b6`, naast de bestaande package-oplevering `5da0a2198bf3e3a2b2e005049f50ced71c875e9d`. Geen nieuwe herstelproef of productwijziging. De bijbehorende actuele, gesaneerde inventaris staat in `../evidence/product/IN-002-platform-availability-2026-09-13.json`.

## Uitkomst

Er is binnen de huidige lock **geen ondersteunde omgeving** waarop deze normale herstelroute groen kan worden. Alle vijf targets zijn unsupported; de twee tier1-targets zijn unvalidated zonder bewijsreferentie. Alle achttien OCI-records zijn onopgelost. Dit is geen nieuwe eigenaarblokkade.

De bestaande operator-tar is nog beschikbaar en zijn hash klopt: `f571e9f1c8b9aa36b3044f846ab074ee6724cf9de24125558109e271f9932f96`. De eerdere echte launcherproef bewees Python 3.12.14, imports, assets en een bereikbare pagina, maar terecht HTTP 409 voor herstel. Diezelfde proef opnieuw uitvoeren levert geen nieuw bewijs en is niet herhaald.

De huidige lokale host meldt Ubuntu 24.04.4 LTS / x86_64 en Docker Server 29.1.3. De gecachte Docker-in-Docker-basis `sha256:64d6ee47ea821c986467199baa162f5ac8cde3f57b719f18e23f3ed7a7444131` declareert eveneens Docker 29.1.3. Zij vult het gat naar 29.7.2 dus niet. `/dev/kvm` bestaat, maar qemu-system-x86_64, qemu-system-aarch64, qemu-img, virsh en cloud-localds ontbreken in PATH. Er is met deze begrensde inventaris geen passende startklare geïsoleerde VM aangetoond. Dit is geen uitspraak over onbekende remote hosts.

## Vereiste tuple en twee afzonderlijke grenzen

De geselecteerde eerste tuple is Ubuntu Server 24.04.4 LTS / amd64 / containerized / Docker Engine 29.7.2, met operator-Python 3.12.14. De uiteindelijke release vereist daarnaast dezelfde tuple voor arm64, PostgreSQL 16.13, DBOS 2.30.0, Pydantic 2.12.5 en Pydantic-AI Slim 2.37.0 volgens de bestaande lock.

1. **Echte omgeving:** een geïsoleerde gast met de juiste OS-, Python- en Docker-versies ontbreekt in het aangetoonde aanbod. De bestaande Debian-operatorcontainer bewijst deze hosttuple niet. Een Docker-socket van de huidige host doorgeven verandert diens versie niet.
2. **Releasebewijs:** zelfs een passende amd64-gast maakt de huidige lock niet supported. `PlatformLockV3` vereist bij validated beide tier1-receipts en alle resolved OCI-records (repository, digest, SBOM en runnable). De normale preflight eist vervolgens die gevalideerde release. Eén lokale launcherproef mag die voorwaarden niet vervangen.

Bovendien vergelijkt `_target` letterlijk de labeltekst “Ubuntu Server …” met de door `inspect_host` gelezen PRETTY_NAME. De huidige metadata luidt “Ubuntu …”. Bij de echte kandidaat moeten de oorspronkelijke OS-ID/versie/variantgegevens worden vastgelegd; alleen indien dat een echte herkenningsfout bewijst volgt een afzonderlijke minimale normalisatie met negatieve gevallen. Geen metadata herschrijven, lock-label aanpassen of servervariant veronderstellen.

## Eerstvolgende uitvoerbare stap: geïsoleerde amd64-kandidaat provisioneren

Dit is een concreet technisch provisioningpakket, nog **niet uitgevoerd**:

- Verifieer eerst of de exact geselecteerde Ubuntu- en Docker-versies uit hun bestaande vertrouwde distributiekanalen verkrijgbaar zijn en leg originele signature/checksum, image/disk-hash en packageversies vast. De lock is een gewenste pin, geen bewijs van upstream beschikbaarheid. Ontbreekt een exacte distributie, rapporteer dat aan de release-eigenaar; kies niet stil een andere versie.
- Laat de platform-eigenaar één aparte amd64-VM met eigen lege virtuele disk en eigen Docker-daemon leveren. Voorgestelde limiet: 2 vCPU, 4 GiB RAM, maximaal 20 GiB wegwerp-disk. Dat zijn aangevraagde grenzen, geen gemeten verbruik. KVM/tooling en eventuele acquisitie zijn uitsluitend voor die gast; geen upgrade van de bestaande WSL/Docker-installatie en geen productievolume of Docker-socket delen.
- Hergebruik de reeds gehashte operatorpackage en wheels. Controleer eerst de glibc/interpreterafhankelijkheden op de Ubuntu-gast; het bewezen Debian-package krijgt geen automatische Ubuntu-compatibiliteitsclaim. Indien noodzakelijk uitsluitend de bestaande packagebuilder op de echte gast herhalen, met dezelfde dependency-pins en een nieuw manifest.
- Laat de ongewijzigde normale hostdetectie draaien en bewaar OS-ID, VERSION_ID, PRETTY_NAME, architectuur, echte Python-versie en Docker-serverversie. Start de normale launcher met alleen een privéwerkmap. Verwacht voorlopig release_support=blocked. Dit sluit de *omgeving* af als afzonderlijk bewijs, zonder een vervalste ready-receipt.
- Geef dat gehashte resultaat aan de bestaande releasevalidatie-eigenaar voor de ontbrekende platform-/OCI-bewijzen. Pas na echte contractvervulling wordt de bestaande synthetische archief/object/herstelautoriteitsproef via de normale launcher uitgevoerd, op een lege PostgreSQL 16.13-doelinstallatie. Controleer opnieuw aanmelden, oorspronkelijke identiteit en toegestane bytes; geen privébackup nodig.

Eindpunt van deze eerstvolgende stap: één heropenbare, versie-exacte amd64-kandidaat met echte detectie en launcherbewijs, plus eerlijk resterende release_support-blokkade. Geen volledige platformpoort, supportlabel, IN-002-productafronding of live herstel. Centrale administratie blijft bij de coördinator; deze notitie is geen tweede werkbord.
