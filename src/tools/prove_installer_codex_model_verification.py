#!/usr/bin/env python3
"""Prove the installer Codex verifier against local HTTP contract servers."""

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

from eva.adapters.connectors.installer_codex_model_probe import (
    CodexInstallerModelProbe,
)
from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from tools.installer_provider_verification_cli import (
    run_installer_provider_verification,
)
from eva.operations.installer_secret_store import InstallerSecretStore


ROOT = Path(__file__).resolve().parents[2]
ACCESS_TOKEN = "synthetic-proof-access-token-123456789"
ACCOUNT_ID = "synthetic-proof-account"
LEASE = json.dumps(
    {
        "access_token": ACCESS_TOKEN,
        "account_id": ACCOUNT_ID,
        "contract": "EvaModelAccessLease/v1",
        "source_sha256": "b" * 64,
    },
    separators=(",", ":"),
    sort_keys=True,
)
SOURCE_PATHS = (
    "src/eva/adapters/connectors/installer_codex_model_probe.py",
    "src/eva/operations/installer_provider_verification.py",
    "src/tools/installer_provider_verification_cli.py",
    "development/tests/connectors/test_installer_codex_model_probe.py",
    "development/tests/operations/test_installer_provider_verification.py",
    "development/tests/operations/test_installer_provider_verification_cli.py",
    "src/tools/prove_installer_codex_model_verification.py",
)


def _stream() -> bytes:
    events = (
        {"type": "response.output_text.delta", "delta": "EVA2_MODEL_READY"},
        {
            "type": "response.completed",
            "response": {
                "error": None,
                "status": "completed",
                "usage": {
                    "input_tokens": 19,
                    "output_tokens": 5,
                    "total_tokens": 24,
                },
            },
        },
    )
    return b"".join(
        b"data: " + json.dumps(event, separators=(",", ":")).encode() + b"\n\n"
        for event in events
    ) + b"data: [DONE]\n\n"


class _CodexContractHandler(BaseHTTPRequestHandler):
    response_status = 200
    request_count = 0
    matching_requests = 0

    def do_POST(self) -> None:  # noqa: N802
        type(self).request_count += 1
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        if (
            self.path == "/backend-api/codex/responses"
            and self.headers.get("Authorization") == f"Bearer {ACCESS_TOKEN}"
            and self.headers.get("OpenAI-Account-ID") == ACCOUNT_ID
            and body.get("tools") == []
            and body.get("tool_choice") == "none"
            and body.get("store") is False
        ):
            type(self).matching_requests += 1
        raw = _stream() if self.response_status == 200 else b"denied-private-body"
        self.send_response(self.response_status)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _contract_server(
    *, status: int
) -> Iterator[tuple[str, type[_CodexContractHandler]]]:
    handler = type("BoundCodexContractHandler", (_CodexContractHandler,), {})
    handler.response_status = status
    handler.request_count = 0
    handler.matching_requests = 0
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
            raise RuntimeError("Codex contract server did not stop")


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700)
    path.chmod(0o700)


def _prepare(root: Path) -> tuple[Path, Path, Path]:
    capability = root / "capabilities"
    secrets = root / "secrets"
    evidence = root / "development/evidence"
    for path in (capability, secrets, evidence):
        _private_directory(path)
    workflow = InstallerCapabilityWorkflow(workspace=capability)
    workflow.select(step_id="model_provider", wanted=True)
    InstallerSecretStore(workspace=secrets).store(
        slot_id="model_provider", secret=LEASE
    )
    return capability, secrets, evidence


def _run(
    *, capability: Path, secrets: Path, evidence: Path, probe: CodexInstallerModelProbe
) -> tuple[int, dict[str, object]]:
    output = StringIO()
    with redirect_stdout(output):
        code = run_installer_provider_verification(
            [
                "--slot",
                "model_provider",
                "--capability-workspace",
                str(capability),
                "--secret-workspace",
                str(secrets),
                "--evidence-workspace",
                str(evidence),
                "--timeout-seconds",
                "2",
            ],
            probes={"model_provider": probe},
        )
    lines = output.getvalue().splitlines()
    if len(lines) != 1:
        raise RuntimeError("model verifier output was not singular")
    payload = json.loads(lines[0])
    if not isinstance(payload, dict):
        raise RuntimeError("model verifier output was not an object")
    return code, payload


