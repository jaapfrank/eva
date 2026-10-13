# Eva2 sealed memory probe

This directory is an isolated implementation of the memory subtrial sealed in
`docs/eva2-architecture-bakeoff-v1.md`.  It does not import Eva1, use a product
database, or start a product service.  Runtime databases, container mounts, and
Docker networks are uniquely named children of `/tmp/docker` and are removed by
the runners.

## Reproduce

Prerequisites are Python 3.12 with SQLite FTS5 and, only for Hindsight, Docker.

```sh
python3 run_sql_probe.py --output-dir evidence
python3 run_hindsight_probe.py --output-dir evidence --scales 10000
python3 -m py_compile canonical_memory.py hindsight_adapter.py run_sql_probe.py run_hindsight_probe.py
ruff check canonical_memory.py hindsight_adapter.py run_sql_probe.py run_hindsight_probe.py
```

`run_sql_probe.py` always runs the sealed 10,000 and 100,000-event scales in
fresh subprocesses.  `run_hindsight_probe.py` uses the immutable official
Hindsight 0.9.1 image manifest and its account-free
`HINDSIGHT_API_LLM_PROVIDER=none` route.  This is a real API/storage/retrieval
probe, but it is only infrastructure evidence: chunks mode performs no LLM
fact or temporal extraction, so semantic memory quality is `not_proven`.

Hindsight receives every canonical fact.  High-volume archive facts are JSONL
bundled (one full canonical row per line) to avoid pretending that document
count equals event count.  The evidence records both counts.  Candidate recalls
are only ranked event IDs; the mandatory SQL authority barrier enforces tenant,
subject, slot, validity and trust, then attaches source ID, connector, source
hash, observation time, validity interval, supersession/retraction and trust to
every returned fact.

The SQL runner emits content-addressed JSON under `evidence/`.  It covers
now/then temporal correction, same-name cross-tenant canaries, untrusted-email
prompt injection versus direct correction, an accepted commit followed by exit
86 before readback, retract/erase/byte-level compaction, projection destruction
and rebuild, SQL/FTS fallback, p95 latency, tokens, RAM, storage and writes.

`candidate-blockers.json` records the bounded Hindsight-100k stop and the exact
official-source Graphiti blocker inspection.  Missing runs remain
`not_proven`; neither blocker is a winner or an impossibility claim.
