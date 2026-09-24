"""Wizard package integration; real PostgreSQL, explicitly mocked runtime guard.

External component bytes remain synthetic. No external database import, native
relocation, provider startup or complete installation activation is exercised.
"""
import json
import os

import pytest
import sqlalchemy as sa

from eva.application.installer_restore import InactiveRecoveryMaterialRetained
from eva.application.portable_archive import canonical_json_bytes
from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
from eva.operations.restore_activation import require_restore_activation, RestoreActivationRequired
from tools.installer_portable_restore import InstallerPortableRestore
from tools.installation_recovery_package import read_installation_package, write_installation_package
from tools.installation_recovery_materializer import COMPONENTS
from tools.portable_transport import seal_portable_archive
from tests.portable.restore_authority_fixture import restore_authority_for_archive
from tests.portable.test_installation_recovery_package import staged, current_archive, PASSPHRASE
from tests.portable.test_portable_v2_current_work import RUNTIME


@pytest.fixture
def installation(staged, current_archive, tmp_path):
    root, inputs = staged
    package = write_installation_package(root, **inputs)
    sealed = tmp_path / "installation.eva"
    seal_portable_archive(root, sealed, passphrase=PASSPHRASE, validator=read_installation_package)
    authority = json.loads(restore_authority_for_archive(package["core_archive"]["manifest_raw"]))
    authority["authorized_archives"] += json.loads(
        restore_authority_for_archive(package["manifest_raw"]))["authorized_archives"]
    authority["authorized_archives"].sort(key=lambda row: row["archive_id"])
    current_authority = [canonical_json_bytes(authority)]
    target = current_archive["database"]()
    workspace = tmp_path / "wizard"
    workspace.mkdir(mode=0o700)
    parent = tmp_path / "materialized"
    parent.mkdir(mode=0o700)
    destination = parent / "inactive"
    guards = []
    def guard(connection):
        assert connection.in_transaction() and not connection.in_nested_transaction()
        guards.append(connection.exec_driver_sql("SELECT count(*) FROM eva_core.installations").scalar_one())
    wizard = InstallerPortableRestore(engine=target, authority_reader=lambda:current_authority[0],
        workspace=workspace, object_store_root=tmp_path / "objects", runtime_identity=RUNTIME,
        runtime_guard=guard, recovery_materialization={"destination":str(destination),
            "owners":{name:{"uid":os.geteuid(), "gid":os.getegid()} for name in COMPONENTS}})
    try:
        yield wizard, target, current_authority, destination, package, sealed.read_bytes(), guards
    finally:
        wizard.close()


def assert_empty(engine):
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM eva_core.installations").scalar_one() == 0
        assert connection.exec_driver_sql("SELECT count(*) FROM eva_core.restore_activation").scalar_one() == 0


def test_package_restores_inner_state_binds_outer_hold_and_keeps_eva_off(installation, current_archive):
    wizard, target, _, destination, package, blob, guards = installation
    preview = wizard.prepare(blob, PASSPHRASE)
    assert "Eva blijft uit" in preview["summary"]["notice"]
    assert preview["summary"]["archive_sha256"] == package["archive_sha256"]
    result = wizard.execute(preview["confirmation"])
    assert result["external_components"] == "materialized_inactive"
    assert result["target_activation"] == "closed"
    assert result["core_archive_sha256"] == package["core_archive"]["archive_sha256"]
    assert guards[:2] == [0, 0] and len(guards) == 3 and guards[-1] > 0
    with target.connect() as connection:
        hold = connection.exec_driver_sql("SELECT archive_sha256, held FROM eva_core.restore_activation").one()
        assert hold == (package["archive_sha256"], True)
    with pytest.raises(RestoreActivationRequired):
        require_restore_activation(target)
    store = PostgresDelegatedWorkStore(target, current_archive["scope"])
    for name, expected in current_archive["source_work"].items():
        assert store.load(current_archive["specs"][name].work_id,
            owner_principal_id=current_archive["owner"], principal_binding_id=current_archive["binding"]) == expected
    assert read_installation_package(destination)["manifest_raw"] == package["manifest_raw"]
    assert (destination / "config/provider.json").stat().st_mode & 0o777 == 0o400


def test_final_authority_revocation_rolls_back_database_and_retains_inactive_files(installation):
    wizard, target, authority, destination, package, blob, guards = installation
    original = wizard.runtime_guard
    def revoke(connection):
        original(connection)
        if len(guards) == 3:
            revoked = json.loads(authority[0])
            revoked.update(erasure_generation=2, authorized_archives=[])
            authority[0] = canonical_json_bytes(revoked)
    wizard.runtime_guard = revoke
    preview = wizard.prepare(blob, PASSPHRASE)
    with pytest.raises(InactiveRecoveryMaterialRetained) as error:
        wizard.execute(preview["confirmation"])
    assert error.value.destination == str(destination)
    assert_empty(target)
    assert read_installation_package(destination)["manifest_raw"] == package["manifest_raw"]
    assert not list(wizard.workspace.iterdir())


def test_existing_destination_is_preserved_without_database_import(installation):
    wizard, target, _, destination, _, blob, _ = installation
    preview = wizard.prepare(blob, PASSPHRASE)
    destination.mkdir()
    marker = destination / "do-not-overwrite"
    marker.write_bytes(b"retained")
    with pytest.raises(FileExistsError):
        wizard.execute(preview["confirmation"])
    assert marker.read_bytes() == b"retained"
    assert_empty(target)


@pytest.mark.parametrize("missing", ["materialization", "runtime_identity", "outer_permission", "inner_permission"])
def test_prepare_requires_separate_configuration_and_both_permissions(installation, missing):
    wizard, target, authority, destination, package, blob, _ = installation
    if missing == "materialization":
        wizard.recovery_materialization = None
    elif missing == "runtime_identity":
        wizard.runtime_identity = None
    else:
        value = json.loads(authority[0])
        removed = package["archive_sha256"] if missing == "outer_permission" else package["core_archive"]["archive_sha256"]
        value["authorized_archives"] = [row for row in value["authorized_archives"] if row["archive_json_sha256"] != removed]
        authority[0] = canonical_json_bytes(value)
    with pytest.raises(ValueError):
        wizard.prepare(blob, PASSPHRASE)
    assert_empty(target)
    assert not destination.exists()
