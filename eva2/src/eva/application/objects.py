"""Framework-neutral encrypted-object ports and pre-selection orchestration."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_MIME_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")


class ObjectApplicationError(RuntimeError):
    """Base error for the encrypted-object application boundary."""


class ObjectBindingError(ObjectApplicationError):
    """An immutable object binding is not in its canonical wire form."""


class ObjectIntegrityError(ObjectApplicationError):
    """A candidate or independent readback did not match its binding."""


class CandidateBindingCommitError(ObjectApplicationError):
    """The prepared temp/candidate binding was not durably committed."""


def _canonical_uuid(value: str, field: str) -> None:
    if not isinstance(value, str):
        raise ObjectBindingError(f"{field} must be a lowercase canonical UUID")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ObjectBindingError(f"{field} must be a lowercase canonical UUID") from exc
    if str(parsed) != value:
        raise ObjectBindingError(f"{field} must be a lowercase canonical UUID")


def _require_sha256(value: str, field: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ObjectIntegrityError(f"{field} must be a lowercase SHA-256")


def _canonical_hash(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ObjectBinding:
    """Immutable scope incorporated into the authenticated object AAD."""

    tenant_id: str
    installation_id: str
    object_id: str
    key_ref: str
    mime_type: str

    def __post_init__(self) -> None:
        for field in ("tenant_id", "installation_id", "object_id", "key_ref"):
            _canonical_uuid(getattr(self, field), field)
        if (
            not isinstance(self.mime_type, str)
            or len(self.mime_type.encode("utf-8")) > 127
            or _MIME_TYPE_RE.fullmatch(self.mime_type) is None
        ):
            raise ObjectBindingError("mime_type is not a canonical MIME type")


@dataclass(frozen=True, slots=True)
class ObjectEnvelopeMetadata:
    """Structurally inspected envelope metadata; not authentication evidence."""

    binding: ObjectBinding
    canonical_aad: bytes
    ciphertext_sha256: str
    plaintext_sha256: str
    plaintext_byte_length: int
    stored_envelope_byte_length: int


@dataclass(frozen=True, slots=True)
class EncryptedObjectCandidate:
    """One complete immutable envelope before PostgreSQL selection authority."""

    binding: ObjectBinding
    envelope: bytes
    canonical_aad: bytes
    ciphertext_sha256: str
    plaintext_sha256: str
    plaintext_byte_length: int
    stored_envelope_byte_length: int

    def __post_init__(self) -> None:
        if not isinstance(self.envelope, bytes) or not isinstance(
            self.canonical_aad, bytes
        ):
            raise ObjectIntegrityError("object candidate bytes must be immutable bytes")
        _require_sha256(self.ciphertext_sha256, "candidate ciphertext SHA-256")
        _require_sha256(self.plaintext_sha256, "candidate plaintext SHA-256")
        if hashlib.sha256(self.envelope).hexdigest() != self.ciphertext_sha256:
            raise ObjectIntegrityError("candidate ciphertext SHA-256 does not match")
        if len(self.envelope) != self.stored_envelope_byte_length:
            raise ObjectIntegrityError("candidate envelope length does not match")
        if not 0 <= self.plaintext_byte_length <= 65_536:
            raise ObjectIntegrityError("candidate plaintext length is out of range")


@dataclass(frozen=True, slots=True)
class PreparedCandidateBinding:
    """Non-secret durable fields for one fsynced attempt-scoped temp file."""

    binding: ObjectBinding
    ciphertext_sha256: str
    plaintext_sha256: str
    plaintext_byte_length: int
    stored_envelope_byte_length: int
    temp_name_sha256: str

    def __post_init__(self) -> None:
        _require_sha256(self.ciphertext_sha256, "prepared ciphertext SHA-256")
        _require_sha256(self.plaintext_sha256, "prepared plaintext SHA-256")
        _require_sha256(self.temp_name_sha256, "prepared temp-name SHA-256")
        if not 0 <= self.plaintext_byte_length <= 65_536:
            raise ObjectIntegrityError("prepared plaintext length is out of range")
        if not 1 <= self.stored_envelope_byte_length <= 66_612:
            raise ObjectIntegrityError("prepared envelope length is out of range")

    @property
    def canonical_sha256(self) -> str:
        return _canonical_hash(
            {
                "ciphertext_sha256": self.ciphertext_sha256,
                "installation_id": self.binding.installation_id,
                "key_ref": self.binding.key_ref,
                "mime_type": self.binding.mime_type,
                "object_id": self.binding.object_id,
                "plaintext_byte_length": self.plaintext_byte_length,
                "plaintext_sha256": self.plaintext_sha256,
                "stored_envelope_byte_length": self.stored_envelope_byte_length,
                "temp_name_sha256": self.temp_name_sha256,
                "tenant_id": self.binding.tenant_id,
            }
        )


@dataclass(frozen=True, slots=True)
class DurableCandidateBindingCommit:
    """Receipt returned only after caller-owned durable binding commit."""

    prepared_binding_sha256: str
    committed_at: str
    commit_receipt_sha256: str

    def __post_init__(self) -> None:
        _require_sha256(self.prepared_binding_sha256, "prepared binding SHA-256")
        _require_sha256(self.commit_receipt_sha256, "binding commit receipt SHA-256")
        if (
            not isinstance(self.committed_at, str)
            or _TIMESTAMP_RE.fullmatch(self.committed_at) is None
        ):
            raise CandidateBindingCommitError(
                "candidate binding committed_at must be canonical UTC"
            )


class PreparedCandidateHandle(Protocol):
    """Opaque CAS-owned installation capability."""

    @property
    def binding(self) -> PreparedCandidateBinding: ...


class CasInstallStatus(StrEnum):
    CREATED = "created"
    ALREADY_SAME = "already_same"


@dataclass(frozen=True, slots=True)
class CasReadback:
    """Bytes independently reopened from the final content-addressed path."""

    status: CasInstallStatus
    ciphertext_sha256: str
    envelope: bytes
    path: Path


class ObjectKeyResolver(Protocol):
    def __call__(self, binding: ObjectBinding) -> bytes: ...


class ObjectCipher(Protocol):
    def encrypt(
        self,
        binding: ObjectBinding,
        plaintext: bytes,
        *,
        key_resolver: ObjectKeyResolver,
    ) -> EncryptedObjectCandidate: ...

    def inspect(self, envelope: bytes) -> ObjectEnvelopeMetadata: ...

    def decrypt(
        self,
        envelope: bytes,
        binding: ObjectBinding,
        *,
        key_resolver: ObjectKeyResolver,
    ) -> bytes: ...


class ContentAddressedObjectStore(Protocol):
    def prepare(
        self, candidate: EncryptedObjectCandidate
    ) -> PreparedCandidateHandle: ...

    def install_prepared_and_readback(
        self,
        prepared: PreparedCandidateHandle,
        commit: DurableCandidateBindingCommit,
    ) -> CasReadback: ...

    def recover_prepared(
        self,
        candidate: EncryptedObjectCandidate,
        durable_binding: PreparedCandidateBinding,
        commit: DurableCandidateBindingCommit,
    ) -> PreparedCandidateHandle: ...

    def discard_uncommitted(self, prepared: PreparedCandidateHandle) -> bool: ...

    def read(self, ciphertext_sha256: str) -> bytes: ...


class CandidateBindingCommitterPort(Protocol):
    def __call__(
        self, binding: PreparedCandidateBinding
    ) -> DurableCandidateBindingCommit | None: ...


@dataclass(frozen=True, slots=True)
class AuthenticatedCandidateReadback:
    """Authenticated CAS candidate; deliberately not a selected ObjectRef."""

    binding: ObjectBinding
    canonical_aad: bytes
    ciphertext_sha256: str
    plaintext_sha256: str
    plaintext_byte_length: int
    stored_envelope_byte_length: int
    install_status: CasInstallStatus
    cas_path: Path
    authenticated: Literal[True]


@dataclass(frozen=True, slots=True)
class DatabaseSelectedObject:
    """Selection authority supplied by the later PostgreSQL selector."""

    binding: ObjectBinding
    ciphertext_sha256: str
    plaintext_sha256: str
    plaintext_byte_length: int
    stored_envelope_byte_length: int

    def __post_init__(self) -> None:
        _require_sha256(self.ciphertext_sha256, "selected ciphertext SHA-256")
        _require_sha256(self.plaintext_sha256, "selected plaintext SHA-256")
        if not 0 <= self.plaintext_byte_length <= 65_536:
            raise ObjectIntegrityError("selected plaintext length is out of range")
        if not 1 <= self.stored_envelope_byte_length <= 66_612:
            raise ObjectIntegrityError("selected envelope length is out of range")


@dataclass(frozen=True, slots=True)
class AuthenticatedFoundSameReadback:
    """Authenticated bytes for an already DB-selected, possibly other digest."""

    selected_authority: DatabaseSelectedObject
    binding: ObjectBinding
    canonical_aad: bytes
    ciphertext_sha256: str
    plaintext_sha256: str
    plaintext_byte_length: int
    stored_envelope_byte_length: int
    authenticated: Literal[True]


def _require_candidate_metadata(
    candidate: EncryptedObjectCandidate,
    inspected: ObjectEnvelopeMetadata,
    expected_plaintext: bytes,
) -> None:
    expected_hash = hashlib.sha256(expected_plaintext).hexdigest()
    comparisons = (
        (candidate.binding, inspected.binding, "binding"),
        (candidate.canonical_aad, inspected.canonical_aad, "canonical AAD"),
        (
            candidate.ciphertext_sha256,
            inspected.ciphertext_sha256,
            "ciphertext SHA-256",
        ),
        (
            candidate.plaintext_sha256,
            inspected.plaintext_sha256,
            "plaintext SHA-256",
        ),
        (
            candidate.plaintext_byte_length,
            inspected.plaintext_byte_length,
            "plaintext length",
        ),
        (
            candidate.stored_envelope_byte_length,
            inspected.stored_envelope_byte_length,
            "stored envelope length",
        ),
        (candidate.plaintext_sha256, expected_hash, "plaintext SHA-256"),
        (
            candidate.plaintext_byte_length,
            len(expected_plaintext),
            "plaintext length",
        ),
    )
    for actual, expected, label in comparisons:
        if actual != expected:
            raise ObjectIntegrityError(f"candidate {label} differs from envelope")


def _require_prepared_binding(
    candidate: EncryptedObjectCandidate, prepared: PreparedCandidateBinding
) -> None:
    comparisons = (
        (prepared.binding, candidate.binding, "binding"),
        (
            prepared.ciphertext_sha256,
            candidate.ciphertext_sha256,
            "ciphertext SHA-256",
        ),
        (
            prepared.plaintext_sha256,
            candidate.plaintext_sha256,
            "plaintext SHA-256",
        ),
        (
            prepared.plaintext_byte_length,
            candidate.plaintext_byte_length,
            "plaintext length",
        ),
        (
            prepared.stored_envelope_byte_length,
            candidate.stored_envelope_byte_length,
            "stored envelope length",
        ),
    )
    for actual, expected, label in comparisons:
        if actual != expected:
            raise ObjectIntegrityError(
                f"prepared candidate {label} differs from candidate"
            )


class ObjectApplicationService:
    """Persist candidates without claiming PostgreSQL selection authority."""

    def __init__(
        self, *, cipher: ObjectCipher, store: ContentAddressedObjectStore
    ) -> None:
        self._cipher = cipher
        self._store = store

    def create_candidate_and_authenticate(
        self,
        binding: ObjectBinding,
        plaintext: bytes,
        *,
        key_resolver: ObjectKeyResolver,
        binding_committer: CandidateBindingCommitterPort,
    ) -> AuthenticatedCandidateReadback:
        candidate = self._cipher.encrypt(binding, plaintext, key_resolver=key_resolver)
        return self.write_candidate_and_authenticate(
            candidate,
            expected_plaintext=plaintext,
            key_resolver=key_resolver,
            binding_committer=binding_committer,
        )

    def write_candidate_and_authenticate(
        self,
        candidate: EncryptedObjectCandidate,
        *,
        expected_plaintext: bytes,
        key_resolver: ObjectKeyResolver,
        binding_committer: CandidateBindingCommitterPort,
    ) -> AuthenticatedCandidateReadback:
        """Preflight, prepare, commit binding, install, reopen and authenticate."""

        if not isinstance(candidate, EncryptedObjectCandidate):
            raise TypeError("only an encrypted candidate may enter the CAS")
        if not isinstance(expected_plaintext, bytes):
            raise TypeError("expected object plaintext must be bytes")
        before = self._cipher.inspect(candidate.envelope)
        _require_candidate_metadata(candidate, before, expected_plaintext)
        prepared = self._store.prepare(candidate)
        try:
            _require_prepared_binding(candidate, prepared.binding)
        except Exception:
            self._store.discard_uncommitted(prepared)
            raise
        try:
            commit = binding_committer(prepared.binding)
        except Exception as exc:
            # The caller may have committed before losing its acknowledgement.
            # Preserve the fsynced temp for reconciliation; deleting it here
            # would turn an ambiguous durable binding into unrecoverable state.
            raise CandidateBindingCommitError(
                "candidate binding commit outcome is ambiguous; temp preserved"
            ) from exc
        if commit is None:
            self._store.discard_uncommitted(prepared)
            raise CandidateBindingCommitError(
                "durable candidate/temp binding commit is required before CAS install"
            )
        if not isinstance(commit, DurableCandidateBindingCommit):
            raise CandidateBindingCommitError(
                "candidate binding commit returned an invalid receipt; temp preserved"
            )
        if commit.prepared_binding_sha256 != prepared.binding.canonical_sha256:
            raise CandidateBindingCommitError(
                "durable candidate binding receipt names another preparation; temp preserved"
            )
        readback = self._store.install_prepared_and_readback(prepared, commit)
        return self._authenticate_candidate_readback(
            candidate,
            readback,
            expected_plaintext=expected_plaintext,
            key_resolver=key_resolver,
        )

    def recover_candidate_and_authenticate(
        self,
        candidate: EncryptedObjectCandidate,
        *,
        durable_binding: PreparedCandidateBinding,
        expected_plaintext: bytes,
        key_resolver: ObjectKeyResolver,
        durable_commit: DurableCandidateBindingCommit,
    ) -> AuthenticatedCandidateReadback:
        """Resume a DB-bound preparation after restart, then authenticate it."""

        if not isinstance(candidate, EncryptedObjectCandidate):
            raise TypeError("only an encrypted candidate may be recovered")
        if not isinstance(expected_plaintext, bytes):
            raise TypeError("expected object plaintext must be bytes")
        if not isinstance(durable_commit, DurableCandidateBindingCommit):
            raise CandidateBindingCommitError(
                "exact durable candidate binding receipt is required for recovery"
            )
        before = self._cipher.inspect(candidate.envelope)
        _require_candidate_metadata(candidate, before, expected_plaintext)
        if not isinstance(durable_binding, PreparedCandidateBinding):
            raise CandidateBindingCommitError(
                "exact durable prepared binding is required for recovery"
            )
        _require_prepared_binding(candidate, durable_binding)
        if durable_commit.prepared_binding_sha256 != durable_binding.canonical_sha256:
            raise CandidateBindingCommitError(
                "durable receipt differs from the stored prepared binding"
            )
        prepared = self._store.recover_prepared(
            candidate, durable_binding, durable_commit
        )
        _require_prepared_binding(candidate, prepared.binding)
        if durable_commit.prepared_binding_sha256 != prepared.binding.canonical_sha256:
            raise CandidateBindingCommitError(
                "durable candidate binding receipt names another preparation"
            )
        readback = self._store.install_prepared_and_readback(prepared, durable_commit)
        return self._authenticate_candidate_readback(
            candidate,
            readback,
            expected_plaintext=expected_plaintext,
            key_resolver=key_resolver,
        )

    def _authenticate_candidate_readback(
        self,
        candidate: EncryptedObjectCandidate,
        readback: CasReadback,
        *,
        expected_plaintext: bytes,
        key_resolver: ObjectKeyResolver,
    ) -> AuthenticatedCandidateReadback:
        if readback.envelope != candidate.envelope:
            raise ObjectIntegrityError("CAS readback bytes differ from candidate")
        if readback.ciphertext_sha256 != candidate.ciphertext_sha256:
            raise ObjectIntegrityError("CAS readback digest differs from candidate")
        if readback.ciphertext_sha256 != hashlib.sha256(readback.envelope).hexdigest():
            raise ObjectIntegrityError("CAS readback digest differs from its bytes")
        inspected = self._cipher.inspect(readback.envelope)
        _require_candidate_metadata(candidate, inspected, expected_plaintext)
        plaintext = self._cipher.decrypt(
            readback.envelope, inspected.binding, key_resolver=key_resolver
        )
        plaintext_sha256 = hashlib.sha256(plaintext).hexdigest()
        if plaintext != expected_plaintext:
            raise ObjectIntegrityError("authenticated readback plaintext differs")
        if (
            plaintext_sha256 != inspected.plaintext_sha256
            or len(plaintext) != inspected.plaintext_byte_length
        ):
            raise ObjectIntegrityError(
                "authenticated plaintext metadata differs from inspected AAD"
            )
        return AuthenticatedCandidateReadback(
            binding=inspected.binding,
            canonical_aad=inspected.canonical_aad,
            ciphertext_sha256=hashlib.sha256(readback.envelope).hexdigest(),
            plaintext_sha256=plaintext_sha256,
            plaintext_byte_length=len(plaintext),
            stored_envelope_byte_length=len(readback.envelope),
            install_status=readback.status,
            cas_path=readback.path,
            authenticated=True,
        )

    def authenticate_found_same(
        self,
        *,
        selected: DatabaseSelectedObject,
        reserved_candidate: EncryptedObjectCandidate,
        expected_plaintext: bytes,
        key_resolver: ObjectKeyResolver,
    ) -> AuthenticatedFoundSameReadback:
        """Authenticate DB-selected bytes without installing the contender."""

        reserved_metadata = self._cipher.inspect(reserved_candidate.envelope)
        _require_candidate_metadata(
            reserved_candidate, reserved_metadata, expected_plaintext
        )
        if selected.binding != reserved_candidate.binding:
            raise ObjectIntegrityError(
                "selected object binding differs from reservation"
            )
        if selected.plaintext_sha256 != reserved_candidate.plaintext_sha256:
            raise ObjectIntegrityError(
                "selected object plaintext SHA-256 differs from reservation"
            )
        if selected.plaintext_byte_length != reserved_candidate.plaintext_byte_length:
            raise ObjectIntegrityError(
                "selected object plaintext length differs from reservation"
            )
        envelope = self._store.read(selected.ciphertext_sha256)
        inspected = self._cipher.inspect(envelope)
        selected_comparisons = (
            (inspected.binding, selected.binding, "binding"),
            (
                inspected.canonical_aad,
                reserved_candidate.canonical_aad,
                "canonical AAD",
            ),
            (
                inspected.ciphertext_sha256,
                selected.ciphertext_sha256,
                "ciphertext SHA-256",
            ),
            (
                inspected.plaintext_sha256,
                selected.plaintext_sha256,
                "plaintext SHA-256",
            ),
            (
                inspected.plaintext_byte_length,
                selected.plaintext_byte_length,
                "plaintext length",
            ),
            (
                inspected.stored_envelope_byte_length,
                selected.stored_envelope_byte_length,
                "stored envelope length",
            ),
        )
        for actual, expected, label in selected_comparisons:
            if actual != expected:
                raise ObjectIntegrityError(
                    f"selected object {label} differs from authenticated envelope"
                )
        plaintext = self._cipher.decrypt(
            envelope, inspected.binding, key_resolver=key_resolver
        )
        plaintext_sha256 = hashlib.sha256(plaintext).hexdigest()
        if plaintext != expected_plaintext:
            raise ObjectIntegrityError("selected object plaintext is not found_same")
        if (
            plaintext_sha256 != inspected.plaintext_sha256
            or len(plaintext) != inspected.plaintext_byte_length
        ):
            raise ObjectIntegrityError(
                "selected authenticated plaintext metadata differs from AAD"
            )
        return AuthenticatedFoundSameReadback(
            selected_authority=selected,
            binding=inspected.binding,
            canonical_aad=inspected.canonical_aad,
            ciphertext_sha256=hashlib.sha256(envelope).hexdigest(),
            plaintext_sha256=plaintext_sha256,
            plaintext_byte_length=len(plaintext),
            stored_envelope_byte_length=len(envelope),
            authenticated=True,
        )
