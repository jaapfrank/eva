"""Lossless, quiescent cutover between the two retained managed-B slots.

The ordinary canary switch deliberately does not touch data.  Retained slots,
however, own separate PostgreSQL volumes.  This operator therefore stops all
user ingress, quiesces both database writers, copies and attests both logical
databases, and only then changes Eva1's explicit ``/eva2`` route.  The same
operation works in the opposite direction, so rollback first copies every new
write back instead of silently returning to stale state.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time
from typing import Any, Protocol
from urllib import error, request

from tools.eva2_canary_cutover import (
    EVA1_PATHS,
    _atomic_dropin,
    _read_exact_dropin,
    canonical_dropin,
)
from eva.operations.logical_restore_authority import logical_restore_preparation_sql


DOCKER = "/usr/bin/docker"
SYSTEMCTL = "/usr/bin/systemctl"
SERVICE = "eva.service"
DROPIN = Path("/etc/systemd/system/eva.service.d/30-eva2-current-candidate.conf")
LOCK = Path("/run/lock/eva2-quiescent-cutover.lock")
WORK_PARENT = Path("/var/lib/eva2/quiescent-cutover")
EVA1_DATABASE = Path("/home/administrator/project/Bottie/data/eva.db")
EVA1_BACKUPS = Path("/var/lib/eva2/eva1-cutover-backups")
MANIFEST = (
    Path(__file__).resolve().parents[1] / "deploy/living-core/managed-b.compose.yml"
)

_SCHEMA_HEAD_SCRIPT = (
    "from eva.operations.install_schema import _HEAD_REVISION; "
    "print(_HEAD_REVISION)"
)
_SCHEMA_DOWNGRADE_SCRIPT = (
    "import sys; "
    "from alembic import command; "
    "from alembic.config import Config; "
    "from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn; "
    "from eva.bootstrap.secrets import read_runtime_secret_file; "
    "dsn=sqlalchemy_postgres_dsn(read_runtime_secret_file("
    "'/run/secrets/database_dsn',setting_name='EVA_DATABASE_DSN_FILE')); "
    "config=Config('/opt/eva/alembic.ini'); "
    "config.set_main_option('sqlalchemy.url',dsn.replace('%','%%')); "
    "command.downgrade(config,sys.argv[1])"
)

_COUNT_SQL = """
SELECT stats.schemaname || '.' || stats.relname || '=' ||
       (xpath('/row/c/text()', query_to_xml(
           format('SELECT count(*) AS c FROM %I.%I', stats.schemaname, stats.relname),
           false, true, ''
       )))[1]::text
