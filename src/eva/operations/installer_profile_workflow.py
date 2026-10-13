"""Private, restart-safe setup profile for Eva's local installer.

This workflow retains only non-secret human choices.  Staging and confirming a
profile never creates an installation, principal, connector, secret, database,
or runtime configuration.  A later installer step must consume the confirmed
profile under its own authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from pathlib import Path
import re
from typing import Callable, Literal
import unicodedata
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from eva.application.installer import InstallerUseCaseError, InstallerUserInputError
from eva.contracts.canonical_json import canonical_json_bytes
from eva.operations.installer_safety import (
    InstallerTwoPhaseJournal,
    InstallerTwoPhaseMessages,
    owner_only_lock,
    require_owner_only_directory,
    require_utc_timestamp,
)


_LANGUAGE_RE = re.compile(r"[a-z]{2,3}(?:-[A-Z][a-z]{3})?(?:-(?:[A-Z]{2}|[0-9]{3}))?")


class InstallerProfileWorkflowError(InstallerUseCaseError):
    """Raised when the private installer profile cannot be trusted."""


class InstallerProfileInputError(InstallerUserInputError, InstallerProfileWorkflowError):
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
    return require_utc_timestamp(
        value,
        error=InstallerProfileWorkflowError,
        invalid_message=f"{label} is invalid",
        non_utc_message=f"{label} must be UTC",
    )


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


def _profile_value(value: object) -> InstallerProfile:
    if (
        not isinstance(value, dict)
        or set(value)
        != {"contract", "installation_name", "language", "owner_label", "time_zone"}
        or value.get("contract") != "EvaInstallerProfile/v1"
    ):
        raise InstallerProfileWorkflowError("installer profile is invalid")
    return _profile(
        installation_name=value.get("installation_name"),
        owner_label=value.get("owner_label"),
        language=value.get("language"),
        time_zone=value.get("time_zone"),
    )


_PROFILE_MESSAGES = InstallerTwoPhaseMessages(
    invalid="installer profile workflow is invalid",
    digest_invalid="profile hash is invalid",
    retained_digest_differs="installer profile hash differs",
    expected_digest_differs="installer profile hash differs",
    state_differs="installer profile workflow phase is invalid",
    history_invalid="installer profile workflow history is invalid",
    update_differs="installer profile update time differs from history",
    write_failed="installer profile workflow write failed",
    different_retained="a different installer profile is already retained",
    missing_staged="no installer profile is staged",
    confirmation_precedes="profile confirmation time precedes staging",
)


@dataclass(frozen=True, slots=True)
class InstallerProfileWorkflow:
    workspace: Path
    clock: Callable[[], str] = _utc_now

    def __post_init__(self) -> None:
        require_owner_only_directory(
            self.workspace,
            error=InstallerProfileWorkflowError,
            unavailable_message="installer profile workspace is unavailable",
            invalid_message="installer profile workspace must be an owner-only directory",
        )

    @property
    def state_path(self) -> Path:
        return self.workspace / "profile-workflow.json"

    def _lock(self):
        return owner_only_lock(
            self.workspace / ".profile-workflow.lock",
            error=InstallerProfileWorkflowError,
            unavailable_message="installer profile lock is unavailable",
        )

    def _journal(self) -> InstallerTwoPhaseJournal[InstallerProfile]:
        return InstallerTwoPhaseJournal(
            path=self.state_path,
            contract="EvaInstallerProfileWorkflow/v1",
            value_field="profile",
            digest_field="profile_sha256",
            read_label="installer profile workflow",
            decode=_profile_value,
            encode=InstallerProfile.as_dict,
            value_digest=_profile_sha256,
            event_timestamp=lambda value: _timestamp(
                value, label="profile event timestamp"
            ),
            update_timestamp=lambda value: _timestamp(
                value, label="profile update time"
            ),
            error=InstallerProfileWorkflowError,
            messages=_PROFILE_MESSAGES,
        )

    def status(self) -> InstallerProfileStatus:
        with self._lock():
            retained = self._journal().load()
            if retained is not None:
                return InstallerProfileStatus(
                    phase=retained.phase,
                    revision=retained.revision,
                    profile_sha256=retained.digest,
                    profile=retained.value,
                    updated_at=retained.updated_at,
                )
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
        with self._lock():
            change = self._journal().stage(
                profile,
                at=_timestamp(self.clock(), label="profile stage time"),
            )
        return InstallerProfileTransition(
            phase=change.record.phase,
            revision=change.record.revision,
            profile_sha256=change.record.digest,
            already_in_phase=change.already_in_phase,
        )

    def confirm(self, *, expected_profile_sha256: object) -> InstallerProfileTransition:
        journal = self._journal()
        expected_hash = journal.require_digest(expected_profile_sha256)
        with self._lock():
            change = journal.confirm(
                expected_hash,
                confirmed_at=lambda: _timestamp(
                    self.clock(), label="profile confirmation time"
                ),
            )
        return InstallerProfileTransition(
            phase="ready",
            revision=change.record.revision,
            profile_sha256=change.record.digest,
            already_in_phase=change.already_in_phase,
        )


__all__ = [
    "InstallerProfile",
    "InstallerProfileStatus",
    "InstallerProfileTransition",
    "InstallerProfileWorkflow",
    "InstallerProfileWorkflowError",
    "InstallerProfileInputError",
]
