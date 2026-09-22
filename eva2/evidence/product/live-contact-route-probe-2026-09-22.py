"""One instrumented ASGI voice turn using installed authority, memory and model.

Run baseline and candidate once each in separate interpreters. No deployment.
Only counts/timing/usage and the probe greeting leave the process.
"""
import asyncio
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from time import monotonic
from uuid import uuid4

from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.adapters.projection.hindsight import HindsightMemoryEngine
from eva.bootstrap.runtime import create_runtime_app, PostgresHindsightMemoryRuntime

measurements, outputs = [], []


def wrap(cls, name):
    original = getattr(cls, name)
    def measured(self, *args, **kwargs):
        start = monotonic()
        try:
            if name == "_run_pydantic_phase":
                from eva.adapters.model.pydantic_conversation import _input_tokenizer
                encode = _input_tokenizer().encode_ordinary
                instructions = kwargs["instructions"]
                headings = ["Vaste principes:", "Actief procedureel IdentityPack:",
                            "Duurzame stijlspiegel:", "Relevante herinneringen:",
                            "Jouw duurzame eigen toestand:", "Recente gesprekstijdlijn:"]
                starts = [("general_instructions", 0)] + [(h, instructions.index(h)) for h in headings]
                sections = {label: len(encode(instructions[offset:(starts[i + 1][1] if i + 1 < len(starts) else len(instructions))]))
                            for i, (label, offset) in enumerate(starts)}
                tools = kwargs.get("tools", ())
                outputs.append({"local_bpe_estimates_not_provider_breakdown": {
                    "instruction_sections": sections,
                    "instructions_total": len(encode(instructions)),
                    "tools_json": len(encode(json.dumps(tools, ensure_ascii=False, separators=(",", ":")))),
                    "prompt": len(encode(kwargs["prompt"])), "tool_count": len(tools)},
                    "instructions_sha256": hashlib.sha256(instructions.encode()).hexdigest()})
            result = original(self, *args, **kwargs)
            if name == "generate":
                outputs.append({"provider": result.provider, "usage": result.usage.to_wire(),
                                "timing": result.timing.to_wire(),
                                "action": None if result.action_call is None else result.action_call.name})
            if name == "recall_for_presence":
                outputs.append({"memory_count": len(result.confirmed_facts),
                                "memory_sha256": hashlib.sha256(repr(result.confirmed_facts).encode()).hexdigest()})
            return result
        finally:
            measurements.append({"stage": cls.__name__ + "." + name,
                                 "ms": round((monotonic() - start) * 1000)})
    setattr(cls, name, measured)


for cls, names in [
    (HindsightMemoryEngine, ["recall_for_presence_fast", "recall_for_presence", "_recall_response", "ensure_bank"]),
    (PostgresConversationActions, ["pending", "available_action_names", "active_context"]),
    (PydanticConversationModel, ["generate", "_admit_request", "_instructions", "_run_pydantic_phase"]),
    (PostgresHindsightMemoryRuntime, ["respond", "admit_voice_turn"]),
]:
    for name in names:
        wrap(cls, name)


def refuse_effects(*args, **kwargs):
    raise RuntimeError("The greeting probe may not dispatch actions")
PostgresConversationActions.execute = refuse_effects
PostgresConversationActions.execute_step = refuse_effects


async def main():
    app = create_runtime_app()
    token = Path(os.environ["EVA_DASHBOARD_ACCESS_TOKEN_FILE"]).read_text().strip()
    body = json.dumps({"session_id": str(uuid4()), "message_id": str(uuid4()), "text": "Hallo."}).encode()
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": "/internal/v1/voice-session/respond", "raw_path": b"/internal/v1/voice-session/respond",
             "query_string": b"", "root_path": "",
             "headers": [(b"authorization", ("Bearer " + token).encode()), (b"content-type", b"application/json")],
             "server": ("localhost", 80), "client": ("127.0.0.1", 12345)}
    events = []
    start = monotonic()
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    async def send(message):
        raw = message.get("body", b"")
        for line in raw.splitlines():
            event = json.loads(line)
            events.append({"type": event["type"], "at_ms": round((monotonic() - start) * 1000)})
    await app(scope, receive, send)
    print(json.dumps({"scope": "one real ASGI voice-session turn; no physical audio; fresh process",
                      "elapsed_ms": round((monotonic() - start) * 1000),
                      "events": events, "measurements": measurements, "outputs": outputs}, indent=2), flush=True)


asyncio.run(main())
