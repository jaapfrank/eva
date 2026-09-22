# Public research through existing background work

## Status and exact outcome

Candidate implemented and model-free tests passed. The only real provider probe
remains **ambiguous**, not successful: the existing research usage parser records
zero hosted tool calls. No second real request was made. Independent review,
model-adapter counter repair, complete conversation wiring, a newly authorized
real probe, installation and owner acceptance remain with the coordinator.

Baseline: origin/main `64fdd6cb7041b49233ff36ad3dafaeb2b792ee3f` was directly
verified. WSL worktree `/home/administrator/project/eva2-background-research-tools`,
branch `codex/background-research-tools`. The prior dashboard refresh is retained
via baseline integration `48c926b8`. No dashboard, central workboard, deployment,
conversation.py, runtime.py or model-adapter implementation files were edited.

The missing product connection was concrete: the existing delegated worker had an
explicitly tool/network/context-free v1 grant, and persisted usage required zero
tools. Existing public research already supported source artifacts and durable
execution in OwnerResearchContinuationTool / PostgresExternalConversationTools.

## Implementation and authority

- Preserve DelegatedWork/v1 and its tool-free semantics. New explicit
  DelegatedResearchWork/v2 / DelegatedResearchGrant/v2 permits only the existing
  host tool `research.public_sources`, with no ambient memory/workspace or child
  network access. PublicResearchRequest/v1 seals exact query/title/options.
- Reuse the existing owner admission, binding checks, work IDs, database table,
  claim/call fence, result hashing, stop/resume and work/result UI. No migration,
  new scheduler, task-specific research script or new provider was added.
- Admission is opt-in through a host-composed research profile. The new
  `start_background_research` tool journals admission and returns work_id without
  doing provider work. Existing get/list/stop/resume handle it. Explicit resume
  retains its public query and v2 grant; retrying admission/resume is idempotent.
- Reuse the existing public projection checks and owner research tool adapter.
  Worker journal contains only the admitted public question, exact private owner
  scope and call ID; no memory retrieval. Current owner binding, call fence,
  deadline and durable stop are checked before tool dispatch. No uncertain task
  is automatically executed again after a crash.
- One existing worker alternates between at most the tool-free and research
  sealed releases; it still executes one claimed task at a time. This does not
  prove multiple research tasks execute simultaneously. Foreground admission and
  conversation do not wait for that worker.
- Successful output is the research answer plus concrete sources. Technical
  request/artifact hashes and proof limits stay in the tool journal and evidence,
  not a repeated technical disclaimer in the household's answer.
- Existing measured failure usage is recovered from the same journal without
  rerunning the tool. V2 failures can retain actual over-budget usage; success
  still enforces all grant limits. V1 failure/success rules remain unchanged.

## Tests

Final model-free run: **37 passed in 8.78 s**:

```sh
PYTHONPATH=src EVA2_TEST_POSTGRES_DSN=postgresql+psycopg://postgres@127.0.0.1:44620/eva2_4b_disposable_research_final \
/home/administrator/project/.eva2-artifacts/pydantic-ai-2.37-gate-env.NWD66e/venv/bin/python -m pytest -q \
 tests/postgres/test_delegated_research.py tests/contract/test_delegated_work.py \
 tests/bootstrap/test_delegated_work_provider.py tests/bootstrap/test_delegated_work_service.py \
 tests/postgres/test_delegated_work_actions.py
```

Nine new PostgreSQL checks cover version isolation, no ambient authority,
source result persistence/readback, event-loop availability during held research,
stop before/during dispatch, crash without replay, binding revocation after claim,
other-person denial, immediate journaled foreground admission, idempotence,
explicit v2 resume, mismatched artifact refusal and measured over-budget failure.
The other 28 are existing v1 contract/provider/service/action checks.

Existing owner-service lifecycle regression: **7 passed in 5.13 s**, using a
separate empty disposable database. A first combined invocation hit the existing
module fixture's empty-schema requirement (9 passed, 6 setup errors); those six
were rerun with their own fresh database, no test checks relaxed. Initial test
setup also identified an unsuitable locally tagged PostgreSQL18 image; it was
replaced with the required cached PostgreSQL16.13 before database tests. An early
fixture typo called nonexistent get_owned; changed to existing load. Final
`git diff --check` passed. No dependency/lock changes.

## Single real probe and unfulfilled criterion

Root explicitly authorized one public question about HEAD response bodies and
current official HTTP semantics. An isolated read-only container used the existing
model lease, subscription endpoint/admission and copied non-secret runtime budget;
only its disposable database and evidence directory were writable. The script
instrumented HTTPS transport to permit at most one outgoing provider HTTP request,
including auth retries/redirects. It made exactly **1 request**, no blocked retry.

