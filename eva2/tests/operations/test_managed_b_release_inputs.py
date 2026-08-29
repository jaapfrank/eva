from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from tools.managed_b_candidate.release_inputs import (
    AUTHORITY_PATH,
    _pin_rows,
    _validate_static_archive_inputs,
    prepare_managed_b_release_inputs,
)


ROOT = Path(__file__).resolve().parents[2]


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        + b"\n"
    )


def test_release_pin_rows_bind_bytes_and_the_complete_sorted_path_set() -> None:
    files = {
        "a.py": (b"a\n", 0o644),
        "b.py": (b"b\n", 0o644),
        "c.py": (b"c\n", 0o644),
    }
    rows = [
        {"path": path, "sha256": hashlib.sha256(files[path][0]).hexdigest()}
        for path in ("a.py", "b.py")
    ]
    expected_paths_sha256 = hashlib.sha256(_canonical(("a.py", "b.py"))).hexdigest()

    assert _pin_rows(
        files,
        rows,
        expected_paths_sha256=expected_paths_sha256,
        label="test",
    ) == tuple((row["path"], row["sha256"]) for row in rows)

    with pytest.raises(RuntimeError, match="path set differs"):
        _pin_rows(
            files,
            rows[:1],
            expected_paths_sha256=expected_paths_sha256,
            label="test",
        )
    with pytest.raises(RuntimeError, match="path set differs"):
        _pin_rows(
            files,
            rows + [{"path": "c.py", "sha256": hashlib.sha256(b"c\n").hexdigest()}],
            expected_paths_sha256=expected_paths_sha256,
            label="test",
        )
    with pytest.raises(RuntimeError, match="pins must be unique and sorted"):
        _pin_rows(
            files,
            list(reversed(rows)),
            expected_paths_sha256=expected_paths_sha256,
            label="test",
        )
    changed = copy.deepcopy(rows)
    changed[0]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="pinned input differs"):
        _pin_rows(
            files,
            changed,
            expected_paths_sha256=expected_paths_sha256,
            label="test",
        )


def _authority() -> tuple[dict[str, object], bytes]:
    raw = (ROOT / AUTHORITY_PATH).read_bytes()
    return json.loads(raw), raw


def _files(authority: dict[str, object]) -> dict[str, tuple[bytes, int]]:
    paths = {AUTHORITY_PATH}
    for key in ("requiredInputs", "requiredTests"):
        paths.update(row["path"] for row in authority[key])
    migration_authority = authority["migrationAuthority"]
    paths.update(row["path"] for row in migration_authority["revisions"])
    manifest = json.loads((ROOT / "schemas/contracts/manifest.json").read_bytes())
    paths.update(
        f"schemas/contracts/{contract.replace('/', '-')}.schema.json"
        for contract in manifest["schemas"]
    )
    portable_v1_manifest = json.loads(
        (ROOT / "schemas/portable/v1/contracts/manifest.json").read_bytes()
    )
    paths.update(
        f"schemas/portable/v1/contracts/{contract.replace('/', '-')}.schema.json"
        for contract in portable_v1_manifest["schemas"]
    )
    return {
        path: ((ROOT / path).read_bytes(), (ROOT / path).stat().st_mode & 0o777)
        for path in sorted(paths)
    }


def _validate(
    files: dict[str, tuple[bytes, int]], authority_raw: bytes
) -> dict[str, object]:
    return _validate_static_archive_inputs(files, authority_raw)


def _replace_authority(
    files: dict[str, tuple[bytes, int]], authority: dict[str, object]
) -> bytes:
    raw = _canonical(authority)
    files[AUTHORITY_PATH] = (raw, 0o644)
    return raw


def _replace_public_projection(
    files: dict[str, tuple[bytes, int]],
    authority: dict[str, object],
    projection: dict[str, object],
) -> bytes:
    path = "evidence/public/eva-cockpit-sanitized-evidence-v1.json"
    files[path] = (_canonical(projection), 0o644)
    projection_sha256 = hashlib.sha256(files[path][0]).hexdigest()
    index_path = "release/eva-cockpit-evidence-index-v1.json"
    index = json.loads(files[index_path][0])
    for entry in index["entries"]:
        for source in entry["sources"]:
            if source["path"] == path:
                source["sha256"] = projection_sha256
    files[index_path] = (_canonical(index), 0o644)
    for changed_path in (path, index_path):
        row = next(
            item for item in authority["requiredInputs"] if item["path"] == changed_path
        )
        row["sha256"] = hashlib.sha256(files[changed_path][0]).hexdigest()
    return _replace_authority(files, authority)


