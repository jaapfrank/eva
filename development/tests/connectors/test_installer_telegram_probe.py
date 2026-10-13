from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import Iterator

import pytest

from eva.adapters.connectors.installer_telegram_probe import (
    TelegramInstallerReadOnlyProbe,
)
from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from eva.operations.installer_provider_verification import InstallerProviderVerifier
from eva.operations.installer_secret_store import InstallerSecretStore


TOKEN = "7000000001:Eva2SyntheticTokenValue_1234567890ABCDE"


class TelegramFixtureHandler(BaseHTTPRequestHandler):
    response_status = 200
    response_type = "application/json"
    response_payload: object = {
        "ok": True,
        "result": {"id": 7000000001, "is_bot": True, "username": "eva2_fixture_bot"},
    }
    requests: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).requests.append(
            {
                "body": body,
                "method": "POST",
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
            self.send_header("Location", "/must-not-follow")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def telegram_fixture(
    *, status: int = 200, payload: object | None = None, content_type: str = "application/json"
) -> Iterator[tuple[str, type[TelegramFixtureHandler]]]:
    handler = type("BoundTelegramFixtureHandler", (TelegramFixtureHandler,), {})
    handler.response_status = status
    handler.response_type = content_type
    handler.response_payload = (
        TelegramFixtureHandler.response_payload if payload is None else payload
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


def _verifier(root: Path, probe: TelegramInstallerReadOnlyProbe):
    capability_workspace = root / "capabilities"
    secret_workspace = root / "secrets"
    evidence_workspace = root / "development/evidence"
    for path in (capability_workspace, secret_workspace, evidence_workspace):
        path.mkdir(mode=0o700)
    workflow = InstallerCapabilityWorkflow(
        workspace=capability_workspace,
        clock=lambda: "2026-08-29T06:02:00Z",
    )
    store = InstallerSecretStore(
        workspace=secret_workspace,
        clock=lambda: "2026-08-29T06:00:00Z",
    )
    moments = iter(["2026-08-29T06:00:30Z", "2026-08-29T06:00:31Z"])
    ticks = iter([20.0, 20.05])
    verifier = InstallerProviderVerifier(
        capability_workflow=workflow,
        secret_store=store,
        evidence_workspace=evidence_workspace,
        probes={"telegram": probe},
        clock=lambda: next(moments),
        monotonic=lambda: next(ticks),
    )
    workflow.select(step_id="telegram", wanted=True)
    store.store(slot_id="telegram", secret=TOKEN)
    return workflow, verifier, evidence_workspace


def test_real_loopback_get_me_marks_ready_without_retaining_provider_payload(
    private_tmp_path: Path,
) -> None:
    with telegram_fixture() as (origin, handler):
        probe = TelegramInstallerReadOnlyProbe(
            api_base_url=origin, allow_insecure_loopback_test=True
        )
        workflow, verifier, evidence_workspace = _verifier(private_tmp_path, probe)
        result = verifier.verify(slot_id="telegram", timeout_seconds=2)

    assert result.outcome == "ready"
    assert workflow.status().step("telegram").state == "ready"
    assert handler.requests == [
        {
            "body": b"{}",
            "method": "POST",
            "path": f"/bot{TOKEN}/getMe",
            "user_agent": "Eva2-Installer-Verification/1",
        }
    ]
    receipt = next(evidence_workspace.iterdir()).read_bytes()
    assert TOKEN.encode() not in receipt
    assert b"eva2_fixture_bot" not in receipt
    assert json.loads(receipt)["provider_payload_retained"] is False


@pytest.mark.parametrize(
    ("status", "payload", "expected_reason"),
    [
        (401, {"ok": False, "error_code": 401}, "unauthorized"),
        (200, b"not-json", "invalid_response"),
        (200, {"ok": True, "result": {"id": 1, "is_bot": False}}, "invalid_response"),
        (429, {"ok": False, "error_code": 429}, "unreachable"),
    ],
)
def test_get_me_failure_is_closed_and_never_ready(
    private_tmp_path: Path,
    status: int,
    payload: object,
    expected_reason: str,
) -> None:
    with telegram_fixture(status=status, payload=payload) as (origin, handler):
        probe = TelegramInstallerReadOnlyProbe(
            api_base_url=origin, allow_insecure_loopback_test=True
        )
        workflow, verifier, evidence_workspace = _verifier(private_tmp_path, probe)
        result = verifier.verify(slot_id="telegram", timeout_seconds=2)

    assert len(handler.requests) == 1
    assert result.outcome == "blocked"
    assert result.reason_code == expected_reason
    assert workflow.status().step("telegram").state == "blocked"
    assert TOKEN.encode() not in next(evidence_workspace.iterdir()).read_bytes()


def test_redirect_is_not_followed(private_tmp_path: Path) -> None:
    with telegram_fixture(status=302, payload=b"") as (origin, handler):
        probe = TelegramInstallerReadOnlyProbe(
            api_base_url=origin, allow_insecure_loopback_test=True
        )
        workflow, verifier, _ = _verifier(private_tmp_path, probe)
        result = verifier.verify(slot_id="telegram", timeout_seconds=2)

    assert len(handler.requests) == 1
    assert result.outcome == "blocked"
    assert result.reason_code == "unreachable"
    assert workflow.status().step("telegram").state == "blocked"


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "http://localhost:8080",
        "https://example.com",
        "https://api.telegram.org/extra",
        "https://user@api.telegram.org",
    ],
)
def test_probe_rejects_nonofficial_or_rebinding_prone_origin(origin: str) -> None:
    with pytest.raises(ValueError, match="Telegram"):
        TelegramInstallerReadOnlyProbe(
            api_base_url=origin,
            allow_insecure_loopback_test=origin.startswith("http://"),
        )


def test_invalid_token_is_blocked_before_network(private_tmp_path: Path) -> None:
    with telegram_fixture() as (origin, handler):
        probe = TelegramInstallerReadOnlyProbe(
            api_base_url=origin, allow_insecure_loopback_test=True
        )
        workflow, verifier, evidence_workspace = _verifier(private_tmp_path, probe)
        secret_file = private_tmp_path / "secrets" / "slot-telegram" / "secret.bin"
        secret_file.write_bytes(b"not-a-telegram-token-value")
        secret_file.chmod(0o600)
        result = verifier.verify(slot_id="telegram", timeout_seconds=2)

    assert handler.requests == []
    assert result.outcome == "blocked"
    assert result.reason_code == "unauthorized"
    assert workflow.status().step("telegram").state == "blocked"
    assert b"not-a-telegram-token" not in next(evidence_workspace.iterdir()).read_bytes()
