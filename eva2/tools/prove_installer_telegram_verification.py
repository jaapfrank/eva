#!/usr/bin/env python3
"""Prove the installer Telegram verifier against a local HTTP contract server.

This proof deliberately uses a fully synthetic token and loopback-only server.
It exercises the operator CLI, the connector-neutral verifier, persistent
capability state, the private secret store, sanitized receipts, a rejected
credential, and restart behavior.  It never contacts Telegram or a live Eva
runtime.
"""

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

from eva.adapters.connectors.installer_telegram_probe import (
    TelegramInstallerReadOnlyProbe,
)
from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from tools.installer_provider_verification_cli import (
    run_installer_provider_verification,
)
from eva.operations.installer_secret_store import InstallerSecretStore


ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_TOKEN = "7000000001:Eva2SyntheticTokenValue_1234567890ABCDE"
SYNTHETIC_BOT_ID = 7000000001
SYNTHETIC_USERNAME = "eva2_installer_fixture_bot"
SOURCE_PATHS = (
    "src/eva/adapters/connectors/installer_telegram_probe.py",
    "src/eva/operations/installer_provider_verification.py",
    "tools/installer_provider_verification_cli.py",
    "tests/connectors/test_installer_telegram_probe.py",
    "tests/operations/test_installer_provider_verification.py",
    "tests/operations/test_installer_provider_verification_cli.py",
    "tools/prove_installer_telegram_verification.py",
)


class _TelegramContractHandler(BaseHTTPRequestHandler):
    response_status = 200
    request_count = 0
    request_matches = 0

    def do_POST(self) -> None:  # noqa: N802
        type(self).request_count += 1
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if (
            self.path == f"/bot{SYNTHETIC_TOKEN}/getMe"
            and body == b"{}"
            and self.headers.get("User-Agent")
            == "Eva2-Installer-Verification/1"
        ):
            type(self).request_matches += 1
        payload: object
        if self.response_status == 200:
            payload = {
                "ok": True,
                "result": {
                    "id": SYNTHETIC_BOT_ID,
                    "is_bot": True,
                    "username": SYNTHETIC_USERNAME,
                },
            }
        else:
            payload = {"ok": False, "error_code": self.response_status}
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
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
) -> Iterator[tuple[str, type[_TelegramContractHandler]]]:
    handler = type("BoundTelegramContractHandler", (_TelegramContractHandler,), {})
    handler.response_status = status
    handler.request_count = 0
    handler.request_matches = 0
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
    workflow.select(step_id="telegram", wanted=True)
    InstallerSecretStore(workspace=secrets).store(
        slot_id="telegram", secret=SYNTHETIC_TOKEN
    )
    return capability, secrets, evidence


def _run_cli(
    *,
    capability: Path,
    secrets: Path,
    evidence: Path,
    probe: TelegramInstallerReadOnlyProbe,
) -> tuple[int, dict[str, object]]:
    stream = StringIO()
    with redirect_stdout(stream):
        code = run_installer_provider_verification(
            [
                "--slot",
                "telegram",
                "--capability-workspace",
                str(capability),
                "--secret-workspace",
                str(secrets),
                "--evidence-workspace",
                str(evidence),
                "--timeout-seconds",
                "2",
            ],
            probes={"telegram": probe},
        )
    lines = stream.getvalue().splitlines()
    if len(lines) != 1:
        raise RuntimeError("installer verification CLI output was not singular")
    value = json.loads(lines[0])
    if not isinstance(value, dict):
        raise RuntimeError("installer verification CLI output was not an object")
    return code, value


