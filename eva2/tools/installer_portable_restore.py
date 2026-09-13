"""Wizard composition at the existing portable operator boundary.

The target engine and fresh private authority reader are supplied at startup,
never by HTTP. A preview token refers to authenticated local staged bytes only.
"""

from __future__ import annotations

import json
from graphlib import TopologicalSorter
from eva.adapters.postgres.catalog import metadata
import secrets
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from time import monotonic
from collections.abc import Callable
from typing import Any

from eva.operations.portable_restore_authority import authorize_portable_restore
from tools.portable_transport import open_portable_transport
from tools.read_eva_portable_archive import validate_and_derive
from tools.postgres_portable_export import CANONICAL_EXPORT_SPECS
from tools.portable_restore import (
    restore_column_preserving_records,
    verify_empty_restore_target,
    validate_portable_restore_records,
)
from tools.portable_object_verifier import verify_portable_object_candidates
from tools.portable_object_materializer import materialize_verified_portable_objects


def _restore_specs():
    """Respect immediate foreign keys; export ordinal is not insert order."""
    specs = {str(spec["table"]): spec for spec in CANONICAL_EXPORT_SPECS}
    dependencies = {
        name: sorted(
            {
                constraint.referred_table.fullname
                for constraint in metadata.tables[name].foreign_key_constraints
                if not constraint.deferrable
                and constraint.referred_table.fullname != name
                and constraint.referred_table.fullname in specs
            }
        )
        for name in specs
    }
    return tuple(specs[name] for name in TopologicalSorter(dependencies).static_order())


class InstallerPortableRestore:
    def __init__(
        self,
        *,
        engine: Any,
        authority_reader: Callable[[], bytes],
        workspace: Path,
        object_store_root: Path,
        key_resolver: Any = None,
    ):
        # The composition root must provide an isolated, traffic-closed target.
        if (
            not workspace.is_dir()
            or workspace.is_symlink()
            or workspace.stat().st_mode & 0o077
        ):
            raise ValueError("restore workspace must be a private directory")
        self.engine = engine
        self.authority_reader = authority_reader
        self.workspace = workspace
        self.object_store_root = object_store_root
        self.key_resolver = key_resolver
        self._lock = Lock()
        self._pending: tuple[TemporaryDirectory, str, float, str] | None = None

    def close(self) -> None:
        with self._lock:
            self._discard()

    def _discard(self) -> None:
        if self._pending:
            self._pending[0].cleanup()
            self._pending = None

    def _read(self, directory: Path) -> tuple[dict, bytes, dict]:
        outputs, proof = validate_and_derive(directory)
        manifest = (directory / "archive.json").read_bytes()
        authorize_portable_restore(
            archive_manifest_raw=manifest, restore_authority_raw=self.authority_reader()
        )
        records = {
            str(spec["table"]): [
                json.loads(line)
                for line in (directory / str(spec["record_jsonl_relative_path"]))
                .read_text()
                .splitlines()
            ]
            for spec in CANONICAL_EXPORT_SPECS
        }
        identity = json.loads(outputs["readback/identity.json"])
        summary = {
            "archive_sha256": proof["archive_json_sha256"],
            "installation": identity["installation_record"]["row"]["installation_id"],
            "records": sum(map(len, records.values())),
            "reauthorization_required": [
                str(
                    row["row"].get(
                        "provider_id", row["row"].get("connector_id", "connector")
                    )
                )
                for row in records["eva_core.connector_bindings"]
            ],
            "notice": "Aanmeldgegevens en installatiesleutels worden niet uit de reservekopie hersteld.",
        }
        objects = records.get("eva_evidence.object_refs", [])
        if objects:
            if self.key_resolver is None:
                raise ValueError("separately provisioned object keyring is required")
            verify_portable_object_candidates(
                object_root=directory / "objects",
                records_by_table=records,
                key_resolver=self.key_resolver,
            )
        return records, manifest, summary

    def prepare(self, archive: bytes, passphrase: str) -> dict[str, object]:
        with self._lock:
            self._discard()
            staging = TemporaryDirectory(prefix="restore-", dir=self.workspace)
            try:
                root = Path(staging.name)
                source = root / "backup.eva"
                source.write_bytes(archive)
                source.chmod(0o600)
                directory = root / "archive"
                open_portable_transport(
                    source,
                    directory,
                    passphrase=passphrase,
                    validator=validate_and_derive,
                )
                records, _, summary = self._read(directory)
                checked_objects = ()
                preview_objects = root / "preview-objects"
                if records.get("eva_evidence.object_refs"):
                    checked_objects = materialize_verified_portable_objects(
                        source_object_root=directory / "objects",
                        target_object_root=preview_objects,
                        records_by_table=records,
                        key_resolver=self.key_resolver,
                    )
                validate_portable_restore_records(
                    records,
                    table_specs=CANONICAL_EXPORT_SPECS,
                    materialized_objects=checked_objects,
                    object_store_root=preview_objects,
                )
                with self.engine.connect().execution_options(
                    isolation_level="SERIALIZABLE"
                ) as connection:
                    with connection.begin():
                        verify_empty_restore_target(
                            connection, table_specs=CANONICAL_EXPORT_SPECS
                        )
                token = secrets.token_urlsafe(32)
                digest = str(summary["archive_sha256"])
                self._pending = (staging, token, monotonic() + 600, digest)
                return {"confirmation": token, "summary": summary}
            except BaseException:
                staging.cleanup()
                raise

    def execute(self, confirmation: str) -> dict[str, object]:
        with self._lock:
            pending = self._pending
            if pending is None or not secrets.compare_digest(confirmation, pending[1]):
                raise ValueError("restore confirmation is invalid")
            try:
                if monotonic() > pending[2]:
                    raise ValueError("restore preview expired")
                directory = Path(pending[0].name) / "archive"
                records, manifest, summary = self._read(directory)
                if summary["archive_sha256"] != pending[3]:
                    raise ValueError("restore preview changed")
                with self.engine.connect().execution_options(
                    isolation_level="SERIALIZABLE"
                ) as connection:
                    with connection.begin():
                        verify_empty_restore_target(
                            connection, table_specs=CANONICAL_EXPORT_SPECS
                        )
                        # Cross-table references are checked together at commit.
                        connection.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
                        materialized = ()
                        if records.get("eva_evidence.object_refs"):
                            materialized = materialize_verified_portable_objects(
                                source_object_root=directory / "objects",
                                target_object_root=self.object_store_root,
                                records_by_table=records,
                                key_resolver=self.key_resolver,
                            )
                        inserted = restore_column_preserving_records(
                            connection,
                            records,
                            archive_manifest_raw=manifest,
                            restore_authority_raw=self.authority_reader(),
                            table_specs=_restore_specs(),
                            materialized_objects=materialized,
                            object_store_root=self.object_store_root,
                        )
                return {"status": "restored", "inserted_records": inserted, **summary}
            finally:
                self._discard()
