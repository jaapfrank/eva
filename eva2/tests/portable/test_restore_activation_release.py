"""Real restore transaction; component reconciliation and process guard are mocked.

These checks prove the database release boundary, not full runtime activation.
"""
import json
from datetime import UTC, datetime
from copy import deepcopy

import pytest

from eva.application.portable_archive import canonical_json_bytes
from eva.contracts.canonical_json import canonical_sha256
from eva.operations.restore_activation import release_restored_database, require_restore_activation
from tests.portable.test_installer_installation_recovery import installation
from tests.portable.test_installation_recovery_package import staged, current_archive, PASSPHRASE
from tests.portable.test_portable_v2_current_work import RUNTIME


@pytest.mark.parametrize("failure", [None, "component", "revocation"])
def test_release_requires_fresh_component_check_and_current_authority(installation, failure):
    wizard, engine, authority, destination, package, blob, guards = installation
    preview = wizard.prepare(blob, PASSPHRASE)
    restored = wizard.execute(preview["confirmation"])
    restore_id = str(restored["restore_id"])
    called = []

    def components(connection):
        called.append("components")
        if failure == "component":
            raise ValueError("synthetic component verification failed")
        receipt = {
            "contract": "EvaRestoreActivationComponents/v1",
            "restore_id": restore_id,
            "archive_sha256": package["archive_sha256"],
            "target_database_oid": connection.exec_driver_sql(
                "SELECT oid::bigint FROM pg_database WHERE datname=current_database()").scalar_one(),
            "verified_components": {key: canonical_sha256(value) for key, value in
                                    package["manifest"]["source_components"].items()},
            "verified_requirements": package["core_archive"]["manifest"]["external_requirements"],
        }
        if failure == "revocation":
            revoked = json.loads(authority[0])
            revoked["authorized_archives"] = []
            authority[0] = canonical_json_bytes(revoked)
        return receipt

    def release():
        with engine.begin() as connection:
            return release_restored_database(connection, restore_id=restore_id,
                package_manifest_raw=package["manifest_raw"],
                core_manifest_raw=package["core_archive"]["manifest_raw"],
                authority_reader=lambda: authority[0], runtime_identity=RUNTIME,
                runtime_guard=wizard.runtime_guard, verify_components=components,
                now=datetime(2026, 9, 24, 12, 0, tzinfo=UTC))

    if failure is not None:
        with pytest.raises(ValueError):
            release()
    else:
        result = release()
        require_restore_activation(engine)
    assert called == ["components"]
    with engine.connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT held,release_authority_sha256 FROM eva_core.restore_activation").one()
    assert row.held is (failure is not None)
    assert (row.release_authority_sha256 is None) is (failure is not None)
