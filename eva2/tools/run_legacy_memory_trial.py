#!/usr/bin/env python3
"""Import one explicitly confirmed old-Eva memory slice into a disposable Eva2 DB.

The command is intentionally unsuitable for production: it accepts only an
explicitly named disposable database, a private person-label file, and a private
confirmation file.  It prints hashes and counts only, never old memory content.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from typing import Iterator, Sequence

import sqlalchemy as sa
from sqlalchemy.engine import make_url

from eva.adapters.postgres.memory import PostgresMemoryRepository
from eva.adapters.postgres.scope import set_local_scope
from eva.application.legacy_memory_trial import (
    LegacyMemoryRecord,
    LegacyMemoryTrialError,
    LegacyMemoryTrialSpec,
    build_legacy_memory_trial,
)
from eva.application.memory import MemoryAuthority, RecallCurrentFacts
from eva.application.memory_audience import MemoryAudience
from eva.contracts.canonical_json import canonical_sha256


_CONFIRMATION = "CONFIRM_LEGACY_MEMORY_TRIAL_V1"
_MAX_PRIVATE_FILE_BYTES = 512


def _private_regular_file(path: Path, *, label: str) -> Path:
    descriptor = os.lstat(path)
    if not stat.S_ISREG(descriptor.st_mode) or stat.S_ISLNK(descriptor.st_mode):
        raise LegacyMemoryTrialError(f"{label} must be a regular non-symlink file")
    if descriptor.st_size < 1 or descriptor.st_size > _MAX_PRIVATE_FILE_BYTES:
        raise LegacyMemoryTrialError(f"{label} has an unsafe size")
    if descriptor.st_mode & 0o077:
        raise LegacyMemoryTrialError(f"{label} must not be readable by group or others")
    return path


def _read_private_text(path: Path, *, label: str) -> str:
    _private_regular_file(path, label=label)
    value = path.read_text(encoding="utf-8").strip()
    if not value or "\x00" in value or len(value.encode("utf-8")) > 480:
        raise LegacyMemoryTrialError(f"{label} has invalid text")
    return value


def _source_sha256(path: Path) -> str:
    descriptor = os.lstat(path)
    if not stat.S_ISREG(descriptor.st_mode) or stat.S_ISLNK(descriptor.st_mode):
        raise LegacyMemoryTrialError("legacy SQLite source must be a regular non-symlink file")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _readonly_sqlite_uri(path: Path) -> str:
    """Return a URI that cannot turn a missing source into a new database."""

    return f"{path.resolve().as_uri()}?mode=ro"


@contextmanager
def _private_snapshot_directory() -> Iterator[Path]:
    """Yield a local 0700 directory or refuse to materialize user memories."""

    for base in (Path("/var/tmp"), Path("/tmp")):
        if not base.is_dir():
            continue
        temporary = tempfile.TemporaryDirectory(
            prefix="eva2-legacy-memory-trial-", dir=base
        )
        path = Path(temporary.name)
        try:
            if path.stat().st_mode & 0o077:
                continue
            yield path
            return
        finally:
            temporary.cleanup()
    raise LegacyMemoryTrialError("no private local directory for legacy memory snapshot")


def _copy_readonly_sqlite_snapshot(*, source: Path, destination: Path) -> None:
    """Take one consistent source snapshot without writing the live Eva1 DB.

    SQLite's backup API includes committed WAL state in the copy.  Hashing and
    selecting rows from that private copy avoids the otherwise unavoidable race
    where a live writer changes Eva1 between the source hash and the SELECT.
    """

    _source_sha256(source)  # validates regular/non-symlink source before opening it.
    if destination.exists():
        raise LegacyMemoryTrialError("legacy snapshot destination already exists")
    source_connection = sqlite3.connect(_readonly_sqlite_uri(source), uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        integrity = destination_connection.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",):
            raise LegacyMemoryTrialError("legacy SQLite snapshot integrity check failed")
    except sqlite3.Error as error:
        raise LegacyMemoryTrialError("legacy SQLite snapshot could not be created") from error
    finally:
        destination_connection.close()
        source_connection.close()
    destination.chmod(0o600)
    if destination.stat().st_mode & 0o077:
        raise LegacyMemoryTrialError("legacy SQLite snapshot is not private")


def _active_records(
    *, source: Path, person_label: str, limit: int
) -> tuple[LegacyMemoryRecord, ...]:
    if not 1 <= limit <= 50:
        raise LegacyMemoryTrialError("legacy memory trial limit must be between 1 and 50")
    connection = sqlite3.connect(_readonly_sqlite_uri(source), uri=True)
    try:
        rows = connection.execute(
            """
            SELECT id, ts, COALESCE(person, ''), content, COALESCE(category, '')
            FROM memories
            WHERE COALESCE(archived, 0) = 0
              AND COALESCE(person, '') = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (person_label, limit),
        ).fetchall()
    except sqlite3.Error as error:
        raise LegacyMemoryTrialError("legacy source lacks the expected memories table") from error
    finally:
        connection.close()
    if not rows:
        raise LegacyMemoryTrialError("selected legacy person has no active memories")
    return tuple(
        LegacyMemoryRecord(
            legacy_memory_id=row_id,
            occurred_at=occurred_at,
            person_label_sha256=hashlib.sha256(label.encode("utf-8")).hexdigest(),
            content=content,
            category_sha256=hashlib.sha256(category.encode("utf-8")).hexdigest(),
            archived=False,
        )
        for row_id, occurred_at, label, content, category in rows
    )


