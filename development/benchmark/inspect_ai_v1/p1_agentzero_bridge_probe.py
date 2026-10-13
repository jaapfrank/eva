"""Exercise the pinned native SDK/OAuth bridge without external inference.

Native configuration, models, LiteLLM serialization, Flask routes, authentication
lookup and SDK parsing are retained. Only upstream provider HTTP is mocked.
Run in a disabled network namespace with an existing independent auth store.
This is a route fixture, not a live agent or comparison admission.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
from contextlib import ExitStack
from datetime import datetime, UTC
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from cp001_accounting import ProviderSampleLedger, SampleAsyncStream, SampleStopped

PIN = "e3051fb584b1a36be2b0a0c90606f1c2c2d356ec"
PROFILES = {"foreground_contact": ("gpt-6-sol", "none"),
            "helper": ("gpt-6-astra", "medium")}


class AgentZeroParentLedger:
    """Native SDK mirror of the existing authoritative parent, never a registry."""

    def __init__(self, sample_id, control):
        from cp001_process_accounting import ProcessSampleLedger
        from math import isfinite
        assert control["namespace"] == "agent_zero"
        assert control["request_id"] == sample_id
        deadline = control["monotonic_deadline"]
        assert isinstance(deadline, (int, float)) and not isinstance(deadline, bool) and isfinite(deadline)
        self.sample_id, self.deadline = sample_id, deadline
        self.peer = ProcessSampleLedger(**{key: control[key] for key in
            ("socket_path", "namespace", "request_id", "token")})

    def __getattr__(self, name):
        return getattr(self.peer, name)

    @property
    def stopped(self):
        return self.peer.stopped

    @stopped.setter
    def stopped(self, reason):
        self.peer.stopped = reason

    def begin(self, role, model, effort):
        if time.monotonic() >= self.deadline:
            self.peer.stop_async_attempts("native sample wall envelope reached")
            raise SampleStopped(self.peer.stopped)
        parent_role = {"foreground_contact": "contact", "helper": "helper"}.get(role)
        if parent_role is None:
            self.peer.stop_async_attempts("unbound native Agent Zero role")
            raise SampleStopped(self.peer.stopped)
        attempt = self.peer.begin(parent_role, model, effort)
        with self.peer.lock:
            self.peer.attempts[attempt].update(id=attempt, sample_id=self.sample_id,
                role=role, model=model, reasoning_effort=effort, parent_role=parent_role)
        return attempt

    def report(self):
        from types import SimpleNamespace
        with self.peer.lock:
            mirror = SimpleNamespace(lock=self.peer.lock, sample_id=self.sample_id,
                attempts=[dict(value) for _, value in sorted(self.peer.attempts.items())],
                stopped=self.peer.stopped, stop_requested=self.peer.stop_requested,
                async_attempts=self.peer.async_attempts, cancel_errors=self.peer.cancel_errors)
            report = ProviderSampleLedger.report(mirror)
        report.update(coverage="Native SDK mirror; authoritative usage and aggregate admission stay in parent",
            parent_accounting_bound=True, authoritative_local_registry=False)
        return report

    def close(self, *, settled):
        return self.peer.close(settled=settled)


class NativeSyncOwnership:
    """Keep blocking native proxy work observable while its caller can stop."""
    def __init__(self, ledger, seconds):
        self.ledger, self.seconds = ledger, seconds
        self.deadline = None
        self.pending = set()
        self.close_errors = []
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="p1-native-http")

    async def call(self, function, *args, cleanup=False):
        if self.deadline is None:
            self.deadline = time.monotonic() + self.seconds
        remaining = self.seconds if cleanup else self.deadline - time.monotonic()
        if remaining <= 0:
            self.ledger.stop_async_attempts("native sample wall envelope reached")
            raise SampleStopped("native sample wall envelope reached")
        context = contextvars.copy_context()
        task = self.executor.submit(context.run, function, *args)
        self.pending.add(task)
        def done(value):
            self.pending.discard(value)
            if not value.cancelled():
                value.exception()  # Consume background error without hiding ownership.
        task.add_done_callback(done)
        # Shield preserves the still-running thread as pending after cancellation.
        return await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(task)), remaining)

    async def close(self, function):
        try:
            await self.call(function, cleanup=True)
        except BaseException as error:
            self.close_errors.append(type(error).__name__)
            self.ledger.stop_async_attempts("native upstream close unconfirmed")
            raise

    async def close_response(self, context, response, upstreams):
        for upstream in upstreams:
            if not upstream.native_closed:
                await self.close(upstream.close)
        # Wait for the actual blocking reads to release their Context, not merely
        # for cancellation of their asyncio callers.
        try:
            pending = list(self.pending)
            if pending:
                await asyncio.wait_for(asyncio.shield(asyncio.gather(
                    *(asyncio.wrap_future(item) for item in pending),
                    return_exceptions=True)), self.seconds)
        except BaseException as error:
            self.close_errors.append(type(error).__name__)
            self.ledger.stop_async_attempts("native read ownership not released")
            raise
        await self.close(lambda: context.run(response.close))

    def report(self):
        return {"pending_native_io": len(self.pending),
                "close_errors": list(self.close_errors),
                "quiescent": not self.pending and not self.close_errors}


def _main(lifecycle_stack) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--auth-store", type=Path, required=True)
    parser.add_argument("--dockerized", choices=["1"], required=True)
    parser.add_argument("--case", choices=["all", "stream-cases", "contact-stream", "work-nonstream", "partial-stop", "native-loop"], default="all")
    parser.add_argument("--embedding-cache", type=Path, help="Existing pinned local cache, required for native-loop")
    parser.add_argument("--bind-stream", action="store_true", help="Use the explicit comparison-side SDK stream guard")
    parser.add_argument("--provider-mode", choices=["fixture", "subscription"], default="fixture")
    parser.add_argument("--readiness-only", action="store_true")
    parser.add_argument("--parent-accounting-control", type=Path)
    parser.add_argument("--development-case", choices=["C01", "C04"])
    parser.add_argument("--epoch", type=int, choices=[1, 2])
    parser.add_argument("--final-cm-inputs", type=Path)
    parser.add_argument("--final-cm-admission", type=Path)
    args = parser.parse_args()
    subscription = args.provider_mode == "subscription"
    if subscription and args.case != "native-loop":
        parser.error("subscription requires the existing native-loop route")
    if args.case == "native-loop" and (not args.bind_stream or args.embedding_cache is None):
        parser.error("native-loop requires --bind-stream and the existing --embedding-cache")
    if bool(args.development_case) != bool(args.epoch):
        parser.error("development case and epoch must be supplied together")
    if subscription and not args.development_case and not args.final_cm_inputs:
        parser.error("subscription requires an explicit original development slot")
    if bool(args.final_cm_inputs) != bool(args.final_cm_admission):
        parser.error("final CM inputs and explicit admission must be supplied together")
    if args.final_cm_inputs and (args.development_case or args.case != "native-loop"):
        parser.error("final CM and development entrypoints are separate")
    if subscription and args.final_cm_inputs and not args.readiness_only and not args.parent_accounting_control:
        parser.error("final subscription requires authoritative parent accounting before input decoding")
    parent_control = None
    if args.parent_accounting_control:
        assert subscription and not args.readiness_only
        parent_control = json.loads(args.parent_accounting_control.read_text())
    final_cm = None
    if args.final_cm_inputs:
        admission = json.loads(args.final_cm_admission.read_text())
        raw = args.final_cm_inputs.read_bytes()
        frozen_plan = Path(__file__).with_name("ev003_execution_plan_20261004.json")
        plan_sha = "6012762d1f6fa745a0cdb6321fe0df1a19c40f1cd3a836c2ee3fbf9928079dd2"
        assert admission["admitted"] is True and admission["participant"] == "agent_zero"
        assert admission["probe_sha256"] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        assert admission["projection_sha256"] == hashlib.sha256(raw).hexdigest()
        assert admission["plan_sha256"] == plan_sha == hashlib.sha256(frozen_plan.read_bytes()).hexdigest()
        for filename in ("ev003_python_calendar.py", "ev003_agentzero_readback.py", "cp001_accounting.py"):
            assert admission["instrumentation_sha256"][filename] == hashlib.sha256(Path(__file__).with_name(filename).read_bytes()).hexdigest()
        projection = json.loads(raw)
        assert projection["contract"] == "Ev003PrivateNativeInputs/v1" and projection["oracle_included"] is False
        assert projection["plan_sha256"] == plan_sha
        assert projection["case_id"] == admission["case_id"] and projection["case_id"].startswith("EV003-CM-")
        assert projection["epoch"] == admission["epoch"]
        final_cm = {"binding": admission, "clock": projection["clock"],
                    "participant": projection["participants"]["agent_zero"]}
        del raw, projection
    plan_path = Path(__file__).with_name("p1_participants_20260929.json")
    plan = json.loads(plan_path.read_text())["developmentPlan"]
    selected_slot = None
    if args.development_case:
        slot = {"participant": "agent_zero", "case": args.development_case, "epoch": args.epoch}
        if slot not in plan["schedule"] or args.case != "native-loop":
            parser.error("selected slot is not an original native-loop development entry")
        case_definition = next(item for item in plan["cases"] if item["id"] == args.development_case)
        selected_slot = {**slot, "sample_id": f"AgentZero:{args.development_case}:epoch{args.epoch}",
                         "schedule_index": plan["schedule"].index(slot),
                         "stimulus": case_definition["stimulus"], "limits": plan["perSampleLimits"],
                         "manifest_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest()}
    source, output = args.source.resolve(), args.output.resolve()
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip() == PIN
    assert subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=source).returncode == 0
    assert not output.exists(), "Keep each route fixture's evidence separate"
    assert output != source and source not in output.parents
    os.umask(0o077)
    output.mkdir(parents=True)
    if selected_slot is not None:
        (output / "development-slot.json").write_text(json.dumps(selected_slot, indent=2) + "\n")
    (output / "execution-source.json").write_text(json.dumps({
        "files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in [Path(__file__), Path(__file__).with_name("cp001_accounting.py")]},
        "sourceCommit": PIN, "selectedCase": args.case, "comparisonStreamGuard": args.bind_stream,
    }, indent=2) + "\n")
    if final_cm:
        from zoneinfo import ZoneInfo
        ZoneInfo(final_cm["clock"]["timezone"])
        os.environ["DEFAULT_USER_TIMEZONE"] = final_cm["clock"]["timezone"]
        os.environ["TZ"] = final_cm["clock"]["timezone"]
        time.tzset()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))
    if args.embedding_cache is not None:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_CACHE"] = str(args.embedding_cache.resolve())

    def reject_connection(*args, **kwargs):
        raise RuntimeError("External network is disabled in this native bridge fixture")

    if not subscription or args.readiness_only:
        socket.socket.connect = reject_connection
        socket.socket.connect_ex = reject_connection
    from helpers import files, runtime
    runtime.initialize()
    if subscription:
        assert runtime.is_dockerized(), "Native launch requires --dockerized=1"
    files.USER_DIR = str(output / "usr")
    original_path = files.get_abs_path

    def isolated_path(*parts):
        resolved = Path(original_path(*parts)).resolve()
        try:
            relative = resolved.relative_to(source)
        except ValueError:
            return str(resolved)
        if relative.parts and relative.parts[0] in {"usr", "tmp", "logs"}:
            return str(output / relative)
        return str(resolved)

    files.get_abs_path = isolated_path
    here = Path(__file__).parent
    preset_dir = output / "usr/plugins/_model_config"
    preset_dir.mkdir(parents=True)
    shutil.copyfile(here / "p1_agentzero_presets.yaml", preset_dir / "presets.yaml")
    if subscription and args.embedding_cache is not None:
        snapshot = (args.embedding_cache.resolve() / "models--sentence-transformers--all-MiniLM-L6-v2" /
                    "snapshots/1110a243fdf4706b3f48f1d95db1a4f5529b4d41")
        assert snapshot.is_dir(), "Pinned public embedding snapshot is required"
        (output / "embedding-binding.json").write_text(json.dumps({
            "model": "sentence-transformers/all-MiniLM-L6-v2",
            "revision": snapshot.name, "local_snapshot": str(snapshot),
            "files": {str(path.relative_to(snapshot)): hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in sorted(snapshot.rglob("*")) if path.is_file()},
            "native_embedding_implementation_unchanged": True}) + "\n")
    shutil.copyfile(here / "p1_agentzero_selection.json", preset_dir / "config.json")
    from helpers import plugins
    settings = json.loads((here / "p1_agentzero_oauth.json").read_text())
    settings["codex"]["auth_file_path"] = str(args.auth_store.resolve())
    plugins.save_plugin_config("_oauth", "", "", settings)

    import httpx
    import requests
    from flask import Flask
    from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
    from plugins._oauth.helpers import codex, routes
    from plugins._model_config.helpers import model_config
    from helpers import litellm_transport
    import litellm
    capabilities = json.loads((here / "p1_agentzero_litellm_capabilities.json").read_text())
    capability_readback = {}
    for model_name, declaration in capabilities.items():
        before = litellm.utils.supports_native_streaming(model_name, "openai")
        litellm.register_model({model_name: declaration})
        after = litellm.utils.supports_native_streaming(model_name, "openai")
        assert after, "Configured native streaming must survive the SDK capability lookup"
        capability_readback[model_name] = {"before": before, "configured": after}
    if final_cm:
        from ev003_python_calendar import protect_native_auth
        protect_native_auth(lifecycle_stack, agentzero_auth_module=codex)
    auth = codex.load_auth(ensure_fresh=False)
    if codex.should_refresh(auth.access_token, auth.last_refresh):
        (output / "native-readiness.json").write_text(json.dumps({"status": "owned_auth_expired", "provider_calls": 0, "login_started": False}) + "\n")
        return
    if subscription:
        native_load_auth = codex.load_auth
        def access_only_auth(*_args, **_kwargs):
            value = native_load_auth(ensure_fresh=False)
            if codex.should_refresh(value.access_token, value.last_refresh):
                raise RuntimeError("Owned native auth expired; no refresh in sample")
            return value
        codex.load_auth = access_only_auth
    app = Flask("p1-native-bridge")
    routes.register_oauth_routes(app)
    limits = json.loads((here / "p1_participants_20260929.json").read_text())["developmentPlan"]["perSampleLimits"]
    if final_cm:
        limits = {"providerAttempts": 16, "providerInputTokens": 1048576,
                  "providerOutputTokens": 19200, "wallSeconds": 480,
                  "toolDispatches": 16, "externalActions": 0}
        assert final_cm["binding"]["limits"] == limits
    results = []

    if subscription:
        # Preserve native model-name dispatch and its local-cache fallback. The
        # generic HTTP guard below raises BaseException for inference stop; it
        # must not replace requests' ordinary cache-miss exception during load.
        previous_connect, previous_connect_ex = socket.socket.connect, socket.socket.connect_ex
        socket.socket.connect = reject_connection
        socket.socket.connect_ex = reject_connection
        try:
            model_config.build_embedding_model().embed_query("Synthetic native memory prerequisite")
        finally:
            socket.socket.connect, socket.socket.connect_ex = previous_connect, previous_connect_ex

    async def case(name, role, *, stream, partial=False, native_loop=False):
        ledger = (AgentZeroParentLedger(name, parent_control) if parent_control is not None
                  else ProviderSampleLedger(name, limits, PROFILES))
        model_name, effort = PROFILES[role]
        upstreams, requests_seen, sdk_requests, native_responses, stream_bindings = [], [], [], [], []
        waiting = asyncio.Event()
        wire_attempt = contextvars.ContextVar("p1_wire_attempt", default=None)
        sdk_attempts = contextvars.ContextVar("p1_sdk_attempts", default=None)
        helper_settled = threading.Event()
        wire_dispatches = set()
        tools = []
        refused_sdk = []
        native_context = None
        native_contexts = []
        active_context_ids = {name}
        calendar = None
        calendar_file = output / "scenario-clock.json"
        initial_offset = None
        cm_turns = []
        sdk_idle_cleanup = []
        native_io = NativeSyncOwnership(ledger, limits["wallSeconds"])
        if parent_control is not None:
            native_io.deadline = ledger.deadline
        restore_native = []

        def sdk_usage(response):
            usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
            return usage if isinstance(usage, dict) else usage.model_dump() if hasattr(usage, "model_dump") else None

        class Upstream(requests.Response):
            def __init__(self, attempt, request_role, requested_model):
                super().__init__()
                self.attempt = attempt
                self.request_role, self.requested_model = request_role, requested_model
                self.status_code = 200
                self.headers["Content-Type"] = "text/event-stream"
                self.native_closed = False

            def iter_content(self, chunk_size=1, decode_unicode=False):
                text = "Native bridge fixture completed."
                if native_loop:
                    text = (json.dumps({"thoughts": ["Offline native protocol check"], "tool_name": "response",
                                        "tool_args": {"text": "Native bridge fixture completed."}})
                            if self.request_role == "foreground_contact" else "[]")
                events = [
                    {"type": "response.output_text.delta", "delta": text, "output_index": 0, "content_index": 0},
                    {"type": "response.completed", "response": {
                        "id": "resp_bridge_fixture", "object": "response", "created_at": 0,
                        "model": self.requested_model, "status": "completed", "usage": {
                            "input_tokens": 21, "output_tokens": 9, "total_tokens": 30},
                        "output": [{"type": "message", "id": "msg_fixture", "role": "assistant",
                                    "status": "completed", "content": [{"type": "output_text",
                                    "text": text, "annotations": []}]}]}}]
                for event in events:
                    if event["type"] == "response.completed" and not native_loop:
                        ledger.finish(self.attempt, event["response"]["usage"])
                    yield ("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode()

            def close(self):
                self.native_closed = True
                if not native_loop:
                    ledger.incomplete(self.attempt, "native_upstream_closed_usage_unknown")

        def upstream_request(method, url, **kwargs):
            assert method == "POST" and url.endswith("/responses")
            headers = kwargs["headers"]
            assert headers["Authorization"] == "Bearer " + auth.access_token
            assert headers["chatgpt-account-id"] == auth.account_id
            wire = json.loads(kwargs["data"])
            request_role = role
            if native_loop:
                from p1_agentzero_native_probe import ROLE, CONTEXT
                request_role = ROLE.get()
                assert CONTEXT.get() in active_context_ids, "Upstream call escaped its native AgentContext"
            else:
                assert wire["model"] == model_name and wire["reasoning"]["effort"] == effort
            if native_loop:
                assert len(sdk_attempts.get() or []) == 1, "Native upstream has no single admitted SDK owner"
                attempt = sdk_attempts.get()[0]
                assert ledger.attempts[attempt]["model"] == wire["model"]
                assert ledger.attempts[attempt]["reasoning_effort"] == wire["reasoning"]["effort"]
            else:
                attempt = ledger.begin(request_role, wire["model"], wire["reasoning"]["effort"])
                ledger.track_async_attempt(attempt)
            wire_attempt.set(attempt)
            if sdk_attempts.get() is not None:
                if native_loop:
                    assert attempt not in wire_dispatches, "Repeated SDK upstream is not admitted"
                else:
                    sdk_attempts.get().append(attempt)
            requests_seen.append({"model": wire["model"], "effort": wire["reasoning"]["effort"],
                                  "forced_stream": wire["stream"], "owned_auth_forwarded": True,
                                  "role": request_role})
            if subscription:
                # The first native HTTP dispatch belongs to the SDK reservation;
                # an unexpected retry is refused by the single-owner assertion above.
                if url != "https://chatgpt.com/backend-api/codex/responses":
                    raise SampleStopped("Unadmitted native upstream endpoint")
                if any(getattr(adapter.max_retries, "total", None) not in (0, False) for adapter in codex._UPSTREAM_SESSION.adapters.values()):
                    raise SampleStopped("Native transport retries are not accounted")
                kwargs["allow_redirects"] = False
                kwargs["timeout"] = min(kwargs.get("timeout", 120), max(0.1, native_io.deadline-time.monotonic()))
                if ledger.stopped or time.monotonic() >= native_io.deadline:
                    ledger.incomplete(attempt, "stopped_before_native_http_dispatch")
                    raise SampleStopped(ledger.stopped or "native sample wall envelope reached")
                wire_dispatches.add(attempt)  # Count actual HTTP entries, including failed connects.
                response = original_request(method, url, **kwargs)
                response.attempt = attempt
                response.native_closed = False
                native_close = response.close
                def close_owned():
                    native_close()
                    response.native_closed = True
                response.close = close_owned
            else:
                response = Upstream(attempt, request_role, wire["model"])
            upstreams.append(response)
            return response

        async def close_native_response(context, response):
            await native_io.close_response(context, response, upstreams)

        def completed_response(response):
            get = response.get if isinstance(response, dict) else lambda key: getattr(response, key, None)
            return get("status") == "completed" and not get("error") and not get("incomplete_details")

        class BridgeStream(httpx.AsyncByteStream):
            def __init__(self, response, context):
                self.response, self.context = response, context

            async def __aiter__(self):
                iterator = iter(self.response.response)
                index = 0
                sentinel = object()
                while True:
                    chunk = (await native_io.call(self.context.run, next, iterator, sentinel)
                             if subscription else self.context.run(next, iterator, sentinel))
                    if chunk is sentinel:
                        break
                    yield chunk
                    if partial and index == 0:
                        waiting.set()
                        await asyncio.Future()
                    index += 1

            async def aclose(self):
                if subscription:
                    await close_native_response(self.context, self.response)
                else:
                    self.context.run(self.response.close)

        class BridgeTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                assert request.url.path == "/oauth/codex/v1/responses"
                wire = json.loads(request.content)
                sdk_request = {"model": wire.get("model"), "stream": wire.get("stream")}
                sdk_requests.append(sdk_request)
                context = contextvars.copy_context()
                def dispatch():
                    return context.run(app.test_client().post, request.url.path, data=request.content,
                                       content_type="application/json", buffered=False)
                response = await native_io.call(dispatch) if subscription else dispatch()
                native_responses.append((context, response))
                sdk_request["native_content_type"] = response.content_type
                sdk_request["native_status"] = response.status_code
                (output / "sdk-request-boundary.json").write_text(json.dumps(sdk_requests, indent=2) + "\n")
                if response.status_code != 200:
                    if subscription:
                        await close_native_response(context, response)
                    else:
                        context.run(response.close)
                    raise RuntimeError("Native proxy failed before a bound SDK response; raw diagnostics withheld")
                return httpx.Response(response.status_code, headers=dict(response.headers),
                                      stream=BridgeStream(response, context), request=request,
                                      extensions={"comparison_attempt": context.get(wire_attempt)})

        class BridgeHTTPHandler(AsyncHTTPHandler):
            def create_client(self, *args, **kwargs):
                return httpx.AsyncClient(transport=BridgeTransport())

        original_request = codex._UPSTREAM_SESSION.request
        if subscription:
            def denied_sync(*_args, **_kwargs):
                refused_sdk.append({"entrypoint": "unsupported_sync_or_chat", "before_sdk_execution": True})
                ledger.stop_async_attempts("Native alternative SDK entrypoint not admitted")
                raise SampleStopped(ledger.stopped)
            async def denied_async(*_args, **_kwargs):
                return denied_sync()
            for attribute in ("completion", "responses", "delete_responses", "adelete_responses", "acompletion"):
                original = getattr(litellm_transport, attribute)
                restore_native.append((litellm_transport, attribute, original))
                setattr(litellm_transport, attribute, denied_async if attribute.startswith("a") else denied_sync)
            original_session_request = requests.Session.request
            restore_native.append((requests.Session, "request", original_session_request))
            usage_urls = set(codex.usage_endpoint_candidates("https://chatgpt.com/backend-api/codex"))
            def deny_other_http(session, *positional, **kwargs):
                method = kwargs.get("method", positional[0] if positional else "")
                url = kwargs.get("url", positional[1] if len(positional) > 1 else "")
                if str(method).upper() == "GET" and url in usage_urls:
                    raise requests.ConnectionError("Quota metadata network access is disabled in this sample")
                ledger.stop_async_attempts("Unbound native HTTP entrypoint refused")
                raise SampleStopped(ledger.stopped)
            requests.Session.request = deny_other_http
        original_responses = litellm_transport.aresponses
        codex._UPSTREAM_SESSION.request = upstream_request

        async def bound_responses(**request):
            attempts = []
            if native_loop:
                from p1_agentzero_native_probe import ROLE, CONTEXT
                assert CONTEXT.get() in active_context_ids, "SDK call escaped its native AgentContext"
                model_name = request["model"].removeprefix("openai/")
                reasoning = request.get("extra_body", {}).get("reasoning", request.get("reasoning", {}))
                effort = reasoning.get("effort") or request.get("reasoning_effort")
                try:
                    attempt = ledger.begin(ROLE.get(), model_name, effort)
                except SampleStopped:
                    refused_sdk.append({"role": ROLE.get(), "model": model_name, "effort": effort,
                                        "before_sdk_execution": True})
                    raise
                attempts.append(attempt)
                ledger.track_async_attempt(attempt)
            if native_loop and args.bind_stream:
                request["client"] = client
                assert isinstance(request["client"], BridgeHTTPHandler)
                endpoint = request.get("api_base") or request.get("base_url")
                from urllib.parse import urlparse
                parsed_endpoint = urlparse(endpoint or "")
                if parsed_endpoint.hostname not in {"127.0.0.1", "localhost"} or parsed_endpoint.path.rstrip("/") != "/oauth/codex/v1":
                    ledger.incomplete(attempt, "native_proxy_endpoint_not_bound")
                    ledger.stop_async_attempts("Native SDK proxy endpoint is not bound")
                    raise SampleStopped(ledger.stopped)
                with (output / "sdk-client-binding.jsonl").open("a") as binding_receipt:
                    binding_receipt.write(json.dumps({
                        "attempt": attempt, "client_type": type(request["client"]).__name__,
                        "endpoint_host": parsed_endpoint.hostname, "endpoint_path": parsed_endpoint.path}) + "\n")
            token = sdk_attempts.set(attempts)
            try:
                iterator = await original_responses(**request)
            except BaseException:
                if native_loop:
                    ledger.incomplete(attempt, "sdk_failed_usage_unknown")
                raise
            finally:
                sdk_attempts.reset(token)
            if native_loop:
                assert len(attempts) == 1, "SDK call has missing or repeated native upstream attempts"
            if request.get("stream"):
                # The fixture binds the actual response to its observed upstream
                # attempt. This is not an attestation of deployed proxy coverage.
                attempt = iterator.response.extensions["comparison_attempt"]
                if native_loop:
                    assert attempt == attempts[0], "SDK response belongs to another native attempt"
                def observe_usage(event):
                    event_type = event.get("type") if isinstance(event, dict) else getattr(event, "type", None)
                    if event_type == "response.completed":
                        response = event.get("response") if isinstance(event, dict) else getattr(event, "response", None)
                        ledger.finish(attempt, sdk_usage(response))
                        if subscription and not completed_response(response):
                            ledger.stop_async_attempts("Native terminal response is not completed")
                            raise SampleStopped(ledger.stopped)
                        if ledger.attempts[attempt]["role"] == "helper":
                            helper_settled.set()
                bound = SampleAsyncStream(ledger, attempt, iterator,
                                          on_event=observe_usage if native_loop else None)
                stream_bindings.append(bound)
                return bound
            if native_loop:
                attempt = attempts[0]
                ledger.finish(attempt, sdk_usage(iterator))
                if subscription and not completed_response(iterator):
                    ledger.stop_async_attempts("Native terminal response is not completed")
                    raise SampleStopped(ledger.stopped)
                if ledger.attempts[attempt]["role"] == "helper":
                    helper_settled.set()
            return iterator

        if args.bind_stream:
            litellm_transport.aresponses = bound_responses
        client = BridgeHTTPHandler()
        if native_loop:
            import agent
            import initialize
            from p1_agentzero_native_probe import tag_method
            for method, model_role in (("call_chat_model_turn", "foreground_contact"),
                                       ("call_chat_model", "foreground_contact"),
                                       ("call_utility_model", "helper")):
                original = getattr(agent.Agent, method)
                restore_native.append((agent.Agent, method, original))
                setattr(agent.Agent, method, tag_method(original, model_role))
            for builder in ("build_chat_model", "build_utility_model"):
                original = getattr(model_config, builder)
                restore_native.append((model_config, builder, original))
                def build(*positional, _original=original, **kwargs):
                    result = _original(*positional, **kwargs)
                    result.kwargs["client"] = client
                    return result
                setattr(model_config, builder, build)
            original_tool = agent.Agent._execute_tool_request
            restore_native.append((agent.Agent, "_execute_tool_request", original_tool))
            def check_tool(actor, tool_name):
                assert actor.context.id in active_context_ids
                allowed_tools = {"response"}
                if final_cm:
                    allowed_tools |= {"memory_load", "memory_save", "memory_delete", "memory_forget"}
                if ledger.stopped or tool_name not in allowed_tools:
                    ledger.stop_async_attempts("Native comparison tool is not admitted")
                    raise SampleStopped(ledger.stopped)
            async def execute_tool(actor, tool_name, *positional, **kwargs):
                check_tool(actor, tool_name)
                return await original_tool(actor, tool_name, *positional, **kwargs)
            agent.Agent._execute_tool_request = execute_tool
            original_legacy = agent.Agent.process_tools
            restore_native.append((agent.Agent, "process_tools", original_legacy))
            async def process_tools(actor, message):
                from helpers import extract_tools
                parsed = extract_tools.extract_tool_request(message)
                if parsed is not None:
                    tool_name, _arguments = extract_tools.normalize_tool_request(parsed)
                    check_tool(actor, tool_name)
                return await original_legacy(actor, message)
            agent.Agent.process_tools = process_tools
            from helpers import extension
            original_extensions = extension.call_extensions_async
            restore_native.append((extension, "call_extensions_async", original_extensions))
            async def call_extensions(point, *positional, **kwargs):
                if point == "tool_execute_before":
                    actor = positional[0] if positional else kwargs["agent"]
                    tool_name = kwargs["tool_name"]
                    check_tool(actor, tool_name)
                    tools.append({"name": tool_name, "protocol_only": tool_name == "response"})
                    if len(tools) > limits["toolDispatches"]:
                        ledger.stop_async_attempts("Native comparison tool envelope reached")
                        raise SampleStopped(ledger.stopped)
                return await original_extensions(point, *positional, **kwargs)
            extension.call_extensions_async = call_extensions
            workdir = output / "usr/workdir"
            workdir.mkdir(parents=True)
            (output / "usr/settings.json").write_text(json.dumps({"workdir_path": str(workdir)}))
            config = initialize.initialize_agent({"mcp_servers": ""})
            model_config.build_embedding_model().embed_query("Synthetic native memory prerequisite")
            if final_cm:
                from ev003_python_calendar import ScenarioCalendar
                participant = final_cm["participant"]
                turns = participant["turns"]
                assert len(turns) == 3 and [t["ordinal"] for t in turns] == [0, 1, 2]
                assert [t["session_index"] for t in turns] == [0, 0, 1]
                assert participant["provisioning"]["memory_subdir"] == "default"
                assert isinstance(participant["provisioning"]["actor_id"], str) and participant["provisioning"]["actor_id"]
                initial = datetime.fromisoformat(final_cm["clock"]["initial"].replace("Z", "+00:00"))
                assert initial.tzinfo is not None
                initial_offset = (initial.timestamp() - time.time()) * 1000
                first = turns[0]["clock"]
                calendar_file.write_text(json.dumps({"contract": "EvaScenarioCalendar/v1",
                    "generation": first["generation"],
                    "offsetMilliseconds": initial_offset + first["offset_delta_milliseconds"]}))
                calendar = ScenarioCalendar(calendar_file)
                calendar.refresh()
                from helpers.localization import Localization
                assert Localization.get().get_timezone() == final_cm["clock"]["timezone"]
                lifecycle_stack.callback(calendar.restore)
                active_context_ids = {turns[0]["context_id"]}
            native_context = agent.AgentContext(config=config,
                id=turns[0]["context_id"] if final_cm else name)
            native_contexts.append(native_context)
        model = model_config.build_chat_model() if role == "foreground_contact" else model_config.build_utility_model()
        model.kwargs["client"] = client
        observed = []

        async def observe(delta, full):
            observed.append(delta)

        task = None
        result = None
        failure = None
        try:
            if native_loop and final_cm:
                from ev003_agentzero_readback import read_agentzero_snapshot
                from helpers.defer import EventLoopThread
                from plugins._memory.helpers.memory import get_agent_memory_subdir
                if parent_control is None:
                    native_io.deadline = time.monotonic() + limits["wallSeconds"]
                whole_started = time.monotonic()
                prior_session = 0
                response_counts = {}

                async def settle_cm():
                    async def count_pending():
                        from litellm.litellm_core_utils.logging_worker import LoggingWorker
                        pending, idle = [], []
                        for task in asyncio.all_tasks():
                            if task.done() or task is asyncio.current_task():
                                continue
                            coroutine = task.get_coro()
                            label = getattr(coroutine, "__qualname__", type(coroutine).__name__)
                            owner = getattr(coroutine, "cr_frame", None)
                            owner = owner.f_locals.get("self") if owner else None
                            if (getattr(coroutine, "cr_code", None) is LoggingWorker._worker_loop.__code__
                                    and isinstance(owner, LoggingWorker) and owner._queue is not None
                                    and owner._queue.empty() and not owner._running_tasks):
                                idle.append(label)
                            else:
                                pending.append(label)
                        return {"pending": pending, "idle_sdk_logging": idle}
                    while True:
                        if ledger.stopped:
                            raise SampleStopped(ledger.stopped)
                        remaining = native_io.deadline - time.monotonic()
                        if remaining <= 0:
                            ledger.stop_async_attempts("native sample wall envelope reached")
                            raise SampleStopped(ledger.stopped)
                        counts = [asyncio.run_coroutine_threadsafe(count_pending(), item.loop).result(timeout=min(1, remaining))
                                  for item in list(EventLoopThread._instances.values()) if item.loop and item.loop.is_running()]
                        report = ledger.report()
                        (output / "cm-settlement.private.json").write_text(json.dumps({"tasks": counts,
                            "ledger": report, "native_io": native_io.report()}, indent=2)+"\n")
                        if (not any(item["pending"] for item in counts) and report["complete"] and report["active_async_attempts"] == 0
                                and native_io.report()["quiescent"]):
                            return {"pending_native_tasks": counts, "ledger": report, "native_io": native_io.report()}
                        await asyncio.sleep(0.02)

                if args.readiness_only:
                    (output / "native-readiness.json").write_text(json.dumps({"status": "native_context_ready",
                        "source_pin": PIN, "provider_calls": 0, "mode": args.provider_mode,
                        "sample": name, "limits": limits}) + "\n")
                    return
                for turn in turns:
                    if cm_turns:
                        await settle_cm()
                        state = {"contract": "EvaScenarioCalendar/v1", "generation": turn["clock"]["generation"],
                                 "offsetMilliseconds": initial_offset + turn["clock"]["offset_delta_milliseconds"]}
                        temporary = calendar_file.with_suffix(".next")
                        temporary.write_text(json.dumps(state)); temporary.replace(calendar_file)
                        assert calendar.refresh() == state
                    if turn["session_index"] != prior_session:
                        assert not native_context.is_running()
                        native_context = agent.AgentContext(config=config, id=turn["context_id"])
                        native_contexts.append(native_context)
                        active_context_ids.add(native_context.id)
                        prior_session = turn["session_index"]
                    assert native_context.id == turn["context_id"]
                    memory_subdir = get_agent_memory_subdir(native_context.agent0)
                    assert memory_subdir == "default", "CM memory must stay in the fresh isolated owner store"
                    turn_started = time.monotonic()
                    entry = {"ordinal": turn["ordinal"], "context_id": native_context.id,
                             "calendar": calendar.refresh(), "native_dispatch_at": datetime.now(UTC).isoformat()}
                    native_task = native_context.communicate(agent.UserMessage(turn["text"]))
                    response = native_task.result_sync(timeout=max(.001, native_io.deadline-time.monotonic()))
                    entry["native_response"] = response
                    entry["native_response_seconds"] = time.monotonic()-turn_started
                    entry["settlement"] = await settle_cm()
                    # Native message_loop_end already persists this context.
                    # Read those bytes independently; do not manufacture a save.
                    snapshot = read_agentzero_snapshot(user_dir=output/"usr",
                        context_id=native_context.id, memory_subdir=memory_subdir)
                    assert snapshot["chat_exists"] and snapshot["reply_text"] == response, "durable native reply differs"
                    assert snapshot["response_count"] > response_counts.get(native_context.id, 0), "no new durable native response"
                    response_counts[native_context.id] = snapshot["response_count"]
                    integrity = snapshot["memory_index"]
                    if integrity["saved"]:
                        assert integrity["mapping_matches_documents"] is True
                        assert integrity["native_hash_matches"] is not False
                    entry["independent_readback"] = snapshot
                    entry["settled_seconds"] = time.monotonic()-turn_started
                    cm_turns.append(entry)
                    (output/"cm-turns.private.json").write_text(json.dumps(cm_turns, indent=2)+"\n")
                result = {"case": name, "turns": cm_turns, "requests": requests_seen,
                          "sdk_requests": sdk_requests, "src/tools": tools, "ledger": ledger.report(),
                          "native_response_seconds": time.monotonic()-whole_started,
                          "timing_scope": "Entire three-turn native conversation plus helper/memory settlement; startup excluded",
                          "whole_sample_complete": True, "outcome": "native_cm_observed",
                          "http_dispatch_attempts": None, "known_partial_dispatches": len(wire_dispatches),
                          "usage_observation": "SDK-consumed raw usage; native helpers retained",
                          "refused_sdk": refused_sdk}
                results.append(result)
                return
            if native_loop:
                if subscription:
                    (output / "native-readiness.json").write_text(json.dumps({"status": "native_context_ready", "source_pin": PIN, "provider_calls": 0, "mode": "subscription", "sample": selected_slot["sample_id"], "limits": limits}) + "\n")
                    if args.readiness_only:
                        return
                    if parent_control is None:
                        native_io.deadline = time.monotonic() + limits["wallSeconds"]
                stimulus = selected_slot["stimulus"] if selected_slot else "Complete the offline native protocol check. No external tools."
                native_started = time.monotonic()
                native_task = native_context.communicate(agent.UserMessage(stimulus))
                response = native_task.result_sync(timeout=limits["wallSeconds"])
                native_response_seconds = time.monotonic() - native_started
                if subscription:
                    from helpers.defer import EventLoopThread
                    async def pending_tasks():
                        return sum(not t.done() and t is not asyncio.current_task() for t in asyncio.all_tasks())
                    while True:
                        pending = [asyncio.run_coroutine_threadsafe(pending_tasks(), item.loop).result(timeout=1)
                                   for item in list(EventLoopThread._instances.values()) if item.loop and item.loop.is_running()]
                        if not any(pending) or ledger.stopped:
                            break
                        if time.monotonic() >= native_io.deadline:
                            ledger.stop_async_attempts("native sample wall envelope reached")
                            break
                        await asyncio.sleep(0.02)
                else:
                    assert response == "Native bridge fixture completed."
                    assert helper_settled.wait(limits["wallSeconds"]), "Native helper did not settle"
                    assert [x["role"] for x in requests_seen].count("foreground_contact") == 1
                    assert [x["role"] for x in requests_seen].count("helper") == 1
                    assert tools == [{"name": "response", "protocol_only": True}]
                    assert ledger.report()["active_async_attempts"] == 0
                    assert ledger.report()["total_usage"]["output_tokens"] == 18
                result = {"case": name, "requests": requests_seen, "sdk_requests": sdk_requests,
                          "native_response": response,
                          "native_response_seconds": native_response_seconds,
                          "timing_scope": "Native communicate to final answer; prerequisite startup excluded; helper cleanup reported separately.",
                          "src/tools": tools, "ledger": ledger.report(),
                          "sample_completion": "not_established; ledger covers admitted SDK attempts only",
                          "refused_sdk": refused_sdk,
                          "usage_observation": "SDK-consumed response.completed/nonstream response, " + ("native subscription upstream" if subscription else "synthetic upstream"),
                          "http_dispatch_attempts": None,
                          "known_partial_dispatches": len(wire_dispatches),
                          "native_upstream_closed": [x.native_closed for x in upstreams],
                          "bound_streams_closed": [x.closed for x in stream_bindings],
                          "native_io": native_io.report(), "whole_sample_complete": subscription and not ledger.stopped and not any(pending) and native_io.report()["quiescent"],
                          "outcome": "native_subscription_observed" if subscription else "native_loop_real_sdk_routes_observed_not_admitted"}
                results.append(result)
                return
            task = asyncio.create_task(model.unified_call(user_message="Isolated native route fixture.",
                                      response_callback=observe if stream else None))
            if partial:
                waiter = asyncio.create_task(waiting.wait())
                ready, _pending = await asyncio.wait({waiter, task}, return_when=asyncio.FIRST_COMPLETED)
                if task in ready:
                    waiter.cancel()
                    await task
                    raise AssertionError("Partial fixture ended before its open-stream boundary")
                boundary = {"sdk_requests": sdk_requests, "requests": requests_seen,
                            "ledger": ledger.report(), "model_task_done": task.done(),
                            "bound_task_done": [bound.done() for _loop, bound in ledger.async_attempts.values()]}
                (output / "partial-boundary.json").write_text(json.dumps(boundary, indent=2) + "\n")
                assert boundary["ledger"]["active_async_attempts"] == 1, "Fixture paused after provider completion"
                assert sdk_requests[0]["stream"] is True
                ledger.stop_async_attempts("fixture sample stop")
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                assert task.cancelled(), "The model task must actually settle on sample stop"
                assert ledger.report()["total_usage"]["output_tokens"] is None
                if args.bind_stream:
                    assert all(x.native_closed for x in upstreams), "Bound SDK stop must close upstream before fixture cleanup"
                    assert ledger.report()["active_async_attempts"] == 0
            else:
                response, _reasoning = await task
                assert response == "Native bridge fixture completed."
                assert ledger.report()["total_usage"]["output_tokens"] == 9
            assert len(requests_seen) == 1
            result = {"case": name, "requests": requests_seen, "sdk_requests": sdk_requests, "stream": stream,
                            "partial": partial, "native_upstream_closed": [x.native_closed for x in upstreams],
                            "ledger": ledger.report(), "fixture_output_observed": bool(observed),
                            "comparison_stream_guard": args.bind_stream,
                            "bound_streams_closed": [x.closed for x in stream_bindings],
                            "outcome": ("model_cancelled_upstream_close_observed" if all(x.native_closed for x in upstreams)
                                        else "model_cancelled_upstream_close_unverified") if partial
                                       else "observed_route_completed"}
            results.append(result)
        except BaseException as error:
            failure = {
                "error_type": type(error).__name__, "case": name,
                "requests": requests_seen, "sdk_requests": sdk_requests,
                "src/tools": tools, "ledger": ledger.report(),
                "externalInferenceRequests": None if subscription else 0,
                "known_partial_dispatches": len(wire_dispatches),
                "native_io": native_io.report(), "admission": "not_admitted",
            }
            (output / "native-case-failure.json").write_text(json.dumps(failure, indent=2) + "\n")
            raise
        finally:
            if native_context is not None:
                for context in native_contexts:
                    context.kill_process()
                ledger.stop_async_attempts("Explicit native fixture cleanup")
                from helpers.defer import DeferredTask, EventLoopThread
                if final_cm:
                    async def close_idle_sdk_logging():
                        from litellm.litellm_core_utils.logging_worker import LoggingWorker
                        idle, other = [], []
                        for pending_task in asyncio.all_tasks():
                            if pending_task.done() or pending_task is asyncio.current_task():
                                continue
                            coroutine = pending_task.get_coro()
                            frame = getattr(coroutine, "cr_frame", None)
                            owner = frame.f_locals.get("self") if frame else None
                            if (getattr(coroutine, "cr_code", None) is LoggingWorker._worker_loop.__code__
                                    and isinstance(owner, LoggingWorker) and owner._queue is not None
                                    and owner._queue.empty() and not owner._running_tasks):
                                idle.append(pending_task)
                            else:
                                other.append(getattr(coroutine, "__qualname__", type(coroutine).__name__))
                        for pending_task in idle:
                            pending_task.cancel()
                        await asyncio.gather(*idle, return_exceptions=True)
                        return {"idle_logging_closed": len(idle),
                                "all_idle_done": all(item.done() for item in idle),
                                "other_pending": other}
                    for item in list(EventLoopThread._instances.values()):
                        if item.loop and item.loop.is_running():
                            try:
                                sdk_idle_cleanup.append(asyncio.run_coroutine_threadsafe(
                                    close_idle_sdk_logging(), item.loop).result(timeout=1))
                            except BaseException as error:
                                sdk_idle_cleanup.append({"error_type": type(error).__name__, "all_idle_done": False})
                for thread_name in list(EventLoopThread._instances):
                    DeferredTask(thread_name=thread_name).kill(terminate_thread=True)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await client.close()
            for context, response in native_responses:
                if subscription:
                    try:
                        await close_native_response(context, response)
                    except BaseException:
                        pass  # NativeSyncOwnership retains failed-close evidence.
                else:
                    context.run(response.close)
            for upstream in upstreams:
                if subscription:
                    try:
                        await native_io.close(upstream.close)
                    except BaseException:
                        pass
                else:
                    upstream.close()
            native_io.executor.shutdown(wait=False, cancel_futures=False)
            # Keep HTTP denial installed until native tasks and transports close.
            # If ownership is unresolved this process must remain fail-closed.
            if not subscription or (native_io.report()["quiescent"] and ledger.report()["active_async_attempts"] == 0):
                for target, attribute, original in reversed(restore_native):
                    setattr(target, attribute, original)
                codex._UPSTREAM_SESSION.request = original_request
                litellm_transport.aresponses = original_responses
            final_ownership = native_io.report()["quiescent"] and ledger.report()["active_async_attempts"] == 0
            final_dispatches = len(wire_dispatches) if final_ownership else None
            parent_close_acknowledged = (ledger.close(settled=final_ownership)
                if parent_control is not None else None)
            if failure is not None:
                if parent_control is not None:
                    failure["parent_accounting_close_acknowledged"] = parent_close_acknowledged
                failure.update(externalInferenceRequests=final_dispatches if subscription else 0,
                               known_partial_dispatches=len(wire_dispatches),
                               native_io=native_io.report(), ledger=ledger.report(),
                               sdk_idle_cleanup=sdk_idle_cleanup,
                               accounting_phase="after_native_and_transport_cleanup")
                (output / "native-case-failure.json").write_text(json.dumps(failure, indent=2) + "\n")
            if result is not None:
                if parent_control is not None:
                    result["parent_accounting_close_acknowledged"] = parent_close_acknowledged
                    result["whole_sample_complete"] = bool(result.get("whole_sample_complete") and parent_close_acknowledged)
                result["http_dispatch_attempts"] = final_dispatches
                result["known_partial_dispatches"] = len(wire_dispatches)
                result["dispatch_accounting_phase"] = "after_native_and_transport_cleanup"
                result["fixture_cleanup"] = {"explicit_native_response_close": all(x.native_closed for x in upstreams), "native_io": native_io.report(),
                                             "ledger": ledger.report(), "refused_sdk": list(refused_sdk),
                                             "sdk_idle_cleanup": sdk_idle_cleanup}
                if subscription or final_cm:
                    result["whole_sample_complete"] = bool(
                        result.get("whole_sample_complete")
                        and native_io.report()["quiescent"]
                        and all(x.native_closed for x in upstreams)
                        and all(x.closed for x in stream_bindings)
                        and ledger.report()["active_async_attempts"] == 0
                        and (not final_cm or (ledger.report()["complete"] and all(
                            item.get("all_idle_done") and not item.get("other_pending")
                            for item in sdk_idle_cleanup))))
                (output / "completed-cases.json").write_text(json.dumps(results, indent=2) + "\n")

    async def run():
        if args.case == "native-loop":
            await case(final_cm["participant"]["run_id"] if final_cm else selected_slot["sample_id"] if selected_slot else "native-loop-sdk-composition", "foreground_contact", stream=True, native_loop=True)
        if args.case in {"all", "stream-cases", "contact-stream"}:
            await case("native-bridge-contact-stream", "foreground_contact", stream=True)
        if args.case in {"all", "work-nonstream"}:
            await case("native-bridge-work-nonstream", "helper", stream=False)
        if args.case in {"all", "stream-cases", "partial-stop"}:
            await case("native-bridge-partial-stop", "foreground_contact", stream=True, partial=True)

    asyncio.run(run())
    dispatch_counts = [x.get("http_dispatch_attempts") for x in results]
    final_dispatch_total = sum(dispatch_counts) if dispatch_counts and all(x is not None for x in dispatch_counts) else None
    report = {"sourceCommit": PIN, "scope": "Native model, async SDK, registered OAuth routes and owned auth; upstream " + ("subscription" if subscription else "mocked"),
              "cases": results, "externalInferenceRequests": final_dispatch_total if subscription else 0,
              "known_partial_dispatches": sum(x.get("known_partial_dispatches", 0) for x in results), "providerMode": args.provider_mode, "productionRuntimeChanges": 0,
              "nativeClientVersion": codex.resolve_codex_version(), "admission": "not_admitted",
              "configuredStreamingCapabilities": capability_readback,
              "comparisonStreamGuard": {"enabled": args.bind_stream, "nativeSourceModified": False,
                                        "comparisonStopAssistance": args.bind_stream,
                                        "scope": "Explicit comparison-side ownership/close assistance; stock stop limitation retained."},
              "sourceHashes": {str(Path(module.__file__).relative_to(source)): hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                               for module in (codex, routes, model_config)},
              "limitations": ["Not a deployed UI/proxy. Full monologue is observed only in the explicit native-loop case.", "Raw SDK-consumed usage in subscription mode; synthetic usage in fixture mode. No cloud cancellation claim.",
                              "Streaming capability metadata is a configured declaration, not a live-provider observation; the pinned local SDK catalog lacked these models.",
                              "Only observed async SDK routes; sync/tools and other participant parity remain open."]}
    (output / "native-bridge-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(output), "final_cm": True, "admission": "not_admitted"}) if final_cm else json.dumps(report))


def main() -> None:
    with ExitStack() as lifecycle_stack:
        _main(lifecycle_stack)


if __name__ == "__main__":
    main()
