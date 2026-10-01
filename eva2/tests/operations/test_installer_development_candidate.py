"""A local development receipt never fabricates production release support."""
import hashlib
import json
from pathlib import Path
import pytest
from eva.operations.installer_development_candidate import verify_installer_development_candidate, development_preflight


def package(tmp_path):
    file = tmp_path / "module.py"
    file.write_text("actual local bytes\n")
    images = {key: "sha256:" + digit * 64 for key, digit in
              (("living-core", "1"), ("postgres", "2"), ("hindsight", "3"))}
    metadata = {"contract": "EvaInstallerPackageStaging/v1", "status": "staged_not_validated",
        "base_commit": "a" * 40, "source_files": {"module.py": hashlib.sha256(file.read_bytes()).hexdigest()},
        "development_candidate": {"contract": "EvaLocalInstallerCandidate/v1", "imageDigests": images,
                                  "sourceCommit": "a" * 40, "public_release": False}}
    (tmp_path / "package-staging.json").write_text(json.dumps(metadata))
    return lambda digest: {"Id": digest, "Os": "linux", "Architecture": "amd64"}


def test_actual_package_bytes_are_rechecked_and_no_release_slot_is_authorized(tmp_path):
    manifest, verified = verify_installer_development_candidate(tmp_path, inspect_image=package(tmp_path))
    assert manifest["public_release"] is False
    assert not hasattr(verified, "for_slot")
    (tmp_path / "module.py").write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        verified.require_verified()


def test_actual_image_mismatch_blocks_before_any_installation(tmp_path):
    package(tmp_path)
    with pytest.raises(ValueError, match="differs"):
        verify_installer_development_candidate(tmp_path, inspect_image=lambda digest:
            {"Id": "sha256:" + "f" * 64, "Os": "linux", "Architecture": "amd64"})


def test_real_package_sized_inventory_does_not_use_small_message_object_limit(tmp_path):
    inspect = package(tmp_path)
    path = tmp_path / "package-staging.json"
    metadata = json.loads(path.read_text())
    digest = metadata["source_files"]["module.py"]
    for index in range(1100):
        filename = f"module-{index}.py"
        (tmp_path / filename).write_bytes((tmp_path / "module.py").read_bytes())
        metadata["source_files"][filename] = digest
    path.write_text(json.dumps(metadata))
    _, verified = verify_installer_development_candidate(tmp_path, inspect_image=inspect)
    assert len(verified.source_files) == 1101


@pytest.mark.parametrize("host_ok", [True, False])
def test_local_target_admission_preserves_unproved_public_support(tmp_path, host_ok):
    _, candidate = verify_installer_development_candidate(tmp_path, inspect_image=package(tmp_path))
    original = {"status": "blocked", "next": "blocked", "non_claims": [], "checks": [
        {"id": key, "state": "passed" if key != "release_support" and (key != "host" or host_ok) else "blocked"}
        for key in ("python", "host", "container_engine", "release_support")]}
    result = development_preflight(original, candidate)
    assert result["status"] == ("ready" if host_ok else "blocked")
    assert next(row for row in result["checks"] if row["id"] == "release_support")["state"] == "blocked"
    assert original["status"] == "blocked"