def _timestamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _trial_url(value: str) -> str:
    url = make_url(value)
    if url.get_backend_name() != "postgresql" or not (
        url.database and url.database.startswith("eva2_legacy_memory_trial_")
    ):
        raise LegacyMemoryTrialError(
            "trial DSN must point to an eva2_legacy_memory_trial_ PostgreSQL database"
        )
    if url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise LegacyMemoryTrialError("trial DSN must use a local PostgreSQL host")
    return value


def _require_empty_memory_trial_target(
    connection: sa.Connection, spec: LegacyMemoryTrialSpec
) -> None:
    """Refuse to merge a selected old-Eva slice into an existing memory trial.

    The destination has a deliberately disposable database name, but that name
    alone does not prove it is unused.  A second selection must use a fresh
    target instead of silently mixing two old-Eva labels, source snapshots or
    review decisions in one Eva2 memory authority.
    """

    for table in (
        "eva_memory.source_events",
        "eva_memory.fact_assertions",
        "eva_memory.current_facts",
    ):
        count = connection.execute(
            sa.text(
                f"SELECT COUNT(*) FROM {table} "
                "WHERE tenant_id = :tenant_id AND installation_id = :installation_id"
            ),
            {
                "tenant_id": spec.tenant_id,
                "installation_id": spec.installation_id,
            },
        ).scalar_one()
        if count != 0:
            raise LegacyMemoryTrialError(
                "legacy memory trial target already contains memory authority data"
            )


