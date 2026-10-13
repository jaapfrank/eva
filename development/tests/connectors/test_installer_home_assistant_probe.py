from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import threading
from typing import Iterator

import pytest

from eva.adapters.connectors.installer_home_assistant_probe import (
    HomeAssistantInstallerReadOnlyProbe,
)
from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from eva.operations.installer_provider_verification import (
    InstallerProviderVerificationError,
    InstallerProviderVerifier,
)
from tools.installer_provider_verification_cli import (
    run_installer_provider_verification,
)
from eva.operations.installer_secret_store import InstallerSecretStore


TOKEN = "Eva2SyntheticHomeAssistantToken_1234567890ABC"


class HomeAssistantFixtureHandler(BaseHTTPRequestHandler):
    response_status = 200
    response_type = "application/json"
    response_payload: object = {"message": "API running."}
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
            self.send_header("Location", "/api/states")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def home_assistant_fixture(
    *,
    status: int = 200,
    payload: object | None = None,
    content_type: str = "application/json",
) -> Iterator[tuple[str, type[HomeAssistantFixtureHandler]]]:
    handler = type(
        "BoundHomeAssistantFixtureHandler", (HomeAssistantFixtureHandler,), {}
    )
    handler.response_status = status
    handler.response_type = content_type
    handler.response_payload = (
        HomeAssistantFixtureHandler.response_payload if payload is None else payload
    )
    handler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _verifier(root: Path, probe: HomeAssistantInstallerReadOnlyProbe):
    capability_workspace = root / "capabilities"
    secret_workspace = root / "secrets"
    evidence_workspace = root / "development/evidence"
    for path in (capability_workspace, secret_workspace, evidence_workspace):
        path.mkdir(mode=0o700)
    workflow = InstallerCapabilityWorkflow(
        workspace=capability_workspace,
        clock=lambda: "2026-08-29T00:30:00Z",
    )
    store = InstallerSecretStore(
        workspace=secret_workspace,
        clock=lambda: "2026-08-29T00:30:01Z",
    )
    moments = iter(["2026-08-29T00:30:02Z", "2026-08-29T00:30:03Z"])
    ticks = iter([30.0, 30.05])
    verifier = InstallerProviderVerifier(
        capability_workflow=workflow,
        secret_store=store,
        evidence_workspace=evidence_workspace,
        probes={"home_assistant": probe},
        clock=lambda: next(moments),
        monotonic=lambda: next(ticks),
    )
    workflow.select(step_id="home_assistant", wanted=True)
    store.store(slot_id="home_assistant", secret=TOKEN)
    return workflow, verifier, evidence_workspace


def test_real_loopback_api_root_marks_ready_without_retaining_payload(
    private_tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with home_assistant_fixture() as (origin, handler):
        workflow, verifier, evidence = _verifier(
            private_tmp_path,
            HomeAssistantInstallerReadOnlyProbe(base_url=origin),
        )
        arguments = [
            "--slot",
            "home_assistant",
            "--home-assistant-url",
            origin,
            "--capability-workspace",
            str(verifier.capability_workflow.workspace),
            "--secret-workspace",
            str(verifier.secret_store.workspace),
            "--evidence-workspace",
            str(evidence),
            "--timeout-seconds",
            "2",
        ]
        exit_code = run_installer_provider_verification(arguments)
        restart_exit_code = run_installer_provider_verification(arguments)

    outputs = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(outputs) == 2
    result, restart = outputs
    assert exit_code == 0
    assert restart_exit_code == 2
    assert result["outcome"] == "ready"
    assert result["reason_code"] == "verified"
    assert restart["status"] == "failed"
    assert workflow.status().step("home_assistant").state == "ready"
    assert handler.requests == [
        {
            "authorization": f"Bearer {TOKEN}",
            "method": "GET",
            "path": "/api/",
            "user_agent": "Eva2-Installer-Verification/1",
        }
    ]
    receipt = next(evidence.iterdir()).read_bytes()
    assert TOKEN.encode() not in receipt
    assert b"API running" not in receipt
    assert json.loads(receipt)["provider_payload_retained"] is False


@pytest.mark.parametrize(
    ("status", "payload", "content_type", "expected_reason"),
    [
        (401, {"message": "unauthorized"}, "application/json", "unauthorized"),
        (200, b"not-json", "application/json", "invalid_response"),
        (200, {"message": "different"}, "application/json", "invalid_response"),
        (200, {"message": "API running."}, "text/plain", "invalid_response"),
        (503, {"message": "unavailable"}, "application/json", "unreachable"),
    ],
)
def test_failure_is_closed_and_never_ready(
    private_tmp_path: Path,
    status: int,
    payload: object,
    content_type: str,
    expected_reason: str,
) -> None:
    with home_assistant_fixture(
        status=status, payload=payload, content_type=content_type
    ) as (origin, handler):
        workflow, verifier, evidence = _verifier(
            private_tmp_path,
            HomeAssistantInstallerReadOnlyProbe(base_url=origin),
        )
        result = verifier.verify(slot_id="home_assistant", timeout_seconds=2)

    assert len(handler.requests) == 1
    assert result.outcome == "blocked"
    assert result.reason_code == expected_reason
    assert workflow.status().step("home_assistant").state == "blocked"
    assert TOKEN.encode() not in next(evidence.iterdir()).read_bytes()


def test_redirect_is_not_followed(private_tmp_path: Path) -> None:
    with home_assistant_fixture(status=302, payload=b"") as (origin, handler):
        workflow, verifier, _ = _verifier(
            private_tmp_path,
            HomeAssistantInstallerReadOnlyProbe(base_url=origin),
        )
        result = verifier.verify(slot_id="home_assistant", timeout_seconds=2)

    assert len(handler.requests) == 1
    assert result.reason_code == "unreachable"
    assert workflow.status().step("home_assistant").state == "blocked"


@pytest.mark.parametrize(
    "origin",
    [
        "http://8.8.8.8:8123",
        "https://example.com/path",
        "http://user@127.0.0.1:8123",
        "ftp://127.0.0.1:8123",
        "http://bad_host.local:8123",
        "http://[::]:8123",
    ],
)
def test_probe_rejects_nonlocal_or_invalid_origin(origin: str) -> None:
    with pytest.raises(ValueError, match="Home Assistant"):
        HomeAssistantInstallerReadOnlyProbe(base_url=origin)


def test_hostname_resolution_must_remain_local(
    private_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = HomeAssistantInstallerReadOnlyProbe(base_url="http://ha.local:8123")
    workflow, verifier, _ = _verifier(private_tmp_path, probe)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("8.8.8.8", 8123),
            )
        ],
    )

    result = verifier.verify(slot_id="home_assistant", timeout_seconds=2)

    assert result.reason_code == "unreachable"
    assert workflow.status().step("home_assistant").state == "blocked"


def test_tampered_invalid_token_is_rejected_before_dns_or_network(
    private_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with home_assistant_fixture() as (origin, handler):
        workflow, verifier, _ = _verifier(
            private_tmp_path,
            HomeAssistantInstallerReadOnlyProbe(base_url=origin),
        )
        secret_file = (
            private_tmp_path
            / "secrets"
            / "slot-home_assistant"
            / "secret.bin"
        )
        secret_file.write_bytes(b"invalid token value")
        secret_file.chmod(0o600)
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *_args, **_kwargs: pytest.fail("DNS should not be used"),
        )
        with pytest.raises(
            InstallerProviderVerificationError, match="secret is unavailable"
        ):
            verifier.verify(slot_id="home_assistant", timeout_seconds=2)

    assert handler.requests == []
    assert workflow.status().step("home_assistant").state == "selected"
