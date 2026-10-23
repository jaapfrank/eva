"""E4.17 existing-format preservation; no runtime adoption or external restore.

The signed core archive uses synthetic rows from the existing v3 fixture.
Memory/gateway stand-ins are opaque bytes: no database import is claimed.
The skill bundles and companion documents use existing package/document roles.
Their companion status is data preservation, not runtime pause enforcement.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator

from eva.application.skill_packages import AgentSkillPackage, SkillResource, replace_python_resource
from eva.application.skill_package_execution import procedure_input
from eva.application.portable_archive import canonical_json_bytes
from tools.installation_recovery_package import (
    installation_identity_trust, read_installation_package, write_installation_package,
)
from tools.installation_recovery_materializer import COMPONENTS, materialize_installation_package
from tools.portable_transport import seal_portable_archive, open_portable_transport
from tests.portable.test_portable_runtime_archive import runtime_archive


SOURCE = b'''import json,sys
value=json.load(sys.stdin)
print(json.dumps({"count":len(value["observations"]),"unit":value["config"]["unit"]},sort_keys=True))
'''
CANDIDATE = SOURCE.replace(b'"count":len(value["observations"])', b'"count":999')
PREFIX = "documents/extensions/restore-probe/"


def skill_bundle():
    return AgentSkillPackage(
        repository_url="https://example.invalid/e417-fixture", revision="a" * 40,
        subdirectory="", license_id="LicenseRef-TestFixture", license_path="LICENSE",
        files=(
            SkillResource("SKILL.md", b"---\nname: restore-probe\ndescription: Offline recovery test fixture\n---\nCount the supplied observations.\n"),
            SkillResource("LICENSE", b"Synthetic test fixture; not an upstream license or project license change.\n"),
            SkillResource("requirements.lock", b"# Python 3.12 standard library only; no downloaded dependencies.\n"),
            SkillResource("schemas/config.schema.json", canonical_json_bytes({
                "type": "object", "properties": {"unit": {"type": "string"},
                "credential_ref": {"type": "string"}},
                "required": ["unit", "credential_ref"], "additionalProperties": False})),
            SkillResource("scripts/report.py", SOURCE),
        ),
    )


def run_offline(package, data, directory):
    """Trusted fixture only: fresh isolated Python process, no Eva/source imports."""
    directory.mkdir(mode=0o700)
    script = directory / "report.py"
    script.write_bytes(package.resource("scripts/report.py"))
    completed = subprocess.run(
        [sys.executable, "-I", "-S", str(script)], input=canonical_json_bytes(data),
        cwd=directory, env={"PATH": os.defpath, "HOME": str(directory)},
        capture_output=True, check=True, timeout=30,
    )
    return json.loads(completed.stdout)


@pytest.fixture
def recovered_extension(runtime_archive, tmp_path):
    args, _ = runtime_archive
    trust = args["identity_trust"]
    root = tmp_path / "old-installation"
    root.mkdir(mode=0o700)
    shutil.copytree(args["destination"], root / "core")
    baseline = skill_bundle()
    candidate = replace_python_resource(baseline, "scripts/report.py", CANDIDATE)
    config = {"unit": "C", "credential_ref": "owner:optional-api"}
    observations = [12, 18, 21]
    status = {"paused": True, "adopted_package_sha256": baseline.sha256,
              "candidate_package_sha256": candidate.sha256,
              "last_working_package_sha256": baseline.sha256,
              "permissions": ["read_own_files"],
              "existing_realization": {"id": "fixture:already-created", "status": "paused"}}
    payloads = {
        "memory/hindsight.dump": b"Synthetic opaque dump; no database restore.",
        "gateway/contextforge.sqlite": b"Synthetic opaque gateway; no SQLite restore.",
        PREFIX + "v1.skill.json": canonical_json_bytes(baseline.to_wire()),
        PREFIX + "v2.skill.json": canonical_json_bytes(candidate.to_wire()),
        PREFIX + "config.json": canonical_json_bytes(config),
        PREFIX + "observations.json": canonical_json_bytes(observations),
        PREFIX + "status.json": canonical_json_bytes(status),
    }
    for name, content in payloads.items():
        path = root / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o600)
    roles = {name: "document_artifact" for name in payloads if name.startswith("documents/")}
    sources = {kind: [{"name": kind + "-fixture", "image_sha256": "b" * 64}]
               for kind in ("memory", "gateway", "documents")}
    keys = {"gateway_encryption": "key:fixture-encryption", "gateway_jwt": "key:fixture-jwt",
            "gateway_access": "key:fixture-access"}
    expected_output = run_offline(baseline, {"observations": observations, "config": config}, tmp_path / "before-run")
    with installation_identity_trust(trust):
        saved = write_installation_package(root, file_roles=roles, source_components=sources,
                                           key_references=keys, identity_trust=trust)
        sealed = tmp_path / "recovery.eva"
        seal_portable_archive(root, sealed, passphrase="e417-offline-test-passphrase",
                              validator=read_installation_package)
        # Destroy all original archive/source bytes before opening on the clean target.
        shutil.rmtree(root)
        shutil.rmtree(args["destination"])
        shutil.rmtree(tmp_path / "before-run")
        assert not root.exists() and not args["destination"].exists()
        assert not (tmp_path / "before-run").exists()
        opened = tmp_path / "opened-on-new-host"
        open_portable_transport(sealed, opened, passphrase="e417-offline-test-passphrase",
                                validator=read_installation_package)
        parent = tmp_path / "new-private-installations"
        parent.mkdir(mode=0o700)
        target = parent / "inactive"
        owners = {name: {"uid": os.geteuid(), "gid": os.getegid()} for name in COMPONENTS}
        receipt = materialize_installation_package(opened, target, owners=owners)
        restored = read_installation_package(target, identity_trust=trust)
    return {"target": target, "opened": opened, "owners": owners, "trust": trust,
            "saved": saved, "restored": restored, "receipt": receipt, "payloads": payloads,
            "baseline": baseline, "candidate": candidate, "status": status,
            "expected_output": expected_output, "source": root}


def test_existing_archive_preserves_bundle_versions_data_and_paused_state(recovered_extension):
    value = recovered_extension
    target = value["target"]
    assert not value["source"].exists()
    assert value["receipt"]["status"] == "materialized_inactive"
    assert value["restored"]["manifest_raw"] == value["saved"]["manifest_raw"]
    for name, raw in value["payloads"].items():
        assert (target / name).read_bytes() == raw
        assert (target / name).stat().st_mode & 0o777 == 0o600
    state = json.loads((target / (PREFIX + "status.json")).read_bytes())
    assert state == value["status"] and state["paused"] is True
    for version, expected in [("v1", value["baseline"]), ("v2", value["candidate"])]:
        package = AgentSkillPackage.from_wire(json.loads((target / (PREFIX + version + ".skill.json")).read_bytes()))
        assert package.manifest() == expected.manifest()
        assert package.sha256 == expected.sha256
        assert package.resource("requirements.lock") == expected.resource("requirements.lock")
    assert state["last_working_package_sha256"] == value["baseline"].sha256
    assert state["last_working_package_sha256"] != value["candidate"].sha256
    assert not {"activated", "authorized", "released"} & set(value["restored"]["manifest"])


def test_restored_current_bundle_has_same_offline_behavior_without_old_source(recovered_extension, tmp_path):
    value = recovered_extension
    target = value["target"]
    package = AgentSkillPackage.from_wire(json.loads((target / (PREFIX + "v1.skill.json")).read_bytes()))
    data = {"observations": json.loads((target / (PREFIX + "observations.json")).read_bytes()),
            "config": json.loads((target / (PREFIX + "config.json")).read_bytes())}
    Draft202012Validator(json.loads(package.resource("schemas/config.schema.json"))).validate(data["config"])
    task = {"files": {}, "steps": [{"script": "scripts/report.py", "arguments": []}], "outputs": []}
    prepared = procedure_input(package, task)
    assert prepared["package_sha256"] == value["status"]["last_working_package_sha256"]
    assert run_offline(package, data, tmp_path / "clean-assessment") == value["expected_output"] == {"count": 3, "unit": "C"}
    # This explicit fixture assessment is not activation; the preserved status stays paused.
    assert json.loads((target / (PREFIX + "status.json")).read_bytes())["paused"] is True


def test_existing_target_is_preserved_and_not_reactivated(recovered_extension):
    value = recovered_extension
    target = value["target"]
    before = {str(p.relative_to(target)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in target.rglob("*") if p.is_file()}
    with installation_identity_trust(value["trust"]), pytest.raises(FileExistsError):
        materialize_installation_package(value["opened"], target, owners=value["owners"])
    assert before == {str(p.relative_to(target)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in target.rglob("*") if p.is_file()}


def test_packaged_schema_authority_does_not_require_a_checkout(monkeypatch):
    from tools import portable_runtime_archive as archive
    from tools.portable_export import _schema_payloads
    monkeypatch.setattr(archive, "__file__", "/absent-source-checkout/src/tools/portable_runtime_archive.py")
    assert archive._schemas() == _schema_payloads()


def test_record_spec_paths_and_hashes_match_the_existing_sealed_archive_manifest():
    from tools.postgres_portable_export import CANONICAL_EXPORT_SPECS
    from tools.portable_export import _schema_payloads
    sealed = json.loads(_schema_payloads()["schemas/portable/manifest.json"])
    expected = {row["table"]: (row["recordSchemaRelativePath"], row["record_schema_sha256"])
                for row in sealed["schemas"]}
    assert {spec["table"]: (spec["record_schema_relative_path"], spec["record_schema_sha256"])
            for spec in CANONICAL_EXPORT_SPECS} == expected
