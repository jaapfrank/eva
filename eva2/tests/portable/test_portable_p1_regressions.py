from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from tools.postgres_portable_export import (
    PrincipalBindingIsolationError,
    validate_principal_binding_evidence_scope,
)
from eva.adapters.postgres.catalog_generated import CATALOG_SPEC
from eva.application.portable_archive import PortableArchiveError, canonical_json_bytes
from tools.portable_export import _schema_payloads

from test_portable_export_end_to_end import _fixture_record_sets


EVA2_ROOT = Path(__file__).resolve().parents[2]
READER_PATH = EVA2_ROOT / "tools" / "read_eva_portable_archive.py"
HISTORICAL_V1_MANIFEST_SHA256 = (
    "fcf6655ccc61f9258b132620b02d51492c7eb19ce762b4e1728548b6fa2d53a5"
)
CURRENT_MANIFEST_SHA256 = (
    "b8124d2db419864db4f2213d5b455a59d7f836197822f90c17a38549f1864469"
)


def _reader_module():
    spec = importlib.util.spec_from_file_location("eva_portable_reader_p1", READER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot_records(snapshot_id: str) -> dict[str, list[dict[str, object]]]:
    record_sets, _, _ = _fixture_record_sets(snapshot_id)
    return {
        item.table: [copy.deepcopy(row) for row in item.records] for item in record_sets
    }


def _terminal_records() -> dict[str, list[dict[str, object]]]:
    return _snapshot_records("terminal")


def test_portable_v1_uses_its_historical_bundle_not_the_current_registry() -> None:
    payloads = _schema_payloads(EVA2_ROOT / "schemas")

    assert hashlib.sha256(
        payloads["schemas/contracts/manifest.json"]
    ).hexdigest() == HISTORICAL_V1_MANIFEST_SHA256
    assert hashlib.sha256(
        (EVA2_ROOT / "schemas" / "contracts" / "manifest.json").read_bytes()
    ).hexdigest() == CURRENT_MANIFEST_SHA256
    assert HISTORICAL_V1_MANIFEST_SHA256 != CURRENT_MANIFEST_SHA256


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        (
            "contracts/manifest.json",
            "fcf6655ccc61f9258b132620b02d51492c7eb19ce762b4e1728548b6fa2d53a5",
        ),
        (
            "contracts/canonical-json-profile-v1.json",
            "d3cd8347c77de4a57c4ad7834b8ba170516b420c9abd5079dac1442d0a9d0ad9",
        ),
    ],
)
def test_producer_rejects_unpinned_contract_authority(
    tmp_path: Path, relative: str, expected: str
) -> None:
    schema_root = tmp_path / "schemas"
    shutil.copytree(EVA2_ROOT / "schemas", schema_root)
    target = schema_root / "portable" / "v1" / relative
    value = json.loads(target.read_bytes())
    value["p1_tamper"] = True
    target.write_bytes(canonical_json_bytes(value) + b"\n")
    assert hashlib.sha256(target.read_bytes()).hexdigest() != expected

    with pytest.raises(PortableArchiveError, match="pinned"):
        _schema_payloads(schema_root)


@pytest.mark.parametrize(
    "relative",
    (
        "schemas/contracts/manifest.json",
        "schemas/contracts/canonical-json-profile-v1.json",
    ),
)
def test_reader_rejects_unpinned_contract_authority(
    tmp_path: Path, relative: str
) -> None:
    reader = _reader_module()
    archive_root = tmp_path / "archive"
    for path, raw in _schema_payloads(EVA2_ROOT / "schemas").items():
        target = archive_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    target = archive_root / relative
    target.write_bytes(target.read_bytes() + b" ")

    with reader._root_descriptor(archive_root) as root_descriptor:
        with pytest.raises(reader.ArchiveValidationError, match="pinned"):
            reader._load_schema_registry(
                root_descriptor,
                {"json_schema_manifest_sha256": reader.CONTRACT_MANIFEST_SHA256},
            )


