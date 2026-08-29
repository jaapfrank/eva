from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from eva.adapters.postgres.catalog_generated import CATALOG_SPEC
from eva.application.portable_archive import (
    PortableArchiveError,
    RecordSetBytes,
    canonical_json_bytes,
)
from tools.portable_export import publish_portable_archive


EVA2_ROOT = Path(__file__).resolve().parents[2]
SCHEDULE_PATH = (
    EVA2_ROOT / "design" / "vertical" / "noor-4d-concurrency-crash-schedules-v1.json"
)
READER_PATH = EVA2_ROOT / "tools" / "read_eva_portable_archive.py"
SNAPSHOT_IDS = tuple(
    item["snapshot_id"]
    for item in json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))[
        "portableSnapshotAuthority"
    ]["precomputedSnapshots"]
)


def _fixture_record_sets(
    snapshot_id: str,
) -> tuple[tuple[RecordSetBytes, ...], str, dict[str, object]]:
    authority = json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))[
        "portableSnapshotAuthority"
    ]
    snapshot = next(
        item
        for item in authority["precomputedSnapshots"]
        if item["snapshot_id"] == snapshot_id
    )
    facts = {
        item["fact_id"]: item["portable_record"]
        for item in authority["fixtureFactCatalog"]["facts"]
    }
    table_specs = {item["table"]: item for item in CATALOG_SPEC["canonical_tables"]}
    result: list[RecordSetBytes] = []
    for expected in snapshot["record_sets"]:
        spec = table_specs[expected["table"]]
        portable = spec["portableExport"]
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
    return tuple(result), snapshot["source_installation_id"], authority


@pytest.mark.parametrize("snapshot_id", SNAPSHOT_IDS)
def test_each_of_31_sealed_snapshots_is_a_valid_portable_archive(
    tmp_path: Path, snapshot_id: str
) -> None:
    record_sets, installation_id, authority = _fixture_record_sets(snapshot_id)
    snapshot = next(
        item
        for item in authority["precomputedSnapshots"]
        if item["snapshot_id"] == snapshot_id
    )
    object_root = None
    if snapshot["encrypted_object_count"]:
        selected = authority["selectedEncryptedObjects"]["objects"][0]
        digest = selected["ciphertext_sha256"]
        object_root = tmp_path / "cas"
        target = object_root / "sha256" / digest[:2] / f"{digest}.eva2xo"
        target.parent.mkdir(parents=True)
        target.write_bytes(
            base64.b64decode(selected["complete_envelope_base64"], validate=True)
        )
    archive = tmp_path / "archive"

    publish_portable_archive(
        record_sets=record_sets,
        source_installation_id=installation_id,
        export_sequence=1,
        created_at="2026-08-20T12:00:00Z",
        destination=archive,
        object_blob_root=object_root,
    )

    assert (
        hashlib.sha256(
            (archive / "objects" / "manifest-v1.jsonl").read_bytes()
        ).hexdigest()
        == snapshot["object_manifest_jsonl_sha256"]
    )


def test_sealed_empty_snapshot_round_trips_through_standalone_reader(
    tmp_path: Path,
) -> None:
    record_sets, installation_id, _ = _fixture_record_sets("empty")
    archive = tmp_path / "portable-archive"
    receipt = publish_portable_archive(
        record_sets=record_sets,
        source_installation_id=installation_id,
        export_sequence=1,
        created_at="2026-08-20T12:00:00Z",
        destination=archive,
        object_blob_root=None,
    )

    assert archive.is_dir()
    assert len(receipt["archive_id"]) == 36
    readback = tmp_path / "readback"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(READER_PATH),
            str(archive),
            "--output",
            str(readback),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert sorted(path.name for path in (readback / "readback").iterdir()) == [
        "conversations.jsonl",
        "effects.jsonl",
        "episodes.jsonl",
        "evidence.jsonl",
        "identity.json",
        "memories.jsonl",
        "object-manifest.jsonl",
        "pending-work.jsonl",
        "summary.json",
    ]


