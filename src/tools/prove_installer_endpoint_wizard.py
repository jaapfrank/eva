#!/usr/bin/env python3
"""Prove Home Assistant and AgentMail configuration through real wizard HTTP."""

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
from eva.adapters.connectors.installer_agentmail_probe import (
    AgentMailInstallerReadOnlyProbe,
)
from eva.adapters.connectors.installer_home_assistant_probe import (
    HomeAssistantInstallerReadOnlyProbe,
)
from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from eva.operations.installer_provider_configuration import (
    InstallerProviderConfigurationStore,
)
from eva.operations.installer_provider_verification import (
    InstallerProviderProbe,
    InstallerProviderVerifier,
)
from eva.operations.installer_secret_store import InstallerSecretStore


ROOT = Path(__file__).resolve().parents[2]
HA_TOKEN = "Eva2SyntheticHomeAssistantToken_123456789"
MAIL_KEY = "Eva2SyntheticAgentMailKey_123456789"
INBOX_ID = "eva-family-test@agentmail.to"
MAIL_ADDRESS = "eva-family-test@agentmail.to"
POD_ID = "synthetic-pod-1"
SOURCE_PATHS = (
    "src/eva/adapters/api/installer_provider_api.py",
    "src/eva/adapters/api/installer_wizard.py",
    "src/eva/bootstrap/installer_wizard_cli.py",
    "src/eva/adapters/connectors/installer_agentmail_probe.py",
    "src/eva/adapters/connectors/installer_home_assistant_probe.py",
    "src/eva/operations/installer_provider_configuration.py",
    "src/eva/operations/installer_provider_verification.py",
    "development/tests/api/test_installer_provider_api.py",
    "development/tests/operations/test_installer_provider_configuration.py",
    "src/tools/prove_installer_endpoint_wizard.py",
)


class _ProviderHandler(BaseHTTPRequestHandler):
    home_requests = 0
    mail_requests = 0
    matching_requests = 0

    def do_GET(self) -> None:  # noqa: N802
        authorization = self.headers.get("Authorization")
        if self.path == "/api/":
            type(self).home_requests += 1
            if authorization == f"Bearer {HA_TOKEN}":
                type(self).matching_requests += 1
            payload = {"message": "API running."}
        elif self.path == f"/v0/inboxes/{INBOX_ID}":
            type(self).mail_requests += 1
            if authorization == f"Bearer {MAIL_KEY}":
                type(self).matching_requests += 1
            payload = {
                "email": MAIL_ADDRESS,
                "inbox_id": INBOX_ID,
                "pod_id": POD_ID,
            }
        else:
            self.send_error(404)
            return
        raw = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _UnusedProbe:
    def read_only_probe(self, *, secret: memoryview, timeout_seconds: float):
        del secret, timeout_seconds
        raise RuntimeError("unused base probe was called")