def test_reader_rejects_parent_symlink_swapped_after_root_open(
    tmp_path: Path,
) -> None:
    reader = _reader_module()
    archive_root = tmp_path / "archive"
    parent = archive_root / "records"
    parent.mkdir(parents=True)
    (parent / "one.jsonl").write_bytes(b"original")
    alternate = tmp_path / "alternate"
    alternate.mkdir()
    (alternate / "one.jsonl").write_bytes(b"replacement")

    with reader._root_descriptor(archive_root) as root_descriptor:
        parent.rename(archive_root / "original-records")
        parent.symlink_to(alternate, target_is_directory=True)
        with pytest.raises(reader.ArchiveValidationError, match="without links"):
            reader._read_regular(root_descriptor, "records/one.jsonl")


def test_reader_requires_exact_reachable_principal_and_binding_sets() -> None:
    reader = _reader_module()
    records = _terminal_records()
    reader._validate_reachable_tenant_closure(records)

    extra = copy.deepcopy(records["eva_core.principals"][0])
    extra["row"]["principal_id"] = "00000000-0000-0000-0000-000000000001"
    records["eva_core.principals"].append(extra)
    with pytest.raises(reader.ArchiveValidationError, match="principal closure"):
        reader._validate_reachable_tenant_closure(records)


def test_relationship_evidence_without_matching_binding_is_valid() -> None:
    reader = _reader_module()
    records = _terminal_records()
    binding_evidence = {
        record["row"]["verification_evidence_id"]
        for record in records["eva_core.principal_bindings"]
    }
    unrelated_evidence = next(
        record["row"]["evidence_id"]
        for record in records["eva_evidence.evidence_envelopes"]
        if record["row"]["evidence_id"] not in binding_evidence
    )
    records["eva_core.relationships"][0]["row"]["channel_binding_evidence_refs"].append(
        unrelated_evidence
    )

    reader._validate_reachable_tenant_closure(records)

    records = _terminal_records()
    extra_binding = copy.deepcopy(records["eva_core.principal_bindings"][0])
    extra_binding["row"]["principal_binding_id"] = (
        "00000000-0000-0000-0000-000000000001"
    )
    extra_binding["row"]["verification_evidence_id"] = records[
        "eva_evidence.evidence_envelopes"
    ][-1]["row"]["evidence_id"]
    records["eva_core.principal_bindings"].append(extra_binding)
    with pytest.raises(
        reader.ArchiveValidationError, match="principal_binding closure"
    ):
        reader._validate_reachable_tenant_closure(records)


def test_reader_record_paths_equal_the_sealed_catalog_in_ordinal_order() -> None:
    reader = _reader_module()
    expected = tuple(
        table["portableExport"]["recordJsonlRelativePath"]
        for table in CATALOG_SPEC["canonical_tables"]
    )

    assert len(expected) == 39
    assert reader.SEALED_RECORD_JSONL_PATHS == expected


def test_object_ref_payload_and_object_store_are_one_bound_authority() -> None:
    reader = _reader_module()
    records = _terminal_records()
    reader._validate_object_store_bindings(records)

    records["eva_evidence.object_refs"][0]["row"]["payload"]["mime_type"] = (
        "application/octet-stream"
    )
    with pytest.raises(reader.ArchiveValidationError, match="ObjectRef payload"):
        reader._validate_object_store_bindings(records)

    records = _terminal_records()
    records["eva_core.installations"][0]["row"]["object_store_payload"][
        "encryption_algorithm"
    ] = "other"
    with pytest.raises(reader.ArchiveValidationError, match="ObjectStore authority"):
        reader._validate_object_store_bindings(records)

    records = _terminal_records()
    records["eva_core.installations"][0]["row"]["object_store_payload"][
        "maximum_object_bytes"
    ] = 1
    records["eva_core.installations"][0]["row"]["object_store_payload_sha256"] = (
        hashlib.sha256(
            canonical_json_bytes(
                records["eva_core.installations"][0]["row"]["object_store_payload"]
            )
        ).hexdigest()
    )
    with pytest.raises(reader.ArchiveValidationError, match="ObjectStore differs"):
        reader._validate_object_store_bindings(records)


