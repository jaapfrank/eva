# Coalesced explicit inference GC

This narrow overlay targets the pinned `local_device.py` in the installed
Hindsight image. `patch.py` rejects different upstream bytes; review a new
upstream version before changing the digest. No model weights, recall filters,
ranking algorithm, database indexes or source data change.

With `EVA_HINDSIGHT_COALESCE_GC=1`, explicit Python collections in the shared
embedding/reranking helper are serialized. Full collection runs on the first
call and at least one second after the previous full collection **completed**;
intervening calls collect generation zero. Automatic Python GC remains enabled.
Native heap trim and GPU allocator cache release still run after every call.
With the variable absent or any other value, upstream behavior is retained.

This interval does not bound retained cyclic-object volume. There is no new
timer or background thread; a last batch followed by idle may retain older
cycles until another collection. RSS and GPU stability must be measured with
the actual workload. Finite tests do not establish long-term leak freedom.

## Build and activate

1. Copy `/app/api/hindsight_api/engine/local_device.py` from the actually running
   Hindsight container into a private build directory. Run `python3 patch.py`
   with that file path. The hash check fails closed.
2. Build a one-file overlay on that exact installed image, retaining its user,
   entrypoint and all other files. Copy the patched file to the same path.
3. Run `python3 -m unittest discover -s deploy/living-core/hindsight-gc` and the
   varied-load component comparison. Compare real recall before/after too;
   the shared helper also affects CPU embeddings.
4. Check that voice, delegated work and memory background work are idle. Back
   up the actual materialized Compose configuration privately. Change only the
   Hindsight image and opt-in variable, preserving volumes, GPU bundle/devices,
   credentials, CPU/RAM limits and all other services. Recreate only Hindsight.
5. Verify health, authenticated recall, ordered result equality and timings.
   Roll back on regression. Restore only the original image/configuration after
   checking that no concurrent configuration changes would be overwritten;
   **never restore the memory database**.

Installation receipts and private rollback scripts are kept with the voice
measurement receipts. Product evidence records the measured outcome separately
from physical speaker or complete-conversation latency.

## Warm local inference before readiness

`warmup_patch.py` separately pins the installed `memory_engine.py`. With
`EVA_HINDSIGHT_WARMUP_RERANKER=1`, it runs one eight-pair synthetic prediction
on the existing executor after **all** model-load tasks have joined and the
existing PyTorch default-dtype normalization has completed. It precedes engine
readiness. Do not move this into `cross_encoder.initialize()`: the parallel
embedding loader can still be changing the process-global dtype there.

Only `LocalSTCrossEncoder` is warmed; remote providers and default-off behavior
are unchanged. No memory bank, private content or answer model is consulted.
Failures prevent readiness, and retries run the warmup again. Its wait uses the
configured model-init timeout. A timed-out Python wait cannot forcibly stop a
native GPU worker; keep the external activation deadline and rollback.

Build this second one-file overlay on the verified GC image, retaining the
original `cross_encoder.py`. The first measured real recall after activation
must be checked as well as warm recalls. This moves work into startup; it does
not promise a shorter overall boot or eliminate every cold-start cost.

## Reuse exact local reranker computations

`rerank_cache_patch.py` pins the installed `cross_encoder.py` and embeds the
adjacent `rerank_score_cache.py` helper. It is disabled unless
`EVA_HINDSIGHT_EXACT_RERANK_CACHE=1`. Keep both patch files together during build;
copy only the resulting upstream module into the image. Existing GC and warmup
patches are independent and remain unchanged.

This is arithmetic reuse, not a recall-result cache. Every recall still performs
fresh retrieval, ranking aggregation and the existing host source/authority
validation. Each local model instance retains at most 64 salted hashes of whole,
ordered input batches and immutable numeric scores. A hit expires for use after
30 seconds; expiry cleanup is lazy, on the next eligible call. No query or document
strings are retained in the cache. Entries contain at most 256 scores; batches
larger than 2 MiB of UTF-8 input bypass caching. Changed query/document/order or
model instance, device, FP16, batch/bucketing or PyTorch thread configuration
causes a miss. The pinned inference model/configuration must otherwise remain
immutable; this is not a hot model-weight replacement facility.

Misses run the original inference method unchanged, including its existing
cleanup. Cache locks never cover inference, preserving the existing bounded
worker concurrency. Concurrent identical misses may both compute. Returned
score lists cannot mutate retained values. Restarting the process clears all
entries; no database, disk cache, new provider or background service is added.

Build against the exact currently installed Hindsight image, using:

```sh
python3 deploy/living-core/hindsight-gc/rerank_cache_patch.py /private/build/cross_encoder.py
EVA_RERANK_UPSTREAM_SOURCE=/private/original/cross_encoder.py \
  python3 deploy/living-core/hindsight-gc/test_rerank_score_cache.py
```

The source variable enables seam tests against the actual pinned class, including
unchanged uncached inference and default-off dispatch. Without it, that test is
explicitly skipped. Before installation, compare actual full candidate batches
in an isolated current-image container with the same read-only weights, CPU/RAM
limits and model configuration. Preserve all existing service configuration;
only the verified image and explicit opt-in change. Apply the idle checks and
rollback rules above, then verify full fresh recall/source validation after
installation. Disabling the opt-in or restoring the prior image removes reuse
without changing memory data.

The measured example had identical 32-pair inputs on two real traces; isolated
scores were exactly equal, with roughly 1.9–2.1 seconds for inference versus
0.44 milliseconds for a hit. This proves one repeat case, not general hit rate
or end-to-end voice latency. First calls and changed batches have no inference
saving. Keep original ordering; do not sort inputs to manufacture hits.
