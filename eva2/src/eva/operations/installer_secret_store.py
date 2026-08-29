"""One-time, owner-only secret bundles for Eva's local installer.

The browser-facing layer may write an opaque secret once.  This store returns
only a fixed reference and presence state; it never returns, hashes, logs, or
serializes the secret. Rotation and removal are operator-only operations: the
caller must durably invalidate the capability before this store swaps or
removes bytes. Interrupted mutations remain visible as ``needs_help``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import stat
from typing import Callable, Literal

from eva.contracts.canonical_json import canonical_json_bytes, require_canonical_bytes
from eva.operations.identity_pack_lifecycle import IdentityPackLifecycleError
from eva.operations.installer_identity_storage import (
    atomic_write_private,
    read_private_artifact,
)
from eva.operations.installer_safety import (
    owner_only_lock,
    require_owner_only_directory,
    require_utc_timestamp,
)


SecretSlotState = Literal["missing", "present", "needs_help"]
SECRET_SLOTS = ("model_provider", "telegram", "home_assistant", "email")
_SLOT_SET = frozenset(SECRET_SLOTS)
_MIN_SECRET_BYTES = 16
_MAX_SECRET_BYTES = 8192


class InstallerSecretStoreError(ValueError):
    """Raised when a private installer secret bundle cannot be trusted."""


class InstallerSecretInputError(InstallerSecretStoreError):
    """Raised when a browser-supplied opaque secret is invalid."""


class InstallerSecretNotAvailableError(InstallerSecretStoreError):
    """Raised when a valid secret bundle has not been retained for a slot."""


@dataclass(frozen=True, slots=True)
class InstallerSecretSlotStatus:
    slot_id: str
    state: SecretSlotState
    reference: str | None
    created_at: str | None
    generation: int | None


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp(value: object) -> str:
    return require_utc_timestamp(
        value,
        error=InstallerSecretStoreError,
        invalid_message="installer secret timestamp is invalid",
        non_utc_message="installer secret timestamp must be UTC",
    )


def _slot(value: object) -> str:
    if not isinstance(value, str) or value not in _SLOT_SET:
        raise InstallerSecretInputError("installer secret slot is invalid")
    return value


def _secret_bytes(value: object) -> bytes:
    if isinstance(value, str):
        if not value.isascii():
            raise InstallerSecretInputError("installer secret must be opaque ASCII")
        raw = value.encode("ascii")
    elif isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        if any(character > 0x7F for character in raw):
            raise InstallerSecretInputError("installer secret must be opaque ASCII")
    else:
        raise InstallerSecretInputError("installer secret must be opaque ASCII")
    if not _MIN_SECRET_BYTES <= len(raw) <= _MAX_SECRET_BYTES or any(
        character <= 0x20 or character == 0x7F for character in raw
    ):
        raise InstallerSecretInputError("installer secret is invalid")
    return raw


def _reference(slot_id: str) -> str:
    return f"installer-secret://{slot_id}/v1"


def _private_directory(path: Path, *, label: str) -> None:
    require_owner_only_directory(
        path,
        error=InstallerSecretStoreError,
        unavailable_message=f"{label} is unavailable",
        invalid_message=f"{label} must be an owner-only directory",
    )


def _secret_metadata(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise InstallerSecretStoreError("installer secret file is unavailable") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o077
        or metadata.st_nlink != 1
        or not _MIN_SECRET_BYTES <= metadata.st_size <= _MAX_SECRET_BYTES
    ):
        raise InstallerSecretStoreError(
            "installer secret file is not a private regular file"
        )
    return metadata


def _write_private(path: Path, raw: bytes) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = None
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise InstallerSecretStoreError("installer secret write failed") from exc


@dataclass(frozen=True, slots=True)
class InstallerSecretStore:
    workspace: Path
    clock: Callable[[], str] = _utc_now

    def __post_init__(self) -> None:
        _private_directory(self.workspace, label="installer secret workspace")

    def _bundle(self, slot_id: str) -> Path:
        return self.workspace / f"slot-{slot_id}"

    def _rotation_next(self, slot_id: str) -> Path:
        return self.workspace / f"slot-{slot_id}.rotation-next"

    def _rotation_previous(self, slot_id: str) -> Path:
        return self.workspace / f"slot-{slot_id}.rotation-previous"

    def _removing(self, slot_id: str) -> Path:
        return self.workspace / f"slot-{slot_id}.removing"

    def _generation_path(self, slot_id: str) -> Path:
        return self.workspace / f"slot-{slot_id}.generation.json"

    def _last_generation(self, slot_id: str) -> int:
        path = self._generation_path(slot_id)
        if not path.exists() and not path.is_symlink():
            return 0
        try:
            payload = require_canonical_bytes(
                read_private_artifact(path, label="installer secret generation")
            )
        except (IdentityPackLifecycleError, ValueError) as exc:
            raise InstallerSecretStoreError(
                "installer secret generation ledger is unavailable"
            ) from exc
        generation = payload.get("generation")
        if (
            set(payload) != {"contract", "generation", "slot_id"}
            or payload.get("contract") != "EvaInstallerSecretGeneration/v1"
            or payload.get("slot_id") != slot_id
            or not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 1
        ):
            raise InstallerSecretStoreError(
                "installer secret generation ledger is invalid"
            )
        return generation

    def _retain_generation(self, slot_id: str, generation: int) -> None:
        try:
            atomic_write_private(
                self._generation_path(slot_id),
                canonical_json_bytes(
                    {
                        "contract": "EvaInstallerSecretGeneration/v1",
                        "generation": generation,
                        "slot_id": slot_id,
                    }
                ),
            )
        except IdentityPackLifecycleError as exc:
            raise InstallerSecretStoreError(
                "installer secret generation ledger write failed"
            ) from exc

    def _lock(self):
        return owner_only_lock(
            self.workspace / ".secret-store.lock",
            error=InstallerSecretStoreError,
            unavailable_message="installer secret lock is unavailable",
        )

    def _read_bundle(self, slot_id: str, bundle: Path) -> InstallerSecretSlotStatus:
        if not bundle.exists() and not bundle.is_symlink():
            return InstallerSecretSlotStatus(slot_id, "missing", None, None, None)
        _private_directory(bundle, label="installer secret bundle")
        entries = {item.name for item in bundle.iterdir()}
        allowed = {"complete", "metadata.json", "secret.bin"}
        if not entries <= allowed:
            raise InstallerSecretStoreError(
                "installer secret bundle contains unknown artifacts"
            )
        if entries != allowed:
            return InstallerSecretSlotStatus(slot_id, "needs_help", None, None, None)
        try:
            complete = read_private_artifact(
                bundle / "complete", label="installer secret completion marker"
            )
            metadata_raw = read_private_artifact(
                bundle / "metadata.json", label="installer secret metadata"
            )
        except IdentityPackLifecycleError as exc:
            raise InstallerSecretStoreError(
                "installer secret bundle is unavailable"
            ) from exc
        if complete != b"complete\n":
            raise InstallerSecretStoreError(
                "installer secret completion marker is invalid"
            )
        _secret_metadata(bundle / "secret.bin")
        try:
            metadata = require_canonical_bytes(metadata_raw)
        except ValueError as exc:
            raise InstallerSecretStoreError(
                "installer secret metadata is invalid"
            ) from exc
        v1_fields = {
            "contract",
            "created_at",
            "reference",
            "secret_format",
            "slot_id",
        }
        v2_fields = {*v1_fields, "generation"}
        if (
            set(metadata) == v1_fields
            and metadata.get("contract") == "EvaInstallerSecretMetadata/v1"
        ):
            generation = 1
        elif (
            set(metadata) == v2_fields
            and metadata.get("contract") == "EvaInstallerSecretMetadata/v2"
        ):
            raw_generation = metadata.get("generation")
            if (
                not isinstance(raw_generation, int)
                or isinstance(raw_generation, bool)
                or raw_generation < 1
            ):
                raise InstallerSecretStoreError(
                    "installer secret generation is invalid"
                )
            generation = raw_generation
        else:
            raise InstallerSecretStoreError("installer secret metadata is invalid")
        if (
            metadata.get("slot_id") != slot_id
            or metadata.get("reference") != _reference(slot_id)
            or metadata.get("secret_format") != "opaque_ascii_v1"
        ):
            raise InstallerSecretStoreError("installer secret metadata differs")
        return InstallerSecretSlotStatus(
            slot_id=slot_id,
            state="present",
            reference=_reference(slot_id),
            created_at=_timestamp(metadata.get("created_at")),
            generation=generation,
        )

    def _status(self, slot_id: str) -> InstallerSecretSlotStatus:
        auxiliaries = (
            self._rotation_next(slot_id),
            self._rotation_previous(slot_id),
            self._removing(slot_id),
        )
        if any(path.exists() or path.is_symlink() for path in auxiliaries):
            return InstallerSecretSlotStatus(slot_id, "needs_help", None, None, None)
        return self._read_bundle(slot_id, self._bundle(slot_id))

    @staticmethod
    def _remove_bundle_best_effort(bundle: Path) -> None:
        for name in ("complete", "metadata.json", "secret.bin"):
            try:
                (bundle / name).unlink()
            except FileNotFoundError:
                pass
            except OSError:
                return
        try:
            bundle.rmdir()
        except OSError:
            pass

    def _sync_workspace(self) -> None:
        try:
            descriptor = os.open(self.workspace, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise InstallerSecretStoreError(
                "installer secret workspace sync failed"
            ) from exc

    def _write_bundle(
        self, *, bundle: Path, slot_id: str, raw: bytes, generation: int
    ) -> None:
        created = False
        try:
            bundle.mkdir(mode=0o700)
            created = True
            metadata = canonical_json_bytes(
                {
                    "contract": "EvaInstallerSecretMetadata/v2",
                    "created_at": _timestamp(self.clock()),
                    "generation": generation,
                    "reference": _reference(slot_id),
                    "secret_format": "opaque_ascii_v1",
                    "slot_id": slot_id,
                }
            )
            _write_private(bundle / "secret.bin", raw)
            _write_private(bundle / "metadata.json", metadata)
            directory = os.open(bundle, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            _write_private(bundle / "complete", b"complete\n")
            directory = os.open(bundle, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self._sync_workspace()
        except (OSError, InstallerSecretStoreError) as exc:
            if created:
                self._remove_bundle_best_effort(bundle)
            if isinstance(exc, InstallerSecretStoreError):
                raise
            raise InstallerSecretStoreError(
                "installer secret bundle write failed"
            ) from exc

    def _remove_bundle(self, bundle: Path) -> None:
        _private_directory(bundle, label="installer secret retired bundle")
        if not {item.name for item in bundle.iterdir()} <= {
            "complete",
            "metadata.json",
            "secret.bin",
        }:
            raise InstallerSecretStoreError(
                "installer secret retired bundle contains unknown artifacts"
            )
        try:
            for name in ("secret.bin", "metadata.json", "complete"):
                try:
                    (bundle / name).unlink()
                except FileNotFoundError:
                    pass
            bundle.rmdir()
        except OSError as exc:
            raise InstallerSecretStoreError(
                "installer secret retired bundle removal failed"
            ) from exc
        self._sync_workspace()

    def status(self) -> tuple[InstallerSecretSlotStatus, ...]:
        with self._lock():
            return tuple(self._status(slot_id) for slot_id in SECRET_SLOTS)

    def validate_secret(self, *, secret: object) -> None:
        """Validate replacement input before a lifecycle invalidates authority."""

        _secret_bytes(secret)

    def store(self, *, slot_id: object, secret: object) -> InstallerSecretSlotStatus:
        checked_slot = _slot(slot_id)
        raw = _secret_bytes(secret)
        with self._lock():
            current = self._status(checked_slot)
            if current.state != "missing":
                raise InstallerSecretStoreError("installer secret slot is not empty")
            bundle = self._bundle(checked_slot)
            generation = self._last_generation(checked_slot) + 1
            self._write_bundle(
                bundle=bundle,
                slot_id=checked_slot,
                raw=raw,
                generation=generation,
            )
            self._retain_generation(checked_slot, generation)
            return self._status(checked_slot)

    def rotate(
        self,
        *,
        slot_id: object,
        secret: object,
        invalidate: Callable[[str, int, int], None],
    ) -> InstallerSecretSlotStatus:
        """Replace one secret after the caller durably invalidates the slot."""

        checked_slot = _slot(slot_id)
        raw = _secret_bytes(secret)
        if not callable(invalidate):
            raise InstallerSecretStoreError("installer secret invalidator is invalid")
        with self._lock():
            current = self._status(checked_slot)
            if current.state != "present" or current.generation is None:
                raise InstallerSecretStoreError(
                    "installer secret slot is not ready for rotation"
                )
            next_generation = current.generation + 1
            staged = self._rotation_next(checked_slot)
            previous = self._rotation_previous(checked_slot)
            bundle = self._bundle(checked_slot)
            self._write_bundle(
                bundle=staged,
                slot_id=checked_slot,
                raw=raw,
                generation=next_generation,
            )
            try:
                invalidate(checked_slot, current.generation, next_generation)
            except Exception as exc:
                self._remove_bundle_best_effort(staged)
                raise InstallerSecretStoreError(
                    "installer secret rotation was not authorized"
                ) from exc
            try:
                os.replace(bundle, previous)
                os.replace(staged, bundle)
                self._sync_workspace()
                self._retain_generation(checked_slot, next_generation)
                self._remove_bundle(previous)
            except (OSError, InstallerSecretStoreError) as exc:
                raise InstallerSecretStoreError(
                    "installer secret rotation needs recovery"
                ) from exc
            return self._status(checked_slot)

    def remove(
        self,
        *,
        slot_id: object,
        invalidate: Callable[[str, int, None], None],
    ) -> InstallerSecretSlotStatus:
        """Remove one secret after the caller durably invalidates the slot."""

        checked_slot = _slot(slot_id)
        if not callable(invalidate):
            raise InstallerSecretStoreError("installer secret invalidator is invalid")
        with self._lock():
            current = self._status(checked_slot)
            if current.state != "present" or current.generation is None:
                raise InstallerSecretStoreError(
                    "installer secret slot is not ready for removal"
                )
            try:
                invalidate(checked_slot, current.generation, None)
            except Exception as exc:
                raise InstallerSecretStoreError(
                    "installer secret removal was not authorized"
                ) from exc
            removing = self._removing(checked_slot)
            try:
                self._retain_generation(checked_slot, current.generation)
                os.replace(self._bundle(checked_slot), removing)
                self._sync_workspace()
                self._remove_bundle(removing)
            except (OSError, InstallerSecretStoreError) as exc:
                raise InstallerSecretStoreError(
                    "installer secret removal needs recovery"
                ) from exc
            return self._status(checked_slot)

    def recover(
        self, *, slot_id: object, invalidated: object
    ) -> InstallerSecretSlotStatus:
        """Finish an interrupted mutation without accepting replacement bytes."""

        checked_slot = _slot(slot_id)
        if invalidated is not True:
            raise InstallerSecretStoreError(
                "installer secret recovery requires durable invalidation"
            )
        with self._lock():
            bundle = self._bundle(checked_slot)
            staged = self._rotation_next(checked_slot)
            previous = self._rotation_previous(checked_slot)
            removing = self._removing(checked_slot)

            def exists(path: Path) -> bool:
                return path.exists() or path.is_symlink()

            if exists(removing):
                if exists(bundle) or exists(staged) or exists(previous):
                    raise InstallerSecretStoreError(
                        "installer secret recovery state is ambiguous"
                    )
                self._remove_bundle(removing)
                return self._status(checked_slot)
            if exists(staged):
                staged_status = self._read_bundle(checked_slot, staged)
                if staged_status.generation is None:
                    raise InstallerSecretStoreError(
                        "installer secret recovery state is invalid"
                    )
                if exists(bundle) and not exists(previous):
                    current = self._read_bundle(checked_slot, bundle)
                    if current.generation != staged_status.generation - 1:
                        raise InstallerSecretStoreError(
                            "installer secret recovery generation differs"
                        )
                    os.replace(bundle, previous)
                if not exists(bundle) and exists(previous):
                    prior = self._read_bundle(checked_slot, previous)
                    if prior.generation != staged_status.generation - 1:
                        raise InstallerSecretStoreError(
                            "installer secret recovery generation differs"
                        )
                    os.replace(staged, bundle)
                else:
                    raise InstallerSecretStoreError(
                        "installer secret recovery state is ambiguous"
                    )
            if exists(previous):
                if not exists(bundle) or exists(staged):
                    raise InstallerSecretStoreError(
                        "installer secret recovery state is incomplete"
                    )
                active = self._read_bundle(checked_slot, bundle)
                prior = self._read_bundle(checked_slot, previous)
                if (
                    active.generation is None
                    or prior.generation is None
                    or active.generation != prior.generation + 1
                ):
                    raise InstallerSecretStoreError(
                        "installer secret recovery generation differs"
                    )
                assert active.generation is not None
                self._retain_generation(checked_slot, active.generation)
                self._remove_bundle(previous)
            self._sync_workspace()
            return self._status(checked_slot)

    def read_secret_with_status(
        self, *, slot_id: object
    ) -> tuple[InstallerSecretSlotStatus, bytes]:
        """Atomically bind returned bytes to their retained generation."""

        checked_slot = _slot(slot_id)
        with self._lock():
            status = self._status(checked_slot)
            if status.state != "present":
                raise InstallerSecretNotAvailableError(
                    "installer secret is not available"
                )
            try:
                raw = read_private_artifact(
                    self._bundle(checked_slot) / "secret.bin",
                    label="installer secret",
                )
            except IdentityPackLifecycleError as exc:
                raise InstallerSecretStoreError(
                    "installer secret is unavailable"
                ) from exc
            try:
                _secret_bytes(raw.decode("ascii", errors="strict"))
            except (UnicodeDecodeError, InstallerSecretInputError) as exc:
                raise InstallerSecretStoreError(
                    "installer secret content is invalid"
                ) from exc
            return status, raw

    def read_secret(self, *, slot_id: object) -> bytes:
        """Return secret bytes only to an operator-side provider verifier."""

        _, raw = self.read_secret_with_status(slot_id=slot_id)
        return raw


__all__ = [
    "SECRET_SLOTS",
    "InstallerSecretInputError",
    "InstallerSecretNotAvailableError",
    "InstallerSecretSlotStatus",
    "InstallerSecretStore",
    "InstallerSecretStoreError",
]