def test_absent_object_store_is_valid_only_without_object_refs() -> None:
    reader = _reader_module()
    records = _snapshot_records("empty")
    installation = records["eva_core.installations"][0]["row"]
    for field in (
        "object_store_id",
        "object_store_payload",
        "object_store_payload_sha256",
    ):
        installation[field] = None

    reader._validate_object_store_bindings(records)

    records["eva_evidence.object_refs"] = [
        copy.deepcopy(_terminal_records()["eva_evidence.object_refs"][0])
    ]
    with pytest.raises(reader.ArchiveValidationError, match="absent ObjectStore"):
        reader._validate_object_store_bindings(records)


@pytest.mark.parametrize(
    "missing_field",
    ("object_store_id", "object_store_payload", "object_store_payload_sha256"),
)
def test_partial_object_store_triple_is_rejected(missing_field: str) -> None:
    reader = _reader_module()
    records = _snapshot_records("empty")
    records["eva_core.installations"][0]["row"][missing_field] = None

    with pytest.raises(reader.ArchiveValidationError, match="all-null or all-present"):
        reader._validate_object_store_bindings(records)


@pytest.mark.parametrize("state", ("read_only", "unavailable"))
def test_contract_valid_nonactive_object_store_metadata_is_readable(state: str) -> None:
    reader = _reader_module()
    records = _terminal_records()
    installation = records["eva_core.installations"][0]["row"]
    installation["object_store_payload"]["state"] = state
    installation["object_store_payload_sha256"] = hashlib.sha256(
        canonical_json_bytes(installation["object_store_payload"])
    ).hexdigest()

    reader._validate_object_store_bindings(records)


def test_evidence_readback_keeps_crypto_shredded_object_metadata() -> None:
    reader = _reader_module()
    records = _terminal_records()
    object_record = records["eva_evidence.object_refs"][0]
    object_record["row"]["state"] = "crypto_shredded"
    object_record["row"]["erased_at"] = "2026-08-21T12:00:00Z"
    records["eva_evidence.evidence_envelopes"][0]["row"]["object_ref_id"] = (
        object_record["row"]["object_id"]
    )

    evidence = reader._derive_evidence(records)

    selected = [
        item
        for item in evidence
        if item["evidence_record"]["row"]["object_ref_id"]
        == object_record["row"]["object_id"]
    ]
    assert selected[0]["selected_object_ref_records"] == [object_record]


def test_episode_head_uses_composite_episode_event_identity() -> None:
    reader = _reader_module()
    records = _terminal_records()
    event = copy.deepcopy(records["eva_core.episode_events"][-1])
    collision_episode_id = "00000000-0000-0000-0000-000000000001"
    event["row"]["episode_id"] = collision_episode_id
    event["row"]["after_state"] = "planning"
    event["row"]["after_episode_state"]["episode_id"] = collision_episode_id
    event["row"]["after_episode_state"]["state"] = "planning"
    event["row"]["after_state_sha256"] = hashlib.sha256(
        canonical_json_bytes(event["row"]["after_episode_state"])
    ).hexdigest()
    event["row"]["before_episode_state"]["episode_id"] = collision_episode_id
    event["row"]["before_state_sha256"] = hashlib.sha256(
        canonical_json_bytes(event["row"]["before_episode_state"])
    ).hexdigest()
    records["eva_core.episode_events"].append(event)

    reader._validate_scope_and_heads(
        {
            "source_installation_id": records["eva_core.installations"][0]["row"][
                "installation_id"
            ]
        },
        records,
    )


