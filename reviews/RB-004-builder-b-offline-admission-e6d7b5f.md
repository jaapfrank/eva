# RB-004 — onafhankelijke review van gecorrigeerd bewijs

Besluit: **GO**

Taak: `RB-004` — kandidaat op bouwer B maken.

- Implementatiecommit: `f89d3d72c01fa8d7917d44a5a79395f0301393a9`
- Gecorrigeerde evidencecommit: `e6d7b5fb838831ff8a589b241f4050523c333278`
- Verzegelde productbron: `45d2ff6d61793809a514c01b3a3c000ce1632da6`
- Release-authority-SHA-256: `20323cc166d5b7055c43dc94a0d5e09c47ab729036e479c166768a2a6e841020`

## Bevindingen

De evidencecommit is het directe kind van de implementatiecommit en voegt uitsluitend het publieke RB-004-bewijs toe. De bewaarde private preflight-, build-, postflight- en admissionreceipts zijn opnieuw byte-voor-byte gehasht. Hun hashes, broncommit, authority, run-ID, observation challenge, build challenge, admission challenge, builderidentiteit en kandidaatbinding sluiten op het publieke bewijs.

De B-preflight toont nul Docker-images, nul containers, nul containerd-images, BuildKit-gebruik van `0B`, alleen een `DOWN` loopbackinterface en geen routes of daemonproxy. De postflight toont één ongetagde kandidaat, geen geladen verzegelde basisimages, geen containers en nog steeds geen routes. De builderidentiteit is vóór en na de build gelijk.

Bouwer B verschilt van de toegelaten bouwer A in daemon-ID, Docker-socket, state-root, BuildKit-node-ID, containerd-socket en beide containerd-namespaces. De verifier weigert hergebruik en splice-aanvallen via afzonderlijke receipt-hashes, fysieke paden, challenges, bron/authority en builderidentiteiten. Een handmatige heruitvoering van `verify_single_builder_b` accepteerde de bewaarde keten en het archief met SHA-256 `108406414bf56d0d99c436a19387c39902605ab8df0820e304ac1e486a459acc`.

De eerdere te sterke claims zijn correct ingetrokken. Het aantal fysieke `docker buildx`-aanroepen, de opruiming van de eerdere mislukte readinesspoging en de uiteindelijke resource-teardown zijn expliciet onbekend of niet geclaimd. RB-004 vereist letterlijk dat de verse, offline bouwer één kandidaat produceert met eigen gebonden bewijs; het vereist geen onafhankelijk gemeten exact-één-aanroepteller. Er is voor deze correctie geen nieuwe build geclaimd of nodig.

RB-005 moet de twee kandidaten nog inhoudelijk vergelijken. Deze GO claimt geen scan, signing, deployment, promotie, hostonafhankelijkheid of cleanupbewijs.

## Herhaalde controles

- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp pytest -q tests/operations/test_two_builder_reproducibility.py` — **34 passed**
- `ruff check tools/check_two_builder_reproducibility.py tests/operations/test_two_builder_reproducibility.py` — **All checks passed**

Daarmee zijn de letterlijke `doneWhen`-voorwaarden van RB-004 vervuld.
