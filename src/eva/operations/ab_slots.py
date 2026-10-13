"""Filesystem-backed A/B release state for the standalone Eva operator.

This module owns no business state and never copies or rolls back PostgreSQL.
It records which already-verified release may receive traffic against the one
current canonical database lineage.
"""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Callable, Literal


SlotName = Literal["a", "b"]
SlotStatus = Literal["empty", "staged", "active", "rollback_ready", "failed"]
TrafficState = Literal["closed", "draining", "open"]
_CONTRACT = "EvaABInstallationState/v1"
_STATE_KEYS = {
    "active_slot",
    "autonomy_enabled",
    "contract",
    "database_lineage_id",
    "database_schema_revision",
    "installation_id",
    "revision",
    "slots",
    "traffic_state",
    "updated_at",
}
_SLOT_KEYS = {
    "activation_receipt_sha256",
    "compatible_schema_revisions",
    "image_set_sha256",
    "release_manifest_sha256",
    "status",
    "verification_receipt_sha256",
}


class ABSlotError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class VerifiedReleaseSlot:
    release_manifest_sha256: str
    image_set_sha256: str
    verification_receipt_sha256: str
    compatible_schema_revisions: tuple[str, ...]

    def __post_init__(self) -> None:
        for name, value in (
            ("release manifest", self.release_manifest_sha256),
            ("image set", self.image_set_sha256),
            ("verification receipt", self.verification_receipt_sha256),
        ):
            _require_sha256(value, name)
        if not self.compatible_schema_revisions or any(
            not item for item in self.compatible_schema_revisions
        ):
            raise ValueError("release requires explicit compatible schema revisions")
        if len(set(self.compatible_schema_revisions)) != len(
            self.compatible_schema_revisions
        ):
            raise ValueError("compatible schema revisions must be unique")


@dataclass(frozen=True, slots=True)
class SlotRecord:
    status: SlotStatus
    release_manifest_sha256: str | None
    image_set_sha256: str | None
    verification_receipt_sha256: str | None
    activation_receipt_sha256: str | None
    compatible_schema_revisions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ABInstallationState:
    installation_id: str
    database_lineage_id: str
    database_schema_revision: str
    revision: int
    active_slot: SlotName | None
    traffic_state: TrafficState
    autonomy_enabled: bool
    slots: dict[SlotName, SlotRecord]
    updated_at: str


@dataclass(frozen=True, slots=True)
class InstallationDiagnosis:
    healthy: bool
    errors: tuple[str, ...]
    state: ABInstallationState | None


def _require_sha256(value: str, label: str) -> None:
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{label} must be a lowercase SHA-256")


def _empty_slot() -> SlotRecord:
    return SlotRecord("empty", None, None, None, None, ())


def _slot_payload(slot: SlotRecord) -> dict[str, object]:
    return {
        "activation_receipt_sha256": slot.activation_receipt_sha256,
        "compatible_schema_revisions": list(slot.compatible_schema_revisions),
        "image_set_sha256": slot.image_set_sha256,
        "release_manifest_sha256": slot.release_manifest_sha256,
        "status": slot.status,
        "verification_receipt_sha256": slot.verification_receipt_sha256,
    }


def state_payload(state: ABInstallationState) -> dict[str, object]:
    return {
        "active_slot": state.active_slot,
        "autonomy_enabled": state.autonomy_enabled,
        "contract": _CONTRACT,
        "database_lineage_id": state.database_lineage_id,
        "database_schema_revision": state.database_schema_revision,
        "installation_id": state.installation_id,
        "revision": state.revision,
        "slots": {name: _slot_payload(state.slots[name]) for name in ("a", "b")},
        "traffic_state": state.traffic_state,
        "updated_at": state.updated_at,
    }


