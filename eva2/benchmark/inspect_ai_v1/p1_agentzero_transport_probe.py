"""Offline pinned Agent Zero transport, real LiteLLM serialization, mocked HTTP only."""
import json
import socket

def reject_connection(*args, **kwargs):
    raise RuntimeError("Network connections are disabled in this offline probe")

socket.socket.connect = reject_connection
socket.socket.connect_ex = reject_connection
import hashlib
from pathlib import Path
from importlib.metadata import version
import httpx
import litellm
from litellm.llms.custom_httpx.http_handler import HTTPHandler
from helpers.litellm_transport import ResponsesTransport
from plugins._oauth.helpers import codex, config


def main():
    results = []
    original = codex.codex_config
    try:
        for name, model, effort, kwargs in [
            ("ordinary_none", "gpt-6-sol", "none", {"reasoning_effort": "none"}),
            ("extra_body_none", "gpt-6-sol", "none", {"extra_body": {"reasoning": {"effort": "none"}}}),
            ("ordinary_medium", "gpt-6-astra", "medium", {"reasoning_effort": "medium"}),
        ]:
            settings = config.codex_config({"codex": {"reasoning_effort": effort}})
            codex.codex_config = lambda: settings
            captured = []
            def handle(request):
                assert request.url.host == "offline.invalid"
                wire = json.loads(request.content)
                prepared = codex.prepare_responses_body(wire, force_stream=True)
                captured.append({"wire_reasoning": wire.get("reasoning"),
                                 "bridge_reasoning": prepared.get("reasoning"),
                                 "bridge_model": prepared["model"],
                                 "output_limit_preserved": "max_output_tokens" in prepared})
                return httpx.Response(200, json={"id": "resp_offline", "object": "response", "created_at": 0,
                    "model": model, "status": "completed", "output": [], "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}})
            client = HTTPHandler(client=httpx.Client(transport=httpx.MockTransport(handle)))
            request = ResponsesTransport.from_chat([{"role": "user", "content": "Offline shape only"}],
                {"responses_state": "local", "max_output_tokens": 256, **kwargs}, model=model)
            try:
                litellm.responses(model="openai/" + model, client=client, api_base="https://offline.invalid/v1", api_key="offline-placeholder", max_retries=0, **request)
            finally:
                client.close()
            assert len(captured) == 1
            results.append({"case": name, "requested_effort": effort, **captured[0]})
    finally:
        codex.codex_config = original
    assert [r["bridge_reasoning"]["effort"] for r in results] == ["high", "none", "medium"]
    print(json.dumps({"scope": "Actual Agent Zero ResponsesTransport, LiteLLM HTTP serialization; mocked HTTP fed to actual OAuth preparer",
        "upstream_calls": 0, "credentials": "dummy only", "cases": results,
        "versions": {key: version(key) for key in ["litellm", "openai", "httpx", "pillow"]},
        "limits": ["No agent loop, persisted model configuration, OAuth login, provider response or aggregate accounting verified"],
        "transport_sha256": hashlib.sha256(Path(__import__(ResponsesTransport.__module__, fromlist=["x"]).__file__).read_bytes()).hexdigest()}, indent=2))

if __name__ == "__main__":
    main()
