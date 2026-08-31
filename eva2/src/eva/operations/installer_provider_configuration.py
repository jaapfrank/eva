"""Private, restart-safe non-secret configuration for installer providers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from pathlib import Path
from typing import Callable, Literal

from eva.application.installer import InstallerUseCaseError, InstallerUserInputError
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


ProviderConfigurationState = Literal["missing", "configured"]
PROVIDER_CONFIGURATION_SLOTS = ("home_assistant", "email")
_SLOT_SET = frozenset(PROVIDER_CONFIGURATION_SLOTS)
_MAX_VALUE_LENGTH = 1_024


class InstallerProviderConfigurationError(InstallerUseCaseError):
    """Raised when retained provider configuration cannot be trusted."""


class InstallerProviderConfigurationInputError(
    InstallerUserInputError, InstallerProviderConfigurationError
):
    """Raised when browser-supplied provider configuration is invalid."""


@dataclass(frozen=True, slots=True)
class InstallerProviderConfigurationStatus:
    slot_id: str
    state: ProviderConfigurationState
    configuration_sha256: str | None
    updated_at: str | None
    revision: int


@dataclass(frozen=True, slots=True)
class InstallerProviderConfiguration:
    slot_id: str
    value: str
    configuration_sha256: str
    updated_at: str
    revision: int


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp(value: object) -> str:
    return require_utc_timestamp(
        value,
        error=InstallerProviderConfigurationError,
        invalid_message="provider configuration timestamp is invalid",
        non_utc_message="provider configuration timestamp must be UTC",
    )


def _slot(value: object) -> str:
    if not isinstance(value, str) or value not in _SLOT_SET:
        raise InstallerProviderConfigurationInputError(
            "provider configuration slot is invalid"
        )
    return value


def _value(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_VALUE_LENGTH
        or value != value.strip()
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise InstallerProviderConfigurationInputError(
            "provider configuration value is invalid"
        )
    return value


def _digest(slot_id: str, value: str) -> str:
    return hashlib.sha256(
        canonical_json_bytes({"slot_id": slot_id, "value": value})
    ).hexdigest()


def _private_directory(path: Path) -> None:
    require_owner_only_directory(
        path,
        error=InstallerProviderConfigurationError,
        unavailable_message="provider configuration workspace is unavailable",
        invalid_message="provider configuration workspace must be owner-only",
    )


@dataclass(frozen=True, slots=True)
class InstallerProviderConfigurationStore:
    workspace: Path
    clock: Callable[[], str] = _utc_now

    def __post_init__(self) -> None:
        _private_directory(self.workspace)

    @property
    def state_path(self) -> Path:
        return self.workspace / "provider-configuration.json"

    def _lock(self):
        return owner_only_lock(
            self.workspace / ".provider-configuration.lock",
            error=InstallerProviderConfigurationError,
            unavailable_message="provider configuration lock is unavailable",
        )

    def _load(self) -> tuple[int, dict[str, InstallerProviderConfiguration]]:
        if not self.state_path.exists() and not self.state_path.is_symlink():
            return 0, {}
        try:
            payload = require_canonical_bytes(
                read_private_artifact(
                    self.state_path, label="installer provider configuration"
                )
            )
        except (IdentityPackLifecycleError, ValueError) as exc:
            raise InstallerProviderConfigurationError(
                "provider configuration state is unavailable"
            ) from exc
        revision = payload.get("revision")
        slots = payload.get("slots")
        if (
            set(payload) != {"contract", "revision", "slots"}
            or payload.get("contract") != "EvaInstallerProviderConfiguration/v1"
            or not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 1
            or not isinstance(slots, dict)
            or not set(slots) <= _SLOT_SET
            or revision < len(slots)
        ):
            raise InstallerProviderConfigurationError(
                "provider configuration state is invalid"
            )
        decoded: dict[str, InstallerProviderConfiguration] = {}
        for slot_id, row in slots.items():
            if not isinstance(row, dict) or set(row) != {
                "configuration_sha256",
                "revision",
                "updated_at",
                "value",
            }:
                raise InstallerProviderConfigurationError(
                    "provider configuration state is invalid"
                )
            try:
                checked_slot = _slot(slot_id)
                checked_value = _value(row.get("value"))
            except InstallerProviderConfigurationInputError as exc:
                raise InstallerProviderConfigurationError(
                    "provider configuration state is invalid"
                ) from exc
            row_revision = row.get("revision")
            digest = row.get("configuration_sha256")
            if (
                not isinstance(row_revision, int)
                or isinstance(row_revision, bool)
                or not 1 <= row_revision <= revision
                or not isinstance(digest, str)
                or digest != _digest(checked_slot, checked_value)
            ):
                raise InstallerProviderConfigurationError(
                    "provider configuration state is invalid"
                )
            decoded[checked_slot] = InstallerProviderConfiguration(
                slot_id=checked_slot,
                value=checked_value,
                configuration_sha256=digest,
                updated_at=_timestamp(row.get("updated_at")),
                revision=row_revision,
            )
        return revision, decoded

    def status(self) -> tuple[InstallerProviderConfigurationStatus, ...]:
        with self._lock():
            revision, configured = self._load()
            return tuple(
                InstallerProviderConfigurationStatus(
                    slot_id=slot_id,
                    state="configured" if slot_id in configured else "missing",
                    configuration_sha256=(
                        configured[slot_id].configuration_sha256
                        if slot_id in configured
                        else None
                    ),
                    updated_at=(
                        configured[slot_id].updated_at
                        if slot_id in configured
                        else None
                    ),
                    revision=revision,
                )
                for slot_id in PROVIDER_CONFIGURATION_SLOTS
            )

    def read(self, *, slot_id: object) -> InstallerProviderConfiguration:
        checked_slot = _slot(slot_id)
        with self._lock():
            _, configured = self._load()
            try:
                return configured[checked_slot]
            except KeyError as exc:
                raise InstallerProviderConfigurationError(
                    "provider configuration is missing"
                ) from exc

    def configure(
        self, *, slot_id: object, value: object
    ) -> InstallerProviderConfiguration:
        checked_slot = _slot(slot_id)
        checked_value = _value(value)
        with self._lock():
            revision, configured = self._load()
            current = configured.get(checked_slot)
            digest = _digest(checked_slot, checked_value)
            if current is not None and current.configuration_sha256 == digest:
                return current
            next_revision = revision + 1
            replacement = InstallerProviderConfiguration(
                slot_id=checked_slot,
                value=checked_value,
                configuration_sha256=digest,
                updated_at=_timestamp(self.clock()),
                revision=next_revision,
            )
            configured[checked_slot] = replacement
            payload = {
                "contract": "EvaInstallerProviderConfiguration/v1",
                "revision": next_revision,
                "slots": {
                    item_slot: {
                        "configuration_sha256": item.configuration_sha256,
                        "revision": item.revision,
                        "updated_at": item.updated_at,
                        "value": item.value,
                    }
                    for item_slot, item in configured.items()
                },
            }
            try:
                atomic_write_private(self.state_path, canonical_json_bytes(payload))
            except IdentityPackLifecycleError as exc:
                raise InstallerProviderConfigurationError(
                    "provider configuration write failed"
                ) from exc
            _, retained = self._load()
            return retained[checked_slot]


__all__ = [
    "InstallerProviderConfiguration",
    "InstallerProviderConfigurationError",
    "InstallerProviderConfigurationInputError",
    "InstallerProviderConfigurationStatus",
    "InstallerProviderConfigurationStore",
    "PROVIDER_CONFIGURATION_SLOTS",
]
