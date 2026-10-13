from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from eva.adapters.postgres.catalog_generated import CATALOG_SPEC
from eva.application.portable_archive import RecordSetBytes, canonical_json_bytes
from tools.portable_export import publish_portable_archive
from tools.portable_transport import (
    PortableTransportError,
    open_portable_transport,
    seal_portable_archive,
)


EVA2_ROOT = Path(__file__).resolve().parents[2]
SCHEDULE_PATH = (
    EVA2_ROOT / "design" / "vertical" / "noor-4d-concurrency-crash-schedules-v1.json"
)
READER_PATH = EVA2_ROOT / "tools" / "read_eva_portable_archive.py"
PASSPHRASE = "correct horse battery staple"


def _empty_record_sets() -> tuple[tuple[RecordSetBytes, ...], str]:
    authority = json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))["portableSnapshotAuthority"]
    snapshot = next(
        item for item in authority["precomputedSnapshots"] if item["snapshot_id"] == "empty"
    )
    facts = {
        item["fact_id"]: item["portable_record"]
        for item in authority["fixtureFactCatalog"]["facts"]
    }
    table_specs = {item["table"]: item for item in CATALOG_SPEC["canonical_tables"]}
    result: list[RecordSetBytes] = []
    for expected in snapshot["record_sets"]:
        portable = table_specs[expected["table"]]["portableExport"]
        records = tuple(facts[fact_id] for fact_id in expected["record_fact_ids"])
        content = b"".join(canonical_json_bytes(record) + b"\n" for record in records)
        assert hashlib.sha256(content).hexdigest() == expected["jsonl_sha256"]
        result.append(
            RecordSetBytes(
                export_ordinal=expected["exportOrdinal"],
                table=expected["table"],
                contract=portable["recordContract"],
                relative_path=portable["recordJsonlRelativePath"],
                record_schema_relative_path=portable["recordSchemaRelativePath"],
                record_schema_sha256=expected["record_schema_sha256"],
                records=records,
                content=content,
            )
        )
    return tuple(result), snapshot["source_installation_id"]


