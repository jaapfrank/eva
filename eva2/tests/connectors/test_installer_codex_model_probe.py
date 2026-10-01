from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import threading
from typing import Iterator

import pytest

from eva.adapters.connectors.installer_codex_model_probe import (
    CodexInstallerModelProbe,
)
from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from eva.operations.installer_provider_verification import InstallerProviderVerifier
from tools.installer_provider_verification_cli import (
    run_installer_provider_verification,
)
from eva.operations.installer_secret_store import InstallerSecretStore


ACCESS_TOKEN = "synthetic-access-token-value-123456789"
ACCOUNT_ID = "synthetic-account-id"
LEASE = json.dumps(
    {
        "access_token": ACCESS_TOKEN,
        "account_id": ACCOUNT_ID,
        "contract": "EvaModelAccessLease/v1",
        "source_sha256": "a" * 64,
    },
    separators=(",", ":"),
    sort_keys=True,
)


def _success_stream(text: str = "EVA2_MODEL_READY") -> bytes:
    events = [
        {"type": "response.output_text.delta", "delta": text},
        {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "error": None,
                "usage": {
                    "input_tokens": 17,
                    "output_tokens": 5,
                    "total_tokens": 22,
                },
            },
        },
    ]
    return b"".join(
        b"data: " + json.dumps(event, separators=(",", ":")).encode() + b"\n\n"
        for event in events
    ) + b"data: [DONE]\n\n"


