"""Synthetic public question through the installed household EVA backend.

This measures text/backend routing, not microphone or acoustic latency.
No answer text, private memory, query or credentials are printed.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
from time import monotonic
from uuid import uuid4

sys.path.insert(0, "/run/speaker-deploy")
from launch import prepare
from speaker_service import ServiceConfig
from eva.voice_service.agent import HouseholdCoreClient


async def main():
    prepare()
    config = ServiceConfig.load(Path("/run/eva-speaker-runtime/config.json"))
    binding = config.grants[0].binding
    core = HouseholdCoreClient(config.eva_url, config.eva_token,
        satellite_id=binding.satellite_id, device_id=binding.device_id)
    assert await core.ready()
    session = (await core.work_return("open", str(uuid4())))["session_id"]
    async def renew():
        while True:
            await asyncio.sleep(4)
            await core.work_return("renew", session)
    renewal = asyncio.create_task(renew())
    start, final = monotonic(), False
    try:
        async for event in core.events({"session_id": session, "message_id": str(uuid4()),
                "text": os.environ["EVA_TEST_PUBLIC_QUESTION"], "recent": [],
                "active_work": [], "work_results": []}):
            if event.get("type") == "delta":
                continue
            metadata = {"event": "public_question_probe", "session": session,
                "elapsed_ms": round((monotonic() - start) * 1000),
                "kind": event.get("type")}
            if event.get("type") in {"action_started", "action_finished"}:
                metadata.update(name=event.get("name"), status=event.get("status"))
            if event.get("type") == "final":
                final = True
                reply = event["reply"]
                metadata.update(provider=reply.get("provider"), answer_characters=len(reply["text"]))
            print(json.dumps(metadata), flush=True)
        assert final, "No final backend reply"
    finally:
        renewal.cancel()
        await asyncio.gather(renewal, return_exceptions=True)
        await core.work_return("close", session)


asyncio.run(main())
