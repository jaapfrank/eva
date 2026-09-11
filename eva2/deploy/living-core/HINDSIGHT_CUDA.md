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
