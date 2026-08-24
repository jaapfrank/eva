from __future__ import annotations

import json
from pathlib import Path

import pytest

from eva.operations.model_credentials import (
    ModelCredentialLeaseError,
    build_access_lease,
    build_codex_consumer_auth,
    install_auth_owner,
    publish_access_lease,
    publish_codex_consumer_auth,
    publish_unavailable_lease,
)


def _source(path: Path, access: str = "access-one") -> None:
    path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": access,
                    "account_id": "account-one",
                    "refresh_token": "must-never-leave-owner",
                },
            }
        )
    )


def test_access_lease_excludes_refresh_authority(tmp_path: Path) -> None:
    source = tmp_path / "owner" / "auth.json"
    source.parent.mkdir()
    _source(source)

    lease = json.loads(build_access_lease(source))

    assert lease["contract"] == "EvaModelAccessLease/v1"
    assert lease["access_token"] == "access-one"
    assert lease["account_id"] == "account-one"
    assert "refresh_token" not in lease
    assert "must-never-leave-owner" not in json.dumps(lease)

    consumer = json.loads(build_codex_consumer_auth(source))
    assert consumer == {
        "auth_mode": "chatgpt",
        "tokens": {"access_token": "access-one", "account_id": "account-one"},
    }
    assert "refresh_token" not in json.dumps(consumer)


def test_access_lease_follows_atomic_owner_rotation(tmp_path: Path) -> None:
    source = tmp_path / "owner" / "auth.json"
    target = tmp_path / "consumer" / "auth.json"
    source.parent.mkdir()
    _source(source)

    assert publish_access_lease(source, target) is True
    first = target.stat().st_ino
    assert publish_access_lease(source, target) is False

    replacement = source.with_suffix(".new")
    _source(replacement, "access-two")
    replacement.replace(source)
    assert publish_access_lease(source, target) is True

    assert target.stat().st_ino != first
    assert json.loads(target.read_bytes())["access_token"] == "access-two"


def test_codex_consumer_follows_rotation_without_refresh_token(tmp_path: Path) -> None:
    source = tmp_path / "owner" / "auth.json"
    target = tmp_path / "hindsight" / "auth.json"
    source.parent.mkdir()
    _source(source)

    assert publish_codex_consumer_auth(source, target) is True
    _source(source, "access-two")
    assert publish_codex_consumer_auth(source, target) is True

    consumer = json.loads(target.read_bytes())
    assert consumer["tokens"]["access_token"] == "access-two"
    assert "refresh_token" not in json.dumps(consumer)
    assert target.stat().st_mode & 0o777 == 0o600


def test_unavailable_owner_replaces_a_stale_access_token(tmp_path: Path) -> None:
    target = tmp_path / "consumer" / "auth.json"
    target.parent.mkdir()
    target.write_text('{"access_token":"stale-secret"}')

    assert publish_unavailable_lease(target) is True

    value = json.loads(target.read_bytes())
    assert value == {
        "contract": "EvaModelAccessLeaseUnavailable/v1",
        "state": "source_unavailable",
    }
    assert "stale-secret" not in target.read_text()


def test_staged_login_replaces_live_owner_only_after_validation(tmp_path: Path) -> None:
    staged = tmp_path / "staged" / "auth.json"
    owner = tmp_path / "owner" / "auth.json"
    staged.parent.mkdir()
    owner.parent.mkdir()
    owner.write_text("old-invalid-owner")
    _source(staged, "fresh-access")

    assert install_auth_owner(staged, owner) is True

    assert owner.read_bytes() == staged.read_bytes()
    assert json.loads(owner.read_bytes())["tokens"]["refresh_token"] == (
        "must-never-leave-owner"
    )


def test_invalid_staged_login_cannot_replace_live_owner(tmp_path: Path) -> None:
    staged = tmp_path / "staged.json"
    owner = tmp_path / "owner.json"
    staged.write_text('{"tokens":{"access_token":"incomplete"}}')
    owner.write_text("current-owner")

    with pytest.raises(ModelCredentialLeaseError):
        install_auth_owner(staged, owner)

    assert owner.read_text() == "current-owner"
