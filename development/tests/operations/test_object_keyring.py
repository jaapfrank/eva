from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from eva.application.objects import ObjectBinding
from tools.object_keyring import FileObjectKeyResolver, ObjectKeyringError


TENANT = "11111111-1111-4111-8111-111111111111"
INSTALLATION = "22222222-2222-4222-8222-222222222222"
KEY_REF = "33333333-3333-4333-8333-333333333333"


def _binding(**changes: str) -> ObjectBinding:
    values = {
        "tenant_id": TENANT,
        "installation_id": INSTALLATION,
        "object_id": "44444444-4444-4444-8444-444444444444",
        "key_ref": KEY_REF,
        "mime_type": "text/plain",
    }
    values.update(changes)
    return ObjectBinding(**values)


def _keyring(path: Path, *, keys: list[dict[str, str]] | None = None) -> None:
    path.write_text(
        json.dumps(
            {
                "contract": "EvaObjectKeyring/v1",
                "keys": keys
                or [
                    {
                        "tenant_id": TENANT,
                        "installation_id": INSTALLATION,
                        "key_ref": KEY_REF,
                        "key_material_hex": "a" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_resolves_only_the_exact_private_installation_key(private_tmp_path: Path) -> None:
    keyring = private_tmp_path / "object-keys.json"
    _keyring(keyring)

    resolver = FileObjectKeyResolver(keyring, effective_uid=os.geteuid())

    assert resolver(_binding()) == bytes.fromhex("a" * 64)
    with pytest.raises(ObjectKeyringError, match="no exact object key"):
        resolver(_binding(tenant_id="55555555-5555-4555-8555-555555555555"))


@pytest.mark.parametrize(
    ("mode", "error"),
    [(0o640, "unavailable"), (0o604, "unavailable")],
)
def test_rejects_non_private_keyring_file(
    private_tmp_path: Path, mode: int, error: str
) -> None:
    keyring = private_tmp_path / "object-keys.json"
    _keyring(keyring)
    keyring.chmod(mode)

    with pytest.raises(ObjectKeyringError, match=error):
        FileObjectKeyResolver(keyring, effective_uid=os.geteuid())


def test_rejects_duplicate_or_malformed_key_authority(private_tmp_path: Path) -> None:
    keyring = private_tmp_path / "object-keys.json"
    duplicate = {
        "tenant_id": TENANT,
        "installation_id": INSTALLATION,
        "key_ref": KEY_REF,
        "key_material_hex": "b" * 64,
    }
    _keyring(keyring, keys=[duplicate, duplicate])

    with pytest.raises(ObjectKeyringError, match="selectors must be unique"):
        FileObjectKeyResolver(keyring, effective_uid=os.geteuid())

    _keyring(
        keyring,
        keys=[
            {
                "tenant_id": TENANT,
                "installation_id": INSTALLATION,
                "key_ref": KEY_REF,
                "key_material_hex": "not-a-key",
            }
        ],
    )
    with pytest.raises(ObjectKeyringError, match="key material is invalid"):
        FileObjectKeyResolver(keyring, effective_uid=os.geteuid())


def test_rejects_symlink_keyring(private_tmp_path: Path) -> None:
    source = private_tmp_path / "object-keys.json"
    link = private_tmp_path / "linked-keys.json"
    _keyring(source)
    link.symlink_to(source)

    with pytest.raises(ObjectKeyringError, match="unavailable"):
        FileObjectKeyResolver(link, effective_uid=os.geteuid())
