# P1 native Hindsight lifecycle — independent review, 3 October 2026

Decision: **GO for the isolated native retain/consolidation accounting proof.** No blocking finding remains within this bounded change. This is not foreground-plus-memory sample admission, a live comparison or P1 completion. The reviewer read code and existing receipts; no probe, test, model, provider or auth flow was executed by the reviewer.

The frozen probe uses the installed `MemoryEngine`, `WorkerPoller` and `CodexLLM`, real temporary PostgreSQL storage and existing public model resources. Constructor interception binds the Codex clients created inside this engine to the same existing `ProviderSampleLedger`; the result records four provider instances. Only authentication and provider HTTP responses are synthetic. The wrapper observes the actual call schema/scope and delegates to the original native method. Synthetic responses validate against that native schema; consolidation derives its source IDs from independently persisted facts rather than inventing references. The raw SSE attestor and HTTP accounting components match the earlier reviewed hashes. Their nine checks were reused, not repeated.

Prelaunch review corrected an overly broad completion predicate: retained memory alone was insufficient. Completion now requires the returned world fact, an observation linked to it, a completed consolidation operation, no remaining pending/running operations, and settled tracked work. It also refuses an existing output path before starting. Installed worker shutdown source was inspected: shutdown can cancel and requeue work, so a finished poller alone is insufficient. The corrected probe reads native in-flight and unfinished task counts after shutdown and checks database state again.

One execution completed with exit 0. Before and after shutdown, independent SQL readback contains the same single world fact, observation whose source ID is exactly that fact, and completed consolidation operation. Both readbacks show zero pending/running operations. Post-shutdown worker in-flight/unfinished tasks, ledger active attempts and tracked responses are all zero; no cleanup errors are recorded. This supports quiescence of the observed provider responses and worker jobs, not an unrestricted claim about every possible native subsystem.

Two completed synthetic HTTP attempts are recorded: `retain_extract_facts` and `consolidation`, both `gpt-6-astra` with `medium` effort. Raw fixture usage is 62 input / 14 output / 76 total tokens. There were zero upstream model calls. Local model resources were loaded; these synthetic token values are not billed usage or quality evidence. Probe elapsed time was 13.45209074500599 seconds, including 12.412414697988424 seconds of preparation; launcher elapsed time was 24.410865357000148 seconds. These are offline fixture timings, not provider latency or a speed advantage.

The existing two-attempt envelope was unchanged and fully consumed by this nonempty retain fixture. That neither admits a combined foreground/helper sample nor proves that every Eva2 case is impossible within the envelope. Recall, reflect, complete foreground integration, live provider behavior and full sample coverage remain separate. Native char/4 estimates are not substituted for measured raw usage. Stop assistance remains comparison-side control.

Isolation evidence binds a fresh tmpfs PostgreSQL 18.6/vector 0.8.6 database with zero initial public tables, on a dedicated internal network. Only two public model cache repositories were copied (226,325,451 bytes); production memory and auth were not mounted. The probe used a read-only root filesystem, dropped capabilities, fixture UID 0 and read-only code/dependencies/models. The initial Docker automatic-subnet allocation failure is retained as preparation history; an explicitly checked unused subnet was selected without changing other networks. There was no lifecycle rerun. Database dump and scoped container/network cleanup remain root-owned infrastructure actions, not additional proof claims in this review.

Bound SHA-256 values:

| Input | SHA-256 |
| --- | --- |
| `p1_hindsight_lifecycle_probe.py` | `263a131e454392198c108e7897d1f6ef69c5f05ac6f932fd536f60a0f9d0e83d` |
| `cp001_accounting.py` | `c57637e389204ba9e816f430da40db073e217bf969b9968cc74f791f4854986f` |
| `cp001_http_accounting.py` | `906ea1bf9ee43ee5892aeb2c1d609447bbaa73698c8dd514cc4269a5bb3c5ff9` |
| Reused attestor loader | `512766ab2dd0e0804062acdbb3b1c962613503b6266b8d78b7326b63762aed1e` |
| Eva attestor source | `efdaf9e083ef15ee77b16a1015b7cfb1ac5cd82538e47840404fa5a290ca843f` |
| Native `CodexLLM` source | `b4cda4b56b3a6cc1f6bca64eaca7cbc7c75b5e8420d6bb77750c0d6304098afe` |
| Native `MemoryEngine` source | `12ec0ef2d30514813c778d05597f0b8f0e8af3fee18d5acfd9eb6e166dd9ab5a` |
| Native `WorkerPoller` source | `f845467c37cee58c21033a195a971a907cf92154172ad5194d5fcc258451cd53` |
| `attempt1/result.json` | `dfd833f61b066266ac21b777e7f6377d47322d090a916eca61f937316018b855` |
| `attempt1/launch.json` | `26d2a46fd74fc2dcc78e580b10f5c311dc75c35e2597ff26b6cf676b8a1826df` |
| `attempt1/exit.json` | `4117c74bd2d032d490532b5d18da8b1b595e42086f62f2df281045b670a69d83` |
| `database-start.json` | `15e13492b36c37b7b51a970657cd8b9ae784b7aadd1d2ee71b9bc6418f2837dd` |
| `public-model-resources.json` | `c838bc38884c3934dd2a08b49fa59e7262f077c90e6972033f211c0db216c73c` |

Receipts are under `.eva2-artifacts/p1-hindsight-lifecycle-20261003-v1`. The native Hindsight image is `sha256:20d72ad34164bd1a67906c4d61b57a51f2b255c49ddf8cb7ac5e901ebc3b3710`; the database image is `sha256:7e641ed0cf38285bebfc6151ba48243439dfe9cf3306feab4023a812466cd6c1`. No additional execution is needed for this bounded decision.