def test_release_inputs_bind_current_registry_runtime_dependencies_and_migrations() -> (
    None
):
    authority, raw = _authority()

    receipt = _validate(_files(authority), raw)

    assert receipt["contract"] == "EvaManagedBReleaseInputReceipt/v1"
    assert receipt["status"] == "version_matched_dependency_inputs_bound"
    assert receipt["dependency_alignment"]["status"] == (
        "version_matched_runtime_dependency_inputs"
    )
    assert receipt["dependency_alignment"]["expected_wheel_count"] == 35
    assert receipt["current_contract_count"] == 71
    assert receipt["historical_4d_contract_count"] == 64
    assert receipt["portable_v1_contract_count"] == 64
    assert receipt["cockpit_evidence_claim_count"] == 16
    assert receipt["core_live_case_count"] == 15
    assert receipt["portable_v1_manifest_sha256"] == (
        "fcf6655ccc61f9258b132620b02d51492c7eb19ce762b4e1728548b6fa2d53a5"
    )
    assert receipt["migration_head"] == "4e_0006_memory_outbox_erasure"
    assert "source_commit" not in receipt
    assert "source_archive_sha256" not in receipt
    assert receipt["authority_sha256"] == hashlib.sha256(raw).hexdigest()
    assert receipt["network_used"] is False
    assert not ({"dsn", "password", "secret_value", "token"} & set(receipt))


def test_release_inputs_bind_cockpit_claims_to_exact_evidence_sources() -> None:
    authority, _ = _authority()
    files = _files(authority)
    changed = copy.deepcopy(authority)
    path = "reviews/delegated-subagent-vertical-f9373f1.md"
    files[path] = (files[path][0] + b"\n", 0o644)
    row = next(item for item in changed["requiredInputs"] if item["path"] == path)
    row["sha256"] = hashlib.sha256(files[path][0]).hexdigest()
    raw = _replace_authority(files, changed)

    with pytest.raises(RuntimeError, match="cockpit evidence source differs"):
        _validate(files, raw)


def test_release_inputs_refuse_a_relabelled_cockpit_claim() -> None:
    authority, _ = _authority()
    files = _files(authority)
    changed = copy.deepcopy(authority)
    path = "release/eva-cockpit-evidence-index-v1.json"
    index = json.loads(files[path][0])
    index["entries"][0]["claimState"] = "live"
    files[path] = (_canonical(index), 0o644)
    row = next(item for item in changed["requiredInputs"] if item["path"] == path)
    row["sha256"] = hashlib.sha256(files[path][0]).hexdigest()
    raw = _replace_authority(files, changed)

    with pytest.raises(RuntimeError, match="cockpit evidence entry is invalid"):
        _validate(files, raw)


def test_release_inputs_refuse_a_relabelled_sanitized_core_case() -> None:
    authority, _ = _authority()
    files = _files(authority)
    changed = copy.deepcopy(authority)
    path = "evidence/public/eva-cockpit-sanitized-evidence-v1.json"
    projection = json.loads(files[path][0])
    projection["coreCases"][0]["status"] = "passed"
    raw = _replace_public_projection(files, changed, projection)

    with pytest.raises(RuntimeError, match="sanitized core case is invalid"):
        _validate(files, raw)


@pytest.mark.parametrize(
    "private_field",
    ("reply", "entity_id", "corrected_name", "principal_id"),
)
def test_release_inputs_refuse_raw_fields_in_public_evidence(
    private_field: str,
) -> None:
    authority, _ = _authority()
    files = _files(authority)
    changed = copy.deepcopy(authority)
    path = "evidence/public/eva-cockpit-sanitized-evidence-v1.json"
    projection = json.loads(files[path][0])
    projection["claims"][0][private_field] = "must-not-enter-release"
    raw = _replace_public_projection(files, changed, projection)

    with pytest.raises(RuntimeError, match="sanitized cockpit claim fields differ"):
        _validate(files, raw)


def test_release_inputs_refuse_drift_in_an_original_historical_contract() -> None:
    authority, raw = _authority()
    files = _files(authority)
    manifest = json.loads(files["schemas/contracts/manifest.json"][0])
    historical_contract = next(
        contract
        for contract in manifest["schemas"]
        if contract
        not in authority["contractRegistry"]["historical4d"]["allowedCurrentAdditions"]
    )
    path = f"schemas/contracts/{historical_contract.replace('/', '-')}.schema.json"
    files[path] = (b"{}\n", 0o644)

    with pytest.raises(RuntimeError, match="contract schema differs"):
        _validate(files, raw)


