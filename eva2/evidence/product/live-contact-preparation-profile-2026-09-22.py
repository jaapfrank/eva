"""Profile installed shared preparation without model dispatch or task writes.

Run in a separate interpreter inside the installed core. Only numeric timings,
counts and hashes leave the process; no memory text, identities or credentials.
"""
import hashlib
import json
from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.adapters.projection.hindsight import HindsightMemoryEngine
from eva.application.conversation import ConversationTurn, ConversationModelUsage, ConversationModelTiming
from eva.application.voice_context import VoiceContext, voice_context
from eva.bootstrap import runtime as runtime_module

measurements = []
envelopes = []


def wrap(cls, name):
    original = getattr(cls, name)
    def measured(self, *args, **kwargs):
        start = monotonic()
        try:
            return original(self, *args, **kwargs)
        finally:
            measurements.append({"stage": cls.__name__ + "." + name,
                                 "ms": round((monotonic() - start) * 1000)})
    setattr(cls, name, measured)


for cls, names in [
    (HindsightMemoryEngine, ["recall_for_presence_fast", "recall_for_presence", "_recall_response", "ensure_bank"]),
    (PostgresConversationActions, ["pending", "available_action_names", "active_context"]),
    (PydanticConversationModel, ["generate", "_admit_request", "_instructions"]),
]:
    for name in names:
        wrap(cls, name)


def no_dispatch(self, **kwargs):
    tools = kwargs.get("tools", ())
    instructions = kwargs["instructions"]
    envelopes.append({"model": self._model, "tools": len(tools),
                      "tool_bytes": len(json.dumps(tools).encode()),
                      "instructions_bytes": len(instructions.encode()),
                      "instructions_sha256": hashlib.sha256(instructions.encode()).hexdigest()})
    return ("Preparation probe.", None,
            ConversationModelUsage.measured(input_tokens=0, output_tokens=0, tool_calls=0),
            ConversationModelTiming.unmeasured())


PydanticConversationModel._run_pydantic_phase = no_dispatch
composition = {}
def capture_app(runtime, **kwargs):
    composition.update(kwargs)
    return runtime
runtime_module.create_app = capture_app
start = monotonic()
runtime = runtime_module.create_runtime_app()
build_ms = round((monotonic() - start) * 1000)
grant = composition["dashboard_voice"].issue_session()
composition["dashboard_voice"].check(grant)
turn = ConversationTurn.private(
    grant.tenant_id, grant.installation_id,
    grant.principal_id, "voice:profile:" + str(uuid4()),
    "profile:" + str(uuid4()), "Hallo.", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
)
start = monotonic()
runtime.admit_turn_audience(turn)
recent = runtime._journal(turn).recent(turn, exclude_message_id=turn.message_id)
with voice_context(VoiceContext(recent, (), lambda _: None, lambda _: None)):
    runtime._conversation.respond(turn, recent)
print(json.dumps({"scope": "installed preparation, no model dispatch or conversation/task write",
                  "build_ms": build_ms, "preparation_ms": round((monotonic() - start) * 1000),
                  "measurements": measurements, "envelopes": envelopes}, indent=2))
