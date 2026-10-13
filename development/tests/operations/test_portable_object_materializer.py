from __future__ import annotations

import base64
import json
import stat
from pathlib import Path

import pytest

from tools.cas import FileSystemCAS
from tools.object_keyring import FileObjectKeyResolver
from tools.portable_object_materializer import (
    PortableObjectMaterializationError,
    _append_journal,
    materialize_verified_portable_objects,
)
from tools.portable_object_verifier import verify_portable_object_candidates


ROOT = Path(__file__).resolve().parents[3]
PROFILE = ROOT / "development/design/vertical/noor-4d-execution-profile-v1.json"
SCHEDULE = ROOT / "development/design/vertical/noor-4d-concurrency-crash-schedules-v1.json"


def _fixture() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    schedule = json.loads(SCHEDULE.read_text(encoding="utf-8"))
    record = next(
        item["portable_record"]
        for item in schedule["portableSnapshotAuthority"]["fixtureFactCatalog"]["facts"]
        if item["fact_id"] == "correction/object/document"
    )
    selected = schedule["portableSnapshotAuthority"]["selectedEncryptedObjects"]["objects"][0]
    return record, profile["encryptedObjectFormat"]["keyBinding"]["fourDFixtureKeyRecords"][0], selected


def _keyring(path: Path, key: dict[str, object], *, material: str | None = None) -> FileObjectKeyResolver:
    path.write_text(
        json.dumps(
            {
                "contract": "EvaObjectKeyring/v1",
                "keys": [
                    {
                        "tenant_id": key["tenantId"],
                        "installation_id": key["installationId"],
                        "key_ref": key["keyRef"],
                        "key_material_hex": material or key["keyMaterial"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return FileObjectKeyResolver(path)


def _source(private_tmp_path: Path, selected: dict[str, object]) -> Path:
    root = private_tmp_path / "source-objects"
    digest = selected["ciphertext_sha256"]
    assert isinstance(digest, str)
    target = root / "sha256" / digest[:2] / f"{digest}.eva2xo"
    target.parent.mkdir(parents=True)
    target.write_bytes(base64.b64decode(selected["complete_envelope_base64"], validate=True))
    return root


def test_materializes_an_authenticated_archive_byte_once_then_is_idempotent(
    private_tmp_path: Path,
) -> None:
    record, key, selected = _fixture()
    resolver = _keyring(private_tmp_path / "keys.json", key)
    source = _source(private_tmp_path, selected)
    target = private_tmp_path / "target-objects"

    created = materialize_verified_portable_objects(
        source_object_root=source,
        target_object_root=target,
        records_by_table={"eva_evidence.object_refs": [record]},
        key_resolver=resolver,
    )
    repeated = materialize_verified_portable_objects(
        source_object_root=source,
        target_object_root=target,
        records_by_table={"eva_evidence.object_refs": [record]},
        key_resolver=resolver,
    )

    digest = selected["ciphertext_sha256"]
    assert isinstance(digest, str)
    assert created[0].status == "created"
    assert repeated[0].status == "already_same"
    assert (target / created[0].relative_path).read_bytes() == (source / created[0].relative_path).read_bytes()
    journal = target / ".eva-portable-object-materialization-v1.jsonl"
    assert stat.S_IMODE(journal.stat().st_mode) == 0o600
    assert len(journal.read_text(encoding="utf-8").splitlines()) == 1


def test_restarts_from_the_fsynced_journaled_temp_instead_of_making_another_copy(
    private_tmp_path: Path,
) -> None:
    record, key, selected = _fixture()
    resolver = _keyring(private_tmp_path / "keys.json", key)
    source = _source(private_tmp_path, selected)
    target = private_tmp_path / "target-objects"
    target.mkdir(mode=0o700)
    candidate = verify_portable_object_candidates(
        object_root=source,
        records_by_table={"eva_evidence.object_refs": [record]},
        key_resolver=resolver,
    )[0]
    prepared = FileSystemCAS(target).prepare(candidate.candidate)
    _append_journal(
        target / ".eva-portable-object-materialization-v1.jsonl",
        candidate,
        prepared.binding,
    )
    del prepared

    result = materialize_verified_portable_objects(
        source_object_root=source,
        target_object_root=target,
        records_by_table={"eva_evidence.object_refs": [record]},
        key_resolver=resolver,
    )

    assert result[0].status == "created"
    assert list(target.rglob(".eva-candidate-*")) == []
    assert len(list(target.rglob("*.eva2xo"))) == 1


def test_wrong_target_key_never_creates_a_target_directory(private_tmp_path: Path) -> None:
    record, key, selected = _fixture()
    resolver = _keyring(private_tmp_path / "keys.json", key, material="0" * 64)
    source = _source(private_tmp_path, selected)
    target = private_tmp_path / "target-objects"

    with pytest.raises(PortableObjectMaterializationError, match="source portable object"):
        materialize_verified_portable_objects(
            source_object_root=source,
            target_object_root=target,
            records_by_table={"eva_evidence.object_refs": [record]},
            key_resolver=resolver,
        )

    assert not target.exists()
