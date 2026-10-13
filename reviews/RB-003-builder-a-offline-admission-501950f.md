# RB-003 — onafhankelijke review builder-A-admission

Besluit: **GO**

Beoordeelde keten:

- basis: `3f1d9fcb95a5f97dc5a67e9d8d5b56ef09e6d2ee`
- implementatie: `501950f998b5801df3ca245eb625b9fb7fbb7260`
- evidence: `c6149e53ba6eab4f50b4171271cc7e31855f9b44`
- toegelaten bron: `45d2ff6d61793809a514c01b3a3c000ce1632da6`
- release-inputauthority: `20323cc166d5b7055c43dc94a0d5e09c47ab729036e479c166768a2a6e841020`

De implementatiecommit heeft de opgegeven basis als directe parent en wijzigt
uitsluitend de twee RB-003-`changePaths`. De evidencecommit heeft de
implementatie als directe parent en voegt uitsluitend het ene evidencebestand
toe.

## Bewijsbeoordeling

- De private preflight-, build-, postflight-, releaseauthority-, kandidaat- en
  admissionreceipts bestaan als private reguliere bestanden; alle publieke
  hashes komen exact overeen.
- De preflight bindt een lege Docker-, containerd- en BuildKit-staat, een
  routeloze namespace met uitsluitend uitgeschakelde loopback, de builder en
  drie afzonderlijke challenges vóór de build.
- Broncommit `45d2ff6d`, source-archivehash `79dd7fae…` en authorityhash
  `20323cc1…` zijn door de volledige keten gelijk gebonden.
- Builderidentiteit is vóór, tijdens en na de build gelijk. De postflight bevat
  precies één ongetagde kandidaat en geen van de drie verzegelde basisimages.
- De kandidaatarchivehash `10840641…` en kandidaatreceipthash `7a5f0109…`
  zijn rechtstreeks op de bewaarde bestanden herhaald.
- De Docker-manifest-, config-, image-ID-, laagblob- en `rootfs.diff_ids`-
  binding wordt opnieuw uit de archivebytes berekend. Zowel ongecomprimeerde
  als gziplagen worden correct behandeld.
- Verkeerde bron/authority, niet-lege start, gewijzigde builder, hergebruikte
  challenge of admission, verwisselde receipt/hash en archive/BuildKit-splice
  worden fail-closed geweigerd.
- De admissioncode start of herhaalt geen build: zij valideert uitsluitend de
  reeds duurzaam geschreven fysieke keten. Er is dus geen verborgen
  buildretryroute.

## Omvang en hergebruik

De netto wijziging is 405 regels verifiercode en 317 regels tests. De nieuwe
code is strikt bewijsparsen en -binding boven de bestaande veilige file-,
builder-, build- en OCI-helpers in hetzelfde bestand; zij voegt geen tweede
build-, runtime- of deployroute toe. De omvang is verklaarbaar door exacte
contractsleutels, OCI/gzip-controle en de afzonderlijke aanvalstests.

`verify_single_builder_a()` is nog niet als CLI-subcommand aangeboden. Dat is
geen RB-003-blokkade: de `doneWhen` vraagt een fysiek toegelaten builder-A-keten
en onafhankelijk bewijs, niet een algemene operatorinterface. De functie is
wel direct testbaar en de echte admissionreceipt is duurzaam bewaard. Een CLI
kan voor een latere herhaalbare operatorworkflow worden toegevoegd zonder deze
bewijsuitkomst sterker voor te stellen.

## Herhaalde controles

```text
TMPDIR=/tmp TEMP=/tmp TMP=/tmp pytest -q tests/operations/test_two_builder_reproducibility.py
21 passed in 0.15s

ruff check tools/check_two_builder_reproducibility.py tests/operations/test_two_builder_reproducibility.py
All checks passed!
```

## Scope

Dit is één builder-A-resultaat. Het bewijst nog geen reproduceerbaarheid over
twee onafhankelijke builders, scan, ondertekening, push, Compose-adoptie,
deployment of live promotie. De evidence vermeldt die nonclaims correct.
Cleanup meldt Docker en containerd `inactive/dead`, geen resterende
proofprocessen en een verwijderde netwerknamespace.

## Conclusie

RB-003 voldoet aan alle `doneWhen`-voorwaarden. Er is geen resterende
reviewblokkade; builder B en de twee-buildervergelijking blijven terecht latere
bordstappen.
