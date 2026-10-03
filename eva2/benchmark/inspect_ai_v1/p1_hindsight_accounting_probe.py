"""Run the installed Hindsight provider offline; no auth cache or model access.

The image has HTTPX but not Eva's Pydantic-AI dependencies. Load the exact three
pure usage-attestation definitions from a hash-bound Eva source file, rather
than copy a parser or fake Hindsight modules. Native provider imports stay real.
"""
import argparse
import ast
import asyncio
import base64
from dataclasses import dataclass
import gzip
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time
import types
import unittest
from unittest.mock import patch


def reject_network(*args, **kwargs):
    raise RuntimeError("Network is disabled in the offline native provider probe")


socket.socket.connect = reject_network
socket.socket.connect_ex = reject_network

import httpx
from pydantic import BaseModel
from hindsight_api.engine.providers import codex_llm
from cp001_accounting import ProviderSampleLedger, SampleStopped
from cp001_http_accounting import ResponsesSampleTransport

ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"
RESULTS = {}


def load_attestor(source, expected):
    raw = source.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == expected
    names = {"CodexProviderError", "RawUsageAttestation", "_RawSseUsageAttestor"}
    nodes = [n for n in ast.parse(raw).body if isinstance(n, ast.ClassDef) and n.name in names]
    assert {n.name for n in nodes} == names
    module = types.ModuleType("bound_eva_usage_attestor")
    module.__dict__.update(dataclass=dataclass, json=json, FailureCategory=str)
    sys.modules[module.__name__] = module
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), module.__dict__)
    return module._RawSseUsageAttestor


def book(sample):
    plan = json.loads((Path(__file__).parent / "p1_participants_20260929.json").read_text())["developmentPlan"]
    return ProviderSampleLedger(sample, plan["perSampleLimits"],
                                {"helper": ("gpt-6-astra", "medium")})


def events(text="fixture", *, usage=True, duplicate=False):
    delta = {"type": "response.text.delta", "delta": text}
    value = "event: response.text.delta\ndata: " + json.dumps(delta) + "\n\n"
    if usage:
        terminal = {"type": "response.completed", "response": {"status": "completed", "error": None,
            "usage": {"input_tokens": 31, "output_tokens": 7, "total_tokens": 38}}}
        frame = "event: response.completed\ndata: " + json.dumps(terminal) + "\n\n"
        value += frame * (2 if duplicate else 1)
    return (value + "data: [DONE]\n\n").encode()


class Body(httpx.AsyncByteStream):
    def __init__(self, chunks, *, block=False, fail_close=False):
        self.chunks, self.block, self.fail_close = chunks, block, fail_close
        self.started, self.closes = asyncio.Event(), 0

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.block:
            self.started.set()
            await asyncio.Future()

    async def aclose(self):
        self.closes += 1
        if self.fail_close:
            raise OSError("fixture close failure")


class FixtureResponse(BaseModel):
    result: str


async def native_provider(transport):
    token = "fixture." + base64.urlsafe_b64encode(json.dumps({"exp": 4102444800}).encode()).decode().rstrip("=") + ".fixture"
    with patch.object(codex_llm.CodexLLM, "_load_codex_auth", return_value=(token, "offline-fixture")), patch.object(
        codex_llm.CodexLLM, "_load_codex_refresh_token", return_value=None
    ):
        provider = codex_llm.CodexLLM(provider="openai-codex", api_key="", base_url="https://chatgpt.com/backend-api",
                                    model="gpt-6-astra", reasoning_effort="medium")
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=transport)
    return provider


async def native_call(provider):
    return await provider.call(messages=[{"role": "user", "content": "Return the fixture JSON."}],
        response_format=FixtureResponse, strict_schema=False, return_usage=True,
        max_retries=1, initial_backoff=0, max_backoff=0, scope="retain")


