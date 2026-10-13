"""Shared fail-closed filesystem and timestamp checks for installer workflows."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import fcntl
import hmac
import os
from pathlib import Path
import re
import stat
from typing import Generic, Literal, TypeVar

from eva.contracts.canonical_json import canonical_json_bytes, require_canonical_bytes
from eva.operations.identity_pack_lifecycle import IdentityPackLifecycleError
from eva.operations.installer_identity_storage import (
    atomic_write_private,
    read_private_artifact,
)


ErrorFactory = Callable[[str], Exception]
ValueT = TypeVar("ValueT")
TwoPhase = Literal["staged", "ready"]
_TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class InstallerTwoPhaseMessages:
    invalid: str
    digest_invalid: str
    retained_digest_differs: str
    expected_digest_differs: str
    state_differs: str
    history_invalid: str
    update_differs: str
    write_failed: str
    different_retained: str
    missing_staged: str
    confirmation_precedes: str


@dataclass(frozen=True, slots=True)
class InstallerTwoPhaseRecord(Generic[ValueT]):
    phase: TwoPhase
    revision: int
    digest: str
    value: ValueT
    updated_at: str


@dataclass(frozen=True, slots=True)
class InstallerTwoPhaseChange(Generic[ValueT]):
    record: InstallerTwoPhaseRecord[ValueT]
    already_in_phase: bool


def require_utc_timestamp(
    value: object,
    *,
    error: ErrorFactory,
    invalid_message: str,
    non_utc_message: str,
) -> str:
    """Return one strict second-resolution UTC timestamp or fail closed."""

    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        raise error(invalid_message)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise error(invalid_message) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise error(non_utc_message)
    return value


def require_owner_only_directory(
    path: Path,
    *,
    error: ErrorFactory,
    unavailable_message: str,
    invalid_message: str,
) -> None:
    """Require an existing, non-linked directory owned privately by this user."""

    try:
        metadata = path.lstat()
    except OSError as exc:
        raise error(unavailable_message) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o077
    ):
        raise error(invalid_message)


@contextmanager
def owner_only_lock(
    path: Path,
    *,
    error: ErrorFactory,
    unavailable_message: str,
) -> Iterator[None]:
    """Hold one owner-only, non-linked regular-file lock for a workflow."""

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
            raise OSError("installer lock is not a private regular file")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except OSError as exc:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise error(unavailable_message) from exc
    assert descriptor is not None
    try:
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@dataclass(frozen=True, slots=True)
class InstallerTwoPhaseJournal(Generic[ValueT]):
    """Canonical private staged-to-ready journal shared by installer choices."""

    path: Path
    contract: str
    value_field: str
    digest_field: str
    read_label: str
    decode: Callable[[object], ValueT]
    encode: Callable[[ValueT], dict[str, object]]
    value_digest: Callable[[ValueT], str]
    event_timestamp: Callable[[object], str]
    update_timestamp: Callable[[object], str]
    error: ErrorFactory
    messages: InstallerTwoPhaseMessages

    def require_digest(self, value: object) -> str:
        if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
            raise self.error(self.messages.digest_invalid)
        return value

    def load(self) -> InstallerTwoPhaseRecord[ValueT] | None:
        if not self.path.exists() and not self.path.is_symlink():
            return None
        try:
            payload = require_canonical_bytes(
                read_private_artifact(self.path, label=self.read_label)
            )
        except (IdentityPackLifecycleError, ValueError) as exc:
            raise self.error(self.messages.invalid) from exc
        if (
            set(payload)
            != {
                self.value_field,
                self.digest_field,
                "contract",
                "events",
                "phase",
                "revision",
                "updated_at",
            }
            or payload.get("contract") != self.contract
        ):
            raise self.error(self.messages.invalid)

        value = self.decode(payload.get(self.value_field))
        digest = payload.get(self.digest_field)
        phase = payload.get("phase")
        revision = payload.get("revision")
        events = payload.get("events")
        if (
            not isinstance(digest, str)
            or _SHA256_RE.fullmatch(digest) is None
            or not hmac.compare_digest(digest, self.value_digest(value))
        ):
            raise self.error(self.messages.retained_digest_differs)
        if (
            phase not in {"staged", "ready"}
            or revision not in {1, 2}
            or not isinstance(events, list)
            or len(events) != revision
        ):
            raise self.error(self.messages.state_differs)

        transitions = ["staged"] if phase == "staged" else ["staged", "confirmed"]
        previous_at = ""
        for index, event in enumerate(events, start=1):
            if not isinstance(event, dict) or set(event) != {
                "at",
                self.digest_field,
                "revision",
                "transition",
            }:
                raise self.error(self.messages.history_invalid)
            at = self.event_timestamp(event.get("at"))
            if (
                event.get("revision") != index
                or event.get("transition") != transitions[index - 1]
                or event.get(self.digest_field) != digest
                or (previous_at and at < previous_at)
            ):
                raise self.error(self.messages.history_invalid)
            previous_at = at
        updated_at = self.update_timestamp(payload.get("updated_at"))
        if updated_at != previous_at:
            raise self.error(self.messages.update_differs)
        return InstallerTwoPhaseRecord(
            phase=phase,
            revision=revision,
            digest=digest,
            value=value,
            updated_at=updated_at,
        )

    def write(
        self,
        *,
        phase: TwoPhase,
        value: ValueT,
        events: list[dict[str, object]],
    ) -> InstallerTwoPhaseRecord[ValueT]:
        digest = self.value_digest(value)
        payload = {
            self.value_field: self.encode(value),
            self.digest_field: digest,
            "contract": self.contract,
            "events": events,
            "phase": phase,
            "revision": len(events),
            "updated_at": events[-1]["at"],
        }
        try:
            atomic_write_private(self.path, canonical_json_bytes(payload))
        except IdentityPackLifecycleError as exc:
            raise self.error(self.messages.write_failed) from exc
        return InstallerTwoPhaseRecord(
            phase=phase,
            revision=len(events),
            digest=digest,
            value=value,
            updated_at=str(events[-1]["at"]),
        )

    def stage(self, value: ValueT, *, at: str) -> InstallerTwoPhaseChange[ValueT]:
        digest = self.value_digest(value)
        retained = self.load()
        if retained is not None:
            if not hmac.compare_digest(retained.digest, digest):
                raise self.error(self.messages.different_retained)
            return InstallerTwoPhaseChange(retained, already_in_phase=True)
        written = self.write(
            phase="staged",
            value=value,
            events=[self._event(at=at, digest=digest, revision=1, transition="staged")],
        )
        return InstallerTwoPhaseChange(written, already_in_phase=False)

    def confirm(
        self,
        expected_digest: str,
        *,
        confirmed_at: Callable[[], str],
    ) -> InstallerTwoPhaseChange[ValueT]:
        retained = self.load()
        if retained is None:
            raise self.error(self.messages.missing_staged)
        if not hmac.compare_digest(retained.digest, expected_digest):
            raise self.error(self.messages.expected_digest_differs)
        if retained.phase == "ready":
            return InstallerTwoPhaseChange(retained, already_in_phase=True)
        at = confirmed_at()
        if at < retained.updated_at:
            raise self.error(self.messages.confirmation_precedes)
        written = self.write(
            phase="ready",
            value=retained.value,
            events=[
                self._event(
                    at=retained.updated_at,
                    digest=retained.digest,
                    revision=1,
                    transition="staged",
                ),
                self._event(
                    at=at,
                    digest=retained.digest,
                    revision=2,
                    transition="confirmed",
                ),
            ],
        )
        return InstallerTwoPhaseChange(written, already_in_phase=False)

    def _event(
        self,
        *,
        at: str,
        digest: str,
        revision: int,
        transition: Literal["staged", "confirmed"],
    ) -> dict[str, object]:
        return {
            "at": at,
            self.digest_field: digest,
            "revision": revision,
            "transition": transition,
        }


__all__ = [
    "InstallerTwoPhaseChange",
    "InstallerTwoPhaseJournal",
    "InstallerTwoPhaseMessages",
    "InstallerTwoPhaseRecord",
    "owner_only_lock",
    "require_owner_only_directory",
    "require_utc_timestamp",
]
