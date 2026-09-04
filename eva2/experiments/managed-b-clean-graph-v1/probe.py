#!/usr/bin/env python3
"""Run the sealed managed-B graph against disposable local state.

The default probe uses fresh state.  ``--clone-current-route`` reads consistent
logical snapshots from the exact current /eva2 PostgreSQL and embedded
Hindsight databases, restores them only into unique disposable volumes, and
never stops or writes either source.  Both modes print one secret-free JSON
result and remove their private temporary state.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import time
from typing import Any

from tools.managed_b_candidate.private_receipt import (
    PrivateReceiptError,
    write_new_private_json,
)
from urllib import error, parse, request
from uuid import uuid4

from eva.operations.logical_restore_authority import logical_restore_preparation_sql

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
CORE_IMAGE_SERVICES = {
    "core",
    "durable-goal-worker",
    "hindsight-access-lease",
    "model-access-lease",
    "schema-installer",
    "telegram-ingress",
}
STARTED_CORE_IMAGE_SERVICES = {
    "core",
    "hindsight-access-lease",
    "model-access-lease",
}
EXPECTED_RUNTIME_LIBRARY_PATH = "LD_LIBRARY_PATH=/usr/local/lib"
CURRENT_ROUTE_CORE = "eva2-managed-b-slot4-core"
CURRENT_ROUTE_POSTGRES = "eva2-managed-b-slot4-postgres-1"
CURRENT_ROUTE_POSTGRES_VOLUME = "eva2-managed-b-slot4-operational"
CURRENT_ROUTE_HINDSIGHT = "eva2-managed-b-slot4-hindsight-postgres-1"
CURRENT_ROUTE_HINDSIGHT_VOLUME = "eva2-managed-b-slot4-memory"
CURRENT_ROUTE_PORT = "44711"
CURRENT_ROUTE_HA_TOKEN = Path(
    "/var/lib/eva2/managed-b-slot4/secrets/home-assistant-token"
)
CURRENT_ROUTE_DASHBOARD_TOKEN = Path(
    "/var/lib/eva2/managed-b-slot4/secrets/dashboard-token"
)
CURRENT_ROUTE_ENVIRONMENT = Path("/var/lib/eva2/managed-b-slot4/candidate.env")
CURRENT_ROUTE_HINDSIGHT_NAMESPACE = "eva2_candidate_be70ec4"
_COUNT_SQL = """
SELECT stats.schemaname || '.' || stats.relname || '=' ||
       (xpath('/row/c/text()', query_to_xml(
           format(
               'SELECT count(*) AS c FROM %I.%I',
               stats.schemaname,
               stats.relname
           ),
           false, true, ''
       )))[1]::text
FROM pg_stat_user_tables AS stats
JOIN pg_class AS relation ON relation.oid = stats.relid
WHERE relation.relkind IN ('r', 'p')
ORDER BY stats.schemaname, stats.relname
"""
_PRESERVED_HINDSIGHT_TABLES = {
    "public.banks",
    "public.documents",
    "public.entities",
    "public.memory_links",
    "public.memory_units",
    "public.mental_models",
    "public.observation_history",
}
DEFAULT_CONVERSATION_PROMPT = "Antwoord alleen met: beheerd"
MAX_CONVERSATION_PROMPT_BYTES = 4_096


class ProbeError(RuntimeError):
    pass


def _conversation_prompt(path: Path | None) -> str:
    """Read one bounded private prompt without accepting links or special files."""

    if path is None:
        return DEFAULT_CONVERSATION_PROMPT
    metadata = os.lstat(path)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or not 1 <= metadata.st_size <= MAX_CONVERSATION_PROMPT_BYTES
    ):
        raise ProbeError("conversation prompt file is invalid")
    try:
        prompt = path.read_bytes().decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise ProbeError("conversation prompt file is not UTF-8") from error
    if not prompt or "\x00" in prompt:
        raise ProbeError("conversation prompt is invalid")
    return prompt


def _conversation_measurement(data: object, *, expected_text: str) -> dict[str, Any]:
    """Validate the durable reply fields used by a bounded conversation probe."""

    if not isinstance(data, dict) or data.get("text") != expected_text:
        raise ProbeError("durable conversation reply differs")
    provider = data.get("provider")
    usage = data.get("model_usage")
    if provider != "chatgpt-codex:gpt-5.4-mini" or not isinstance(usage, dict):
        raise ProbeError("durable conversation provider or usage differs")
    expected_usage_fields = {
        "input_tokens",
        "measurement",
        "model_attempts",
        "output_tokens",
        "tool_calls",
    }
    if set(usage) != expected_usage_fields or usage.get("measurement") not in {
        "measured",
        "partial",
    }:
        raise ProbeError("durable conversation usage is not measured")
    numeric = {
        key: usage.get(key) for key in expected_usage_fields if key != "measurement"
    }
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in numeric.values()
    ):
        raise ProbeError("durable conversation usage is invalid")
    if not 1 <= numeric["model_attempts"] <= 2:
        raise ProbeError("conversation exceeded its model-call budget")
    if numeric["tool_calls"] != 0:
        raise ProbeError("conversation used a tool in a tool-free case")
    if numeric["input_tokens"] > 24_000 or numeric["output_tokens"] > 4_096:
        raise ProbeError("conversation exceeded its token budget")
    return {
        "inputTokens": numeric["input_tokens"],
        "measurement": usage["measurement"],
        "modelAttempts": numeric["model_attempts"],
        "outputTokens": numeric["output_tokens"],
        "provider": provider,
        "toolCalls": numeric["tool_calls"],
    }


def _require_runtime_library_path(config: object) -> str:
    if not isinstance(config, dict):
        raise ProbeError("runtime configuration is invalid")
    environment = config.get("Env")
    if not isinstance(environment, list) or not all(
        isinstance(item, str) for item in environment
    ):
        raise ProbeError("runtime environment is invalid")
    assignments = [item for item in environment if item.startswith("LD_LIBRARY_PATH=")]
    if assignments != [EXPECTED_RUNTIME_LIBRARY_PATH]:
        raise ProbeError("runtime library path differs")
    return "/usr/local/lib"


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


def _run_binary_to_private_file(
    command: list[str], path: Path, *, timeout: int = 300
) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            completed = subprocess.run(
                command,
                check=False,
                stdout=output,
                stderr=subprocess.PIPE,
                timeout=timeout,
                env={"HOME": "/root", "LC_ALL": "C", "PATH": os.environ["PATH"]},
            )
            output.flush()
            os.fsync(output.fileno())
        if completed.returncode != 0:
            diagnostic = _sanitize_diagnostic(
                completed.stderr.decode("utf-8", errors="replace")[-2000:]
            )
            raise ProbeError(
                f"database snapshot failed: {diagnostic or 'no diagnostic'}"
            )
        os.fchmod(descriptor, 0o600)
    except (OSError, subprocess.SubprocessError) as exc:
        path.unlink(missing_ok=True)
        if isinstance(exc, ProbeError):
            raise
        raise ProbeError("database snapshot failed") from exc
    finally:
        os.close(descriptor)


def _restore_private_dump(
    container: str,
    database: str,
    path: Path,
    *,
    preserve_authority: bool = False,
    timeout: int = 300,
) -> None:
    authority_arguments = (
        [] if preserve_authority else ["--no-owner", "--no-privileges"]
    )
    try:
        with path.open("rb") as source:
            completed = subprocess.run(
                [
                    "/usr/bin/docker",
                    "exec",
                    "-i",
                    "-u",
                    "postgres",
                    container,
                    "/usr/bin/pg_restore",
                    "--exit-on-error",
                    *authority_arguments,
                    "-U",
                    "postgres",
                    "-d",
                    database,
                ],
                check=False,
                stdin=source,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                env={"HOME": "/root", "LC_ALL": "C", "PATH": os.environ["PATH"]},
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProbeError("database restoration failed") from exc
    if completed.returncode != 0:
        diagnostic = _sanitize_diagnostic(
            completed.stderr.decode("utf-8", errors="replace")[-2000:]
        )
        raise ProbeError(
            f"database restoration failed: {diagnostic or 'no diagnostic'}"
        )


def _prepare_logical_restore_roles(container: str, database: str) -> None:
    _run(
        [
            "/usr/bin/docker",
            "exec",
            "-u",
            "postgres",
            container,
            "/usr/bin/psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            database,
            "-c",
            logical_restore_preparation_sql(),
        ]
    )


def _parse_counts(output: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in output.splitlines():
        name, separator, raw_count = line.partition("=")
        if not separator or not name or name in counts or not raw_count.isdigit():
            raise ProbeError("database count output was invalid")
        counts[name] = int(raw_count)
    if not counts:
        raise ProbeError("database count output was empty")
    return counts


def _operational_counts(container: str, database: str | None = None) -> dict[str, int]:
    if database is None:
        output = _run(
            [
                "/usr/bin/docker",
                "exec",
                "-u",
                "postgres",
                container,
                "sh",
                "-lc",
                'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" '
                '-d "$POSTGRES_DB" -Atc "$1"',
                "count",
                _COUNT_SQL,
            ]
        )
    else:
        output = _run(
            [
                "/usr/bin/docker",
                "exec",
                "-u",
                "postgres",
                container,
                "/usr/bin/psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "postgres",
                "-d",
                database,
                "-Atc",
                _COUNT_SQL,
            ]
        )
    return _parse_counts(output)


def _durable_conversation_reply(
    container: str, database: str, message_id: str
) -> dict[str, Any]:
    if re.fullmatch(r"managed-b-probe:[0-9a-f]{12}:1", message_id) is None:
        raise ProbeError("conversation message ID is invalid")
    thread_id = f"conversation:{message_id}"
    output = _run(
        [
            "/usr/bin/docker",
            "exec",
            "-u",
            "postgres",
            container,
            "/usr/bin/psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            database,
            "-Atc",
            (
                "SELECT payload->'data' FROM eva_core.operational_events "
                f"WHERE thread_id='{thread_id}' AND event_type='REPLY_COMMITTED' "
                "ORDER BY sequence DESC"
            ),
        ]
    ).strip()
    lines = output.splitlines()
    if len(lines) != 1:
        raise ProbeError("durable conversation reply count differs")
    try:
        data = json.loads(lines[0])
    except json.JSONDecodeError as error:
        raise ProbeError("durable conversation reply is invalid") from error
    if not isinstance(data, dict):
        raise ProbeError("durable conversation reply is invalid")
    return data


def _embedded_hindsight_counts(container: str) -> dict[str, int]:
    program = """
