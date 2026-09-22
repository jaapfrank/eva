"""Both installation and core permissions must survive the same erasure generation."""
import hashlib
import json
import pytest
from eva.application.portable_archive import canonical_json_bytes
from eva.operations.portable_restore_authority import (
    PortableRestoreAuthorityError, authorize_installation_recovery,
)
from tests.portable.restore_authority_fixture import ARCHIVE_MANIFEST_RAW, restore_authority_for_archive


def pair():
    core = json.loads(ARCHIVE_MANIFEST_RAW)
    core.update(contract="EvaPortableArchive/v2", schema_version=2,
                source_tenant_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                runtime_identity={"tenant_id":"test", "installation_id":"test"})
    raw = canonical_json_bytes(core)
    digest = hashlib.sha256(raw).hexdigest()
    package = {**core, "contract":"EvaInstallationRecovery/v1", "schema_version":1,
        "archive_id":"dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        "core_archive_sha256":digest, "files":{"core/archive.json":{"sha256":digest}}}
    return raw, canonical_json_bytes(package)


def authority_for(core, package, *, admit_core=True, admit_package=True):
    value = json.loads(restore_authority_for_archive(core, admit=admit_core))
    if admit_package:
        value["authorized_archives"] += json.loads(restore_authority_for_archive(package))["authorized_archives"]
    return canonical_json_bytes(value)


def test_requires_two_exact_permissions_from_one_generation():
    core, package = pair()
    outer, inner = authorize_installation_recovery(package_manifest_raw=package,
        core_manifest_raw=core, restore_authority_raw=authority_for(core, package))
    assert outer.archive_json_sha256 == hashlib.sha256(package).hexdigest()
    assert inner.archive_json_sha256 == hashlib.sha256(core).hexdigest()
    assert outer.erasure_generation == inner.erasure_generation == 1
    assert outer.authority_sha256 == inner.authority_sha256


@pytest.mark.parametrize("admit_core,admit_package", [(False, True), (True, False), (False, False)])
def test_one_permission_never_authorizes_the_other(admit_core, admit_package):
    core, package = pair()
    with pytest.raises(PortableRestoreAuthorityError, match="not authorized"):
        authorize_installation_recovery(package_manifest_raw=package, core_manifest_raw=core,
            restore_authority_raw=authority_for(core, package, admit_core=admit_core, admit_package=admit_package))


@pytest.mark.parametrize("field", ["source_tenant_id", "runtime_identity", "created_at", "core_archive_sha256", "files"])
def test_even_authorized_mismatched_package_is_refused(field):
    core, package = pair()
    changed = json.loads(package)
    changed[field] = {"tenant_id":"other", "installation_id":"other"} if field == "runtime_identity" else (
        {} if field == "files" else "2026-08-29T07:31:00Z" if field == "created_at" else "e"*64)
    package = canonical_json_bytes(changed)
    with pytest.raises(PortableRestoreAuthorityError, match="binding differs|identity differs"):
        authorize_installation_recovery(package_manifest_raw=package, core_manifest_raw=core,
            restore_authority_raw=authority_for(core, package))


def test_new_erasure_generation_rejects_previously_authorized_package():
    core, package = pair()
    authority = json.loads(authority_for(core, package))
    authority.update(erasure_generation=2, authorized_archives=[])
    with pytest.raises(PortableRestoreAuthorityError, match="not authorized"):
        authorize_installation_recovery(package_manifest_raw=package, core_manifest_raw=core,
            restore_authority_raw=canonical_json_bytes(authority))
