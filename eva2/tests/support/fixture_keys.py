"""Test-only 4D fixture keys, KATs and one-shot traced nonce harness.

This module is deliberately not imported by the package marker or a default
bootstrap. Its fixed nonce path produces a private, non-adoptable value until
the caller's isolated schedule ledger returns a durable commit receipt.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from nacl.secret import Aead

from eva.adapters.objects.envelope import (
    NONCE_BYTES,
    _candidate_fields,
    _encrypted_message_bytes,
    _prepare_encryption,
    decrypt_object,
)
from eva.application.objects import (
    EncryptedObjectCandidate,
    ObjectBinding,
    ObjectKeyResolver,
)
from eva.contracts.canonical_json import canonical_sha256


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/@+\-=]{0,191}$")


class FixtureHarnessError(ValueError):
    """The isolated fixture authority or its exact scope was violated."""


@dataclass(frozen=True, slots=True)
class _PendingFixtureEncryption:
    """Fixed-nonce bytes that cannot be passed to the CAS adapter."""

    binding: ObjectBinding
    envelope: bytes
    canonical_aad: bytes
    ciphertext_sha256: str
    plaintext_sha256: str
    plaintext_byte_length: int
    stored_envelope_byte_length: int

    def promote_after_trace(self) -> EncryptedObjectCandidate:
        return EncryptedObjectCandidate(
            binding=self.binding,
            envelope=self.envelope,
            canonical_aad=self.canonical_aad,
            ciphertext_sha256=self.ciphertext_sha256,
            plaintext_sha256=self.plaintext_sha256,
            plaintext_byte_length=self.plaintext_byte_length,
            stored_envelope_byte_length=self.stored_envelope_byte_length,
        )


def _fixed_nonce_encrypt(
    binding: ObjectBinding,
    plaintext: bytes,
    *,
    key_resolver: ObjectKeyResolver,
    nonce_source: Callable[[], bytes],
) -> _PendingFixtureEncryption:
    canonical_aad, header, key = _prepare_encryption(
        binding, plaintext, key_resolver=key_resolver
    )
    nonce = nonce_source()
    if not isinstance(nonce, bytes) or len(nonce) != NONCE_BYTES:
        raise FixtureHarnessError("fixture nonce source must return exactly 24 bytes")
    encrypted = Aead(key).encrypt(plaintext, aad=header, nonce=nonce)
    fields = _candidate_fields(
        binding=binding,
        plaintext=plaintext,
        encrypted_message=_encrypted_message_bytes(encrypted, len(plaintext)),
        canonical_aad=canonical_aad,
    )
    return _PendingFixtureEncryption(**fields)  # type: ignore[arg-type]


def _load_json_exact(path: Path) -> dict[str, Any]:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise FixtureHarnessError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FixtureHarnessError(
            f"cannot load sealed fixture authority: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise FixtureHarnessError("sealed fixture authority root must be an object")
    return value


@dataclass(frozen=True, slots=True)
class _FixtureObjectKey:
    installation_id: str
    tenant_id: str
    key_ref: str
    key: bytes
    key_sha256: str


class FixtureObjectKeyResolver:
    """Exact installation/tenant/key-ref resolver for public 4D test keys."""

    def __init__(self, records: tuple[_FixtureObjectKey, ...]) -> None:
        if not records:
            raise FixtureHarnessError("fixture object key set must not be empty")
        selectors = {
            (record.installation_id, record.tenant_id, record.key_ref)
            for record in records
        }
        if len(selectors) != len(records):
            raise FixtureHarnessError("fixture object key selectors must be unique")
        self._records = records

    @classmethod
    def from_sealed_profile(cls, path: Path) -> "FixtureObjectKeyResolver":
        profile = _load_json_exact(path)
        try:
            raw_records = profile["encryptedObjectFormat"]["keyBinding"][
                "fourDFixtureKeyRecords"
            ]
        except (KeyError, TypeError) as exc:
            raise FixtureHarnessError(
                "profile does not contain sealed fixture object keys"
            ) from exc
        if not isinstance(raw_records, list):
            raise FixtureHarnessError("fixture object key records must be an array")
        records: list[_FixtureObjectKey] = []
        for raw in raw_records:
            if not isinstance(raw, dict):
                raise FixtureHarnessError("fixture object key record must be an object")
            required = {
                "identity",
                "contract",
                "classification",
                "keyAuthorityRef",
                "installationId",
                "tenantId",
                "keyRef",
                "algorithm",
                "keyMaterialEncoding",
                "keyMaterial",
                "keySha256",
                "productionUseAuthorized",
            }
            if set(raw) != required:
                raise FixtureHarnessError(
                    "fixture object key record does not have the sealed shape"
                )
            if (
                raw["contract"] != "Eva4DFixtureObjectKeyRecord/v1"
                or raw["classification"] != "public_test_fixture_not_a_secret"
                or raw["algorithm"] != "XChaCha20-Poly1305"
                or raw["keyMaterialEncoding"] != "lowercase_hex"
                or raw["productionUseAuthorized"] is not False
            ):
                raise FixtureHarnessError("fixture object key record is not authorized")
            material = raw["keyMaterial"]
            if (
                not isinstance(material, str)
                or len(material) != 64
                or material.lower() != material
            ):
                raise FixtureHarnessError("fixture object key material is malformed")
            try:
                key = bytes.fromhex(material)
            except ValueError as exc:
                raise FixtureHarnessError(
                    "fixture object key material is malformed"
                ) from exc
            key_sha256 = hashlib.sha256(key).hexdigest()
            if key_sha256 != raw["keySha256"]:
                raise FixtureHarnessError("fixture object key SHA-256 differs")
            records.append(
                _FixtureObjectKey(
                    installation_id=raw["installationId"],
                    tenant_id=raw["tenantId"],
                    key_ref=raw["keyRef"],
                    key=key,
                    key_sha256=key_sha256,
                )
            )
        return cls(tuple(records))

    def __call__(self, binding: ObjectBinding) -> bytes:
        matches = [
            record
            for record in self._records
            if (
                record.installation_id,
                record.tenant_id,
                record.key_ref,
            )
            == (binding.installation_id, binding.tenant_id, binding.key_ref)
        ]
        if len(matches) != 1:
            raise FixtureHarnessError(
                "no exact installation-scoped fixture object key exists"
            )
        return matches[0].key


class OneShotFixtureNonce:
    """Thread-safe one-consumption callback owned only by the schedule harness."""

    def __init__(self, nonce: bytes, *, authority_sha256: str) -> None:
        if not isinstance(nonce, bytes) or len(nonce) != 24:
            raise FixtureHarnessError("sealed fixture nonce must contain 24 bytes")
        if _SHA256_RE.fullmatch(authority_sha256) is None:
            raise FixtureHarnessError("fixture entropy authority SHA-256 is malformed")
        self._nonce = nonce
        self.authority_sha256 = authority_sha256
        self._consumed = False
        self._lock = threading.Lock()

    @classmethod
    def from_sealed_schedule(cls, path: Path) -> "OneShotFixtureNonce":
        authority = _authority_from_schedule(path)
        injection = authority.get("nonce_injection")
        if not isinstance(injection, dict):
            raise FixtureHarnessError("fixture nonce authority is missing")
        try:
            nonce_hex = injection["nonce_hex"]
            nonce = bytes.fromhex(nonce_hex)
        except (KeyError, TypeError, ValueError) as exc:
            raise FixtureHarnessError("sealed fixture nonce is malformed") from exc
        if (
            injection.get("ownership") != "noor_4d_schedule_harness_only"
            or injection.get("maximum_consumptions_per_isolated_scenario_run") != 1
            or injection.get("consumption_ordinal") != 1
            or injection.get("nonce_byte_length") != 24
            or injection.get("nonce_sha256") != hashlib.sha256(nonce).hexdigest()
        ):
            raise FixtureHarnessError("fixture nonce authority is incoherent")
        return cls(nonce, authority_sha256=authority["authority_sha256"])

    @property
    def consumed(self) -> bool:
        with self._lock:
            return self._consumed

    def __call__(self) -> bytes:
        with self._lock:
            if self._consumed:
                raise FixtureHarnessError("one-shot fixture nonce was already consumed")
            self._consumed = True
            return self._nonce


@dataclass(frozen=True, slots=True)
class DurableFixtureTraceCommit:
    ledger_sequence: int
    committed_at: str
    ledger_commit_receipt_sha256: str

    def __post_init__(self) -> None:
        if type(self.ledger_sequence) is not int or self.ledger_sequence < 1:
            raise FixtureHarnessError("durable trace ledger sequence must be positive")
        if _TIMESTAMP_RE.fullmatch(self.committed_at) is None:
            raise FixtureHarnessError("durable trace committed_at is not canonical UTC")
        if _SHA256_RE.fullmatch(self.ledger_commit_receipt_sha256) is None:
            raise FixtureHarnessError(
                "durable trace commit receipt SHA-256 is malformed"
            )


@dataclass(frozen=True, slots=True)
class FixtureEntropyConsumptionTrace:
    contract: str
    schema_version: int
    authority_sha256: str
    scenario_run_id: str
    consumption_ordinal: int
    identity: str
    tenant_id: str
    installation_id: str
    object_id: str
    object_store_id: str
    key_ref: str
    key_sha256: str
    effect_attempt_id: str
    nonce_sha256: str
    complete_envelope_sha256: str
    ledger_sequence: int
    committed_at: str
    ledger_commit_receipt_sha256: str


@dataclass(frozen=True, slots=True)
class FixtureEntropyConsumptionDraft:
    contract: str
    schema_version: int
    authority_sha256: str
    scenario_run_id: str
    consumption_ordinal: int
    identity: str
    tenant_id: str
    installation_id: str
    object_id: str
    object_store_id: str
    key_ref: str
    key_sha256: str
    effect_attempt_id: str
    nonce_sha256: str
    complete_envelope_sha256: str

    def complete(
        self, commit: DurableFixtureTraceCommit
    ) -> FixtureEntropyConsumptionTrace:
        return FixtureEntropyConsumptionTrace(
            **{
                **asdict(self),
                "ledger_sequence": commit.ledger_sequence,
                "committed_at": commit.committed_at,
                "ledger_commit_receipt_sha256": commit.ledger_commit_receipt_sha256,
            }
        )


FixtureTraceCommitter = Callable[
    [FixtureEntropyConsumptionDraft], DurableFixtureTraceCommit | None
]


def _authority_from_schedule(path: Path) -> dict[str, Any]:
    schedule = _load_json_exact(path)
    authority = schedule.get("harnessEntropyInjectionAuthority")
    if not isinstance(authority, dict):
        raise FixtureHarnessError("schedule has no fixture entropy authority")
    if (
        authority.get("contract") != "Eva4DHarnessEntropyInjectionAuthority/v1"
        or authority.get("status") != "sealed_harness_only_fail_closed"
    ):
        raise FixtureHarnessError("fixture entropy authority is not sealed")
    claimed_hash = authority.get("authority_sha256")
    unsigned = dict(authority)
    unsigned.pop("authority_sha256", None)
    if not isinstance(claimed_hash, str) or canonical_sha256(unsigned) != claimed_hash:
        raise FixtureHarnessError("fixture entropy authority SHA-256 differs")
    return authority


def verify_known_answer_vector(
    *,
    binding: ObjectBinding,
    plaintext: bytes,
    key_resolver: ObjectKeyResolver,
    nonce: bytes,
    expected_envelope: bytes,
) -> bool:
    """Run a fixed-nonce format KAT without returning adoptable object bytes."""

    used = False

    def source() -> bytes:
        nonlocal used
        if used:
            raise FixtureHarnessError("known-answer nonce was consumed twice")
        used = True
        return nonce

    pending = _fixed_nonce_encrypt(
        binding,
        plaintext,
        key_resolver=key_resolver,
        nonce_source=source,
    )
    if pending.envelope != expected_envelope:
        return False
    if hashlib.sha256(expected_envelope).hexdigest() != pending.ciphertext_sha256:
        return False
    return (
        decrypt_object(expected_envelope, binding, key_resolver=key_resolver)
        == plaintext
    )


def _require_scope_value(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise FixtureHarnessError(f"fixture entropy scope {label} differs")


def create_traced_eva_fixture_candidate(
    *,
    authority_path: Path,
    scenario_run_id: str,
    identity: str,
    object_store_id: str,
    effect_attempt_id: str,
    binding: ObjectBinding,
    plaintext: bytes,
    key_resolver: ObjectKeyResolver,
    nonce_source: OneShotFixtureNonce,
    trace_committer: FixtureTraceCommitter,
) -> EncryptedObjectCandidate:
    """Exercise the sole fixed-nonce adoption exception, trace first."""

    if (
        not isinstance(scenario_run_id, str)
        or _IDENTIFIER_RE.fullmatch(scenario_run_id) is None
    ):
        raise FixtureHarnessError("scenario_run_id is not a canonical identifier")
    authority = _authority_from_schedule(authority_path)
    scope = authority.get("exact_fixture_scope")
    if not isinstance(scope, dict):
        raise FixtureHarnessError("fixture entropy exact scope is missing")
    _require_scope_value("identity", identity, scope.get("identity"))
    for field in ("tenant_id", "installation_id", "object_id", "key_ref"):
        _require_scope_value(field, getattr(binding, field), scope.get(field))
    _require_scope_value(
        "object_store_id", object_store_id, scope.get("object_store_id")
    )
    _require_scope_value("mime_type", binding.mime_type, "text/plain")
    _require_scope_value(
        "plaintext_byte_length", len(plaintext), scope.get("plaintext_byte_length")
    )
    plaintext_sha256 = hashlib.sha256(plaintext).hexdigest()
    _require_scope_value(
        "plaintext_sha256", plaintext_sha256, scope.get("plaintext_sha256")
    )

    attempts = scope.get("allowed_effect_attempts")
    if not isinstance(attempts, list):
        raise FixtureHarnessError("fixture entropy allowed attempts are missing")
    matched_attempts = [
        attempt
        for attempt in attempts
        if isinstance(attempt, dict)
        and attempt.get("portable_effect_attempt_id") == effect_attempt_id
    ]
    if len(matched_attempts) != 1:
        raise FixtureHarnessError("effect_attempt_id is outside fixture entropy scope")
    attempt = matched_attempts[0]
    _require_scope_value(
        "attempt object_id", attempt.get("object_candidate_id"), binding.object_id
    )
    _require_scope_value(
        "attempt object_store_id", attempt.get("object_store_id"), object_store_id
    )

    if not isinstance(nonce_source, OneShotFixtureNonce):
        raise FixtureHarnessError("nonce source is not the sealed one-shot callback")
    _require_scope_value(
        "authority_sha256",
        nonce_source.authority_sha256,
        authority.get("authority_sha256"),
    )
    key = key_resolver(binding)
    if not isinstance(key, bytes) or len(key) != 32:
        raise FixtureHarnessError("fixture object key must contain exactly 32 bytes")
    key_sha256 = hashlib.sha256(key).hexdigest()
    _require_scope_value("key_sha256", key_sha256, scope.get("key_sha256"))

    pending = _fixed_nonce_encrypt(
        binding,
        plaintext,
        key_resolver=lambda requested: (
            key
            if requested == binding
            else (_ for _ in ()).throw(FixtureHarnessError("fixture key scope changed"))
        ),
        nonce_source=nonce_source,
    )
    _require_scope_value(
        "complete_envelope_sha256",
        pending.ciphertext_sha256,
        scope.get("complete_envelope_sha256"),
    )
    _require_scope_value(
        "complete_envelope_byte_length",
        pending.stored_envelope_byte_length,
        scope.get("complete_envelope_byte_length"),
    )
    trace_gate = authority.get("durable_consumption_trace_gate")
    if not isinstance(trace_gate, dict):
        raise FixtureHarnessError("durable trace gate is missing")
    required = trace_gate.get("required_exact_values")
    injection = authority.get("nonce_injection")
    if not isinstance(required, dict) or not isinstance(injection, dict):
        raise FixtureHarnessError("durable trace authority is incomplete")
    draft = FixtureEntropyConsumptionDraft(
        contract="Eva4DHarnessEntropyConsumptionTrace/v1",
        schema_version=1,
        authority_sha256=authority["authority_sha256"],
        scenario_run_id=scenario_run_id,
        consumption_ordinal=1,
        identity=identity,
        tenant_id=binding.tenant_id,
        installation_id=binding.installation_id,
        object_id=binding.object_id,
        object_store_id=object_store_id,
        key_ref=binding.key_ref,
        key_sha256=key_sha256,
        effect_attempt_id=effect_attempt_id,
        nonce_sha256=injection["nonce_sha256"],
        complete_envelope_sha256=pending.ciphertext_sha256,
    )
    commit = trace_committer(draft)
    if not isinstance(commit, DurableFixtureTraceCommit):
        raise FixtureHarnessError(
            "durable trace commit receipt is required before candidate adoption"
        )
    trace = draft.complete(commit)
    for field, expected in required.items():
        if hasattr(trace, field) and getattr(trace, field) != expected:
            raise FixtureHarnessError(f"durable trace field {field} differs")
    # Promotion occurs strictly after the synchronous ledger commit returned.
    return pending.promote_after_trace()


__all__ = [
    "DurableFixtureTraceCommit",
    "FixtureEntropyConsumptionDraft",
    "FixtureEntropyConsumptionTrace",
    "FixtureHarnessError",
    "FixtureObjectKeyResolver",
    "OneShotFixtureNonce",
    "create_traced_eva_fixture_candidate",
    "verify_known_answer_vector",
]