def canonical_state_bytes(state: ABInstallationState) -> bytes:
    return json.dumps(
        state_payload(state),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def state_sha256(state: ABInstallationState) -> str:
    return hashlib.sha256(canonical_state_bytes(state)).hexdigest()


def _parse_slot(value: object) -> SlotRecord:
    if not isinstance(value, dict) or set(value) != _SLOT_KEYS:
        raise ABSlotError("state_invalid", "slot state has an unknown or missing field")
    compatible = value["compatible_schema_revisions"]
    if not isinstance(compatible, list) or not all(
        isinstance(item, str) and item for item in compatible
    ):
        raise ABSlotError("state_invalid", "slot schema compatibility is invalid")
    status_value = value["status"]
    if status_value not in {"empty", "staged", "active", "rollback_ready", "failed"}:
        raise ABSlotError("state_invalid", "slot status is invalid")
    slot = SlotRecord(
        status_value,
        value["release_manifest_sha256"],  # type: ignore[arg-type]
        value["image_set_sha256"],  # type: ignore[arg-type]
        value["verification_receipt_sha256"],  # type: ignore[arg-type]
        value["activation_receipt_sha256"],  # type: ignore[arg-type]
        tuple(compatible),
    )
    _validate_slot(slot)
    return slot


def _validate_slot(slot: SlotRecord) -> None:
    hashes = (
        slot.release_manifest_sha256,
        slot.image_set_sha256,
        slot.verification_receipt_sha256,
    )
    if slot.status == "empty":
        if any(
            value is not None for value in (*hashes, slot.activation_receipt_sha256)
        ):
            raise ABSlotError("state_invalid", "empty slot carries release authority")
        if slot.compatible_schema_revisions:
            raise ABSlotError("state_invalid", "empty slot carries schema authority")
        return
    if any(not isinstance(value, str) for value in hashes):
        raise ABSlotError("state_invalid", "non-empty slot lacks release authority")
    for label, value in zip(
        ("release manifest", "image set", "verification receipt"), hashes, strict=True
    ):
        try:
            _require_sha256(value, label)  # type: ignore[arg-type]
        except ValueError as exc:
            raise ABSlotError("state_invalid", str(exc)) from exc
    if not slot.compatible_schema_revisions:
        raise ABSlotError("state_invalid", "non-empty slot lacks schema compatibility")
    if slot.status in {"active", "rollback_ready"}:
        if not isinstance(slot.activation_receipt_sha256, str):
            raise ABSlotError("state_invalid", "activated slot lacks health receipt")
        try:
            _require_sha256(slot.activation_receipt_sha256, "activation receipt")
        except ValueError as exc:
            raise ABSlotError("state_invalid", str(exc)) from exc
    elif slot.activation_receipt_sha256 is not None:
        raise ABSlotError("state_invalid", "unactivated slot carries health authority")


def parse_state_bytes(data: bytes) -> ABInstallationState:
    if len(data) > 65_536:
        raise ABSlotError("state_invalid", "installation state is too large")
    try:
        decoded = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ABSlotError(
            "state_invalid", "installation state is not valid JSON"
        ) from exc
    if not isinstance(decoded, dict) or set(decoded) != _STATE_KEYS:
        raise ABSlotError("state_invalid", "installation state has unknown fields")
    if decoded["contract"] != _CONTRACT:
        raise ABSlotError("state_invalid", "installation state contract is unknown")
    slots = decoded["slots"]
    if not isinstance(slots, dict) or set(slots) != {"a", "b"}:
        raise ABSlotError(
            "state_invalid", "installation requires exactly A and B slots"
        )
    state = ABInstallationState(
        installation_id=decoded["installation_id"],
        database_lineage_id=decoded["database_lineage_id"],
        database_schema_revision=decoded["database_schema_revision"],
        revision=decoded["revision"],
        active_slot=decoded["active_slot"],
        traffic_state=decoded["traffic_state"],
        autonomy_enabled=decoded["autonomy_enabled"],
        slots={"a": _parse_slot(slots["a"]), "b": _parse_slot(slots["b"])},
        updated_at=decoded["updated_at"],
    )
    _validate_state(state)
    if canonical_state_bytes(state) != data:
        raise ABSlotError("state_invalid", "installation state is not canonical JSON")
    return state


def _validate_state(state: ABInstallationState) -> None:
    if not all(
        isinstance(value, str) and value
        for value in (
            state.installation_id,
            state.database_lineage_id,
            state.database_schema_revision,
            state.updated_at,
        )
    ):
        raise ABSlotError("state_invalid", "installation identity is incomplete")
    if (
        not isinstance(state.revision, int)
        or isinstance(state.revision, bool)
        or state.revision < 1
    ):
        raise ABSlotError("state_invalid", "installation revision is invalid")
    if state.active_slot not in {None, "a", "b"}:
        raise ABSlotError("state_invalid", "active slot is invalid")
    if state.traffic_state not in {"closed", "draining", "open"}:
        raise ABSlotError("state_invalid", "traffic state is invalid")
    if not isinstance(state.autonomy_enabled, bool):
        raise ABSlotError("state_invalid", "autonomy state is invalid")
    active = [name for name, slot in state.slots.items() if slot.status == "active"]
    if active != ([] if state.active_slot is None else [state.active_slot]):
        raise ABSlotError("state_invalid", "active slot pointer and slot state differ")
    if state.traffic_state == "open" and state.active_slot is None:
        raise ABSlotError("state_invalid", "traffic cannot open without an active slot")
    for slot in state.slots.values():
        _validate_slot(slot)


class ABSlotStore:
    def __init__(self, root: Path) -> None:
        if not root.is_absolute():
            raise ValueError("installation root must be absolute")
        absolute = Path(os.path.abspath(root))
        if absolute in {Path("/"), Path.home().resolve()}:
            raise ValueError("installation root cannot be root or a home directory")
        if absolute.exists() and absolute.is_symlink():
            raise ValueError("installation root cannot be a symbolic link")
        self.root = absolute
        self.state_dir = self.root / "state"
        self.state_path = self.state_dir / "ab-installation.json"
        self.lock_path = self.state_dir / "ab-installation.lock"

    def initialize(
        self,
        *,
        installation_id: str,
        database_lineage_id: str,
        database_schema_revision: str,
        initialized_at: str,
    ) -> ABInstallationState:
        if self.root.exists():
            info = self.root.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise ABSlotError(
                    "layout_invalid", "installation root is not a directory"
                )
            if not self.state_path.exists() and any(self.root.iterdir()):
                raise ABSlotError(
                    "layout_not_empty",
                    "new installation root contains unrelated files",
                )
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.state_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        with self._lock():
            if self.state_path.exists():
                existing = self._load_unlocked()
                expected = (
                    installation_id,
                    database_lineage_id,
                    database_schema_revision,
                )
                actual = (
                    existing.installation_id,
                    existing.database_lineage_id,
                    existing.database_schema_revision,
                )
                if actual != expected:
                    raise ABSlotError(
                        "installation_conflict",
                        "existing installation identity differs from init request",
                    )
                return existing
            state = ABInstallationState(
                installation_id,
                database_lineage_id,
                database_schema_revision,
                1,
                None,
                "closed",
                False,
                {"a": _empty_slot(), "b": _empty_slot()},
                initialized_at,
            )
            self._write_unlocked(state)
            return state

    def load(self) -> ABInstallationState:
        self._attest_layout()
        with self._lock():
            return self._load_unlocked()

    def stage_release(
        self,
        slot: SlotName,
        release: VerifiedReleaseSlot,
        *,
        expected_revision: int,
        updated_at: str,
    ) -> ABInstallationState:
        def change(state: ABInstallationState) -> ABInstallationState:
            if state.active_slot == slot:
                raise ABSlotError(
                    "active_slot_mutation", "cannot stage over active slot"
                )
            replacement = SlotRecord(
                "staged",
                release.release_manifest_sha256,
                release.image_set_sha256,
                release.verification_receipt_sha256,
                None,
                release.compatible_schema_revisions,
            )
            slots = dict(state.slots)
            slots[slot] = replacement
            return self._next(state, updated_at=updated_at, slots=slots)

        return self._transition(expected_revision, change)

    def activate(
        self,
        slot: SlotName,
        *,
        activation_receipt_sha256: str,
        expected_revision: int,
        updated_at: str,
    ) -> ABInstallationState:
        _require_sha256(activation_receipt_sha256, "activation receipt")

        def change(state: ABInstallationState) -> ABInstallationState:
            if state.traffic_state != "closed":
                raise ABSlotError(
                    "traffic_not_closed", "cutover requires closed traffic"
                )
            target = state.slots[slot]
            if target.status not in {"staged", "rollback_ready"}:
                raise ABSlotError("slot_not_ready", "target slot is not ready")
            if state.database_schema_revision not in target.compatible_schema_revisions:
                raise ABSlotError(
                    "schema_incompatible",
                    "target release cannot run on the current canonical database",
                )
            slots = dict(state.slots)
            if state.active_slot is not None:
                previous = slots[state.active_slot]
                slots[state.active_slot] = SlotRecord(
                    "rollback_ready",
                    previous.release_manifest_sha256,
                    previous.image_set_sha256,
                    previous.verification_receipt_sha256,
                    previous.activation_receipt_sha256,
                    previous.compatible_schema_revisions,
                )
            slots[slot] = SlotRecord(
                "active",
                target.release_manifest_sha256,
                target.image_set_sha256,
                target.verification_receipt_sha256,
                activation_receipt_sha256,
                target.compatible_schema_revisions,
            )
            return self._next(
                state,
                updated_at=updated_at,
                slots=slots,
                active_slot=slot,
                autonomy_enabled=False,
            )

        return self._transition(expected_revision, change)

    def set_traffic(
        self,
        traffic_state: TrafficState,
        *,
        expected_revision: int,
        updated_at: str,
    ) -> ABInstallationState:
        def change(state: ABInstallationState) -> ABInstallationState:
            if traffic_state == "open" and state.active_slot is None:
                raise ABSlotError("slot_not_active", "traffic requires active release")
            return self._next(state, updated_at=updated_at, traffic_state=traffic_state)

        return self._transition(expected_revision, change)

    def record_expand_migration(
        self,
        database_schema_revision: str,
        *,
        migration_receipt_sha256: str,
        expected_revision: int,
        updated_at: str,
    ) -> ABInstallationState:
        _require_sha256(migration_receipt_sha256, "migration receipt")
        if not database_schema_revision:
            raise ValueError("database schema revision is required")

        def change(state: ABInstallationState) -> ABInstallationState:
            if state.traffic_state != "closed":
                raise ABSlotError(
                    "traffic_not_closed", "migration requires closed traffic"
                )
            if not any(
                slot.status in {"staged", "active"}
                and database_schema_revision in slot.compatible_schema_revisions
                for slot in state.slots.values()
            ):
                raise ABSlotError(
                    "schema_unbound", "no verified release accepts the migrated schema"
                )
            return self._next(
                state,
                updated_at=updated_at,
                database_schema_revision=database_schema_revision,
            )

        return self._transition(expected_revision, change)

    def diagnose(self) -> InstallationDiagnosis:
        errors: list[str] = []
        state: ABInstallationState | None = None
        try:
            state = self.load()
        except (ABSlotError, OSError, ValueError) as exc:
            errors.append(str(exc))
        return InstallationDiagnosis(not errors, tuple(errors), state)

    def _next(
        self, state: ABInstallationState, *, updated_at: str, **changes: object
    ) -> ABInstallationState:
        values = {
            "installation_id": state.installation_id,
            "database_lineage_id": state.database_lineage_id,
            "database_schema_revision": state.database_schema_revision,
            "revision": state.revision + 1,
            "active_slot": state.active_slot,
            "traffic_state": state.traffic_state,
            "autonomy_enabled": state.autonomy_enabled,
            "slots": state.slots,
            "updated_at": updated_at,
            **changes,
        }
        result = ABInstallationState(**values)  # type: ignore[arg-type]
        _validate_state(result)
        return result

    def _transition(
        self,
        expected_revision: int,
        change: Callable[[ABInstallationState], ABInstallationState],
    ) -> ABInstallationState:
        self._attest_layout()
        with self._lock():
            state = self._load_unlocked()
            if state.revision != expected_revision:
                raise ABSlotError("revision_conflict", "installation state changed")
            updated = change(state)
            self._write_unlocked(updated)
            return updated

    def _attest_layout(self) -> None:
        for path, mode in ((self.root, 0o700), (self.state_dir, 0o700)):
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise ABSlotError("layout_invalid", f"{path} is not a real directory")
            if stat.S_IMODE(info.st_mode) != mode:
                raise ABSlotError("layout_invalid", f"{path} is not private")

    def _lock(self):  # type: ignore[no-untyped-def]
        descriptor = os.open(
            self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )

        class _Lock:
            def __enter__(inner_self) -> None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)

            def __exit__(inner_self, *_args: object) -> None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

        return _Lock()

    def _load_unlocked(self) -> ABInstallationState:
        info = self.state_path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ABSlotError(
                "state_invalid", "installation state is not a regular file"
            )
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise ABSlotError("state_invalid", "installation state is not private")
        return parse_state_bytes(self.state_path.read_bytes())

    def _write_unlocked(self, state: ABInstallationState) -> None:
        payload = canonical_state_bytes(state)
        descriptor, raw_path = tempfile.mkstemp(prefix=".ab-state-", dir=self.state_dir)
        temporary = Path(raw_path)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_path)
            directory = os.open(self.state_dir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary.exists():
                temporary.unlink()


__all__ = [
    "ABInstallationState",
    "ABSlotError",
    "ABSlotStore",
    "InstallationDiagnosis",
    "SlotRecord",
    "VerifiedReleaseSlot",
    "canonical_state_bytes",
    "parse_state_bytes",
    "state_payload",
    "state_sha256",
]