import json, os, subprocess, sys
from pathlib import Path
cfg = json.loads(Path('/home/hindsight/.pg0/instances/hindsight/instance.json').read_text())
env = dict(os.environ, PGPASSWORD=cfg['password'])
psql = str(Path(cfg['installation_dir']) / cfg['version'] / 'bin' / 'psql')
subprocess.run([psql, '-X', '-v', 'ON_ERROR_STOP=1', '-h', '127.0.0.1',
                '-U', cfg['username'], '-d', cfg['database'], '-Atc', sys.argv[1]],
               check=True, env=env)
"""
    return _parse_counts(
        _run(
            [
                "/usr/bin/docker",
                "exec",
                container,
                "/app/api/.venv/bin/python3",
                "-c",
                program,
                _COUNT_SQL,
            ]
        )
    )


def _count_digest(counts: dict[str, int]) -> str:
    payload = json.dumps(counts, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _validate_count_window(
    before: dict[str, int], restored: dict[str, int], after: dict[str, int]
) -> None:
    if set(before) != set(after) or set(before) != set(restored):
        raise ProbeError("restored database table set differs from its live source")
    for table, restored_count in restored.items():
        lower = min(before[table], after[table])
        upper = max(before[table], after[table])
        if not lower <= restored_count <= upper:
            raise ProbeError(
                "restored database row count differs from the dump window: "
                f"{table} source={before[table]}..{after[table]} "
                f"restored={restored_count}"
            )


def _require_current_route_sources() -> None:
    inspected = {
        name: json.loads(_run(["/usr/bin/docker", "inspect", name]))[0]
        for name in (
            CURRENT_ROUTE_CORE,
            CURRENT_ROUTE_POSTGRES,
            CURRENT_ROUTE_HINDSIGHT,
        )
    }
    if inspected[CURRENT_ROUTE_CORE]["State"].get("Running") is not True:
        raise ProbeError("current Eva2 route core is not running")
    port_bindings = (
        inspected[CURRENT_ROUTE_CORE]["NetworkSettings"]["Ports"].get("8080/tcp") or []
    )
    if not any(
        binding.get("HostIp") == "127.0.0.1"
        and binding.get("HostPort") == CURRENT_ROUTE_PORT
        for binding in port_bindings
    ):
        raise ProbeError("current Eva2 route no longer owns its sealed loopback port")
    expected_mounts = {
        CURRENT_ROUTE_POSTGRES: (
            CURRENT_ROUTE_POSTGRES_VOLUME,
            "/var/lib/postgresql/data",
        ),
        CURRENT_ROUTE_HINDSIGHT: (
            CURRENT_ROUTE_HINDSIGHT_VOLUME,
            "/var/lib/postgresql/data",
        ),
    }
    for name, expected in expected_mounts.items():
        mounts = {
            (mount.get("Name"), mount.get("Destination"))
            for mount in inspected[name].get("Mounts", [])
            if mount.get("Type") == "volume"
        }
        if (
            expected not in mounts
            or inspected[name]["State"].get("Running") is not True
        ):
            raise ProbeError(
                "current Eva2 data source no longer matches its sealed volume"
            )


def _snapshot_current_route(
    operational_dump: Path, hindsight_dump: Path
) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]]:
    _require_current_route_sources()
    operational_before = _operational_counts(CURRENT_ROUTE_POSTGRES)
    hindsight_before = _operational_counts(CURRENT_ROUTE_HINDSIGHT)
    _run_binary_to_private_file(
        [
            "/usr/bin/docker",
            "exec",
            "-u",
            "postgres",
            CURRENT_ROUTE_POSTGRES,
            "sh",
            "-lc",
            'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom',
        ],
        operational_dump,
    )
    _run_binary_to_private_file(
        [
            "/usr/bin/docker",
            "exec",
            "-u",
            "postgres",
            CURRENT_ROUTE_HINDSIGHT,
            "sh",
            "-lc",
            'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "--format=custom --no-owner --no-privileges",
        ],
        hindsight_dump,
    )
    return (
        operational_before,
        _operational_counts(CURRENT_ROUTE_POSTGRES),
        hindsight_before,
        _operational_counts(CURRENT_ROUTE_HINDSIGHT),
    )


def _current_environment_value(name: str) -> str:
    metadata = os.lstat(CURRENT_ROUTE_ENVIRONMENT)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size > 65_536
    ):
        raise ProbeError("current managed route environment is not private")
    matches = []
    for raw_line in CURRENT_ROUTE_ENVIRONMENT.read_text(encoding="utf-8").splitlines():
        key, separator, value = raw_line.partition("=")
        if separator and key == name:
            matches.append(value.strip())
    if len(matches) != 1 or not matches[0]:
        raise ProbeError(f"current managed route {name} is unavailable")
    return matches[0]


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
    req = request.Request(
        url, data=body, headers=headers, method="POST" if body else "GET"
    )
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


def _verify_dashboard_access(port: int, token_file: Path) -> None:
    token = token_file.read_text(encoding="utf-8").strip()
    if len(token) < 32 or any(character.isspace() for character in token):
        raise ProbeError("candidate dashboard token is invalid")
    url = f"http://127.0.0.1:{port}/internal/v1/dashboard/workboard"
    authenticated = request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with request.urlopen(authenticated, timeout=10) as response:
            if response.status != 200:
                raise ProbeError("candidate dashboard rejected its bound owner")
    except error.URLError as exc:
        raise ProbeError("candidate dashboard owner route is unavailable") from exc
    try:
        with request.urlopen(request.Request(url, method="GET"), timeout=10):
            pass
    except error.HTTPError as exc:
        if exc.code == 401:
            return
        raise ProbeError("candidate dashboard anonymous status differs") from exc
    except error.URLError as exc:
        raise ProbeError("candidate dashboard anonymous route is unavailable") from exc
    raise ProbeError("candidate dashboard accepted an anonymous request")


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


def _live_status(url: str, *, attempts: int = 3) -> int:
    for attempt in range(attempts):
        try:
            with request.urlopen(url, timeout=15) as response:
                return response.status
        except (OSError, error.URLError):
            if attempt + 1 < attempts:
                time.sleep(1)
    return 0


def _wait_healthy(container: str, *, timeout: int = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        inspected = json.loads(_run(["/usr/bin/docker", "inspect", container]))[0]
        state = inspected.get("State", {})
        health = state.get("Health", {}).get("Status")
        if state.get("Running") is True and health in {None, "healthy"}:
            return
        if state.get("Running") is not True and state.get("Status") in {
            "dead",
            "exited",
        }:
            raise ProbeError("candidate database exited before becoming healthy")
        time.sleep(1)
    raise ProbeError("candidate database did not become healthy")


def _immutable_core_image(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}", value):
        raise ProbeError(
            "candidate core image must be an immutable local digest reference"
        )
    return value


def _immutable_local_candidate_image_id(value: str) -> str:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ProbeError("local candidate image must be one complete sha256 image ID")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise ProbeError("candidate authority could not be read") from exc
    return digest.hexdigest()


def _candidate_authority(
    path: Path,
    *,
    expected_sha256: str,
    image_id: str,
) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ProbeError("candidate authority SHA-256 is invalid")
    if not path.is_file() or path.is_symlink():
        raise ProbeError("candidate authority must be one regular file")
    if path.stat().st_size > 1024 * 1024:
        raise ProbeError("candidate authority is too large")
    actual_sha256 = _sha256_file(path)
    if not secrets.compare_digest(actual_sha256, expected_sha256):
        raise ProbeError("candidate authority SHA-256 differs")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProbeError("candidate authority is not valid JSON") from exc
    if not isinstance(document, dict):
        raise ProbeError("candidate authority root is invalid")

    files = document.get("files")
    findings = document.get("findings")
    nonclaims = document.get("nonclaims")
    sbom = document.get("sbom")
    trivy = document.get("trivy")
    if (
        document.get("contract") != "EvaManagedBCandidateArtifactAnalysis/v1"
        or document.get("status") != "passed_candidate_artifacts"
        or document.get("candidate_image_id") != image_id
        or document.get("network_used") is not False
        or findings != {"HIGH": 0, "CRITICAL": 0}
        or not isinstance(files, dict)
        or not re.fullmatch(r"[0-9a-f]{64}", str(files.get("image/image.tar", "")))
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(document.get("candidate_receipt_sha256", ""))
        )
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(document.get("tool_authority_sha256", ""))
        )
        or not isinstance(sbom, dict)
        or sbom.get("format") != "SPDX-2.3-json"
        or not isinstance(trivy, dict)
        or not isinstance(trivy.get("scanner_version"), str)
        or not isinstance(trivy.get("database_updated_at"), str)
        or not isinstance(nonclaims, list)
        or not all(isinstance(item, str) for item in nonclaims)
        or not any("not tagged" in item for item in nonclaims)
    ):
        raise ProbeError("candidate authority does not admit this disposable run")
    return {
        "contract": document["contract"],
        "candidateReceiptSha256": document["candidate_receipt_sha256"],
        "fileSha256": actual_sha256,
        "findings": {"CRITICAL": 0, "HIGH": 0},
        "imageArchiveSha256": files["image/image.tar"],
        "networkUsed": False,
        "sbomFormat": "SPDX-2.3-json",
        "scannerDatabaseUpdatedAt": trivy["database_updated_at"],
        "scannerVersion": trivy["scanner_version"],
        "status": document["status"],
        "toolAuthoritySha256": document["tool_authority_sha256"],
    }


def _inspect_local_candidate_image(
    image_id: str, *, expected_source_commit: str
) -> dict[str, Any]:
    try:
        payload = json.loads(
            _run(["/usr/bin/docker", "image", "inspect", image_id], timeout=30)
        )
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProbeError("local candidate image inspection is invalid") from exc
    if not isinstance(payload, list) or len(payload) != 1:
        raise ProbeError("local candidate image inspection is ambiguous")
    inspected = payload[0]
    if not isinstance(inspected, dict):
        raise ProbeError("local candidate image inspection is invalid")
    config = inspected.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    runtime_library_path = _require_runtime_library_path(config)
    if (
        inspected.get("Id") != image_id
        or inspected.get("Os") != "linux"
        or inspected.get("Architecture") != "amd64"
        or inspected.get("RepoTags") not in (None, [])
        or inspected.get("RepoDigests") not in (None, [])
        or not isinstance(labels, dict)
        or labels.get("io.eva2.source-commit") != expected_source_commit
        or labels.get("io.eva2.source-refresh") != "git-archive-v1"
        or not re.fullmatch(r"[0-9]+", str(labels.get("io.eva2.source-date-epoch", "")))
    ):
        raise ProbeError("local candidate image identity or provenance differs")
    return {
        "architecture": "amd64",
        "id": image_id,
        "os": "linux",
        "runtimeLibraryPath": runtime_library_path,
        "sourceCommit": expected_source_commit,
        "sourceDateEpoch": labels["io.eva2.source-date-epoch"],
        "sourceRefresh": "git-archive-v1",
    }


def _validate_local_candidate_render(
    rendered: dict[str, Any], *, image_id: str
) -> dict[str, dict[str, str]]:
    services = rendered.get("services")
    if not isinstance(services, dict):
        raise ProbeError("rendered service graph is invalid")
    bindings: dict[str, dict[str, str]] = {}
    for name in CORE_IMAGE_SERVICES:
        service = services.get(name)
        if (
            not isinstance(service, dict)
            or service.get("image") != image_id
            or service.get("pull_policy") != "never"
        ):
            raise ProbeError("local candidate render binding differs")
        bindings[name] = {"image": image_id, "pullPolicy": "never"}
    for name, service in services.items():
        if (
            name not in CORE_IMAGE_SERVICES
            and isinstance(service, dict)
            and service.get("image") == image_id
        ):
            raise ProbeError("local candidate image escaped its service set")
    return dict(sorted(bindings.items()))


def _inspect_started_local_candidate_services(
    project: str, environment: Path, *, image_id: str, expected_source_commit: str
) -> dict[str, dict[str, str]]:
    bindings: dict[str, dict[str, str]] = {}
    for service in sorted(STARTED_CORE_IMAGE_SERVICES):
        container_id = _run(
            _compose(project, environment, "ps", "-q", service), timeout=30
        ).strip()
        if not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
            raise ProbeError("local candidate service container is missing")
        try:
            inspected = json.loads(
                _run(["/usr/bin/docker", "inspect", container_id], timeout=30)
            )
        except (json.JSONDecodeError, TypeError) as exc:
            raise ProbeError("local candidate container inspection is invalid") from exc
        if not isinstance(inspected, list) or len(inspected) != 1:
            raise ProbeError("local candidate container inspection is ambiguous")
        container = inspected[0]
        config = container.get("Config") if isinstance(container, dict) else None
        labels = config.get("Labels") if isinstance(config, dict) else None
        runtime_library_path = _require_runtime_library_path(config)
        if (
            not isinstance(container, dict)
            or container.get("Image") != image_id
            or not isinstance(config, dict)
            or config.get("Image") != image_id
            or not isinstance(labels, dict)
            or labels.get("io.eva2.source-commit") != expected_source_commit
            or labels.get("com.docker.compose.project") != project
            or labels.get("com.docker.compose.service") != service
        ):
            raise ProbeError("started local candidate binding differs")
        bindings[service] = {
            "containerId": container_id,
            "image": image_id,
            "runtimeLibraryPath": runtime_library_path,
        }
    return bindings


def _write_new_private_receipt(path: Path, value: object) -> None:
    try:
        write_new_private_json(path, value, label="clean-graph probe receipt")
    except PrivateReceiptError as error:
        raise ProbeError(str(error)) from error


def main(
    *,
    clone_current_route: bool = False,
    core_image: str | None = None,
    candidate_local_image_id: str | None = None,
    candidate_authority: Path | None = None,
    expected_candidate_authority_sha256: str | None = None,
    expected_source_commit: str | None = None,
    retain_slot: bool = False,
    retained_slot_number: int = 2,
    conversation_prompt_file: Path | None = None,
    receipt_path: Path | None = None,
) -> int:
    if os.geteuid() != 0:
        print(json.dumps({"error": "probe requires root", "status": "failed"}))
        return 2
    try:
        conversation_prompt = _conversation_prompt(conversation_prompt_file)
    except (OSError, ProbeError) as exc:
        print(json.dumps({"error": str(exc), "status": "failed"}, sort_keys=True))
        return 2
    local_candidate = candidate_local_image_id is not None
    local_candidate_authority: dict[str, Any] | None = None
    local_candidate_inspection: dict[str, Any] | None = None
    try:
        if local_candidate:
            if core_image is not None:
                raise ProbeError(
                    "--core-image and --candidate-local-image-id are mutually exclusive"
                )
            if clone_current_route or retain_slot:
                raise ProbeError(
                    "local candidate image is allowed only with fresh disposable state"
                )
            if (
                candidate_authority is None
                or expected_candidate_authority_sha256 is None
                or expected_source_commit is None
            ):
                raise ProbeError(
                    "local candidate image requires authority SHA-256 and source commit"
                )
            if not re.fullmatch(r"[0-9a-f]{40}", expected_source_commit):
                raise ProbeError("candidate source commit is invalid")
            core_image = _immutable_local_candidate_image_id(candidate_local_image_id)
            local_candidate_authority = _candidate_authority(
                candidate_authority,
                expected_sha256=expected_candidate_authority_sha256,
                image_id=core_image,
            )
            local_candidate_inspection = _inspect_local_candidate_image(
                core_image, expected_source_commit=expected_source_commit
            )
        else:
            if any(
                value is not None
                for value in (
                    candidate_authority,
                    expected_candidate_authority_sha256,
                    expected_source_commit,
                )
            ):
                raise ProbeError(
                    "candidate authority options require --candidate-local-image-id"
                )
            core_image = _immutable_core_image(core_image or CORE_IMAGE)
    except (OSError, ProbeError) as exc:
        print(json.dumps({"error": str(exc), "status": "failed"}, sort_keys=True))
        return 2
    assert core_image is not None
    if retain_slot and not clone_current_route:
        print(
            json.dumps(
                {
                    "error": "retained slot requires a current-route clone",
                    "status": "failed",
                },
                sort_keys=True,
            )
        )
        return 2
    if retain_slot and retained_slot_number not in {2, 3, 4}:
        print(
            json.dumps(
                {"error": "retained slot number must be 2, 3 or 4", "status": "failed"},
                sort_keys=True,
            )
        )
        return 2
    retained_name = f"slot{retained_slot_number}"
    run_id = retained_name if retain_slot else uuid4().hex[:12]
    project = (
        f"eva2-managed-b-{retained_name}" if retain_slot else f"eva2-clean-b-{run_id}"
    )
    core_name = (
        f"eva2-managed-b-{retained_name}-core"
        if retain_slot
        else f"eva2-clean-b-core-{run_id}"
    )
    staging = (
        STAGING_PARENT / f"managed-b-{retained_name}"
        if retain_slot
        else STAGING_PARENT / f"managed-b-clean-probe-{run_id}"
    )
    port = 44707 + retained_slot_number if retain_slot else _available_port()
    operational_password = secrets.token_urlsafe(32)
    memory_password = secrets.token_urlsafe(32)
    environment_file = staging / "candidate.env"
    operational_dump = staging / "operational.dump"
    hindsight_dump = staging / "hindsight.dump"
    cleaned = False
    keep_retained = False
    staging_created = False
    phase = "prepare"
    result: dict[str, Any] = {
        "contract": (
            "Eva2ManagedBCurrentDataTransitionProbeReceipt/v1"
            if clone_current_route
            else "Eva2ManagedBCleanGraphProbeReceipt/v1"
        ),
        "issuedAt": datetime.now(UTC).isoformat(),
        "manifestSha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        "coreImage": core_image,
        "coreImageMode": (
            "local_image_id_disposable_candidate"
            if local_candidate
            else "repository_digest"
        ),
        "status": "failed",
    }
    if local_candidate:
        assert local_candidate_authority is not None
        assert local_candidate_inspection is not None
        result["candidateAuthority"] = local_candidate_authority
        result["candidateImageInspectionBeforeResources"] = local_candidate_inspection
        result["nonclaims"] = [
            "This daemon-local candidate run is not a tag, push, signature, pullable release, deployment, promotion or retained slot.",
            "This run does not satisfy the repository-digest release boundary.",
            "The nine-service manifest is rendered; only the support, schema and core lifecycle is executed.",
            "This fresh disposable run does not clone or write live Eva2 data and does not route candidate traffic through the live route; only existing safety health endpoints are read.",
        ]
    initial_live = {
        "eva1": _live_status("http://127.0.0.1:3210/api/health"),
        "currentEva2": _live_status(f"http://127.0.0.1:{CURRENT_ROUTE_PORT}/readyz"),
    }
    try:
        if retain_slot:
            if staging.exists() or _live_status(f"http://127.0.0.1:{port}/readyz"):
                raise ProbeError("retained managed-B slot already exists")
            existing = _run(
                [
                    "/usr/bin/docker",
                    "ps",
                    "--all",
                    "--quiet",
                    "--filter",
                    f"label=com.docker.compose.project={project}",
                ]
            ).strip()
            if existing:
                raise ProbeError("retained managed-B Docker project already exists")
            retained_volumes = {
                f"{project}-operational",
                f"{project}-memory",
                f"{project}-work",
            }
            existing_volumes = set(
                _run(
                    [
                        "/usr/bin/docker",
                        "volume",
                        "ls",
                        "--format",
                        "{{.Name}}",
                    ]
                ).splitlines()
            )
            if retained_volumes & existing_volumes:
                raise ProbeError("retained managed-B Docker volume already exists")
        staging.mkdir(mode=0o700)
        staging_created = True
        secrets_dir = staging / "secrets"
        identity_dir = staging / "identity"
        codex_dir = staging / "codex-auth"
        model_lease = staging / "model-access-lease"
        hindsight_lease = staging / "hindsight-access"
        restore_authority_dir = staging / "restore-authority"
        for directory, uid, gid, mode in (
            (secrets_dir, 0, 0, 0o700),
            (identity_dir, 65532, 65532, 0o700),
            (codex_dir, 1000, 1000, 0o700),
            (model_lease, 1000, 65532, 0o2750),
            (hindsight_lease, 1000, 1000, 0o700),
            (restore_authority_dir, 65532, 65532, 0o700),
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
        if retain_slot:
            _copy_private(
                CURRENT_ROUTE_HA_TOKEN,
                secrets_dir / "home-assistant-token",
                uid=65532,
                gid=65532,
            )
            home_assistant_url = _current_environment_value("EVA_HOME_ASSISTANT_URL")
            home_effect_allowlist = _current_environment_value(
                "EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST"
            )
            home_effect_principals = _current_environment_value(
                "EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST"
            )
            if not re.fullmatch(r"https?://[^\s]{1,2048}", home_assistant_url):
                raise ProbeError("current Home Assistant URL is invalid")
        else:
            _write_private(
                secrets_dir / "home-assistant-token",
                b"probe-only",
                uid=65532,
                gid=65532,
            )
            home_assistant_url = "http://127.0.0.1:9"
            home_effect_allowlist = ""
            home_effect_principals = ""
        if clone_current_route:
            _copy_private(
                CURRENT_ROUTE_DASHBOARD_TOKEN,
                secrets_dir / "dashboard-token",
                uid=65532,
                gid=65532,
            )
        else:
            _write_private(
                secrets_dir / "dashboard-token",
                secrets.token_urlsafe(32).encode(),
                uid=65532,
                gid=65532,
            )
        _write_private(
            secrets_dir / "telegram-token", b"probe-only", uid=65532, gid=65532
        )
        _write_private(
            secrets_dir / "telegram-bindings",
            (
                b'{"bindings":[{"allowed_chat_ids":["900000001"],'
                b'"principal_binding_id":"44444444-4444-4444-8444-444444444444",'
                b'"principal_id":"33333333-3333-3333-8333-333333333333",'
                b'"telegram_user_id":900000001}],'
                b'"contract":"EvaTelegramPrincipalBindings/v2"}'
            ),
            uid=65532,
            gid=65532,
        )
        _write_private(
            secrets_dir / "telegram-pairing",
            secrets.token_urlsafe(32).encode(),
            uid=65532,
            gid=65532,
        )
        _copy_private(
            IDENTITY_PACK, identity_dir / "current.json", uid=65532, gid=65532
        )
        _copy_private(IDENTITY_TRUST, identity_dir / "trust.json", uid=65532, gid=65532)
        if not retain_slot:
            _copy_private(SOURCE_AUTH, codex_dir / "auth.json", uid=1000, gid=1000)

        runtime_tenant_id = (
            "live-test-tenant"
            if retain_slot
            else "11111111-1111-1111-8111-111111111111"
        )
        runtime_installation_id = (
            "live-test-installation"
            if retain_slot
            else "22222222-2222-2222-8222-222222222222"
        )
        runtime_budget_file = secrets_dir / "runtime-budget.json"
        runtime_budget_values = {
            "recall_context_tokens": 2048,
            "retrieval_nodes": 40,
            "max_input_tokens": 12000,
            "max_output_tokens": 1200,
            "profile_context_tokens": 800,
            "memory_records_per_person": 2000,
            "memory_bytes_per_person": 20971520,
            "memory_records_per_group": 2000,
            "memory_bytes_per_group": 20971520,
            "memory_records_per_project": 2000,
            "memory_bytes_per_project": 20971520,
            "memory_records_per_installation": 20000,
            "memory_bytes_per_installation": 209715200,
            "retention_days": 365,
            "background_reflections_per_person_day": 4,
            "background_reflections_per_installation_day": 40,
            "external_monthly_spend_cap_eur_cents": 0,
        }
        installer_budget_payload = {
            "contract": "EvaInstallerBudget/v2",
            **runtime_budget_values,
            "unknown_cost_policy": "block_autonomous_spend",
        }
        source_budget_sha256 = hashlib.sha256(
            json.dumps(
                installer_budget_payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        runtime_budget = {
            "budget": installer_budget_payload,
            "contract": "EvaRuntimeResourceBudget/v2",
            "installation_id": runtime_installation_id,
            "source_budget_sha256": source_budget_sha256,
            "tenant_id": runtime_tenant_id,
        }
        _write_private(
            runtime_budget_file,
            json.dumps(
                runtime_budget,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            uid=65532,
            gid=65532,
        )

        values = {
            "EVA_B_CONFIGURATION_SHA256": hashlib.sha256(run_id.encode()).hexdigest(),
            "EVA_LIVING_CORE_IMAGE": core_image,
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
            "EVA_HOME_ASSISTANT_URL": home_assistant_url,
            "EVA_HOME_ASSISTANT_EFFECTS_ENABLED": "true" if retain_slot else "false",
            "EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST": home_effect_allowlist,
            "EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST": home_effect_principals,
            "EVA_HOME_ASSISTANT_TOKEN_FILE_HOST": (
                secrets_dir / "home-assistant-token"
            ).as_posix(),
            "EVA_DASHBOARD_ACCESS_TOKEN_FILE_HOST": (
                secrets_dir / "dashboard-token"
            ).as_posix(),
            "EVA_DASHBOARD_TENANT_ID": "11111111-1111-1111-8111-111111111111",
            "EVA_DASHBOARD_INSTALLATION_ID": "22222222-2222-2222-8222-222222222222",
            "EVA_DASHBOARD_PRINCIPAL_BINDING_ID": "44444444-4444-4444-8444-444444444444",
            "EVA_DASHBOARD_RELEASE_SHA256": core_image.rsplit("sha256:", 1)[-1],
            "EVA_PORTABLE_RESTORE_AUTHORITY_DIR_HOST": (
                restore_authority_dir.as_posix()
            ),
            "EVA_RUNTIME_RESOURCE_BUDGET_FILE_HOST": runtime_budget_file.as_posix(),
            "EVA_IDENTITY_PACK_FILE_HOST": (identity_dir / "current.json").as_posix(),
            "EVA_IDENTITY_PACK_TRUST_FILE_HOST": (
                identity_dir / "trust.json"
            ).as_posix(),
            "EVA_TELEGRAM_BOT_TOKEN_FILE_HOST": (
                secrets_dir / "telegram-token"
            ).as_posix(),
            "EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE_HOST": (
                secrets_dir / "telegram-bindings"
            ).as_posix(),
            "EVA_TELEGRAM_PAIRING_SECRET_FILE_HOST": (
                secrets_dir / "telegram-pairing"
            ).as_posix(),
            "EVA_RUNTIME_TENANT_ID": runtime_tenant_id,
            "EVA_RUNTIME_INSTALLATION_ID": runtime_installation_id,
            "EVA_RUNTIME_PRINCIPAL_ID": "33333333-3333-3333-8333-333333333333",
            # The retained canary has no real durable-goal provider.  Keep the
            # worker wiring renderable, but do not advertise an unusable
            # conversation capability.
            "EVA_DURABLE_GOAL_ADMISSION_PROVIDER": "",
            "EVA_DURABLE_GOAL_ADMISSION_RECIPIENTS": "",
            "EVA_DURABLE_GOAL_PROVIDER": "probe",
            "EVA_DURABLE_GOAL_PROVIDER_URL": "http://127.0.0.1:9",
            "EVA_DURABLE_GOAL_RECIPIENTS": "probe",
            "EVA_FANOUT_RECIPIENT_ENDPOINTS_JSON": '{"probe":"https://example.invalid"}',
            "EVA_FANOUT_ALLOW_INSECURE_HTTP": "false",
            "EVA_FILE_BATCH_ROOT": "/var/lib/eva/work",
            "EVA_HINDSIGHT_BANK_NAMESPACE": (
                CURRENT_ROUTE_HINDSIGHT_NAMESPACE
                if clone_current_route
                else f"probe-{run_id}"
            ),
            "EVA_CODEX_AUTH_DIR": (
                SOURCE_AUTH.parent.as_posix() if retain_slot else codex_dir.as_posix()
            ),
            "EVA_MODEL_ACCESS_LEASE_DIR": model_lease.as_posix(),
            "EVA_HINDSIGHT_ACCESS_DIR": hindsight_lease.as_posix(),
        }
        _write_private(
            environment_file,
            "".join(
                f"{key}={value}\n" for key, value in sorted(values.items())
            ).encode(),
            uid=0,
            gid=0,
        )

        source_counts: (
            tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]] | None
        ) = None
        if clone_current_route:
            phase = "current-route-snapshot"
            source_counts = _snapshot_current_route(operational_dump, hindsight_dump)

        phase = "render"
        rendered = json.loads(
            _run(
                _compose(
                    project,
                    environment_file,
                    "--profile",
                    "schema-install",
                    "config",
                    "--format",
                    "json",
                )
            )
        )
        if set(rendered.get("services", {})) != EXPECTED_SERVICES:
            raise ProbeError("rendered service graph differs")
        hindsight_environment = rendered["services"]["hindsight"]["environment"]
        if "HINDSIGHT_API_DATABASE_URL" in hindsight_environment:
            raise ProbeError("render exposed an inline Hindsight DSN")
        if local_candidate:
            result["candidateRenderedBindings"] = _validate_local_candidate_render(
                rendered, image_id=core_image
            )
            assert expected_source_commit is not None
            phase = "local-candidate-reinspect-before-start"
            repeated_inspection = _inspect_local_candidate_image(
                core_image, expected_source_commit=expected_source_commit
            )
            if repeated_inspection != local_candidate_inspection:
                raise ProbeError("local candidate image changed before start")
            result["candidateImageInspectionBeforeStart"] = repeated_inspection

        phase = "support-start"
        support_services = [
            "postgres",
            "hindsight-postgres",
            "model-access-lease",
            "hindsight-access-lease",
        ]
        if not clone_current_route:
            support_services.append("hindsight")
        _run(_compose(project, environment_file, "up", "-d", *support_services))

        restored_operational: dict[str, int] | None = None
        restored_hindsight: dict[str, int] | None = None
        if clone_current_route:
            phase = "candidate-database-ready"
            operational_id = _run(
                _compose(project, environment_file, "ps", "-q", "postgres")
            ).strip()
            memory_id = _run(
                _compose(project, environment_file, "ps", "-q", "hindsight-postgres")
            ).strip()
            if not operational_id or not memory_id:
                raise ProbeError("candidate database container was not created")
            _wait_healthy(operational_id)
            _wait_healthy(memory_id)
            phase = "current-route-restore"
            _prepare_logical_restore_roles(operational_id, "eva2")
            _restore_private_dump(
                operational_id,
                "eva2",
                operational_dump,
                preserve_authority=True,
            )
            _restore_private_dump(memory_id, "hindsight", hindsight_dump)
            restored_operational = _operational_counts(operational_id, "eva2")
            restored_hindsight = _operational_counts(memory_id, "hindsight")
            assert source_counts is not None
            operational_before, operational_after, hindsight_before, hindsight_after = (
                source_counts
            )
            _validate_count_window(
                operational_before, restored_operational, operational_after
            )
            _validate_count_window(
                hindsight_before, restored_hindsight, hindsight_after
            )
            phase = "restored-hindsight-start"
            _run(_compose(project, environment_file, "up", "-d", "hindsight"))
        phase = "schema-install"
        _run(
            _compose(
                project,
                environment_file,
                "--profile",
                "schema-install",
                "run",
                "--rm",
                "schema-installer",
            )
        )
        if not clone_current_route:
            phase = "dashboard-authority-install"
            _run(
                _compose(
                    project,
                    environment_file,
                    "--profile",
                    "schema-install",
                    "run",
                    "--rm",
                    "--entrypoint",
                    "/usr/local/bin/python",
                    "schema-installer",
                    "-m",
                    "eva.operations.initialize_dashboard_authority",
                )
            )
        phase = "core-start"
        _run(_compose(project, environment_file, "up", "-d", "core"))
        phase = "core-ready-before-restart"
        _wait_ready(port)
        if local_candidate:
            assert expected_source_commit is not None
            result["candidateStartedBindings"] = (
                _inspect_started_local_candidate_services(
                    project,
                    environment_file,
                    image_id=core_image,
                    expected_source_commit=expected_source_commit,
                )
            )
        phase = "dashboard-authority-readback"
        _verify_dashboard_access(port, secrets_dir / "dashboard-token")

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
            raise ProbeError(
                "Hindsight database did not load the pinned vector extension"
            )

        phase = "real-model-turn"
        message_id = f"managed-b-probe:{run_id}:1"
        turn = {
            "audience": {
                "audience_id": "person:33333333-3333-3333-8333-333333333333",
                "kind": "private_person",
                "principal_id": "33333333-3333-3333-8333-333333333333",
            },
            "conversation_id": f"managed-b-probe:{run_id}",
            "installation_id": (
                "live-test-installation"
                if retain_slot
                else "22222222-2222-2222-8222-222222222222"
            ),
            "message_id": message_id,
            "occurred_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "principal_id": "33333333-3333-3333-8333-333333333333",
            "tenant_id": (
                "live-test-tenant"
                if retain_slot
                else "11111111-1111-1111-8111-111111111111"
            ),
            "text": conversation_prompt,
        }
        reply = _http_json(
            f"http://127.0.0.1:{port}/internal/v1/turns/respond", payload=turn
        )
        reply_text = reply.get("text")
        if not isinstance(reply_text, str) or not reply_text.strip():
            raise ProbeError("real model response was empty")
        if (
            conversation_prompt == DEFAULT_CONVERSATION_PROMPT
            and reply_text.strip().casefold() != "beheerd"
        ):
            raise ProbeError("real model response was not the requested exact word")
        operational_id = _run(
            _compose(project, environment_file, "ps", "-q", "postgres")
        ).strip()
        durable_reply = _durable_conversation_reply(operational_id, "eva2", message_id)
        measurement = _conversation_measurement(durable_reply, expected_text=reply_text)
        result["conversationTurn"] = {
            "messageId": message_id,
            "prompt": conversation_prompt,
            "promptSha256": hashlib.sha256(
                conversation_prompt.encode("utf-8")
            ).hexdigest(),
            "reply": reply_text.strip(),
            "replySha256": hashlib.sha256(
                reply_text.strip().encode("utf-8")
            ).hexdigest(),
            "usage": measurement,
        }

        phase = "joint-stop"
        _run(
            _compose(
                project,
                environment_file,
                "stop",
                "core",
                "hindsight",
                "hindsight-postgres",
                "postgres",
            )
        )
        phase = "joint-start"
        _run(
            _compose(
                project,
                environment_file,
                "start",
                "postgres",
                "hindsight-postgres",
                "hindsight",
                "core",
            )
        )
        phase = "core-ready-after-restart"
        _wait_ready(port)

        if clone_current_route:
            memory_id = _run(
                _compose(project, environment_file, "ps", "-q", "hindsight-postgres")
            ).strip()
            memory_after_restart = _operational_counts(memory_id, "hindsight")
            assert restored_hindsight is not None
            for table in _PRESERVED_HINDSIGHT_TABLES:
                if table not in restored_hindsight or (
                    memory_after_restart.get(table, -1) < restored_hindsight[table]
                ):
                    raise ProbeError(
                        "restored Hindsight semantic data decreased after restart"
                    )

        hindsight_id = _run(
            _compose(project, environment_file, "ps", "-q", "hindsight")
        ).strip()
        inspected = json.loads(_run(["/usr/bin/docker", "inspect", hindsight_id]))[0]
        configured_names = {
            item.partition("=")[0] for item in inspected["Config"]["Env"]
        }
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
                "dashboardOwnerAuthenticated": True,
                "dashboardAnonymousRejected": True,
                "realCodexReplyExact": (
                    conversation_prompt == DEFAULT_CONVERSATION_PROMPT
                ),
                "coreReadyAfterDatabaseAndServiceRestart": True,
                "publishedAddress": "127.0.0.1",
                "temporaryPort": port,
                "liveSafetyBefore": initial_live,
            }
        )
        if clone_current_route:
            assert source_counts is not None
            assert restored_operational is not None
            assert restored_hindsight is not None
            operational_before, operational_after, hindsight_before, hindsight_after = (
                source_counts
            )
            result["dataTransition"] = {
                "sourceRoutePort": int(CURRENT_ROUTE_PORT),
                "sourceOperationalVolume": CURRENT_ROUTE_POSTGRES_VOLUME,
                "sourceHindsightVolume": CURRENT_ROUTE_HINDSIGHT_VOLUME,
                "sourceDatabasesReadOnly": True,
                "operationalTableCount": len(restored_operational),
                "hindsightTableCount": len(restored_hindsight),
                "operationalCountDigest": _count_digest(restored_operational),
                "hindsightCountDigest": _count_digest(restored_hindsight),
                "operationalDumpSha256": hashlib.sha256(
                    operational_dump.read_bytes()
                ).hexdigest(),
                "hindsightDumpSha256": hashlib.sha256(
                    hindsight_dump.read_bytes()
                ).hexdigest(),
                "operationalDumpBytes": operational_dump.stat().st_size,
                "hindsightDumpBytes": hindsight_dump.stat().st_size,
                "restoredWithinOperationalSnapshotWindow": True,
                "restoredWithinHindsightSnapshotWindow": True,
                "sourceOperationalWindowDigests": [
                    _count_digest(operational_before),
                    _count_digest(operational_after),
                ],
                "sourceHindsightWindowDigests": [
                    _count_digest(hindsight_before),
                    _count_digest(hindsight_after),
                ],
                "semanticHindsightDataSurvivedJointRestart": True,
                "plaintextRetainedAfterProbe": False,
            }
            if retain_slot:
                operational_dump.unlink()
                hindsight_dump.unlink()
                result["retainedSlot"] = {
                    "coreContainer": core_name,
                    "candidateRuntimeScope": "legacy-live-test-compatible",
                    "environmentFile": environment_file.as_posix(),
                    "port": port,
                    "project": project,
                    "plaintextDumpsRetained": False,
                    "requiresScopeMigrationBeforeActivation": False,
                    "sourceRuntimeScope": "legacy-live-test",
                    "status": "ready_not_active",
                }
    except Exception as exc:
        diagnostic = _sanitize_diagnostic(str(exc))
        result["error"] = (
            diagnostic
            if isinstance(exc, ProbeError)
            else f"unexpected {type(exc).__name__}: {diagnostic}"
        )
        result["failedPhase"] = phase
        if environment_file.exists() and not keep_retained:
            try:
                state = _run(
                    _compose(
                        project, environment_file, "ps", "--all", "--format", "json"
                    )
                )
                result["candidateState"] = _sanitize_diagnostic(state)
                for service in ("core", "hindsight"):
                    logs = _run(
                        _compose(
                            project,
                            environment_file,
                            "logs",
                            "--no-color",
                            "--tail",
                            "40",
                            service,
                        )
                    )
                    result[f"{service}Diagnostic"] = _sanitize_diagnostic(logs)
            except ProbeError:
                result["diagnosticCapture"] = "unavailable"
    finally:
        keep_retained = retain_slot and result.get("status") == "passed"
        if environment_file.exists() and not keep_retained:
            try:
                _run(
                    _compose(
                        project,
                        environment_file,
                        "down",
                        "--volumes",
                        "--remove-orphans",
                    )
                )
                cleaned = True
            except ProbeError:
                cleaned = False
        if (
            not keep_retained
            and staging_created
            and staging.parent == STAGING_PARENT
            and (
                staging.name.startswith("managed-b-clean-probe-")
                or staging.name
                in {"managed-b-slot2", "managed-b-slot3", "managed-b-slot4"}
            )
        ):
            shutil.rmtree(staging, ignore_errors=True)
        result["temporaryResourcesRemoved"] = (
            False
            if keep_retained
            else (not staging_created or cleaned) and not staging.exists()
        )
        result["retainedSlotReady"] = keep_retained
        result["liveSafetyAfter"] = {
            "eva1": _live_status("http://127.0.0.1:3210/api/health"),
            "currentEva2": _live_status(
                f"http://127.0.0.1:{CURRENT_ROUTE_PORT}/readyz"
            ),
        }
        operational_password = ""
        memory_password = ""

    if receipt_path is not None:
        try:
            _write_new_private_receipt(receipt_path, result)
        except (OSError, ProbeError) as exc:
            result["error"] = f"receipt persistence failed: {exc}"
            result["status"] = "failed"

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    completed = result["status"] == "passed" and (
        result["retainedSlotReady"]
        if retain_slot
        else result["temporaryResourcesRemoved"]
    )
    return 0 if completed else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--clone-current-route", action="store_true")
    image_group = parser.add_mutually_exclusive_group()
    image_group.add_argument("--core-image")
    image_group.add_argument("--candidate-local-image-id")
    parser.add_argument("--candidate-authority", type=Path)
    parser.add_argument("--expected-candidate-authority-sha256")
    parser.add_argument("--expected-source-commit")
    parser.add_argument("--retain-slot", action="store_true")
    parser.add_argument(
        "--retained-slot-number", type=int, choices=(2, 3, 4), default=2
    )
    parser.add_argument("--conversation-prompt-file", type=Path)
    parser.add_argument("--receipt", type=Path, required=True)
    arguments = parser.parse_args()
    sys.exit(
        main(
            clone_current_route=arguments.clone_current_route,
            core_image=arguments.core_image,
            candidate_local_image_id=arguments.candidate_local_image_id,
            candidate_authority=arguments.candidate_authority,
            expected_candidate_authority_sha256=(
                arguments.expected_candidate_authority_sha256
            ),
            expected_source_commit=arguments.expected_source_commit,
            retain_slot=arguments.retain_slot,
            retained_slot_number=arguments.retained_slot_number,
            conversation_prompt_file=arguments.conversation_prompt_file,
            receipt_path=arguments.receipt,
        )
    )
