from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from eva.adapters.connectors.home_assistant_localtuya import (
    HomeAssistantLocalTuyaAdapter,
    LocalTuyaProbeSecret,
)
from eva.application.advanced_tasks import (
    AdvancedTaskError,
    LocalTuyaCanaryConfiguration,
    LocalTuyaEntityConfiguration,
)


ENTRY_ID = "01K3XJR7BTERG1Z20Q1J2HYP0B"
FLOW_ID = "flow-localtuya-canary-001"
LOCAL_KEY = "0123456789abcdef"


class _FlowServer:
    def __init__(self, *, reject_credentials: bool = False) -> None:
        self.requests: list[tuple[str, str, object]] = []
        self.deleted = False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                pass

            def do_POST(self) -> None:  # noqa: N802
                size = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(size)) if size else {}
                outer.requests.append(("POST", self.path, payload))
                if self.path.endswith("/options/flow"):
                    self._send(_form("init"))
                elif payload == {"action": "add_device"}:
                    self._send(_form("add_device"))
                elif payload == {"selected_device": "..."}:
                    self._send(_form("configure_device"))
                elif payload.get("no_additional_entities") is True:
                    self._send({"data": {}, "type": "create_entry"})
                elif set(payload) == {"platform_to_add"}:
                    self._send(_form("configure_entity"))
                elif "id" in payload:
                    self._send(_form("pick_entity_type"))
                elif reject_credentials:
                    self._send(
                        _form("configure_device", errors={"base": "invalid_auth"})
                    )
                else:
                    self._send(
                        _form(
                            "pick_entity_type",
                            data_schema=[
                                {
                                    "name": "platform_to_add",
                                    "options": ["light", "switch"],
                                    "required": True,
                                }
                            ],
                        )
                    )

            def do_DELETE(self) -> None:  # noqa: N802
                outer.deleted = True
                outer.requests.append(("DELETE", self.path, {}))
                self._send({})

            def _send(self, value: object) -> None:
                body = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> _FlowServer:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"


def _form(
    step_id: str,
    *,
    data_schema: object | None = None,
    errors: dict[str, str] | None = None,
) -> dict[str, object]:
    return {
        "data_schema": [] if data_schema is None else data_schema,
        "errors": {} if errors is None else errors,
        "flow_id": FLOW_ID,
        "step_id": step_id,
        "type": "form",
    }


def _secret() -> LocalTuyaProbeSecret:
    return LocalTuyaProbeSecret(
        friendly_name="Schuur 1",
        host="10.0.20.81",
        device_id="bf1234567890abcd",
        local_key=LOCAL_KEY,
        protocol_version="3.3",
    )


def test_localtuya_probe_validates_connection_then_aborts_before_device_write() -> None:
    with _FlowServer() as server:
        adapter = HomeAssistantLocalTuyaAdapter(
            base_url=server.base_url, token="test-token"
        )
        proof = asyncio.run(adapter.probe(config_entry_id=ENTRY_ID, secret=_secret()))

    assert proof.next_step_id == "pick_entity_type"
    assert proof.flow_aborted is True
    assert server.deleted is True
    assert [request[0] for request in server.requests] == [
        "POST",
        "POST",
        "POST",
        "POST",
        "DELETE",
    ]
    assert LOCAL_KEY not in json.dumps(proof.to_payload())
    assert LOCAL_KEY not in repr(_secret())
    assert len(proof.sha256) == 64


def test_localtuya_probe_aborts_failed_credentials_and_emits_no_proof() -> None:
    with _FlowServer(reject_credentials=True) as server:
        adapter = HomeAssistantLocalTuyaAdapter(
            base_url=server.base_url, token="test-token"
        )
        with pytest.raises(AdvancedTaskError, match="returned a form error"):
            asyncio.run(adapter.probe(config_entry_id=ENTRY_ID, secret=_secret()))

    assert server.deleted is True


def test_localtuya_canary_executes_exact_entity_flow_and_returns_secret_free_receipt() -> (
    None
):
    configuration = LocalTuyaCanaryConfiguration(
        friendly_name="Schuur 1 lokaal",
        host="10.0.20.81",
        device_id="bf1234567890abcd",
        local_key=LOCAL_KEY,
        protocol_version="3.3",
        entities=(
            LocalTuyaEntityConfiguration(
                "switch",
                {
                    "current": "18",
                    "friendly_name": "Schuur 1 lokaal",
                    "id": "1 (value: True)",
                },
            ),
        ),
    )
    with _FlowServer() as server:
        adapter = HomeAssistantLocalTuyaAdapter(
            base_url=server.base_url, token="test-token"
        )
        receipt = asyncio.run(
            adapter.create_canary(config_entry_id=ENTRY_ID, configuration=configuration)
        )

    assert receipt.configuration_sha256 == configuration.sha256
    assert receipt.configured_entity_count == 1
    assert receipt.disposition == "created"
    assert server.deleted is False
    assert [request[0] for request in server.requests] == ["POST"] * 7
    assert LOCAL_KEY not in json.dumps(receipt.to_payload())
    assert LOCAL_KEY not in repr(configuration)


@pytest.mark.parametrize(
    ("host", "local_key"),
    [
        ("8.8.8.8", LOCAL_KEY),
        ("127.0.0.1", LOCAL_KEY),
        ("10.0.20.81", "too-short"),
    ],
)
def test_localtuya_probe_rejects_nonlocal_or_malformed_secrets(
    host: str, local_key: str
) -> None:
    with pytest.raises(ValueError):
        LocalTuyaProbeSecret(
            friendly_name="Schuur 1",
            host=host,
            device_id="bf1234567890abcd",
            local_key=local_key,
        )