class NativeAccountingChecks(unittest.IsolatedAsyncioTestCase):
    attestor = None

    def transport(self, ledger, handler):
        return ResponsesSampleTransport(httpx.MockTransport(handler), ledger, role="helper",
                                        endpoint=ENDPOINT, attestor_factory=type(self).attestor)

    async def test_native_retry_counts_both_requests_and_keeps_estimate_separate(self):
        ledger, requests, bodies = book("native-retry"), [], []
        async def respond(request):
            requests.append(json.loads(request.content))
            raw = events("not-json" if len(requests) == 1 else '{"result":"native parser accepted"}')
            body = Body([raw[:17], raw[17:]])
            bodies.append(body)
            return httpx.Response(200, stream=body, headers={"content-type": "text/event-stream"})
        transport = self.transport(ledger, respond)
        provider = await native_provider(transport)
        try:
            result, estimate = await native_call(provider)
            self.assertEqual(result.result, "native parser accepted")
            self.assertEqual(len(requests), 2)
            self.assertTrue(all(r["model"] == "gpt-6-astra" and r["reasoning"]["effort"] == "medium" for r in requests))
            report = ledger.report()
            self.assertTrue(report["complete"])
            self.assertEqual(report["total_usage"], {"input_tokens": 62, "output_tokens": 14, "total_tokens": 76})
            self.assertNotEqual(estimate.model_dump(), report["total_usage"])
            self.assertEqual([b.closes for b in bodies], [1, 1])
            self.assertEqual(transport.active_responses, 0)
            RESULTS[self._testMethodName] = {"ledger": report, "native_estimate": estimate.model_dump(),
                "note": "Native char/4 estimate is not raw measured usage; both responses are synthetic."}
        finally:
            await provider._client.aclose()

    async def test_native_missing_usage_stops_before_retry(self):
        for status, raw in [(200, events("not-json", usage=False)), (503, b"unavailable")]:
            with self.subTest(status=status):
                ledger, dispatches = book("missing-" + str(status)), []
                body = Body([raw])
                def respond(request):
                    dispatches.append(1)
                    return httpx.Response(status, stream=body)
                transport = self.transport(ledger, respond)
                provider = await native_provider(transport)
                try:
                    with self.assertRaises(SampleStopped):
                        await native_call(provider)
                    self.assertEqual(len(dispatches), 1)
                    self.assertIsNone(ledger.report()["total_usage"]["input_tokens"])
                    self.assertEqual(ledger.report()["active_async_attempts"], 0)
                    self.assertEqual(body.closes, 1)
                    RESULTS[self._testMethodName + str(status)] = ledger.report()
                finally:
                    await provider._client.aclose()

    async def test_fragmented_gzip_preserves_wire_bytes_and_request(self):
        ledger, raw, seen = book("compressed"), events("fragmented"), []
        compressed = gzip.compress(raw)
        chunks = [compressed[i:i+3] for i in range(0, len(compressed), 3)]
        body = Body(chunks)
        def respond(request):
            seen.append(request.content)
            return httpx.Response(200, stream=body, headers={"content-encoding": "gzip", "x-fixture": "unchanged"})
        transport = self.transport(ledger, respond)
        payload = b'{"model":"gpt-6-astra","reasoning":{"effort":"medium"},"input":"unchanged"}'
        async with httpx.AsyncClient(transport=transport) as client:
            async with client.stream("POST", ENDPOINT, content=payload) as response:
                delivered = [chunk async for chunk in response.aiter_raw()]
                self.assertEqual(delivered, chunks)
                self.assertEqual(response.headers["content-encoding"], "gzip")
                self.assertEqual(response.headers["x-fixture"], "unchanged")
        self.assertEqual(seen, [payload])
        self.assertEqual(gzip.decompress(b"".join(delivered)), raw)
        self.assertTrue(ledger.report()["complete"])
        self.assertEqual(body.closes, 1)
        RESULTS[self._testMethodName] = ledger.report()

    async def test_wrong_role_is_rejected_before_dispatch(self):
        ledger, seen = book("wrong-role"), []
        def respond(request):
            seen.append(1)
            return httpx.Response(200, content=events())
        transport = self.transport(ledger, respond)
        async with httpx.AsyncClient(transport=transport) as client:
            with self.assertRaises(SampleStopped):
                await client.post(ENDPOINT, json={"model": "gpt-6-sol", "reasoning": {"effort": "none"}})
        self.assertEqual(seen, [])
        self.assertEqual(ledger.report()["provider_attempts"], 0)
        RESULTS[self._testMethodName] = ledger.report()

    async def test_stop_closes_only_selected_native_call_even_after_terminal(self):
        books = [book("stop-left"), book("stop-right")]
        bodies = [Body([events()], block=True), Body([], block=True)]
        transports = [self.transport(ledger, lambda request, body=body: httpx.Response(200, stream=body))
                      for ledger, body in zip(books, bodies)]
        providers = [await native_provider(transport) for transport in transports]
        tasks = [asyncio.create_task(native_call(provider)) for provider in providers]
        try:
            await asyncio.wait_for(asyncio.gather(*(body.started.wait() for body in bodies)), 5)
            books[0].stop_async_attempts()
            with self.assertRaises(asyncio.CancelledError):
                await tasks[0]
            self.assertFalse(tasks[1].done())
            self.assertEqual([body.closes for body in bodies], [1, 0])
            self.assertEqual(books[0].report()["active_async_attempts"], 0)
            self.assertEqual(books[1].report()["active_async_attempts"], 1)
            self.assertIsNone(books[0].report()["total_usage"]["input_tokens"])
            RESULTS[self._testMethodName] = {"left": books[0].report(), "right_before_cleanup": books[1].report()}
        finally:
            books[1].stop_async_attempts()
            await asyncio.gather(*tasks, return_exceptions=True)
            for provider in providers:
                await provider._client.aclose()

    async def test_failed_close_remains_unsettled(self):
        ledger, body = book("close-failed"), Body([], block=True, fail_close=True)
        transport = self.transport(ledger, lambda request: httpx.Response(200, stream=body))
        provider = await native_provider(transport)
        task = asyncio.create_task(native_call(provider))
        await asyncio.wait_for(body.started.wait(), 5)
        ledger.stop_async_attempts()
        with self.assertRaises(OSError):
            await task
        with self.assertRaises(OSError):
            await transport.aclose()
        self.assertEqual(body.closes, 1)
        self.assertEqual(ledger.report()["active_async_attempts"], 1)
        self.assertEqual(transport.active_responses, 1)
        self.assertFalse(ledger.report()["complete"])
        RESULTS[self._testMethodName] = ledger.report()

    async def test_eof_close_failure_prevents_native_retry(self):
        ledger, dispatches = book("eof-close-failed"), []
        body = Body([events("not-json")], fail_close=True)
        def respond(request):
            dispatches.append(1)
            return httpx.Response(200, stream=body)
        transport = self.transport(ledger, respond)
        provider = await native_provider(transport)
        with self.assertRaises(OSError):
            await native_call(provider)
        with self.assertRaises(SampleStopped):
            await native_call(provider)
        self.assertEqual(len(dispatches), 1)
        self.assertEqual(ledger.report()["active_async_attempts"], 1)
        self.assertEqual(body.closes, 1)
        with self.assertRaises(OSError):
            await transport.aclose()
        RESULTS[self._testMethodName] = ledger.report()

    async def test_transport_close_attempts_all_owned_bodies(self):
        ledger = book("close-all")
        bodies = [Body([], fail_close=True), Body([])]
        class Inner(httpx.AsyncBaseTransport):
            def __init__(self):
                self.index, self.closes = 0, 0
            async def handle_async_request(self, request):
                body = bodies[self.index]
                self.index += 1
                return httpx.Response(200, stream=body)
            async def aclose(self):
                self.closes += 1
        inner = Inner()
        transport = ResponsesSampleTransport(inner, ledger, role="helper", endpoint=ENDPOINT,
                                            attestor_factory=type(self).attestor)
        for _ in range(2):
            await transport.handle_async_request(httpx.Request("POST", ENDPOINT,
                json={"model": "gpt-6-astra", "reasoning": {"effort": "medium"}}))
        with self.assertRaises(OSError):
            await transport.aclose()
        self.assertEqual([b.closes for b in bodies], [1, 1])
        self.assertEqual(inner.closes, 1)
        self.assertEqual(transport.active_responses, 1)
        self.assertEqual(ledger.report()["active_async_attempts"], 1)
        RESULTS[self._testMethodName] = ledger.report()

    async def test_duplicate_terminal_never_becomes_complete(self):
        ledger, body = book("duplicate"), Body([events(duplicate=True)])
        transport = self.transport(ledger, lambda request: httpx.Response(200, stream=body))
        async with httpx.AsyncClient(transport=transport) as client:
            with self.assertRaises(SampleStopped):
                await client.post(ENDPOINT, json={"model": "gpt-6-astra", "reasoning": {"effort": "medium"}})
        self.assertFalse(ledger.report()["complete"])
        self.assertEqual(body.closes, 1)
        RESULTS[self._testMethodName] = ledger.report()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--eva-provider-source", type=Path, required=True)
    parser.add_argument("--eva-source-sha256", required=True)
    parser.add_argument("--native-source-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--test", action="append")
    args = parser.parse_args()
    assert not args.output.exists(), "Do not overwrite earlier observations"
    assert os.environ.get("CODEX_HOME") == "/tmp/offline-codex-empty"
    assert not Path(os.environ["CODEX_HOME"]).exists()
    native_hash = hashlib.sha256(Path(codex_llm.__file__).read_bytes()).hexdigest()
    assert native_hash == args.native_source_sha256
    NativeAccountingChecks.attestor = load_attestor(args.eva_provider_source, args.eva_source_sha256)
    suite = (unittest.TestSuite(NativeAccountingChecks(name) for name in args.test) if args.test
             else unittest.defaultTestLoader.loadTestsFromTestCase(NativeAccountingChecks))
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {"success": result.wasSuccessful(), "tests_run": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "seconds": time.monotonic()-started,
        "native_provider_sha256": native_hash, "eva_provider_source_sha256": args.eva_source_sha256,
        "httpx_version": httpx.__version__, "upstream_model_calls": 0,
        "credentials": "synthetic only; no auth file read or copied",
        "cases": RESULTS, "limits": ["Mock HTTP responses; no provider latency or billed cost measured",
            "Actual CodexLLM retry and decode, not complete retain/reflect lifecycle",
            "Only the explicitly bound client; no full-instance admission", "Comparison stop is assistance, not native cancellation"],
        "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                    [Path(__file__), Path(__file__).with_name('cp001_http_accounting.py'), Path(__file__).with_name('cp001_accounting.py')]}}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k not in ["cases", "sources"]}))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
