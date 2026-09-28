# B0 pinned Hermes source inspection — incomplete

Status: **UNKNOWN; compatibility gate not completed.** Checked 28 September 2026.
Eva integration base: `130b78a166facc8c07db472b467b554ef908d1ac`.
Hermes source pin: `f97608f178d1ffeca59860195ab7da295f7c8e5f` (planned v2026.9.24 / 0.21.5).
Scope: public source inspection only. The coordinator stopped expansion before deterministic probes,
following the owner's challenge to replacement justification. No installed change follows from this report.

## Decision and replacement justification

**No current-Eva functional gap has been established here that requires Hermes instead of a smaller
change to the existing runner.** The blueprint identifies single-step model mediation and custom
continuation ownership in current Eva. Those are architectural observations, not evidence that Hermes
improves a particular family outcome or that a native multi-step existing-runner change is insufficient.

This inspection establishes candidate integration seams and limitations. It measures no task quality,
latency, cost, autonomy, family experience or maintenance advantage. Compatibility is not replacement
justification. Do not select a cutover, trigger fallback C as if Hermes had failed, or claim five passes
from these source findings. Preserve the functioning baseline while the coordinator records the concrete
user-visible gap and the smallest sufficient change.

## Five required contracts

| Contract | Result | Evidence obtained / missing |
|---|---|---|
| Stateful ownership | UNKNOWN | Independent session handles, asynchronous submit thread, targeted steer/interrupt and client event replay are present. No two-job/contact/reconnect trace was executed. Detached background RPC is not a substitute for owned sessions. |
| Scope propagation | UNKNOWN | Profile/session metadata, memory provider and scoped thread support exist. No person/household/child/history/delivery isolation trace ran. Prefetch is once per user turn, retained in historical model context, and fails open; correction enforcement requires more than provider cache eviction. |
| Existing access/effect seam | UNKNOWN | Explicit Codex credential resolution and plugin handler context forwarding are present. Actual header/transport compatibility, credential rotation and retained Eva authority/readback/unknown-effect reconciliation were not executed. |
| Continuation/delivery | UNKNOWN | Submit persists the incoming row; replay has an epoch and truncation signal. Replay is in memory. Durable completed-result redelivery, independent work after contact reset and scheduled checkpoint continuation were not demonstrated. |
| Replaceability | UNKNOWN | A documented TUI RPC boundary and memory/tool extensions exist at the pin. No integrated ingress-to-owner trace removed the old runner, or proved that the required policy can be enforced without another scheduler/general-loop fork. |

No contract receives PASS from method names or isolated source availability.
No decisive overall public-seam NO-GO was established before work stopped.

## Concrete pinned-source findings

1. **Owned work should not use `prompt.background`.** In
   `tui_gateway/methods_prompt.py:970–1028`, this constructs a side agent on a worker thread and
   emits `background.complete` to its parent; it is not inserted as a normal controllable session.
   By contrast, `tui_gateway/methods_session.py:334–428` creates independent runtime/stored session
   IDs, and `methods_prompt.py:684–700` persists an input row before starting its turn thread.
   These are source-supported alternatives, not a tested Eva task lifecycle.

2. **Steer/interrupt are concrete but cooperative.**
   `tui_gateway/methods_session.py:2124–2207` targets the selected session, retires its crash marker
   on interruption, and queues steering. Queued steering is not proof the agent consumed it.
   `session.close` at lines 2010–2014 removes the selected live session.
   The required work/session reset relationship remains untested.

3. **Reconnect replay is not a durable result outbox.**
   `tui_gateway/event_replay.py:21–42,50–121` stores bounded per-session events in process memory,
   identifies the process epoch and marks truncated replay. A client must refetch authoritative
   state after truncation/restart. Eva's retained result/outbox service would still need an
   integrated delivery trace; event availability alone cannot prove durable exactly-once delivery.

4. **The memory documentation overstates the refresh cadence.**
   Pinned `website/docs/developer-guide/memory-provider-plugin.md:159` describes prefetch before
   each API call. Actual `agent/turn_context.py:1–7,859–881` places it in the once-per-turn prologue.
   Lines 884–926 persist recalled `api_content`; lines 1190–1252 replay historical sidecars.
   `agent/memory_manager.py:447–500` treats prefetch failures/timeouts as non-fatal.
   The blueprint permits a fresh scoped session on correction, but that mechanism was not built
   or tested here. Mid-work correction cannot be certified by merely clearing a provider cache.

5. **Access-only credentials have an explicit resolver seam; default login differs.**
   `hermes_cli/runtime_provider.py:664–677` accepts an explicit key/access token and Codex base URL
   without resolving the stored OAuth credentials when a key is supplied.
   `hermes_cli/auth_codex.py:90–109` instead requires both access and refresh tokens in the default
   Hermes auth-store route. Current Eva declares
   `https://chatgpt.com/backend-api/codex/responses` in
   `eva2/src/eva/bootstrap/delegated_work_provider.py:46` and
   `eva2/src/eva/adapters/model/pydantic_conversation.py:556`.
   This source compatibility is not verified authentication, token rotation or request-header parity.
   No account, token, refresh secret or live credential file was read.

6. **There is an effect adapter seam, not an executed effect proof.**
   `tools/registry.py:662–697,887–915` registers plugin handlers and forwards accepted context
   keyword arguments, including task/session/parent context, to the handler.
   The retained Eva effect services were located, but no synthetic dispatch through their
   authorization, readback and restart reconciliation was performed.
   `agent/memory_provider.py:84–181` supplies the memory extension contract; its presence likewise
   does not prove family authorization or correction behavior.

## Evidence and limits

The accompanying [source-references.json](source-references.json) records checked UTC, exact URLs,
SHA-256, Git blob identity and selected inspected ranges. Downloaded does not mean fully reviewed.
Source excerpts and selected implementations were inspected; this is not a whole-project security audit.
The upstream MIT license file was fetched at the pin. No dependency installation, source clone,
upstream code execution, model call, credential connection, household action, firmware change,
production mutation, staging or commit was performed by this task. No probe/test result exists.

Temporary public-source cache: `/tmp/eva2-b0-hermes-0ha6e084`.
Raw-source requests intermittently returned HTTP 429; after bounded retries, header transport and
some continuation files remained unfetched. Those gaps remain unknown; HTTP failure is not evidence
of a missing upstream feature. The coordinator independently read the session/admission/interrupt
and event replay seams. That source review does not turn any unexecuted contract into PASS.
