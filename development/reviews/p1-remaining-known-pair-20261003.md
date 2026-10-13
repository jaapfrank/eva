# Remaining known-pair samples: bounded independent admission

Decision: **GO for the six fixed development entries below, once each, in this order.** This is source and execution-wiring admission, not an outcome, semantic score, full participant admission or final EV-003 acceptance. Reviewed on 3 October 2026; no tests, provider requests or model calls were performed by the reviewer.

The manifest is `754eb7cde0712b75493a31762e28d1ff993edc3f3516ffaf0ef8362c986171a8`. The corrected Inspect driver is `388fe2c573f1e027bde1741df2ebf520491af609a4024eec1ae83a12bb48f38f`. Both are under the private artifact directory `p1-remaining-fixed-pair-20261003-v1`.

| Order | Entry | Launcher SHA-256 |
| --- | --- | --- |
| 1 | eva2:C04:epoch1 | `f69681ee623b9a942745a79aae2e75ae5cc06bff5709cb9ab80b9da79f916a76` |
| 2 | eva1-bottie:C04:epoch1 | `85760d78c96da4ac126b44d28e4e82205fa3fd5f78dafcab15a7fec659b6b435` |
| 3 | eva1-bottie:C01:epoch2 | `dfaf42298fe5223e296204abd96125604b5f338066dc4e15f6bdb386a456fb50` |
| 4 | eva2:C01:epoch2 | `789feb8a2645df1b39eea97f584ca94463cf914cb6c67cad8980ab7c4b6256dd` |
| 5 | eva1-bottie:C04:epoch2 | `4a3788c73fad48d9837ef956bdcc7db6b5157cec4d736e435ad68540f6c65cef` |
| 6 | eva2:C04:epoch2 | `74665047859727cb562a3ae11e90b393742a0fb0682f555656183a82b383c31e` |

The reviewed parameter diffs reuse the already executed native routes. Bottie changes only the fixed case/epoch and isolated run, container and network identifiers. Eva2 additionally uses the already demonstrated native lease owner UID 1000:65532 for its workers and schema setup, valid 12-hex request IDs, fresh databases and separate memory namespaces. Its budget copies retain SHA-256 `d3d2dfcee5c3e6fcab099f04bfe300349640c1695389f30ade762742bfa3f6a5`. All six launcher hashes and the manifest's seven worker/accounting/tunnel source hashes were rechecked against current bytes. The Bottie group manifest remains `9c7bd62c7afdfcbc7eb40d536c3a2f6e90a15e7bdfcb212d4ba88c3bbd513457`; Eva2 preparation remains `8fea3488fa6b6004710e53c330bd6c4ec318ebe5cccf157a7a22d533b5285d1c`.

Models, native variants, image pins, read-only access leases, endpoint-only network route, effect guards and existing accounting ownership are unchanged. The previously reviewed per-sample limits remain two provider attempts, 24,000 input tokens, 4,096 output tokens, 120 seconds, four tool dispatches and zero external actions. Inspect runs sequentially with `max_samples=1` and zero retries. The two observed first C01 samples remain counted: four attempts, 14,059 input tokens and 443 output tokens. No unused participant budget is redistributed.

The initial driver review found three concrete continuation gaps: checking complete usage without the authoritative stop reason, permitting an unsuccessful Bottie exchange without verified readback/ACK, and omitting attempts from an incomplete-usage aggregate. The corrected driver resolves those findings. It accepts only the explicit normal stop reasons or the three declared attempt/token/wall envelope reasons; Eva2 also requires a recognized native outcome and independent reply integrity. A normal Bottie result requires verified readback, one ACK and native-tail completion. Its separate known-envelope branch still requires complete accounting and a settled native worker; a failed readback cannot pass. Unknown usage, other enforcement stops, stop/cleanup errors, missing receipts and technical failures halt the remaining sequence. Known attempt counts and partial token lower bounds are retained while incomplete exact totals become unknown.

A completely accounted local envelope refusal remains an incomplete/failed sample outcome; it does not itself prohibit the next predeclared entry. This does not upgrade Eva2's existing pending consolidation to completed memory work. No retry or budget change is admitted. Bottie's proven completion scope remains native `startWeb` and the awaited ACK, not service schedulers. Buffered Bottie HTTP timing and Eva2 streamed first-content timing remain distinct; costs of incomplete and complete native samples must not be presented as equivalent completed-work costs.

Existing focused evidence and the first pair's result reviews are reused. Unavailable OpenClaw, Hermes and Agent Zero entries remain unexecuted/unknown, never losses. Semantic scores and both human ratings remain unset; no AI judge is introduced. These known C01/C04 development measurements do not replace the independent unseen final comparison or establish superiority. There is no remaining concrete source blocker within this six-entry scope.

## Pre-input setup continuation

Decision: **GO for the same six entries through the corrected preparation**, with no model retry or quota redistribution. The first batch stopped at host-side access-only lease inspection, before ledger, controller, network, container or request creation. Its retained failure receipt is `bef7d1b2c7aeee782ddcd88cfadc865a5183d2239b77554bd4b5f76b74088be2`; commands, worker receipts and created/retained resources are empty. Request and parent accounting are absent. Source ordering establishes zero provider calls. The receipt's generic missing-readback/worker cleanup blockers do not imply that those resources or calls existed. This remains a recorded technical preparation failure, not a sample result or success.

The resumed group manifest is `89f1273c231f8ff596b5d4bcc7f9e92eaa87e7092f3a0dafa58bc182f4807439`; driver is `ce32d4243610765dceb8305b4f9396d2f2fe42b6b58a5732582cb6ef4d7ce6fd`. Relative to the admitted version, the driver only changes stdout/stderr and Eva2 result paths, preserving the previous observations. The three Eva2 launchers now capture the existing local auth inspection using `sudo -n cat`, retain only its access-only check, and delete the parsed object; credentials are neither printed nor copied into artifacts. Native UID 1000:65532 and read-only lease mounts are unchanged. New results require a fresh `execution` directory; the existing named socket directory is reused only if empty. All three diffs contain only these preparation/output changes.

The corrected Eva2 launcher hashes are:

- C04 epoch 1: `d0e2c92edc95179f5058917b2344aaaa5b3adf579b93e06e9620b8b69ee0b019`.
- C01 epoch 2: `1ea4730d2d0a99a33cbb2ed8bf3b796562fbf81a29e2fb151ec1e4208466bea0`.
- C04 epoch 2: `166a1e87710ffe8a5c3b5622e376331d9fe109ead39bc6fe899df56f8c6a18c3`.

The original launchers remain preserved as `run.before-input-fix.py` with their previously admitted hashes. Bottie launchers, entry order, source pins, budgets and continuation stop rules are unchanged. No authentication values, tests or provider calls were read or executed by the reviewer. All preceding scope and outcome limitations continue to apply.
