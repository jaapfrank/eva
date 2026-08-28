"""One-time, owner-only secret bundles for Eva's local installer.

The browser-facing layer may write an opaque secret once.  This store returns
only a fixed reference and presence state; it never returns, hashes, logs, or
serializes the secret.  Rotation and removal deliberately remain separate
operator workflows.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import fcntl
import os
from pathlib import Path
import re
import stat
from typing import Callable, Literal

from eva.contracts.canonical_json import canonical_json_bytes, require_canonical_bytes
from eva.operations.identity_pack_lifecycle import IdentityPackLifecycleError
from eva.operations.installer_identity_storage import read_private_artifact


SecretSlotState = Literal["missing", "present", "needs_help"]
SECRET_SLOTS = ("model_provider", "telegram", "home_assistant", "email")
_SLOT_SET = frozenset(SECRET_SLOTS)
_TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_MIN_SECRET_BYTES = 16
_MAX_SECRET_BYTES = 8192


class InstallerSecretStoreError(ValueError):
    """Raised when a private installer secret bundle cannot be trusted."""


class InstallerSecretInputError(InstallerSecretStoreError):
    """Raised when a browser-supplied opaque secret is invalid."""


@dataclass(frozen=True, slots=True)
class InstallerSecretSlotStatus:
    slot_id: str
    state: SecretSlotState
    reference: str | None
    created_at: str | None


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp(value: object) -> str:
    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        raise InstallerSecretStoreError("installer secret timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InstallerSecretStoreError(
            "installer secret timestamp is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise InstallerSecretStoreError("installer secret timestamp must be UTC")
    return value


def _slot(value: object) -> str:
    if not isinstance(value, str) or value not in _SLOT_SET:
        raise InstallerSecretInputError("installer secret slot is invalid")
    return value


def _secret_bytes(value: object) -> bytes:
    if not isinstance(value, str) or not value.isascii():
        raise InstallerSecretInputError("installer secret must be opaque ASCII")
    raw = value.encode("ascii")
    if not _MIN_SECRET_BYTES <= len(raw) <= _MAX_SECRET_BYTES or any(
        character <= 0x20 or character == 0x7F for character in raw
    ):
        raise InstallerSecretInputError("installer secret is invalid")
    return raw


def _reference(slot_id: str) -> str:
    return f"installer-secret://{slot_id}/v1"


def _private_directory(path: Path, *, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise InstallerSecretStoreError(f"{label} is unavailable") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o077
    ):
        raise InstallerSecretStoreError(f"{label} must be an owner-only directory")


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

    @contextmanager
    def _lock(self):
        path = self.workspace / ".secret-store.lock"
        descriptor: int | None = None
        try:
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or metadata.st_mode & 0o077
                or metadata.st_nlink != 1
            ):
                raise OSError("secret lock is not a private regular file")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as exc:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            raise InstallerSecretStoreError(
                "installer secret lock is unavailable"
            ) from exc
        assert descriptor is not None
        try:
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _status(self, slot_id: str) -> InstallerSecretSlotStatus:
        bundle = self._bundle(slot_id)
        if not bundle.exists() and not bundle.is_symlink():
            return InstallerSecretSlotStatus(slot_id, "missing", None, None)
        _private_directory(bundle, label="installer secret bundle")
        entries = {item.name for item in bundle.iterdir()}
        allowed = {"complete", "metadata.json", "secret.bin"}
        if not entries <= allowed:
            raise InstallerSecretStoreError(
                "installer secret bundle contains unknown artifacts"
            )
        if entries != allowed:
            return InstallerSecretSlotStatus(slot_id, "needs_help", None, None)
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
        if (
            set(metadata)
            != {
                "contract",
                "created_at",
                "reference",
                "secret_format",
                "slot_id",
            }
            or metadata.get("contract") != "EvaInstallerSecretMetadata/v1"
        ):
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
        )

    def status(self) -> tuple[InstallerSecretSlotStatus, ...]:
        with self._lock():
            return tuple(self._status(slot_id) for slot_id in SECRET_SLOTS)

    def store(self, *, slot_id: object, secret: object) -> InstallerSecretSlotStatus:
        checked_slot = _slot(slot_id)
        raw = _secret_bytes(secret)
        with self._lock():
            current = self._status(checked_slot)
            if current.state != "missing":
                raise InstallerSecretStoreError("installer secret slot is not empty")
            bundle = self._bundle(checked_slot)
            created = False
            try:
                bundle.mkdir(mode=0o700)
                created = True
                created_at = _timestamp(self.clock())
                metadata = canonical_json_bytes(
                    {
                        "contract": "EvaInstallerSecretMetadata/v1",
                        "created_at": created_at,
                        "reference": _reference(checked_slot),
                        "secret_format": "opaque_ascii_v1",
                        "slot_id": checked_slot,
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
                parent = os.open(self.workspace, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
            except (OSError, InstallerSecretStoreError) as exc:
                if created:
                    for name in ("complete", "metadata.json", "secret.bin"):
                        try:
                            (bundle / name).unlink()
                        except FileNotFoundError:
                            pass
                        except OSError:
                            break
                    try:
                        bundle.rmdir()
                    except OSError:
                        pass
                if isinstance(exc, InstallerSecretStoreError):
                    raise
                raise InstallerSecretStoreError(
                    "installer secret bundle write failed"
                ) from exc
            return self._status(checked_slot)

    def read_secret(self, *, slot_id: object) -> bytes:
        """Return secret bytes only to an operator-side provider verifier."""

        checked_slot = _slot(slot_id)
        with self._lock():
            status = self._status(checked_slot)
            if status.state != "present":
                raise InstallerSecretStoreError("installer secret is not available")
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
            return raw


__all__ = [
    "SECRET_SLOTS",
    "InstallerSecretInputError",
    "InstallerSecretSlotStatus",
    "InstallerSecretStore",
    "InstallerSecretStoreError",
]
