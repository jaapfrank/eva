# P1 native subscription binding — bounded source review

**Final decision: GO for exactly Eva2 / C01 / epoch 1 under the supplied frozen admission. NOT RUN by the reviewer.** This is permission to attempt one counted development sample, not a successful result or admission of other samples/participants. Existing stop conditions, unknown-usage handling, budgets and human/semantic assessment remain unchanged.

**Earlier checkpoint: preparation only; C01 NOT RUN and NOT ADMITTED.** The initial review stopped here on owner steering; the final bounded decision below supersedes only the Eva2 admission hold. No comparison/model/provider call, credential-value read, reviewer test or deployment was performed. Earlier settled accounting evidence remains unchanged. This record is not P1 completion or a quality/superiority result.

The reviewed Eva2 worker retains the native credential loader and Hindsight authentication, parser and client options. Subscription mode replaces only the observed transport; synthetic mode remains the default. The existing shared parent ledger checks model/effort and the unchanged two-attempt envelope before HTTP dispatch. Substantive actions are refused at the actual `execute`/`execute_step` methods. The disposable launcher uses fresh databases, read-only access-only credential mounts, the existing pinned core/Hindsight images and an internal network. The CONNECT relay restricts host/port to `chatgpt.com:443`; the accounting transport separately checks the exact Responses URL. TLS remains native end to end.

The unexecuted launcher retains the frozen C01 stimulus and limits, native NDJSON timing, independent durable reply readback, memory completion checks and explicit child/parent settlement. The Inspect wrapper invokes it once with no retry or AI judge and leaves semantic/human scores unknown. The initial wall-stop classification error was corrected: response and memory paths now classify the first authoritative stop reason while retaining exceptions. Timeout classification does not make missing usage complete or allow later samples.

Findings at the earlier checkpoint (Eva2 resolved below; Bottie remains separate):

- **Eva2:** finish and review the transparent observation of Hindsight's actual accepted `structured_response`, then bind final source/launcher/image/admission hashes. A requested schema alone is not a tool dispatch. Installed `CodexLLM._parse_sse_tool_stream` is async at line978; `call` lines531–534 select the first matching name with dictionary arguments, then consume/validate it at548–549. Preserve the original returned objects and first-match rule. One such consumption per HTTP attempt gives at most two under the existing shared cap, below the unchanged four-tool limit. No additional counting authority is necessary.
- **Core concern resolved without edits:** the exact Pydantic conversation route uses `output_type=[str, DeferredToolRequests]`, so no final-answer output tool exists there. Deferred substantive actions reach the existing guard. The initial concern about relying on native `action_call` counts does not justify adding a generic SDK hook.
- **Bottie, separately unadmitted:** subscription stream cancellation currently swallows close failures and can remove ownership before successful closure; non-OK bodies also need explicit close. Preserve failed-close/error state rather than report false quiescence. Reject observed cancelled/error and malformed/non-completed terminal events, as well as existing missing/duplicate failures. These points were sent to the implementer and do not imply an Eva2 defect.

All 87 disposable Bottie source hashes matched its manifest. The three added hook files delegate access-only authentication, bind request roles and guard actual tool dispatch. Original installed Bottie remains unchanged. Native authenticated request construction is retained, but refresh is deliberately refused; the entire OAuth lifecycle is therefore not claimed unchanged. The prior explicit equal-model patch remains a separately declared variant.

The reported focused offline checks were reused, not rerun. No live cancellation, streaming latency neutrality, full memory/sample admission or semantic success is certified. Any later source changes require only review of their concrete delta, not another broad audit.

## Reviewed checkpoint hashes

| Input | SHA-256 |
|---|---|
| `p1_eva2_native_worker.py` | `67180e48166b57258c1d29d4dbe3ee35acfb0a49bd233a9cc592f7b1475c4cf8` |
| `p1_model_tunnel.py` | `12bd58796a92eb09096dab3a484ed3aeb6d5c8f1724fccdc2827ffa3f269bb08` |
| `test_p1_subscription_binding.py` | `551b7f66907b87a5cd6c623e6c3bb934c74fe6bbde70491531abca805e13acf1` |
| Private C01 `run.py`, corrected stop classifier | `044d7683d3514b495d230df5d7d7d34fbc6366334efd7f723ffe0a7b26eba8dc` |
| Private C01 `inspect-run.py` | `516b875661607ce148d2cc04a10354e51279eca6f4d08453a744da4eedad9dda` |
| Participant manifest at read | `5b117a0d320b0a8b62ce71f3a1fb5be8e76c2d6e8fdff48726df3aa547b43f7e` |
| `cp001_bottie_worker.mjs` | `dea9b34078cbb4fa14672ac2231cbb00cdd9d5f2c56b4a10a9bbd6c0a00d5ff9` |
| Disposable Bottie `hook-manifest.json` | `a9962e86decc187588292beefc4968fd348b90ba094dbb96da080c572453169b` |
| Disposable Bottie `native-hooks.patch` | `d72c0f9afb78081fc3b2e103a9c962e3f322a7b775ea83cdfdf7a22313bd8500` |
| Installed Hindsight `codex_llm.py` | `b4cda4b56b3a6cc1f6bca64eaca7cbc7c75b5e8420d6bb77750c0d6304098afe` |

