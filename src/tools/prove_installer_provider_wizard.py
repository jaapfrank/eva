#!/usr/bin/env python3
"""Prove the browser-facing installer verification route over real loopback HTTP."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
from typing import Iterator

import uvicorn

from eva.adapters.api.installer_wizard import (
    InstallerBootstrap,
    create_installer_wizard,
)
from eva.adapters.connectors.installer_telegram_probe import (
    TelegramInstallerReadOnlyProbe,
)
from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from eva.operations.installer_provider_verification import InstallerProviderVerifier
from eva.operations.installer_secret_store import InstallerSecretStore


ROOT = Path(__file__).resolve().parents[2]
TOKEN = "7000000001:Eva2SyntheticTokenValue_1234567890ABCDE"
BOT_ID = 7_000_000_001
BOT_USERNAME = "eva2_installer_synthetic_bot"
SOURCE_PATHS = (
    "src/eva/adapters/api/installer_capability_api.py",
    "src/eva/adapters/api/installer_provider_api.py",
    "src/eva/adapters/api/installer_secret_api.py",
    "src/eva/adapters/api/installer_wizard.py",
    "src/eva/bootstrap/installer_wizard_cli.py",
    "src/eva/operations/installer_provider_verification.py",
    "development/tests/api/test_installer_provider_api.py",
    "development/tests/api/test_installer_wizard.py",
    "src/tools/prove_installer_provider_wizard.py",
)


class _TelegramHandler(BaseHTTPRequestHandler):
    request_count = 0
    matching_count = 0

    def do_POST(self) -> None:  # noqa: N802
        type(self).request_count += 1
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if self.path == f"/bot{TOKEN}/getMe" and body == b"{}":
            type(self).matching_count += 1
        raw = json.dumps(
            {
                "ok": True,
                "result": {
                    "id": BOT_ID,
                    "is_bot": True,
                    "username": BOT_USERNAME,
                },
            },
            separators=(",", ":"),
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _telegram_server() -> Iterator[tuple[str, type[_TelegramHandler]]]:
    handler = type("BoundTelegramHandler", (_TelegramHandler,), {})
    handler.request_count = 0
    handler.matching_count = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if thread.is_alive():
            raise RuntimeError("Telegram contract server did not stop")


@contextmanager
def _wizard_server(app: object) -> Iterator[int]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            app,  # type: ignore[arg-type]
            access_log=False,
            lifespan="off",
            log_level="critical",
        )
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2)
        raise RuntimeError("installer wizard did not start")
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("installer wizard did not stop")


def _receipt() -> dict[str, object]:
    return {
        "checks": [
            {"detail": "Ondersteund", "id": "release_support", "state": "passed"}
        ],
        "contract": "EvaInstallerPreflight/v1",
        "host": {
            "architecture": "amd64",
            "docker_version": "29.1.3",
            "operating_system": "Ubuntu",
            "python_version": "3.12.3",
        },
        "next": "De lokale wizard mag verder.",
        "non_claims": ["geen installatie"],
        "platform_lock_claim_state": "selected_for_validation",
        "read_only": True,
        "status": "ready",
    }


def _request(
    port: int,
    method: str,
    path: str,
    *,
    body: dict[str, object] | None = None,
    cookie: str | None = None,
    origin: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    raw = b"" if body is None else json.dumps(body, separators=(",", ":")).encode()
    headers = {"Accept": "application/json", "Connection": "close"}
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(raw))
    if cookie is not None:
        headers["Cookie"] = cookie
    if origin is not None:
        headers["Origin"] = origin
    connection = HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request(method, path, body=raw, headers=headers)
        response = connection.getresponse()
        return (
            response.status,
            {key.casefold(): value for key, value in response.getheaders()},
            response.read(),
        )
    finally:
        connection.close()


def _bootstrap(port: int, token: str) -> str:
    status, headers, raw = _request(
        port,
        "POST",
        "/internal/v1/bootstrap",
        body={"token": token},
        origin=f"http://127.0.0.1:{port}",
    )
    cookie = headers.get("set-cookie", "").split(";", 1)[0]
    if status != 204 or raw or not cookie.startswith("eva_installer_session="):
        raise RuntimeError("installer bootstrap exchange differs")
    return cookie


def _json(raw: bytes) -> dict[str, object]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("installer response was not an object")
    return value


def _private(path: Path) -> None:
    path.mkdir(mode=0o700)
    path.chmod(0o700)


def _app(
    *,
    capability: Path,
    secrets: Path,
    evidence: Path,
    telegram_origin: str,
    bootstrap_token: str,
):
    workflow = InstallerCapabilityWorkflow(workspace=capability)
    store = InstallerSecretStore(workspace=secrets)
    verifier = InstallerProviderVerifier(
        capability_workflow=workflow,
        secret_store=store,
        evidence_workspace=evidence,
        probes={
            "telegram": TelegramInstallerReadOnlyProbe(
                api_base_url=telegram_origin,
                allow_insecure_loopback_test=True,
            )
        },
    )
    bootstrap = InstallerBootstrap(
        token=bootstrap_token, created_at=1.0, clock=lambda: 1.0
    )
    return create_installer_wizard(
        _receipt(),
        bootstrap,
        capability_workflow=workflow,
        secret_store=store,
        provider_verifier=verifier,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def prove(*, focused_tests_passed: int, broad_tests_passed: int) -> dict[str, object]:
    if focused_tests_passed < 1 or broad_tests_passed < focused_tests_passed:
        raise ValueError("test counts are invalid")
    responses: list[bytes] = []
    with tempfile.TemporaryDirectory(
        prefix="eva2-installer-provider-ui-proof-", dir="/tmp"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        capability, secrets, evidence = (
            root / "capabilities",
            root / "secrets",
            root / "development/evidence",
        )
        for path in (capability, secrets, evidence):
            _private(path)
        with _telegram_server() as (telegram_origin, handler):
            first_token = "a" * 43
            with _wizard_server(
                _app(
                    capability=capability,
                    secrets=secrets,
                    evidence=evidence,
                    telegram_origin=telegram_origin,
                    bootstrap_token=first_token,
                )
            ) as port:
                cookie = _bootstrap(port, first_token)
                origin = f"http://127.0.0.1:{port}"
                status, _, raw = _request(
                    port,
                    "POST",
                    "/internal/v1/onboarding/capabilities/select",
                    body={"step_id": "telegram", "wanted": True},
                    cookie=cookie,
                    origin=origin,
                )
                responses.append(raw)
                if status != 200:
                    raise RuntimeError("wizard capability selection failed")
                status, _, raw = _request(
                    port,
                    "POST",
                    "/internal/v1/onboarding/secrets/telegram/store",
                    body={"secret": TOKEN},
                    cookie=cookie,
                    origin=origin,
                )
                responses.append(raw)
                if status != 200 or _json(raw).get("secret_returned") is not False:
                    raise RuntimeError("wizard secret intake failed")
                status, _, raw = _request(
                    port,
                    "GET",
                    "/internal/v1/onboarding/providers/status",
                    cookie=cookie,
                )
                responses.append(raw)
                provider_status = _json(raw)
                telegram_scope = next(
                    item
                    for item in provider_status["slots"]  # type: ignore[union-attr]
                    if item["slot_id"] == "telegram"
                )
                if (
                    status != 200
                    or telegram_scope["provider_scopes"]
                    != [
                        {
                            "description": "Alleen de identiteit van de testbot lezen",
                            "scope_id": "telegram.bot_identity.read",
                        }
                    ]
                    or not isinstance(
                        telegram_scope["provider_scope_manifest_sha256"], str
                    )
                ):
                    raise RuntimeError("wizard provider scope differs")
                status, _, raw = _request(
                    port,
                    "POST",
                    "/internal/v1/onboarding/providers/telegram/verify",
                    body={"confirmation": "VERBINDING CONTROLEREN"},
                    cookie=cookie,
                    origin="http://evil.invalid",
                )
                responses.append(raw)
                if status != 403 or handler.request_count != 0:
                    raise RuntimeError("wizard cross-origin verification was not denied")
                status, _, raw = _request(
                    port,
                    "POST",
                    "/internal/v1/onboarding/providers/telegram/verify",
                    body={"confirmation": "VERBINDING CONTROLEREN"},
                    cookie=cookie,
                    origin=origin,
                )
                responses.append(raw)
                verified = _json(raw)
                if (
                    status != 200
                    or verified.get("outcome") != "ready"
                    or verified.get("secret_returned") is not False
                    or verified.get("provider_payload_returned") is not False
                ):
                    raise RuntimeError("wizard provider verification failed")

            second_token = "b" * 43
            with _wizard_server(
                _app(
                    capability=capability,
                    secrets=secrets,
                    evidence=evidence,
                    telegram_origin=telegram_origin,
                    bootstrap_token=second_token,
                )
            ) as port:
                cookie = _bootstrap(port, second_token)
                origin = f"http://127.0.0.1:{port}"
                status, _, raw = _request(
                    port,
                    "GET",
                    "/internal/v1/onboarding/providers/status",
                    cookie=cookie,
                )
                responses.append(raw)
                payload = _json(raw)
                telegram = next(
                    item
                    for item in payload["slots"]  # type: ignore[union-attr]
                    if item["slot_id"] == "telegram"
                )
                if status != 200 or telegram["state"] != "ready":
                    raise RuntimeError("wizard provider state did not survive restart")
                status, _, raw = _request(
                    port,
                    "POST",
                    "/internal/v1/onboarding/providers/telegram/verify",
                    body={"confirmation": "VERBINDING CONTROLEREN"},
                    cookie=cookie,
                    origin=origin,
                )
                responses.append(raw)
                if status != 409:
                    raise RuntimeError("wizard repeated provider verification")

            evidence_files = tuple(evidence.iterdir())
            if (
                handler.request_count != 1
                or handler.matching_count != 1
                or len(evidence_files) != 1
            ):
                raise RuntimeError("wizard provider request count differs")
            evidence_raw = evidence_files[0].read_bytes()
            evidence_value = json.loads(evidence_raw)
            evidence_sha256 = hashlib.sha256(evidence_raw).hexdigest()
            if evidence_files[0].name != f"{evidence_sha256}.json":
                raise RuntimeError("wizard provider evidence is not content-addressed")
            if (
                evidence_value.get("provider_scopes")
                != ["telegram.bot_identity.read"]
                or evidence_value.get("provider_scope_manifest_sha256")
                != telegram_scope["provider_scope_manifest_sha256"]
            ):
                raise RuntimeError("wizard provider evidence lacks scope binding")
            forbidden = (TOKEN.encode(), str(BOT_ID).encode(), BOT_USERNAME.encode())
            if any(
                item in evidence_raw or any(item in response for response in responses)
                for item in forbidden
            ):
                raise RuntimeError("wizard retained private provider data")

    return {
        "boundaries": {
            "crossOriginWriteDenied": True,
            "providerPayloadReturned": False,
            "providerScopeBound": True,
            "secretReturned": False,
            "secondProviderCallAfterRestart": False,
        },
        "contract": "EvaInstallerProviderWizardReceipt/v1",
        "issuedAt": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "loopbackRun": {
            "browserSessions": 2,
            "providerRequests": 1,
            "providerVerificationReceipts": 1,
            "scopeManifests": 1,
            "temporaryWorkspaceRemovedAfterRun": True,
            "transport": "real_http_on_127.0.0.1",
            "wizardServerProcesses": 2,
        },
        "nonclaims": [
            "The proof used a synthetic Telegram token and loopback contract server; it did not contact Telegram.",
            "This proves browser controls for selection, one-time intake and verification, not a complete installation or a live provider account.",
            "Home Assistant and AgentMail need their non-secret endpoint choices before the same browser control can be enabled for them.",
        ],
        "source": {
            "commit": _source_commit(),
            "files": [
                {"path": path, "sha256": _sha256(ROOT / path)}
                for path in SOURCE_PATHS
            ],
        },
        "status": "passed",
        "verification": {
            "broadInstallerReleaseTestsPassed": broad_tests_passed,
            "focusedTestsPassed": focused_tests_passed,
            "networkUsedOutsideLoopback": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--focused-tests-passed", type=int, required=True)
    parser.add_argument("--broad-tests-passed", type=int, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    receipt = prove(
        focused_tests_passed=arguments.focused_tests_passed,
        broad_tests_passed=arguments.broad_tests_passed,
    )
    raw = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"
    if arguments.output is not None:
        descriptor = os.open(
            arguments.output,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    print(raw.decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