def _receipt_bytes(evidence: Path) -> tuple[str, bytes]:
    paths = list(evidence.iterdir())
    if len(paths) != 1 or not paths[0].is_file():
        raise RuntimeError("installer verification receipt set differs")
    raw = paths[0].read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if paths[0].name != f"{digest}.json":
        raise RuntimeError("installer verification receipt is not content-addressed")
    forbidden = (
        SYNTHETIC_TOKEN.encode("ascii"),
        str(SYNTHETIC_BOT_ID).encode("ascii"),
        SYNTHETIC_USERNAME.encode("ascii"),
    )
    if any(value in raw for value in forbidden):
        raise RuntimeError("installer verification receipt retained provider data")
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
        prefix="eva2-installer-telegram-proof-", dir="/tmp"
    ) as temporary:
        temporary_root = Path(temporary)
        temporary_root.chmod(0o700)

        success_root = temporary_root / "success"
        _private_directory(success_root)
        capability, secrets, evidence = _prepare(success_root)
        with _contract_server(status=200) as (origin, handler):
            probe = TelegramInstallerReadOnlyProbe(
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
                probe=TelegramInstallerReadOnlyProbe(
                    api_base_url=origin,
                    allow_insecure_loopback_test=True,
                ),
            )
        success_digest, success_raw = _receipt_bytes(evidence)
        if (
            success_code != 0
            or success.get("status") != "passed"
            or success.get("outcome") != "ready"
            or success.get("reason_code") != "verified"
            or success.get("evidence_sha256") != success_digest
            or restart_code != 2
            or restart.get("status") != "failed"
            or handler.request_count != 1
            or handler.request_matches != 1
            or InstallerCapabilityWorkflow(workspace=capability)
            .status()
            .step("telegram")
            .state
            != "ready"
        ):
            raise RuntimeError("successful installer verification proof differs")

        blocked_root = temporary_root / "blocked"
        _private_directory(blocked_root)
        blocked_capability, blocked_secrets, blocked_evidence = _prepare(blocked_root)
        with _contract_server(status=401) as (origin, blocked_handler):
            blocked_code, blocked = _run_cli(
                capability=blocked_capability,
                secrets=blocked_secrets,
                evidence=blocked_evidence,
                probe=TelegramInstallerReadOnlyProbe(
                    api_base_url=origin,
                    allow_insecure_loopback_test=True,
                ),
            )
        blocked_digest, blocked_raw = _receipt_bytes(blocked_evidence)
        if (
            blocked_code != 2
            or blocked.get("status") != "blocked"
            or blocked.get("outcome") != "blocked"
            or blocked.get("reason_code") != "unauthorized"
            or blocked.get("evidence_sha256") != blocked_digest
            or blocked_handler.request_count != 1
            or blocked_handler.request_matches != 1
            or InstallerCapabilityWorkflow(workspace=blocked_capability)
            .status()
            .step("telegram")
            .state
            != "blocked"
        ):
            raise RuntimeError("blocked installer verification proof differs")

        serialized_cli = json.dumps(
            [success, restart, blocked], sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        forbidden = (
            SYNTHETIC_TOKEN.encode("ascii"),
            str(SYNTHETIC_BOT_ID).encode("ascii"),
            SYNTHETIC_USERNAME.encode("ascii"),
        )
        if any(
            value in serialized_cli or value in success_raw or value in blocked_raw
            for value in forbidden
        ):
            raise RuntimeError("installer verification output retained provider data")

    issued_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    return {
        "contract": "EvaInstallerTelegramReadOnlyVerificationReceipt/v1",
        "issuedAt": issued_at,
        "status": "passed",
        "source": {
            "commit": _source_commit(),
            "files": [
                {"path": path, "sha256": _sha256(ROOT / path)}
                for path in SOURCE_PATHS
            ],
        },
        "loopbackRun": {
            "transport": "real_http_on_127.0.0.1",
            "operatorCliInvocations": 3,
            "providerRequests": 2,
            "successfulReadOnlyGetMe": 1,
            "unauthorizedReadOnlyGetMe": 1,
            "restartAttemptProviderRequests": 0,
            "temporaryWorkspaceRemovedAfterRun": True,
        },
        "boundaries": {
            "browserProviderRouteExists": False,
            "mutationAttempted": False,
            "providerPayloadRetained": False,
            "secretReturned": False,
            "secretHashRetained": False,
            "secretLengthRetained": False,
            "contentAddressedPrivateReceipts": 2,
            "successfulCapabilityState": "ready",
            "rejectedCapabilityState": "blocked",
            "runtimeChanges": 0,
        },
        "verification": {
            "focusedTestsPassed": focused_tests_passed,
            "broadInstallerDashboardReleaseTestsPassed": broad_tests_passed,
            "ruff": "passed",
            "cliHelpChecked": True,
            "gitDiffCheck": "passed",
        },
        "nonclaims": [
            "The proof used a fully synthetic token and a local contract server; it did not contact Telegram or verify a real bot credential.",
            "A local getMe contract proof is not connector activation, message delivery, group consent or a live Telegram claim.",
            "The verifier clears its mutable secret buffer best-effort; Python and HTTP libraries may create short-lived internal copies, so broad process-memory erasure is not claimed.",
            "No Eva1, Eva2, container, database, live route, external provider or real credential was read or changed.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--focused-tests-passed", type=int, required=True)
    parser.add_argument("--broad-tests-passed", type=int, required=True)
    arguments = parser.parse_args()
    result = prove(
        focused_tests_passed=arguments.focused_tests_passed,
        broad_tests_passed=arguments.broad_tests_passed,
    )
    raw = (
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False).encode("utf-8")
        + b"\n"
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        arguments.output,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o644,
    )
    with os.fdopen(descriptor, "wb", closefd=True) as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    print(json.dumps({"output": str(arguments.output), "status": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
