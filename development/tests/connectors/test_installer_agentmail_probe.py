from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import Iterator

import pytest

from eva.adapters.connectors.installer_agentmail_probe import (
    AgentMailInstallerReadOnlyProbe,
)
from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from eva.operations.installer_provider_verification import InstallerProviderVerifier
from tools.installer_provider_verification_cli import (
    run_installer_provider_verification,
)
from eva.operations.installer_secret_store import InstallerSecretStore


INBOX_ID = "eva-test@agentmail.to"
API_KEY = "am_synthetic_installer_key_1234567890"


class AgentMailFixtureHandler(BaseHTTPRequestHandler):
    response_status = 200
    response_type = "application/json"
    response_payload: object = {
        "inbox_id": INBOX_ID,
        "email": INBOX_ID,
        "pod_id": "pod-synthetic",
        "metadata": {"private": "must-not-be-retained"},
    }
    requests: list[dict[str, object]] = []

    def do_GET(self) -> None:  # noqa: N802
        type(self).requests.append(
            {
                "authorization": self.headers.get("Authorization"),
                "method": "GET",
                "path": self.path,
                "user_agent": self.headers.get("User-Agent"),
            }
        )
        raw = (
            self.response_payload
            if isinstance(self.response_payload, bytes)
            else json.dumps(self.response_payload).encode()
        )
        self.send_response(self.response_status)
        self.send_header("Content-Type", self.response_type)
        if self.response_status == 302:
            self.send_header("Location", "/v0/inboxes")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def agentmail_fixture(
    *,
    status: int = 200,
    payload: object | None = None,
    content_type: str = "application/json",
) -> Iterator[tuple[str, type[AgentMailFixtureHandler]]]:
    handler = type("BoundAgentMailFixtureHandler", (AgentMailFixtureHandler,), {})
    handler.response_status = status
    handler.response_type = content_type
    handler.response_payload = (
        AgentMailFixtureHandler.response_payload if payload is None else payload
    )
    handler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v0", handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if thread.is_alive():
            raise RuntimeError("AgentMail fixture server did not stop")


def _verifier(root: Path, probe: AgentMailInstallerReadOnlyProbe):
    capability_workspace = root / "capabilities"
    secret_workspace = root / "secrets"
    evidence_workspace = root / "development/evidence"
    for path in (capability_workspace, secret_workspace, evidence_workspace):
        path.mkdir(mode=0o700)
    workflow = InstallerCapabilityWorkflow(
        workspace=capability_workspace,
        clock=lambda: "2026-08-29T00:10:00Z",
    )
    store = InstallerSecretStore(
        workspace=secret_workspace,
        clock=lambda: "2026-08-29T00:10:01Z",
    )
    moments = iter(["2026-08-29T00:10:02Z", "2026-08-29T00:10:03Z"])
    ticks = iter([40.0, 40.05])
    verifier = InstallerProviderVerifier(
        capability_workflow=workflow,
        secret_store=store,
        evidence_workspace=evidence_workspace,
        probes={"email": probe},
        clock=lambda: next(moments),
        monotonic=lambda: next(ticks),
    )
    workflow.select(step_id="email", wanted=True)
    store.store(slot_id="email", secret=API_KEY)
    return workflow, verifier, evidence_workspace


def _cli_arguments(verifier: InstallerProviderVerifier, evidence: Path) -> list[str]:
    return [
        "--slot",
        "email",
        "--agentmail-inbox-id",
        INBOX_ID,
        "--capability-workspace",
        str(verifier.capability_workflow.workspace),
        "--secret-workspace",
        str(verifier.secret_store.workspace),
        "--evidence-workspace",
        str(evidence),
        "--timeout-seconds",
        "2",
    ]


