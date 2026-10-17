"""Operational entry point for one-installation portable archive export."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from tools.postgres_portable_export import (
    CANONICAL_EXPORT_SPECS,
    PostgresPortableSnapshot,
)
from eva.application.portable_archive import (
    PortableArchiveError,
    RecordSetBytes,
    build_checksums_bytes,
    canonical_json_bytes,
    compute_archive_id,
    discard_staging_tree,
    publish_directory_atomically,
    write_payload_tree,
)


_CAS_KEY = re.compile(r"^sha256/([0-9a-f]{2})/([0-9a-f]{64})\.eva2xo$")
CONTRACT_MANIFEST_SHA256 = (
    "fcf6655ccc61f9258b132620b02d51492c7eb19ce762b4e1728548b6fa2d53a5"
)
CANONICAL_PROFILE_SHA256 = (
    "d3cd8347c77de4a57c4ad7834b8ba170516b420c9abd5079dac1442d0a9d0ad9"
)
PORTABLE_MANIFEST_SHA256 = (
    "de2e302c8d260707563a8c16af1a6dfc2a3932507013dddb79d7780a3e36dc68"
)
READER_MANIFEST_SHA256 = (
    "5b26d3b51a556b4b214b4d2d1cd664fb5532064d5eb0c7c7db70e833c320e876"
)
OBJECT_SCHEMA_SHA256 = (
    "1e951976fa1021893bb5bc3812a98997560b7fcf636d0e69573e4444ec4930a8"
)
_PENDING_EPISODES = {
    "accepted",
    "planning",
    "executing",
    "reporting",
    "waiting_user",
    "waiting_external",
    "blocked",
}
_PENDING_EFFECTS = {
    "prepared",
    "dispatch_authorized",
    "waiting_breaker",
    "ambiguous",
    "not_observed",
    "manual_reconciliation",
    "blocked",
}
_PENDING_DELIVERIES = {
    "prepared",
    "dispatch_authorized",
    "provider_accepted",
    "ambiguous",
}


def _eva2_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _safe_source_relative_path(relative: str) -> tuple[str, ...]:
    if (
        not relative
        or relative.startswith("/")
        or "\\" in relative
        or any(ord(character) < 0x20 for character in relative)
    ):
        raise PortableArchiveError("source resource path is unsafe")
    parts = tuple(relative.split("/"))
    if any(not part or part in {".", ".."} for part in parts):
        raise PortableArchiveError("source resource path has an unsafe segment")
    return parts


@contextmanager
def _root_descriptor(root: Path, *, label: str) -> Iterator[int]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(str(root), flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise PortableArchiveError(f"{label} root is not a directory")
    except OSError as exc:
        raise PortableArchiveError(
            f"{label} root cannot be opened without links"
        ) from exc
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _read_regular_at(
    root_descriptor: int,
    relative: str,
    *,
    label: str,
    maximum: int | None = None,
) -> bytes:
    parts = _safe_source_relative_path(relative)
    read_flags = (
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    )
    directory_flags = read_flags | getattr(os, "O_DIRECTORY", 0)
    opened: list[int] = []
    parent = root_descriptor
    try:
        for part in parts[:-1]:
            parent = os.open(part, directory_flags, dir_fd=parent)
            opened.append(parent)
            if not stat.S_ISDIR(os.fstat(parent).st_mode):
                raise PortableArchiveError(f"{label} parent is not a directory")
        file_descriptor = os.open(parts[-1], read_flags, dir_fd=parent)
        opened.append(file_descriptor)
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise PortableArchiveError(f"{label} is not a single-link regular file")
        if maximum is not None and before.st_size > maximum:
            raise PortableArchiveError(f"{label} exceeds its byte limit")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(file_descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        content = b"".join(chunks)
        if identity_before != identity_after or len(content) != before.st_size:
            raise PortableArchiveError(f"{label} changed while being read")
        return content
    except OSError as exc:
        raise PortableArchiveError(f"{label} cannot be opened without links") from exc
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def _require_sha256(raw: bytes, expected: str, *, label: str) -> None:
    if hashlib.sha256(raw).hexdigest() != expected:
        raise PortableArchiveError(f"{label} differs from pinned authority")


def _schema_payloads(schema_root: Path | None = None) -> dict[str, bytes]:
    if schema_root is None:
        from eva.resources.portable_v1 import schema_resources
        with schema_resources() as packaged_root:
            return _schema_payloads(packaged_root)
    payloads: dict[str, bytes] = {}
    v1_contract_root = schema_root / "portable" / "v1" / "contracts"
    with (
        _root_descriptor(schema_root, label="schema") as root_descriptor,
        _root_descriptor(
            v1_contract_root, label="portable v1 contract schema"
        ) as v1_contract_descriptor,
    ):

        def include(
            local: str,
            archive: str,
            expected: str | None = None,
            *,
            source_descriptor: int = root_descriptor,
        ) -> bytes:
            raw = _read_regular_at(
                source_descriptor,
                local,
                label=f"schema resource {local}",
                maximum=16_777_216,
            )
            if expected is not None:
                _require_sha256(raw, expected, label=local)
            payloads[archive] = raw
            return raw

        contract_raw = include(
            "manifest.json",
            "schemas/contracts/manifest.json",
            CONTRACT_MANIFEST_SHA256,
            source_descriptor=v1_contract_descriptor,
        )
        profile_raw = include(
            "canonical-json-profile-v1.json",
            "schemas/contracts/canonical-json-profile-v1.json",
            CANONICAL_PROFILE_SHA256,
            source_descriptor=v1_contract_descriptor,
        )
        portable_raw = include(
            "portable/record-schema-manifest-v1.json",
            "schemas/portable/manifest.json",
            PORTABLE_MANIFEST_SHA256,
        )
        reader_raw = include(
            "portable/generic-reader-output-schema-manifest-v1.json",
            "schemas/readback/manifest.json",
            READER_MANIFEST_SHA256,
        )

        contract_manifest = _canonical_object(contract_raw, allow_trailing_lf=True)
        if contract_manifest.get("profileSha256") != CANONICAL_PROFILE_SHA256:
            raise PortableArchiveError("contract manifest profile pin differs")
        _canonical_object(profile_raw, allow_trailing_lf=True)
        for contract, expected_hash in contract_manifest["schemas"].items():
            name = f"{contract.replace('/', '-')}.schema.json"
            include(
                name,
                f"schemas/contracts/{name}",
                expected_hash,
                source_descriptor=v1_contract_descriptor,
            )

        portable_manifest = _canonical_object(portable_raw)
        portable_entries = portable_manifest.get("schemas")
        if not isinstance(portable_entries, list) or len(portable_entries) != 39:
            raise PortableArchiveError("portable schema resource count is not 39")
        for entry in portable_entries:
            archive_path = entry["recordSchemaRelativePath"]
            include(
                archive_path.removeprefix("schemas/"),
                archive_path,
                entry["record_schema_sha256"],
            )

        reader_manifest = _canonical_object(reader_raw)
        if (
            reader_manifest.get("portableRecordSchemaManifestSha256")
            != hashlib.sha256(portable_raw).hexdigest()
        ):
            raise PortableArchiveError("reader/portable schema authority differs")
        object_schema_sha256 = reader_manifest.get("objectManifestRecordSchemaSha256")
        if not isinstance(object_schema_sha256, str):
            raise PortableArchiveError("reader object schema authority is absent")
        if object_schema_sha256 != OBJECT_SCHEMA_SHA256:
            raise PortableArchiveError("reader object schema authority differs")
        include(
            "portable/object-manifest-record-v1.schema.json",
            "schemas/portable/object-manifest-record-v1.schema.json",
            object_schema_sha256,
        )
        reader_entries = reader_manifest.get("schemas")
        if not isinstance(reader_entries, list) or len(reader_entries) != 8:
            raise PortableArchiveError("reader schema resource count is not 8")
        for entry in reader_entries:
            archive_path = entry["schemaRelativePath"]
            local_path = "portable/readback/" + archive_path.removeprefix(
                "schemas/readback/"
            )
            include(local_path, archive_path, entry["record_schema_sha256"])
    return payloads


def _canonical_object(raw: bytes, *, allow_trailing_lf: bool = False) -> dict[str, Any]:
    try:
        value = json_loads_no_duplicates(raw)
    except (UnicodeError, ValueError) as exc:
        raise PortableArchiveError("sealed schema resource is invalid JSON") from exc
    suffix = b"\n" if allow_trailing_lf else b""
    if not isinstance(value, dict) or canonical_json_bytes(value) + suffix != raw:
        raise PortableArchiveError("sealed schema resource is not canonical JSON")
    return value


def json_loads_no_duplicates(raw: bytes) -> Any:
    import json

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique)


def _read_cas_file(root_descriptor: int, relative: str) -> bytes:
    match = _CAS_KEY.fullmatch(relative)
    if match is None or match.group(1) != match.group(2)[:2]:
        raise PortableArchiveError("ObjectRef physical_cas_key is not canonical")
    return _read_regular_at(
        root_descriptor, relative, label="CAS object", maximum=1 << 34
    )


def _records_by_table(
    record_sets: Sequence[RecordSetBytes],
) -> dict[str, tuple[dict[str, Any], ...]]:
    if len(record_sets) != 39:
        raise PortableArchiveError("portable archive requires 39 record sets")
    result = {}
    for ordinal, (record_set, expected) in enumerate(
        zip(record_sets, CANONICAL_EXPORT_SPECS, strict=True), start=1
    ):
        exact_metadata = (
            record_set.export_ordinal == ordinal == expected["export_ordinal"]
            and record_set.table == expected["table"]
            and record_set.contract == expected["record_contract"]
            and record_set.relative_path == expected["record_jsonl_relative_path"]
            and record_set.record_schema_relative_path
            == expected["record_schema_relative_path"]
            and record_set.record_schema_sha256 == expected["record_schema_sha256"]
        )
        if not exact_metadata or record_set.table in result:
            raise PortableArchiveError("portable record set authority differs")
        expected_content = b"".join(
            canonical_json_bytes(record) + b"\n" for record in record_set.records
        )
        if expected_content != record_set.content:
            raise PortableArchiveError("portable record set objects/content differ")
        result[record_set.table] = record_set.records
    return result


def _object_payloads(
    records: dict[str, tuple[dict[str, Any], ...]], object_blob_root: Path | None
) -> tuple[dict[str, bytes], list[dict[str, Any]]]:
    active = [
        record
        for record in records["eva_evidence.object_refs"]
        if record["row"]["state"] == "active"
    ]
    active.sort(
        key=lambda record: (
            record["row"]["ciphertext_sha256"].encode("utf-8"),
            UUID(record["row"]["object_id"]).bytes,
        )
    )
    if active and object_blob_root is None:
        raise PortableArchiveError("active ObjectRefs require an explicit CAS root")
    payloads: dict[str, bytes] = {}
    manifest_records = []
    if active:
        assert object_blob_root is not None
        with _root_descriptor(object_blob_root, label="CAS") as root_descriptor:
            for record in active:
                row = record["row"]
                digest = row["ciphertext_sha256"]
                relative = f"objects/sha256/{digest[:2]}/{digest}.eva2xo"
                content = _read_cas_file(root_descriptor, row["physical_cas_key"])
                if len(content) != row["envelope_byte_length"]:
                    raise PortableArchiveError("CAS envelope byte length differs")
                if hashlib.sha256(content).hexdigest() != digest:
                    raise PortableArchiveError("CAS envelope SHA-256 differs")
                payloads[relative] = content
                manifest_records.append(
                    {
                        "schema_version": 1,
                        "contract": "EvaEncryptedObjectManifestRecord/v1",
                        "tenant_id": row["tenant_id"],
                        "installation_id": row["installation_id"],
                        "object_id": row["object_id"],
                        "object_store_id": row["object_store_id"],
                        "ciphertext_sha256": digest,
                        "plaintext_sha256": row["plaintext_sha256"],
                        "plaintext_byte_length": row["plaintext_byte_length"],
                        "envelope_byte_length": row["envelope_byte_length"],
                        "mime_type": row["mime_type"],
                        "encryption_algorithm": row["encryption_algorithm"],
                        "wrapped_key_ref": row["wrapped_key_ref"],
                        "relative_path": relative,
                        "retention_until": row["retention_until"],
                        "object_ref_row_sha256": record["row_sha256"],
                    }
                )
    manifest_raw = b"".join(
        canonical_json_bytes(item) + b"\n" for item in manifest_records
    )
    payloads["objects/manifest-v1.jsonl"] = manifest_raw
    return payloads, manifest_records


def _pack_hashes(
    records: dict[str, tuple[dict[str, Any], ...]],
) -> tuple[list[str], list[str]]:
    if len(records["eva_core.installations"]) != 1:
        raise PortableArchiveError("export requires exactly one installation row")
    installation = records["eva_core.installations"][0]["row"]
    identity = {
        (record["row"]["identity_id"], record["row"]["pack_version"]): record["row"][
            "canonical_sha256"
        ]
        for record in records["eva_core.identity_pack_versions"]
    }
    policy = {
        (record["row"]["policy_id"], record["row"]["policy_version"]): record["row"][
            "canonical_sha256"
        ]
        for record in records["eva_core.policy_pack_versions"]
    }
    identity_refs: set[tuple[str, int]] = set()
    policy_refs: set[tuple[str, int]] = set()

    def add_selector(
        target: set[tuple[str, int]], identifier: Any, version: Any
    ) -> None:
        if identifier is None and version is None:
            return
        if (
            not isinstance(identifier, str)
            or isinstance(version, bool)
            or not isinstance(version, int)
        ):
            raise PortableArchiveError("pack selector is only partially populated")
        target.add((identifier, version))

    add_selector(
        identity_refs,
        installation["current_identity_id"],
        installation["current_identity_pack_version"],
    )
    add_selector(
        policy_refs,
        installation["current_policy_id"],
        installation["current_policy_pack_version"],
    )
    for record in records["eva_core.episode_generations"]:
        row = record["row"]
        add_selector(identity_refs, row["identity_id"], row["identity_pack_version"])
        add_selector(policy_refs, row["policy_id"], row["policy_pack_version"])
    for record in records["eva_core.messages"]:
        row = record["row"]
        add_selector(identity_refs, row["identity_id"], row["identity_pack_version"])
    try:
        return (
            sorted({identity[item] for item in identity_refs}),
            sorted({policy[item] for item in policy_refs}),
        )
    except KeyError as exc:
        raise PortableArchiveError(
            "referenced pack is absent from portable rows"
        ) from exc


def _archive_manifest(
    *,
    record_sets: Sequence[RecordSetBytes],
    records: dict[str, tuple[dict[str, Any], ...]],
    object_records: list[dict[str, Any]],
    object_manifest_raw: bytes,
    source_installation_id: str,
    export_sequence: int,
    created_at: str,
    checksums_sha256: str,
    contract_manifest_sha256: str,
    object_manifest_schema_sha256: str,
) -> dict[str, Any]:
    identity_hashes, _ = _pack_hashes(records)
    deadlines = [
        record["row"]["retention_until"]
        for table_records in records.values()
        for record in table_records
        if record["row"].get("retention_until") is not None
    ] + [record["retention_until"] for record in object_records]
    if not deadlines:
        raise PortableArchiveError("empty archive is forbidden")
    object_manifest_sha256 = hashlib.sha256(object_manifest_raw).hexdigest()
    chunks = (
        [
            {
                "relative_path": "objects/manifest-v1.jsonl",
                "record_schema_sha256": object_manifest_schema_sha256,
                "jsonl_sha256": object_manifest_sha256,
                "record_count": len(object_records),
            }
        ]
        if object_records
        else []
    )
    return {
        "schema_version": 1,
        "contract": "EvaPortableArchive/v1",
        "archive_id": compute_archive_id(
            source_installation_id=source_installation_id,
            export_sequence=export_sequence,
            checksums_sha256=checksums_sha256,
        ),
        "source_installation_id": source_installation_id,
        "export_sequence": export_sequence,
        "canonical_profile": "EvaCanonicalJSON/v1",
        "record_sets": [
            {
                "contract_type": item.contract,
                "relative_path": item.relative_path,
                "contract_schema_sha256": item.record_schema_sha256,
                "jsonl_sha256": item.jsonl_sha256,
                "record_count": item.record_count,
                "minimum_schema_version": 1,
                "maximum_schema_version": 1,
            }
            for item in record_sets
        ],
        "json_schema_manifest_sha256": contract_manifest_sha256,
        "identity_pack_sha256s": identity_hashes,
        "encrypted_object_manifest_chunks": chunks,
        "encrypted_object_count": len(object_records),
        "pending_state_in_record_sets": True,
        "pending_episode_count": sum(
            record["row"]["state"] in _PENDING_EPISODES
            for record in records["eva_core.episodes"]
        ),
        "pending_effect_intent_count": sum(
            record["row"]["state"] in _PENDING_EFFECTS
            for record in records["eva_effects.effect_intents"]
        ),
        "pending_delivery_count": sum(
            record["row"]["state"] in _PENDING_DELIVERIES
            for record in records["eva_effects.deliveries"]
        ),
        "checksums_sha256": checksums_sha256,
        "dbos_data_included": False,
        "credentials_included": False,
        "installation_master_key_included": False,
        "telemetry_included": False,
        "absolute_host_paths_included": False,
        "retention_until": min(deadlines),
        "created_at": created_at,
    }


def _standalone_validator(reader_path: Path) -> Callable[[Path], None]:
    def validate(staging: Path) -> None:
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(reader_path),
                str(staging),
                "--validate-only",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=environment,
        )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise PortableArchiveError(
                f"standalone generic reader rejected staged archive: {detail}"
            )

    return validate


def publish_portable_archive(
    *,
    record_sets: Sequence[RecordSetBytes],
    source_installation_id: str,
    export_sequence: int,
    created_at: str,
    destination: Path,
    object_blob_root: Path | None,
    schema_root: Path | None = None,
    validator: Callable[[Path], None] | None = None,
) -> dict[str, str]:
    """Create, fully validate, then atomically publish a portable directory."""

    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise PortableArchiveError("archive destination already exists")
    records = _records_by_table(record_sets)
    payloads = _schema_payloads(schema_root)
    for item in record_sets:
        if item.relative_path in payloads:
            raise PortableArchiveError("record path collides with schema payload")
        payloads[item.relative_path] = item.content
    object_payloads, object_records = _object_payloads(records, object_blob_root)
    payloads.update(object_payloads)
    checksums = build_checksums_bytes(payloads)
    checksums_sha256 = hashlib.sha256(checksums).hexdigest()
    manifest = _archive_manifest(
        record_sets=record_sets,
        records=records,
        object_records=object_records,
        object_manifest_raw=payloads["objects/manifest-v1.jsonl"],
        source_installation_id=source_installation_id,
        export_sequence=export_sequence,
        created_at=created_at,
        checksums_sha256=checksums_sha256,
        contract_manifest_sha256=hashlib.sha256(
            payloads["schemas/contracts/manifest.json"]
        ).hexdigest(),
        object_manifest_schema_sha256=hashlib.sha256(
            payloads["schemas/portable/object-manifest-record-v1.schema.json"]
        ).hexdigest(),
    )
    staging = destination.parent / f".{destination.name}.staging-{uuid4()}"
    try:
        write_payload_tree(staging, payloads)
        (staging / "checksums.sha256").write_bytes(checksums)
        (staging / "archive.json").write_bytes(canonical_json_bytes(manifest))
        required_validator = _standalone_validator(
            Path(__file__).with_name("read_eva_portable_archive.py")
        )

        def validate_before_publication(candidate: Path) -> None:
            if validator is not None:
                validator(candidate)
            required_validator(candidate)

        publish_directory_atomically(
            staging=staging,
            destination=destination,
            validator=validate_before_publication,
        )
    except BaseException:
        discard_staging_tree(staging)
        raise
    return {
        "archive_id": manifest["archive_id"],
        "archive_json_sha256": hashlib.sha256(
            canonical_json_bytes(manifest)
        ).hexdigest(),
        "checksums_sha256": checksums_sha256,
    }


def export_portable_archive(
    connection: Any,
    *,
    tenant_id: str,
    installation_id: str,
    export_sequence: int,
    created_at: str,
    destination: Path,
    object_blob_root: Path | None,
) -> dict[str, str]:
    """Read one PostgreSQL snapshot, then publish its validated archive."""

    with PostgresPortableSnapshot(
        connection,
        tenant_id=tenant_id,
        installation_id=installation_id,
    ) as snapshot:
        record_sets = snapshot.build_record_sets()
    return publish_portable_archive(
        record_sets=record_sets,
        source_installation_id=installation_id,
        export_sequence=export_sequence,
        created_at=created_at,
        destination=destination,
        object_blob_root=object_blob_root,
    )
