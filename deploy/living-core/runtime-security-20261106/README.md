# Gerichte runtime-beveiligingsbouw — 6 november 2026

Dit zijn openbare bouwinvoer en controles voor de behouden Eva-runtime. Deze map
bevat geen credentials, DSNs, databasegegevens, wheels, Debian-binaries,
privécriteria, actieve claims of installatieplan. Bouwen start geen Eva-dienst en
vervangt geen draaiende container. Gebruik de bestaande image-updater voor een
apart beoordeelde installatie met verse claim-/lease-/STOP-/geheugengates.

## Vastgelegde wijzigingen

`vendor-inputs.json` bindt twee onafhankelijke overlays:

1. Hindsight Python 3.11.16 → 3.11.18 uit de officiële, digestgepinde Linux/amd64
   Python-image. SOABI, GCC en configuratieopties blijven gelijk. Uitsluitend
   1069 interpreterpaden worden vervangen; site-packages, pip, native distro-
   bibliotheken, Hindsight-bron en entrypoint blijven behouden.
2. Ondertekende Debian 13/trixie-patches voor de exacte core-, worker- en
   Hindsight-Python18-bases. Alle 14 werkelijk geïnstalleerde distroversies zijn
   vergeleken met trixie en trixie-security. Acht waren actueel; zes wijzigen:
   libc6 naar `2.41-12+deb13u4`, libssl3t64 naar `3.5.7-1~deb13u3`,
   ca-certificates naar `20250419+deb13u1`, tzdata en tzdata-legacy naar
   `2026c-0+deb13u1`, base-files naar `13.8+deb13u7`.

Het OpenSSL-upstreamnummer blijft 3.5.7; de Debian-revisie bevat extra
beveiligingspatches. Alle zes Depends-velden blijven gelijk. Bestaande distroless-
omissies blijven bewust ontbreken: geen shell, pakketbeheerder, debconf of
optionele legacy-provider toevoegen. De bestaande aangepaste Distroless
os-release blijft exact behouden; Debian-pointversionmetadata wordt bijgewerkt.

CA-bundlebeheer verwijdert uitsluitend de twee exacte DER-hashes van de door
het ondertekende Debian-pakket ingetrokken Comodo AAA- en emSign ECC G3-roots.
De oude en nieuwe pakketversies zijn vergeleken: alle overige PEMbytes blijven
gelijk. Bestaande bundelvolgorde en eventuele eigen roots blijven behouden.

## Vereisten en interpretercontext

Gebruik Linux, Python 3, Docker, gpgv en dpkg-deb. De in het manifest genoemde
basisimages en officiële vendorimage moeten lokaal aanwezig zijn. De basisimages
bevatten eerdere beoordeelde Eva-overlays en zijn vereist; willekeurige upstream-
images vervangen die invoer niet. De officiële vendorimage kan expliciet worden
opgehaald met zijn digest uit `vendor-inputs.json`.

```sh
python3 prepare_python31118.py --output /absolute/new/python31118-context
# Optioneel: --build-image. Dockerbouw gebruikt --network=none en --pull=false.
```

De voorbereider extraheert alleen `/usr/local` uit niet gestarte containerobjecten,
verifieert de volledige canonical-hashes van de geselecteerde oude/nieuwe
interpreterbestanden en maakt een offlinecontext. De oorspronkelijke
PSF-licentietekst staat in `CPYTHON-LICENSE.txt` en blijft ook in de vendorstdlib.

## Ondertekende Debian-invoer en native contexten

```sh
python3 prepare_native.py --output /absolute/new/native-contexts
# Optioneel: --build-images. Dit bouwt uitsluitend de drie contexten.
```

De helper haalt de Debian-archivekeyring uit de officiële digestgepinde
vendorimage en controleert diens SHA256. Gpgv controleert beide opgeslagen
InRelease-bestanden. De hashketen loopt verder naar de volledige Packages.xz en
ieder exact Debian-pakket. De keyfingerprints zijn tevens onafhankelijk getoetst
tegen de officiële Debian-publicatie. De originele auteurs-/licentiemetadata van
Debian-pakketten blijft behouden; geen licentievoorwaarden worden gewijzigd.

De bouw is een historische reconstructie van de beoordeling van 6 november,
geen claim dat deze versies op een latere datum de nieuwste zijn. Bij gewijzigde
publieke indexen weigert de helper de afwijkende hash. Gebruik dan de behouden
exacte indexen uit een passende officiële archiefkopie via `--index-directory`:

```sh
python3 prepare_native.py --output /absolute/new/native-contexts \
  --index-directory /absolute/verified-indexes \
  --deb-directory /absolute/verified-deb-cache
```

Die invoer heeft submappen `trixie/Packages.xz` en
`trixie-security/Packages.xz`. Een optionele .deb-cache wordt op dezelfde signed
checksums gecontroleerd. De helper kopieert alleen bestaande, bewezen package-
owned bestanden; afwijkende baselinebestanden worden geweigerd, met uitsluitend
de vastgelegde aangepaste os-release als expliciet behouden uitzondering.
Gegenereerde CA-bundel en distroless status.d worden afzonderlijk gebonden.

```sh
EVA_RUNTIME_NATIVE_INPUTS=/absolute/new/native-contexts \
  PYTHONPATH="$PWD" python3 -m unittest discover -s tests -p test_bundle_filter.py
```

Elke Dockerbouw blijft netwerkloos. Deze helpers inspecteren en exporteren alleen
niet gestarte tijdelijke containerobjecten en verwijderen uitsluitend die eigen
objecten. Zij raken geen actieve containers, volumes of databases.

## Bewijs en grenzen

`validation-summary.json` geeft de gerichte uitkomsten, inclusief de twee
ongewijzigde bestaande providertestfouten. Volledige bestandsvergelijkingen
controleerden bytes, modes, eigenaren en links; alleen289 gebonden packagepaden
plus twee verklarende metadatafiles per native image veranderen. Imageconfiguratie,
appbron en Pythondependencies blijven gelijk. Een eerste Hindsight-bouw met
bijgeschreven .pyc-bestanden is afgewezen; de bouwcontrole gebruikt `python -B` en
sluit geen caches uit van de vergelijking.

Nieuwe Docker-tijdstempels kunnen andere image-ID's opleveren. Verifieer een
herbouwde image opnieuw en bind de nieuwe digest pas na onafhankelijke review.
De native-contextmanifesten gebruiken de exact behouden Python18-basis; een
anders gehashte interpreterherbouw is geen automatische vervanging daarvan.

De oorspronkelijke geïsoleerde canaries gebruikten de bestaande Astra/medium-
access-lease: twee coremodelrequests en twee native geheugenmodelrequests, uitsluitend
synthetische inhoud. Cost, facturering en reasoningtokens zijn onbekend. Het nieuwe
geheugenbankje is na bewijs verwijderd; alle 21 bankgebonden tabellen waren leeg en
de testworker is gestopt. Dit bewijst de geraakte route en sluit E4.16 of het
volledige Eva-project niet af. Geen bestaande modelrequest of onbekend effect
opnieuw uitvoeren om een herbouw te controleren.
