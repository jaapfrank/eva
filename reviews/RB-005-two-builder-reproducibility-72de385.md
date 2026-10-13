# RB-005 — onafhankelijke review gecorrigeerde twee-builderketen

Besluit: **GO**

Beoordeelde directe keten:

- basis: `172c9b487ec1184082837ad9734e6361f48698b4`
- implementatie: `72de385d2759c64db01b3e3d53dc54629f0a846f`
- evidence: `1b56ac38adc252e53f5c26a847d4bd5c94f477fe`
- verzegelde productbron: `45d2ff6d61793809a514c01b3a3c000ce1632da6`
- release-inputauthority: `20323cc166d5b7055c43dc94a0d5e09c47ab729036e479c166768a2a6e841020`

De implementatie is het directe kind van de basis en wijzigt uitsluitend
`eva2/tools/check_two_builder_reproducibility.py` en
`eva2/tests/operations/test_two_builder_reproducibility.py`. Het evidencecommit
is het directe kind van de implementatie en voegt uitsluitend
`eva2/evidence/product/RB-005-two-builder-reproducibility-2026-09-01.json`
toe. Beide commitgrenzen zijn schoon volgens `git diff --check`.

## Bewijscontrole

- Het private resultaatreceipt is een regulier bestand met modus `0600` en
  SHA-256
  `848c236c76abe66e4c2db1d00324aa3df6987a98d4cfc41e9198de0a59261a4f`.
  Een volledig nieuwe, alleen-lezen aanroep van `verify_two_builds()` op de
  bewaarde artefacten leverde exact hetzelfde JSON-object op.
- De openbare RB-003- en RB-004-ankers hebben respectievelijk SHA-256
  `2c7122fa031779f2ab3463becac4c5906687853ab482fa016a991a34bf729ad5`
  en
  `66c570f4acd15c4ff446d8e9a860ab68c3bbf6193618e272185d98da85781d0e`.
  Hun private admissionreceipts hebben respectievelijk SHA-256
  `b60b16fe4065b1deef7a207ef05d98e259608f436e1b7bd6a07ca62f4271de70`
  en
  `20f7e8e6b4add92fa5f19d30a1fb8e5f8337e8c415ebbd8c5aaead7fc7eeee49`.
  Alle vier hashes sluiten exact op het RB-005-bewijs.
- De A- en B-ketens binden ieder hun eigen preflight-, build-, kandidaat- en
  postflightreceipt. Buildreceipts `2bd6a7f8…` en `fb0dab00…` en
  kandidaatreceipts `7a5f0109…` en `3de27f48…` zijn rechtstreeks op de
  bewaarde bestanden herhaald en verschillen zoals vereist.
- Rollen zijn vast `RB-003`/builder A en `RB-004`/builder B. Verwisselen wordt
  geweigerd. Alle zes admission-, build- en observationchallenges zijn uniek.
  BuildKit-node, daemon, Docker-socket, state-root, containerd-socket en beide
  containerd-namespaces verschillen op alle zeven verplichte
  identiteitsdimensies. De toolchain en volledige build-inputauthority zijn
  gelijk.
- Beide builders binden hetzelfde bytegelijke releaseauthorityreceipt met
  SHA-256 `a8179c61901b5c36fbfabffdd7acc5d4ab1046c4364f02cdb0148f7d2da7ae21`,
  dezelfde broncommit en bronarchive
  `79dd7faeab1e7f334e4612b2dd7b5f34d0e173f8764881bac7e299c0183b5b92`.
- De kandidaatarchieven zijn bytegelijk met SHA-256
  `108406414bf56d0d99c436a19387c39902605ab8df0820e304ac1e486a459acc`.
  Een afzonderlijke read-only OCI-herberekening bindt index naar manifest,
  manifest naar config en alle laagdescriptors naar hun blobs. Image-ID
  `sha256:3a4f65544be8b0b4e543ba5148b73775b35c40ee5ab4a49dd55eec9b724899f1`
  en config-SHA-256
  `0d6bbb33c715067ecececf07fa6d8f137bcbb5aca5062c0175896fd4c22df00b`
  kloppen.
- Alle 26 geordende ongecomprimeerde `DiffID`s zijn uit de laagbytes
  herberekend en komen exact overeen met de config. Hun canonieke lijst heeft
  SHA-256
  `1440971670373d57874d75d8810bbb75d88a2759355b7eeb09327dc0d6ac1385`;
  de expliciet als laagvolgorde benoemde binding is
  `0d42de733e3c2c32d04b97f7ff4f573e2a78cb539d1de9f8ca0fa7d09e7b9178`.
  Config-, laag- en runtimebestanddrift falen gesloten.

## Herhaalde controles

```text
TMPDIR=/tmp TEMP=/tmp TMP=/tmp pytest -q tests/operations/test_two_builder_reproducibility.py
45 passed in 0.54s

ruff check tools/check_two_builder_reproducibility.py tests/operations/test_two_builder_reproducibility.py
All checks passed!

python3 tools/check_two_builder_reproducibility.py --help
exit 0

python3 -m tools.check_two_builder_reproducibility --help
exit 0; uitvoer bytegelijk aan de directe helpvorm

cmp -s <builder-a-candidate.tar> <builder-b-candidate.tar>
exit 0
```

Vier extra read-only negatieve controles met een verkeerde A-admissionhash,
verkeerde B-admissionhash, verkeerde releaseauthority en verkeerde broncommit
werden allemaal geweigerd. Deze controles schreven geen receipt. De gerichte
testset weigert daarnaast bewijshergebruik, builderverwisseling,
challengehergebruik, gedeelde builderidentiteiten, authority- of toolchaindrift,
een los OCI-image-ID, configdrift, ongecomprimeerde laag-/runtimebestanddrift,
een hergebruikt buildreceipt en een kandidaatreceipt dat niet bij de build
hoort.

## Grenzen

Dit bewijst twee verschillende daemon-, cache- en builderstaten op één host;
het bewijst geen onafhankelijkheid van de gedeelde host, kernel of operator.
De geordende ongecomprimeerde laagbytes zijn gebonden, maar er wordt geen
afzonderlijk gematerialiseerde flattened-rootfs-treehash geclaimd. Er is voor
RB-005 geen image gebouwd en geen container, live route, database, volume,
service of secret geopend of gewijzigd. Scan, SBOM, signing, push, Compose,
deployment en promotie blijven afzonderlijke gates. Reproduceerbaarheid alleen
bewijst niet dat de kandidaat kwetsbaarheidsvrij of releaseklaar is.

## Conclusie

Beide eerder onafhankelijk toegelaten builds wijzen naar exact dezelfde
verzegelde bron en release-inputset. De volledige kandidaatarchive, OCI-config,
geordende ongecomprimeerde lagen en daarmee de runtime-laaginhoud zijn
bytegelijk, terwijl rollen, bewijsuitdagingen en alle verplichte
builderidentiteiten verschillend blijven. Daarmee zijn alle `doneWhen`-eisen
van RB-005 vervuld; SBOM- en kwetsbaarheidscontrole blijft terecht RB-006.
