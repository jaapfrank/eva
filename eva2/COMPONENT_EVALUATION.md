# Isolated component evaluation

The digital worker can read installed source, record an exact replacement draft,
and request an isolated comparison using `evaluate_own_component_improvement`.
Its `draft_step` points to the earlier durable draft in the same execution scope.
The action rechecks the installed source hash before dispatch. The evaluator
checks the baseline image source hash as well; it never silently rebases a patch.

The host service runs one operator-selected command on the original image and on
an exact-path readonly candidate mount. The model cannot choose a command, image,
network, mount or resource limit. The service has Docker access; Eva's core and
worker do not gain Docker access. Candidate containers have no network, no
production data or credentials, a readonly root, bounded temporary storage,
nonroot execution, dropped capabilities and bounded CPU, memory, processes,
output and execution time. Use only an image without baked-in secrets or data.

## Configuration

Run `python -m eva.operations.component_evaluation_service --config FILE` on the
Docker host. Required JSON fields are `image` (prefer the local immutable image
ID), `command` (fixed argv), `token_file`, numeric IPv4 `bind_host`, `port` and
`allowed_paths` relative to the installed `eva` package. Optional resource fields
are those of `EvaluationConfig`. Bind to the existing internal Docker bridge.
The service uses a bearer token, rejects redirects at the client and permits one
active execution. Graceful service stop waits for bounded container cleanup.

Both core and worker receive `EVA_COMPONENT_EVALUATION_CLIENT_FILE`, pointing to
JSON with `url`, `token_file`, `allowed_paths`, `suite_sha256`, and
`timeout_seconds`. Mount the config and credential readonly. The suite binding is
SHA-256 of compact JSON `[image_id, command_sha256]`; command SHA-256 is the hash
of compact JSON argv. These settings and client source participate in the
existing digital release fingerprint. Changing capabilities can prevent an older
work grant from being resumed; no grant is expanded silently.

## Installed initial suite

The first suite reuses the existing timestamp, canonical JSON and cancellation
checks. It accepts `contracts/timestamps.py`, `contracts/canonical_json.py`, and
`application/cancellation.py`. It does not claim coverage for other components.
To add a component, select meaningful existing checks, build a new isolated image
and update both service and client binding together. Do not let a candidate
supply its own success criterion.

The isolated image reuses the already installed pytest 8.4.2 (MIT), pluggy 1.6.0
(MIT), iniconfig 2.3.0 (MIT), packaging 26.3 (Apache-2.0 OR BSD-2-Clause), and
Pygments 2.17.2 (BSD-2-Clause), including distribution metadata and licenses.
No new package download or version resolution is involved. Versions and license
metadata were read locally on 2026-09-24; this is not a vulnerability or current
upstream maintenance assessment. Reassess through the existing dependency
maintenance process when upgrading this suite; the coordinator owns that check.

## Meaning of a report

Reports bind the source, candidate, pinned image and fixed command. They contain
exit codes, timeout flags, bounded stdout/stderr and elapsed milliseconds.
Elapsed time includes container startup and is not a component benchmark.
A failed baseline remains visible. A zero exit code proves only that the selected
command returned zero; candidate code can affect its own process. The report
always leaves semantic improvement unverified and installation false.

The digital action records the report in the task timeline and instructs Eva to
save it with the existing document tool. A task cancellation prevents further
owner results/actions; an isolated check already running may finish within its
configured bound. No candidate changes running Eva automatically. Evidence of
actual benefit, independent review, a rollback and product installation remain
required before an own improvement is accepted. Broad evaluation and owner
trials remain separate from the installation startup check.