[Original receipt](real-probe.json): **9.967 s**, 8,895 input + 135 output = 9,030
tokens, reported tool_calls=0, stored state=ambiguous, no delegated success result.
The 1,941 USD-micros figure is the existing Luna API-equivalent token calculation,
not actual subscription/credit cost; actual credits are unknown.

[Read-only journal readback](journal-readback.json) found TOOL_REQUEST_ADMITTED,
TOOL_PREPARED, TOOL_EXECUTED, STATE_VERIFIED and a source artifact citing RFC9110.
It proves artifact storage, not how many hosted searches actually ran. No raw SSE
was retained. The original zero count/state is preserved; no retrospective tool
count or success is invented from a URL in generated output.

Concrete dependency: `pydantic_conversation.py::_request_using` computes tool_calls
only from a ConversationActionCall at parsed[1]. Research returns
(text, cited_sources, consulted_sources), so it always records zero. The model
adapter owner is implementing an independent offline-tested count of unique
completed hosted web_search_call IDs. The research runner correctly refuses
success without measured tool use. Root will review integration before deciding
whether to authorize one changed-hypothesis probe. No automatic second probe.

The fixed 60-second client timeout does not prove server-side cancellation.
The provider does not currently expose a proven enforced hosted-search cap through
this route. Three calls is a result-admission threshold, not an advance cost cap.
Input/output limits are 65,536/1,200 for the proof; the ordinary runtime budget is
unchanged. Submitted input has existing preflight checks; provider-added retrieval
context is only observable afterward. The new result admission checks its full
measured input. No new spending route or unknown-cost exception was introduced.

## Minimal integration fragment (coordinator / conversation owner)

1. On both core admission and worker, construct the same existing
   PydanticConversationModel with current read-only lease, MODEL_NAME
   (gpt-5.6-luna), reasoning_effort='low', research_timeout_seconds=60 and the
   unchanged RuntimeSettings.runtime_resource_budget. Do not instantiate memory,
   unrelated tools or a second LivingConversation for this worker.
2. Construct DelegatedResearchProfile(MODEL_PROFILE_ID,
   research.research_configuration_sha256(),
   maximum_input_tokens=budget.max_input_tokens,
   maximum_output_tokens=budget.max_output_tokens, maximum_tool_calls=3).
   Both ends require exact same budget scope/configuration and source hashes.
3. Runtime's existing DelegatedWorkOwnerService constructor receives
   research_profile=profile. Both existing descriptor aggregation sites call
   conversation_tools(include_research=True). With no profile/default argument,
   research remains disabled; the v1 tools remain unchanged.
4. Worker entrypoint calls await serve_research_enabled_work(settings, research,
   profile) from bootstrap.delegated_research. It uses the current worker service
   loop, existing DSN/scope/lease and the two sealed release selectors. It replaces
   the worker's entrypoint composition; do not start a second scheduler/daemon.
   Existing environment paths suffice; no new credentials or external account.
   The worker needs the existing non-secret runtime-budget artifact read-only.
5. In conversation.py, add start_background_research to the existing
   continuation_names start set (end foreground tool continuation after admission)
   and contact_receipts set (acknowledgment stays on contact model). Add a short
   conditional instruction to use that offered tool for explicit public research,
   while start_delegated_work remains tool-free. Preserve current stop/correction
   semantics and the selected work ID; no channel-specific regex or canned reply.
6. In modeladapter _instructions future-capability set, add
   start_background_research alongside start_delegated_work/resume/create goal.
   This avoids the old blanket inability instruction conflicting with a real tool.
   The model-adapter owner also supplies the hosted-search measurement fix.

Steps 1–6 are instructions, not an installed or tested complete conversation flow.
The injected-service and event-loop checks do not prove natural-language routing,
actual foreground conversation during real work or fast acknowledgment latency.
Those remain part of the coordinator's integrated end-to-end verification.

Before cutover, retain existing durable v1 rows and results; compare active
release fingerprints and deal explicitly with outstanding old-release calls.
Never rewrite old task grants or auto-replay ambiguous work. Rollback disables
new research admission and restores the prior worker composition; preserve v2
rows with this compatible reader available. A pre-v2 binary cannot read v2 rows,
so rolling back binaries without retaining that reader is not a proven rollback.

## Time and remaining work

Worktree created 12:40:29 UTC, final 37-test pass by 13:03:34 UTC: about 23 minutes
elapsed, excluding this evidence/commit handoff. Active compute/token/credit totals
are not measured. Initial 1–2 agent-hour estimate was not a cost guarantee. No
extra agents were started. The original real-probe script remains at
/tmp/eva2-research-single-probe-20260922.py solely for coordinator inspection;
rerunning it requires separate authorization and a fresh disposable database.

Next: independent source review, counter fix, minimal wiring, integrated model-free
checks, separately authorized changed-hypothesis real probe, then coordinator
installation/live check/rollback. No complete product, general autonomy, parallel
research execution, owner acceptance or present live availability is claimed.
