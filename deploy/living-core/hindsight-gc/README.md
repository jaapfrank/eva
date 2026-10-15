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
