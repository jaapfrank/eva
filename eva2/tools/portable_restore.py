"""Fail-closed decoding boundary for future Eva portable database restore.

Portable archives are deliberately not SQL dumps: connector and credential
fields are redacted before export.  This module only reconstructs rows whose
sealed transform is entirely column-preserving.  Callers must send every other
row through the separate re-authorisation path; they may never guess a secret
or revive an old provider attempt.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from datetime import datetime
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

from eva.application.portable_archive import PortableArchiveError, canonical_json_bytes
from eva.application.portable_objects import MaterializedPortableObject
from eva.operations.portable_restore_authority import authorize_portable_restore
from eva.contracts.execution import (
    CapabilityGrant,
    ConnectorBinding,
    CredentialGrant,
    EffectAttempt,
    ReadAttempt,
)
from eva.contracts.planning import ModelAttempt


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONNECTOR_BINDING_TABLE = "eva_core.connector_bindings"
_MODEL_ATTEMPT_TABLE = "eva_core.model_attempts"
_READ_ATTEMPT_TABLE = "eva_effects.read_attempts"
_EFFECT_ATTEMPT_TABLE = "eva_effects.effect_attempts"
_OBJECT_REF_TABLE = "eva_evidence.object_refs"


class PortableRestoreError(PortableArchiveError):
    """A portable record is malformed or cannot safely become a database row."""


def _one_value(result: Any, *, label: str) -> Any:
    row = result.fetchone()
    if row is None:
        raise PortableRestoreError(f"restore target {label} returned no value")
    if isinstance(row, Mapping):
        if len(row) != 1:
            raise PortableRestoreError(f"restore target {label} returned extra values")
        return next(iter(row.values()))
    if hasattr(row, "_mapping"):
        values = tuple(row._mapping.values())
    else:
        values = tuple(row)
    if len(values) != 1:
        raise PortableRestoreError(f"restore target {label} returned extra values")
    return values[0]


def _execute(connection: Any, sql: str, params: tuple[Any, ...] | None = None) -> Any:
    execute = getattr(connection, "exec_driver_sql", None)
    if callable(execute):
        return execute(sql) if params is None else execute(sql, params)
    return connection.execute(sql) if params is None else connection.execute(sql, params)


def _portable_row(record: Mapping[str, Any], *, table: str) -> dict[str, Any]:
    if set(record) != {
        "contract",
        "primary_key",
        "row",
        "row_sha256",
        "schema_version",
        "source_table",
    }:
        raise PortableRestoreError("portable record has an unexpected shape")
    if record.get("schema_version") != 1 or record.get("source_table") != table:
        raise PortableRestoreError("portable record table or schema version differs")
    row = record.get("row")
    if not isinstance(row, dict):
        raise PortableRestoreError("portable record row is not an object")
    row_bytes = canonical_json_bytes(row)
    if record.get("row_sha256") != hashlib.sha256(row_bytes).hexdigest():
        raise PortableRestoreError("portable record row hash differs")
    return row


def _require_exact_properties(
    value: Mapping[str, Any], expected: Sequence[str], *, label: str
) -> None:
    """Require exactly the sealed names, never an incidental JSON key order.

    Portable records are canonical JSON and are deliberately emitted with
    lexicographically sorted object keys. Their Python insertion order after a
    read therefore cannot be the database column order. The transform mapping
    remains the sole authority for output order; this guard protects the exact
    field set instead.
    """

    if len(set(expected)) != len(expected) or set(value) != set(expected):
        raise PortableRestoreError(f"portable {label} properties differ from sealed set")


def _materialized_object_index(
    materialized_objects: Sequence[MaterializedPortableObject] | None,
    *,
    object_store_root: Path | None,
) -> dict[tuple[str, str, str], MaterializedPortableObject]:
    """Accept only one exact, completed target-CAS proof per active record."""

    if materialized_objects is None:
        if object_store_root is not None:
            raise PortableRestoreError("object store root requires materialized object proof")
        return {}
    if isinstance(materialized_objects, (str, bytes)) or not isinstance(
        materialized_objects, Sequence
    ):
        raise PortableRestoreError("materialized object proof set is invalid")
    if not isinstance(object_store_root, Path):
        raise PortableRestoreError("materialized object proof requires target object root")
    result: dict[tuple[str, str, str], MaterializedPortableObject] = {}
    for item in materialized_objects:
        if not isinstance(item, MaterializedPortableObject):
            raise PortableRestoreError("materialized object proof is invalid")
        key = (item.object_id, item.ciphertext_sha256, item.record_row_sha256)
        if (
            item.target_object_root != object_store_root
            or item.status not in {"created", "already_same"}
            or item.relative_path != _object_relative_path(item.ciphertext_sha256)
            or key in result
        ):
            raise PortableRestoreError("materialized object proof is invalid")
        result[key] = item
    return result


def _object_relative_path(ciphertext_sha256: str) -> Path:
    if not isinstance(ciphertext_sha256, str) or _SHA256.fullmatch(ciphertext_sha256) is None:
        raise PortableRestoreError("materialized object ciphertext is invalid")
    return Path(
        "sha256",
        ciphertext_sha256[:2],
        f"{ciphertext_sha256}.eva2xo",
    )


def _require_materialized_object_ref(
    record: Mapping[str, Any],
    restored: Mapping[str, Any],
    *,
    materialized_index: Mapping[tuple[str, str, str], MaterializedPortableObject],
    consumed: set[tuple[str, str, str]],
) -> None:
    """Bind an ObjectRef row to exactly the target CAS result for its archive row."""

    state = restored.get("state")
    if state == "crypto_shredded":
        return
    if state != "active":
        raise PortableRestoreError("portable ObjectRef state is invalid")
    row_sha256 = record.get("row_sha256")
    object_id = restored.get("object_id")
    digest = restored.get("ciphertext_sha256")
    if not all(isinstance(value, str) for value in (row_sha256, object_id, digest)):
        raise PortableRestoreError("portable ObjectRef materialization key is invalid")
    key = (object_id, digest, row_sha256)
    if key not in materialized_index:
        raise PortableRestoreError("portable restore requires verified object CAS materialization")
    consumed.add(key)


def _spec_for_table(table_specs: tuple[Mapping[str, Any], ...], table: str) -> Mapping[str, Any]:
    matches = [item for item in table_specs if item.get("table") == table]
    if len(matches) != 1:
        raise PortableRestoreError("restore table is not uniquely sealed")
    return matches[0]


def decode_column_preserving_record(
    record: Mapping[str, Any],
    *,
    table: str,
    table_specs: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    """Return exact database columns only when no exported field was redacted.

    The returned values are still portable JSON values.  A PostgreSQL adapter
    owns the final typed binding and target transaction; this boundary makes
    sure that adapter never receives an invented credential field.
    """

    spec = _spec_for_table(table_specs, table)
    expected_contract = spec.get("record_contract")
    if record.get("contract") != expected_contract:
        raise PortableRestoreError("portable record contract differs from table pin")
    row = _portable_row(record, table=table)
    transform = spec.get("transform_spec")
    if not isinstance(transform, Mapping):
        raise PortableRestoreError("restore transform is absent")
    mappings = transform.get("propertyMappings")
    if not isinstance(mappings, list):
        raise PortableRestoreError("restore transform mappings are absent")

    expected_properties = tuple(transform.get("portablePropertyOrder", ()))
    _require_exact_properties(row, expected_properties, label="row")

    restored: dict[str, Any] = {}
    for mapping in mappings:
        if not isinstance(mapping, Mapping):
            raise PortableRestoreError("restore transform mapping is invalid")
        property_name = mapping.get("portableProperty")
        source = mapping.get("source")
        if not isinstance(property_name, str) or not isinstance(source, Mapping):
            raise PortableRestoreError("restore transform mapping is incomplete")
        if source.get("kind") != "column":
            raise PortableRestoreError(
                "portable row contains a redacted or derived field and requires "
                "the dedicated re-authorisation restore path"
            )
        column = source.get("column")
        if not isinstance(column, str) or column in restored:
            raise PortableRestoreError("restore transform source column is invalid")
        restored[column] = row[property_name]

    expected_columns = tuple(spec.get("database_column_order", ()))
    if tuple(restored) != expected_columns:
        raise PortableRestoreError("restored database columns differ from table pin")
    primary_key = record.get("primary_key")
    if not isinstance(primary_key, Mapping):
        raise PortableRestoreError("portable primary key shape differs from table pin")
    _require_exact_properties(
        primary_key, tuple(spec.get("primary_key", ())), label="primary key"
    )
    if any(restored[column] != value for column, value in primary_key.items()):
        raise PortableRestoreError("portable primary key differs from restored row")
    return restored


def decode_reauthorization_required_connector_binding(
    record: Mapping[str, Any],
    *,
    table_specs: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    """Replace a redacted connector with a provably unusable re-link record.

    A portable connector record retains the binding's non-secret identity and
    the hash of the old secret descriptor, but deliberately has no credential.
    This decoder creates a new deterministic *sentinel* descriptor solely so
    the existing closed ConnectorBinding/v1 schema can represent that state.
    It is unrelated to the former descriptor and the database/payload status is
    always ``reauthorization_required``. Existing action-chain validators only
    dispatch a binding whose stored status is ``verified``.
    """

    table = _CONNECTOR_BINDING_TABLE
    spec = _spec_for_table(table_specs, table)
    if record.get("contract") != spec.get("record_contract"):
        raise PortableRestoreError("portable connector record contract differs")
    row = _portable_row(record, table=table)
    transform = spec.get("transform_spec")
    mappings = transform.get("propertyMappings") if isinstance(transform, Mapping) else None
    expected_properties = (
        tuple(transform.get("portablePropertyOrder", ()))
        if isinstance(transform, Mapping)
        else ()
    )
    if not isinstance(mappings, list):
        raise PortableRestoreError("portable connector record properties differ")
    _require_exact_properties(row, expected_properties, label="connector row")
    if (
        row.get("credential_reference") is not None
        or row.get("credential_reauthorization_required") is not True
        or row.get("restore_status") != "reauthorization_required"
    ):
        raise PortableRestoreError("portable connector redaction is invalid")
    redacted_payload = row.get("payload")
    if not isinstance(redacted_payload, dict):
        raise PortableRestoreError("portable connector redacted payload is invalid")
    redacted_payload_sha256 = row.get("redacted_payload_sha256")
    if (
        not isinstance(redacted_payload_sha256, str)
        or not _SHA256.fullmatch(redacted_payload_sha256)
        or hashlib.sha256(canonical_json_bytes(redacted_payload)).hexdigest()
        != redacted_payload_sha256
    ):
        raise PortableRestoreError("portable connector redacted payload hash differs")
    if (
        redacted_payload.get("contract") != "EvaRedactedConnectorBinding/v1"
        or redacted_payload.get("source_contract") != "ConnectorBinding/v1"
        or redacted_payload.get("credential_reference") is not None
        or redacted_payload.get("credential_reauthorization_required") is not True
        or redacted_payload.get("credential_reference_sha256")
        != row.get("credential_reference_sha256")
    ):
        raise PortableRestoreError("portable connector redaction contract differs")
    source_payload_sha256 = row.get("source_payload_sha256")
    if (
        not isinstance(source_payload_sha256, str)
        or not _SHA256.fullmatch(source_payload_sha256)
    ):
        raise PortableRestoreError("portable connector source payload hash differs")

    binding_id = _canonical_uuid(row.get("binding_id"))
    if binding_id is None:
        raise PortableRestoreError("portable connector binding ID is absent")
    sentinel_reference = f"portable-restore-redacted:{binding_id}"
    payload = {
        key: value
        for key, value in redacted_payload.items()
        if key
        not in {
            "source_contract",
            "credential_reference_sha256",
            "credential_reauthorization_required",
        }
    }
    payload["contract"] = "ConnectorBinding/v1"
    payload["credential_reference"] = sentinel_reference
    payload["status"] = "reauthorization_required"
    try:
        restored_payload = ConnectorBinding.model_validate(payload).model_dump(mode="json")
    except ValueError as error:
        raise PortableRestoreError("portable connector payload cannot be re-linked") from error

    # The redacted contract is still allowed to retain only the metadata that
    # describes a connector.  Bind every such value back to its accompanying
    # database column so a syntactically valid, but internally inconsistent,
    # archive cannot silently change a connector's owner or capabilities.
    payload_column_pairs = {
        "tenant_id": "tenant_id",
        "installation_id": "installation_id",
        "binding_id": "binding_id",
        "owner_principal_id": "owner_principal_id",
        "provider_id": "provider_id",
        "provider_account_id": "provider_account_id",
        "provider_resource_id": "provider_resource_id",
        "confirmed_operations": "confirmed_operations",
        "confirmed_scopes": "confirmed_scopes",
        "verification_evidence_ref": "verification_evidence_id",
        "verified_at": "verified_at",
    }
    if any(
        restored_payload[payload_name] != row[column_name]
        for payload_name, column_name in payload_column_pairs.items()
    ):
        raise PortableRestoreError("portable connector payload and row differ")

    expected_columns = tuple(spec.get("database_column_order", ()))
    restored: dict[str, Any] = {}
    for mapping in mappings:
        if not isinstance(mapping, Mapping):
            raise PortableRestoreError("portable connector transform mapping is invalid")
        source = mapping.get("source")
        property_name = mapping.get("portableProperty")
        if not isinstance(source, Mapping) or not isinstance(property_name, str):
            raise PortableRestoreError("portable connector transform mapping is incomplete")
        source_kind = source.get("kind")
        if source_kind == "column":
            column = source.get("column")
            if not isinstance(column, str):
                raise PortableRestoreError("portable connector source column is invalid")
            if column == "credential_reference":
                restored[column] = sentinel_reference
            elif column == "status":
                restored[column] = "reauthorization_required"
            elif column == "payload":
                restored[column] = restored_payload
            elif column == "payload_sha256":
                restored[column] = hashlib.sha256(
                    canonical_json_bytes(restored_payload)
                ).hexdigest()
            else:
                restored[column] = row[property_name]
        elif source_kind == "literal" and property_name == "credential_reference":
            restored["credential_reference"] = sentinel_reference
        elif source_kind == "redactedContractColumn" and property_name == "payload":
            restored["payload"] = restored_payload
        elif (
            source_kind == "literal"
            and property_name in {"credential_reauthorization_required", "restore_status"}
        ) or source_kind in {"sha256Utf8Column", "sha256CanonicalPortableProperty"}:
            continue
        else:
            raise PortableRestoreError("portable connector transform kind is unknown")
    if tuple(restored) != expected_columns:
        raise PortableRestoreError("re-linked connector columns differ from table pin")
    primary_key = record.get("primary_key")
    if not isinstance(primary_key, Mapping):
        raise PortableRestoreError("portable connector primary key shape differs")
    _require_exact_properties(
        primary_key, tuple(spec.get("primary_key", ())), label="connector primary key"
    )
    if any(restored[column] != value for column, value in primary_key.items()):
        raise PortableRestoreError("portable connector primary key differs")
    return restored


def decode_ambiguous_model_attempt(
    record: Mapping[str, Any], *, table_specs: tuple[Mapping[str, Any], ...]
) -> dict[str, Any]:
    """Keep a redacted historical model call without preserving a usable grant.

    A restored model attempt is never a candidate for replay.  Its old provider
    result is deliberately reduced to ``ambiguous`` and its grant receives a
    deterministic non-secret descriptor.  This preserves the audit identity
    while making any old call impossible to resume.
    """

    spec = _spec_for_table(table_specs, _MODEL_ATTEMPT_TABLE)
    if record.get("contract") != spec.get("record_contract"):
        raise PortableRestoreError("portable model attempt record contract differs")
    row = _portable_row(record, table=_MODEL_ATTEMPT_TABLE)
    transform = spec.get("transform_spec")
    mappings = transform.get("propertyMappings") if isinstance(transform, Mapping) else None
    expected_properties = tuple(transform.get("portablePropertyOrder", ())) if isinstance(transform, Mapping) else ()
    if not isinstance(mappings, list):
        raise PortableRestoreError("portable model attempt properties differ")
    _require_exact_properties(row, expected_properties, label="model attempt row")
    if row.get("credential_reauthorization_required") is not True:
        raise PortableRestoreError("portable model attempt lacks a redacted credential grant")
    redacted_grant = row.get("credential_grant_payload")
    if not isinstance(redacted_grant, dict):
        raise PortableRestoreError("portable model attempt redacted grant is invalid")
    grant_id = _canonical_uuid(row.get("credential_grant_id"))
    if grant_id is None:
        raise PortableRestoreError("portable model attempt credential grant ID is absent")
    if (
        redacted_grant.get("contract") != "EvaRedactedCredentialGrant/v1"
        or redacted_grant.get("source_contract") != "CredentialGrant/v1"
        or redacted_grant.get("secret_descriptor_id") is not None
        or redacted_grant.get("credential_reauthorization_required") is not True
        or not isinstance(redacted_grant.get("secret_descriptor_id_sha256"), str)
        or not _SHA256.fullmatch(redacted_grant["secret_descriptor_id_sha256"])
    ):
        raise PortableRestoreError("portable model attempt grant redaction differs")
    grant_payload = {
        key: value
        for key, value in redacted_grant.items()
        if key not in {"source_contract", "secret_descriptor_id_sha256", "credential_reauthorization_required"}
    }
    grant_payload["contract"] = "CredentialGrant/v1"
    grant_payload["secret_descriptor_id"] = f"portable-restore-redacted:{grant_id}"
    try:
        grant = CredentialGrant.model_validate(grant_payload).model_dump(mode="json")
    except ValueError as error:
        raise PortableRestoreError("portable model attempt grant cannot be re-linked") from error
    if (
        grant["credential_grant_id"] != grant_id
        or grant["model_provider_profile_id"] != row.get("provider_profile_id")
        or grant["nonce"] != row.get("credential_grant_nonce")
        or grant["deadline"] != row.get("credential_grant_deadline")
    ):
        raise PortableRestoreError("portable model attempt grant and row differ")

    source_payload = row.get("payload")
    if not isinstance(source_payload, dict):
        raise PortableRestoreError("portable model attempt payload is invalid")
    attempt_payload = dict(source_payload)
    attempt_payload.update(
        state="ambiguous",
        response_sha256=None,
        credential_grant_id=grant_id,
    )
    try:
        attempt = ModelAttempt.model_validate(attempt_payload).model_dump(mode="json")
    except ValueError as error:
        raise PortableRestoreError("portable model attempt cannot become terminal") from error
    direct_pairs = {
        "attempt_id": "attempt_id", "episode_id": "episode_id", "generation": "generation",
        "purpose": "purpose", "ordinal": "ordinal",
        "provider_profile_id": "provider_profile_id", "request_sha256": "request_sha256",
        "maximum_input_tokens": "maximum_input_tokens", "maximum_output_tokens": "maximum_output_tokens",
        "timeout_ms": "timeout_ms", "input_tokens_used": "input_tokens_used",
        "output_tokens_used": "output_tokens_used", "provider_cost_euro_micros": "provider_cost_euro_micros",
        "created_at": "created_at",
    }
    if any(attempt[key] != row[value] for key, value in direct_pairs.items()):
        raise PortableRestoreError("portable model attempt payload and row differ")

    restored: dict[str, Any] = {}
    for mapping in mappings:
        source, property_name = mapping.get("source"), mapping.get("portableProperty")
        if not isinstance(source, Mapping) or not isinstance(property_name, str):
            raise PortableRestoreError("portable model attempt transform mapping is incomplete")
        kind = source.get("kind")
        if kind == "column":
            column = source.get("column")
            if not isinstance(column, str):
                raise PortableRestoreError("portable model attempt source column is invalid")
            if column == "state":
                restored[column] = "ambiguous"
            elif column == "response_sha256":
                restored[column] = None
            elif column == "credential_grant_payload":
                restored[column] = grant
            elif column == "credential_grant_sha256":
                restored[column] = hashlib.sha256(canonical_json_bytes(grant)).hexdigest()
            elif column == "provider_reference":
                restored[column] = None
            elif column == "payload":
                restored[column] = attempt
            elif column == "payload_sha256":
                restored[column] = hashlib.sha256(
                    canonical_json_bytes(attempt)
                ).hexdigest()
            else:
                restored[column] = row[property_name]
        elif kind == "redactedContractColumn" and property_name == "credential_grant_payload":
            restored["credential_grant_payload"] = grant
        elif kind == "credentialGrantPresence":
            continue
        else:
            raise PortableRestoreError("portable model attempt transform kind is unknown")
    if tuple(restored) != tuple(spec.get("database_column_order", ())):
        raise PortableRestoreError("re-linked model attempt columns differ from table pin")
    return restored


def _decode_terminal_attempt(
    record: Mapping[str, Any],
    *,
    table_specs: tuple[Mapping[str, Any], ...],
    table: str,
    label: str,
    terminal_states: frozenset[str],
    attempt_type: type[ReadAttempt] | type[EffectAttempt],
    direct_pairs: Mapping[str, str],
) -> dict[str, Any]:
    spec = _spec_for_table(table_specs, table)
    if record.get("contract") != spec.get("record_contract"):
        raise PortableRestoreError(f"portable {label} attempt record contract differs")
    row = _portable_row(record, table=table)
    transform = spec.get("transform_spec")
    mappings = transform.get("propertyMappings") if isinstance(transform, Mapping) else None
    expected_properties = (
        tuple(transform.get("portablePropertyOrder", ()))
        if isinstance(transform, Mapping)
        else ()
    )
    if not isinstance(mappings, list):
        raise PortableRestoreError(f"portable {label} attempt properties differ")
    _require_exact_properties(row, expected_properties, label=f"{label} attempt row")
    if row.get("credential_reauthorization_required") is not True:
        raise PortableRestoreError(
            f"portable {label} attempt lacks a redacted credential grant"
        )
    if row.get("state") not in terminal_states:
        raise PortableRestoreError(f"portable {label} attempt is still executable")

    capability_payload = row.get("capability_grant_payload")
    if not isinstance(capability_payload, dict):
        raise PortableRestoreError(
            f"portable {label} attempt capability grant is invalid"
        )
    try:
        capability_grant = CapabilityGrant.model_validate(capability_payload).model_dump(
            mode="json"
        )
    except ValueError as error:
        raise PortableRestoreError(
            f"portable {label} attempt capability grant is invalid"
        ) from error
    capability_hash = hashlib.sha256(canonical_json_bytes(capability_grant)).hexdigest()
    if (
        capability_hash != row.get("capability_grant_sha256")
        or capability_grant["grant_id"] != row.get("capability_grant_id")
        or capability_grant["nonce"] != row.get("capability_grant_nonce")
        or capability_grant["deadline"] != row.get("capability_grant_deadline")
    ):
        raise PortableRestoreError(
            f"portable {label} attempt capability grant and row differ"
        )

    redacted_grant = row.get("credential_grant_payload")
    if not isinstance(redacted_grant, dict):
        raise PortableRestoreError(f"portable {label} attempt redacted grant is invalid")
    grant_id = _canonical_uuid(row.get("credential_grant_id"))
    if grant_id is None:
        raise PortableRestoreError(
            f"portable {label} attempt credential grant ID is absent"
        )
    if (
        redacted_grant.get("contract") != "EvaRedactedCredentialGrant/v1"
        or redacted_grant.get("source_contract") != "CredentialGrant/v1"
        or redacted_grant.get("secret_descriptor_id") is not None
        or redacted_grant.get("credential_reauthorization_required") is not True
        or not isinstance(redacted_grant.get("secret_descriptor_id_sha256"), str)
        or not _SHA256.fullmatch(redacted_grant["secret_descriptor_id_sha256"])
    ):
        raise PortableRestoreError(f"portable {label} attempt grant redaction differs")
    grant_payload = {
        key: value
        for key, value in redacted_grant.items()
        if key
        not in {
            "source_contract",
            "secret_descriptor_id_sha256",
            "credential_reauthorization_required",
        }
    }
    grant_payload["contract"] = "CredentialGrant/v1"
    grant_payload["secret_descriptor_id"] = f"portable-restore-redacted:{grant_id}"
    try:
        credential_grant = CredentialGrant.model_validate(grant_payload).model_dump(
            mode="json"
        )
    except ValueError as error:
        raise PortableRestoreError(
            f"portable {label} attempt grant cannot be re-linked"
        ) from error
    if (
        credential_grant["credential_grant_id"] != grant_id
        or credential_grant["connector_binding_id"]
        != capability_grant["connector_binding_id"]
        or credential_grant["model_provider_profile_id"] is not None
        or credential_grant["nonce"] != row.get("credential_grant_nonce")
        or credential_grant["deadline"] != row.get("credential_grant_deadline")
    ):
        raise PortableRestoreError(f"portable {label} attempt grant and row differ")

    source_payload = row.get("payload")
    if not isinstance(source_payload, dict):
        raise PortableRestoreError(f"portable {label} attempt payload is invalid")
    try:
        attempt = attempt_type.model_validate(source_payload).model_dump(mode="json")
    except ValueError as error:
        raise PortableRestoreError(
            f"portable {label} attempt payload is invalid"
        ) from error
    if any(attempt[key] != row[value] for key, value in direct_pairs.items()):
        raise PortableRestoreError(f"portable {label} attempt payload and row differ")
    if hashlib.sha256(canonical_json_bytes(attempt)).hexdigest() != row.get(
        "payload_sha256"
    ):
        raise PortableRestoreError(f"portable {label} attempt payload hash differs")

    restored: dict[str, Any] = {}
    for mapping in mappings:
        source, property_name = mapping.get("source"), mapping.get("portableProperty")
        if not isinstance(source, Mapping) or not isinstance(property_name, str):
            raise PortableRestoreError(
                f"portable {label} attempt transform mapping is incomplete"
            )
        kind = source.get("kind")
        if kind == "column":
            column = source.get("column")
            if not isinstance(column, str):
                raise PortableRestoreError(
                    f"portable {label} attempt source column is invalid"
                )
            if column == "credential_grant_payload":
                restored[column] = credential_grant
            elif column == "credential_grant_sha256":
                restored[column] = hashlib.sha256(
                    canonical_json_bytes(credential_grant)
                ).hexdigest()
            else:
                restored[column] = row[property_name]
        elif kind == "redactedContractColumn" and property_name == "credential_grant_payload":
            restored["credential_grant_payload"] = credential_grant
        elif kind == "credentialGrantPresence":
            continue
        else:
            raise PortableRestoreError(
                f"portable {label} attempt transform kind is unknown"
            )
    if tuple(restored) != tuple(spec.get("database_column_order", ())):
        raise PortableRestoreError(
            f"re-linked {label} attempt columns differ from table pin"
        )
    return restored


def decode_terminal_read_attempt(
    record: Mapping[str, Any], *, table_specs: tuple[Mapping[str, Any], ...]
) -> dict[str, Any]:
    """Restore finished read history with an unusable old credential."""

    return _decode_terminal_attempt(
        record,
        table_specs=table_specs,
        table=_READ_ATTEMPT_TABLE,
        label="read",
        terminal_states=frozenset({"observed", "ambiguous", "rejected", "rate_limited"}),
        attempt_type=ReadAttempt,
        direct_pairs={
            key: key
            for key in (
                "read_attempt_id",
                "read_intent_id",
                "ordinal",
                "state",
                "capability_grant_id",
                "credential_grant_id",
                "connector_manifest_sha256",
                "request_sha256",
                "response_sha256",
                "deadline",
                "created_at",
            )
        },
    )


def decode_terminal_effect_attempt(
    record: Mapping[str, Any], *, table_specs: tuple[Mapping[str, Any], ...]
) -> dict[str, Any]:
    """Restore completed effect history without reviving its provider access."""

    return _decode_terminal_attempt(
        record,
        table_specs=table_specs,
        table=_EFFECT_ATTEMPT_TABLE,
        label="effect",
        terminal_states=frozenset(
            {
                "provider_accepted",
                "definitive_reject",
                "ambiguous_transport",
                "rate_limited",
                "policy_denied",
                "hash_conflict",
                "internal_failure",
            }
        ),
        attempt_type=EffectAttempt,
        direct_pairs={
            key: key
            for key in (
                "effect_attempt_id",
                "effect_intent_id",
                "ordinal",
                "state",
                "dispatch_permit_sha256",
                "capability_grant_id",
                "credential_grant_id",
                "connector_manifest_sha256",
                "request_sha256",
                "response_sha256",
                "error_class",
                "deadline",
                "created_at",
            )
        },
    )


def verify_empty_restore_target(
    connection: Any,
    *,
    table_specs: tuple[Mapping[str, Any], ...],
) -> None:
    """Require the caller's current serializable transaction to have no Eva rows.

    This is an admission check, not an import.  The later importer must call it
    while traffic to the new target is closed and before it writes a first row.
    It intentionally checks every canonical table, not just installations, so
    a target with a stray tenant or a prior failed restore never looks empty.
    """

    isolation = _one_value(
        _execute(connection, "SHOW transaction_isolation"), label="isolation"
    )
    if isolation != "serializable":
        raise PortableRestoreError("restore target transaction is not serializable")
    _one_value(
        _execute(
            connection,
            "SELECT pg_catalog.pg_advisory_xact_lock("
            "pg_catalog.hashtextextended('EvaPortableRestore/v1', 0))",
        ),
        label="restore lock",
    )
    seen: set[str] = set()
    for spec in table_specs:
        table = spec.get("table")
        if (
            not isinstance(table, str)
            or table in seen
            or len(table.split(".")) != 2
            or any(not _IDENTIFIER.fullmatch(part) for part in table.split("."))
        ):
            raise PortableRestoreError("restore target table specification is invalid")
        seen.add(table)
        present = _one_value(
            _execute(connection, f"SELECT pg_catalog.to_regclass('{table}') IS NOT NULL"),
            label=f"table {table}",
        )
        if present is not True:
            raise PortableRestoreError(f"restore target lacks sealed table {table}")
        row_count = _one_value(
            _execute(connection, f"SELECT COUNT(*) FROM {table}"),
            label=f"row count {table}",
        )
        if isinstance(row_count, bool) or not isinstance(row_count, int):
            raise PortableRestoreError(f"restore target {table} count is invalid")
        if row_count:
            raise PortableRestoreError(f"restore target is not empty: {table}")


def _canonical_uuid(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PortableRestoreError("portable UUID is not text")
    try:
        canonical = str(UUID(value))
    except ValueError as error:
        raise PortableRestoreError("portable UUID is invalid") from error
    if canonical != value:
        raise PortableRestoreError("portable UUID is not lowercase canonical")
    return canonical


def _canonical_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PortableRestoreError("portable timestamp is not text")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise PortableRestoreError("portable timestamp is not canonical UTC seconds") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise PortableRestoreError("portable timestamp is not canonical UTC seconds")
    return value


def _canonical_sha256(value: Any) -> bytes | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise PortableRestoreError("portable SHA-256 is not lowercase hex")
    return bytes.fromhex(value)


def _integer(value: Any, *, safe: bool) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise PortableRestoreError("portable integer is invalid")
    if safe and not -9_007_199_254_740_991 <= value <= 9_007_199_254_740_991:
        raise PortableRestoreError("portable integer is outside the safe range")
    return value


def _json_array(value: Any, *, uuid_values: bool) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PortableRestoreError("portable array is invalid")
    if uuid_values:
        result: list[str] = []
        for item in value:
            canonical = _canonical_uuid(item)
            if canonical is None:  # item is a str, but keep the failure closed.
                raise PortableRestoreError("portable UUID array contains null")
            result.append(canonical)
        return result
    return list(value)


def _bind_value(value: Any, encoding: str) -> tuple[str, Any]:
    """Return one safe PostgreSQL placeholder and its typed portable value."""

    if encoding == "uuidLowercase":
        return "%s", _canonical_uuid(value)
    if encoding == "utf8Text":
        if value is not None and not isinstance(value, str):
            raise PortableRestoreError("portable text is invalid")
        return "%s", value
    if encoding == "utcWholeSeconds":
        return "%s", _canonical_timestamp(value)
    if encoding == "sha256LowercaseHex":
        return "%s", _canonical_sha256(value)
    if encoding == "jsonInteger":
        return "%s", _integer(value, safe=False)
    if encoding == "jsonSafeInteger":
        return "%s", _integer(value, safe=True)
    if encoding == "jsonBoolean":
        if not isinstance(value, bool):
            raise PortableRestoreError("portable boolean is invalid")
        return "%s", value
    if encoding == "canonicalJson":
        return "CAST(%s AS jsonb)", canonical_json_bytes(value).decode("utf-8")
    if encoding == "canonicalUuidArray":
        return "CAST(%s AS uuid[])", _json_array(value, uuid_values=True)
    if encoding == "canonicalTextArray":
        return "CAST(%s AS text[])", _json_array(value, uuid_values=False)
    raise PortableRestoreError("portable column encoding is not sealed for restore")


def _prepare_reauthorization_required_connector_record(
    record: Mapping[str, Any],
    *,
    table_specs: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, Any], tuple[str, ...], tuple[Any, ...]]:
    """Type one safely re-linked connector row from its sealed redacted form."""

    spec = _spec_for_table(table_specs, _CONNECTOR_BINDING_TABLE)
    restored = decode_reauthorization_required_connector_binding(
        record, table_specs=table_specs
    )
    transform = spec.get("transform_spec")
    mappings = transform.get("propertyMappings") if isinstance(transform, Mapping) else None
    if not isinstance(mappings, list):
        raise PortableRestoreError("portable connector transform mappings are absent")

    encodings: dict[str, str] = {}
    for mapping in mappings:
        if not isinstance(mapping, Mapping):
            raise PortableRestoreError("portable connector transform mapping is invalid")
        property_name = mapping.get("portableProperty")
        source = mapping.get("source")
        encoding = mapping.get("encoding")
        if (
            not isinstance(property_name, str)
            or not isinstance(source, Mapping)
            or not isinstance(encoding, str)
        ):
            raise PortableRestoreError("portable connector transform mapping is incomplete")
        if source.get("kind") == "column":
            column = source.get("column")
            if not isinstance(column, str) or column in encodings:
                raise PortableRestoreError("portable connector source column is invalid")
            encodings[column] = encoding
        elif source.get("kind") == "literal" and property_name == "credential_reference":
            # The sealed export uses JSON null for this property, but the
            # restore inserts our non-secret sentinel into a text column.
            encodings["credential_reference"] = "utf8Text"
        elif source.get("kind") == "redactedContractColumn" and property_name == "payload":
            encodings["payload"] = "canonicalJson"

    columns = tuple(spec.get("database_column_order", ()))
    if tuple(encodings) != columns:
        raise PortableRestoreError("portable connector encodings differ from table pin")
    placeholders: list[str] = []
    bindings: list[Any] = []
    for column in columns:
        placeholder, value = _bind_value(restored[column], encodings[column])
        placeholders.append(placeholder)
        bindings.append(value)
    return restored, tuple(placeholders), tuple(bindings)


def _prepare_terminal_attempt_record(
    record: Mapping[str, Any],
    *,
    table_specs: tuple[Mapping[str, Any], ...],
    table: str,
    decoder: Callable[..., dict[str, Any]],
    label: str,
) -> tuple[dict[str, Any], tuple[str, ...], tuple[Any, ...]]:
    spec = _spec_for_table(table_specs, table)
    restored = decoder(record, table_specs=table_specs)
    transform = spec.get("transform_spec")
    mappings = transform.get("propertyMappings") if isinstance(transform, Mapping) else None
    if not isinstance(mappings, list):
        raise PortableRestoreError(f"portable {label} attempt transform mappings are absent")
    encodings: dict[str, str] = {}
    for mapping in mappings:
        source, property_name, encoding = (
            mapping.get("source"), mapping.get("portableProperty"), mapping.get("encoding")
        )
        if not isinstance(source, Mapping) or not isinstance(property_name, str) or not isinstance(encoding, str):
            raise PortableRestoreError(f"portable {label} attempt transform mapping is incomplete")
        if source.get("kind") == "column":
            column = source.get("column")
            if not isinstance(column, str) or column in encodings:
                raise PortableRestoreError(f"portable {label} attempt source column is invalid")
            encodings[column] = encoding
        elif source.get("kind") == "redactedContractColumn" and property_name == "credential_grant_payload":
            encodings["credential_grant_payload"] = "canonicalJson"
    columns = tuple(spec.get("database_column_order", ()))
    if tuple(encodings) != columns:
        raise PortableRestoreError(f"portable {label} attempt encodings differ from table pin")
    placeholders: list[str] = []
    bindings: list[Any] = []
    for column in columns:
        placeholder, value = _bind_value(restored[column], encodings[column])
        placeholders.append(placeholder)
        bindings.append(value)
    return restored, tuple(placeholders), tuple(bindings)


def _prepare_ambiguous_model_attempt_record(
    record: Mapping[str, Any], *, table_specs: tuple[Mapping[str, Any], ...]
) -> tuple[dict[str, Any], tuple[str, ...], tuple[Any, ...]]:
    return _prepare_terminal_attempt_record(
        record, table_specs=table_specs, table=_MODEL_ATTEMPT_TABLE,
        decoder=decode_ambiguous_model_attempt, label="model",
    )


def _prepare_terminal_read_attempt_record(
    record: Mapping[str, Any], *, table_specs: tuple[Mapping[str, Any], ...]
) -> tuple[dict[str, Any], tuple[str, ...], tuple[Any, ...]]:
    return _prepare_terminal_attempt_record(
        record, table_specs=table_specs, table=_READ_ATTEMPT_TABLE,
        decoder=decode_terminal_read_attempt, label="read",
    )


def _prepare_terminal_effect_attempt_record(
    record: Mapping[str, Any], *, table_specs: tuple[Mapping[str, Any], ...]
) -> tuple[dict[str, Any], tuple[str, ...], tuple[Any, ...]]:
    return _prepare_terminal_attempt_record(
        record, table_specs=table_specs, table=_EFFECT_ATTEMPT_TABLE,
        decoder=decode_terminal_effect_attempt, label="effect",
    )


def _validate_record_sets(
    records_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    table_specs: tuple[Mapping[str, Any], ...],
    materialized_objects: Sequence[MaterializedPortableObject] | None,
    object_store_root: Path | None,
) -> tuple[
    tuple[
        Mapping[str, Any],
        tuple[tuple[dict[str, Any], tuple[str, ...], tuple[Any, ...]], ...],
    ],
    ...,
]:
    expected_tables = tuple(spec.get("table") for spec in table_specs)
    if (
        len(set(expected_tables)) != len(expected_tables)
        or any(not isinstance(table, str) for table in expected_tables)
        or set(records_by_table) != set(expected_tables)
    ):
        raise PortableRestoreError("portable record sets differ from sealed table list")

    materialized_index = _materialized_object_index(
        materialized_objects, object_store_root=object_store_root
    )
    consumed_materialized: set[tuple[str, str, str]] = set()
    prepared: list[
        tuple[
            Mapping[str, Any],
            tuple[tuple[dict[str, Any], tuple[str, ...], tuple[Any, ...]], ...],
        ]
    ] = []
    for spec in table_specs:
        table = str(spec["table"])
        records = records_by_table[table]
        if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
            raise PortableRestoreError("portable table record set is not a sequence")
        transform = spec.get("transform_spec")
        mappings = transform.get("propertyMappings") if isinstance(transform, Mapping) else None
        if not isinstance(mappings, list):
            raise PortableRestoreError("restore transform mappings are absent")
        rows: list[tuple[dict[str, Any], tuple[str, ...], tuple[Any, ...]]] = []
        for record in records:
            if not isinstance(record, Mapping):
                raise PortableRestoreError("portable table record is not an object")
            if table == _OBJECT_REF_TABLE:
                restored = decode_column_preserving_record(
                    record, table=table, table_specs=table_specs
                )
                _require_materialized_object_ref(
                    record,
                    restored,
                    materialized_index=materialized_index,
                    consumed=consumed_materialized,
                )
                bindings: list[Any] = []
                placeholders: list[str] = []
                for mapping in mappings:
                    property_name = mapping.get("portableProperty")
                    source = mapping.get("source")
                    encoding = mapping.get("encoding")
                    if (
                        not isinstance(property_name, str)
                        or not isinstance(source, Mapping)
                        or source.get("kind") != "column"
                        or not isinstance(encoding, str)
                    ):
                        raise PortableRestoreError("restore mapping is not column-preserving")
                    placeholder, value = _bind_value(
                        restored[str(source["column"])], encoding
                    )
                    placeholders.append(placeholder)
                    bindings.append(value)
                rows.append((restored, tuple(placeholders), tuple(bindings)))
                continue
            if table == _CONNECTOR_BINDING_TABLE:
                rows.append(
                    _prepare_reauthorization_required_connector_record(
                        record, table_specs=table_specs
                    )
                )
                continue
            if table == _MODEL_ATTEMPT_TABLE:
                rows.append(
                    _prepare_ambiguous_model_attempt_record(
                        record, table_specs=table_specs
                    )
                )
                continue
            if table == _READ_ATTEMPT_TABLE:
                rows.append(
                    _prepare_terminal_read_attempt_record(
                        record, table_specs=table_specs
                    )
                )
                continue
            if table == _EFFECT_ATTEMPT_TABLE:
                rows.append(
                    _prepare_terminal_effect_attempt_record(
                        record, table_specs=table_specs
                    )
                )
                continue
            restored = decode_column_preserving_record(
                record, table=table, table_specs=table_specs
            )
            bindings: list[Any] = []
            placeholders: list[str] = []
            for mapping in mappings:
                property_name = mapping.get("portableProperty")
                source = mapping.get("source")
                encoding = mapping.get("encoding")
                if (
                    not isinstance(property_name, str)
                    or not isinstance(source, Mapping)
                    or source.get("kind") != "column"
                    or not isinstance(encoding, str)
                ):
                    raise PortableRestoreError("restore mapping is not column-preserving")
                placeholder, value = _bind_value(restored[str(source["column"])], encoding)
                placeholders.append(placeholder)
                bindings.append(value)
            rows.append((restored, tuple(placeholders), tuple(bindings)))
        prepared.append((spec, tuple(rows)))
    if set(materialized_index) != consumed_materialized:
        raise PortableRestoreError("materialized object proof is not referenced by archive")
    return tuple(prepared)


def restore_column_preserving_records(
    connection: Any,
    records_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    archive_manifest_raw: bytes,
    restore_authority_raw: bytes,
    table_specs: tuple[Mapping[str, Any], ...],
    materialized_objects: Sequence[MaterializedPortableObject] | None = None,
    object_store_root: Path | None = None,
) -> int:
    """Write one verified, all-column archive only into an empty target.

    The entire archive is decoded and typed *before* inspecting or writing the
    target. A row using redaction, a missing set, a changed record or an unknown
    encoding therefore leaves the target untouched. The savepoint also removes
    every inserted row when a later database constraint rejects the archive.

    A redacted connector restores only as ``reauthorization_required``; a
    redacted model, completed read, or completed effect attempt restores with
    a new unusable credential descriptor. These exceptions preserve audit
    history, never a working secret, provider call, or retry.

    The private restore authority is checked before record decoding and before
    the first database query.  A completed erasure clears its archive list, so
    an older backup cannot silently bring forgotten content back.
    """

    authorize_portable_restore(
        archive_manifest_raw=archive_manifest_raw,
        restore_authority_raw=restore_authority_raw,
    )

    prepared = _validate_record_sets(
        records_by_table,
        table_specs=table_specs,
        materialized_objects=materialized_objects,
        object_store_root=object_store_root,
    )
    verify_empty_restore_target(connection, table_specs=table_specs)
    _execute(connection, "SAVEPOINT eva_portable_restore_write")
    inserted = 0
    try:
        for spec, rows in prepared:
            table = str(spec["table"])
            columns = tuple(spec.get("database_column_order", ()))
            if not columns or any(not isinstance(column, str) or not _IDENTIFIER.fullmatch(column) for column in columns):
                raise PortableRestoreError("restore database columns are invalid")
            rendered_columns = ", ".join(columns)
            for _restored, placeholders, bindings in rows:
                if len(placeholders) != len(columns) or len(bindings) != len(columns):
                    raise PortableRestoreError("restore binding count differs from columns")
                _execute(
                    connection,
                    f"INSERT INTO {table} ({rendered_columns}) VALUES ({', '.join(placeholders)})",
                    bindings,
                )
                inserted += 1
    except BaseException:
        _execute(connection, "ROLLBACK TO SAVEPOINT eva_portable_restore_write")
        _execute(connection, "RELEASE SAVEPOINT eva_portable_restore_write")
        raise
    _execute(connection, "RELEASE SAVEPOINT eva_portable_restore_write")
    return inserted


__all__ = [
    "PortableRestoreError",
    "decode_ambiguous_model_attempt",
    "decode_column_preserving_record",
    "decode_reauthorization_required_connector_binding",
    "decode_terminal_effect_attempt",
    "decode_terminal_read_attempt",
    "restore_column_preserving_records",
    "verify_empty_restore_target",
]
