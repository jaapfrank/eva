#!/usr/bin/env python3
"""Prove secret rotation, removal, race closure and recovery over wizard HTTP."""

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

from eva.adapters.api.installer_wizard import InstallerBootstrap, create_installer_wizard
from eva.adapters.connectors.installer_telegram_probe import (
    TelegramInstallerReadOnlyProbe,
)
from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from eva.operations.installer_provider_verification import InstallerProviderVerifier
from eva.operations.installer_secret_lifecycle import (
    InstallerSecretLifecycle,
    InstallerSecretLifecycleError,
)
from eva.operations.installer_secret_store import InstallerSecretStore


ROOT = Path(__file__).resolve().parents[1]
OLD_TOKEN = "7000000001:Eva2OldSyntheticTokenValue_1234567890ABCDE"
NEW_TOKEN = "7000000001:Eva2NewSyntheticTokenValue_1234567890ABCDE"
FINAL_TOKEN = "7000000001:Eva2FinalSyntheticToken_1234567890ABCDEFG"
RECOVERY_OLD = "Eva2SyntheticModelAccessOld_1234567890"
RECOVERY_NEW = "Eva2SyntheticModelAccessNew_0987654321"
BOT_ID = 7_000_000_001
BOT_USERNAME = "eva2_lifecycle_synthetic_bot"
SOURCE_PATHS = (
    "src/eva/adapters/api/installer_provider_api.py",
    "src/eva/adapters/api/installer_secret_api.py",
    "src/eva/adapters/api/installer_wizard.py",
    "src/eva/adapters/api/installer_wizard_cli.py",
    "src/eva/operations/installer_capability_workflow.py",
    "src/eva/operations/installer_provider_verification.py",
    "src/eva/operations/installer_secret_lifecycle.py",
    "src/eva/operations/installer_secret_store.py",
    "tests/api/test_installer_wizard.py",
    "tests/operations/test_installer_capability_workflow.py",
    "tests/operations/test_installer_provider_verification.py",
    "tools/prove_installer_secret_lifecycle_wizard.py",
)


class _TelegramHandler(BaseHTTPRequestHandler):
    request_tokens: list[str] = []
    block_token: str | None = None
    block_started = threading.Event()
    release_block = threading.Event()

    def do_POST(self) -> None:  # noqa: N802
        prefix = "/bot"
        suffix = "/getMe"
        if not self.path.startswith(prefix) or not self.path.endswith(suffix):
            self.send_error(404)
            return
        token = self.path[len(prefix) : -len(suffix)]
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if token not in {OLD_TOKEN, NEW_TOKEN, FINAL_TOKEN} or body != b"{}":
            self.send_error(401)
            return
        type(self).request_tokens.append(token)
        if token == type(self).block_token:
            type(self).block_started.set()
            if not type(self).release_block.wait(timeout=5):
                self.send_error(503)
                return
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
    handler.request_tokens = []
    handler.block_token = None
    handler.block_started = threading.Event()
    handler.release_block = threading.Event()
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", handler
    finally:
        handler.release_block.set()
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
    connection = HTTPConnection("127.0.0.1", port, timeout=8)
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
    lifecycle = InstallerSecretLifecycle(
        capability_workflow=workflow,
        secret_store=store,
        evidence_workspace=evidence,
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
        secret_lifecycle=lifecycle,
    )


def _post(
    port: int,
    path: str,
    body: dict[str, object],
    cookie: str,
    origin: str,
    responses: list[bytes],
) -> dict[str, object]:
    status, _, raw = _request(
        port, "POST", path, body=body, cookie=cookie, origin=origin
    )
    responses.append(raw)
    if status != 200:
        raise RuntimeError(f"wizard request failed: {path}")
    return _json(raw)


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


