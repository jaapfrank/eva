"""Disposable native Eva2 services with sample-owned provider accounting.

Run only with fresh operator-owned databases and a dedicated internal network.
Both native application factories, the conversation engine and background workers
remain intact. Synthetic responses remain the default. Subscription mode retains native credentials
and requires an explicit model-only proxy. This worker does not authorize a run.
"""
import argparse
import asyncio
import base64
from contextlib import ExitStack
from contextvars import ContextVar
from dataclasses import asdict
import hashlib
import inspect
import json
import os
import signal
from pathlib import Path
import time
from unittest.mock import patch

from cp001_process_accounting import ProcessSampleLedger
from cp001_accounting import SampleStopped

ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"
FACT = "Fixture visitor prefers chamomile tea."


def subscription_transport(http, args, result):
    """Preserve wire bytes; the sample proxy exposes only the authorized endpoint."""
    if not args.provider_proxy:
        raise ValueError("subscription mode requires the isolated model-only proxy")
    inner = http.AsyncHTTPTransport(proxy=args.provider_proxy, retries=0)

    class Network(http.AsyncBaseTransport):
        async def handle_async_request(self, request):
            payload = json.loads(await request.aread())
            result["calls"].append({"model": payload.get("model"),
                "effort": payload.get("reasoning", {}).get("effort"),
                "kind": "subscription_dispatch"})
            # Only ResponsesSampleTransport may call this transport, after its
            # endpoint, role and parent-ledger admission checks.
            return await inner.handle_async_request(request)

        async def aclose(self):
            await inner.aclose()

    return Network()


def bind_memory_client(client, http, args, result, ledger, attestor):
    """Replace only native transport; retain its client options and auth manager."""
    from cp001_http_accounting import ResponsesSampleTransport
    if any(t is not None for t in client._mounts.values()):
        raise ValueError("native memory client contains an unbound transport mount")
    original = client._transport
    transport = ResponsesSampleTransport(subscription_transport(http, args, result),
        ledger, role="helper", endpoint=ENDPOINT, attestor_factory=attestor)
    client._transport = transport
    return original, transport


def observe_memory_protocol(parsed, result):
    """Mirror native first-match selection without replacing parser results."""
    _text, tool_calls = parsed
    for call in tool_calls:
        if call.name == "structured_response":
            if isinstance(call.arguments, dict):
                result.setdefault("protocol_consumptions", []).append({
                    "name": call.name, "kind": "native_structured_output",
                    "external_effect": False})
            break
    return parsed


def block_action(result, ledger, name):
    """C01/C04 allow dialogue and native journaling, never a requested action."""
    result.setdefault("action_attempts", []).append({"name": name, "dispatched": False})
    ledger.stop_async_attempts("unexpected action in fixed conversation case")
    raise SampleStopped("unexpected action in fixed conversation case")


def native_response_boundary(respond):
    """Expose a settled benchmark stop through the native HTTP error contract.

    SampleStopped remains a BaseException inside SDK/ledger control to prevent
    native retries. Translate only after the complete native respond stack has
    unwound, including its stream ownership cleanup. Never clear the ledger or
    convert unknown usage into success.
    """
    def bound(*args, **kwargs):
        try:
            return respond(*args, **kwargs)
        except SampleStopped as error:
            raise RuntimeError("comparison sample stopped") from error
    return bound


def source_hash(cls):
    p = Path(inspect.getfile(cls))
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()}