class CodexFixtureHandler(BaseHTTPRequestHandler):
    response_status = 200
    response_type: str | None = "text/event-stream"
    response_payload = _success_stream()
    requests: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).requests.append(
            {
                "account": self.headers.get("OpenAI-Account-ID"),
                "authorization": self.headers.get("Authorization"),
                "body": json.loads(body),
                "method": "POST",
                "path": self.path,
            }
        )
        raw = self.response_payload
        self.send_response(self.response_status)
        if self.response_type is not None:
            self.send_header("Content-Type", self.response_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def codex_fixture(
    *, status: int = 200, payload: bytes | None = None, content_type: str | None = "text/event-stream"
) -> Iterator[tuple[str, type[CodexFixtureHandler]]]:
    handler = type("BoundCodexFixtureHandler", (CodexFixtureHandler,), {})
    handler.response_status = status
    handler.response_type = content_type
    handler.response_payload = _success_stream() if payload is None else payload
    handler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            f"http://127.0.0.1:{server.server_port}/backend-api/codex/responses",
            handler,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if thread.is_alive():
            raise RuntimeError("Codex fixture server did not stop")


def _verifier(root: Path, probe: CodexInstallerModelProbe):
    capability = root / "capabilities"
    secrets = root / "secrets"
    evidence = root / "evidence"
    for path in (capability, secrets, evidence):
        path.mkdir(mode=0o700)
    workflow = InstallerCapabilityWorkflow(workspace=capability)
    store = InstallerSecretStore(workspace=secrets)
    workflow.select(step_id="model_provider", wanted=True)
    store.store(slot_id="model_provider", secret=LEASE)
    verifier = InstallerProviderVerifier(
        capability_workflow=workflow,
        secret_store=store,
        evidence_workspace=evidence,
        probes={"model_provider": probe},
    )
    return workflow, verifier, evidence


def _arguments(verifier: InstallerProviderVerifier, evidence: Path) -> list[str]:
    return [
        "--slot",
        "model_provider",
        "--capability-workspace",
        str(verifier.capability_workflow.workspace),
        "--secret-workspace",
        str(verifier.secret_store.workspace),
        "--evidence-workspace",
        str(evidence),
        "--timeout-seconds",
        "2",
    ]


def test_real_loopback_generation_records_usage_without_retaining_payload(
    private_tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with codex_fixture() as (endpoint, handler):
        probe = CodexInstallerModelProbe(
            endpoint=endpoint, allow_insecure_loopback_test=True
        )
        workflow, verifier, evidence = _verifier(private_tmp_path, probe)
        arguments = _arguments(verifier, evidence)
        exit_code = run_installer_provider_verification(
            arguments, probes={"model_provider": probe}
        )
        restart_code = run_installer_provider_verification(
            arguments, probes={"model_provider": probe}
        )

    outputs = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert exit_code == 0
    assert restart_code == 2
    assert outputs[0]["outcome"] == "ready"
    assert outputs[1]["status"] == "failed"
    assert workflow.status().step("model_provider").state == "ready"
    assert len(handler.requests) == 1
    request = handler.requests[0]
    assert request["path"] == "/backend-api/codex/responses"
    assert request["authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert request["account"] == ACCOUNT_ID
    body = request["body"]
    assert isinstance(body, dict)
    assert body["tools"] == []
    assert body["tool_choice"] == "none"
    assert body["store"] is False
    receipt_raw = next(evidence.iterdir()).read_bytes()
    receipt = json.loads(receipt_raw)
    assert hashlib.sha256(receipt_raw).hexdigest() == outputs[0]["evidence_sha256"]
    assert receipt["contract"] == "EvaInstallerProviderVerificationReceipt/v4"
    assert receipt["configuration_sha256"] is None
    assert receipt["billable_request_attempted"] is True
    assert receipt["provider_operation"] == "bounded_generation"
    assert receipt["provider_scopes"] == [
        "model.response.generate:installer_probe"
    ]
    assert receipt["usage_measurement"] == "measured"
    assert receipt["input_tokens"] == 17
    assert receipt["output_tokens"] == 5
    assert receipt["cost_measurement"] == "not_reported_by_chatgpt_access"
    for private in (ACCESS_TOKEN, ACCOUNT_ID, "EVA2_MODEL_READY"):
        assert private.encode() not in receipt_raw


@pytest.mark.parametrize(
    ("status", "payload", "content_type", "reason"),
    [
        (401, b"denied", "text/event-stream", "unauthorized"),
        (503, b"down", "text/event-stream", "unreachable"),
        (200, b"not-sse", "application/json", "invalid_response"),
        (200, b"not-sse", None, "invalid_response"),
        (200, _success_stream(), "application/json", "invalid_response"),
        (200, _success_stream("almost ready"), "text/event-stream", "invalid_response"),
        (
            200,
            b'data: {"type":"response.completed","response":{"status":"completed","error":null,"usage":null}}\n\n',
            "text/event-stream",
            "invalid_response",
        ),
    ],
)
def test_provider_failures_remain_closed(
    private_tmp_path: Path,
    status: int,
    payload: bytes,
    content_type: str | None,
    reason: str,
) -> None:
    with codex_fixture(status=status, payload=payload, content_type=content_type) as (
        endpoint,
        handler,
    ):
        workflow, verifier, _evidence = _verifier(
            private_tmp_path,
            CodexInstallerModelProbe(
                endpoint=endpoint, allow_insecure_loopback_test=True
            ),
        )
        result = verifier.verify(slot_id="model_provider", timeout_seconds=2)
    assert len(handler.requests) == 1
    assert result.outcome == "blocked"
    assert result.reason_code == reason
    assert workflow.status().step("model_provider").state == "blocked"


def test_completed_generation_without_content_type_preserves_measured_usage() -> None:
    with codex_fixture(content_type=None) as (endpoint, handler):
        observation = CodexInstallerModelProbe(
            endpoint=endpoint, allow_insecure_loopback_test=True
        ).read_only_probe(secret=memoryview(LEASE.encode()), timeout_seconds=2)
    assert len(handler.requests) == 1
    assert observation.outcome == "ready"
    assert observation.input_tokens == 17
    assert observation.output_tokens == 5


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://chatgpt.com/backend-api/codex/responses",
        "https://example.com/backend-api/codex/responses",
        "https://chatgpt.com/v1/responses",
        "https://user@chatgpt.com/backend-api/codex/responses",
        "http://localhost/backend-api/codex/responses",
        "http://8.8.8.8/backend-api/codex/responses",
    ],
)
def test_probe_rejects_untrusted_endpoint(endpoint: str) -> None:
    with pytest.raises(ValueError, match="Codex"):
        CodexInstallerModelProbe(
            endpoint=endpoint, allow_insecure_loopback_test=True
        )


def test_full_login_or_refresh_authority_is_rejected_before_network() -> None:
    with codex_fixture() as (endpoint, handler):
        probe = CodexInstallerModelProbe(
            endpoint=endpoint, allow_insecure_loopback_test=True
        )
        invalid = json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": ACCESS_TOKEN,
                    "account_id": ACCOUNT_ID,
                    "refresh_token": "must-never-leave-owner",
                },
            },
            separators=(",", ":"),
        ).encode()
        with pytest.raises(Exception) as captured:
            probe.read_only_probe(secret=memoryview(invalid), timeout_seconds=2)
    assert getattr(captured.value, "reason_code", None) == "unauthorized"
    assert handler.requests == []
