"""Disposable native Eva2 services with synthetic provider HTTP and shared accounting.

Run only with fresh operator-owned databases and a dedicated internal network.
Both native application factories, the conversation engine and background workers
remain intact. This worker neither launches containers nor authorizes live calls.
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
from pathlib import Path
import time
from unittest.mock import patch

from cp001_process_accounting import ProcessSampleLedger

ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"
FACT = "Fixture visitor prefers chamomile tea."


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
            transport = ResponsesSampleTransport(Network(), ledger, role=role,
                endpoint=ENDPOINT, attestor_factory=_RawSseUsageAttestor)
            transports.append(transport)
            super().__init__(settings, credential_loader=lambda: ("offline-fixture", "offline-fixture"),
                network_transport=transport)

    with patch.object(model_module, "OpenAICodexProvider", Provider), \
         patch.object(composition, "create_app", capture_runtime):
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

    def observe_poller(self, *a, **kw):
        original_poller(self, *a, **kw)
        pollers.append(self)

    token = "fixture." + base64.urlsafe_b64encode(json.dumps({"exp": 4102444800}).encode()).decode().rstrip("=") + ".fixture"
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(CodexLLM, "_load_codex_auth", return_value=(token, "offline-fixture")))
            stack.enter_context(patch.object(CodexLLM, "_load_codex_refresh_token", return_value=None))
            stack.enter_context(patch.object(CodexLLM, "__init__", initialize))
            stack.enter_context(patch.object(CodexLLM, "call", observed_call))
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
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; preserve previous evidence")
    credentials = json.loads(args.control_file.read_text())
    ledger = ProcessSampleLedger(**credentials)
    result = {"contract": "P1NativeEva2Worker/v1", "kind": args.kind, "native_sources": {},
        "upstream_model_calls": 0, "calls": [], "errors": [], "local_settled": False}
    started = time.monotonic()
    try:
        if args.kind == "core":
            serve_core(args, ledger, result)
        else:
            asyncio.run(serve_memory(args, ledger, result))
    except BaseException as error:
        import traceback
        traceback.print_exc()
        result["errors"].append({"phase": "native_worker", "type": type(error).__name__})
    finally:
        result["local_ledger"] = ledger.report()
        result["control_close_acknowledged"] = ledger.close(settled=result["local_settled"] and not result["errors"])
        result["ledger_after_close"] = ledger.report()
        if not result["control_close_acknowledged"]:
            result["errors"].append({"phase": "control_close", "type": "receipt_not_acknowledged"})
        result["seconds"] = time.monotonic() - started
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    return 0 if result["local_settled"] and not result["errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