def run_trial(args: argparse.Namespace) -> dict[str, object]:
    """Prepare or write one explicitly selected, review-only memory slice.

    ``--plan-only`` deliberately stops before PostgreSQL.  It permits the
    owner to inspect the count and stable command hash before authorizing the
    disposable-database write.  It never prints legacy memory content.
    """

    source = Path(args.source_sqlite)
    if not source.is_absolute():
        source = Path.cwd() / source
    source = source.absolute()
    if not source.exists():
        raise LegacyMemoryTrialError("legacy SQLite source does not exist")
    person_label = _read_private_text(Path(args.person_label_file), label="person label file")
    if _read_private_text(Path(args.confirmation_file), label="confirmation file") != _CONFIRMATION:
        raise LegacyMemoryTrialError("confirmation file does not authorize a legacy trial")
    # The temporary directory is mode 0700. It is removed on every normal or
    # exceptional exit and contains the only short-lived plaintext copy.
    with _private_snapshot_directory() as temporary:
        snapshot = temporary / "legacy-eva1-snapshot.sqlite"
        _copy_readonly_sqlite_snapshot(source=source, destination=snapshot)
        source_sha = _source_sha256(snapshot)
        records = _active_records(
            source=snapshot, person_label=person_label, limit=args.limit
        )
    now = datetime.now(timezone.utc).replace(microsecond=0)
    imported_at = _timestamp(now)
    retention_until = _timestamp(now + timedelta(days=args.retention_days))
    spec = LegacyMemoryTrialSpec(
        tenant_id=args.tenant_id,
        installation_id=args.installation_id,
        target_principal_id=args.target_principal_id,
        provenance_evidence_id=args.provenance_evidence_id,
        legacy_source_sha256=source_sha,
        selected_person_label_sha256=hashlib.sha256(person_label.encode("utf-8")).hexdigest(),
        imported_at=imported_at,
        retention_until=retention_until,
    )
    commands = build_legacy_memory_trial(records, spec)
    plan_only = bool(getattr(args, "plan_only", False))
    ordinary_recall_count: int | None = None
    if not plan_only:
        trial_dsn = getattr(args, "trial_dsn", None)
        if not isinstance(trial_dsn, str) or not trial_dsn:
            raise LegacyMemoryTrialError("trial DSN is required unless --plan-only is used")
        dsn = _trial_url(trial_dsn)
        engine = sa.create_engine(dsn, pool_pre_ping=False)
        try:
            with engine.begin() as connection:
                connection.execute(sa.text("SET CONSTRAINTS ALL DEFERRED"))
                connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
                set_local_scope(
                    connection,
                    tenant_id=spec.tenant_id,
                    installation_id=spec.installation_id,
                )
                _require_empty_memory_trial_target(connection, spec)
                memory = MemoryAuthority(
                    PostgresMemoryRepository(
                        connection, spec.tenant_id, spec.installation_id
                    )
                )
                for command in commands:
                    memory.remember(command)
                recalled = memory.recall_for_presence(
                    RecallCurrentFacts(
                        recall_id="legacy:memory:trial:negative-recall",
                        installation_id=spec.installation_id,
                        tenant_id=spec.tenant_id,
                        principal_id=spec.target_principal_id,
                        audience=MemoryAudience.private_person(
                            principal_id=spec.target_principal_id
                        ),
                        conversation_id="legacy:memory:trial",
                        project_id=None,
                        query_purpose="reply",
                        requested_valid_at=imported_at,
                        scope=commands[0].scope,
                        subject_ids=(spec.target_principal_id,),
                        query_text="legacy memory trial",
                    )
                )
                ordinary_recall_count = len(recalled.confirmed_facts)
                if ordinary_recall_count:
                    raise LegacyMemoryTrialError(
                        "restricted legacy trial entered normal recall"
                    )
        finally:
            engine.dispose()
    return {
        "contract": "Eva2LegacyMemoryTrialReceipt/v1",
        "mode": "plan_only" if plan_only else "written_to_disposable_database",
        "source_sha256": source_sha,
        "selected_person_label_sha256": spec.selected_person_label_sha256,
        "selected_count": len(commands),
        "imported_count": 0 if plan_only else len(commands),
        "commands_sha256": canonical_sha256(
            [command.fact.model_dump(mode="json") for command in commands]
        ),
        "ordinary_recall_count": ordinary_recall_count,
        "trust": "legacy_unverified",
        "privacy_class": "restricted",
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sqlite", required=True, type=Path)
    parser.add_argument("--person-label-file", required=True, type=Path)
    parser.add_argument("--confirmation-file", required=True, type=Path)
    parser.add_argument("--trial-dsn")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--installation-id", required=True)
    parser.add_argument("--target-principal-id", required=True)
    parser.add_argument("--provenance-evidence-id", required=True)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--retention-days", type=int, default=7)
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="prepare and hash the selected slice without opening PostgreSQL",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.retention_days <= 30:
        parser.error("--retention-days must be between 1 and 30")
    try:
        receipt = run_trial(args)
    except (LegacyMemoryTrialError, OSError, UnicodeError) as error:
        parser.error(str(error))
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
