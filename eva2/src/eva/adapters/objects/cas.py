"""Atomic immutable filesystem CAS for encrypted object envelopes."""

from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from eva.application.objects import (
    CasInstallStatus,
    CasReadback,
    DurableCandidateBindingCommit,
    EncryptedObjectCandidate,
    PreparedCandidateBinding,
)
from eva.adapters.objects.envelope import ObjectEnvelopeError, envelope_metadata


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAXIMUM_STORED_ENVELOPE_BYTES = 66_612


class CasError(RuntimeError):
    """Base error for immutable content-addressed object storage."""


class CasIntegrityError(CasError):
    """The final CAS entry is missing, mutable, or digest-incoherent."""


class CasStateError(CasError):
    """A prepared temp capability was reused or cleaned in the wrong state."""


class CasRecoveryError(CasError):
    """A durable prepared binding cannot be recovered exactly and safely."""


@dataclass(slots=True)
class _PreparedFile:
    _owner: object = field(repr=False)
    _binding: PreparedCandidateBinding = field(repr=False)
    _candidate: EncryptedObjectCandidate = field(repr=False)
    _temporary: Path | None = field(repr=False)
    _durably_committed: bool = field(default=False, repr=False)
    _durable_commit: DurableCandidateBindingCommit | None = field(
        default=None, repr=False
    )
    _finished: bool = field(default=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def binding(self) -> PreparedCandidateBinding:
        return self._binding

    def __repr__(self) -> str:
        return f"<PreparedCasCandidate {self._binding.ciphertext_sha256}>"


class FileSystemCAS:
    """Install complete envelopes by SHA-256 with an atomic no-replace link."""

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path):
            raise TypeError("CAS root must be a pathlib.Path")
        self._root = root

    @staticmethod
    def relative_path(ciphertext_sha256: str) -> Path:
        if (
            not isinstance(ciphertext_sha256, str)
            or _SHA256_RE.fullmatch(ciphertext_sha256) is None
        ):
            raise CasIntegrityError(
                "CAS address must be a lowercase hexadecimal SHA-256"
            )
        return Path(
            "sha256",
            ciphertext_sha256[:2],
            f"{ciphertext_sha256}.eva2xo",
        )

    def _target(self, ciphertext_sha256: str) -> tuple[Path, Path]:
        relative = self.relative_path(ciphertext_sha256)
        return relative, self._root / relative

    @staticmethod
    def _flush_directory(path: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @classmethod
    def _ensure_directory(cls, path: Path) -> None:
        missing: list[Path] = []
        cursor = path
        while True:
            try:
                metadata = os.lstat(cursor)
            except FileNotFoundError:
                missing.append(cursor)
                parent = cursor.parent
                if parent == cursor:
                    raise CasIntegrityError("CAS directory has no existing ancestor")
                cursor = parent
                continue
            if not stat.S_ISDIR(metadata.st_mode):
                raise CasIntegrityError("CAS directory path is not a real directory")
            break
        for directory in reversed(missing):
            try:
                os.mkdir(directory, mode=0o750)
            except FileExistsError:
                metadata = os.lstat(directory)
                if not stat.S_ISDIR(metadata.st_mode):
                    raise CasIntegrityError(
                        "CAS directory path is not a real directory"
                    )
            else:
                # Persist each newly created namespace edge, not only the final
                # content-addressed file name.
                cls._flush_directory(directory.parent)

    @staticmethod
    def _write_all(descriptor: int, data: bytes) -> None:
        view = memoryview(data)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:  # pragma: no cover - defensive OS contract guard
                raise CasIntegrityError("temporary CAS write made no progress")
            written += count

    def read(self, ciphertext_sha256: str) -> bytes:
        """Open only the derived final path and verify its complete digest."""

        _, target = self._target(ciphertext_sha256)
        try:
            metadata = os.lstat(target)
        except FileNotFoundError as exc:
            raise CasIntegrityError("content-addressed object is not observed") from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise CasIntegrityError("CAS entry is not a regular immutable file")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(target, flags)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.EMLINK}:
                raise CasIntegrityError(
                    "CAS entry is not a regular immutable file"
                ) from exc
            raise CasIntegrityError(
                "content-addressed object cannot be opened"
            ) from exc
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise CasIntegrityError("CAS entry is not a regular immutable file")
            chunks: list[bytes] = []
            remaining = _MAXIMUM_STORED_ENVELOPE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(65_536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        finally:
            os.close(descriptor)
        envelope = b"".join(chunks)
        if len(envelope) > _MAXIMUM_STORED_ENVELOPE_BYTES:
            raise CasIntegrityError("CAS envelope exceeds the sealed size limit")
        if hashlib.sha256(envelope).hexdigest() != ciphertext_sha256:
            raise CasIntegrityError("CAS envelope SHA-256 differs from its address")
        return envelope

    @staticmethod
    def _validate_candidate(candidate: EncryptedObjectCandidate) -> None:
        if not isinstance(candidate, EncryptedObjectCandidate):
            raise TypeError("CAS accepts only an encrypted candidate")
        if (
            hashlib.sha256(candidate.envelope).hexdigest()
            != candidate.ciphertext_sha256
        ):
            raise CasIntegrityError("candidate envelope SHA-256 differs")
        if len(candidate.envelope) != candidate.stored_envelope_byte_length:
            raise CasIntegrityError("candidate envelope length differs")

    @staticmethod
    def _binding_for_temp_name(
        candidate: EncryptedObjectCandidate, temp_name: str
    ) -> PreparedCandidateBinding:
        if (
            not isinstance(temp_name, str)
            or not temp_name.startswith(".eva-candidate-")
            or Path(temp_name).name != temp_name
        ):
            raise CasRecoveryError("prepared CAS temp name is not canonical")
        try:
            encoded_name = temp_name.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise CasRecoveryError(
                "prepared CAS temp name is not canonical UTF-8"
            ) from exc
        return PreparedCandidateBinding(
            binding=candidate.binding,
            ciphertext_sha256=candidate.ciphertext_sha256,
            plaintext_sha256=candidate.plaintext_sha256,
            plaintext_byte_length=candidate.plaintext_byte_length,
            stored_envelope_byte_length=candidate.stored_envelope_byte_length,
            temp_name_sha256=hashlib.sha256(encoded_name).hexdigest(),
        )

    @staticmethod
    def _validate_recovery_envelope(candidate: EncryptedObjectCandidate) -> None:
        try:
            inspected = envelope_metadata(candidate.envelope)
        except (ObjectEnvelopeError, TypeError, ValueError) as exc:
            raise CasRecoveryError(
                "expected candidate envelope is structurally invalid"
            ) from exc
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
        )
        for actual, expected, label in comparisons:
            if actual != expected:
                raise CasRecoveryError(
                    f"expected candidate {label} differs from envelope"
                )

    @staticmethod
    def _validate_durable_binding(
        candidate: EncryptedObjectCandidate,
        durable_binding: PreparedCandidateBinding,
    ) -> None:
        if not isinstance(durable_binding, PreparedCandidateBinding):
            raise CasRecoveryError(
                "exact durable prepared binding is required for recovery"
            )
        comparisons = (
            (durable_binding.binding, candidate.binding, "binding"),
            (
                durable_binding.ciphertext_sha256,
                candidate.ciphertext_sha256,
                "ciphertext SHA-256",
            ),
            (
                durable_binding.plaintext_sha256,
                candidate.plaintext_sha256,
                "plaintext SHA-256",
            ),
            (
                durable_binding.plaintext_byte_length,
                candidate.plaintext_byte_length,
                "plaintext length",
            ),
            (
                durable_binding.stored_envelope_byte_length,
                candidate.stored_envelope_byte_length,
                "stored envelope length",
            ),
        )
        for actual, expected, label in comparisons:
            if actual != expected:
                raise CasRecoveryError(
                    f"durable prepared {label} differs from expected candidate"
                )

    @staticmethod
    def _read_recovery_temp(
        temporary: Path, expected_length: int
    ) -> tuple[bytes, os.stat_result]:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(temporary, flags)
        except OSError as exc:
            raise CasRecoveryError(
                "prepared CAS temp cannot be opened without following links"
            ) from exc
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise CasRecoveryError("prepared CAS temp is not a regular file")
            try:
                named = os.lstat(temporary)
            except FileNotFoundError as exc:
                raise CasRecoveryError(
                    "prepared CAS temp disappeared during recovery"
                ) from exc
            if (
                not stat.S_ISREG(named.st_mode)
                or named.st_dev != opened.st_dev
                or named.st_ino != opened.st_ino
            ):
                raise CasRecoveryError("prepared CAS temp name changed during recovery")
            chunks: list[bytes] = []
            remaining = expected_length + 1
            while remaining:
                chunk = os.read(descriptor, min(65_536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
            if (
                after.st_dev != opened.st_dev
                or after.st_ino != opened.st_ino
                or after.st_size != opened.st_size
            ):
                raise CasRecoveryError(
                    "prepared CAS temp changed while being recovered"
                )
        finally:
            os.close(descriptor)
        return b"".join(chunks), after

    @staticmethod
    def _validate_recovery_link_count(opened: os.stat_result, target: Path) -> None:
        if opened.st_nlink == 1:
            return
        if opened.st_nlink == 2:
            try:
                final = os.lstat(target)
            except FileNotFoundError:
                final = None
            if (
                final is not None
                and stat.S_ISREG(final.st_mode)
                and final.st_dev == opened.st_dev
                and final.st_ino == opened.st_ino
            ):
                # Recovery after the no-replace link itself succeeded but the
                # process stopped before readback/temp cleanup.
                return
        raise CasRecoveryError("prepared CAS temp has multiple unexpected links")

    def prepare(self, candidate: EncryptedObjectCandidate) -> _PreparedFile:
        """Write and fsync only an attempt-scoped non-final temporary file."""

        self._validate_candidate(candidate)
        _, target = self._target(candidate.ciphertext_sha256)
        self._ensure_directory(target.parent)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".eva-candidate-", dir=target.parent
        )
        temporary = Path(temporary_name)
        try:
            try:
                self._write_all(descriptor, candidate.envelope)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._flush_directory(target.parent)
            binding = self._binding_for_temp_name(candidate, temporary.name)
        except BaseException:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise
        return _PreparedFile(
            _owner=self,
            _binding=binding,
            _candidate=candidate,
            _temporary=temporary,
        )

    def recover_prepared(
        self,
        candidate: EncryptedObjectCandidate,
        durable_binding: PreparedCandidateBinding,
        commit: DurableCandidateBindingCommit,
    ) -> _PreparedFile:
        """Recover exactly one DB-bound temp after a process restart.

        The durable receipt commits to all candidate fields and the hash of the
        exact temporary basename. Unbound or ambiguous files are never removed.
        """

        try:
            self._validate_candidate(candidate)
        except (CasIntegrityError, TypeError) as exc:
            raise CasRecoveryError("expected recovery candidate is invalid") from exc
        self._validate_recovery_envelope(candidate)
        self._validate_durable_binding(candidate, durable_binding)
        if not isinstance(commit, DurableCandidateBindingCommit):
            raise CasRecoveryError(
                "exact durable candidate binding receipt is required"
            )
        if commit.prepared_binding_sha256 != durable_binding.canonical_sha256:
            raise CasRecoveryError(
                "durable receipt differs from the stored prepared binding"
            )

        _, target = self._target(candidate.ciphertext_sha256)
        directory = target.parent
        try:
            directory_metadata = os.lstat(directory)
        except FileNotFoundError:
            return self._recover_final_only(candidate, durable_binding, commit)
        if not stat.S_ISDIR(directory_metadata.st_mode):
            raise CasRecoveryError("prepared digest path is not a real directory")

        matches: list[tuple[Path, PreparedCandidateBinding]] = []
        try:
            with os.scandir(directory) as iterator:
                entries = list(iterator)
        except OSError as exc:
            raise CasRecoveryError("prepared digest directory cannot be read") from exc
        for entry in entries:
            if not entry.name.startswith(".eva-candidate-"):
                continue
            binding = self._binding_for_temp_name(candidate, entry.name)
            if binding.temp_name_sha256 == durable_binding.temp_name_sha256:
                matches.append((directory / entry.name, binding))
        if len(matches) == 0:
            return self._recover_final_only(candidate, durable_binding, commit)
        if len(matches) != 1:
            raise CasRecoveryError(
                "multiple prepared temps match the durable binding receipt"
            )

        temporary, binding = matches[0]
        if binding != durable_binding:
            raise CasRecoveryError(
                "prepared temp binding differs from the durable binding"
            )
        temporary_bytes, opened = self._read_recovery_temp(
            temporary, candidate.stored_envelope_byte_length
        )
        self._validate_recovery_link_count(opened, target)
        if len(temporary_bytes) != candidate.stored_envelope_byte_length:
            raise CasRecoveryError("prepared CAS temp length differs from candidate")
        if hashlib.sha256(temporary_bytes).hexdigest() != candidate.ciphertext_sha256:
            raise CasRecoveryError("prepared CAS temp digest differs from candidate")
        if temporary_bytes != candidate.envelope:
            raise CasRecoveryError("prepared CAS temp bytes differ from candidate")

        # Inspect the bytes read from disk as well as the caller's expected
        # envelope. This prevents a metadata-only reservation from becoming a
        # recovery capability.
        recovered_candidate = EncryptedObjectCandidate(
            binding=candidate.binding,
            envelope=temporary_bytes,
            canonical_aad=candidate.canonical_aad,
            ciphertext_sha256=hashlib.sha256(temporary_bytes).hexdigest(),
            plaintext_sha256=candidate.plaintext_sha256,
            plaintext_byte_length=candidate.plaintext_byte_length,
            stored_envelope_byte_length=len(temporary_bytes),
        )
        self._validate_recovery_envelope(recovered_candidate)
        return _PreparedFile(
            _owner=self,
            _binding=binding,
            _candidate=recovered_candidate,
            _temporary=temporary,
            _durably_committed=True,
            _durable_commit=commit,
        )

    def _recover_final_only(
        self,
        candidate: EncryptedObjectCandidate,
        durable_binding: PreparedCandidateBinding,
        commit: DurableCandidateBindingCommit,
    ) -> _PreparedFile:
        """Recover the post-temp-cleanup crash window without mutating CAS."""

        try:
            final_envelope = self.read(candidate.ciphertext_sha256)
        except (CasIntegrityError, OSError) as exc:
            raise CasRecoveryError(
                "no prepared temp exists and exact final CAS readback is unavailable"
            ) from exc
        if final_envelope != candidate.envelope:
            raise CasRecoveryError(
                "no prepared temp exists and final CAS bytes differ from candidate"
            )
        recovered_candidate = EncryptedObjectCandidate(
            binding=candidate.binding,
            envelope=final_envelope,
            canonical_aad=candidate.canonical_aad,
            ciphertext_sha256=hashlib.sha256(final_envelope).hexdigest(),
            plaintext_sha256=candidate.plaintext_sha256,
            plaintext_byte_length=candidate.plaintext_byte_length,
            stored_envelope_byte_length=len(final_envelope),
        )
        self._validate_recovery_envelope(recovered_candidate)
        return _PreparedFile(
            _owner=self,
            _binding=durable_binding,
            _candidate=recovered_candidate,
            _temporary=None,
            _durably_committed=True,
            _durable_commit=commit,
        )

    def _require_prepared(self, prepared: object) -> _PreparedFile:
        if not isinstance(prepared, _PreparedFile) or prepared._owner is not self:
            raise CasStateError("prepared CAS capability belongs to another store")
        return prepared

    def install_prepared_and_readback(
        self,
        prepared: object,
        commit: DurableCandidateBindingCommit,
    ) -> CasReadback:
        """After durable binding commit, no-replace link and independently read."""

        item = self._require_prepared(prepared)
        with item._lock:
            if item._finished:
                raise CasStateError("prepared CAS capability is already finished")
            if not isinstance(commit, DurableCandidateBindingCommit):
                raise CasStateError("durable candidate binding commit is required")
            if commit.prepared_binding_sha256 != item.binding.canonical_sha256:
                raise CasStateError("durable commit names another prepared binding")
            if item._durable_commit is not None and commit != item._durable_commit:
                raise CasStateError("durable commit differs from the recovered receipt")
            item._durably_committed = True
            item._durable_commit = commit
            candidate = item._candidate
            relative, target = self._target(candidate.ciphertext_sha256)
            temporary = item._temporary
            if temporary is None:
                envelope = self.read(candidate.ciphertext_sha256)
                if envelope != candidate.envelope:
                    raise CasIntegrityError(
                        "final-only CAS recovery bytes differ from candidate"
                    )
                final_candidate = EncryptedObjectCandidate(
                    binding=candidate.binding,
                    envelope=envelope,
                    canonical_aad=candidate.canonical_aad,
                    ciphertext_sha256=hashlib.sha256(envelope).hexdigest(),
                    plaintext_sha256=candidate.plaintext_sha256,
                    plaintext_byte_length=candidate.plaintext_byte_length,
                    stored_envelope_byte_length=len(envelope),
                )
                self._validate_recovery_envelope(final_candidate)
                item._finished = True
                return CasReadback(
                    status=CasInstallStatus.ALREADY_SAME,
                    ciphertext_sha256=final_candidate.ciphertext_sha256,
                    envelope=envelope,
                    path=relative,
                )
            try:
                temporary_bytes = temporary.read_bytes()
            except FileNotFoundError as exc:
                raise CasIntegrityError(
                    "prepared CAS temp file is not observed"
                ) from exc
            if temporary_bytes != candidate.envelope:
                raise CasIntegrityError("prepared CAS temp bytes differ from candidate")
            try:
                os.link(temporary, target, follow_symlinks=False)
                created = True
                self._flush_directory(target.parent)
            except FileExistsError:
                created = False

            envelope = self.read(candidate.ciphertext_sha256)
            if envelope != candidate.envelope:
                raise CasIntegrityError(
                    "existing CAS entry is not byte-identical to the candidate"
                )
            try:
                temporary.unlink()
            except FileNotFoundError as exc:
                raise CasIntegrityError(
                    "prepared CAS temp disappeared before cleanup"
                ) from exc
            self._flush_directory(target.parent)
            item._finished = True
            return CasReadback(
                status=(
                    CasInstallStatus.CREATED
                    if created
                    else CasInstallStatus.ALREADY_SAME
                ),
                ciphertext_sha256=candidate.ciphertext_sha256,
                envelope=envelope,
                path=relative,
            )

    def discard_uncommitted(self, prepared: object) -> bool:
        """Explicitly remove only a temp file with no durable binding receipt."""

        item = self._require_prepared(prepared)
        with item._lock:
            if item._durably_committed:
                raise CasStateError(
                    "durably committed candidate temp must remain for recovery"
                )
            if item._finished:
                return False
            if item._temporary is None:
                raise CasStateError("prepared CAS temp is not available for cleanup")
            try:
                item._temporary.unlink()
                removed = True
            except FileNotFoundError:
                removed = False
            self._flush_directory(item._temporary.parent)
            item._finished = True
            return removed


__all__ = [
    "CasError",
    "CasIntegrityError",
    "CasRecoveryError",
    "CasStateError",
    "FileSystemCAS",
]
