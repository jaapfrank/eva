"""Authorized six-request model-only comparison. No actions are executed.
Hard network cap includes retries and repair calls; results persist after each call.
Synthetic context and admission receipt are not live task or end-to-end proof.
"""
import json, os, sys
from pathlib import Path
from time import monotonic
from eva.adapters.model import openai_codex_provider as provider
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.adapters.objects.identity_verifier import load_current_identity_pack
from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
from eva.application.conversation import ConversationTurn, IdentityProfile, ConversationModelResult, LivingConversation, RecentConversationMessage, ConversationActionCall, ConversationActionReceipt
from eva.application.memory import PresenceMemory, RecallCurrentFacts
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.memory import MemoryRecall, RecalledFact
from eva.contracts.planning import ConfirmedFact
NOW = "2026-09-24T12:00:00Z"
class _Memory:
    def __init__(self) -> None:
        self.query: RecallCurrentFacts | None = None

    def recall_for_presence(self, query: RecallCurrentFacts) -> PresenceMemory:
        self.query = query
        fact = ConfirmedFact(
            fact_id="hindsight:memory-1",
            subject=query.principal_id,
            predicate="memory.observation",
            value={"text": "Jaap wil korte, directe antwoorden."},
            source_ref="telegram:history",
            evidence_ref="hindsight:memory-1",
            observed_at=NOW,
            trust="host_policy",
        )
        recall = MemoryRecall(
            schema_version=1,
            contract="MemoryRecall/v1",
            recall_id=query.recall_id,
            query_purpose=query.query_purpose,
            tenant_id=query.tenant_id,
            principal_id=query.principal_id,
            conversation_id=query.conversation_id,
            project_id=query.project_id,
            requested_valid_at=query.requested_valid_at,
            admitted_fact_ids_sha256=canonical_sha256([fact.fact_id]),
            selected=[
                RecalledFact(
                    rank=1,
                    fact_id=fact.fact_id,
                    source_event_ids=[fact.source_ref],
                    trust="model_inference_unconfirmed",
                    ranking_reason="hindsight_temporal_semantic_match",
                ).model_dump(mode="json")
            ],
            excluded_reasons={},
            projection_version="hindsight-0.9.1-v1",
            scope_sha256=canonical_sha256(query.scope),
            created_at=NOW,
        )
        return PresenceMemory(recall, (fact,))

class Capture:
    def generate(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        return ConversationModelResult("Captured", "fixture")

output = Path("/tmp/contact-comparison-20260924.json")
report = {
 "scope": "Synthetic model-only, current identity pack and delegated-work schemas. Not full runtime catalog, STT/TTS, private memory, actual work admission or channel latency.",
 "hypothesis": "GPT-6 Luna low may improve short contact latency and quality over GPT-5.6 Luna low on the unchanged provider route.",
 "criteria_before_run": {"greeting": "Warm concise Dutch greeting, no unnecessary tool or claims", "small_talk_during_work": "Answer current small talk, do not answer unfinished research or claim it complete", "accepted_work": "Briefly acknowledge admitted task without claiming completed result", "selection": "Quality must pass all three; prefer lower first-content latency across most cases, with no unsupported availability/latency claim"},
 "official_candidate_source": "https://developers.openai.com/api/docs/models/gpt-6-luna",
 "maximum_actual_provider_attempts": 6, "actual_provider_attempts": 0,
 "subscription_cost_eur": None, "rows": []}

def save():
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")

original = provider._CodexTransport.handle_async_request
async def bounded_transport(self, request):
    if not request.url.path.endswith("/responses/input_tokens"):
        if report["actual_provider_attempts"] >= 6:
            raise RuntimeError("Authorized provider request cap reached")
        report["actual_provider_attempts"] += 1
        save()
    return await original(self, request)
provider._CodexTransport.handle_async_request = bounded_transport

if sys.argv[1:] != ["--run-model-probe"]:
    raise SystemExit("Explicit --run-model-probe required")
identity=IdentityProfile("Eva", "Een warme, betrokken digitale huisgenoot.", (
 "Zeg eerlijk wat je wel en niet weet.", "Reageer als één persoon, niet als een verzameling scripts.",
 "Beweer een handeling pas na onafhankelijk bewijs.", "Gebruik herinneringen om de persoon te begrijpen, niet om hem na te doen."), 110, load_current_identity_pack())
cases=[("greeting", "Hallo"), ("small_talk_during_work", "Hoe gaat het met jou?"), ("accepted_work", "Zoek op de achtergrond uit wat Home Assistant Cloud voor spraak doet, met bronnen.")]
for index,(name,prompt) in enumerate(cases):
    turn=ConversationTurn.private("synthetic-tenant", "synthetic-installation", "jaap", "synthetic-conversation", name, prompt, NOW)
    capture=Capture()
    recent=() if name != "small_talk_during_work" else (
      RecentConversationMessage("Jaap", "Zoek op de achtergrond uit wat Home Assistant Cloud voor spraak doet, met bronnen.", NOW),
      RecentConversationMessage("Eva", "Ik zoek het voor je uit.", NOW))
    LivingConversation(_Memory(), capture, identity, contact_model=capture).respond(turn,recent)
    kwargs=dict(capture.kwargs)
    if name == "small_talk_during_work":
        kwargs["self_context"]=("Synthetische taak work:probe: onderzoek Home Assistant Cloud, state=call_started. Nog geen onderzoeksresultaat beschikbaar.",)
    if name == "accepted_work":
        kwargs["completed_action"]=ConversationActionReceipt(ConversationActionCall("synthetic-admission", "start_delegated_work", {"task":prompt}), {"status":"verified", "operation_result":{"operation":"start", "work":{"work_id":"work:probe", "state":"admitted"}}})
    models=["gpt-5.6-luna", "gpt-6-luna"] if index != 1 else ["gpt-6-luna", "gpt-5.6-luna"]
    for model_name in models:
        if report["actual_provider_attempts"] >= 6: break
        model=PydanticConversationModel(auth_file=Path(os.environ["EVA_CONVERSATION_AUTH_FILE"]), model=model_name, reasoning_effort="low", timeout_seconds=35, conversation_tools=PostgresDelegatedWorkActions.conversation_tools())
        started=monotonic(); before=report["actual_provider_attempts"]
        row={"case":name,"model":model_name,"effort":"low"}
        try:
            result=model.generate(*capture.args,**kwargs)
            row.update(timing=result.timing.to_wire(),usage=result.usage.to_wire(),text=result.text,action=None if result.action_call is None else result.action_call.name)
        except Exception as error:
            row.update(error_type=type(error).__name__, usage="unmeasured")
            cause=error.__cause__
            if cause is not None: row["provider_category"]=getattr(cause,"category",type(cause).__name__)
        row.update(wall_ms=round((monotonic()-started)*1000),actual_provider_attempts=report["actual_provider_attempts"]-before)
        report["rows"].append(row);save();print(json.dumps(row,ensure_ascii=False),flush=True)
        if "error_type" in row:
            report["stopped_reason"]="First failed comparison case; no repeated failure or extra discovery calls";save();raise SystemExit(0)
save()
