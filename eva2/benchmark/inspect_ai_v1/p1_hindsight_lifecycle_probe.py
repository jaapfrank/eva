"""Disposable native Hindsight retain/broker probe; synthetic HTTP, real storage.

Run only in the pinned Hindsight image with a new empty vector database and
existing local model resources. No production credentials or banks are used.
"""
import argparse
import ast
import asyncio
import base64
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import time
import types
from unittest.mock import patch

import asyncpg
import httpx

from cp001_accounting import ProviderSampleLedger, SampleStopped
from cp001_http_accounting import ResponsesSampleTransport

ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"
FACT = "Fixture visitor prefers chamomile tea."
SCHEMA = ContextVar("probe_native_schema", default=None)
SCOPE = ContextVar("probe_native_scope", default=None)


def bound_attestor(source, expected):
    # Reuse the existing loader without importing its global socket denial.
    loader = Path(__file__).with_name("p1_hindsight_accounting_probe.py")
    tree = ast.parse(loader.read_bytes())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "load_attestor")
    scope = dict(ast=ast, hashlib=hashlib, types=types, sys=sys, dataclass=dataclass, json=json)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(loader), "exec"), scope)
    return scope["load_attestor"](source, expected), hashlib.sha256(loader.read_bytes()).hexdigest()


class Body(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data = data
    async def __aiter__(self):
        yield self.data[:23]
        yield self.data[23:]
    async def aclose(self):
        pass


def sse(value, forced):
    text = json.dumps(value)
    if forced:
        event = {"type": "response.output_item.done", "item": {
            "type": "function_call", "status": "completed", "name": "structured_response",
            "call_id": "fixture-structured", "arguments": text}}
    else:
        event = {"type": "response.text.delta", "delta": text}
    terminal = {"type": "response.completed", "response": {"status": "completed", "error": None,
        "usage": {"input_tokens": 31, "output_tokens": 7, "total_tokens": 38}}}
    return (f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
            f"event: response.completed\ndata: {json.dumps(terminal)}\n\ndata: [DONE]\n\n").encode()


async def probe(args):
    # All configuration is set before native package imports/config caching.
    if not args.bank.startswith("p1-offline-"):
        raise ValueError("a dedicated p1-offline- bank is required")
    if not args.database_file.is_absolute():
        raise ValueError("explicit private disposable database file required")
    dsn = args.database_file.read_text().strip()
    os.environ.update(HINDSIGHT_API_LLM_PROVIDER="openai-codex",
        HINDSIGHT_API_LLM_MODEL="gpt-6-astra", HINDSIGHT_API_LLM_REASONING_EFFORT="medium",
        HINDSIGHT_API_RETAIN_LLM_PROVIDER="openai-codex",
        HINDSIGHT_API_RETAIN_LLM_MODEL="gpt-6-astra", HINDSIGHT_API_RETAIN_LLM_REASONING_EFFORT="medium",
        HINDSIGHT_API_REFLECT_LLM_PROVIDER="openai-codex",
        HINDSIGHT_API_REFLECT_LLM_MODEL="gpt-6-astra", HINDSIGHT_API_REFLECT_LLM_REASONING_EFFORT="medium",
        HINDSIGHT_API_CONSOLIDATION_LLM_PROVIDER="openai-codex",
        HINDSIGHT_API_CONSOLIDATION_LLM_MODEL="gpt-6-astra",
        HINDSIGHT_API_CONSOLIDATION_LLM_REASONING_EFFORT="medium",
        HINDSIGHT_API_SKIP_LLM_VERIFICATION="true", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
        CODEX_HOME=str(args.output.parent / "unused-synthetic-auth"))
    from hindsight_api import MemoryEngine
    from hindsight_api.models import RequestContext
    from hindsight_api.config import get_config
    from hindsight_api.engine.providers.codex_llm import CodexLLM
    from hindsight_api.worker.poller import WorkerPoller
    from hindsight_api.worker.stage import get_stage

    plan = json.loads(Path(__file__).with_name("p1_participants_20260929.json").read_text())
    limits = plan["developmentPlan"]["perSampleLimits"]
    ledger = ProviderSampleLedger(args.bank, limits, {"helper": ("gpt-6-astra", "medium")})
    attestor, loader_hash = bound_attestor(args.eva_source, args.eva_sha256)
    clients, transports, unused_clients, calls = [], [], [], []
    original_init, original_call = CodexLLM.__init__, CodexLLM.call
    signature = inspect.signature(original_call)
    result = {"contract": "P1HindsightLifecycleProbe/v1", "upstream_model_calls": 0,
        "synthetic_http_usage": True, "native_usage_is_estimated": True,
        "loader_sha256": loader_hash, "bank": args.bank, "limits": limits,
        "statuses": {}, "errors": [], "calls": calls,
        "native_source_sha256": {str(Path(inspect.getfile(cls))): hashlib.sha256(Path(inspect.getfile(cls)).read_bytes()).hexdigest()
            for cls in (CodexLLM, MemoryEngine, WorkerPoller)}, "eva_source_sha256": args.eva_sha256}
    reader = await asyncpg.connect(dsn)
    # The operator must supply a fresh database, never a private existing schema.
    existing = await reader.fetchval("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('banks','memory_units','async_operations')")
    if existing:
        await reader.close()
        raise ValueError("database already has Hindsight state; refusing to read it")

    async def respond(request):
        payload = json.loads(request.content)
        schema = SCHEMA.get()
        if schema is None:
            raise RuntimeError("native call has no supported structured fixture schema")
        fields = schema.model_fields
        if "facts" in fields:
            value = {"facts": [{"what": FACT, "when": "N/A", "where": "N/A",
                "who": "Fixture visitor", "why": "Personal preference", "fact_type": "world",
                "fact_kind": "conversation", "entities": ["Fixture visitor"],
                "occurred_start": None, "occurred_end": None, "causal_relations": []}]}
        elif set(("creates", "updates", "deletes")).issubset(fields):
            ids = await reader.fetch("SELECT id FROM memory_units WHERE bank_id=$1 AND fact_type='world'", args.bank)
            if not ids:
                raise RuntimeError("consolidation has no independently persisted source facts")
            value = {"creates": [{"text": FACT, "source_fact_ids": [str(row["id"]) for row in ids],
                "reason": "Preserve the supplied preference."}], "updates": [], "deletes": []}
        else:
            raise RuntimeError("unsupported native fixture schema: " + schema.__name__)
        value = schema.model_validate(value).model_dump(mode="json")
        forced = isinstance(payload.get("tool_choice"), dict) and payload["tool_choice"].get("name") == "structured_response"
        calls.append({"schema": schema.__name__, "scope": SCOPE.get(), "stage": get_stage(), "model": payload.get("model"),
            "effort": payload.get("reasoning", {}).get("effort"), "forced_tool": forced})
        return httpx.Response(200, stream=Body(sse(value, forced)), headers={"content-type": "text/event-stream"})

    def initialize_provider(self, *a, **kw):
        original_init(self, *a, **kw)
        unused_clients.append(self._client)
        transport = ResponsesSampleTransport(httpx.MockTransport(respond), ledger, role="helper",
            endpoint=ENDPOINT, attestor_factory=attestor)
        self._client = httpx.AsyncClient(transport=transport)
        clients.append(self._client)
        transports.append(transport)

    async def observed_call(self, *a, **kw):
        bound = signature.bind(self, *a, **kw)
        token = SCHEMA.set(bound.arguments.get("response_format"))
        scope_token = SCOPE.set(bound.arguments.get("scope", "memory"))
        try:
            return await original_call(self, *a, **kw)
        finally:
            SCHEMA.reset(token)
            SCOPE.reset(scope_token)

    async def snapshot():
        memories = await reader.fetch("SELECT id::text, fact_type, text, source_memory_ids::text[] FROM memory_units WHERE bank_id=$1 ORDER BY id", args.bank)
        operations = await reader.fetch("SELECT operation_id::text, operation_type, status FROM async_operations WHERE bank_id=$1 ORDER BY operation_id", args.bank)
        return {"memories": [dict(row) for row in memories], "operations": [dict(row) for row in operations],
            "pending_running": sum(row["status"] in ("pending", "running", "processing") for row in operations)}

    token = "fixture." + base64.urlsafe_b64encode(json.dumps({"exp": 4102444800}).encode()).decode().rstrip("=") + ".fixture"
    engine = poller = poller_task = None
    started = time.monotonic()
    with patch.object(CodexLLM, "_load_codex_auth", return_value=(token, "offline-fixture")), \
         patch.object(CodexLLM, "_load_codex_refresh_token", return_value=None), \
         patch.object(CodexLLM, "__init__", initialize_provider), \
         patch.object(CodexLLM, "call", observed_call):
        try:
            engine = MemoryEngine(db_url=dsn, skip_llm_verification=True)
            await engine.initialize()
            result["statuses"]["initialized"] = True
            config = get_config()
            poller = WorkerPoller(backend=engine._backend, worker_id=args.bank,
                executor=engine.execute_task, tenant_extension=engine._tenant_extension,
                poll_interval_ms=config.worker_poll_interval_ms, max_slots=config.worker_max_slots,
                slot_reservations=config.worker_slot_reservations,
                consolidation_bank_priority=config.worker_consolidation_bank_priority or None)
            poller_task = asyncio.create_task(poller.run())
            result["preparation_seconds"] = time.monotonic() - started
            async with asyncio.timeout(limits["wallSeconds"]):
                ids = await engine.retain_async(args.bank, FACT, request_context=RequestContext())
                result["statuses"]["retain_ids"] = [str(value) for value in ids]
                while True:
                    current = await snapshot()
                    result["readback_before_stop"] = current
                    if ledger.stopped:
                        result["statuses"]["pipeline"] = "ledger_refused"
                        break
                    if poller_task.done():
                        await poller_task
                        raise RuntimeError("native worker ended before lifecycle completion")
                    async with poller._in_flight_lock:
                        in_flight = poller._in_flight_count
                    if not current["pending_running"] and not in_flight:
                        result["statuses"]["pipeline"] = "terminal"
                        break
                    await asyncio.sleep(config.worker_poll_interval_ms / 1000)
        except BaseException as error:
            result["errors"].append({"phase": "pipeline", "type": type(error).__name__,
                "detail": str(error) if isinstance(error, SampleStopped) else "native lifecycle failed; see private diagnostic log"})
            import traceback
            traceback.print_exc()
        finally:
            ledger.stop_async_attempts("offline lifecycle probe finishing")
            if poller is not None:
                try:
                    await poller.shutdown_graceful()
                except BaseException as error:
                    result["errors"].append({"phase": "poller_shutdown", "type": type(error).__name__})
            if poller_task is not None:
                poller_task.cancel()
                try:
                    await poller_task
                except asyncio.CancelledError:
                    pass
                except BaseException as error:
                    result["errors"].append({"phase": "poller_settlement", "type": type(error).__name__})
            if poller is not None:
                async with poller._in_flight_lock:
                    result["worker_after_stop"] = {"in_flight": poller._in_flight_count,
                        "unfinished_tasks": sum(not info.bg_task.done() for info in poller._active_tasks.values())}
            if engine is not None:
                try:
                    await engine.close()
                except BaseException as error:
                    result["errors"].append({"phase": "engine_close", "type": type(error).__name__})
            for client in clients + unused_clients:
                try:
                    await client.aclose()
                except BaseException as error:
                    result["errors"].append({"phase": "client_close", "type": type(error).__name__})
            try:
                result["readback_after_stop"] = await snapshot()
            except Exception as error:
                result["errors"].append({"phase": "readback", "type": type(error).__name__})
            await reader.close()
    result["ledger"] = ledger.report()
    result["provider_instances"] = len(clients)
    result["active_responses"] = sum(t.active_responses for t in transports)
    result["local_quiescence"] = (not result["ledger"]["active_async_attempts"] and not result["active_responses"]
        and (poller_task is None or poller_task.done())
        and result.get("worker_after_stop", {"in_flight": 0, "unfinished_tasks": 0}) == {"in_flight": 0, "unfinished_tasks": 0}
        and not any(e["phase"].endswith(("close", "shutdown", "settlement")) for e in result["errors"]))
    result["seconds"] = time.monotonic() - started
    final = result.get("readback_after_stop", {})
    retained_ids = set(result["statuses"].get("retain_ids", []))
    facts = {row["id"] for row in final.get("memories", [])
        if row["fact_type"] == "world" and row["id"] in retained_ids and FACT in row["text"]}
    observations = [row["id"] for row in final.get("memories", [])
        if row["fact_type"] == "observation" and FACT in row["text"]
        and facts.intersection(row["source_memory_ids"] or [])]
    consolidation = [row["operation_id"] for row in final.get("operations", [])
        if row["operation_type"] == "consolidation" and row["status"] == "completed"]
    result["pipeline_proof"] = {"retained_fact_ids": sorted(facts),
        "linked_observation_ids": observations, "completed_consolidation_ids": consolidation}
    result["complete"] = (bool(facts) and bool(observations) and bool(consolidation) and result["statuses"].get("pipeline") == "terminal" and not result["errors"]
        and result["ledger"]["complete"] and bool(result.get("readback_after_stop", {}).get("memories"))
        and result["readback_after_stop"]["pending_running"] == 0
        and all(row["status"] == "completed" for row in result["readback_after_stop"]["operations"])
        and result["local_quiescence"])
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-file", required=True, type=Path)
    parser.add_argument("--bank", required=True)
    parser.add_argument("--eva-source", required=True, type=Path)
    parser.add_argument("--eva-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; preserve the previous lifecycle receipt")
    result = asyncio.run(probe(args))
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    return 0 if result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
