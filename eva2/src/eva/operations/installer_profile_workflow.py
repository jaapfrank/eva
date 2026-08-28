"""Private, restart-safe setup profile for Eva's local installer.

This workflow retains only non-secret human choices.  Staging and confirming a
profile never creates an installation, principal, connector, secret, database,
or runtime configuration.  A later installer step must consume the confirmed
profile under its own authority.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import fcntl
import hashlib
import hmac
import os
from pathlib import Path
import re
import stat
from typing import Callable, Literal
import unicodedata
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from eva.contracts.canonical_json import canonical_json_bytes, require_canonical_bytes
from eva.operations.identity_pack_lifecycle import IdentityPackLifecycleError
from eva.operations.installer_identity_storage import (
    atomic_write_private,
    read_private_artifact,
)


_LANGUAGE_RE = re.compile(r"[a-z]{2,3}(?:-[A-Z][a-z]{3})?(?:-(?:[A-Z]{2}|[0-9]{3}))?")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


class InstallerProfileWorkflowError(ValueError):
    """Raised when the private installer profile cannot be trusted."""


class InstallerProfileInputError(InstallerProfileWorkflowError):
    """Raised when a human-supplied profile field is invalid."""


ProfilePhase = Literal["empty", "staged", "ready"]


@dataclass(frozen=True, slots=True)
class InstallerProfile:
    installation_name: str
    owner_label: str
    language: str
    time_zone: str

    def as_dict(self) -> dict[str, object]:
        return {
            "contract": "EvaInstallerProfile/v1",
            "installation_name": self.installation_name,
            "language": self.language,
            "owner_label": self.owner_label,
            "time_zone": self.time_zone,
        }


@dataclass(frozen=True, slots=True)
class InstallerProfileStatus:
    phase: ProfilePhase
    revision: int
    profile_sha256: str | None
    profile: InstallerProfile | None
    updated_at: str | None


@dataclass(frozen=True, slots=True)
class InstallerProfileTransition:
    phase: Literal["staged", "ready"]
    revision: int
    profile_sha256: str
    already_in_phase: bool


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        raise InstallerProfileWorkflowError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InstallerProfileWorkflowError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise InstallerProfileWorkflowError(f"{label} must be UTC")
    return value


def _bounded_text(value: object, *, label: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or value != value.strip()
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise InstallerProfileInputError(f"{label} is invalid")
    return value


def _profile(
    *,
    installation_name: object,
    owner_label: object,
    language: object,
    time_zone: object,
) -> InstallerProfile:
    checked_language = _bounded_text(language, label="language", maximum=35)
    if _LANGUAGE_RE.fullmatch(checked_language) is None:
        raise InstallerProfileInputError("language is invalid")
    checked_zone = _bounded_text(time_zone, label="time zone", maximum=80)
    try:
        zone = ZoneInfo(checked_zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InstallerProfileInputError("time zone is invalid") from exc
    if zone.key != checked_zone:
        raise InstallerProfileInputError("time zone is not canonical")
    return InstallerProfile(
        installation_name=_bounded_text(
            installation_name, label="installation name", maximum=80
        ),
        owner_label=_bounded_text(owner_label, label="owner label", maximum=80),
        language=checked_language,
        time_zone=checked_zone,
    )


def _profile_sha256(profile: InstallerProfile) -> str:
    return hashlib.sha256(canonical_json_bytes(profile.as_dict())).hexdigest()


def _require_sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise InstallerProfileWorkflowError("profile hash is invalid")
    return value


@dataclass(frozen=True, slots=True)
class InstallerProfileWorkflow:
    workspace: Path
    clock: Callable[[], str] = _utc_now

    def __post_init__(self) -> None:
        try:
            metadata = self.workspace.lstat()
        except OSError as exc:
            raise InstallerProfileWorkflowError(
                "installer profile workspace is unavailable"
            ) from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o077
        ):
            raise InstallerProfileWorkflowError(
                "installer profile workspace must be an owner-only directory"
            )

    @property
    def state_path(self) -> Path:
        return self.workspace / "profile-workflow.json"

    @contextmanager
    def _lock(self):
        path = self.workspace / ".profile-workflow.lock"
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
                raise OSError("profile lock is not a private regular file")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as exc:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            raise InstallerProfileWorkflowError(
                "installer profile lock is unavailable"
            ) from exc
        assert descriptor is not None
        try:
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _load(self) -> InstallerProfileStatus | None:
        if not self.state_path.exists() and not self.state_path.is_symlink():
            return None
        try:
            raw = read_private_artifact(
                self.state_path, label="installer profile workflow"
            )
        except IdentityPackLifecycleError as exc:
            raise InstallerProfileWorkflowError(
                "installer profile workflow is unavailable"
            ) from exc
        try:
            payload = require_canonical_bytes(raw)
        except ValueError as exc:
            raise InstallerProfileWorkflowError(
                "installer profile workflow is invalid"
            ) from exc
        if (
            set(payload)
            != {
                "contract",
                "events",
                "phase",
                "profile",
                "profile_sha256",
                "revision",
                "updated_at",
            }
            or payload.get("contract") != "EvaInstallerProfileWorkflow/v1"
        ):
            raise InstallerProfileWorkflowError("installer profile workflow is invalid")
        phase = payload.get("phase")
        revision = payload.get("revision")
        events = payload.get("events")
        if phase not in {"staged", "ready"} or revision not in {1, 2}:
            raise InstallerProfileWorkflowError(
                "installer profile workflow phase is invalid"
            )
        if not isinstance(events, list) or len(events) != revision:
            raise InstallerProfileWorkflowError(
                "installer profile workflow history is invalid"
            )
        profile_value = payload.get("profile")
        if (
            not isinstance(profile_value, dict)
            or set(profile_value)
            != {
                "contract",
                "installation_name",
                "language",
                "owner_label",
                "time_zone",
            }
            or profile_value.get("contract") != "EvaInstallerProfile/v1"
        ):
            raise InstallerProfileWorkflowError("installer profile is invalid")
        profile = _profile(
            installation_name=profile_value.get("installation_name"),
            owner_label=profile_value.get("owner_label"),
            language=profile_value.get("language"),
            time_zone=profile_value.get("time_zone"),
        )
        profile_hash = _require_sha256(payload.get("profile_sha256"))
        if not hmac.compare_digest(profile_hash, _profile_sha256(profile)):
            raise InstallerProfileWorkflowError("installer profile hash differs")
        expected_transitions = (
            ["staged"] if phase == "staged" else ["staged", "confirmed"]
        )
        previous_at = ""
        for index, event in enumerate(events, start=1):
            if not isinstance(event, dict) or set(event) != {
                "at",
                "profile_sha256",
                "revision",
                "transition",
            }:
                raise InstallerProfileWorkflowError(
                    "installer profile workflow history is invalid"
                )
            at = _timestamp(event.get("at"), label="profile event timestamp")
            if (
                event.get("revision") != index
                or event.get("transition") != expected_transitions[index - 1]
                or not isinstance(event.get("profile_sha256"), str)
                or not hmac.compare_digest(event["profile_sha256"], profile_hash)
                or (previous_at and at < previous_at)
            ):
                raise InstallerProfileWorkflowError(
                    "installer profile workflow history is invalid"
                )
            previous_at = at
        updated_at = _timestamp(payload.get("updated_at"), label="profile update time")
        if updated_at != previous_at:
            raise InstallerProfileWorkflowError(
                "installer profile update time differs from history"
            )
        return InstallerProfileStatus(
            phase=phase,
            revision=revision,
            profile_sha256=profile_hash,
            profile=profile,
            updated_at=updated_at,
        )

    def _write(
        self,
        *,
        phase: Literal["staged", "ready"],
        profile: InstallerProfile,
        events: list[dict[str, object]],
    ) -> InstallerProfileStatus:
        profile_hash = _profile_sha256(profile)
        payload = {
            "contract": "EvaInstallerProfileWorkflow/v1",
            "events": events,
            "phase": phase,
            "profile": profile.as_dict(),
            "profile_sha256": profile_hash,
            "revision": len(events),
            "updated_at": events[-1]["at"],
        }
        try:
            atomic_write_private(self.state_path, canonical_json_bytes(payload))
        except IdentityPackLifecycleError as exc:
            raise InstallerProfileWorkflowError(
                "installer profile workflow write failed"
            ) from exc
        return InstallerProfileStatus(
            phase=phase,
            revision=len(events),
            profile_sha256=profile_hash,
            profile=profile,
            updated_at=str(events[-1]["at"]),
        )

    def status(self) -> InstallerProfileStatus:
        with self._lock():
            retained = self._load()
            if retained is not None:
                return retained
            return InstallerProfileStatus(
                phase="empty",
                revision=0,
                profile_sha256=None,
                profile=None,
                updated_at=None,
            )

    def stage(
        self,
        *,
        installation_name: object,
        owner_label: object,
        language: object,
        time_zone: object,
    ) -> InstallerProfileTransition:
        profile = _profile(
            installation_name=installation_name,
            owner_label=owner_label,
            language=language,
            time_zone=time_zone,
        )
        profile_hash = _profile_sha256(profile)
        with self._lock():
            retained = self._load()
            if retained is not None:
                if retained.profile_sha256 is None or not hmac.compare_digest(
                    retained.profile_sha256, profile_hash
                ):
                    raise InstallerProfileWorkflowError(
                        "a different installer profile is already retained"
                    )
                return InstallerProfileTransition(
                    phase=retained.phase,
                    revision=retained.revision,
                    profile_sha256=profile_hash,
                    already_in_phase=True,
                )
            at = _timestamp(self.clock(), label="profile stage time")
            written = self._write(
                phase="staged",
                profile=profile,
                events=[
                    {
                        "at": at,
                        "profile_sha256": profile_hash,
                        "revision": 1,
                        "transition": "staged",
                    }
                ],
            )
            return InstallerProfileTransition(
                phase="staged",
                revision=written.revision,
                profile_sha256=profile_hash,
                already_in_phase=False,
            )

    def confirm(self, *, expected_profile_sha256: object) -> InstallerProfileTransition:
        expected_hash = _require_sha256(expected_profile_sha256)
        with self._lock():
            retained = self._load()
            if (
                retained is None
                or retained.profile is None
                or retained.profile_sha256 is None
            ):
                raise InstallerProfileWorkflowError("no installer profile is staged")
            if not hmac.compare_digest(retained.profile_sha256, expected_hash):
                raise InstallerProfileWorkflowError("installer profile hash differs")
            if retained.phase == "ready":
                return InstallerProfileTransition(
                    phase="ready",
                    revision=retained.revision,
                    profile_sha256=retained.profile_sha256,
                    already_in_phase=True,
                )
            at = _timestamp(self.clock(), label="profile confirmation time")
            first_at = retained.updated_at
            assert first_at is not None
            if at < first_at:
                raise InstallerProfileWorkflowError(
                    "profile confirmation time precedes staging"
                )
            written = self._write(
                phase="ready",
                profile=retained.profile,
                events=[
                    {
                        "at": first_at,
                        "profile_sha256": retained.profile_sha256,
                        "revision": 1,
                        "transition": "staged",
                    },
                    {
                        "at": at,
                        "profile_sha256": retained.profile_sha256,
                        "revision": 2,
                        "transition": "confirmed",
                    },
                ],
            )
            return InstallerProfileTransition(
                phase="ready",
                revision=written.revision,
                profile_sha256=retained.profile_sha256,
                already_in_phase=False,
            )


__all__ = [
    "InstallerProfile",
    "InstallerProfileStatus",
    "InstallerProfileTransition",
    "InstallerProfileWorkflow",
    "InstallerProfileWorkflowError",
    "InstallerProfileInputError",
]
