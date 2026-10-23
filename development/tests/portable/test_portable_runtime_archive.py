"""Focused v3 validation with real signed identity and no legacy pack rows."""
from contextlib import nullcontext
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from eva.application.identity_verifier import load_default_identity_pack_trust_binding, IdentityPackTrustBinding
from eva.application.portable_archive import canonical_json_bytes
from tools import portable_runtime_archive as archive
from tests.portable.test_portable_restore_full_schema import _exact_fixture_row


ROOT = Path(__file__).resolve().parents[3]


def runtime_rows():
    """Minimal real-column fixture preserving absent canonical pack references."""
    rows = {s["table"]: [] for s in archive.ALL_SPECS}
    rows["eva_core.tenants"] = [dict(_exact_fixture_row("static/tenant").values)]
    installation = dict(_exact_fixture_row("static/installation").values)
    for name in ("current_identity_id", "current_identity_pack_version",
                 "current_policy_id", "current_policy_pack_version"):
        if name in installation:
            installation[name] = None
    rows["eva_core.installations"] = [installation]
    return rows


class Connection:
    def begin(self):
        return nullcontext()

    def exec_driver_sql(self, sql):
        return self

    def scalar_one(self):
        return archive.SCHEMA_HEAD


@pytest.fixture
def runtime_archive(tmp_path, monkeypatch):
    rows = runtime_rows()

    class Snapshot:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read_all(self):
            return deepcopy(rows)

    monkeypatch.setattr(archive, "CurrentSnapshot", Snapshot)
    trust = load_default_identity_pack_trust_binding()
    identity = (ROOT / "src/eva/application/default_identity_pack.json").read_bytes()
    installation = rows["eva_core.installations"][0]
    arguments = dict(tenant_id=str(installation["tenant_id"]),
        installation_id=str(installation["installation_id"]),
        runtime_identity={"tenant_id": "runtime-test", "installation_id": "home"},
        destination=tmp_path / "archive", created_at="2026-09-29T12:00:00Z", export_sequence=1,
        runtime_identity_bytes=identity, identity_trust=trust)
    archive.export_runtime_archive(Connection(), **arguments)
    return arguments, rows


def test_null_legacy_identity_round_trip_with_external_trust(runtime_archive):
    args, rows = runtime_archive
    result = archive.read_runtime_archive(args["destination"], identity_trust=args["identity_trust"])
    assert result["runtime_identity_bytes"] == args["runtime_identity_bytes"]
    assert result["records"]["eva_core.identity_pack_versions"] == []
    assert result["records"]["eva_core.policy_pack_versions"] == []
    assert result["records"]["eva_core.installations"][0]["row"]["current_identity_id"] is None
    assert not (args["destination"] / "canonical").exists()
    assert result["manifest"]["contract"] == "EvaPortableArchive/v3"


def _rewrite(path, name, raw):
    import json
    (path / name).write_bytes(raw)
    manifest = json.loads((path / "archive.json").read_bytes())
    manifest["files"][name] = hashlib.sha256(raw).hexdigest()
    (path / "archive.json").write_bytes(canonical_json_bytes(manifest))


def test_identity_signature_rechecked_even_after_archive_rehash(runtime_archive):
    import json
    args, _ = runtime_archive
    identity = json.loads(args["runtime_identity_bytes"])
    identity["source_sha256"] = "0" * 64
    _rewrite(args["destination"], archive.IDENTITY_PATH, canonical_json_bytes(identity))
    with pytest.raises(ValueError, match="SHA-256"):
        archive.read_runtime_archive(args["destination"], identity_trust=args["identity_trust"])


def test_archive_cannot_supply_its_own_trust(runtime_archive):
    args, _ = runtime_archive
    trust = args["identity_trust"]
    other = b"a" * 32
    foreign = IdentityPackTrustBinding(trust.expected_identity_id, trust.expected_issuer,
                                      other, hashlib.sha256(other).hexdigest())
    with pytest.raises(ValueError, match="signature"):
        archive.read_runtime_archive(args["destination"], identity_trust=foreign)


