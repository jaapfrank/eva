# Onafhankelijke review — installer en onboarding IN00

Datum: 25 augustus 2026.

## Scope

- `design/product/installer-and-onboarding-v1.md`
- de installatiestatus in `PROJECT_STATUS.md`
- de installatie- en geheugenbestandsgrens in
  `EVA2_MOGELIJKHEDEN_EN_GATEN.md`
- de installer/onboardingrij in `design/architecture/living-core-v1.md`

De reviewer wijzigde niets en benaderde geen B-service, secret of externe
connector.

## Eerste oordeel

`NO-GO` door drie te ruime huidige claims:

- Linux/amd64 en Windows/WSL2 waren als ondersteund beschreven terwijl de
  platformlock ze nog `unvalidated`/`unsupported` noemt.
- Een fresh PostgreSQL/core-proef met bestaande externe Hindsight- en
  modelautoriteit was te ruim als volledige lege-stackinstallatie samengevat.
- De portable export kan versleutelde objectpayloads dragen, maar versleutelt
  niet het hele exportpakket.

## Correcties en eindoordeel

- De platformen zijn alleen beoogde targets; de huidige kandidaat heeft enkel
  een `linux/amd64`-buildconstraint en IN06 moet ondersteuning bewijzen.
- De huidige installatiebewijzen zijn begrensd tot componenten en verse
  PostgreSQL/core met hergebruikte externe diensten; managed adoptie blijft
  open.
- De bestaande export is canoniek en gesaneerd. Gehele transportversleuteling
  plus restore is toekomstig IN07.

Eindoordeel: `GO`. De afwezigheid van runtime-`SOUL.md`/`MEMORY.md`, de
IdentityPack/PostgreSQL/Hindsight-equivalenten, staged import, lokale
secretgrens, least-privilege connectoren en IN01-IN08 zijn eerlijk en
fail-closed vastgelegd. Dit bewijst nog geen productinstaller of ondersteund
installatieplatform.
