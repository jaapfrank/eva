"""Actual core HTTPX2 decoder compatibility; synthetic SSE, no network."""
import argparse
import asyncio
import gzip
import hashlib
import json
from pathlib import Path
import time

import httpx2
from eva.adapters.model.openai_codex_provider import _RawSseUsageAttestor
from cp001_accounting import ProviderSampleLedger
from cp001_http_accounting import ResponsesSampleTransport

ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"


class Body(httpx2.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.closes = chunks, 0
    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
    async def aclose(self):
        self.closes += 1


async def case(compressed):
    events = [
        {"type": "response.output_text.delta", "delta": "fixture"},
        {"type": "response.completed", "response": {"status": "completed", "error": None,
            "usage": {"input_tokens": 31, "output_tokens": 7, "total_tokens": 38}}},
    ]
    raw = ("".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n").encode()
    wire = gzip.compress(raw, mtime=0) if compressed else raw
    chunks = [wire[i:i + 3] for i in range(0, len(wire), 3)]
    body = Body(chunks)
    seen = []

    class Network(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            seen.append(await request.aread())
            return httpx2.Response(200, stream=body, headers={"content-type": "text/event-stream",
                **({"content-encoding": "gzip"} if compressed else {})})

    ledger = ProviderSampleLedger("httpx2-gzip" if compressed else "httpx2-plain",
        {"providerAttempts": 2, "providerInputTokens": 24000, "providerOutputTokens": 4096},
        {"contact": ("gpt-6-sol", "none")})
    transport = ResponsesSampleTransport(Network(), ledger, role="contact", endpoint=ENDPOINT,
        attestor_factory=_RawSseUsageAttestor)
    request_bytes = b'{"model":"gpt-6-sol","reasoning":{"effort":"none"},"input":"fixture"}'
    async with httpx2.AsyncClient(transport=transport) as client:
        async with client.stream("POST", ENDPOINT, content=request_bytes) as response:
            forwarded = [chunk async for chunk in response.aiter_raw()]
    assert seen == [request_bytes]
    assert forwarded == chunks, "accounting changed original response chunks"
    assert b"".join(forwarded) == wire
    assert body.closes == 1
    report = ledger.report()
    assert report["complete"] and report["provider_attempts"] == 1
    assert report["total_usage"] == {"input_tokens": 31, "output_tokens": 7, "total_tokens": 38}
    assert report["active_async_attempts"] == transport.active_responses == 0
    return {"encoding": "gzip" if compressed else "identity", "wire_sha256": hashlib.sha256(wire).hexdigest(),
        "chunks": len(chunks), "body_closes": body.closes, "ledger": report,
        "active_responses": transport.active_responses}



async def terminal_case(kind):
    terminal = {"type": "response.completed", "response": {"status": "completed", "error": None,
        "usage": {"input_tokens": 17, "output_tokens": 7, "total_tokens": 24}}}
    frame = ("data: " + json.dumps(terminal) + "\n\n").encode()
    raw = frame if kind != "missing" else b'data: {"type":"response.output_text.delta","delta":"fixture"}\n\n'
    if kind == "duplicate":
        raw += frame
    observed = asyncio.Event()

    class TerminalBody(httpx2.AsyncByteStream):
        closes = 0
        tail_reads = 0
        async def __aiter__(self):
            yield raw
            self.tail_reads += 1
            # Early-close consumers must never be forced to drain this tail.
            await asyncio.Future()
        async def aclose(self):
            self.closes += 1
            if kind == "close-failed":
                raise OSError("fixture close failed")

    body = TerminalBody()
    class Network(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx2.Response(200, stream=body, headers={"content-type": "text/event-stream"})

    ledger = ProviderSampleLedger("terminal-" + kind,
        {"providerAttempts": 2, "providerInputTokens": 24000, "providerOutputTokens": 4096},
        {"contact": ("gpt-6-sol", "none")})
    transport = ResponsesSampleTransport(Network(), ledger, role="contact", endpoint=ENDPOINT,
        attestor_factory=_RawSseUsageAttestor)
    failures = []
    async def consume():
        try:
            async with httpx2.AsyncClient(transport=transport) as client:
                async with client.stream("POST", ENDPOINT,
                    content=b'{"model":"gpt-6-sol","reasoning":{"effort":"none"}}') as response:
                    iterator = response.aiter_raw().__aiter__()
                    first = await anext(iterator)
                    assert first == raw
                    observed.set()
                    if kind == "cancelled":
                        await asyncio.Future()
                    # Mirrors the SDK ending iteration immediately after completed.
        except BaseException as error:
            failures.append(type(error).__name__)
    task = asyncio.create_task(consume())
    if kind == "cancelled":
        await observed.wait()
        ledger.stop_async_attempts("explicit fixture stop after terminal")
    await task
    report = ledger.report()
    assert body.tail_reads == 0, "observer drained native unread bytes"
    assert body.closes == 1
    if kind == "early-close":
        assert not failures and report["complete"]
        assert report["total_usage"] == {"input_tokens": 17, "output_tokens": 7, "total_tokens": 24}
        assert report["active_async_attempts"] == transport.active_responses == 0
    else:
        assert not report["complete"] and report["total_usage"]["input_tokens"] is None
        if kind == "close-failed":
            assert report["active_async_attempts"] == 1 and transport.active_responses == 1
        else:
            assert report["active_async_attempts"] == transport.active_responses == 0
    return {"consumer": kind, "tail_reads": body.tail_reads, "body_closes": body.closes,
        "observed_errors": failures, "ledger": report, "active_responses": transport.active_responses}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve existing probe output")
    result = {"upstream_model_calls": 0, "cases": [], "errors": []}
    started = time.monotonic()
    for compressed in (False, True):
        try:
            result["cases"].append(asyncio.run(case(compressed)))
        except BaseException as error:
            result["errors"].append({"compressed": compressed, "type": type(error).__name__, "message": str(error)})
            import traceback
            traceback.print_exc()
    for kind in ("early-close", "missing", "duplicate", "close-failed", "cancelled"):
        try:
            result["cases"].append(asyncio.run(terminal_case(kind)))
        except BaseException as error:
            result["errors"].append({"consumer": kind, "type": type(error).__name__, "message": str(error)})
            import traceback
            traceback.print_exc()
    result["seconds"] = time.monotonic() - started
    result["success"] = len(result["cases"]) == 7 and not result["errors"]
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
