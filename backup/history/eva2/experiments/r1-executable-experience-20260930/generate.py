"""Synthesize one ordinary method and one predictor via Eva's existing provider."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits
from eva.adapters.model.openai_codex_provider import OpenAICodexProvider, OpenAICodexProviderSettings
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.bootstrap.runtime import RuntimeSettings
from service import CONTRACT, ReservationService


async def main():
    destination = Path(sys.argv[1])
    settings = RuntimeSettings.from_environment()
    adapter = PydanticConversationModel(auth_file=Path(settings.conversation_auth_file), model=settings.conversation_model)
    profile = OpenAICodexProviderSettings(endpoint=adapter._endpoint,
        model_name=settings.conversation_model, reasoning_effort=settings.conversation_reasoning_effort,
        timeout_seconds=adapter._research_timeout_seconds)
    if profile.model_name != "gpt-6-astra" or profile.reasoning_effort not in {"medium", "high"}:
        raise ValueError("owner work-model profile differs")
    development = ReservationService({"state": {"version": 1, "revision": 1,
        "capacities": {"alpha": 2, "beta": 3}, "reservations": {},
        "tasks": {"dev": {"audience": "owner", "authorized_ids": ["x"], "stopped": False}}}})
    transitions = []
    for path, body in (
        ("/reserve", {"id": "x", "slot": "alpha", "units": 2, "task_id": "dev", "request_id": "dev1", "expected_revision": 1}),
        ("/move", {"id": "x", "slot": "beta", "task_id": "dev", "request_id": "dev2", "expected_revision": 2}),
        ("/release", {"id": "x", "task_id": "dev", "request_id": "dev3", "expected_revision": 3}),
    ):
        before = development.request("GET", "/state", "")
        response = development.request("POST", path, json.dumps(body))
        transitions.append({"before": before, "operation": {"path": path, "body": body},
            "observed_response": response, "observed_after": development.request("GET", "/state", "")})
    prompt = """Create a small learned artifact from this unfamiliar API contract.
Return only JSON with ordinary_method (string) and predictor_source (Python string).
ordinary_method is a complete reusable procedure with normal freshness, current
contract, authorization, stop, idempotency, audience and independent readback
checks. It may use /validate for a sequence on a copy. Do not weaken it to make
the other artifact appear better. Mention temporary exceptions and current
instructions, not a fixed business task.
predictor_source reads {state,operations} JSON from stdin; each operation is
{path,body}. Predict all version-1 transitions on a copy and print a single JSON
object {unsupported:boolean,steps:[{ok:boolean,error?:string}],state:object}.
When a step fails, preserve that step's input, stop the sequence, and return its
error; prior successful predicted steps remain in the predicted state. Version
other than1 must return unsupported:true with no steps and an unchanged state.
Validate stale expected_revision, stopped task and authorized_ids using supplied
state; unavailable assumptions must be an explicit error. Predict only what the
contract supports. No reads, sockets, network, files, subprocesses or imports other
than sys,json,copy. Do not include hidden variables, hardcoded IDs or particular
slot names. It is advisory: never mutate input or claim observed success.
Keep source below the existing executor's16384byte bound. Unknown future versions
are for the ordinary reasoning owner to inspect, not guessed predictor support.
API contract follows:\n""" + CONTRACT + "\nObserved development transitions (not hidden cases):\n" + json.dumps(transitions)
    started = time.monotonic()
    async with OpenAICodexProvider(profile, credential_loader=adapter._load_auth) as session:
        result = await Agent(session.model, instructions="Learn only from the supplied public contract. Produce the requested bounded artifacts; do not execute external actions.", retries=0).run(prompt,
            usage_limits=UsageLimits(request_limit=1))
        raw = session.consume_raw_usage()
        record = {"model": profile.model_name, "reasoning": profile.reasoning_effort,
            "seconds": time.monotonic() - started, "requests": result.usage.requests,
            "inputTokens": raw.input_tokens, "outputTokens": raw.output_tokens,
            "cachedInputTokens": result.usage.cache_read_tokens,
            "providerEndpoint": profile.endpoint, "output": result.output}
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        value = result.output.strip()
        if value.startswith("```"):
            value = "\n".join(value.splitlines()[1:-1])
        artifact = json.loads(value)
        if set(artifact) != {"ordinary_method", "predictor_source"}:
            raise ValueError("generated artifact fields differ")
        if not all(isinstance(artifact[key], str) and artifact[key].strip() for key in artifact):
            raise ValueError("empty generated artifact")
        if len(artifact["predictor_source"].encode()) > 16384:
            raise ValueError("generated source exceeds existing executor bound")
        artifact_path = destination.with_name("generated-artifact.json")
        artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({key: record[key] for key in record if key != "output"}
            | {"artifactSha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest()}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
