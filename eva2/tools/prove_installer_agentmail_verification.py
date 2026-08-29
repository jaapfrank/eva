#!/usr/bin/env python3
"""Prove the installer AgentMail verifier against local HTTP contract servers."""

from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout
from datetime import UTC, datetime
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from typing import Iterator

from eva.adapters.connectors.installer_agentmail_probe import (
    AgentMailInstallerReadOnlyProbe,
)
from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from eva.operations.installer_provider_verification_cli import (
    run_installer_provider_verification,
)
from eva.operations.installer_secret_store import InstallerSecretStore


ROOT = Path(__file__).resolve().parents[1]
INBOX_ID = "eva-proof@agentmail.to"
API_KEY = "am_synthetic_proof_key_1234567890"
SOURCE_PATHS = (
    "src/eva/adapters/connectors/installer_agentmail_probe.py",
    "src/eva/operations/installer_provider_verification.py",
    "src/eva/operations/installer_provider_verification_cli.py",
    "tests/connectors/test_installer_agentmail_probe.py",
    "tests/operations/test_installer_provider_verification.py",
    "tests/operations/test_installer_provider_verification_cli.py",
    "tools/prove_installer_agentmail_verification.py",
)


class _AgentMailContractHandler(BaseHTTPRequestHandler):
    response_status = 200
    request_count = 0
    request_matches = 0

    def do_GET(self) -> None:  # noqa: N802
        type(self).request_count += 1
        if (
            self.path == f"/v0/inboxes/{INBOX_ID}"
            and self.headers.get("Authorization") == f"Bearer {API_KEY}"
            and self.headers.get("User-Agent")
            == "Eva2-Installer-Verification/1"
        ):
            type(self).request_matches += 1
        payload: object
        if self.response_status == 200:
            payload = {
                "email": INBOX_ID,
                "inbox_id": INBOX_ID,
                "metadata": {"private": "discard-this-provider-payload"},
                "pod_id": "pod-proof-private",
            }
        else:
            payload = {"detail": "rejected-private-provider-payload"}
        raw = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(self.response_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _contract_server(
    *, status: int
) -> Iterator[tuple[str, type[_AgentMailContractHandler]]]:
    handler = type("BoundAgentMailContractHandler", (_AgentMailContractHandler,), {})
    handler.response_status = status
    handler.request_count = 0
    handler.request_matches = 0
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
            raise RuntimeError("AgentMail contract server did not stop")


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700)
    path.chmod(0o700)


def _prepare(root: Path) -> tuple[Path, Path, Path]:
    capability = root / "capabilities"
    secrets = root / "secrets"
    evidence = root / "evidence"
    for path in (capability, secrets, evidence):
        _private_directory(path)
    workflow = InstallerCapabilityWorkflow(workspace=capability)
    workflow.select(step_id="email", wanted=True)
    InstallerSecretStore(workspace=secrets).store(slot_id="email", secret=API_KEY)
    return capability, secrets, evidence


def _run_cli(
    *,
    capability: Path,
    secrets: Path,
    evidence: Path,
    probe: AgentMailInstallerReadOnlyProbe,
) -> tuple[int, dict[str, object]]:
    stream = StringIO()
    with redirect_stdout(stream):
        code = run_installer_provider_verification(
            [
                "--slot",
                "email",
                "--agentmail-inbox-id",
                INBOX_ID,
                "--capability-workspace",
                str(capability),
                "--secret-workspace",
                str(secrets),
                "--evidence-workspace",
                str(evidence),
                "--timeout-seconds",
                "2",
            ],
            probes={"email": probe},
        )
    lines = stream.getvalue().splitlines()
    if len(lines) != 1:
        raise RuntimeError("installer verification CLI output was not singular")
    payload = json.loads(lines[0])
    if not isinstance(payload, dict):
        raise RuntimeError("installer verification CLI output was not an object")
    return code, payload


