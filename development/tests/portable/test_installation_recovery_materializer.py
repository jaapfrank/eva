"""Inactive role-bound publication using the real transported core fixture."""
from copy import deepcopy
import os
import stat

import pytest

from tools.installation_recovery_package import read_installation_package, write_installation_package
from tools.installation_recovery_materializer import materialize_installation_package, COMPONENTS
from tools.portable_transport import seal_portable_archive, open_portable_transport
from tests.portable.test_installation_recovery_package import staged, current_archive, PASSPHRASE


@pytest.fixture
def transported(staged, tmp_path):
    source, inputs = staged
    write_installation_package(source, **inputs)
    sealed, opened = tmp_path / "sealed.eva", tmp_path / "opened"
    seal_portable_archive(source, sealed, passphrase=PASSPHRASE, validator=read_installation_package)
    open_portable_transport(sealed, opened, passphrase=PASSPHRASE, validator=read_installation_package)
    parent = tmp_path / "private-targets"
    parent.mkdir(mode=0o700)
    owners = {name: {"uid": os.geteuid(), "gid": os.getegid()} for name in COMPONENTS}
    return opened, parent / "inactive", owners


def test_transported_bytes_get_role_modes_without_starting_or_releasing(transported):
    source, target, owners = transported
    before = read_installation_package(source)
    assert (source / "config/provider.json").stat().st_mode & 0o777 == 0o600
    result = materialize_installation_package(source, target, owners=owners)
    assert result["status"] == "materialized_inactive"
    assert result["archive_sha256"] == before["archive_sha256"]
    assert read_installation_package(target)["manifest_raw"] == before["manifest_raw"]
    for name, entry in before["manifest"]["files"].items():
        actual = target / name
        assert actual.read_bytes() == (source / name).read_bytes()
        info = actual.stat()
        assert stat.S_IMODE(info.st_mode) == int(entry["mode"], 8)
        assert (info.st_uid, info.st_gid) == (owners[name.split("/")[0]]["uid"], owners[name.split("/")[0]]["gid"])
    for path in target.rglob("*"):
        if path.is_dir():
            assert stat.S_IMODE(path.stat().st_mode) == 0o700
    assert (source / "config/provider.json").stat().st_mode & 0o777 == 0o600
    assert not list(target.parent.glob(".installation-inactive-*"))


@pytest.mark.parametrize("kind", ["directory", "file", "symlink"])
def test_existing_target_is_never_replaced(transported, kind):
    source, target, owners = transported
    if kind == "directory":
        target.mkdir()
    elif kind == "file":
        target.write_bytes(b"preserved")
    else:
        target.symlink_to(source, target_is_directory=True)
    before = target.lstat()
    with pytest.raises(FileExistsError):
        materialize_installation_package(source, target, owners=owners)
    assert target.lstat() == before


@pytest.mark.parametrize("kind", ["changed", "symlink", "hardlink"])
def test_invalid_source_never_publishes(transported, kind):
    source, target, owners = transported
    file = source / "documents/project/report.md"
    if kind == "changed":
        file.write_bytes(b"tampered")
    elif kind == "symlink":
        file.unlink()
        file.symlink_to(source / "memory/hindsight.dump")
    else:
        os.link(file, source / "documents/project/extra.md")
    with pytest.raises(ValueError):
        materialize_installation_package(source, target, owners=owners)
    assert not target.exists()
    assert not list(target.parent.glob(".installation-inactive-*"))


def test_owner_policy_is_external_and_missing_component_is_refused(transported):
    source, target, owners = transported
    del owners["documents"]
    with pytest.raises(ValueError, match="one trusted owner"):
        materialize_installation_package(source, target, owners=owners)
    assert not target.exists()


def test_unprivileged_process_cannot_choose_another_owner(transported, monkeypatch):
    source, target, owners = transported
    owners = deepcopy(owners)
    owners["documents"]["uid"] += 1
    # Root test runs still exercise the unprivileged policy decision without
    # pretending an actual chown took place.
    monkeypatch.setattr(os, "geteuid", lambda: 23456)
    with pytest.raises(ValueError, match="privileged provisioning"):
        materialize_installation_package(source, target, owners=owners)
    assert not target.exists()


def test_wrong_materialized_owner_blocks_publication(transported, monkeypatch):
    source, target, owners = transported
    import tools.installation_recovery_materializer as module
    original = module._verify_tree
    def wrong_owner(fd, raw, entries, policy):
        altered = deepcopy(policy)
        altered["documents"]["gid"] += 1
        original(fd, raw, entries, altered)
    monkeypatch.setattr(module, "_verify_tree", wrong_owner)
    with pytest.raises(ValueError, match="owner or mode"):
        materialize_installation_package(source, target, owners=owners)
    assert not target.exists()
    assert not list(target.parent.glob(".installation-inactive-*"))


def test_destination_appearing_at_publication_is_preserved(transported, monkeypatch):
    source, target, owners = transported
    import tools.installation_recovery_materializer as module
    original = module._rename_absent
    def race(parent, staging, name):
        os.mkdir(name, mode=0o700, dir_fd=parent)
        original(parent, staging, name)
    monkeypatch.setattr(module, "_rename_absent", race)
    with pytest.raises(FileExistsError):
        materialize_installation_package(source, target, owners=owners)
    assert target.is_dir() and list(target.iterdir()) == []
    assert not list(target.parent.glob(".installation-inactive-*"))


def test_nonprivate_target_parent_is_refused(transported):
    source, target, owners = transported
    target.parent.chmod(0o755)
    with pytest.raises(ValueError, match="private and operator-owned"):
        materialize_installation_package(source, target, owners=owners)
    assert not target.exists()

@pytest.mark.skipif(os.geteuid() != 0, reason="actual cross-owner provisioning requires root")
def test_root_provisions_real_provider_uid_10001(transported):
    source, target, owners = transported
    for name in ("documents", "config", "gateway"):
        owners[name] = {"uid": 10001, "gid": 10001}
    package = read_installation_package(source)
    materialize_installation_package(source, target, owners=owners)
    for name, entry in package["manifest"]["files"].items():
        file = target / name
        owner = owners[name.split("/")[0]]
        assert (file.stat().st_uid, file.stat().st_gid) == (owner["uid"], owner["gid"])
        assert stat.S_IMODE(file.stat().st_mode) == int(entry["mode"], 8)
        assert file.read_bytes() == (source / name).read_bytes()
    for name in ("documents", "config", "gateway"):
        for path in [target / name, *(target / name).rglob("*")]:
            assert (path.stat().st_uid, path.stat().st_gid) == (10001, 10001)
            if path.is_dir():
                assert stat.S_IMODE(path.stat().st_mode) == 0o700
    assert target.stat().st_uid == 0
