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
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

from cp001_accounting import ProviderSampleLedger, SampleAsyncStream

PIN = "e3051fb584b1a36be2b0a0c90606f1c2c2d356ec"
PROFILES = {"foreground_contact": ("gpt-6-sol", "none"),
            "helper": ("gpt-6-astra", "medium")}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--auth-store", type=Path, required=True)
    parser.add_argument("--dockerized", choices=["1"], required=True)
    parser.add_argument("--case", choices=["all", "stream-cases", "contact-stream", "work-nonstream", "partial-stop"], default="all")
    parser.add_argument("--bind-stream", action="store_true", help="Use the explicit comparison-side SDK stream guard")
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip() == PIN
    assert subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=source).returncode == 0
    assert not output.exists(), "Keep each route fixture's evidence separate"
    assert output != source and source not in output.parents
    os.umask(0o077)
    output.mkdir(parents=True)
    (output / "execution-source.json").write_text(json.dumps({
        "files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in [Path(__file__), Path(__file__).with_name("cp001_accounting.py")]},
        "sourceCommit": PIN, "selectedCase": args.case, "comparisonStreamGuard": args.bind_stream,
    }, indent=2) + "\n")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))

    def reject_connection(*args, **kwargs):
        raise RuntimeError("External network is disabled in this native bridge fixture")

    socket.socket.connect = reject_connection
    socket.socket.connect_ex = reject_connection
    from helpers import files, runtime
    runtime.initialize()
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
    auth = codex.load_auth(ensure_fresh=False)
    assert not codex.should_refresh(auth.access_token, auth.last_refresh), "Refresh the owned native auth before this network-isolated fixture"
    app = Flask("p1-native-bridge")
    routes.register_oauth_routes(app)
    limits = json.loads((here / "p1_participants_20260929.json").read_text())["developmentPlan"]["perSampleLimits"]
    results = []

    async def case(name, role, *, stream, partial=False):
        ledger = ProviderSampleLedger(name, limits, PROFILES)
        model_name, effort = PROFILES[role]
        upstreams, requests_seen, sdk_requests, native_responses, stream_bindings = [], [], [], [], []
        waiting = asyncio.Event()

        class Upstream(requests.Response):
            def __init__(self, attempt):
                super().__init__()
                self.attempt = attempt
                self.status_code = 200
                self.headers["Content-Type"] = "text/event-stream"
                self.native_closed = False

            def iter_content(self, chunk_size=1, decode_unicode=False):
                text = "Native bridge fixture completed."
                events = [
                    {"type": "response.output_text.delta", "delta": text, "output_index": 0, "content_index": 0},
                    {"type": "response.completed", "response": {
                        "id": "resp_bridge_fixture", "object": "response", "created_at": 0,
                        "model": model_name, "status": "completed", "usage": {
                            "input_tokens": 21, "output_tokens": 9, "total_tokens": 30},
                        "output": [{"type": "message", "id": "msg_fixture", "role": "assistant",
                                    "status": "completed", "content": [{"type": "output_text",
                                    "text": text, "annotations": []}]}]}}]
                for event in events:
                    if event["type"] == "response.completed":
                        ledger.finish(self.attempt, event["response"]["usage"])
                    yield ("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode()

            def close(self):
                self.native_closed = True
                ledger.incomplete(self.attempt, "native_upstream_closed_usage_unknown")

        def upstream_request(method, url, **kwargs):
            assert method == "POST" and url.endswith("/responses")
            headers = kwargs["headers"]
            assert headers["Authorization"] == "Bearer " + auth.access_token
            assert headers["chatgpt-account-id"] == auth.account_id
            wire = json.loads(kwargs["data"])
            assert wire["model"] == model_name and wire["reasoning"]["effort"] == effort
            attempt = ledger.begin(role, wire["model"], wire["reasoning"]["effort"])
            ledger.track_async_attempt(attempt)
            requests_seen.append({"model": wire["model"], "effort": wire["reasoning"]["effort"],
                                  "forced_stream": wire["stream"], "owned_auth_forwarded": True})
            response = Upstream(attempt)
            upstreams.append(response)
            return response

        class BridgeStream(httpx.AsyncByteStream):
            def __init__(self, response, context):
                self.response, self.context = response, context

            async def __aiter__(self):
                iterator = iter(self.response.response)
                index = 0
                sentinel = object()
                while (chunk := self.context.run(next, iterator, sentinel)) is not sentinel:
                    yield chunk
                    if partial and index == 0:
                        waiting.set()
                        await asyncio.Future()
                    index += 1

            async def aclose(self):
                self.context.run(self.response.close)

        class BridgeTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                assert request.url.path == "/oauth/codex/v1/responses"
                wire = json.loads(request.content)
                sdk_request = {"model": wire.get("model"), "stream": wire.get("stream")}
                sdk_requests.append(sdk_request)
                context = contextvars.Context()
                response = context.run(app.test_client().post, request.url.path, data=request.content,
                                       content_type="application/json", buffered=False)
                native_responses.append((context, response))
                sdk_request["native_content_type"] = response.content_type
                sdk_request["native_status"] = response.status_code
                (output / "sdk-request-boundary.json").write_text(json.dumps(sdk_requests, indent=2) + "\n")
                if response.status_code != 200:
                    context.run(response.close)
                    raise RuntimeError("Native proxy failed before a bound SDK response; raw diagnostics withheld")
                return httpx.Response(response.status_code, headers=dict(response.headers),
                                      stream=BridgeStream(response, context), request=request,
                                      extensions={"comparison_attempt": upstreams[-1].attempt})

        class BridgeHTTPHandler(AsyncHTTPHandler):
            def create_client(self, *args, **kwargs):
                return httpx.AsyncClient(transport=BridgeTransport())

        original_request = codex._UPSTREAM_SESSION.request
        original_responses = litellm_transport.aresponses
        codex._UPSTREAM_SESSION.request = upstream_request

        async def bound_responses(**request):
            iterator = await original_responses(**request)
            if request.get("stream"):
                # The fixture binds the actual response to its observed upstream
                # attempt. This is not an attestation of deployed proxy coverage.
                attempt = iterator.response.extensions["comparison_attempt"]
                bound = SampleAsyncStream(ledger, attempt, iterator)
                stream_bindings.append(bound)
                return bound
            return iterator

        if args.bind_stream:
            litellm_transport.aresponses = bound_responses
        client = BridgeHTTPHandler()
        model = model_config.build_chat_model() if role == "foreground_contact" else model_config.build_utility_model()
        model.kwargs["client"] = client
        observed = []

        async def observe(delta, full):
            observed.append(delta)

        task = None
        result = None
        try:
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
        finally:
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await client.close()
            for context, response in native_responses:
                context.run(response.close)
            for upstream in upstreams:
                upstream.close()
            codex._UPSTREAM_SESSION.request = original_request
            litellm_transport.aresponses = original_responses
            if result is not None:
                result["fixture_cleanup"] = {"explicit_native_response_close": True,
                                             "ledger": ledger.report()}
                (output / "completed-cases.json").write_text(json.dumps(results, indent=2) + "\n")

    async def run():
        if args.case in {"all", "stream-cases", "contact-stream"}:
            await case("native-bridge-contact-stream", "foreground_contact", stream=True)
        if args.case in {"all", "work-nonstream"}:
            await case("native-bridge-work-nonstream", "helper", stream=False)
        if args.case in {"all", "stream-cases", "partial-stop"}:
            await case("native-bridge-partial-stop", "foreground_contact", stream=True, partial=True)

    asyncio.run(run())
    report = {"sourceCommit": PIN, "scope": "Native model, real async SDK, registered native OAuth routes and owned auth lookup; upstream provider HTTP mocked.",
              "cases": results, "externalInferenceRequests": 0, "productionRuntimeChanges": 0,
              "nativeClientVersion": codex.resolve_codex_version(), "admission": "not_admitted",
              "configuredStreamingCapabilities": capability_readback,
              "comparisonStreamGuard": {"enabled": args.bind_stream, "nativeSourceModified": False,
                                        "comparisonStopAssistance": args.bind_stream,
                                        "scope": "Explicit comparison-side ownership/close assistance; stock stop limitation retained."},
              "sourceHashes": {str(Path(module.__file__).relative_to(source)): hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                               for module in (codex, routes, model_config)},
              "limitations": ["Not a deployed UI/proxy or full monologue run.", "Synthetic usage only; no provider inference or cloud cancellation.",
                              "Streaming capability metadata is a configured declaration, not a live-provider observation; the pinned local SDK catalog lacked these models.",
                              "Only observed async SDK routes; sync/tools and other participant parity remain open."]}
    (output / "native-bridge-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