def test_failed_full_validation_never_publishes_archive(tmp_path: Path) -> None:
    record_sets, installation_id, _ = _fixture_record_sets("empty")
    archive = tmp_path / "portable-archive"

    def reject(_staging: Path) -> None:
        raise PortableArchiveError("deliberate full-validation failure")

    with pytest.raises(PortableArchiveError, match="deliberate"):
        publish_portable_archive(
            record_sets=record_sets,
            source_installation_id=installation_id,
            export_sequence=1,
            created_at="2026-08-20T12:00:00Z",
            destination=archive,
            object_blob_root=None,
            validator=reject,
        )

    assert not archive.exists()
    assert not list(tmp_path.glob(".portable-archive.staging-*"))


def test_sealed_terminal_snapshot_verifies_selected_encrypted_object(
    tmp_path: Path,
) -> None:
    record_sets, installation_id, authority = _fixture_record_sets("terminal")
    selected = authority["selectedEncryptedObjects"]["objects"][0]  # type: ignore[index]
    digest = selected["ciphertext_sha256"]
    cas_root = tmp_path / "cas"
    cas_path = cas_root / "sha256" / digest[:2] / f"{digest}.eva2xo"
    cas_path.parent.mkdir(parents=True)
    cas_path.write_bytes(
        base64.b64decode(selected["complete_envelope_base64"], validate=True)
    )
    archive = tmp_path / "portable-terminal"

    producer_receipt = publish_portable_archive(
        record_sets=record_sets,
        source_installation_id=installation_id,
        export_sequence=2,
        created_at="2026-08-20T12:00:00Z",
        destination=archive,
        object_blob_root=cas_root,
    )

    expected_manifest = b"".join(
        canonical_json_bytes(record) + b"\n"
        for record in next(
            item
            for item in authority["precomputedSnapshots"]  # type: ignore[index]
            if item["snapshot_id"] == "terminal"
        )["object_manifest_records"]
    )
    assert (archive / "objects" / "manifest-v1.jsonl").read_bytes() == expected_manifest
    assert (
        archive / "objects" / "sha256" / digest[:2] / f"{digest}.eva2xo"
    ).read_bytes() == cas_path.read_bytes()

    readback = tmp_path / "terminal-readback"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(READER_PATH),
            str(archive),
            "--output",
            str(readback),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    reader_receipt = json.loads(result.stdout)
    assert result.stdout == canonical_json_bytes(reader_receipt) + b"\n"
    assert (
        reader_receipt["archive_json_sha256"] == producer_receipt["archive_json_sha256"]
    )
    assert reader_receipt["checksums_sha256"] == producer_receipt["checksums_sha256"]
    assert reader_receipt["validation_status"] == "valid"

    summary_raw = (readback / "readback" / "summary.json").read_bytes()
    summary = json.loads(summary_raw)
    terminal = next(
        item
        for item in authority["precomputedSnapshots"]  # type: ignore[index]
        if item["snapshot_id"] == "terminal"
    )
    assert (
        reader_receipt["readback_summary_sha256"]
        == hashlib.sha256(summary_raw).hexdigest()
    )
    assert summary["canonical_record_count"] == sum(terminal["count_vector"][:39])
    assert [item["record_count"] for item in summary["record_sets"]] == terminal[
        "count_vector"
    ][:39]
    assert summary["encrypted_object_count"] == terminal["encrypted_object_count"]
    assert (
        summary["object_manifest_jsonl_sha256"]
        == terminal["object_manifest_jsonl_sha256"]
    )
    assert {
        item["relative_path"]: item["record_count"]
        for item in summary["output_receipts"]
    } == {
        "readback/identity.json": 1,
        "readback/conversations.jsonl": 1,
        "readback/episodes.jsonl": 1,
        "readback/pending-work.jsonl": 4,
        "readback/effects.jsonl": 11,
        "readback/memories.jsonl": 5,
        "readback/evidence.jsonl": 20,
        "readback/object-manifest.jsonl": 1,
    }
    assert (
        readback / "readback" / "object-manifest.jsonl"
    ).read_bytes() == expected_manifest
