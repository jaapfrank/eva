# T2.1 — independent final review, 5 October 2026

**SOURCE GO and INSTALLED OUTCOME GO. T2.1 meets its original technical finish condition for this concrete execution failure. Together with the retained T2.2–T2.4 evidence, this supports technical completion of the T2 work package, “Algemene huisbediening”.** The historical HA timeout remains unexplained. This decision does not establish general autonomy/reliability, complete a broader product group, or reopen the accepted I2.2 outcome.

Reviewer: `/root/agentzero_cm_review`. Root owns product changes, installation, evidence and workboard. The reviewer inspected the bounded source changes and synthetic receipts, added the specifically assigned regression tests, and ran the authorized isolated tests. No model or Home Assistant calls were made by the reviewer. This final review adds no runtime trial or test run.

The unchanged T2.1 condition is: “Een concrete falende uitvoerstap verklaard en verbeterd; geen ongewijzigde proefherhaling of timeoutverhoging zonder bewijs.” The prior malformed `home_assistant_request.body_json` failure was identified before HA transport. The installed route now returns that specific correctable error to Eva, which repairs the new request within the same accepted work without repeating the earlier write. No timeout increase or unchanged task replay establishes this result.

This T2.1 review does not replace earlier accepted evidence. The original T2 criterion in `design/blueprint-2026-09-28/delivery-plan.md` requires semantic device/service discovery, a completed room/comfort request, and automation creation/change/readback through the shared core available to chat, Telegram and both speakers. `evidence/product/t2-room-request-20261002.json` supplies the completed contextual action and independent state verification for T2.2; `evidence/product/t2-current-shared-ha-20261002.json` preserves actual automation evidence and verifies the household/session bindings for T2.3 and T2.4. The blueprint explicitly allows a suitable installed text route and separate adapter checks, without repeating the full HA suite through each speaker. No additional unmet technical T2 finish condition was identified. Physical audio/listening/interruption and household acceptance remain with C3/C4 and the existing acceptance work; the recorded speed/cost limitations and unexplained historical timeout remain visible.

The source review found no blocking issue:

- `home_assistant_api.py` retains hard envelope, route and WebSocket-command checks. Only the specific JSON syntax failure becomes a persisted negative receipt with `success=false`, `request_sent=false` and source `home_assistant_request_validation`. No HTTP/socket operation occurs for that rejection. A tool admission is still recorded by the existing durable route; this is not a claim of zero tool admissions. Corrected arguments require a new indexed step. Authority, cancellation, replay binding and budgets remain intact; the negative receipt cannot alone satisfy completion.
- `household_digital_work.py` permits retained task reads across a changed model-configuration fingerprint. Current execution still requires the exact release. Current actor/device/owner/policy/grant checks and engine/output-schema compatibility remain; there is no blanket exception catch or silent record skipping.
- `native_voice.py` records the failure type, at most 16 basename/function/line frames and a hashed request ID. It does not log exception messages, locals, prompt or token, and preserves generic client failures and cleanup.

`final-installation.json` binds the installed core/worker images to the exact reviewed source:

| Source | SHA-256 |
| --- | --- |
| `src/eva/adapters/connectors/home_assistant_api.py` | `a7ea7034a6f75beb580f879a04a3c1261714960a4a29fa6c91071b9e4536bc85` |
| `src/eva/adapters/api/native_voice.py` | `5ce562ece3e253961a397c874e758fd60d801de5f8b6300d5589c01ea1f9b0da` |
| `src/eva/bootstrap/household_digital_work.py` | `c7a23349301d68ed556028ad4e32c082c4531c147757739ca4e2acccb4f47467` |

The installation evidence records verified runtime source and package verification of 1,225 files/eight images, without a schema change or HA restart. Root reported 22 passing adapter tests. The reviewer ran 28 passing native API tests (2.38 s) and six passing isolated PostgreSQL cases (12.33 s), including input recovery, no repeated write, stop/resume, revocation and retained-task compatibility. The existing `native-test.log` and `profile-test.log` retain these outputs.

Final evidence was read from `/home/administrator/project/.eva2-artifacts/t2-ha-input-recovery-20261005/`: `repaired-manifest.json`, `work-59a67638-36ab-5ca8-be71-7dbcd4f2a939.json`, `final-installation.json` and `cleanup.json`. The five receipts share one source message and execution namespace, with consecutive indices 0–4:

| Step | Recorded outcome |
| --- | --- |
| 0 | GET of the exact synthetic source; limit 1 returned only its entity ID. This did not yet establish its value. |
| 1 | GET with `/state` selection returned 55. |
| 2 | Exactly one POST to that source set its state to 22; HTTP 200. |
| 3 | Intentionally malformed template JSON produced the local negative receipt, with no HA transport. |
| 4 | Corrected template request returned 22; HTTP 200. |

Thus four HA transport responses and one local rejection are evidenced. Stored work `work:household-digital:59a67638-36ab-5ca8-be71-7dbcd4f2a939` is `succeeded`. Its text, work ID and result digest match the callback for `t2-json-repaired-6c5e15022020` and the registered satellite. Result SHA-256: `af30b1baf1d9ffb24699b4a9b8bc96903ea3aeceda521a0535200d9c93ac4ff7`. Acknowledgement was 24.078 s; the complete result was 65.444 s. Cleanup deleted only the created synthetic source and confirmed HTTP 404 afterwards.

Worker accounting is partial: six completed responses, five tool calls, 34,670 input and 536 output tokens. Recorded foreground usage is one model attempt, 4,045 input and 334 output tokens. Total provider cost and development credits remain unknown; no efficiency improvement is claimed.

The probe explicitly requested the malformed JSON and subsequent repair. It proves installed continuation and preservation of an earlier effect with a real model; it is not an unseen behavioral benchmark or proof of general autonomous error recovery. Earlier failures remain evidence, including the diagnosed profile-read incompatibility. Technical completion must retain the historical timeout as a separate unresolved observation and must not describe it as repaired by this change.