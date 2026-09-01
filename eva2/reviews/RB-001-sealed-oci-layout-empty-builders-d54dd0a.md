# RB-001 — onafhankelijke review

Besluit: **GO**

- Taak: `RB-001`
- Implementatie: `d54dd0af97514acf6189ef7a1c0f66dafbb86086`
- Bewijscommit: `51116e8a10783645606ebe4413d004b4f8c59adc`
- Bouwer: `/root/release_authority`
- Reviewer: `/root/proof_review`

## Gecontroleerd

- De implementatiecommit is rechtstreeks gebaseerd op `56c3e5231620ed7d805cf259d26f5e1633dc56a7`
  en wijzigt exact de zes vooraf toegelaten RB-001-bestanden.
- De drie basisimages worden als verzegelde, digestgebonden OCI-layouts aan de
  echte Dockerfile aangeboden. Registry-pulls en buildnetwerk staan uit.
- Verkeerde archivebytes, digest, platform, rol, tag, builderidentiteit of een
  klassieke Docker-store stoppen vóór de kandidaatbuild.
- De exacte tests zijn opnieuw uitgevoerd: `40 passed`. De exacte Ruff-opdracht
  is groen.
- Twee nieuwe private preflightreceipts bewijzen ieder nul Docker-images, nul
  containers, nul containerd-images, nul bytes BuildKit-cache, lege routes,
  alleen een uitgeschakelde loopbackinterface en lege daemonproxyvelden.
- Ieder postflightreceipt bindt zijn preflight- en buildreceipthash, beide
  challenges en dezelfde volledige builderidentiteit vóór en na de build.
- De twee runs hebben verschillende daemon-, BuildKit-, Docker-socket-,
  containerd-socket-, state-root-, namespace- en challenge-identiteiten.
- Na iedere build staat alleen de ongetagde kandidaat in Docker. Inspectie van
  alle drie basisimagereferenties faalt; de netwerkroute blijft leeg.
- Beide kandidaatarchives zijn bytegelijk met SHA-256
  `b8dc34d47e0cdaf183b1b100c29e9b49fdb8b038f33d7a4914b103e5addf88cd`.
  Ook bron-, context-, BuildKit- en image-identiteit zijn gelijk.
- Alle zes hashes in het publieke evidencebestand komen exact overeen met de
  bewaarde private preflight-, build- en postflightreceipts.

## Begrenzing

Dit is GO voor de offline bootstrap van RB-001. Het bewijs scant, ondertekent,
tagt, pusht of deployt de kandidaat niet en wijzigt geen live Eva-route,
container, volume, database, secret of service. Buildx `0.18.0` publiceert geen
apart Docker-exporterfeatureveld in zijn JSON-uitvoer; daarom wordt dat terecht
niet afzonderlijk geclaimd. De containerd-imageopslag is vooraf gecontroleerd
en beide fysieke Docker-exports zijn geslaagd.