def _prepare_interrupted_rotation(
    *, capability: Path, secrets: Path, evidence: Path
) -> None:
    workflow = InstallerCapabilityWorkflow(workspace=capability)
    store = InstallerSecretStore(workspace=secrets)
    lifecycle = InstallerSecretLifecycle(
        capability_workflow=workflow,
        secret_store=store,
        evidence_workspace=evidence,
    )
    workflow.select(step_id="model_provider", wanted=True)
    store.store(slot_id="model_provider", secret=RECOVERY_OLD)
    workflow.record_verification(
        step_id="model_provider", outcome="ready", evidence_sha256="c" * 64
    )
    real_replace = os.replace

    def interrupt_second_rename(source: object, destination: object) -> None:
        if str(source).endswith(".rotation-next"):
            raise OSError("synthetic process boundary")
        real_replace(source, destination)

    os.replace = interrupt_second_rename  # type: ignore[assignment]
    try:
        try:
            lifecycle.rotate(slot_id="model_provider", secret=RECOVERY_NEW)
        except InstallerSecretLifecycleError as exc:
            if "recovery" not in str(exc):
                raise
        else:
            raise RuntimeError("synthetic rotation was not interrupted")
    finally:
        os.replace = real_replace
    state = next(
        item for item in store.status() if item.slot_id == "model_provider"
    )
    if state.state != "needs_help":
        raise RuntimeError("interrupted rotation did not remain recoverable")