FROM pg_stat_user_tables AS stats
JOIN pg_class AS relation ON relation.oid = stats.relid
WHERE relation.relkind IN ('r', 'p')
ORDER BY stats.schemaname, stats.relname
"""


class QuiescentCutoverError(RuntimeError):
    """The retained-slot cutover stopped closed or restored its prior route."""


@dataclass(frozen=True, slots=True)
class SlotSpec:
    number: int
    project: str
    port: int
    environment: Path
    core: str
    hindsight: str
    operational_postgres: str
    memory_postgres: str
    operational_volume: str
    memory_volume: str
    dashboard_token: Path

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _slot(number: int) -> SlotSpec:
    if number not in {3, 4}:
        raise QuiescentCutoverError("only retained managed-B slots 3 and 4 are allowed")
    project = f"eva2-managed-b-slot{number}"
    return SlotSpec(
        number=number,
        project=project,
        port=44707 + number,
        environment=Path(f"/var/lib/eva2/managed-b-slot{number}/candidate.env"),
        core=f"{project}-core",
        hindsight=f"{project}-hindsight-1",
        operational_postgres=f"{project}-postgres-1",
        memory_postgres=f"{project}-hindsight-postgres-1",
        operational_volume=f"{project}-operational",
        memory_volume=f"{project}-memory",
        dashboard_token=Path(
            f"/var/lib/eva2/managed-b-slot{number}/secrets/dashboard-token"
        ),
    )


@dataclass(frozen=True, slots=True)
class DatabaseAttestation:
    counts: dict[str, int]
    count_digest: str
    schema_digest: str
    data_digest: str


@dataclass(frozen=True, slots=True)
class DatabaseSnapshot:
    operational_dump: Path
    memory_dump: Path
    operational_dump_sha256: str
    memory_dump_sha256: str
    operational_dump_bytes: int
    memory_dump_bytes: int
    operational: DatabaseAttestation
    memory: DatabaseAttestation


class CutoverBackend(Protocol):
    def preflight(self, source: SlotSpec, target: SlotSpec, dropin: Path) -> bytes: ...

    def stop_eva1(self) -> None: ...

    def backup_eva1(self, source: SlotSpec, target: SlotSpec) -> dict[str, object]: ...

    def stop_writers(self, slot: SlotSpec) -> None: ...

    def start_writers(self, slot: SlotSpec) -> None: ...

    def snapshot(
        self, slot: SlotSpec, workspace: Path, label: str
    ) -> DatabaseSnapshot: ...

    def restore(self, slot: SlotSpec, snapshot: DatabaseSnapshot) -> None: ...

    def migrate_schema(self, source: SlotSpec, target: SlotSpec) -> None: ...

    def verify_runtime(self, slot: SlotSpec) -> None: ...

    def write_route(self, dropin: Path, content: bytes) -> None: ...

    def start_eva1(self, slot: SlotSpec) -> dict[str, int]: ...


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _count_digest(counts: dict[str, int]) -> str:
    return hashlib.sha256(
        json.dumps(counts, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _dump_content_digest(lines: Iterable[bytes]) -> str:
    line_digests: list[bytes] = []
    for line in lines:
        # PostgreSQL 17+ emits a fresh anti-injection key on every dump.  It is
        # transport framing, not database content.
        if line.startswith((b"\\restrict ", b"\\unrestrict ")):
            continue
        line_digests.append(hashlib.sha256(line).digest())
    digest = hashlib.sha256()
    for line_digest in sorted(line_digests):
        digest.update(line_digest)
    return digest.hexdigest()


def _sanitize(value: str) -> str:
    value = re.sub(r"postgres(?:ql)?://[^\s'\"]+", "postgresql://<redacted>", value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "<redacted>", value)
    value = re.sub(r"\b\d{8,12}:[A-Za-z0-9_-]{20,}\b", "<redacted>", value)
    return " ".join(value.split())[-1200:]


def _shared_database_consumers(
    *,
    retained_core: str,
    inspected: Iterable[dict[str, Any]],
) -> list[str]:
    """Return running sibling containers that mount the retained core database."""

    documents = list(inspected)
    core = next(
        (
            document
            for document in documents
            if str(document.get("Name", "")).lstrip("/") == retained_core
        ),
        None,
    )
    if core is None:
        raise QuiescentCutoverError("retained slot core inspection is missing")
    sources = {
        str(mount.get("Source", ""))
        for mount in core.get("Mounts", [])
        if mount.get("Destination") == "/run/secrets/database_dsn"
        and mount.get("Source")
    }
    if len(sources) != 1:
        raise QuiescentCutoverError("retained slot database mount is invalid")
    source = next(iter(sources))
    consumers: list[str] = []
    for document in documents:
        name = str(document.get("Name", "")).lstrip("/")
        if not name or name == retained_core:
            continue
        if document.get("State", {}).get("Running") is not True:
            continue
        if any(
            mount.get("Destination") == "/run/secrets/database_dsn"
            and mount.get("Source") == source
            for mount in document.get("Mounts", [])
        ):
            consumers.append(name)
    return sorted(consumers)


class SystemBackend:
    """Strict local implementation for the two retained host slots."""

    def __init__(self, *, owner_uid: int = 0) -> None:
        self.owner_uid = owner_uid

    @staticmethod
    def _run(
        command: list[str],
        *,
        timeout: int = 180,
        extra_env: dict[str, str] | None = None,
    ) -> str:
        process_environment = {
            "HOME": "/root",
            "LC_ALL": "C",
            "PATH": os.environ["PATH"],
        }
        if extra_env:
            process_environment.update(extra_env)
        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=process_environment,
            )
        except subprocess.CalledProcessError as exc:
            diagnostic = _sanitize("\n".join((exc.stdout or "", exc.stderr or "")))
            raise QuiescentCutoverError(
                f"command failed closed: {command[-1]}: {diagnostic or 'no diagnostic'}"
            ) from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise QuiescentCutoverError(
                f"command failed closed: {command[-1]}"
            ) from exc
        return completed.stdout

    @staticmethod
    def _is_running(container: str) -> bool:
        completed = subprocess.run(
            [DOCKER, "inspect", "--format", "{{.State.Running}}", container],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return completed.returncode == 0 and completed.stdout.strip() == "true"

    @staticmethod
    def _private_file(path: Path, *, mode: int, uid: int) -> None:
        try:
            metadata = os.lstat(path)
        except OSError as exc:
            raise QuiescentCutoverError(
                "required private file is inaccessible"
            ) from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != mode
            or metadata.st_uid != uid
        ):
            raise QuiescentCutoverError("required private file metadata is unsafe")

    @staticmethod
    def _environment(path: Path) -> dict[str, str]:
        SystemBackend._private_file(path, mode=0o600, uid=0)
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator or not key or key in values:
                raise QuiescentCutoverError("managed-B environment is malformed")
            values[key] = value
        return values

    @staticmethod
    def _inspect(container: str) -> dict[str, Any]:
        try:
            document = json.loads(SystemBackend._run([DOCKER, "inspect", container]))
        except (ValueError, TypeError) as exc:
            raise QuiescentCutoverError("Docker inspection was invalid") from exc
        if not isinstance(document, list) or len(document) != 1:
            raise QuiescentCutoverError(
                "Docker inspection did not identify one container"
            )
        return document[0]

    @staticmethod
    def _inspect_all() -> list[dict[str, Any]]:
        names = SystemBackend._run(
            [DOCKER, "ps", "-a", "--format", "{{.Names}}"]
        ).splitlines()
        if not names:
            return []
        try:
            documents = json.loads(SystemBackend._run([DOCKER, "inspect", *names]))
        except (ValueError, TypeError) as exc:
            raise QuiescentCutoverError("Docker inspection was invalid") from exc
        if not isinstance(documents, list) or any(
            not isinstance(document, dict) for document in documents
        ):
            raise QuiescentCutoverError("Docker inspection was invalid")
        return documents

    def _verify_slot(self, slot: SlotSpec, *, source: bool) -> dict[str, str]:
        environment = self._environment(slot.environment)
        expected = {
            "EVA_STACK_CORE_CONTAINER": slot.core,
            "EVA_STACK_CORE_PORT": str(slot.port),
            "EVA_STACK_POSTGRES_VOLUME": slot.operational_volume,
            "EVA_STACK_HINDSIGHT_POSTGRES_VOLUME": slot.memory_volume,
        }
        if any(environment.get(key) != value for key, value in expected.items()):
            raise QuiescentCutoverError(
                "retained slot environment differs from its identity"
            )
        containers = {
            "core": slot.core,
            "hindsight": slot.hindsight,
            "postgres": slot.operational_postgres,
            "hindsight-postgres": slot.memory_postgres,
        }
        inspected = {
            service: self._inspect(name) for service, name in containers.items()
        }
        for service, document in inspected.items():
            labels = document.get("Config", {}).get("Labels", {})
            if (
                labels.get("com.docker.compose.project") != slot.project
                or labels.get("com.docker.compose.service") != service
            ):
                raise QuiescentCutoverError("retained slot container identity differs")
        running_services = set(
            self._run(
                [
                    DOCKER,
                    "ps",
                    "--filter",
                    f"label=com.docker.compose.project={slot.project}",
                    "--format",
                    '{{.Label "com.docker.compose.service"}}',
                ]
            ).splitlines()
        )
        allowed_running = {
            "core",
            "hindsight",
            "hindsight-access-lease",
            "hindsight-postgres",
            "model-access-lease",
            "postgres",
        }
        if not running_services <= allowed_running:
            raise QuiescentCutoverError(
                "retained slot has another active database writer"
            )
        for service, volume in (
            ("postgres", slot.operational_volume),
            ("hindsight-postgres", slot.memory_volume),
        ):
            mounts = {
                (item.get("Name"), item.get("Destination"))
                for item in inspected[service].get("Mounts", [])
                if item.get("Type") == "volume"
            }
            if (volume, "/var/lib/postgresql/data") not in mounts:
                raise QuiescentCutoverError("retained slot database volume differs")
            if inspected[service].get("State", {}).get("Running") is not True:
                raise QuiescentCutoverError("retained slot database is not running")
        if source and inspected["core"].get("State", {}).get("Running") is not True:
            raise QuiescentCutoverError("active retained slot core is not running")
        self._private_file(slot.dashboard_token, mode=0o600, uid=65532)
        return environment

    def preflight(self, source: SlotSpec, target: SlotSpec, dropin: Path) -> bytes:
        if os.geteuid() != 0:
            raise QuiescentCutoverError("quiescent cutover requires root authority")
        if source.number == target.number:
            raise QuiescentCutoverError("source and target slots must differ")
        previous = _read_exact_dropin(dropin, source.url, owner_uid=self.owner_uid)
        source_environment = self._verify_slot(source, source=True)
        target_environment = self._verify_slot(target, source=False)
        inspected = self._inspect_all()
        for slot in (source, target):
            if _shared_database_consumers(
                retained_core=slot.core,
                inspected=inspected,
            ):
                raise QuiescentCutoverError(
                    "another retained-slot database consumer is still running"
                )
        for key in ("EVA_POSTGRES_IMAGE", "EVA_HINDSIGHT_POSTGRES_IMAGE"):
            if source_environment.get(key) != target_environment.get(key):
                raise QuiescentCutoverError("retained slot database images differ")
        active = self._run(
            [SYSTEMCTL, "show", SERVICE, "--property=Environment", "--value"]
        )
        if f"EVA2_CANARY_URL={source.url}" not in active.split():
            raise QuiescentCutoverError("Eva1 did not load the expected source slot")
        if not self._ready(source):
            raise QuiescentCutoverError("active retained slot is not ready")
        return previous

    def stop_eva1(self) -> None:
        self._run([SYSTEMCTL, "stop", SERVICE], timeout=90)
        if (
            subprocess.run(
                [SYSTEMCTL, "is-active", "--quiet", SERVICE], check=False, timeout=10
            ).returncode
            == 0
        ):
            raise QuiescentCutoverError("Eva1 remained active during quiescence")

    def backup_eva1(self, source: SlotSpec, target: SlotSpec) -> dict[str, object]:
        self._private_file(EVA1_DATABASE, mode=0o600, uid=1000)
        EVA1_BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(EVA1_BACKUPS, 0o700)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        destination = EVA1_BACKUPS / (
            f"eva1-before-slot{source.number}-to-slot{target.number}-{timestamp}.db"
        )
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(descriptor)
        try:
            with sqlite3.connect(f"file:{EVA1_DATABASE}?mode=ro", uri=True) as live:
                with sqlite3.connect(destination) as backup:
                    live.backup(backup)
                    result = backup.execute("PRAGMA quick_check").fetchone()
            if result != ("ok",):
                raise QuiescentCutoverError("Eva1 safety backup failed quick_check")
            with destination.open("rb") as handle:
                os.fsync(handle.fileno())
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return {
            "bytes": destination.stat().st_size,
            "filename": destination.name,
            "quickCheck": "ok",
            "sha256": _sha256_file(destination),
        }

    def stop_writers(self, slot: SlotSpec) -> None:
        for container in (slot.core, slot.hindsight):
            if self._is_running(container):
                self._run([DOCKER, "stop", "--time", "30", container], timeout=60)
        if any(self._is_running(item) for item in (slot.core, slot.hindsight)):
            raise QuiescentCutoverError("retained slot writers did not stop")

    @staticmethod
    def _wait_container(container: str, *, timeout: int = 90) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            document = SystemBackend._inspect(container)
            state = document.get("State", {})
            health = state.get("Health", {}).get("Status")
            if state.get("Running") is True and health in {None, "healthy"}:
                return
            if state.get("Status") in {"dead", "exited"}:
                raise QuiescentCutoverError("retained slot service exited")
            time.sleep(1)
        raise QuiescentCutoverError("retained slot service did not become healthy")

    def start_writers(self, slot: SlotSpec) -> None:
        environment = self._environment(slot.environment)
        compose = [
            DOCKER,
            "compose",
            "--env-file",
            str(slot.environment),
            "--project-name",
            slot.project,
            "--file",
            str(MANIFEST),
            "up",
            "--detach",
            "--no-deps",
        ]
        self._run([*compose, "hindsight"])
        self._wait_container(slot.hindsight)
        self._run([*compose, "core"])
        self._wait_container(slot.core)
        expected = environment.get("EVA_LIVING_CORE_IMAGE", "").rsplit("@", 1)[-1]
        if self._inspect(slot.core).get("Image") != expected:
            raise QuiescentCutoverError(
                "retained slot core image differs from its environment"
            )

    @staticmethod
    def _schema_installer_command(slot: SlotSpec) -> list[str]:
        return [
            DOCKER,
            "compose",
            "--env-file",
            str(slot.environment),
            "--project-name",
            slot.project,
            "--file",
            str(MANIFEST),
            "--profile",
            "schema-install",
            "run",
            "--rm",
            "schema-installer",
        ]

    @classmethod
    def _image_schema_head(cls, image: str) -> str:
        output = cls._run(
            [
                DOCKER,
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--entrypoint",
                "/usr/local/bin/python",
                image,
                "-c",
                _SCHEMA_HEAD_SCRIPT,
            ]
        ).strip()
        if not re.fullmatch(r"[a-z0-9_]{8,96}", output):
            raise QuiescentCutoverError("candidate image schema head is invalid")
        return output

    @classmethod
    def _database_revision(cls, slot: SlotSpec) -> str:
        output = cls._run(
            [
                DOCKER,
                "exec",
                "-u",
                "postgres",
                slot.operational_postgres,
                "/usr/bin/psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "postgres",
                "-d",
                "eva2",
                "-Atc",
                "SELECT version_num FROM public.eva_alembic_version;",
            ]
        ).strip()
        if not re.fullmatch(r"[a-z0-9_]{8,96}", output):
            raise QuiescentCutoverError("restored database schema head is invalid")
        return output

    def _downgrade_to_target_head(
        self,
        *,
        source: SlotSpec,
        target: SlotSpec,
        source_image: str,
        target_head: str,
    ) -> None:
        command = [
            DOCKER,
            "compose",
            "--env-file",
            str(target.environment),
            "--project-name",
            target.project,
            "--file",
            str(MANIFEST),
            "--profile",
            "schema-install",
            "run",
            "--rm",
            "--no-deps",
            "--entrypoint",
            "/usr/local/bin/python",
            "schema-installer",
            "-c",
            _SCHEMA_DOWNGRADE_SCRIPT,
            target_head,
        ]
        self._run(
            command,
            timeout=300,
            extra_env={"EVA_LIVING_CORE_IMAGE": source_image},
        )

    def migrate_schema(self, source: SlotSpec, target: SlotSpec) -> None:
        """Upgrade normally or losslessly downgrade one retained release."""

        if self._is_running(target.core) or self._is_running(target.hindsight):
            raise QuiescentCutoverError(
                "schema migration requires stopped target writers"
            )
        installer = self._schema_installer_command(target)
        try:
            self._run(installer, timeout=300)
            return
        except QuiescentCutoverError as exc:
            if "database has an unknown Eva schema revision" not in str(exc):
                raise

        source_environment = self._environment(source.environment)
        target_environment = self._environment(target.environment)
        source_image = source_environment.get("EVA_LIVING_CORE_IMAGE", "")
        target_image = target_environment.get("EVA_LIVING_CORE_IMAGE", "")
        if not source_image or not target_image or source_image == target_image:
            raise QuiescentCutoverError(
                "schema rollback images are missing or identical"
            )
        source_head = self._image_schema_head(source_image)
        target_head = self._image_schema_head(target_image)
        database_head = self._database_revision(target)
        if database_head != source_head or target_head == source_head:
            raise QuiescentCutoverError(
                "restored schema cannot be attributed to the source release"
            )
        self._downgrade_to_target_head(
            source=source,
            target=target,
            source_image=source_image,
            target_head=target_head,
        )
        self._run(installer, timeout=300)

    @staticmethod
    def _database_counts(container: str, database: str) -> dict[str, int]:
        output = SystemBackend._run(
            [
                DOCKER,
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
        counts: dict[str, int] = {}
        for line in output.splitlines():
            name, separator, raw = line.partition("=")
            if not separator or not name or name in counts or not raw.isdigit():
                raise QuiescentCutoverError("database count output was invalid")
            counts[name] = int(raw)
        if not counts:
            raise QuiescentCutoverError("database count output was empty")
        return counts

    @staticmethod
    def _hash_dump(container: str, database: str, mode: str) -> str:
        if mode not in {"schema", "data"}:
            raise ValueError("invalid dump fingerprint mode")
        arguments = [
            DOCKER,
            "exec",
            "-u",
            "postgres",
            container,
            "/usr/bin/pg_dump",
            "-U",
            "postgres",
            "-d",
            database,
            "--no-owner",
            "--no-privileges",
            "--schema-only" if mode == "schema" else "--data-only",
        ]
        if mode == "data":
            arguments.append("--column-inserts")
        try:
            process = subprocess.Popen(
                arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            assert process.stdout is not None
            digest = _dump_content_digest(process.stdout)
            _, stderr = process.communicate(timeout=300)
        except (OSError, subprocess.SubprocessError) as exc:
            raise QuiescentCutoverError("database fingerprint failed") from exc
        if process.returncode != 0:
            raise QuiescentCutoverError(
                f"database fingerprint failed: {_sanitize(stderr.decode(errors='replace'))}"
            )
        return digest

    @classmethod
    def _attest(cls, container: str, database: str) -> DatabaseAttestation:
        counts = cls._database_counts(container, database)
        return DatabaseAttestation(
            counts=counts,
            count_digest=_count_digest(counts),
            schema_digest=cls._hash_dump(container, database, "schema"),
            data_digest=cls._hash_dump(container, database, "data"),
        )

    @staticmethod
    def _write_dump(
        container: str, database: str, path: Path, *, portable: bool
    ) -> None:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as output:
                command = [
                    DOCKER,
                    "exec",
                    "-u",
                    "postgres",
                    container,
                    "/usr/bin/pg_dump",
                    "-U",
                    "postgres",
                    "-d",
                    database,
                    "--format=custom",
                ]
                if portable:
                    command.extend(("--no-owner", "--no-privileges"))
                completed = subprocess.run(
                    command,
                    check=False,
                    stdout=output,
                    stderr=subprocess.PIPE,
                    timeout=300,
                )
                output.flush()
                os.fsync(output.fileno())
            if completed.returncode != 0:
                raise QuiescentCutoverError(
                    f"database snapshot failed: {_sanitize(completed.stderr.decode(errors='replace'))}"
                )
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            os.close(descriptor)

    def snapshot(self, slot: SlotSpec, workspace: Path, label: str) -> DatabaseSnapshot:
        if self._is_running(slot.core) or self._is_running(slot.hindsight):
            raise QuiescentCutoverError("database snapshot requires stopped writers")
        operational_before = self._attest(slot.operational_postgres, "eva2")
        memory_before = self._attest(slot.memory_postgres, "hindsight")
        operational_dump = workspace / f"{label}-operational.dump"
        memory_dump = workspace / f"{label}-memory.dump"
        self._write_dump(
            slot.operational_postgres, "eva2", operational_dump, portable=False
        )
        self._write_dump(slot.memory_postgres, "hindsight", memory_dump, portable=True)
        operational_after = self._attest(slot.operational_postgres, "eva2")
        memory_after = self._attest(slot.memory_postgres, "hindsight")
        if operational_before != operational_after or memory_before != memory_after:
            raise QuiescentCutoverError("quiescent source changed during its snapshot")
        return DatabaseSnapshot(
            operational_dump=operational_dump,
            memory_dump=memory_dump,
            operational_dump_sha256=_sha256_file(operational_dump),
            memory_dump_sha256=_sha256_file(memory_dump),
            operational_dump_bytes=operational_dump.stat().st_size,
            memory_dump_bytes=memory_dump.stat().st_size,
            operational=operational_before,
            memory=memory_before,
        )

    @staticmethod
    def _recreate_database(container: str, database: str) -> None:
        SystemBackend._run(
            [
                DOCKER,
                "exec",
                "-u",
                "postgres",
                container,
                "sh",
                "-ec",
                'dropdb -U postgres --if-exists --force "$1"; createdb -U postgres "$1"',
                "database",
                database,
            ]
        )

    @staticmethod
    def _restore_dump(
        container: str, database: str, path: Path, *, portable: bool
    ) -> None:
        arguments = [] if not portable else ["--no-owner", "--no-privileges"]
        try:
            with path.open("rb") as source:
                completed = subprocess.run(
                    [
                        DOCKER,
                        "exec",
                        "-i",
                        "-u",
                        "postgres",
                        container,
                        "/usr/bin/pg_restore",
                        "--exit-on-error",
                        *arguments,
                        "-U",
                        "postgres",
                        "-d",
                        database,
                    ],
                    check=False,
                    stdin=source,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=300,
                )
        except (OSError, subprocess.SubprocessError) as exc:
            raise QuiescentCutoverError("database restoration failed") from exc
        if completed.returncode != 0:
            raise QuiescentCutoverError(
                f"database restoration failed: {_sanitize(completed.stderr.decode(errors='replace'))}"
            )

    def restore(self, slot: SlotSpec, snapshot: DatabaseSnapshot) -> None:
        if self._is_running(slot.core) or self._is_running(slot.hindsight):
            raise QuiescentCutoverError("database restore requires stopped writers")
        self._recreate_database(slot.operational_postgres, "eva2")
        self._run(
            [
                DOCKER,
                "exec",
                "-u",
                "postgres",
                slot.operational_postgres,
                "/usr/bin/psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "postgres",
                "-d",
                "eva2",
                "-c",
                logical_restore_preparation_sql(),
            ]
        )
        self._restore_dump(
            slot.operational_postgres,
            "eva2",
            snapshot.operational_dump,
            portable=False,
        )
        self._recreate_database(slot.memory_postgres, "hindsight")
        self._restore_dump(
            slot.memory_postgres,
            "hindsight",
            snapshot.memory_dump,
            portable=True,
        )
        operational = self._attest(slot.operational_postgres, "eva2")
        memory = self._attest(slot.memory_postgres, "hindsight")
        differences: list[str] = []
        for name, restored, expected in (
            ("operational", operational, snapshot.operational),
            ("memory", memory, snapshot.memory),
        ):
            if restored.counts != expected.counts:
                differences.append(f"{name}-counts")
            if restored.schema_digest != expected.schema_digest:
                differences.append(f"{name}-schema")
            if restored.data_digest != expected.data_digest:
                differences.append(f"{name}-data")
        if differences:
            raise QuiescentCutoverError(
                "restored database content differs from its source: "
                + ",".join(differences)
            )

    @staticmethod
    def _ready(slot: SlotSpec) -> bool:
        try:
            with request.urlopen(f"{slot.url}/readyz", timeout=5) as response:
                document = json.load(response)
            return response.status == 200 and document.get("ready") is True
        except (OSError, ValueError, error.URLError):
            return False

    def verify_runtime(self, slot: SlotSpec) -> None:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if self._ready(slot):
                break
            time.sleep(1)
        else:
            raise QuiescentCutoverError("restored target did not become ready")
        token = slot.dashboard_token.read_text(encoding="utf-8").strip()
        if len(token) < 32 or any(character.isspace() for character in token):
            raise QuiescentCutoverError("retained target dashboard token is invalid")
        url = f"{slot.url}/internal/v1/dashboard/workboard"
        authenticated = request.Request(
            url, headers={"Authorization": f"Bearer {token}"}, method="GET"
        )
        try:
            with request.urlopen(authenticated, timeout=10) as response:
                if response.status != 200:
                    raise QuiescentCutoverError("retained target rejected its owner")
        except error.URLError as exc:
            raise QuiescentCutoverError(
                "retained target owner route is unavailable"
            ) from exc
        try:
            request.urlopen(request.Request(url, method="GET"), timeout=10)
        except error.HTTPError as exc:
            if exc.code == 401:
                return
            raise QuiescentCutoverError(
                "retained target anonymous status differs"
            ) from exc
        except error.URLError as exc:
            raise QuiescentCutoverError(
                "retained target anonymous route unavailable"
            ) from exc
        raise QuiescentCutoverError("retained target accepted anonymous access")

    def write_route(self, dropin: Path, content: bytes) -> None:
        _atomic_dropin(dropin, content, owner_uid=self.owner_uid)
        self._run([SYSTEMCTL, "daemon-reload"])

    def start_eva1(self, slot: SlotSpec) -> dict[str, int]:
        self._run([SYSTEMCTL, "start", SERVICE], timeout=90)
        environment = self._run(
            [SYSTEMCTL, "show", SERVICE, "--property=Environment", "--value"]
        )
        if f"EVA2_CANARY_URL={slot.url}" not in environment.split():
            raise QuiescentCutoverError("Eva1 did not load the requested retained slot")
        statuses: dict[str, int] = {}
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            statuses = {}
            for path in EVA1_PATHS:
                try:
                    with request.urlopen(
                        f"http://127.0.0.1:3210{path}", timeout=12
                    ) as response:
                        statuses[path] = response.status
                except OSError:
                    statuses[path] = 0
            if statuses == {path: 200 for path in EVA1_PATHS} and self._ready(slot):
                return statuses
            time.sleep(1)
        raise QuiescentCutoverError(
            "Eva1 did not become healthy on the retained target"
        )


def _snapshot_receipt(snapshot: DatabaseSnapshot) -> dict[str, object]:
    return {
        "memory": {
            "countDigest": snapshot.memory.count_digest,
            "dataDigest": snapshot.memory.data_digest,
            "dumpBytes": snapshot.memory_dump_bytes,
            "dumpSha256": snapshot.memory_dump_sha256,
            "schemaDigest": snapshot.memory.schema_digest,
            "tableCount": len(snapshot.memory.counts),
        },
        "operational": {
            "countDigest": snapshot.operational.count_digest,
            "dataDigest": snapshot.operational.data_digest,
            "dumpBytes": snapshot.operational_dump_bytes,
            "dumpSha256": snapshot.operational_dump_sha256,
            "schemaDigest": snapshot.operational.schema_digest,
            "tableCount": len(snapshot.operational.counts),
        },
    }


def quiescent_cutover(
    *,
    source_slot: int,
    target_slot: int,
    backend: CutoverBackend,
    dropin: Path = DROPIN,
    workspace: Path,
) -> dict[str, object]:
    """Synchronize and switch once, restoring fresh target writes on failure."""

    source = _slot(source_slot)
    target = _slot(target_slot)
    if source.number == target.number:
        raise QuiescentCutoverError("source and target slots must differ")
    previous = backend.preflight(source, target, dropin)
    replacement = canonical_dropin(target.url)
    eva1_stop_attempted = False
    source_stop_attempted = False
    target_started = False
    route_write_attempted = False
    eva1_start_attempted = False
    snapshot: DatabaseSnapshot | None = None
    backup: dict[str, object] | None = None
    try:
        eva1_stop_attempted = True
        backend.stop_eva1()
        backup = backend.backup_eva1(source, target)
        source_stop_attempted = True
        backend.stop_writers(source)
        backend.stop_writers(target)
        snapshot = backend.snapshot(source, workspace, "forward")
        backend.restore(target, snapshot)
        backend.migrate_schema(source, target)
        target_started = True
        backend.start_writers(target)
        backend.verify_runtime(target)
        route_write_attempted = True
        backend.write_route(dropin, replacement)
        eva1_start_attempted = True
        statuses = backend.start_eva1(target)
    except Exception as error:
        rollback_error: Exception | None = None
        try:
            if eva1_start_attempted:
                backend.stop_eva1()
            if target_started:
                backend.stop_writers(target)
            if eva1_start_attempted:
                reverse = backend.snapshot(target, workspace, "automatic-rollback")
                backend.restore(source, reverse)
            if source_stop_attempted:
                backend.start_writers(source)
                backend.verify_runtime(source)
            if route_write_attempted:
                backend.write_route(dropin, previous)
            if eva1_stop_attempted:
                backend.start_eva1(source)
        except Exception as exc:  # preserve recovery files for manual repair
            rollback_error = exc
        if rollback_error is not None:
            raise QuiescentCutoverError(
                "quiescent cutover and automatic lossless rollback both failed"
            ) from rollback_error
        if isinstance(error, QuiescentCutoverError):
            raise
        raise QuiescentCutoverError(
            "quiescent cutover failed and was rolled back"
        ) from error
    if snapshot is None or backup is None:
        raise QuiescentCutoverError("quiescent cutover did not produce its evidence")
    return {
        "contract": "Eva2QuiescentCutoverReceipt/v1",
        "eva1Backup": backup,
        "eva1HttpStatuses": statuses,
        "issuedAt": datetime.now(UTC).isoformat(),
        "previousDropinSha256": hashlib.sha256(previous).hexdigest(),
        "sourcePort": source.port,
        "sourceWritersStopped": True,
        "status": "switched",
        "synchronizedDatabases": _snapshot_receipt(snapshot),
        "targetPort": target.port,
        "targetReady": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Losslessly synchronize and switch retained Eva2 managed-B slots"
    )
    parser.add_argument("--source-slot", type=int, required=True, choices=(3, 4))
    parser.add_argument("--target-slot", type=int, required=True, choices=(3, 4))
    parser.add_argument("--dropin", type=Path, default=DROPIN)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    arguments = parser.parse_args(argv)
    LOCK.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    lock_descriptor = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    workspace: Path | None = None
    success = False
    retain_workspace = False
    try:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(
                json.dumps({"error": "another cutover is active", "status": "failed"})
            )
            return 2
        backend = SystemBackend()
        if arguments.preflight_only:
            source = _slot(arguments.source_slot)
            target = _slot(arguments.target_slot)
            previous = backend.preflight(source, target, arguments.dropin)
            receipt = {
                "contract": "Eva2QuiescentCutoverPreflight/v1",
                "currentDropinSha256": hashlib.sha256(previous).hexdigest(),
                "sourcePort": source.port,
                "status": "preflight_passed",
                "targetPort": target.port,
            }
        else:
            WORK_PARENT.mkdir(mode=0o700, parents=True, exist_ok=True)
            workspace = Path(tempfile.mkdtemp(prefix="run-", dir=WORK_PARENT))
            os.chmod(workspace, 0o700)
            receipt = quiescent_cutover(
                source_slot=arguments.source_slot,
                target_slot=arguments.target_slot,
                backend=backend,
                dropin=arguments.dropin,
                workspace=workspace,
            )
        success = True
    except QuiescentCutoverError as exc:
        retain_workspace = "rollback both failed" in str(exc) or (
            "automatic lossless rollback both failed" in str(exc)
        )
        print(json.dumps({"error": str(exc), "status": "failed"}, sort_keys=True))
        return 2
    finally:
        if workspace is not None and (success or not retain_workspace):
            shutil.rmtree(workspace)
        os.close(lock_descriptor)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CutoverBackend",
    "DatabaseAttestation",
    "DatabaseSnapshot",
    "QuiescentCutoverError",
    "SlotSpec",
    "SystemBackend",
    "main",
    "quiescent_cutover",
]
