"""Focused checks for the newly connected native provider/isolation seam."""
import asyncio
import json
from pathlib import Path
import socket
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "development/benchmark/inspect_ai_v1"))
from p1_model_tunnel import ModelTunnel
from p1_eva2_native_worker import bind_memory_client, block_action, observe_memory_protocol, native_response_boundary
from cp001_accounting import SampleStopped


class SubscriptionBindingTests(unittest.TestCase):
    def test_tunnel_denies_other_hosts_and_closes_pending_connection(self):
        tunnel = ModelTunnel(("127.0.0.1", 0), "127.0.0.0/8").start()
        client = socket.create_connection(tunnel.server.server_address)
        client.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n")
        self.assertIn(b"403", client.recv(1024))
        client.close()
        idle = socket.create_connection(tunnel.server.server_address)
        idle.sendall(b"CON")
        receipt = tunnel.close()
        idle.close()
        self.assertTrue(receipt["closed"])
        self.assertFalse(tunnel.connections)
        self.assertEqual(receipt["events"], [])

    def test_allowed_tunnel_preserves_bytes_and_shutdown_settles_both_sides(self):
        upstream, remote = socket.socketpair()
        original_connect = socket.create_connection
        tunnel = ModelTunnel(("127.0.0.1", 0), "127.0.0.0/8").start()
        client = original_connect(tunnel.server.server_address)
        def connect(address, **kwargs):
            self.assertEqual(address, ("chatgpt.com", 443))
            return upstream
        try:
            with patch("p1_model_tunnel.socket.create_connection", connect):
                client.sendall(b"CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com\r\n\r\n")
                header = b""
                while b"\r\n\r\n" not in header:
                    header += client.recv(1024)
                self.assertIn(b"200", header)
                client.sendall(b"unchanged encrypted request")
                self.assertEqual(remote.recv(100), b"unchanged encrypted request")
                remote.sendall(b"unchanged encrypted response")
                self.assertEqual(client.recv(100), b"unchanged encrypted response")
                receipt = tunnel.close()
            self.assertTrue(receipt["closed"])
            self.assertFalse(tunnel.connections)
            self.assertEqual(remote.recv(1), b"")
        finally:
            client.close(); remote.close()

    def test_memory_binding_keeps_client_options_and_rejects_proxy_bypass(self):
        import httpx
        class Wire(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                return httpx.Response(200)
        async def check():
            client = httpx.AsyncClient(timeout=123, headers={"native": "retained"}, trust_env=False)
            result = {"calls": []}
            original_timeout, original_headers = client.timeout, client.headers
            with patch("p1_eva2_native_worker.subscription_transport", return_value=Wire()):
                old, bound = bind_memory_client(client, httpx, SimpleNamespace(), result, SimpleNamespace(), object)
            self.assertIs(client.timeout, original_timeout)
            self.assertIs(client.headers, original_headers)
            self.assertIs(client._transport, bound)
            await old.aclose(); await client.aclose()
            other = httpx.AsyncClient(trust_env=False)
            other._mounts = {object(): Wire()}
            with self.assertRaisesRegex(ValueError, "unbound transport mount"):
                bind_memory_client(other, httpx, SimpleNamespace(), result, SimpleNamespace(), object)
            await other.aclose()
        asyncio.run(check())

    def test_native_protocol_first_match_is_counted_without_changing_result(self):
        def call(name, arguments):
            return SimpleNamespace(name=name, arguments=arguments)
        valid = (None, [call("unrelated", {}), call("structured_response", {}), call("structured_response", {"extra": True})])
        result = {}
        self.assertIs(observe_memory_protocol(valid, result), valid)
        self.assertEqual(len(result["protocol_consumptions"]), 1)
        invalid = ("fallback", [call("structured_response", "invalid"), call("structured_response", {})])
        self.assertIs(observe_memory_protocol(invalid, result), invalid)
        self.assertEqual(len(result["protocol_consumptions"]), 1)
        observe_memory_protocol(("text", []), result)
        self.assertEqual(len(result["protocol_consumptions"]), 1)

    def test_forbidden_action_stops_before_effect(self):
        calls = []
        ledger = SimpleNamespace(stop_async_attempts=lambda reason: calls.append(reason))
        result = {}
        with self.assertRaises(SampleStopped):
            block_action(result, ledger, "research_current_sources")
        self.assertEqual(len(calls), 1)
        self.assertFalse(result["action_attempts"][0]["dispatched"])


class NativeStopBoundaryTests(unittest.TestCase):
    def test_failed_sse_settles_unknown_and_native_stream_terminates_without_retry(self):
        from queue import SimpleQueue, Empty
        from eva.adapters.api import dashboard_conversation as api
        from eva.adapters.model.openai_codex_provider import _RawSseUsageAttestor
        from cp001_accounting import ProviderSampleLedger
        from cp001_http_accounting import ResponsesSampleTransport, httpx

        # Exercise the real producer/consumer, but expose its missing terminal
        # deterministically instead of leaving a blocking queue thread behind.
        class ObservedQueue(SimpleQueue):
            def get(self):
                return self.get_nowait()
        producer_errors = []
        class InlineThread:
            def __init__(self, *, target, **kwargs):
                self.target = target
            def start(self):
                try:
                    self.target()
                except BaseException as error:
                    producer_errors.append(error)

        for translated in (False, True):
            with self.subTest(translated=translated):
                ledger = ProviderSampleLedger("public-synthetic", {
                    "providerAttempts": 2, "providerInputTokens": 24000,
                    "providerOutputTokens": 4096, "wallSeconds": 120,
                    "toolCalls": 4, "externalActions": 0},
                    {"contact": ("gpt-6-sol", "none")})
                dispatches, closed = [], []
                class FailedSse(httpx.AsyncByteStream):
                    async def __aiter__(self):
                        yield b'data: {"type":"response.failed"}\n\n'
                    async def aclose(self):
                        closed.append(True)
                def dispatch(request):
                    dispatches.append(True)
                    return httpx.Response(200, stream=FailedSse(), request=request)
                transport = ResponsesSampleTransport(httpx.MockTransport(dispatch), ledger,
                    role="contact", endpoint="https://chatgpt.com/backend-api/codex/responses",
                    attestor_factory=_RawSseUsageAttestor)
                async def consume():
                    request = httpx.Request("POST", "https://chatgpt.com/backend-api/codex/responses",
                        json={"model": "gpt-6-sol", "reasoning": {"effort": "none"}})
                    response = await transport.handle_async_request(request)
                    async for _ in response.aiter_bytes():
                        pass
                def respond(*args):
                    return asyncio.run(consume())
                runtime = SimpleNamespace(respond=native_response_boundary(respond) if translated else respond)
                async def receive():
                    response = api.conversation_stream_response(runtime, SimpleNamespace(), ())
                    return [json.loads(chunk) async for chunk in response.body_iterator]
                with patch.object(api, "SimpleQueue", ObservedQueue), patch.object(api, "Thread", InlineThread),\
                        patch.object(api, "_record_stream_failure"):
                    if translated:
                        self.assertEqual(asyncio.run(receive()), [{"contract": "EvaTextStreamError/v1"}])
                    else:
                        with self.assertRaises(Empty):
                            asyncio.run(receive())
                        self.assertIsInstance(producer_errors[-1], SampleStopped)
                self.assertEqual(len(dispatches), 1)
                self.assertTrue(closed)
                self.assertEqual(ledger.attempts[0]["status"], "comparison_stream_closed_usage_unknown")
                self.assertIsNone(ledger.attempts[0]["usage"])
                self.assertTrue(ledger.stopped)
                with self.assertRaises(SampleStopped):
                    asyncio.run(consume())
                self.assertEqual(len(dispatches), 1)
                asyncio.run(transport.aclose())

        # The real runtime instance accepts/restores this benchmark-only patch;
        # successful calls retain exact arguments and return-object identity.
        from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
        runtime = PostgresHindsightMemoryRuntime.__new__(PostgresHindsightMemoryRuntime)
        original = runtime.respond
        positional, keyword, returned = object(), object(), object()
        calls = []
        def successful(*args, **kwargs):
            calls.append((args, kwargs))
            return returned
        with patch.object(runtime, "respond", native_response_boundary(successful)):
            self.assertIs(runtime.respond(positional, recent_messages=keyword), returned)
        self.assertIs(calls[0][0][0], positional)
        self.assertIs(calls[0][1]["recent_messages"], keyword)
        self.assertEqual(runtime.respond, original)
        self.assertNotIn("respond", runtime.__dict__)

        # The boundary does not turn unrelated fatal control flow into an HTTP error.
        class OtherFatal(BaseException):
            pass
        def fatal():
            raise OtherFatal()
        with self.assertRaises(OtherFatal):
            native_response_boundary(fatal)()
