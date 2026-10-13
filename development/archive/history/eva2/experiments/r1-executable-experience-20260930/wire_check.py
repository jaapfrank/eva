"""Check the real SDK/provider deferred continuation with offline SSE fixtures."""
import asyncio
import json
import httpx2
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.toolsets.external import ExternalToolset
from pydantic_ai.usage import UsageLimits
from eva.adapters.model.openai_codex_provider import OpenAICodexProvider, OpenAICodexProviderSettings
from compare import TOOLS


class Chunks(httpx2.AsyncByteStream):
    def __init__(self, events):
        self.events = events

    async def __aiter__(self):
        for event in self.events:
            yield ("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode()


async def main():
    observed = []
    async def respond(request):
        body = json.loads(request.content)
        observed.append(body)
        item = ({"id": "fc1", "type": "function_call", "call_id": "call1", "status": "completed",
            "name": "service_request", "arguments": '{"method":"GET","path":"/state","body_json":""}'}
            if len(observed) == 1 else {"id": "msg1", "type": "message", "status": "completed", "role": "assistant",
                "content": [{"type": "output_text", "text": "Observed state retained.", "annotations": [], "logprobs": []}]})
        response = {"id": "resp1", "object": "response", "created_at": 1.0, "status": "completed",
            "model": "gpt-6-astra", "output": [item], "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
            "usage": {"input_tokens": 17, "output_tokens": 7, "total_tokens": 24,
                "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}
        events = [{"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
            {"type": "response.output_item.added", "output_index": 0, "item": item},
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": response}]
        if item["type"] == "message":
            events.insert(2, {"type": "response.output_text.delta", "item_id": "msg1", "output_index": 0,
                "content_index": 0, "delta": "Observed state retained.", "logprobs": []})
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=Chunks(events), request=request)
    profile = OpenAICodexProviderSettings(endpoint="https://codex.test/responses", model_name="gpt-6-astra", reasoning_effort="medium")
    async with OpenAICodexProvider(profile, credential_loader=lambda: ("offline-access", "offline-account"),
            network_transport=httpx2.MockTransport(respond)) as session:
        agent = Agent(session.model, instructions="Use the fixture tool and preserve observations.", retries=0,
            toolsets=[ExternalToolset(TOOLS, id="r1-fixture-tools")], output_type=[str, DeferredToolRequests])
        first = await agent.run("Observe the fixture state.", usage_limits=UsageLimits(request_limit=1))
        assert isinstance(first.output, DeferredToolRequests)
        assert session.consume_raw_usage().total_tokens == 24
    async with OpenAICodexProvider(profile, credential_loader=lambda: ("offline-access", "offline-account"),
            network_transport=httpx2.MockTransport(respond)) as session:
        agent = Agent(session.model, instructions="Use the fixture tool and preserve observations.", retries=0,
            toolsets=[ExternalToolset(TOOLS, id="r1-fixture-tools")], output_type=[str, DeferredToolRequests])
        second = await agent.run(None, message_history=first.all_messages(),
            deferred_tool_results=DeferredToolResults(calls={first.output.calls[0].tool_call_id: {"version": 1}}),
            usage_limits=UsageLimits(request_limit=1))
        assert isinstance(second.output, str) and second.output == "Observed state retained."
        assert session.consume_raw_usage().total_tokens == 24
        assert any(item.get("type") == "function_call_output" for item in observed[1]["input"])
    print(json.dumps({"sdkDeferredContinuation": "passed", "rawUsagePerResponse": "passed", "mockRequests": len(observed), "externalRequests": 0}))


if __name__ == "__main__":
    asyncio.run(main())