@pytest.mark.parametrize("mutation", ["extra_file", "duplicate", "foreign_key", "wrong_scope"])
def test_exact_coverage_and_semantic_validation(runtime_archive, mutation):
    import json
    args, _ = runtime_archive
    path = args["destination"]
    spec = next(s for s in archive.ALL_SPECS if s["table"] == "eva_core.installations")
    name = spec["record_jsonl_relative_path"]
    raw = (path / name).read_bytes()
    if mutation == "extra_file":
        (path / "unlisted").write_text("unexpected")
    elif mutation == "duplicate":
        _rewrite(path, name, raw + raw)
    elif mutation == "wrong_scope":
        manifest = json.loads((path / "archive.json").read_bytes())
        manifest["source_tenant_id"] = "00000000-0000-0000-0000-000000000001"
        (path / "archive.json").write_bytes(canonical_json_bytes(manifest))
    else:
        row = json.loads(raw)
        row["row"]["current_identity_id"] = "00000000-0000-0000-0000-000000000001"
        row["row"]["current_identity_pack_version"] = 1
        row["row_sha256"] = hashlib.sha256(canonical_json_bytes(row["row"])).hexdigest()
        _rewrite(path, name, canonical_json_bytes(row) + b"\n")
    with pytest.raises(ValueError):
        archive.read_runtime_archive(path, identity_trust=args["identity_trust"])


def test_nonempty_objects_fail_without_publishing(runtime_archive):
    args, rows = runtime_archive
    rows["eva_evidence.object_refs"] = [{"not": "discarded"}]
    args = {**args, "destination": args["destination"].parent / "unsupported"}
    with pytest.raises(ValueError, match="object_refs are unsupported"):
        archive.export_runtime_archive(Connection(), **args)
    assert not args["destination"].exists()


@pytest.mark.parametrize("delegated", [False, True])
def test_nonempty_attention_reuses_current_and_legacy_contracts(runtime_archive, delegated):
    from tests.portable.test_portable_export_end_to_end import _fixture_record_sets
    args, _ = runtime_archive
    sets, _, _ = _fixture_record_sets("planAmbiguousWaiting")
    for record_set in sets:
        records = deepcopy(record_set.records)
        if record_set.table in {"eva_core.model_attempts", "eva_core.connector_bindings",
                                "eva_core.capability_manifests", "eva_core.capability_activations"}:
            # Historical fixture grants use logical IDs, not restorable DB IDs.
            # This test retains the waiting episode without that unrelated grant.
            records = ()
        if delegated and record_set.table == "eva_core.attention_items":
            for record in records:
                row = record["row"]
                row["kind"] = "delegated_work"
                row["payload"].update(kind="delegated_work", contract="AttentionWake/v2", schema_version=2)
                row["payload_sha256"] = hashlib.sha256(canonical_json_bytes(row["payload"])).hexdigest()
                record["row_sha256"] = hashlib.sha256(canonical_json_bytes(row)).hexdigest()
        _rewrite(args["destination"], record_set.relative_path,
                 b"".join(canonical_json_bytes(record) + b"\n" for record in records))
    result = archive.read_runtime_archive(args["destination"], identity_trust=args["identity_trust"])
    attention = result["records"]["eva_core.attention_items"]
    assert len(attention) == 1
    assert attention[0]["row"]["kind"] == ("delegated_work" if delegated else "ingress")
    if delegated:
        record = attention[0]
        record["row"]["payload"]["state"] = "invalid"
        record["row"]["payload_sha256"] = hashlib.sha256(canonical_json_bytes(record["row"]["payload"])).hexdigest()
        record["row_sha256"] = hashlib.sha256(canonical_json_bytes(record["row"])).hexdigest()
        spec = next(s for s in archive.ALL_SPECS if s["table"] == "eva_core.attention_items")
        _rewrite(args["destination"], spec["record_jsonl_relative_path"], canonical_json_bytes(record) + b"\n")
        with pytest.raises(ValueError):
            archive.read_runtime_archive(args["destination"], identity_trust=args["identity_trust"])


class SchemaResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value

    def fetchone(self):
        return (self.value,)


class SchemaConnection(Connection):
    def __init__(self, head, *, index_valid=True):
        self.head, self.index_valid, self.queries = head, index_valid, []

    def exec_driver_sql(self, sql, parameters=()):
        self.queries.append(sql)
        if "version_num" in sql:
            value = self.head
        elif "pg_catalog.pg_index" in sql:
            value = self.index_valid
        elif "SHOW transaction_isolation" in sql:
            value = "serializable"
        elif "pg_advisory_xact_lock" in sql:
            value = None
        elif "to_regclass" in sql:
            value = True
        elif "SELECT COUNT(*)" in sql:
            value = 0
        else:
            raise AssertionError("Unexpected schema-interface SQL")
        return SchemaResult(value)


def test_0011_export_and_reader_bind_real_head_and_index_profile(runtime_archive, tmp_path):
    import json
    args, _ = runtime_archive
    current = dict(args, destination=tmp_path / "current0011")
    connection = SchemaConnection(archive.CURRENT_SCHEMA_HEAD)
    manifest = archive.export_runtime_archive(connection, **current)
    checked = archive.read_runtime_archive(current["destination"], identity_trust=args["identity_trust"])
    profile = json.loads((current["destination"] / "profile.json").read_bytes())
    assert manifest["schema_head"] == checked["manifest"]["schema_head"] == archive.CURRENT_SCHEMA_HEAD
    assert profile["required_indexes"][0]["columns"] == ["tenant_id", "installation_id", "event_type", "occurred_at"]
    assert manifest["profile_sha256"] != archive.PROFILE_SHA256
    assert archive.PROFILE["schema_head"] == archive.SCHEMA_HEAD == "4e_0010_memory_compaction"
    assert any("i.indisvalid AND i.indisready" in query for query in connection.queries)
    assert any("i.indnkeyatts = 4 AND i.indnatts = 4" in query for query in connection.queries)


@pytest.mark.parametrize("head,index_valid,reason", [
    ("4e_0012_conversation_source_lock", True, "unsupported"),
    (archive.CURRENT_SCHEMA_HEAD, False, "pinned shape"),
    (archive.CURRENT_SCHEMA_HEAD, None, "pinned shape"),
])
def test_unknown_head_or_unusable_0011_index_never_publishes(runtime_archive, tmp_path, head, index_valid, reason):
    args, _ = runtime_archive
    current = dict(args, destination=tmp_path / "rejected")
    with pytest.raises(ValueError, match=reason):
        archive.export_runtime_archive(SchemaConnection(head, index_valid=index_valid), **current)
    assert not current["destination"].exists()


def test_0011_archive_cannot_substitute_legacy_or_different_index_pin(runtime_archive, tmp_path):
    import json
    args, _ = runtime_archive
    current = dict(args, destination=tmp_path / "pin0011")
    archive.export_runtime_archive(SchemaConnection(archive.CURRENT_SCHEMA_HEAD), **current)
    profile = json.loads((current["destination"] / "profile.json").read_bytes())
    profile["required_indexes"][0]["columns"][2:] = ["occurred_at", "event_type"]
    _rewrite(current["destination"], "profile.json", canonical_json_bytes(profile))
    manifest_path = current["destination"] / "archive.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["profile_sha256"] = hashlib.sha256(canonical_json_bytes(profile)).hexdigest()
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    with pytest.raises(ValueError, match="profile differs"):
        archive.read_runtime_archive(current["destination"], identity_trust=args["identity_trust"])
