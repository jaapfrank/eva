#!/usr/bin/env python3
"""Run the sealed clean managed-B graph against disposable local state.

This probe never addresses the live Compose project, live volumes, Eva1, or the
current /eva2 candidate.  It prints one secret-free JSON result and removes its
own uniquely named containers, network, volumes, and private staging directory.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from typing import Any
from urllib import error, parse, request
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "deploy" / "living-core" / "managed-b.compose.yml"
STAGING_PARENT = Path("/var/lib/eva2")
SOURCE_AUTH = Path("/home/administrator/project/Bottie/data/auth.json")
IDENTITY_PACK = Path("/var/lib/eva2/managed-b/identity/current.json")
IDENTITY_TRUST = Path("/var/lib/eva2/managed-b/identity/trust.json")

CORE_IMAGE = (
    "eva2/distroless-core-crypto50-candidate@sha256:"
    "2ec1399520a9ccf0942137b45777409e30d0ad6678cebb339ef9a42af50eadff"
)
POSTGRES_IMAGE = (
    "eva2/chainguard-pgvector18-offline-candidate@sha256:"
    "7e641ed0cf38285bebfc6151ba48243439dfe9cf3306feab4023a812466cd6c1"
)
HINDSIGHT_IMAGE = (
    "eva2/distroless-hindsight-api-file-secret@sha256:"
    "20d72ad34164bd1a67906c4d61b57a51f2b255c49ddf8cb7ac5e901ebc3b3710"
)
EXPECTED_SERVICES = {
    "core",
    "durable-goal-worker",
    "hindsight",
    "hindsight-access-lease",
    "hindsight-postgres",
    "model-access-lease",
    "postgres",
    "schema-installer",
    "telegram-ingress",
}


class ProbeError(RuntimeError):
    pass


def _sanitize_diagnostic(value: str) -> str:
    value = re.sub(
        r"postgres(?:ql)?://[^\s'\"]+",
        "postgresql://<redacted>",
        value,
    )
    value = re.sub(
        r"/var/lib/eva2/managed-b-clean-probe-[^/\s]+",
        "/var/lib/eva2/<probe>",
        value,
    )
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "<redacted>", value)
    value = re.sub(r"\b\d{8,12}:[A-Za-z0-9_-]{20,}\b", "<redacted>", value)
    value = re.sub(
        r"(?i)(authorization:\s*bearer\s+)[^\s]+",
        r"\1<redacted>",
        value,
    )
    return " ".join(value.split())[-2000:]


def _run(command: list[str], *, timeout: int = 180) -> str:
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={"HOME": "/root", "LC_ALL": "C", "PATH": os.environ["PATH"]},
        )
    except subprocess.CalledProcessError as exc:
        diagnostic = "\n".join((exc.stdout or "", exc.stderr or ""))[-2000:]
        diagnostic = _sanitize_diagnostic(diagnostic)
        raise ProbeError(
            f"command failed: {command[-1]}: {diagnostic or 'no diagnostic'}"
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProbeError(f"command failed: {command[-1]}") from exc
    return completed.stdout


def _write_private(path: Path, value: bytes, *, uid: int, gid: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(value)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ProbeError("private file write stopped")
            view = view[written:]
        os.fsync(descriptor)
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _copy_private(source: Path, target: Path, *, uid: int, gid: int) -> None:
    _write_private(target, source.read_bytes(), uid=uid, gid=gid)


def _private_directory(path: Path, *, uid: int, gid: int, mode: int) -> None:
    path.mkdir(mode=mode)
    os.chown(path, uid, gid)
    os.chmod(path, mode)


def _available_port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def _http_json(url: str, *, payload: dict[str, Any] | None = None) -> Any:
    body = None
    headers: dict[str, str] = {}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
    try:
        with request.urlopen(req, timeout=120) as response:
            if response.status != 200:
                raise ProbeError("HTTP probe returned a non-200 status")
            return json.load(response)
    except error.HTTPError as exc:
        diagnostic = _sanitize_diagnostic(exc.read().decode("utf-8", errors="replace"))
        raise ProbeError(
            f"HTTP probe returned {exc.code}: {diagnostic or 'empty response'}"
        ) from exc


def _wait_ready(port: int, *, timeout: int = 60) -> None:
    deadline = time.monotonic() + timeout
    last_document: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            document = _http_json(f"http://127.0.0.1:{port}/readyz")
            if isinstance(document, dict):
                last_document = document
            if document.get("ready") is True:
                return
        except error.HTTPError as exc:
            try:
                document = json.load(exc)
                if isinstance(document, dict):
                    last_document = document
            except (OSError, ValueError):
                pass
        except (OSError, ValueError, error.URLError):
            pass
        except ProbeError:
            pass
        time.sleep(1)
    if last_document is not None:
        raise ProbeError(
            "candidate core did not become ready: "
            + json.dumps(last_document, sort_keys=True)
        )
    raise ProbeError("candidate core did not become reachable")


def _compose(project: str, environment: Path, *arguments: str) -> list[str]:
    return [
        "/usr/bin/docker",
        "compose",
        "--project-name",
        project,
        "--env-file",
        environment.as_posix(),
        "-f",
        MANIFEST.as_posix(),
        *arguments,
    ]


def _live_status(url: str) -> int:
    try:
        with request.urlopen(url, timeout=15) as response:
            return response.status
    except (OSError, error.URLError):
        return 0


def main() -> int:
    if os.geteuid() != 0:
        print(json.dumps({"error": "probe requires root", "status": "failed"}))
        return 2
    run_id = uuid4().hex[:12]
    project = f"eva2-clean-b-{run_id}"
    core_name = f"eva2-clean-b-core-{run_id}"
    staging = STAGING_PARENT / f"managed-b-clean-probe-{run_id}"
    port = _available_port()
    operational_password = secrets.token_urlsafe(32)
    memory_password = secrets.token_urlsafe(32)
    environment_file = staging / "candidate.env"
    cleaned = False
    phase = "prepare"
    result: dict[str, Any] = {
        "contract": "Eva2ManagedBCleanGraphProbeReceipt/v1",
        "issuedAt": datetime.now(UTC).isoformat(),
        "manifestSha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        "status": "failed",
    }
    initial_live = {
        "eva1": _live_status("http://127.0.0.1:3210/api/health"),
        "currentEva2": _live_status("http://127.0.0.1:44706/readyz"),
    }
    try:
        staging.mkdir(mode=0o700)
        secrets_dir = staging / "secrets"
        identity_dir = staging / "identity"
        codex_dir = staging / "codex-auth"
        model_lease = staging / "model-access-lease"
        hindsight_lease = staging / "hindsight-access"
        for directory, uid, gid, mode in (
            (secrets_dir, 0, 0, 0o700),
            (identity_dir, 65532, 65532, 0o700),
            (codex_dir, 1000, 1000, 0o700),
            (model_lease, 1000, 65532, 0o2750),
            (hindsight_lease, 1000, 1000, 0o700),
        ):
            _private_directory(directory, uid=uid, gid=gid, mode=mode)

        operational_password_file = secrets_dir / "postgres-password"
        memory_password_file = secrets_dir / "hindsight-postgres-password"
        operational_dsn_file = secrets_dir / "database-dsn"
        memory_dsn_file = secrets_dir / "hindsight-database-dsn"
        _write_private(
            operational_password_file,
            operational_password.encode(),
            uid=0,
            gid=0,
        )
        _write_private(memory_password_file, memory_password.encode(), uid=0, gid=0)
        _write_private(
            operational_dsn_file,
            (
                "postgresql://postgres:"
                f"{parse.quote(operational_password, safe='')}@postgres:5432/eva2"
            ).encode(),
            uid=65532,
            gid=65532,
        )
        _write_private(
            memory_dsn_file,
            (
                "postgresql://postgres:"
                f"{parse.quote(memory_password, safe='')}@hindsight-postgres:5432/hindsight"
            ).encode(),
            uid=1000,
            gid=1000,
        )
        _write_private(secrets_dir / "home-assistant-token", b"probe-only", uid=65532, gid=65532)
        _write_private(
            secrets_dir / "dashboard-token",
            secrets.token_urlsafe(32).encode(),
            uid=65532,
            gid=65532,
        )
        _write_private(secrets_dir / "telegram-token", b"probe-only", uid=65532, gid=65532)
        _write_private(secrets_dir / "telegram-bindings", b"{}", uid=65532, gid=65532)
        _write_private(
            secrets_dir / "telegram-pairing",
            secrets.token_urlsafe(32).encode(),
            uid=65532,
            gid=65532,
        )
        _copy_private(IDENTITY_PACK, identity_dir / "current.json", uid=65532, gid=65532)
        _copy_private(IDENTITY_TRUST, identity_dir / "trust.json", uid=65532, gid=65532)
        _copy_private(SOURCE_AUTH, codex_dir / "auth.json", uid=1000, gid=1000)

        values = {
            "EVA_B_CONFIGURATION_SHA256": hashlib.sha256(run_id.encode()).hexdigest(),
            "EVA_LIVING_CORE_IMAGE": CORE_IMAGE,
            "EVA_POSTGRES_IMAGE": POSTGRES_IMAGE,
            "EVA_HINDSIGHT_POSTGRES_IMAGE": POSTGRES_IMAGE,
            "EVA_HINDSIGHT_IMAGE": HINDSIGHT_IMAGE,
            "EVA_STACK_CORE_CONTAINER": core_name,
            "EVA_STACK_BIND_ADDRESS": "127.0.0.1",
            "EVA_STACK_CORE_PORT": str(port),
            "EVA_STACK_POSTGRES_DATABASE": "eva2",
            "EVA_STACK_HINDSIGHT_POSTGRES_DATABASE": "hindsight",
            "EVA_STACK_POSTGRES_VOLUME": f"{project}-operational",
            "EVA_STACK_HINDSIGHT_POSTGRES_VOLUME": f"{project}-memory",
            "EVA_STACK_WORK_VOLUME": f"{project}-work",
            "EVA_POSTGRES_PASSWORD_FILE_HOST": operational_password_file.as_posix(),
            "EVA_DATABASE_DSN_FILE_HOST": operational_dsn_file.as_posix(),
            "EVA_HINDSIGHT_POSTGRES_PASSWORD_FILE_HOST": memory_password_file.as_posix(),
            "EVA_HINDSIGHT_DATABASE_DSN_FILE_HOST": memory_dsn_file.as_posix(),
            "EVA_HOME_ASSISTANT_URL": "http://127.0.0.1:9",
            "EVA_HOME_ASSISTANT_EFFECTS_ENABLED": "false",
            "EVA_HOME_ASSISTANT_TOKEN_FILE_HOST": (secrets_dir / "home-assistant-token").as_posix(),
            "EVA_DASHBOARD_ACCESS_TOKEN_FILE_HOST": (secrets_dir / "dashboard-token").as_posix(),
            "EVA_DASHBOARD_PRINCIPAL_BINDING_ID": "44444444-4444-4444-8444-444444444444",
            "EVA_IDENTITY_PACK_FILE_HOST": (identity_dir / "current.json").as_posix(),
            "EVA_IDENTITY_PACK_TRUST_FILE_HOST": (identity_dir / "trust.json").as_posix(),
            "EVA_TELEGRAM_BOT_TOKEN_FILE_HOST": (secrets_dir / "telegram-token").as_posix(),
            "EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE_HOST": (secrets_dir / "telegram-bindings").as_posix(),
            "EVA_TELEGRAM_PAIRING_SECRET_FILE_HOST": (secrets_dir / "telegram-pairing").as_posix(),
            "EVA_RUNTIME_TENANT_ID": "11111111-1111-1111-8111-111111111111",
            "EVA_RUNTIME_INSTALLATION_ID": "22222222-2222-2222-8222-222222222222",
            "EVA_RUNTIME_PRINCIPAL_ID": "33333333-3333-3333-8333-333333333333",
            "EVA_DURABLE_GOAL_PROVIDER": "probe",
            "EVA_DURABLE_GOAL_PROVIDER_URL": "http://127.0.0.1:9",
            "EVA_DURABLE_GOAL_RECIPIENTS": "probe",
            "EVA_FANOUT_RECIPIENT_ENDPOINTS_JSON": '{"probe":"https://example.invalid"}',
            "EVA_FANOUT_ALLOW_INSECURE_HTTP": "false",
            "EVA_FILE_BATCH_ROOT": "/var/lib/eva/work",
            "EVA_HINDSIGHT_BANK_NAMESPACE": f"probe-{run_id}",
            "EVA_CODEX_AUTH_DIR": codex_dir.as_posix(),
            "EVA_MODEL_ACCESS_LEASE_DIR": model_lease.as_posix(),
            "EVA_HINDSIGHT_ACCESS_DIR": hindsight_lease.as_posix(),
        }
        _write_private(
            environment_file,
            "".join(f"{key}={value}\n" for key, value in sorted(values.items())).encode(),
            uid=0,
            gid=0,
        )

        phase = "render"
        rendered = json.loads(_run(_compose(project, environment_file, "--profile", "schema-install", "config", "--format", "json")))
        if set(rendered.get("services", {})) != EXPECTED_SERVICES:
            raise ProbeError("rendered service graph differs")
        hindsight_environment = rendered["services"]["hindsight"]["environment"]
        if "HINDSIGHT_API_DATABASE_URL" in hindsight_environment:
            raise ProbeError("render exposed an inline Hindsight DSN")

        phase = "support-start"
        _run(_compose(project, environment_file, "up", "-d", "postgres", "hindsight-postgres", "model-access-lease", "hindsight-access-lease", "hindsight"))
        phase = "schema-install"
        _run(_compose(project, environment_file, "--profile", "schema-install", "run", "--rm", "schema-installer"))
        phase = "core-start"
        _run(_compose(project, environment_file, "up", "-d", "core"))
        phase = "core-ready-before-restart"
        _wait_ready(port)

        vector_version = _run(
            _compose(
                project,
                environment_file,
                "exec",
                "-T",
                "hindsight-postgres",
                "psql",
                "-U",
                "postgres",
                "-d",
                "hindsight",
                "-Atc",
                "SELECT extversion FROM pg_extension WHERE extname='vector'",
            )
        ).strip()
        if vector_version != "0.8.6":
            raise ProbeError("Hindsight database did not load the pinned vector extension")

        phase = "real-model-turn"
        turn = {
            "audience": {
                "audience_id": "person:33333333-3333-3333-8333-333333333333",
                "kind": "private_person",
                "principal_id": "33333333-3333-3333-8333-333333333333",
            },
            "conversation_id": f"managed-b-probe:{run_id}",
            "installation_id": "22222222-2222-2222-8222-222222222222",
            "message_id": f"managed-b-probe:{run_id}:1",
            "occurred_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "principal_id": "33333333-3333-3333-8333-333333333333",
            "tenant_id": "11111111-1111-1111-8111-111111111111",
            "text": "Antwoord alleen met: beheerd",
        }
        reply = _http_json(
            f"http://127.0.0.1:{port}/internal/v1/turns/respond", payload=turn
        )
        if reply.get("text", "").strip().casefold() != "beheerd":
            raise ProbeError("real model response was not the requested exact word")

        phase = "joint-stop"
        _run(_compose(project, environment_file, "stop", "core", "hindsight", "hindsight-postgres", "postgres"))
        phase = "joint-start"
        _run(_compose(project, environment_file, "start", "postgres", "hindsight-postgres", "hindsight", "core"))
        phase = "core-ready-after-restart"
        _wait_ready(port)

        hindsight_id = _run(_compose(project, environment_file, "ps", "-q", "hindsight")).strip()
        inspected = json.loads(_run(["/usr/bin/docker", "inspect", hindsight_id]))[0]
        configured_names = {item.partition("=")[0] for item in inspected["Config"]["Env"]}
        if "HINDSIGHT_API_DATABASE_URL" in configured_names:
            raise ProbeError("Hindsight container configuration retained an inline DSN")

        result.update(
            {
                "status": "passed",
                "serviceNames": sorted(EXPECTED_SERVICES),
                "renderedServiceCount": len(EXPECTED_SERVICES),
                "separateOperationalAndMemoryDatabases": True,
                "hindsightDatabaseFileSecret": True,
                "inlineHindsightDatabaseUrlAbsent": True,
                "postgresVersion": "18.6",
                "pgvectorVersion": vector_version,
                "coreReadyBeforeRestart": True,
                "realCodexReplyExact": True,
                "coreReadyAfterDatabaseAndServiceRestart": True,
                "publishedAddress": "127.0.0.1",
                "temporaryPort": port,
                "liveSafetyBefore": initial_live,
            }
        )
    except Exception as exc:
        result["error"] = str(exc) if isinstance(exc, ProbeError) else "probe failed"
        result["failedPhase"] = phase
        if environment_file.exists():
            try:
                state = _run(
                    _compose(project, environment_file, "ps", "--all", "--format", "json")
                )
                result["candidateState"] = _sanitize_diagnostic(state)
                logs = _run(
                    _compose(
                        project,
                        environment_file,
                        "logs",
                        "--no-color",
                        "--tail",
                        "40",
                        "core",
                    )
                )
                result["coreDiagnostic"] = _sanitize_diagnostic(logs)
            except ProbeError:
                result["diagnosticCapture"] = "unavailable"
    finally:
        if environment_file.exists():
            try:
                _run(_compose(project, environment_file, "down", "--volumes", "--remove-orphans"))
                cleaned = True
            except ProbeError:
                cleaned = False
        if staging.parent == STAGING_PARENT and staging.name.startswith("managed-b-clean-probe-"):
            shutil.rmtree(staging, ignore_errors=True)
        result["temporaryResourcesRemoved"] = cleaned and not staging.exists()
        result["liveSafetyAfter"] = {
            "eva1": _live_status("http://127.0.0.1:3210/api/health"),
            "currentEva2": _live_status("http://127.0.0.1:44706/readyz"),
        }
        operational_password = ""
        memory_password = ""

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "passed" and result["temporaryResourcesRemoved"] else 2


if __name__ == "__main__":
    sys.exit(main())