def _receipt(directory: Path) -> tuple[str, bytes, dict[str, object]]:
    files = tuple(directory.iterdir())
    if len(files) != 1 or not files[0].is_file():
        raise RuntimeError("model verifier receipt set differs")
    raw = files[0].read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if files[0].name != f"{digest}.json":
        raise RuntimeError("model verifier receipt is not content-addressed")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError("model verifier receipt is not an object")
    return digest, raw, payload


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
    with tempfile.TemporaryDirectory(prefix="eva2-installer-model-proof-", dir="/tmp") as temp:
        root = Path(temp)
        root.chmod(0o700)
        success_root = root / "success"
        _private_directory(success_root)
        capability, secrets, evidence = _prepare(success_root)
        with _contract_server(status=200) as (endpoint, handler):
            probe = CodexInstallerModelProbe(
                endpoint=endpoint, allow_insecure_loopback_test=True
            )
            success_code, success = _run(
                capability=capability, secrets=secrets, evidence=evidence, probe=probe
            )
            restart_code, restart = _run(
                capability=capability, secrets=secrets, evidence=evidence, probe=probe
            )
        success_digest, success_raw, inner = _receipt(evidence)
        if (
            success_code != 0
            or success.get("evidence_sha256") != success_digest
            or restart_code != 2
            or restart.get("status") != "failed"
            or handler.request_count != 1
            or handler.matching_requests != 1
            or inner.get("provider_operation") != "bounded_generation"
            or inner.get("usage_measurement") != "measured"
            or inner.get("input_tokens") != 19
            or inner.get("output_tokens") != 5
            or inner.get("cost_measurement") != "not_reported_by_chatgpt_access"
            or inner.get("provider_scopes")
            != ["model.response.generate:installer_probe"]
            or success.get("provider_scope_manifest_sha256")
            != inner.get("provider_scope_manifest_sha256")
        ):
            raise RuntimeError("successful model installer proof differs")

        blocked_root = root / "blocked"
        _private_directory(blocked_root)
        blocked_capability, blocked_secrets, blocked_evidence = _prepare(blocked_root)
        with _contract_server(status=401) as (endpoint, blocked_handler):
            blocked_code, blocked = _run(
                capability=blocked_capability,
                secrets=blocked_secrets,
                evidence=blocked_evidence,
                probe=CodexInstallerModelProbe(
                    endpoint=endpoint, allow_insecure_loopback_test=True
                ),
            )
        blocked_digest, blocked_raw, _blocked_inner = _receipt(blocked_evidence)
        if (
            blocked_code != 2
            or blocked.get("reason_code") != "unauthorized"
            or blocked.get("evidence_sha256") != blocked_digest
            or blocked_handler.request_count != 1
            or blocked_handler.matching_requests != 1
        ):
            raise RuntimeError("blocked model installer proof differs")

        serialized = json.dumps(
            [success, restart, blocked], sort_keys=True, separators=(",", ":")
        ).encode()
        forbidden = (
            ACCESS_TOKEN.encode(),
            ACCOUNT_ID.encode(),
            b"EVA2_MODEL_READY",
            b"denied-private-body",
        )
        if any(
            value in serialized or value in success_raw or value in blocked_raw
            for value in forbidden
        ):
            raise RuntimeError("model verification retained private provider data")

    return {
        "boundaries": {
            "completeLoginAccepted": False,
            "providerPayloadRetained": False,
            "providerScopeBound": True,
            "refreshTokenAccepted": False,
            "secretReturned": False,
            "toolAuthorityAvailable": False,
        },
        "contract": "EvaInstallerCodexModelVerificationReceipt/v1",
        "issuedAt": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "loopbackRun": {
            "boundedModelGenerations": 1,
            "measuredInputTokens": 19,
            "measuredOutputTokens": 5,
            "operatorCliInvocations": 3,
            "providerRequests": 2,
            "scopeManifests": 1,
            "restartAttemptProviderRequests": 0,
            "temporaryWorkspaceRemovedAfterRun": True,
            "transport": "real_http_sse_on_127.0.0.1",
        },
        "networkBoundary": {
            "method": "POST",
            "officialOrigin": "https://chatgpt.com",
            "path": "/backend-api/codex/responses",
            "redirectFollowed": False,
        },
        "nonclaims": [
            "The proof used a synthetic access-only lease and loopback contract server; it did not contact OpenAI or consume live model access.",
            "The verified ChatGPT access route reports token usage but no monetary charge, so the receipt does not invent a currency cost.",
            "This proves the installer verification boundary, not browser login, token refresh, a real provider call or complete consumer installation.",
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