def test_release_inputs_refuse_a_missing_or_unapproved_current_addition() -> None:
    authority, _ = _authority()
    files = _files(authority)
    changed = copy.deepcopy(authority)
    additions = changed["contractRegistry"]["historical4d"]["allowedCurrentAdditions"]
    additions.pop("IdentityPackDraft/v1")
    raw = _replace_authority(files, changed)

    with pytest.raises(RuntimeError, match="contract count differs"):
        _validate(files, raw)


def test_release_inputs_bind_exact_current_additions_without_resealing_v1() -> None:
    authority, raw = _authority()
    additions = authority["contractRegistry"]["historical4d"]["allowedCurrentAdditions"]

    assert set(additions) == {
        "AttentionWake/v2",
        "DelegatedWork/v1",
        "DelegatedWorkGrant/v1",
        "DelegatedWorkResult/v1",
        "DelegatedWorkUsage/v1",
        "IdentityPackDraft/v1",
        "McpReadOnlyToolImport/v1",
    }
    files = _files(authority)
    assert (
        hashlib.sha256(
            files["schemas/contracts/AttentionWake-v1.schema.json"][0]
        ).hexdigest()
        == "79d91bec82cf0c2f18fe3ed6c2bf95d714842a2ac499a2d1ea6aca11bc5a7cbc"
    )
    _validate(files, raw)


def test_release_inputs_refuse_current_manifest_or_corpus_drift() -> None:
    authority, raw = _authority()
    files = _files(authority)
    path = "schemas/contracts/contract-corpus-v1.json"
    files[path] = (files[path][0] + b" ", 0o644)

    with pytest.raises(RuntimeError, match="pinned input differs"):
        _validate(files, raw)


def test_release_inputs_bind_portable_v1_separately_from_current_registry() -> None:
    authority, raw = _authority()
    files = _files(authority)

    assert (
        hashlib.sha256(
            files["schemas/portable/v1/contracts/manifest.json"][0]
        ).hexdigest()
        == authority["contractRegistry"]["historical4d"]["manifestSha256"]
    )
    assert (
        hashlib.sha256(files["schemas/contracts/manifest.json"][0]).hexdigest()
        == (authority["contractRegistry"]["currentManifestSha256"])
    )
    _validate(files, raw)


@pytest.mark.parametrize(
    "mutation",
    ("missing_schema", "changed_schema", "extra_file", "changed_manifest"),
)
def test_release_inputs_refuse_portable_v1_bundle_drift(mutation: str) -> None:
    authority, raw = _authority()
    files = _files(authority)
    schema_path = "schemas/portable/v1/contracts/AttentionWake-v1.schema.json"
    if mutation == "missing_schema":
        files.pop(schema_path)
    elif mutation == "changed_schema":
        files[schema_path] = (files[schema_path][0] + b" ", 0o644)
    elif mutation == "extra_file":
        files["schemas/portable/v1/contracts/unexpected.json"] = (b"{}\n", 0o644)
    else:
        manifest_path = "schemas/portable/v1/contracts/manifest.json"
        files[manifest_path] = (files[manifest_path][0] + b" ", 0o644)

    with pytest.raises(
        RuntimeError,
        match="portable/v1|portable v1",
    ):
        _validate(files, raw)


def test_release_inputs_refuse_migration_bytes_or_head_drift() -> None:
    authority, raw = _authority()
    files = _files(authority)
    path = authority["migrationAuthority"]["revisions"][0]["path"]
    files[path] = (files[path][0] + b"\n", 0o644)

    with pytest.raises(RuntimeError, match="migration chain differs"):
        _validate(files, raw)

    files = _files(authority)
    changed = copy.deepcopy(authority)
    changed["migrationAuthority"]["head"] = "4e_0001_operational_timeline"
    changed_raw = _replace_authority(files, changed)
    with pytest.raises(RuntimeError, match="migration authority is invalid"):
        _validate(files, changed_raw)


