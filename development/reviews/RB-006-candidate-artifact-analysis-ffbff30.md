# RB-006 — onafhankelijke eindreview kandidaatanalyse

Besluit: **GO**

Taak: `RB-006` — SBOM en kwetsbaarheden controleren.

- Basiscommit: `eaaaa09a9d7955cec97bc3d8ad3bee266774b624`
- Implementatiecommit: `ffbff302cf3f5a35bb381fd6b53d17b14a65860b`
- Evidencecommit: `4bf1a958ca4e2c43ce0fb8539b52daa689fdc5dc`

## Bevindingen

De implementatiecommit is het directe kind van de basiscommit en wijzigt uitsluitend de twee vooraf toegestane RB-006-paden. De evidencecommit is het directe kind van de implementatiecommit en voegt uitsluitend `eva2/evidence/product/RB-006-candidate-artifact-analysis-2026-09-01.json` toe.

De analyse accepteert alleen het geslaagde, exact verankerde RB-005-bewijs. Publieke en private RB-005-receipts, productbron `45d2ff6d61793809a514c01b3a3c000ce1632da6`, inputauthority, release-authority, kandidaat-ID `sha256:3a4f65544be8b0b4e543ba5148b73775b35c40ee5ab4a49dd55eec9b724899f1`, RB-005-archiefhash en kandidaatreceipthash worden vóór export en scan gecontroleerd. Verkeerde, gewijzigde, verwisselde of onveilige inputs falen gesloten.

De fysieke analyseboom bevat exact vijf behouden bestanden. Alle geclaimde bytes, SHA-256-hashes en alleen-lezen modi zijn onafhankelijk gecontroleerd. De verse export heeft SHA-256 `cc583cf439f0b3c90bcc7eb77ea99eaaaff1768c965695485dcf88f76014b139`; exact diezelfde alleen-lezen export is aan beide scanners aangeboden. De code bevat binnen één analyserun één exportopdracht en bewaakt de archiefhash na Syft en na Trivy.

Syft `1.51.0` en Trivy `0.74.0` zijn aan exacte image-digests gebonden. Scannercontainers gebruiken `--network none`, `--pull never`, een alleen-lezen archiefmount en voor Trivy een vooraf en achteraf gevalideerde alleen-lezen cacheauthority. De kandidaatimage zelf is niet gestart en er is geen kandidaatcontainer gemaakt; alleen de twee gepinde scannerimages lezen het archief.

Het behouden SPDX 2.3-document is geldig en niet leeg: 76 packages, 1.521 bestanden en de verplichte relaties zijn aanwezig. De ruwe Trivy-uitvoer heeft de verwachte objectvorm en twee niet-lege resultaatgroepen. Beide bevatten nul kwetsbaarheden; HIGH en CRITICAL zijn exact nul. De productie-validator accepteerde de bewaarde fysieke boom opnieuw. Negatieve tests dekken onder andere verkeerde RB-005-ankers, gewijzigde receipts/bytes, onveilige paden en modi, cachewijziging, misvormde of lege SPDX/Trivy-uitvoer en HIGH/CRITICAL-bevindingen.

Deze GO bewijst alleen RB-006. Zij claimt geen afwezigheid van alle kwetsbaarheden, signing, push, Compose-koppeling, deployment, promotie of releasegoedkeuring.

## Herhaalde controles

- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp pytest -q tests/operations/test_managed_b_artifact_analysis.py tests/operations/test_container_vulnerability_gate.py` — **36 passed**
- `ruff check tools/managed_b_candidate/artifact_analysis.py tests/operations/test_managed_b_artifact_analysis.py` — **All checks passed**
- `git diff --check eaaaa09a9d7955cec97bc3d8ad3bee266774b624..4bf1a958ca4e2c43ce0fb8539b52daa689fdc5dc` — **geen fouten**

Daarmee zijn de letterlijke `doneWhen`-voorwaarden van RB-006 vervuld.
