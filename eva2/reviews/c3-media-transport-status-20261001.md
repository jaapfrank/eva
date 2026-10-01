# C3 media transport status — independent review

## Source decision

**GO for the bounded source change and its recorded installed delivery.**

Reviewer: `/root/open_questions_triage`, 1 October 2026.

Reviewed the working diff and relevant control flow in:

- `eva2/src/eva/voice_service/agent.py`
- `eva2/tests/voice_service/test_work_receipt.py`

The change removes speech synthesis for `request_acknowledged`. The authenticated
HTTP acceptance event remains available to the UI and is offered once per
unsuppressed message. It does not become conversation history or an audio handle.
Actual `spoken_update` and final answers still use the existing delivery path.
Work registration, message binding, interruption, result replay and explicit stop
handling are unchanged. No new model, provider, personality or routing policy is
introduced. No blocking source regression found.

## Focused evidence

The retained coordinator test log reports **17 passed in 3.55 seconds** using the
existing native-image dependencies with networking disabled. The reviewer read
that log and inspected the tests; no tests or provider requests were rerun.

The cases retain acceptance-before-body and rejected-response checks, require a
duplicate transport receipt to notify only once without audio, allow a second
turn while the first remains pending, preserve the later result, and suppress a
stopped final answer. Explicit `session.interrupt()` in this test is a control-flow
check, not physical speech detection or full-duplex evidence.

Reviewed source snapshots:

| File | SHA256 |
| --- | --- |
| `agent.py` | `2de5e47c993bd2094fcf735e0b3c71e18cda9199e8b5c2debb3864ced6194c53` |
| `test_work_receipt.py` | `31bec31f4db4248672f50c932edec86fe2af9e1ea7931a62d699be4ad7f86e0f` |

## Installed delivery review

Reviewed `eva2/evidence/product/c3-media-transport-status-20261001.json` after
installation. The native module matches the source snapshot above. The recorded
owner-module change applies only the same acknowledgement-branch removal to its
retained older module; unrelated older content is preserved.

| Installed route | Immutable image | Installed module SHA256 |
| --- | --- | --- |
| Native | `sha256:5c403643e6fc0504b2ec90c5e3b4d11fbc709ad320aae5ad9ace86043c001914` | `2de5e47c993bd2094fcf735e0b3c71e18cda9199e8b5c2debb3864ced6194c53` |
| Owner | `sha256:4bd59183eb14cc9f99e402498664ae127b9c588960aaf0f02b8ccf3bd4d48184` | `a99cd54ddc99584f92f07dbe93e961e670d6f28fbe58d11ea36d0dd131b80d58` |

The retained owner-module baseline hash is
`b77050d809440386b0357037bafef26000dda3a7e8cde6f005cf18aafe259259`.
Both packaged candidates passed the same 17 focused cases against their actual
packaged modules, without repository-source shadowing. The evidence records one
replacement/start per route, running containers, preserved runtime and host
configuration and mounts, owner readiness, and readiness/work returns for both
native household bindings. Docker list ordering and empty-DNS-list/null
normalization are explicitly recorded rather than treated as meaningful changes.

Previous immutable images and private configuration/module bytes are retained;
no rollback was performed. These are coordinator installation readbacks reviewed
independently, not a second reviewer deployment or physical audio trial. No
additional checks or provider requests were run for this final review.

Removing a routine spoken receipt does not provide continuous listening, speech
barge-in, lower model latency or an accepted physical speaker experience. C3.1
and the corresponding full-duplex requirements remain open.

No builds, deployments, SSH sessions, provider calls or firmware inspection were
performed by this reviewer. Development credit cost is unknown.
