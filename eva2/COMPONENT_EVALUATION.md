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
existing digital release fingerprint. A resumed task preserves its original capability subset when those tools remain
available and every other grant bound is unchanged. Removing an old tool or
changing budgets remains incompatible; no grant is expanded silently.

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

## Installation candidate handoff

`prepare_own_component_release` refers to an earlier `evaluation_step` in the same
background execution. The host retrieves its recorded draft and report, verifies
that production source has not changed, and requires complete successful baseline
and candidate process results. It packages the exact candidate, patch, original
hash and evaluation hash. The worker saves this package with the existing document
tool; the package itself is also retained in the component action journal.

The package state is `awaiting_measured_gain_review`. It is not a signed product
release or permission to replace production code. Independent evidence of the
claimed benefit, review, an unchanged baseline and a rollback remain required by
the existing managed installation process. There is no automatic promotion based
on exit codes or whole-container timing. Actual beneficial application remains
open until a real reviewed candidate exists.

## Importing an independently reviewed candidate

The operator entry point is `python3 eva2/tools/import_component_release.py`.
It uses the Python standard library and runs on the existing development host;
no runtime service or model tool receives source-write or Docker access.

Provide the saved candidate JSON, an operator review JSON, and the root of an
existing clean `codex/` release checkout. This step is restricted to the three
components covered by the installed evaluation suite. It verifies package,
report, candidate, patch, review-evidence hashes and the current source baseline.
The default command only describes the validated import:

```sh
python3 eva2/tools/import_component_release.py --package /path/candidate.json --review /path/review.json --checkout /path/release-checkout
```

After review, add `--apply --receipt /path/new-import-receipt.json` to write the
exact candidate into that checkout. The receipt must be new and outside the
checkout. The command neither commits nor runs the candidate. Commit the source
and retain the receipt/evidence with the ordinary release inputs. Continue through
the existing managed build and installation, checking the actual runtime baseline
again and retaining the previous image/configuration for rollback. A source import
is not a runtime release, measured gain, or owner acceptance.

The review JSON uses contract `EvaComponentReleaseReview/v1`, the exact
`candidate_package_sha256`, a nonempty `reviewer`, and `decision: "accept"`.
Both `measured_gain_evidence` and `independent_review_evidence` contain an existing
`path` and its exact `sha256`. Relative evidence paths resolve beside the review
file. The operator is responsible for the evidence's relevance and independence;
these file hashes preserve the selected evidence but cannot judge its conclusions.
Do not construct an accepting review merely because process exit codes are zero.

No real accepted candidate has been imported by this delivery. Only source syntax
and the command entry point were checked; import/application trials remain deferred.
