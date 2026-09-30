# Gerichte GPU-versnelling van geheugenraadpleging

Het optionele `hindsight-cuda.compose.yml` versnelt de bestaande native
MiniLM-rangschikker. De oorspronkelijke Hindsight-image, modelgewichten,
384-dimensionale CPU-embeddings en database blijven behouden. De CUDA-bundel
bevat Torch 2.10.0+cu128 en zijn dependencies; de bundel wordt alleen-lezen
gekoppeld. Het manifest controleert de afzonderlijke bestanden.

De profielvariabelen zijn `EVA_HINDSIGHT_CUDA_BUNDLE` en
`EVA_HINDSIGHT_CUDA_BUNDLE_SHA256`. Gebruik uitsluitend de geteste,
root-eigendom bundel uit het bijbehorende productbewijs. Native Docker CDI
moet het apparaat `nvidia.com/gpu=all` aanbieden. Op deze installatie is dat
één bestaande GPU; de NVIDIA-base-toolkit maakt de CDI-specificatie zonder
Docker-daemonconfiguratie of een Docker-herstart te wijzigen.

Activeer alleen Hindsight na controle van lopend geheugenwerk. Integreer het
profiel in het daadwerkelijk gebruikte Compose-bestand of de volledige
Compose-bestandslijst, ook voor de opstartroute. Een toekomstige core-cutover
moet dit profiel expliciet overnemen; alleen de image-ID kopiëren bewaart de
CUDA-bundel niet. Behoud alle bestaande database- en credentialvolumes.

De limieten zijn vier CPU's en acht GiB host-RAM zonder extra swap. Dit is
geen VRAM-limiet. Embeddings blijven expliciet op CPU en de rangschikker
gebruikt FP32. Er is geen automatische CPU-herhaling bij GPU-predictfalen.
Controleer na activatie de native device-readback, Hindsight-health en de
geauthenticeerde core-/voice-readiness vóór een echte spraakproef.

De terugweg herstelt de vooraf opgeslagen Compose-bytes en maakt uitsluitend
de Hindsight-container opnieuw aan. Herstel geen databasekopie: nieuwe
herinneringen moeten behouden blijven. Het productbewijs verwijst naar het
CAS-beveiligde terugrolscript, de Compose- en omgevingsbestandshashes, gerenderde serviceconfiguratie,
vaste interpolatieomgeving en de uitgevoerde proef op
een afzonderlijke databaseclone. Gebruik dat script niet blind na een latere
Compose-wijziging; het weigert dan terecht de nieuwe configuratie te overschrijven.

[Productbewijs](../../evidence/product/voice-memory-cuda-2026-09-11.json).

## Native reranker batching — 30 September 2026

The optional `hindsight-reranker-batching.compose.yml` enables the existing
length-sorted batches of eight. Include this overlay in the complete active
Compose file list, including any later Hindsight activation. It changes only
`HINDSIGHT_API_RERANKER_LOCAL_BUCKET_BATCHING` and
`HINDSIGHT_API_RERANKER_LOCAL_BATCH_SIZE`; preserve the existing image, CUDA
bundle, device selection, memory database and access scopes. The existing
activation receipt records its exact command and hash. The prior materialized
base remains available for recovery; no database restore is involved.

The fixed warm CPU comparison preserved all 32 candidate positions for three
queries and improved median reranking by 35–37%. GPU/concurrent throughput is
unmeasured. A successful native contact after activation took 5.039 seconds to
first content; this does not establish completion of C1 or actual Telegram
network latency. See [measurement](../../evidence/product/c1-context-latency-20260930.json).

The same overlay now sets `OMP_NUM_THREADS` and `MKL_NUM_THREADS` to four
within the unchanged four-CPU cap. These variables affect embeddings too.
The separate frozen full-recall comparison therefore includes embeddings and
two concurrent API recalls: median 3350→2878ms with identical ordered facts
and source-fact hashes in every run. A fresh native greeting took 4.084 seconds
to first content and 5.008 seconds to saved completion. Provider timing also
varied; these observations do not attribute all conversation gain to threads
or establish instant response or actual Telegram transport performance.