def serve_core(args, ledger, result):
    import httpx2
    import uvicorn
    from eva.bootstrap import runtime as composition
    from eva.adapters.model import pydantic_conversation as model_module
    from eva.adapters.model.openai_codex_provider import OpenAICodexProvider, _RawSseUsageAttestor
    from cp001_http_accounting import ResponsesSampleTransport

    result["native_sources"].update(source_hash(composition.PostgresHindsightMemoryRuntime))
    result["native_sources"].update(source_hash(OpenAICodexProvider))
    transports, runtimes = [], []
    original_factory = composition.create_app

    def capture_runtime(runtime, *a, **kw):
        runtimes.append(runtime)
        stack.enter_context(patch.object(
            runtime, "respond", native_response_boundary(runtime.respond)))
        return original_factory(runtime, *a, **kw)

    class Body(httpx2.AsyncByteStream):
        def __init__(self, payload):
            self.payload = payload
        async def __aiter__(self):
            text = "Ik onthoud dat je kamillethee lekker vindt."
            response = {"id": "resp_p1_fixture", "object": "response", "created_at": 1777111200.0,
                "status": "completed", "model": self.payload["model"], "output": [
                    {"id": "msg_p1_fixture", "type": "message", "status": "completed", "role": "assistant",
                     "content": [{"type": "output_text", "text": text, "annotations": [], "logprobs": []}]}],
                "parallel_tool_calls": False, "tool_choice": "none", "tools": [],
                "usage": {"input_tokens": 17, "output_tokens": 7, "total_tokens": 24,
                    "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}
            created = {**response, "status": "in_progress", "output": []}
            for event in ({"type": "response.created", "response": created},
                          {"type": "response.output_text.delta", "item_id": "msg_p1_fixture",
                           "output_index": 0, "content_index": 0, "delta": text, "logprobs": []},
                          {"type": "response.completed", "response": response}):
                yield ("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode()
            yield b"data: [DONE]\n\n"
        async def aclose(self):
            pass

    class Network(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            payload = json.loads(await request.aread())
            result["calls"].append({"model": payload.get("model"), "effort": payload.get("reasoning", {}).get("effort")})
            return httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=Body(payload), request=request)

    class Provider(OpenAICodexProvider):
        def __init__(self, settings, *, credential_loader):
            # Role follows the actual wire model at admission, not the fixture answer.
            role = "contact" if settings.model_name == "gpt-6-sol" else "helper"
            network = subscription_transport(httpx2, args, result) if args.provider_mode == "subscription" else Network()
            transport = ResponsesSampleTransport(network, ledger, role=role,
                endpoint=ENDPOINT, attestor_factory=_RawSseUsageAttestor)
            transports.append(transport)
            loader = credential_loader if args.provider_mode == "subscription" else lambda: ("offline-fixture", "offline-fixture")
            super().__init__(settings, credential_loader=loader,
                network_transport=transport)

    def deny_action(self, turn, call, **kwargs):
        return block_action(result, ledger, call.name)

    with ExitStack() as stack:
        if args.calendar_file:
            from ev003_python_calendar import protect_native_auth
            protect_native_auth(stack, core_model=model_module.PydanticConversationModel)
        stack.enter_context(patch.object(model_module, "OpenAICodexProvider", Provider))
        stack.enter_context(patch.object(composition, "create_app", capture_runtime))
        if args.provider_mode == "subscription":
            # Keep the normal advertised catalog and planning; observe and stop
            # an actual attempted dispatch before any effect can occur.
            from eva.adapters.postgres.conversation_actions import PostgresConversationActions
            stack.enter_context(patch.object(PostgresConversationActions, "execute", deny_action))
            stack.enter_context(patch.object(PostgresConversationActions, "execute_step", deny_action))
        app = composition.create_runtime_app()
        server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=args.port, log_level="warning", access_log=False))
        try:
            server.run()
        finally:
            active = sum(t.active_responses for t in transports)
            workers = []
            for runtime in runtimes:
                cold = runtime._cold_path
                workers.append({"thread_alive": bool(cold._thread and cold._thread.is_alive()),
                    "service_thread_alive": bool(cold._service_thread and cold._service_thread.is_alive()),
                    "last_error": cold._last_error})
                runtime._engine.dispose()
            result["active_responses"] = active
            result["workers_after_stop"] = workers
            result["local_settled"] = not active and bool(workers) and all(
                not x["thread_alive"] and not x["service_thread_alive"] for x in workers)


async def serve_memory(args, ledger, result):
    import asyncpg
    import httpx
    import uvicorn
    from hindsight_api import MemoryEngine
    from hindsight_api.api.http import create_app
    from hindsight_api.engine.providers.codex_llm import CodexLLM
    from hindsight_api.worker.poller import WorkerPoller
    from hindsight_api.worker.stage import get_stage
    from cp001_http_accounting import ResponsesSampleTransport
    from p1_hindsight_lifecycle_probe import bound_attestor, sse, Body

    attestor, loader_hash = bound_attestor(args.eva_source, args.eva_sha256)
    result["attestor_loader_sha256"] = loader_hash
    for cls in (MemoryEngine, CodexLLM, WorkerPoller):
        result["native_sources"].update(source_hash(cls))
    dsn = args.database_file.read_text().strip()
    reader = await asyncpg.connect(dsn)
    existing = await reader.fetchval("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('banks','memory_units','async_operations')")
    if existing:
        if not args.existing_empty_schema:
            await reader.close()
            raise ValueError("fresh memory database required")
        # Reuse only schema initialization retained after a pre-request setup error.
        # Any native bank, document, fact or operation makes this state ineligible.
        for table in ("banks", "documents", "memory_units", "async_operations"):
            if await reader.fetchval("SELECT count(*) FROM " + table):
                await reader.close()
                raise ValueError("retained schema contains native sample state")
        result["reused_empty_memory_schema"] = True
    schema_context = ContextVar("p1_native_schema", default=None)
    scope_context = ContextVar("p1_native_scope", default=None)
    clients, unused_clients, transports, pollers = [], [], [], []
    original_init, original_call, original_poller = CodexLLM.__init__, CodexLLM.call, WorkerPoller.__init__
    signature = inspect.signature(original_call)
    original_parse = CodexLLM._parse_sse_tool_stream

    async def respond(request):
        payload = json.loads(request.content)
        schema = schema_context.get()
        if schema is None:
            raise RuntimeError("unsupported native unstructured fixture call")
        fields = schema.model_fields
        if "facts" in fields:
            value = {"facts": [{"what": FACT, "when": "N/A", "where": "N/A", "who": "Fixture visitor",
                "why": "Personal preference", "fact_type": "world", "fact_kind": "conversation",
                "entities": ["Fixture visitor"], "occurred_start": None, "occurred_end": None, "causal_relations": []}]}
        elif set(("creates", "updates", "deletes")).issubset(fields):
            rows = await reader.fetch("SELECT id FROM memory_units WHERE fact_type='world'")
            if not rows:
                raise RuntimeError("consolidation lacks persisted source facts")
            value = {"creates": [{"text": FACT, "source_fact_ids": [str(x["id"]) for x in rows],
                "reason": "Preserve supplied preference."}], "updates": [], "deletes": []}
        else:
            raise RuntimeError("unsupported native fixture schema: " + schema.__name__)
        value = schema.model_validate(value).model_dump(mode="json")
        result["calls"].append({"schema": schema.__name__, "scope": scope_context.get(), "stage": get_stage(),
            "model": payload.get("model"), "effort": payload.get("reasoning", {}).get("effort")})
        forced = isinstance(payload.get("tool_choice"), dict) and payload["tool_choice"].get("name") == "structured_response"
        return httpx.Response(200, stream=Body(sse(value, forced)), headers={"content-type": "text/event-stream"})

    def initialize(self, *a, **kw):
        original_init(self, *a, **kw)
        if args.provider_mode == "subscription":
            # Keep the native client timeout, headers, authentication manager and
            # parser. No mounted proxy may silently bypass the observed transport.
            original_transport, transport = bind_memory_client(
                self._client, httpx, args, result, ledger, attestor)
            unused_clients.append(original_transport)
        else:
            unused_clients.append(self._client)
            transport = ResponsesSampleTransport(httpx.MockTransport(respond), ledger, role="helper",
                endpoint=ENDPOINT, attestor_factory=attestor)
            self._client = httpx.AsyncClient(transport=transport)
        clients.append(self._client)
        transports.append(transport)

    async def observed_call(self, *a, **kw):
        bound = signature.bind(self, *a, **kw)
        token = schema_context.set(bound.arguments.get("response_format"))
        scope = scope_context.set(bound.arguments.get("scope", "memory"))
        try:
            return await original_call(self, *a, **kw)
        finally:
            schema_context.reset(token)
            scope_context.reset(scope)

    async def observed_parse(self, response):
        return observe_memory_protocol(await original_parse(self, response), result)

    def observe_poller(self, *a, **kw):
        original_poller(self, *a, **kw)
        pollers.append(self)

    token = "fixture." + base64.urlsafe_b64encode(json.dumps({"exp": 4102444800}).encode()).decode().rstrip("=") + ".fixture"
    try:
        with ExitStack() as stack:
            if args.calendar_file:
                from hindsight_api.engine.providers import codex_auth
                from ev003_python_calendar import protect_native_auth
                protect_native_auth(stack, memory_auth_module=codex_auth)
            if args.provider_mode == "synthetic":
                stack.enter_context(patch.object(CodexLLM, "_load_codex_auth", return_value=(token, "offline-fixture")))
                stack.enter_context(patch.object(CodexLLM, "_load_codex_refresh_token", return_value=None))
            stack.enter_context(patch.object(CodexLLM, "__init__", initialize))
            stack.enter_context(patch.object(CodexLLM, "call", observed_call))
            stack.enter_context(patch.object(CodexLLM, "_parse_sse_tool_stream", observed_parse))
            stack.enter_context(patch.object(WorkerPoller, "__init__", observe_poller))
            memory = MemoryEngine(db_url=dsn, skip_llm_verification=True)
            app = create_app(memory)
            server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=args.port, log_level="warning", access_log=False))
            await server.serve()
    finally:
        for client in clients + unused_clients:
            try:
                await client.aclose()
            except BaseException as error:
                result["errors"].append({"phase": "client_close", "type": type(error).__name__})
        workers = []
        for poller in pollers:
            async with poller._in_flight_lock:
                workers.append({"in_flight": poller._in_flight_count,
                    "unfinished_tasks": sum(not i.bg_task.done() for i in poller._active_tasks.values())})
        result["workers_after_stop"] = workers
        result["active_responses"] = sum(t.active_responses for t in transports)
        result["provider_instances"] = len(clients)
        result["local_settled"] = bool(workers) and not result["active_responses"] and all(
            not x["in_flight"] and not x["unfinished_tasks"] for x in workers) and not result["errors"]
        await reader.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("core", "memory"))
    parser.add_argument("--control-file", required=True, type=Path)
    parser.add_argument("--database-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--port", default=8080, type=int)
    parser.add_argument("--eva-source", type=Path)
    parser.add_argument("--eva-sha256")
    parser.add_argument("--existing-empty-schema", action="store_true")
    parser.add_argument("--provider-mode", choices=("synthetic", "subscription"), default="synthetic")
    parser.add_argument("--provider-proxy", help="Sample-owned HTTP CONNECT proxy; subscription mode only")
    parser.add_argument("--calendar-file", type=Path)
    parser.add_argument("--calendar-receipt", type=Path)
    args = parser.parse_args()
    if args.provider_mode == "subscription" and not args.provider_proxy:
        parser.error("subscription mode requires the isolated model-only proxy")
    if bool(args.calendar_file) != bool(args.calendar_receipt):
        parser.error("calendar control and private receipt must be supplied together")
    if args.output.exists():
        parser.error("output exists; preserve previous evidence")
    credentials = json.loads(args.control_file.read_text())
    ledger = ProcessSampleLedger(**credentials)
    result = {"contract": "P1NativeEva2Worker/v1", "kind": args.kind, "native_sources": {},
        "provider_mode": args.provider_mode, "upstream_model_calls": 0, "action_attempts": [], "protocol_consumptions": [], "calls": [], "errors": [], "local_settled": False}
    started = time.monotonic()
    calendar = None
    previous_signal_handler = None
    try:
        if args.calendar_file:
            from ev003_python_calendar import ScenarioCalendar
            calendar = ScenarioCalendar(args.calendar_file)
            def refresh_calendar(*_):
                state = calendar.refresh()
                temporary = args.calendar_receipt.with_suffix(".tmp")
                temporary.write_text(json.dumps(state) + "\n")
                temporary.chmod(0o600)
                temporary.replace(args.calendar_receipt)
            refresh_calendar()
            previous_signal_handler = signal.signal(signal.SIGUSR1, refresh_calendar)
            result["calendar"] = {"enabled": True, "refresh": "SIGUSR1_after_native_settlement"}
        if args.kind == "core":
            serve_core(args, ledger, result)
        else:
            asyncio.run(serve_memory(args, ledger, result))
    except BaseException as error:
        import traceback
        traceback.print_exc()
        result["errors"].append({"phase": "native_worker", "type": type(error).__name__})
    finally:
        result["upstream_model_calls"] = sum(c.get("kind") == "subscription_dispatch" for c in result["calls"])
        result["local_ledger"] = ledger.report()
        result["control_close_acknowledged"] = ledger.close(settled=result["local_settled"] and not result["errors"])
        result["ledger_after_close"] = ledger.report()
        if not result["control_close_acknowledged"]:
            result["errors"].append({"phase": "control_close", "type": "receipt_not_acknowledged"})
        result["seconds"] = time.monotonic() - started
        if calendar is not None:
            if previous_signal_handler is not None:
                signal.signal(signal.SIGUSR1, previous_signal_handler)
            result.setdefault("calendar", {})["last_generation"] = (
                calendar._state["generation"] if calendar._state else None)
            calendar.restore()
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    return 0 if result["local_settled"] and not result["errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