def _validate(path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-I", str(READER_PATH), str(path), "--validate-only"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def _portable_archive(tmp_path: Path) -> Path:
    record_sets, installation_id = _empty_record_sets()
    archive = tmp_path / "portable"
    publish_portable_archive(
        record_sets=record_sets,
        source_installation_id=installation_id,
        export_sequence=1,
        created_at="2026-08-26T12:00:00Z",
        destination=archive,
        object_blob_root=None,
    )
    return archive


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


def test_valid_portable_archive_round_trips_as_authenticated_whole_transport(
    tmp_path: Path,
) -> None:
    source = _portable_archive(tmp_path)
    transport = tmp_path / "portable.eva2pt"

    sealed = seal_portable_archive(
        source, transport, passphrase=PASSPHRASE, validator=_validate
    )
    restored = tmp_path / "restored"
    opened = open_portable_transport(
        transport, restored, passphrase=PASSPHRASE, validator=_validate
    )

    assert transport.is_file()
    assert _tree_digest(restored) == _tree_digest(source)
    assert sealed["plaintext_sha256"] == opened["plaintext_sha256"]
    assert sealed["transport_sha256"] == opened["transport_sha256"]
    _validate(restored)


def test_wrong_passphrase_or_changed_ciphertext_never_publishes_restored_archive(
    tmp_path: Path,
) -> None:
    source = _portable_archive(tmp_path)
    transport = tmp_path / "portable.eva2pt"
    seal_portable_archive(source, transport, passphrase=PASSPHRASE, validator=_validate)

    with pytest.raises(PortableTransportError, match="cannot be authenticated"):
        open_portable_transport(
            transport,
            tmp_path / "wrong-passphrase",
            passphrase="another correct horse battery staple",
            validator=_validate,
        )
    assert not (tmp_path / "wrong-passphrase").exists()

    tampered = tmp_path / "tampered.eva2pt"
    payload = bytearray(transport.read_bytes())
    payload[-1] ^= 1
    tampered.write_bytes(payload)
    with pytest.raises(PortableTransportError, match="cannot be authenticated"):
        open_portable_transport(
            tampered,
            tmp_path / "tampered-restore",
            passphrase=PASSPHRASE,
            validator=_validate,
        )
    assert not (tmp_path / "tampered-restore").exists()


def test_seal_rejects_linked_or_unvalidated_source_members(tmp_path: Path) -> None:
    source = _portable_archive(tmp_path)
    (source / "unsafe-link").symlink_to(source / "archive.json")

    with pytest.raises(PortableTransportError, match="single-link regular files"):
        seal_portable_archive(
            source,
            tmp_path / "should-not-exist.eva2pt",
            passphrase=PASSPHRASE,
            validator=lambda _source: None,
        )
    assert not (tmp_path / "should-not-exist.eva2pt").exists()


def test_transport_never_overwrites_an_existing_output_or_restore_target(
    tmp_path: Path,
) -> None:
    source = _portable_archive(tmp_path)
    transport = tmp_path / "portable.eva2pt"
    seal_portable_archive(source, transport, passphrase=PASSPHRASE, validator=_validate)

    existing_restore = tmp_path / "existing-restore"
    existing_restore.mkdir()
    marker = existing_restore / "must-stay"
    marker.write_text("do not replace", encoding="utf-8")
    with pytest.raises(PortableTransportError, match="already exists"):
        open_portable_transport(
            transport,
            existing_restore,
            passphrase=PASSPHRASE,
            validator=_validate,
        )
    assert marker.read_text(encoding="utf-8") == "do not replace"

    existing_transport = tmp_path / "existing.eva2pt"
    existing_transport.write_bytes(b"do not replace")
    with pytest.raises(PortableTransportError, match="already exists"):
        seal_portable_archive(
            source,
            existing_transport,
            passphrase=PASSPHRASE,
            validator=_validate,
        )
    assert existing_transport.read_bytes() == b"do not replace"


def test_operator_cli_reads_only_owner_private_passphrase_file(tmp_path: Path) -> None:
    source = _portable_archive(tmp_path)
    with tempfile.TemporaryDirectory(prefix="eva2-transport-secret-", dir="/tmp") as raw:
        private = Path(raw)
        passphrase_file = private / "transport-passphrase"
        passphrase_file.write_text(PASSPHRASE + "\n", encoding="utf-8")
        os.chmod(passphrase_file, 0o600)
        transport = tmp_path / "portable.eva2pt"

        sealed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.portable_transport_cli",
                "seal",
                "--source",
                str(source),
                "--destination",
                str(transport),
                "--passphrase-file",
                str(passphrase_file),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert sealed.returncode == 0, sealed.stdout
        assert PASSPHRASE not in sealed.stdout

        restored = tmp_path / "cli-restored"
        opened = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.portable_transport_cli",
                "open",
                "--source",
                str(transport),
                "--destination",
                str(restored),
                "--passphrase-file",
                str(passphrase_file),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert opened.returncode == 0, opened.stdout
        assert _tree_digest(restored) == _tree_digest(source)

        os.chmod(passphrase_file, 0o644)
        rejected = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.portable_transport_cli",
                "seal",
                "--source",
                str(source),
                "--destination",
                str(tmp_path / "rejected.eva2pt"),
                "--passphrase-file",
                str(passphrase_file),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert rejected.returncode == 2
        assert "passphrase file must be an owner-only" in rejected.stdout
        assert PASSPHRASE not in rejected.stdout

        os.chmod(passphrase_file, 0o600)
        linked_passphrase = private / "linked-passphrase"
        linked_passphrase.symlink_to(passphrase_file)
        linked = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.portable_transport_cli",
                "seal",
                "--source",
                str(source),
                "--destination",
                str(tmp_path / "linked-rejected.eva2pt"),
                "--passphrase-file",
                str(linked_passphrase),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert linked.returncode == 2
        assert "passphrase file must be an owner-only" in linked.stdout