Private C01 inputs: `/home/administrator/project/.eva2-artifacts/p1-eva2-c01-subscription-20261003-v1/`. Disposable Bottie inputs: `/home/administrator/project/.eva2-artifacts/p1-bottie-native-subscription-20261003-v1/`. These hashes identify the reviewed checkpoint, not an assertion that later unfinished edits were reviewed or admitted.

## Final narrow correction and admission

The final `observe_memory_protocol` mirrors the installed native first-match rule exactly: it records one `structured_response` only for the first matching call with dictionary arguments, then returns the same parsed tuple. The async wrapper awaits the original parser and does not modify native content, arguments, schema validation or retries. A rejected or absent envelope is not counted as accepted. The launcher reports actual protocol consumptions separately from refused actions and receipt completeness. On this exact path, core has no final-answer output tool and Hindsight consumes at most one envelope per provider attempt; the unchanged shared two-attempt limit therefore bounds protocol consumptions to two, below the existing four-tool limit. No new IPC authority or raised limit was introduced. The focused first-match check was reported passed and was not rerun.

Final read bindings:

- Worker SHA-256: `71af2d030d9a30fae4d27483dbccbbafbd38efbcd84971bd9f249741f4f3768e`.
- Launcher SHA-256: `93e556b0b93b0bbfc3d70a7eb0170fa785d74a4f74e8cf1f47b97c9e19293c93`.
- Admission JSON SHA-256 at review: `2edb22c00bbd3e8f6c9d7cb3e00d5fe969a4159e8d7550b7f1b354350b05cc41` (its status was pending this final count-hook review).
- Inspect wrapper remains `516b875661607ce148d2cc04a10354e51279eca6f4d08453a744da4eedad9dda`; unchanged ledger/IPC/HTTP observer/tunnel bindings reuse the preceding reviews.
- Core image: `sha256:fd1c7cd3413405fd6b1b5ce408f2028cffed8de735a301550663e4c891ee5902`.
- Hindsight image: `sha256:20d72ad34164bd1a67906c4d61b57a51f2b255c49ddf8cb7ac5e901ebc3b3710`.
- Database image: `sha256:7e641ed0cf38285bebfc6151ba48243439dfe9cf3306feab4023a812466cd6c1`.

These corrections resolve the remaining Eva2 source blocker. This decision does not resolve the independent Bottie findings, certify live usage or cloud cancellation, score C01, complete P1, or authorize another attempt after a failed/incomplete sample. No credentials, other participants or prior settled paths were reinspected for this final delta review.

## Pre-input setup continuation

**GO for the exact setup continuation, still only Eva2 C01 / epoch 1.** The original launch failed before core startup/request creation when cap-dropped root could not read the native mode-0600 access-only credential file. The retained failure is not relabelled as a completed sample or erased. The continuation asserts the previous parent provider-attempt count is zero and no request was recorded, then checks zero core events and no public memory tables before starting native services. It reuses the existing labelled internal network, database and installed core schema; it does not repeat migration or create a replacement dataset.

The reviewed delta runs both disposable native services as `1000:65532`, changes ownership only on newly created fixture IPC/output and generated control/DSN/budget files, and preserves read-only credential mounts and their existing permissions. Fresh IPC, output and memory-container names avoid overwriting the failed setup evidence. The Inspect continuation uses the same case, epoch, limits and zero-retry setting, with distinct output/log paths. No model budget, provider source, stimulus, parser, scoring rule or native product behavior changes. The temporary `schema` command is constructed but no longer executed; its previous successful receipt is reused.

- `resume.py` SHA-256: `34c6f5811cc52b90c37fbd1067f259099da17c52d1132c77a7766cd24b16834b`.
- `inspect-resume.py` SHA-256: `21e30c5dff30d3a8168ead1caca4144864d510a0fad7ac8e9903bcc45ab0bd0a`.
- Original launcher remains `93e556b0b93b0bbfc3d70a7eb0170fa785d74a4f74e8cf1f47b97c9e19293c93`.

This approves a necessary pre-input permission correction, not a retry after inference or permission for another sample. Review was limited to the launcher/Inspect delta; no auth values, tests or provider calls were read/run. All original stop/accounting requirements still apply to the first actual C01 execution.

## Recorded C01 execution — accounting and stop assessment

**Accepted as a recorded envelope refusal with complete observed provider usage and settled local execution. The full native sample did not complete.** One actual C01 request ran. The earlier permission failure and subsequent invalid 13-character run-ID setup failure both retain zero provider attempts and no request; their original receipts/logs remain evidence. The latter was corrected to the existing 12-character request contract before input, without repeating native inference or changing the sample budget.