@contextmanager
def _provider_server() -> Iterator[tuple[str, type[_ProviderHandler]]]:
    handler = type("BoundProviderHandler", (_ProviderHandler,), {})
    handler.home_requests = 0
    handler.mail_requests = 0
    handler.matching_requests = 0
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
            raise RuntimeError("provider contract server did not stop")


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
    configuration: Path,
    provider_origin: str,
    bootstrap_token: str,
):
    workflow = InstallerCapabilityWorkflow(workspace=capability)
    store = InstallerSecretStore(workspace=secrets)
    verifier = InstallerProviderVerifier(
        capability_workflow=workflow,
        secret_store=store,
        evidence_workspace=evidence,
        probes={"telegram": _UnusedProbe()},
    )
    configuration_store = InstallerProviderConfigurationStore(
        workspace=configuration
    )

    def factory(slot_id: str, value: str) -> InstallerProviderProbe:
        if slot_id == "home_assistant":
            return HomeAssistantInstallerReadOnlyProbe(base_url=value)
        if slot_id == "email":
            return AgentMailInstallerReadOnlyProbe(
                inbox_id=value,
                api_base_url=provider_origin + "/v0",
                allow_insecure_loopback_test=True,
            )
        raise ValueError("configured provider differs")

    bootstrap = InstallerBootstrap(
        token=bootstrap_token, created_at=1.0, clock=lambda: 1.0
    )
    return create_installer_wizard(
        _receipt(),
        bootstrap,
        capability_workflow=workflow,
        secret_store=store,
        provider_verifier=verifier,
        provider_configuration_store=configuration_store,
        configured_probe_factory=factory,
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


def prove(*, focused_tests_passed: int, broad_tests_passed: int) -> dict[str, object]:
    if focused_tests_passed < 1 or broad_tests_passed < focused_tests_passed:
        raise ValueError("test counts are invalid")
    responses: list[bytes] = []
    with tempfile.TemporaryDirectory(
        prefix="eva2-installer-endpoint-proof-", dir="/tmp"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        capability, secrets, evidence, configuration = (
            root / "capabilities",
            root / "secrets",
            root / "development/evidence",
            root / "configuration",
        )
        for path in (capability, secrets, evidence, configuration):
            _private(path)
        with _provider_server() as (provider_origin, handler):
            values = {
                "home_assistant": provider_origin,
                "email": INBOX_ID,
            }
            access = {"home_assistant": HA_TOKEN, "email": MAIL_KEY}
            first_token = "c" * 43
            with _wizard_server(
                _app(
                    capability=capability,
                    secrets=secrets,
                    evidence=evidence,
                    configuration=configuration,
                    provider_origin=provider_origin,
                    bootstrap_token=first_token,
                )
            ) as port:
                cookie = _bootstrap(port, first_token)
                origin = f"http://127.0.0.1:{port}"
                status, _, raw = _request(
                    port,
                    "POST",
                    "/internal/v1/onboarding/providers/home_assistant/configure",
                    body={"configuration": provider_origin},
                    cookie=cookie,
                    origin="http://evil.invalid",
                )
                responses.append(raw)
                if status != 403:
                    raise RuntimeError("cross-origin configuration was not denied")
                status, _, raw = _request(
                    port,
                    "GET",
                    "/internal/v1/onboarding/providers/status",
                    cookie=cookie,
                )
                responses.append(raw)
                provider_status = _json(raw)
                scoped_slots = {
                    item["slot_id"]: item
                    for item in provider_status["slots"]  # type: ignore[union-attr]
                }
                expected_scopes = {
                    "home_assistant": "home_assistant.api_root.read",
                    "email": "agentmail.inbox_identity.read",
                }
                if status != 200 or any(
                    scoped_slots[slot_id]["provider_scopes"][0]["scope_id"]
                    != scope_id
                    or not isinstance(
                        scoped_slots[slot_id]["provider_scope_manifest_sha256"], str
                    )
                    for slot_id, scope_id in expected_scopes.items()
                ):
                    raise RuntimeError("configured provider scopes differ")
                for slot_id in ("home_assistant", "email"):
                    _post(
                        port,
                        "/internal/v1/onboarding/capabilities/select",
                        {"step_id": slot_id, "wanted": True},
                        cookie,
                        origin,
                        responses,
                    )
                    configured = _post(
                        port,
                        f"/internal/v1/onboarding/providers/{slot_id}/configure",
                        {"configuration": values[slot_id]},
                        cookie,
                        origin,
                        responses,
                    )
                    if configured.get("configuration_returned") is not False:
                        raise RuntimeError("provider configuration was returned")
                    stored = _post(
                        port,
                        f"/internal/v1/onboarding/secrets/{slot_id}/store",
                        {"secret": access[slot_id]},
                        cookie,
                        origin,
                        responses,
                    )
                    if stored.get("secret_returned") is not False:
                        raise RuntimeError("provider secret was returned")
                    verified = _post(
                        port,
                        f"/internal/v1/onboarding/providers/{slot_id}/verify",
                        {"confirmation": "VERBINDING CONTROLEREN"},
                        cookie,
                        origin,
                        responses,
                    )
                    if (
                        verified.get("outcome") != "ready"
                        or verified.get("provider_payload_returned") is not False
                    ):
                        raise RuntimeError("configured provider verification failed")

            second_token = "d" * 43
            with _wizard_server(
                _app(
                    capability=capability,
                    secrets=secrets,
                    evidence=evidence,
                    configuration=configuration,
                    provider_origin=provider_origin,
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
                slots = {
                    item["slot_id"]: item
                    for item in payload["slots"]  # type: ignore[union-attr]
                }
                if status != 200 or any(
                    slots[slot_id]["state"] != "ready"
                    or slots[slot_id]["configuration_present"] is not True
                    for slot_id in ("home_assistant", "email")
                ):
                    raise RuntimeError("configured provider state did not resume")
                for slot_id in ("home_assistant", "email"):
                    status, _, raw = _request(
                        port,
                        "POST",
                        f"/internal/v1/onboarding/providers/{slot_id}/verify",
                        body={"confirmation": "VERBINDING CONTROLEREN"},
                        cookie=cookie,
                        origin=origin,
                    )
                    responses.append(raw)
                    if status != 409:
                        raise RuntimeError("provider was called twice after restart")

            evidence_files = tuple(evidence.iterdir())
            if (
                handler.home_requests != 1
                or handler.mail_requests != 1
                or handler.matching_requests != 2
                or len(evidence_files) != 2
            ):
                raise RuntimeError("configured provider request count differs")
            evidence_values = [json.loads(path.read_bytes()) for path in evidence_files]
            if any(
                value.get("contract") != "EvaInstallerProviderVerificationReceipt/v4"
                or not isinstance(value.get("configuration_sha256"), str)
                for value in evidence_values
            ):
                raise RuntimeError("provider evidence lacks configuration binding")
            evidence_by_slot = {value["slot_id"]: value for value in evidence_values}
            if any(
                evidence_by_slot[slot_id].get("provider_scopes") != [scope_id]
                or evidence_by_slot[slot_id].get(
                    "provider_scope_manifest_sha256"
                )
                != scoped_slots[slot_id]["provider_scope_manifest_sha256"]
                for slot_id, scope_id in expected_scopes.items()
            ):
                raise RuntimeError("provider evidence lacks scope binding")
            evidence_raw = b"".join(path.read_bytes() for path in evidence_files)
            forbidden = (
                HA_TOKEN.encode(),
                MAIL_KEY.encode(),
                INBOX_ID.encode(),
                MAIL_ADDRESS.encode(),
                POD_ID.encode(),
                provider_origin.encode(),
            )
            if any(
                item in evidence_raw or any(item in response for response in responses)
                for item in forbidden
            ):
                raise RuntimeError("wizard returned private configured provider data")

    return {
        "boundaries": {
            "configurationReturned": False,
            "crossOriginConfigurationDenied": True,
            "providerPayloadReturned": False,
            "providerScopesBound": True,
            "secretReturned": False,
            "secondProviderCallAfterRestart": False,
        },
        "contract": "EvaInstallerEndpointWizardReceipt/v1",
        "issuedAt": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "loopbackRun": {
            "configurationBoundReceipts": 2,
            "configuredProviderRequests": 2,
            "providerKinds": ["home_assistant", "agentmail"],
            "scopeManifests": 2,
            "temporaryWorkspaceRemovedAfterRun": True,
            "transport": "real_http_on_127.0.0.1",
            "wizardServerProcesses": 2,
        },
        "nonclaims": [
            "The proof used synthetic access and loopback contract servers; it contacted neither a real Home Assistant nor AgentMail.",
            "This proves browser configuration, verification and restart continuity, not a complete installation or live provider account.",
            "Real provider credentials, provider-granted least privilege and a complete installation remain separate gates.",
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
