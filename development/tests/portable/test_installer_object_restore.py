import base64
import hashlib
import json

import pytest
from sqlalchemy import text
from tools.portable_transport import seal_portable_archive
from tools.read_eva_portable_archive import validate_and_derive
from tools.portable_object_verifier import verify_portable_object_candidates
from tests.portable.test_installer_portable_restore import setup, count
from tests.portable.installer_object_fixture import archive_with_object
from tests.portable.test_portable_restore_object_boundary import (
    _current_object_record,
    _keyring,
)
from tests.portable.restore_authority_fixture import restore_authority_for_archive
from tests.portable.test_portable_transport import PASSPHRASE
from tests.api.test_installer_restore import client, login


def prepare_archive(setup):
    w, _, engine, authority, old = setup
    archive = archive_with_object(old.parent)
    authority[0] = restore_authority_for_archive(
        (archive / "archive.json").read_bytes(), generation=0
    )
    _, _, key = _current_object_record()
    w.key_resolver = _keyring(old.parent / "object-keys.json", key)
    sealed = old.parent / "objects.eva"
    seal_portable_archive(
        archive, sealed, passphrase=PASSPHRASE, validator=validate_and_derive
    )
    c, b = client(w)
    assert login(c, b).status_code == 204
    response = c.post(
        "/internal/v1/onboarding/restore/prepare",
        json={
            "archive": base64.b64encode(sealed.read_bytes()).decode(),
            "passphrase": PASSPHRASE,
        },
    )
    assert response.status_code == 200, response.text
    return c, response.json()["confirmation"], archive


def test_real_object_archive_restored_through_wizard(setup):
    w, _, engine, authority, _ = setup
    c, token, archive = prepare_archive(setup)
    assert count(engine) == 0 and not w.object_store_root.exists()
    response = c.post(
        "/internal/v1/onboarding/restore/execute", json={"confirmation": token}
    )
    assert response.status_code == 200, response.text
    records, _, _ = w._read(archive)
    candidates = verify_portable_object_candidates(
        object_root=w.object_store_root,
        records_by_table=records,
        key_resolver=w.key_resolver,
    )
    assert len(candidates) == 1
    record, selected, _ = _current_object_record()
    digest = selected["ciphertext_sha256"]
    actual = w.object_store_root / "sha256" / digest[:2] / (digest + ".eva2xo")
    assert actual.read_bytes() == base64.b64decode(selected["complete_envelope_base64"])
    from eva.adapters.objects.envelope import decrypt_object, inspect_envelope

    envelope = actual.read_bytes()
    plaintext = decrypt_object(
        envelope, inspect_envelope(envelope).binding, key_resolver=w.key_resolver
    )
    assert hashlib.sha256(plaintext).hexdigest() == record["row"]["plaintext_sha256"]
    assert len(plaintext) == record["row"]["plaintext_byte_length"]
    with engine.connect() as connection:
        restored = connection.execute(
            text(
                "SELECT encode(ciphertext_sha256,'hex'), encode(plaintext_sha256,'hex'), state FROM eva_evidence.object_refs"
            )
        ).one()
        assert restored == (digest, record["row"]["plaintext_sha256"], "active")
        assert set(
            connection.execute(
                text("SELECT status FROM eva_core.connector_bindings")
            ).scalars()
        ) == {"reauthorization_required"}
    assert response.json()["reauthorization_required"]


def test_erasure_after_object_preview_prevents_restoration(setup):
    w, _, engine, authority, _ = setup
    c, token, archive = prepare_archive(setup)
    authority[0] = restore_authority_for_archive(
        (archive / "archive.json").read_bytes(), admit=False, generation=1
    )
    response = c.post(
        "/internal/v1/onboarding/restore/execute", json={"confirmation": token}
    )
    assert response.status_code == 409
    assert count(engine) == 0 and not w.object_store_root.exists()
    assert not list(w.workspace.iterdir())


def test_revocation_after_materialization_keeps_only_private_authenticated_cas(setup):
    w, _, engine, authority, _ = setup
    c, token, archive = prepare_archive(setup)
    admitted = authority[0]
    revoked = restore_authority_for_archive(
        (archive / "archive.json").read_bytes(), admit=False, generation=1
    )
    calls = []

    def fresh():
        calls.append(1)
        return admitted if len(calls) == 1 else revoked

    w.authority_reader = fresh
    response = c.post(
        "/internal/v1/onboarding/restore/execute", json={"confirmation": token}
    )
    assert response.status_code == 409
    assert len(calls) == 2 and count(engine) == 0
    files = list(w.object_store_root.rglob("*.eva2xo"))
    assert len(files) == 1
    assert w.object_store_root.stat().st_mode & 0o077 == 0
    before = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in w.object_store_root.rglob("*")
        if p.is_file()
    }
    assert all(
        p.stat().st_mode & 0o077 == 0
        for p in w.object_store_root.rglob("*")
        if p.is_file()
    )
    assert not list(w.workspace.iterdir())
    assert (
        c.post(
            "/internal/v1/onboarding/restore/execute", json={"confirmation": token}
        ).status_code
        == 409
    )
    assert before == {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in w.object_store_root.rglob("*")
        if p.is_file()
    }