def _receipt(evidence: Path) -> tuple[str, bytes]:
    files = list(evidence.iterdir())
    if len(files) != 1 or not files[0].is_file():
        raise RuntimeError("installer verification receipt set differs")
    raw = files[0].read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if files[0].name != f"{digest}.json":
        raise RuntimeError("installer verification receipt is not content-addressed")
    return digest, raw


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
    with tempfile.TemporaryDirectory(
        prefix="eva2-installer-agentmail-proof-", dir="/tmp"
    ) as temporary:
        temporary_root = Path(temporary)
        temporary_root.chmod(0o700)

        success_root = temporary_root / "success"
        _private_directory(success_root)
        capability, secrets, evidence = _prepare(success_root)
        with _contract_server(status=200) as (origin, handler):
            probe = AgentMailInstallerReadOnlyProbe(
                inbox_id=INBOX_ID,
                api_base_url=origin,
                allow_insecure_loopback_test=True,
            )
            success_code, success = _run_cli(
                capability=capability,
                secrets=secrets,
                evidence=evidence,
                probe=probe,
            )
            restart_code, restart = _run_cli(
                capability=capability,
                secrets=secrets,
                evidence=evidence,
                probe=probe,
            )
        success_digest, success_raw = _receipt(evidence)
        if (
            success_code != 0
            or success.get("status") != "passed"
            or success.get("evidence_sha256") != success_digest
            or restart_code != 2
            or restart.get("status") != "failed"
            or handler.request_count != 1
            or handler.request_matches != 1
            or InstallerCapabilityWorkflow(workspace=capability)
            .status()
            .step("email")
            .state
            != "ready"
        ):
            raise RuntimeError("successful AgentMail installer proof differs")

        blocked_root = temporary_root / "blocked"
        _private_directory(blocked_root)
        blocked_capability, blocked_secrets, blocked_evidence = _prepare(blocked_root)
        with _contract_server(status=401) as (origin, blocked_handler):
            blocked_code, blocked = _run_cli(
                capability=blocked_capability,
                secrets=blocked_secrets,
                evidence=blocked_evidence,
                probe=AgentMailInstallerReadOnlyProbe(
                    inbox_id=INBOX_ID,
                    api_base_url=origin,
                    allow_insecure_loopback_test=True,
                ),
            )
        blocked_digest, blocked_raw = _receipt(blocked_evidence)
        if (
            blocked_code != 2
            or blocked.get("status") != "blocked"
            or blocked.get("reason_code") != "unauthorized"
            or blocked.get("evidence_sha256") != blocked_digest
            or blocked_handler.request_count != 1
            or blocked_handler.request_matches != 1
            or InstallerCapabilityWorkflow(workspace=blocked_capability)
            .status()
            .step("email")
            .state
            != "blocked"
        ):
            raise RuntimeError("blocked AgentMail installer proof differs")

        serialized = json.dumps(
            [success, restart, blocked], sort_keys=True, separators=(",", ":")
        ).encode()
        forbidden = (
            API_KEY.encode(),
            INBOX_ID.encode(),
            b"pod-proof-private",
            b"discard-this-provider-payload",
            b"rejected-private-provider-payload",
        )
        if any(
            value in serialized or value in success_raw or value in blocked_raw
            for value in forbidden
        ):
            raise RuntimeError("AgentMail verification retained private data")

    issued_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    return {
        "boundaries": {
            "browserProviderRouteExists": False,
            "inboxMetadataRetained": False,
            "messageContentRead": False,
            "mutationAttempted": False,
            "providerPayloadRetained": False,
            "secretReturned": False,
        },
        "contract": "EvaInstallerAgentMailReadOnlyVerificationReceipt/v1",
        "issuedAt": issued_at,
        "loopbackRun": {
            "operatorCliInvocations": 3,
            "providerRequests": 2,
            "restartAttemptProviderRequests": 0,
            "temporaryWorkspaceRemovedAfterRun": True,
            "transport": "real_http_on_127.0.0.1",
        },
        "networkBoundary": {
            "method": "GET",
            "officialOrigin": "https://api.agentmail.to/v0",
            "pathTemplate": "/v0/inboxes/:inbox_id",
            "redirectFollowed": False,
        },
        "nonclaims": [
            "The proof used a synthetic AgentMail key, inbox and loopback contract server; it did not contact AgentMail or a live mailbox.",
            "The inbox check proves authenticated read-only access to one selected inbox; it does not prove message listing, sending, deletion, attachments or production scope quality.",
            "No Eva1, Eva2 runtime, container, database, live route, external provider or real credential was read or changed.",
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
    raw = json.dumps(
        receipt, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
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
    print(raw.decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