def prove(*, focused_tests_passed: int, broad_tests_passed: int) -> dict[str, object]:
    if focused_tests_passed < 1 or broad_tests_passed < focused_tests_passed:
        raise ValueError("test counts are invalid")
    responses: list[bytes] = []
    with tempfile.TemporaryDirectory(
        prefix="eva2-installer-secret-lifecycle-proof-", dir="/tmp"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        capability, secrets, evidence = (
            root / "capabilities",
            root / "secrets",
            root / "evidence",
        )
        for path in (capability, secrets, evidence):
            _private(path)
        with _telegram_server() as (telegram_origin, handler):
            first_token = "e" * 43
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
                _post(
                    port,
                    "/internal/v1/onboarding/capabilities/select",
                    {"step_id": "telegram", "wanted": True},
                    cookie,
                    origin,
                    responses,
                )
                stored = _post(
                    port,
                    "/internal/v1/onboarding/secrets/telegram/store",
                    {"secret": OLD_TOKEN},
                    cookie,
                    origin,
                    responses,
                )
                if stored.get("secret_returned") is not False:
                    raise RuntimeError("initial secret was returned")
                verified = _post(
                    port,
                    "/internal/v1/onboarding/providers/telegram/verify",
                    {"confirmation": "VERBINDING CONTROLEREN"},
                    cookie,
                    origin,
                    responses,
                )
                if verified.get("outcome") != "ready":
                    raise RuntimeError("initial provider verification failed")
                rotated = _post(
                    port,
                    "/internal/v1/onboarding/secrets/telegram/rotate",
                    {"confirmation": "TOEGANG VERVANGEN", "secret": NEW_TOKEN},
                    cookie,
                    origin,
                    responses,
                )
                if rotated.get("generation") != 2 or rotated.get(
                    "secret_returned"
                ) is not False:
                    raise RuntimeError("first secret rotation differs")

                handler.block_token = NEW_TOKEN
                race_result: list[tuple[int, bytes]] = []

                def verify_rotating_secret() -> None:
                    status, _, raw = _request(
                        port,
                        "POST",
                        "/internal/v1/onboarding/providers/telegram/verify",
                        body={"confirmation": "VERBINDING CONTROLEREN"},
                        cookie=cookie,
                        origin=origin,
                    )
                    race_result.append((status, raw))
                    responses.append(raw)

                race_thread = threading.Thread(
                    target=verify_rotating_secret, daemon=True
                )
                race_thread.start()
                if not handler.block_started.wait(timeout=3):
                    raise RuntimeError("concurrent provider verification did not start")
                second_rotation = _post(
                    port,
                    "/internal/v1/onboarding/secrets/telegram/rotate",
                    {"confirmation": "TOEGANG VERVANGEN", "secret": FINAL_TOKEN},
                    cookie,
                    origin,
                    responses,
                )
                handler.release_block.set()
                race_thread.join(timeout=5)
                if race_thread.is_alive() or not race_result:
                    raise RuntimeError("concurrent provider verification did not stop")
                if race_result[0][0] != 409:
                    raise RuntimeError("stale provider verification became authoritative")
                if second_rotation.get("generation") != 3:
                    raise RuntimeError("second secret rotation differs")
                handler.block_token = None
                current = _post(
                    port,
                    "/internal/v1/onboarding/providers/telegram/verify",
                    {"confirmation": "VERBINDING CONTROLEREN"},
                    cookie,
                    origin,
                    responses,
                )
                if current.get("outcome") != "ready":
                    raise RuntimeError("current provider verification failed")

            second_token = "f" * 43
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
                telegram = next(
                    item
                    for item in _json(raw)["slots"]  # type: ignore[union-attr]
                    if item["slot_id"] == "telegram"
                )
                if status != 200 or telegram["state"] != "ready":
                    raise RuntimeError("rotated provider state did not survive restart")
                request_count = len(handler.request_tokens)
                status, _, raw = _request(
                    port,
                    "POST",
                    "/internal/v1/onboarding/providers/telegram/verify",
                    body={"confirmation": "VERBINDING CONTROLEREN"},
                    cookie=cookie,
                    origin=origin,
                )
                responses.append(raw)
                if status != 409 or len(handler.request_tokens) != request_count:
                    raise RuntimeError("provider verification repeated after restart")
                status, _, raw = _request(
                    port,
                    "POST",
                    "/internal/v1/onboarding/secrets/telegram/remove",
                    body={"confirmation": "KOPPELING LOSMAKEN"},
                    cookie=cookie,
                    origin="http://evil.invalid",
                )
                responses.append(raw)
                if status != 403:
                    raise RuntimeError("cross-origin secret removal was not denied")
                removed = _post(
                    port,
                    "/internal/v1/onboarding/secrets/telegram/remove",
                    {"confirmation": "KOPPELING LOSMAKEN"},
                    cookie,
                    origin,
                    responses,
                )
                if removed.get("state") != "missing" or removed.get(
                    "secret_returned"
                ) is not False:
                    raise RuntimeError("secret removal differs")

            _prepare_interrupted_rotation(
                capability=capability, secrets=secrets, evidence=evidence
            )
            third_token = "g" * 43
            with _wizard_server(
                _app(
                    capability=capability,
                    secrets=secrets,
                    evidence=evidence,
                    telegram_origin=telegram_origin,
                    bootstrap_token=third_token,
                )
            ) as port:
                cookie = _bootstrap(port, third_token)
                origin = f"http://127.0.0.1:{port}"
                status, _, page = _request(port, "GET", "/", cookie=cookie)
                responses.append(page)
                if status != 200 or b"Onderbroken wijziging herstellen" not in page:
                    raise RuntimeError("recovery control was not rendered")
                status, _, raw = _request(
                    port,
                    "POST",
                    "/internal/v1/onboarding/secrets/model_provider/recover",
                    body={"confirmation": "WIJZIGING HERSTELLEN"},
                    cookie=cookie,
                    origin="http://evil.invalid",
                )
                responses.append(raw)
                if status != 403:
                    raise RuntimeError("cross-origin recovery was not denied")
                recovered = _post(
                    port,
                    "/internal/v1/onboarding/secrets/model_provider/recover",
                    {"confirmation": "WIJZIGING HERSTELLEN"},
                    cookie,
                    origin,
                    responses,
                )
                if (
                    recovered.get("state") != "present"
                    or recovered.get("generation") != 2
                    or recovered.get("secret_returned") is not False
                ):
                    raise RuntimeError("secret recovery differs")

            fourth_token = "h" * 43
            with _wizard_server(
                _app(
                    capability=capability,
                    secrets=secrets,
                    evidence=evidence,
                    telegram_origin=telegram_origin,
                    bootstrap_token=fourth_token,
                )
            ) as port:
                cookie = _bootstrap(port, fourth_token)
                status, _, raw = _request(
                    port,
                    "GET",
                    "/internal/v1/onboarding/secrets/status",
                    cookie=cookie,
                )
                responses.append(raw)
                model = next(
                    item
                    for item in _json(raw)["slots"]  # type: ignore[union-attr]
                    if item["slot_id"] == "model_provider"
                )
                if status != 200 or model["state"] != "present":
                    raise RuntimeError("recovered secret did not survive restart")

            expected_tokens = [OLD_TOKEN, NEW_TOKEN, FINAL_TOKEN]
            if handler.request_tokens != expected_tokens:
                raise RuntimeError("provider request sequence differs")
            retained = InstallerSecretStore(workspace=secrets)
            model_status, model_secret = retained.read_secret_with_status(
                slot_id="model_provider"
            )
            if model_status.generation != 2 or model_secret != RECOVERY_NEW.encode():
                raise RuntimeError("recovered secret bytes differ")
            telegram_status = next(
                item for item in retained.status() if item.slot_id == "telegram"
            )
            if telegram_status.state != "missing":
                raise RuntimeError("removed Telegram secret returned")

            evidence_files = tuple(evidence.iterdir())
            contracts: dict[str, int] = {}
            for path in evidence_files:
                raw = path.read_bytes()
                if path.name != f"{hashlib.sha256(raw).hexdigest()}.json":
                    raise RuntimeError("secret lifecycle evidence is not addressed")
                value = json.loads(raw)
                contract = value.get("contract")
                if not isinstance(contract, str):
                    raise RuntimeError("secret lifecycle evidence contract is missing")
                contracts[contract] = contracts.get(contract, 0) + 1
            expected_contracts = {
                "EvaInstallerProviderVerificationReceipt/v3": 3,
                "EvaInstallerSecretInvalidationReceipt/v1": 4,
                "EvaInstallerSecretLifecycleCompletionReceipt/v1": 4,
            }
            if contracts != expected_contracts:
                raise RuntimeError("secret lifecycle evidence count differs")
            evidence_raw = b"".join(path.read_bytes() for path in evidence_files)
            forbidden = tuple(
                value.encode()
                for value in (
                    OLD_TOKEN,
                    NEW_TOKEN,
                    FINAL_TOKEN,
                    RECOVERY_OLD,
                    RECOVERY_NEW,
                    str(BOT_ID),
                    BOT_USERNAME,
                )
            )
            if any(
                item in evidence_raw or any(item in response for response in responses)
                for item in forbidden
            ):
                raise RuntimeError("wizard returned secret or provider identity data")

    return {
        "boundaries": {
            "crossOriginLifecycleDenied": True,
            "providerPayloadReturned": False,
            "removedSecretStayedMissing": True,
            "secretReturned": False,
            "staleVerificationBecameReady": False,
        },
        "contract": "EvaInstallerSecretLifecycleWizardReceipt/v1",
        "issuedAt": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "loopbackRun": {
            "interruptedRotationRecovered": True,
            "providerRequests": 3,
            "secretLifecycleReceipts": 8,
            "temporaryWorkspaceRemovedAfterRun": True,
            "transport": "real_http_on_127.0.0.1",
            "wizardServerProcesses": 4,
        },
        "nonclaims": [
            "The proof used synthetic access and a loopback Telegram contract server; it did not contact Telegram or another live provider.",
            "The interrupted rotation was injected between the two atomic renames; it was recovered through the real wizard HTTP route after a new server start.",
            "This proves the local secret lifecycle, not a complete consumer installation, real provider rotation or managed-B promotion.",
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
            "broadInstallerTestsPassed": broad_tests_passed,
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