def test_real_loopback_inbox_read_marks_ready_without_retaining_payload(
    private_tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with agentmail_fixture() as (origin, handler):
        probe = AgentMailInstallerReadOnlyProbe(
            inbox_id=INBOX_ID,
            api_base_url=origin,
            allow_insecure_loopback_test=True,
        )
        workflow, verifier, evidence = _verifier(private_tmp_path, probe)
        arguments = _cli_arguments(verifier, evidence)
        exit_code = run_installer_provider_verification(
            arguments, probes={"email": probe}
        )
        restart_exit_code = run_installer_provider_verification(
            arguments, probes={"email": probe}
        )

    outputs = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert exit_code == 0
    assert restart_exit_code == 2
    assert outputs[0]["outcome"] == "ready"
    assert outputs[0]["reason_code"] == "verified"
    assert outputs[1]["status"] == "failed"
    assert workflow.status().step("email").state == "ready"
    assert handler.requests == [
        {
            "authorization": f"Bearer {API_KEY}",
            "method": "GET",
            "path": "/v0/inboxes/eva-test@agentmail.to",
            "user_agent": "Eva2-Installer-Verification/1",
        }
    ]
    receipt = next(evidence.iterdir()).read_bytes()
    assert API_KEY.encode() not in receipt
    assert INBOX_ID.encode() not in receipt
    assert b"pod-synthetic" not in receipt
    assert b"must-not-be-retained" not in receipt
    assert json.loads(receipt)["provider_payload_retained"] is False


@pytest.mark.parametrize(
    ("status", "payload", "content_type", "expected_reason"),
    [
        (401, {"detail": "unauthorized"}, "application/json", "unauthorized"),
        (404, {"detail": "not found"}, "application/json", "unauthorized"),
        (200, b"not-json", "application/json", "invalid_response"),
        (200, {"inbox_id": "other"}, "application/json", "invalid_response"),
        (200, AgentMailFixtureHandler.response_payload, "text/plain", "invalid_response"),
        (503, {"detail": "unavailable"}, "application/json", "unreachable"),
        (302, b"", "application/json", "unreachable"),
    ],
)
def test_failure_is_closed_and_never_ready(
    private_tmp_path: Path,
    status: int,
    payload: object,
    content_type: str,
    expected_reason: str,
) -> None:
    with agentmail_fixture(
        status=status, payload=payload, content_type=content_type
    ) as (origin, handler):
        workflow, verifier, evidence = _verifier(
            private_tmp_path,
            AgentMailInstallerReadOnlyProbe(
                inbox_id=INBOX_ID,
                api_base_url=origin,
                allow_insecure_loopback_test=True,
            ),
        )
        result = verifier.verify(slot_id="email", timeout_seconds=2)

    assert len(handler.requests) == 1
    assert result.outcome == "blocked"
    assert result.reason_code == expected_reason
    assert workflow.status().step("email").state == "blocked"
    assert API_KEY.encode() not in next(evidence.iterdir()).read_bytes()


@pytest.mark.parametrize(
    ("inbox_id", "origin", "allow_test"),
    [
        ("x", "https://api.agentmail.to/v0", False),
        (INBOX_ID, "http://api.agentmail.to/v0", False),
        (INBOX_ID, "https://example.com/v0", False),
        (INBOX_ID, "https://api.agentmail.to/v1", False),
        (INBOX_ID, "https://user@api.agentmail.to/v0", False),
        (INBOX_ID, "http://8.8.8.8/v0", True),
        (INBOX_ID, "http://localhost/v0", True),
    ],
)
def test_probe_rejects_invalid_inbox_or_origin(
    inbox_id: str, origin: str, allow_test: bool
) -> None:
    with pytest.raises(ValueError, match="AgentMail"):
        AgentMailInstallerReadOnlyProbe(
            inbox_id=inbox_id,
            api_base_url=origin,
            allow_insecure_loopback_test=allow_test,
        )


@pytest.mark.parametrize(
    "secret",
    [b"short", b"contains space but is otherwise long", b"\xff" * 20],
)
def test_invalid_key_is_rejected_before_network(secret: bytes) -> None:
    probe = AgentMailInstallerReadOnlyProbe(inbox_id=INBOX_ID)
    with pytest.raises(Exception) as captured:
        probe.read_only_probe(secret=memoryview(secret), timeout_seconds=2)
    assert getattr(captured.value, "reason_code", None) == "unauthorized"
