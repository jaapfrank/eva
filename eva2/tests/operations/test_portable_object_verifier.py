from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from eva.operations.object_keyring import FileObjectKeyResolver
from tools.portable_object_verifier import (
    PortableObjectVerificationError,
    verify_portable_object_bytes,
)


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "design/vertical/noor-4d-execution-profile-v1.json"
SCHEDULE = ROOT / "design/vertical/noor-4d-concurrency-crash-schedules-v1.json"


def _fixture() -> tuple[dict[str, object], dict[str, object]]:
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    schedule = json.loads(SCHEDULE.read_text(encoding="utf-8"))
    object_record = next(
        item["portable_record"]
        for item in schedule["portableSnapshotAuthority"]["fixtureFactCatalog"]["facts"]
        if item["fact_id"] == "correction/object/document"
    )
    key = profile["encryptedObjectFormat"]["keyBinding"]["fourDFixtureKeyRecords"][0]
    return object_record, key


def _keyring(path: Path, key: dict[str, object], *, material: str | None = None) -> None:
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


def test_preflight_authenticates_the_exact_archived_object(private_tmp_path: Path) -> None:
    record, key = _fixture()
    selected = json.loads(SCHEDULE.read_text(encoding="utf-8"))["portableSnapshotAuthority"][
        "selectedEncryptedObjects"
    ]["objects"][0]
    root = private_tmp_path / "objects"
    digest = selected["ciphertext_sha256"]
    path = root / "sha256" / digest[:2] / f"{digest}.eva2xo"
    path.parent.mkdir(parents=True)
    path.write_bytes(base64.b64decode(selected["complete_envelope_base64"], validate=True))
    keyring = private_tmp_path / "object-keys.json"
    _keyring(keyring, key)

    verified = verify_portable_object_bytes(
        object_root=root,
        records_by_table={"eva_evidence.object_refs": [record]},
        key_resolver=FileObjectKeyResolver(keyring),
    )

    assert len(verified) == 1
    assert verified[0].ciphertext_sha256 == digest
    assert verified[0].plaintext_byte_length == 122


def test_preflight_rejects_a_key_that_cannot_authenticate_the_archive(
    private_tmp_path: Path,
) -> None:
    record, key = _fixture()
    selected = json.loads(SCHEDULE.read_text(encoding="utf-8"))["portableSnapshotAuthority"][
        "selectedEncryptedObjects"
    ]["objects"][0]
    root = private_tmp_path / "objects"
    digest = selected["ciphertext_sha256"]
    path = root / "sha256" / digest[:2] / f"{digest}.eva2xo"
    path.parent.mkdir(parents=True)
    path.write_bytes(base64.b64decode(selected["complete_envelope_base64"], validate=True))
    keyring = private_tmp_path / "object-keys.json"
    _keyring(keyring, key, material="0" * 64)

    with pytest.raises(PortableObjectVerificationError, match="cannot be authenticated"):
        verify_portable_object_bytes(
            object_root=root,
            records_by_table={"eva_evidence.object_refs": [record]},
            key_resolver=FileObjectKeyResolver(keyring),
        )