The authoritative parent ledger records exactly two completed HTTP attempts: contact `gpt-6-sol` / `none`, 1,866 input + 74 output tokens; helper `gpt-6-astra` / `medium`, 3,008 input + 97 output. Total **4,874 input + 171 output = 5,045 tokens**. The next attempt was refused before dispatch with the exact first stop reason `declared sample attempts reached`. Both native child dispatch counts agree; the relay records two authorized connections. Unknown monetary cost remains unknown. Ledger `complete:true` describes known usage, not completed memory work.

Recorded first nonempty native content was **2.970333628007211 s** after ingress; foreground completion was **5.123795424995478 s**. The final reply packet arrived at 5.1234797530050855 s. Sample observation ended at 9.5058769750176 s with `sample_envelope_refused`; this is not a successful verified-finish time or a comparative speed claim. Independent readback contains exactly one matching durable reply; its text hash, message, conversation and synthetic principal match the returned reply. No semantic or personality score was assigned.

Native retention stored one fact and its retain operations completed. Consolidation changed from processing to pending during shutdown. Therefore neither completed memory processing nor full-sample success is established. Protocol consumptions, substantive attempted actions and external actions dispatched are all recorded as zero; normal journal/memory writes did occur. Both workers report zero active HTTP responses, no active native workers, successful control-close acknowledgements and no errors. Parent workers are disconnected, terminal/settled, with zero unsettled events and no cancellation errors. The relay closed. This supports local stop/settlement, not a claim of remote cloud cancellation.

Retained container metadata confirms the admitted core/Hindsight image pins, UID `1000:65532`, read-only auth mounts and only the fixture network. The request uses the synthetic principal with matching private audience. Both nonempty database dump sizes and hashes were independently checked before accepting the recorded cleanup: core 280,011 bytes, memory 79,820 bytes. Recorded removal of four fixture containers and the fixture network succeeded, with no cleanup blockers. No live cleanup or privacy probe was performed by the reviewer.

The Inspect log contains one sample, retains the exact result hash and records semantic score `unknown` with both human ratings unset. Its successful runner status means the result was recorded; it does not turn the envelope refusal into a task success. No comparison win, P1 completion or permission for a further run follows from this assessment.

Evidence SHA-256:

- Execution result: `bd34f5c38c3bc7ec39ce94a93fa770d4ad04889a6b345dabdf8116f46229e02d`.
- Inspect `.eval`: `554f3d11d8a0a08edadd0628c29a6ea4d275422b4cc685c82fc42754e7f71e2b`.
- Core child receipt (retained continuation output mount): `b8412a3285262dacae0ce7850d6d20280eee2eb3115eb0e464e6959d8b97e7d5`.
- Memory child receipt (same retained mount): `098e4ac89b80d7ea27628e3a52afdaf5b57557cbda0fc44b032a2ffd47e442a2`.
- Core dump: `b5090598cc3ee0f5d3397b5a37f94f38ab8b4f22f6c637226a4020d6e5ef6663`.
- Memory dump: `9f6a7b01ed3f60e64a3c1a33119e0dd103ee3661ad6d5efe708b3a65ceaf45a5`.
- First failed setup: `67aa7bc5407b1ab7f34c126a887616a7e0b32bd3cda9f1e8dc100f69da5ca4ec`.
- Second pre-input setup failure: `467e3784bc0fa82c66ae478094e328368c175e71a6ca6aa999b200307af9df96`.

This assessment used only existing receipts, hashes and recorded metadata. No model calls, tests, semantic AI scoring, new authentication investigation or additional participant review was performed.

## Bottie publication correction — two findings resolved

**GO for these two source corrections only; Bottie native admission remains false.** Failed reader cancellation now records `closeFailed`, stops the local sample and retains its controller. Cleanup cannot erase that ownership; the terminal receipt exposes `provider_close_failures`, a nonzero active count and `quiescent:false`. Non-OK responses explicitly cancel their owned body, including preserving cancellation failure. This resolves the earlier false-quiescence finding.

The observer now refuses observed error/cancelled/incomplete/failed events and terminal responses whose status is not completed or which contain an error. Missing, malformed and duplicate terminals remain rejected with unknown usage. This resolves the earlier terminal-event acceptance finding without changing native response bytes or draining the stream.

The existing focused test covers eleven synthetic variants in one pytest case. Its retained log reports **1 passed in 0.18 s**; the command receipt reports exit 0, 0.4987607850052882 s elapsed and zero provider calls. Close-failure variants assert retained ownership; non-OK variants assert an actual close. No test was repeated by the reviewer, and no further source or participant audit was performed.

- Worker SHA-256: `c06aa86c63aa4f465cd799592272efa67512fd01d3356c3c5b69b3e0854d9ec5`.
- Focused test SHA-256: `bf30ca64f7ccf7f98ca53e38e83191c168ef38a24577bfe01c91d271c36c8403`.
- `review-corrections.json`: `b67b2bce33be61c0990d85d75688708a454b4e2779c74e3ee4f54edebd1c8610`.
- `review-corrections.log`: `fa8e3e1f0a4b084326a7027307e5cc3a94f799f9733ea598c3b878fa3c70f37c`.

This source decision does not establish live provider behavior, cloud cancellation, complete Bottie admission or a comparative result.