def test_episode_readback_uses_declared_head_not_later_event() -> None:
    reader = _reader_module()
    records = _snapshot_records("planReserved")
    later_records = _snapshot_records("calendarPrepared")
    later_event = next(
        event
        for event in later_records["eva_core.episode_events"]
        if event["row"]["after_state"] == "executing"
    )
    records["eva_core.episode_events"].append(copy.deepcopy(later_event))
    manifest = {
        "source_installation_id": records["eva_core.installations"][0]["row"][
            "installation_id"
        ]
    }

    reader._validate_scope_and_heads(manifest, records)
    readback = reader._derive_episodes(records)

    state = readback[0]["current_episode_state"]
    assert state["state"] == "planning"
    assert (
        state["last_event_id"]
        == records["eva_core.episodes"][0]["row"]["last_event_id"]
    )


@pytest.mark.parametrize("field", ("tenant_id", "episode_id"))
def test_episode_before_state_binds_outer_scope(field: str) -> None:
    reader = _reader_module()
    records = _terminal_records()
    event = next(
        item
        for item in records["eva_core.episode_events"]
        if item["row"]["before_episode_state"] is not None
    )["row"]
    event["before_episode_state"][field] = "00000000-0000-0000-0000-000000000001"
    event["before_state_sha256"] = hashlib.sha256(
        canonical_json_bytes(event["before_episode_state"])
    ).hexdigest()

    with pytest.raises(reader.ArchiveValidationError, match="snapshot binding"):
        reader._validate_scope_and_heads(
            {
                "source_installation_id": records["eva_core.installations"][0]["row"][
                    "installation_id"
                ]
            },
            records,
        )


@pytest.mark.parametrize(
    "field", ("principal_id", "conversation_id", "ingress_request_id")
)
def test_episode_head_binds_outer_actor_and_context(field: str) -> None:
    reader = _reader_module()
    records = _terminal_records()
    episode = records["eva_core.episodes"][0]["row"]
    event = next(
        item["row"]
        for item in records["eva_core.episode_events"]
        if item["row"]["event_id"] == episode["last_event_id"]
    )
    event["after_episode_state"][field] = "00000000-0000-0000-0000-000000000001"
    digest = hashlib.sha256(
        canonical_json_bytes(event["after_episode_state"])
    ).hexdigest()
    event["after_state_sha256"] = digest
    episode["state_payload_sha256"] = digest

    with pytest.raises(reader.ArchiveValidationError, match="episode head scalars"):
        reader._validate_scope_and_heads(
            {
                "source_installation_id": records["eva_core.installations"][0]["row"][
                    "installation_id"
                ]
            },
            records,
        )


def test_schema_preflight_rejects_unknown_validation_and_checks_uuid_format() -> None:
    reader = _reader_module()
    reader._preflight_schema(
        {
            "$defs": {"Known": {"type": "string"}},
            "description": "annotation",
            "discriminator": {"propertyName": "contract"},
            "type": "object",
        },
        label="allowed",
    )
    with pytest.raises(reader.ArchiveValidationError, match="unsupported keyword"):
        reader._preflight_schema(
            {"type": "string", "futureValidationKeyword": 1}, label="unknown"
        )
    with pytest.raises(reader.ArchiveValidationError, match="canonical UUID"):
        reader._validate_schema(
            "not-a-uuid",
            {"type": "string", "format": "uuid"},
            {},
            {},
            path="uuid",
        )


def test_export_rejects_binding_without_same_installation_evidence() -> None:
    records = _terminal_records()
    records["eva_core.principal_bindings"][0]["row"]["verification_evidence_id"] = (
        "00000000-0000-0000-0000-000000000001"
    )

    with pytest.raises(PrincipalBindingIsolationError):
        validate_principal_binding_evidence_scope(records)


def test_export_rejects_matching_binding_evidence_from_other_installation() -> None:
    records = _terminal_records()
    binding = records["eva_core.principal_bindings"][0]["row"]
    evidence = next(
        record["row"]
        for record in records["eva_evidence.evidence_envelopes"]
        if record["row"]["evidence_id"] == binding["verification_evidence_id"]
    )
    evidence["installation_id"] = "00000000-0000-0000-0000-000000000001"

    with pytest.raises(PrincipalBindingIsolationError, match="same installation"):
        validate_principal_binding_evidence_scope(records)