def test_release_inputs_refuse_orphan_schema_or_migration_files() -> None:
    authority, raw = _authority()
    files = _files(authority)
    files["schemas/contracts/Orphan-v1.schema.json"] = (b"{}\n", 0o644)
    with pytest.raises(RuntimeError, match="schema file set differs"):
        _validate(files, raw)

    files = _files(authority)
    files["migrations/versions/4z_9999_orphan.py"] = (
        b'revision = "4z_9999_orphan"\ndown_revision = "4e_0001_operational_timeline"\n',
        0o644,
    )
    with pytest.raises(RuntimeError, match="migration file set differs"):
        _validate(files, raw)

    files = _files(authority)
    changed = copy.deepcopy(authority)
    changed["migrationAuthority"]["revisions"].pop()
    changed_raw = _replace_authority(files, changed)
    with pytest.raises(RuntimeError, match="migration authority is invalid"):
        _validate(files, changed_raw)


def test_release_inputs_refuse_changed_build_or_test_input() -> None:
    authority, raw = _authority()
    files = _files(authority)
    build_path = "deploy/living-core/Dockerfile"
    files[build_path] = (files[build_path][0] + b"\n", 0o644)
    with pytest.raises(RuntimeError, match="pinned input differs"):
        _validate(files, raw)

    files = _files(authority)
    test_path = authority["requiredTests"][0]["path"]
    files[test_path] = (files[test_path][0] + b"\n", 0o644)
    with pytest.raises(RuntimeError, match="pinned input differs"):
        _validate(files, raw)


def test_release_inputs_refuse_semantically_resealed_dependency_drift() -> None:
    authority, _ = _authority()
    files = _files(authority)
    changed = copy.deepcopy(authority)
    path = "release/managed-b-wheelhouse-amd64-v2.json"
    manifest = json.loads(files[path][0])
    manifest["selectedManifestSha256"] = "0" * 64
    files[path] = (_canonical(manifest).rstrip(b"\n"), 0o644)
    row = next(item for item in changed["requiredInputs"] if item["path"] == path)
    row["sha256"] = hashlib.sha256(files[path][0]).hexdigest()
    raw = _replace_authority(files, changed)

    with pytest.raises(RuntimeError, match="selected manifest differs"):
        _validate(files, raw)


def test_release_inputs_require_authority_from_same_archive_and_real_git_commit() -> (
    None
):
    authority, raw = _authority()
    files = _files(authority)
    changed = copy.deepcopy(authority)
    changed["nonClaims"] = [*changed["nonClaims"], "Changed outside archive."]
    changed_raw = _canonical(changed)

    with pytest.raises(RuntimeError, match="authority differs from source archive"):
        _validate(files, changed_raw)
    with pytest.raises(RuntimeError, match="source preparation failed"):
        prepare_managed_b_release_inputs("c" * 40)


@pytest.mark.parametrize("key", ["requiredInputs", "requiredTests"])
def test_release_inputs_refuse_a_removed_or_extra_authority_pin(key: str) -> None:
    authority, _ = _authority()
    files = _files(authority)
    changed = copy.deepcopy(authority)
    changed[key].pop()
    raw = _replace_authority(files, changed)
    with pytest.raises(RuntimeError, match="path set differs"):
        _validate(files, raw)

    files = _files(authority)
    changed = copy.deepcopy(authority)
    extra_path = "src/eva/__init__.py"
    files[extra_path] = ((ROOT / extra_path).read_bytes(), 0o644)
    changed[key].append(
        {"path": extra_path, "sha256": hashlib.sha256(files[extra_path][0]).hexdigest()}
    )
    changed[key].sort(key=lambda row: row["path"])
    raw = _replace_authority(files, changed)
    with pytest.raises(RuntimeError, match="path set differs"):
        _validate(files, raw)


def test_release_inputs_refuse_oversold_nonclaims_or_an_unsafe_path() -> None:
    authority, _ = _authority()
    files = _files(authority)
    changed = copy.deepcopy(authority)
    changed["nonClaims"] = ["Everything is production ready."]
    raw = _replace_authority(files, changed)
    with pytest.raises(RuntimeError, match="authority header differs"):
        _validate(files, raw)

    files = _files(authority)
    changed = copy.deepcopy(authority)
    changed["requiredInputs"][0]["path"] = "unsafe\\path"
    raw = _replace_authority(files, changed)
    with pytest.raises(RuntimeError, match="path is invalid"):
        _validate(files, raw)


def test_release_inputs_refuse_group_or_world_writable_pins() -> None:
    authority, raw = _authority()
    files = _files(authority)
    path = authority["requiredInputs"][0]["path"]
    files[path] = (files[path][0], 0o666)

    with pytest.raises(RuntimeError, match="group/world writable"):
        _validate(files, raw)
