from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import stat

import pytest

import eva.operations.model_credentials as credentials
from eva.operations.model_credentials import (
    ModelCredentialLeaseError,
    build_access_lease,
    build_codex_consumer_auth,
    check_codex_consumer_auth,
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
    path.chmod(0o600)


@pytest.fixture(autouse=True)
def local_model_lease_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials, "MODEL_LEASE_OWNER_UID", os.geteuid())
    monkeypatch.setattr(credentials, "EVA_CORE_LEASE_READER_GID", os.getegid())
    monkeypatch.setattr(credentials, "CODEX_LEASE_READER_GID", os.getegid())


def _lease_directory(path: Path, mode: int) -> None:
    path.mkdir(mode=mode)
    path.chmod(mode)


def test_access_lease_excludes_refresh_authority(private_tmp_path: Path) -> None:
    source = private_tmp_path / "owner" / "auth.json"
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


def test_access_lease_accepts_eva1_flat_oauth_authority(
    private_tmp_path: Path,
) -> None:
    source = private_tmp_path / "owner" / "auth.json"
    source.parent.mkdir()
    source.write_text(
        json.dumps(
            {
                "access_token": "access-from-eva1",
                "account_id": "account-one",
                "refresh_token": "must-never-leave-owner",
                "expires_at": 123,
            }
        )
    )
    source.chmod(0o600)

    lease = json.loads(build_access_lease(source))

    assert lease["access_token"] == "access-from-eva1"
    assert lease["account_id"] == "account-one"
    assert "refresh_token" not in lease


def test_access_lease_follows_atomic_owner_rotation(private_tmp_path: Path) -> None:
    source = private_tmp_path / "owner" / "auth.json"
    target = private_tmp_path / "consumer" / "auth.json"
    source.parent.mkdir()
    _lease_directory(target.parent, 0o2750)
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


