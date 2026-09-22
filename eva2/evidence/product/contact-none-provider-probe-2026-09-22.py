"""Explicit one-request subscription capability probe; no memory or tools.

Run only once by the coordinator's authorization. The isolated settings override
tests provider capability before production validation support is considered.
The existing transport rejects max_output_tokens, so request a short answer and
retain the actual token receipt; do not claim a server-enforced 64-token limit.
"""
import asyncio
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from time import monotonic

import httpx2
from pydantic_ai import Agent
from eva.adapters.model.openai_codex_provider import OpenAICodexProvider, OpenAICodexProviderSettings
from eva.adapters.model.pydantic_conversation import PydanticConversationModel

receipt = {"hypothesis": "subscription Luna accepts reasoning none", "started_at": datetime.now(UTC).isoformat(), "requests": [], "tools": 0, "private_memory": False,
           "output_limit": "instruction requests under 64 tokens; transport has no supported server output cap"}

class ObservedNetwork(httpx2.AsyncBaseTransport):
    def __init__(self):
        self.network = httpx2.AsyncHTTPTransport(retries=0)
    async def handle_async_request(self, request):
        if receipt["requests"]:
            raise RuntimeError("one-request budget exhausted")
        body = json.loads(request.content)
        if body["model"] != "gpt-5.6-luna" or body["reasoning"]["effort"] != "none" or body.get("tools"):
            raise RuntimeError("probe envelope differs from authorization")
        receipt["requests"].append({k: body.get(k) for k in ("model", "reasoning", "instructions", "input", "tools", "store", "stream")})
        response = await self.network.handle_async_request(request)
        receipt["http_status"] = response.status_code
        if response.status_code >= 400:
            raw = await response.aread()
            try:
                error = json.loads(raw).get("error", {})
                receipt["provider_rejection"] = {k: error.get(k) for k in ("code", "type", "param", "message")} if isinstance(error, dict) else str(error)[:1000]
            except (ValueError, AttributeError):
                receipt["provider_rejection"] = "non-JSON error; body withheld"
        return response
    async def aclose(self):
        await self.network.aclose()

async def main():
    model = PydanticConversationModel(auth_file=Path(os.environ["EVA_CONVERSATION_AUTH_FILE"]))
    settings = OpenAICodexProviderSettings(endpoint="https://chatgpt.com/backend-api/codex/responses", model_name="gpt-5.6-luna", reasoning_effort="low")
    object.__setattr__(settings, "reasoning_effort", "none")
    start = monotonic()
    try:
        async with OpenAICodexProvider(settings, credential_loader=model._load_auth, network_transport=ObservedNetwork()) as session:
            result = await Agent(session.model, instructions="Je bent Eva. Begroet de gebruiker in één korte Nederlandse zin, onder 64 tokens.").run("Hallo")
            # Corrected after the first call: SDK 2.37 exposes this as a property.
            # The saved first-call receipt retains the original measurement failure.
            usage = result.usage
            receipt.update(accepted=True, output=result.output, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
    except Exception as error:
        receipt.update(accepted=False, exception_type=type(error).__name__)
    receipt["elapsed_ms"] = round((monotonic() - start) * 1000)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))

asyncio.run(main())