def test_codex_consumer_follows_rotation_without_refresh_token(
    private_tmp_path: Path,
) -> None:
    source = private_tmp_path / "owner" / "auth.json"
    target = private_tmp_path / "hindsight" / "auth.json"
    source.parent.mkdir()
    _lease_directory(target.parent, 0o700)
    _source(source)

    assert publish_codex_consumer_auth(source, target) is True
    lock = target.with_suffix(".lock")
    first_lock_inode = lock.stat().st_ino
    first_auth_inode = target.stat().st_ino
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600
    assert lock.stat().st_nlink == 1 and lock.read_bytes() == b""
    with lock.open("a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert publish_codex_consumer_auth(source, target) is False
        replacement = source.with_suffix(".new")
        _source(replacement, "access-two")
        replacement.replace(source)
        assert publish_codex_consumer_auth(source, target) is True
        assert lock.stat().st_ino == first_lock_inode
        assert target.stat().st_ino != first_auth_inode
        with lock.open("a+") as contender:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # The consumer rereads the rotating lease while holding its same flock;
        # publication never replaces/truncates the inode used for that lock.
        consumer = json.loads(target.read_bytes())
        assert consumer["tokens"]["access_token"] == "access-two"
    assert "refresh_token" not in json.dumps(consumer)
    assert target.stat().st_mode & 0o777 == 0o600
    assert lock.read_bytes() == b""
    check_codex_consumer_auth(target)


@pytest.mark.parametrize("invalid_lock", ["symlink", "hardlink", "directory", "nonempty", "wrong_mode"])
def test_codex_consumer_rejects_unsafe_lock_even_when_auth_is_unchanged(
    private_tmp_path: Path, invalid_lock: str,
) -> None:
    source = private_tmp_path / "owner" / "auth.json"
    target = private_tmp_path / "hindsight" / "auth.json"
    source.parent.mkdir()
    _source(source)
    _lease_directory(target.parent, 0o700)
    publish_codex_consumer_auth(source, target)
    auth_before = target.read_bytes(), target.stat().st_ino
    lock = target.with_suffix(".lock")
    lock.unlink()
    sibling = target.parent / "sibling"
    if invalid_lock in {"symlink", "hardlink"}:
        sibling.write_bytes(b"")
        sibling.chmod(0o600)
        if invalid_lock == "symlink":
            lock.symlink_to(sibling)
        else:
            os.link(sibling, lock)
    elif invalid_lock == "directory":
        lock.mkdir(mode=0o700)
    else:
        lock.write_bytes(b"unexpected content" if invalid_lock == "nonempty" else b"")
        lock.chmod(0o640 if invalid_lock == "wrong_mode" else 0o600)
    metadata_before = lock.lstat()
    with pytest.raises(ModelCredentialLeaseError, match="lock"):
        publish_codex_consumer_auth(source, target)
    assert (target.read_bytes(), target.stat().st_ino) == auth_before
    assert lock.lstat() == metadata_before
    if invalid_lock == "nonempty":
        assert lock.read_bytes() == b"unexpected content"
    if sibling.exists():
        assert sibling.read_bytes() == b""


def test_codex_consumer_health_requires_published_auth_and_preexisting_valid_lock(
    private_tmp_path: Path,
) -> None:
    source = private_tmp_path / "owner" / "auth.json"
    target = private_tmp_path / "hindsight" / "auth.json"
    source.parent.mkdir()
    _source(source)
    _lease_directory(target.parent, 0o700)
    with pytest.raises(ModelCredentialLeaseError):
        check_codex_consumer_auth(target)
    assert not target.exists() and not target.with_suffix(".lock").exists()
    publish_codex_consumer_auth(source, target)
    auth_before = target.read_bytes(), target.stat().st_ino
    lock_before = target.with_suffix(".lock").stat().st_ino
    check_codex_consumer_auth(target)
    assert (target.read_bytes(), target.stat().st_ino) == auth_before
    assert target.with_suffix(".lock").stat().st_ino == lock_before
    target.write_text(json.dumps({"auth_mode":"chatgpt", "tokens": {
        "access_token":"access-one", "account_id":"account-one", "refresh_token":"forbidden",
    }}))
    with pytest.raises(ModelCredentialLeaseError):
        check_codex_consumer_auth(target)


def test_unavailable_owner_replaces_a_stale_access_token(private_tmp_path: Path) -> None:
    target = private_tmp_path / "consumer" / "auth.json"
    _lease_directory(target.parent, 0o2750)
    target.write_text('{"access_token":"stale-secret"}')

    assert publish_unavailable_lease(target) is True

    value = json.loads(target.read_bytes())
    assert value == {
        "contract": "EvaModelAccessLeaseUnavailable/v1",
        "state": "source_unavailable",
    }
    assert "stale-secret" not in target.read_text()


def test_staged_login_replaces_live_owner_only_after_validation(
    private_tmp_path: Path,
) -> None:
    staged = private_tmp_path / "staged" / "auth.json"
    owner = private_tmp_path / "owner" / "auth.json"
    staged.parent.mkdir()
    _lease_directory(owner.parent, 0o700)
    owner.write_text("old-invalid-owner")
    _source(staged, "fresh-access")

    assert install_auth_owner(staged, owner) is True

    assert owner.read_bytes() == staged.read_bytes()
    assert json.loads(owner.read_bytes())["tokens"]["refresh_token"] == (
        "must-never-leave-owner"
    )


def test_invalid_staged_login_cannot_replace_live_owner(private_tmp_path: Path) -> None:
    staged = private_tmp_path / "staged.json"
    owner = private_tmp_path / "owner.json"
    owner.parent.chmod(0o700)
    staged.write_text('{"tokens":{"access_token":"incomplete"}}')
    owner.write_text("current-owner")

    with pytest.raises(ModelCredentialLeaseError):
        install_auth_owner(staged, owner)

    assert owner.read_text() == "current-owner"


def test_publisher_requires_exact_preprovisioned_directory_contract(
    private_tmp_path: Path,
) -> None:
    source = private_tmp_path / "auth.json"
    _source(source)
    target = private_tmp_path / "lease" / "auth.json"

    with pytest.raises(ModelCredentialLeaseError, match="directory is unavailable"):
        publish_access_lease(source, target)

    _lease_directory(target.parent, 0o700)
    with pytest.raises(ModelCredentialLeaseError, match="directory differs"):
        publish_access_lease(source, target)

    target.parent.chmod(0o2750)
    assert publish_access_lease(source, target) is True
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
