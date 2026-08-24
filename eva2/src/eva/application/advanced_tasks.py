"""Deterministic planning for risky, multi-step household changes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
from typing import Literal, Protocol, cast

from eva.contracts.base import JsonValue, validate_json_value
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256


Risk = Literal["read_only", "reversible", "high"]

MAINTAINED_LOCALTUYA_REPOSITORY = "xZetsubou/hass-localtuya"
MINIMUM_MAINTAINED_LOCALTUYA_VERSION = (2026, 7, 0)


class AdvancedTaskError(RuntimeError):
    """The requested advanced-task transition is not safe or authoritative."""


class AdvancedTaskBlocked(AdvancedTaskError):
    """A mutation was requested before its sealed preflight was complete."""


@dataclass(frozen=True, slots=True)
class LocalTuyaEntityConfiguration:
    """One exact entity form submission selected during an approved canary."""

    platform: str
    values: dict[str, JsonValue]

    def __post_init__(self) -> None:
        if not self.platform or len(self.platform) > 40 or not self.platform.isascii():
            raise ValueError("LocalTuya entity platform is invalid")
        if not self.values or len(self.values) > 64:
            raise ValueError("LocalTuya entity configuration is invalid")
        validate_json_value(self.values)


@dataclass(frozen=True, slots=True)
class LocalTuyaCanaryConfiguration:
    """One-shot secret-bearing input; only its canonical hash may be persisted."""

    friendly_name: str
    host: str
    device_id: str
    local_key: str = field(repr=False)
    protocol_version: str = "3.3"
    entities: tuple[LocalTuyaEntityConfiguration, ...] = ()

    def __post_init__(self) -> None:
        if not self.friendly_name.strip() or len(self.friendly_name) > 80:
            raise ValueError("LocalTuya friendly name is invalid")
        try:
            import ipaddress

            address = ipaddress.ip_address(self.host)
        except ValueError as exc:
            raise ValueError("LocalTuya host must be a private IPv4 address") from exc
        if (
            address.version != 4
            or not address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
        ):
            raise ValueError("LocalTuya host must be a private IPv4 address")
        if not 8 <= len(self.device_id) <= 64 or any(
            value
            not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
            for value in self.device_id
        ):
            raise ValueError("LocalTuya device ID is invalid")
        if (
            not 16 <= len(self.local_key) <= 64
            or not self.local_key.isascii()
            or not self.local_key.isprintable()
        ):
            raise ValueError("LocalTuya local key is invalid")
        if self.protocol_version not in {"3.1", "3.2", "3.3", "3.4"}:
            raise ValueError("LocalTuya protocol version is invalid")
        if not 1 <= len(self.entities) <= 16:
            raise ValueError("LocalTuya canary must configure 1..16 entities")

    @property
    def sha256(self) -> str:
        return canonical_sha256(
            {
                "device_id": self.device_id,
                "entities": [
                    {"platform": item.platform, "values": item.values}
                    for item in self.entities
                ],
                "friendly_name": self.friendly_name.strip(),
                "host": self.host,
                "local_key": self.local_key,
                "protocol_version": self.protocol_version,
            }
        )


@dataclass(frozen=True, slots=True)
class HomeAssistantIntegration:
    domain: str
    entry_id: str
    state: str
    title: str


@dataclass(frozen=True, slots=True)
class HomeAssistantDevice:
    device_id: str
    name: str
    manufacturer: str | None
    model: str | None
    config_entry_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HomeAssistantEntity:
    entity_id: str
    platform: str
    config_entry_id: str | None
    device_id: str | None
    disabled_by: str | None


@dataclass(frozen=True, slots=True)
class HomeAssistantRepository:
    repository_id: str
    full_name: str
    domain: str
    installed: bool
    installed_version: str | None
    available_version: str | None
    can_download: bool
    minimum_home_assistant_version: str | None = None
    custom: bool = False

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "repository_id": self.repository_id,
            "full_name": self.full_name,
            "domain": self.domain,
            "installed": self.installed,
            "installed_version": self.installed_version,
            "available_version": self.available_version,
            "can_download": self.can_download,
        }
        # Keep legacy durable plans byte-compatible when HACS did not expose
        # these newer provenance fields yet.
        if self.minimum_home_assistant_version is not None:
            payload["minimum_home_assistant_version"] = (
                self.minimum_home_assistant_version
            )
        if self.custom:
            payload["custom"] = True
        return payload

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())


@dataclass(frozen=True, slots=True)
class HomeAssistantInventory:
    integrations: tuple[HomeAssistantIntegration, ...]
    devices: tuple[HomeAssistantDevice, ...]
    entities: tuple[HomeAssistantEntity, ...]
    captured_at: datetime
    repositories: tuple[HomeAssistantRepository, ...] = ()
    repository_inventory_verified: bool = False
    home_assistant_version: str | None = None

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    def to_payload(self) -> dict[str, object]:
        return {
            "captured_at": self.captured_at.isoformat().replace("+00:00", "Z"),
            "devices": [
                {
                    **asdict(value),
                    "config_entry_ids": list(value.config_entry_ids),
                }
                for value in self.devices
            ],
            "entities": [asdict(value) for value in self.entities],
            "integrations": [asdict(value) for value in self.integrations],
            "home_assistant_version": self.home_assistant_version,
            "repositories": [value.to_payload() for value in self.repositories],
            "repository_inventory_verified": self.repository_inventory_verified,
        }

    @classmethod
    def from_payload(cls, raw: object) -> HomeAssistantInventory:
        if not isinstance(raw, dict):
            raise AdvancedTaskError("Home Assistant inventory is malformed")
        try:
            integrations_raw = raw["integrations"]
            devices_raw = raw["devices"]
            entities_raw = raw["entities"]
            repositories_raw = raw["repositories"]
            if not all(
                isinstance(value, list)
                for value in (
                    integrations_raw,
                    devices_raw,
                    entities_raw,
                    repositories_raw,
                )
            ):
                raise TypeError
            inventory = cls(
                integrations=tuple(
                    HomeAssistantIntegration(
                        domain=str(item["domain"]),
                        entry_id=str(item["entry_id"]),
                        state=str(item["state"]),
                        title=str(item["title"]),
                    )
                    for item in integrations_raw
                    if isinstance(item, dict)
                ),
                devices=tuple(
                    HomeAssistantDevice(
                        device_id=str(item["device_id"]),
                        name=str(item["name"]),
                        manufacturer=(
                            None
                            if item["manufacturer"] is None
                            else str(item["manufacturer"])
                        ),
                        model=None if item["model"] is None else str(item["model"]),
                        config_entry_ids=tuple(
                            str(value) for value in item["config_entry_ids"]
                        ),
                    )
                    for item in devices_raw
                    if isinstance(item, dict)
                ),
                entities=tuple(
                    HomeAssistantEntity(
                        entity_id=str(item["entity_id"]),
                        platform=str(item["platform"]),
                        config_entry_id=(
                            None
                            if item["config_entry_id"] is None
                            else str(item["config_entry_id"])
                        ),
                        device_id=(
                            None
                            if item["device_id"] is None
                            else str(item["device_id"])
                        ),
                        disabled_by=(
                            None
                            if item["disabled_by"] is None
                            else str(item["disabled_by"])
                        ),
                    )
                    for item in entities_raw
                    if isinstance(item, dict)
                ),
                captured_at=datetime.fromisoformat(
                    str(raw["captured_at"]).replace("Z", "+00:00")
                ),
                repositories=tuple(
                    HomeAssistantRepository(
                        repository_id=str(item["repository_id"]),
                        full_name=str(item["full_name"]),
                        domain=str(item["domain"]),
                        installed=bool(item["installed"]),
                        installed_version=(
                            None
                            if item["installed_version"] is None
                            else str(item["installed_version"])
                        ),
                        available_version=(
                            None
                            if item["available_version"] is None
                            else str(item["available_version"])
                        ),
                        can_download=bool(item["can_download"]),
                        minimum_home_assistant_version=(
                            None
                            if item.get("minimum_home_assistant_version") is None
                            else str(item["minimum_home_assistant_version"])
                        ),
                        custom=item.get("custom") is True,
                    )
                    for item in repositories_raw
                    if isinstance(item, dict)
                ),
                repository_inventory_verified=bool(
                    raw["repository_inventory_verified"]
                ),
                home_assistant_version=(
                    None
                    if raw["home_assistant_version"] is None
                    else str(raw["home_assistant_version"])
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AdvancedTaskError("Home Assistant inventory is malformed") from exc
        if (
            len(inventory.integrations) != len(integrations_raw)
            or len(inventory.devices) != len(devices_raw)
            or len(inventory.entities) != len(entities_raw)
            or len(inventory.repositories) != len(repositories_raw)
            or inventory.captured_at.tzinfo is None
            or inventory.captured_at.utcoffset() != timedelta(0)
            or inventory.to_payload() != raw
        ):
            raise AdvancedTaskError("Home Assistant inventory shape differs")
        return inventory


@dataclass(frozen=True, slots=True)
class AdvancedTaskDevice:
    device_id: str
    name: str
    route: Literal["localtuya_candidate", "matter_review", "rebuild_scene"]
    entity_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AdvancedTaskStep:
    step_id: str
    title: str
    risk: Risk
    requires_approval: bool
    proof: str
    rollback: str | None


@dataclass(frozen=True, slots=True)
class AdvancedTaskPlan:
    plan_id: str
    operation_id: str
    task: str
    inventory_sha256: str
    state: Literal["blocked_preflight", "awaiting_approval"]
    blockers: tuple[str, ...]
    devices: tuple[AdvancedTaskDevice, ...]
    steps: tuple[AdvancedTaskStep, ...]
    requested_by: str
    created_at: datetime
    repositories: tuple[HomeAssistantRepository, ...] = ()

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload(include_sha=False))

    def to_payload(self, *, include_sha: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "blockers": list(self.blockers),
            "contract": "EvaAdvancedTaskPlan/v1",
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
            "devices": [
                {**asdict(value), "entity_ids": list(value.entity_ids)}
                for value in self.devices
            ],
            "inventory_sha256": self.inventory_sha256,
            "operation_id": self.operation_id,
            "plan_id": self.plan_id,
            "requested_by": self.requested_by,
            "repositories": [value.to_payload() for value in self.repositories],
            "state": self.state,
            "steps": [asdict(value) for value in self.steps],
            "task": self.task,
        }
        if include_sha:
            payload["plan_sha256"] = self.sha256
        return payload

    @classmethod
    def from_payload(cls, raw: object) -> AdvancedTaskPlan:
        if not isinstance(raw, dict) or raw.get("contract") != "EvaAdvancedTaskPlan/v1":
            raise AdvancedTaskError("advanced task plan is malformed")
        try:
            devices_raw = raw["devices"]
            steps_raw = raw["steps"]
            blockers_raw = raw["blockers"]
            repositories_raw = raw["repositories"]
            if not isinstance(devices_raw, list) or not isinstance(steps_raw, list):
                raise TypeError
            if not isinstance(blockers_raw, list) or not isinstance(
                repositories_raw, list
            ):
                raise TypeError
            devices = tuple(
                AdvancedTaskDevice(
                    device_id=str(item["device_id"]),
                    name=str(item["name"]),
                    route=cast(
                        Literal[
                            "localtuya_candidate", "matter_review", "rebuild_scene"
                        ],
                        item["route"],
                    ),
                    entity_ids=tuple(str(value) for value in item["entity_ids"]),
                )
                for item in devices_raw
                if isinstance(item, dict)
            )
            steps = tuple(
                AdvancedTaskStep(
                    step_id=str(item["step_id"]),
                    title=str(item["title"]),
                    risk=cast(Risk, item["risk"]),
                    requires_approval=bool(item["requires_approval"]),
                    proof=str(item["proof"]),
                    rollback=(
                        None if item["rollback"] is None else str(item["rollback"])
                    ),
                )
                for item in steps_raw
                if isinstance(item, dict)
            )
            repositories = tuple(
                HomeAssistantRepository(
                    repository_id=str(item["repository_id"]),
                    full_name=str(item["full_name"]),
                    domain=str(item["domain"]),
                    installed=bool(item["installed"]),
                    installed_version=(
                        None
                        if item["installed_version"] is None
                        else str(item["installed_version"])
                    ),
                    available_version=(
                        None
                        if item["available_version"] is None
                        else str(item["available_version"])
                    ),
                    can_download=bool(item["can_download"]),
                    minimum_home_assistant_version=(
                        None
                        if item.get("minimum_home_assistant_version") is None
                        else str(item["minimum_home_assistant_version"])
                    ),
                    custom=item.get("custom") is True,
                )
                for item in repositories_raw
                if isinstance(item, dict)
            )
            created_at = datetime.fromisoformat(
                str(raw["created_at"]).replace("Z", "+00:00")
            )
            plan = cls(
                plan_id=str(raw["plan_id"]),
                operation_id=str(raw["operation_id"]),
                task=str(raw["task"]),
                inventory_sha256=str(raw["inventory_sha256"]),
                state=cast(
                    Literal["blocked_preflight", "awaiting_approval"], raw["state"]
                ),
                blockers=tuple(str(value) for value in blockers_raw),
                devices=devices,
                steps=steps,
                requested_by=str(raw["requested_by"]),
                created_at=created_at,
                repositories=repositories,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AdvancedTaskError("advanced task plan is malformed") from exc
        if (
            len(devices) != len(devices_raw)
            or len(steps) != len(steps_raw)
            or len(repositories) != len(repositories_raw)
            or created_at.tzinfo is None
            or created_at.utcoffset() != timedelta(0)
            or raw.get("plan_sha256") != plan.sha256
            or plan.to_payload() != raw
        ):
            raise AdvancedTaskError("advanced task plan hash or shape differs")
        return plan


@dataclass(frozen=True, slots=True)
class AdvancedTaskApproval:
    approval_id: str
    plan_id: str
    plan_sha256: str
    inventory_sha256: str
    step_id: Literal["migrate_canary"]
    device_id: str
    approved_by: str
    approved_at: datetime
    expires_at: datetime

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload(include_sha=False))

    def to_payload(self, *, include_sha: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "approval_id": self.approval_id,
            "approved_at": self.approved_at.isoformat().replace("+00:00", "Z"),
            "approved_by": self.approved_by,
            "contract": "EvaAdvancedTaskApproval/v1",
            "device_id": self.device_id,
            "expires_at": self.expires_at.isoformat().replace("+00:00", "Z"),
            "inventory_sha256": self.inventory_sha256,
            "plan_id": self.plan_id,
            "plan_sha256": self.plan_sha256,
            "step_id": self.step_id,
        }
        if include_sha:
            payload["approval_sha256"] = self.sha256
        return payload

    @classmethod
    def from_payload(cls, raw: object) -> AdvancedTaskApproval:
        if (
            not isinstance(raw, dict)
            or raw.get("contract") != "EvaAdvancedTaskApproval/v1"
        ):
            raise AdvancedTaskError("advanced task approval is malformed")
        try:
            approval = cls(
                approval_id=str(raw["approval_id"]),
                plan_id=str(raw["plan_id"]),
                plan_sha256=str(raw["plan_sha256"]),
                inventory_sha256=str(raw["inventory_sha256"]),
                step_id=cast(Literal["migrate_canary"], raw["step_id"]),
                device_id=str(raw["device_id"]),
                approved_by=str(raw["approved_by"]),
                approved_at=datetime.fromisoformat(
                    str(raw["approved_at"]).replace("Z", "+00:00")
                ),
                expires_at=datetime.fromisoformat(
                    str(raw["expires_at"]).replace("Z", "+00:00")
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AdvancedTaskError("advanced task approval is malformed") from exc
        if (
            raw.get("approval_sha256") != approval.sha256
            or approval.to_payload() != raw
        ):
            raise AdvancedTaskError("advanced task approval hash or shape differs")
        return approval


@dataclass(frozen=True, slots=True)
class AdvancedTaskPackageApproval:
    approval_id: str
    plan_id: str
    plan_sha256: str
    inventory_sha256: str
    repository_id: str
    repository_sha256: str
    version: str
    approved_by: str
    approved_at: datetime
    expires_at: datetime

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload(include_sha=False))

    def to_payload(self, *, include_sha: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "approval_id": self.approval_id,
            "approved_at": self.approved_at.isoformat().replace("+00:00", "Z"),
            "approved_by": self.approved_by,
            "contract": "EvaAdvancedTaskPackageApproval/v1",
            "expires_at": self.expires_at.isoformat().replace("+00:00", "Z"),
            "inventory_sha256": self.inventory_sha256,
            "plan_id": self.plan_id,
            "plan_sha256": self.plan_sha256,
            "repository_id": self.repository_id,
            "repository_sha256": self.repository_sha256,
            "step_id": "install_localtuya_package",
            "version": self.version,
        }
        if include_sha:
            payload["approval_sha256"] = self.sha256
        return payload

    @classmethod
    def from_payload(cls, raw: object) -> AdvancedTaskPackageApproval:
        if (
            not isinstance(raw, dict)
            or raw.get("contract") != "EvaAdvancedTaskPackageApproval/v1"
            or raw.get("step_id") != "install_localtuya_package"
        ):
            raise AdvancedTaskError("advanced package approval is malformed")
        try:
            approval = cls(
                approval_id=str(raw["approval_id"]),
                plan_id=str(raw["plan_id"]),
                plan_sha256=str(raw["plan_sha256"]),
                inventory_sha256=str(raw["inventory_sha256"]),
                repository_id=str(raw["repository_id"]),
                repository_sha256=str(raw["repository_sha256"]),
                version=str(raw["version"]),
                approved_by=str(raw["approved_by"]),
                approved_at=datetime.fromisoformat(
                    str(raw["approved_at"]).replace("Z", "+00:00")
                ),
                expires_at=datetime.fromisoformat(
                    str(raw["expires_at"]).replace("Z", "+00:00")
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AdvancedTaskError("advanced package approval is malformed") from exc
        if (
            raw.get("approval_sha256") != approval.sha256
            or approval.to_payload() != raw
        ):
            raise AdvancedTaskError("advanced package approval hash or shape differs")
        return approval


@dataclass(frozen=True, slots=True)
class AdvancedTaskRepositoryApproval:
    approval_id: str
    plan_id: str
    plan_sha256: str
    inventory_sha256: str
    repository_full_name: str
    expected_version: str
    approved_by: str
    approved_at: datetime
    expires_at: datetime

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload(include_sha=False))

    def to_payload(self, *, include_sha: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "approval_id": self.approval_id,
            "approved_at": self.approved_at.isoformat().replace("+00:00", "Z"),
            "approved_by": self.approved_by,
            "contract": "EvaAdvancedTaskRepositoryApproval/v1",
            "expected_version": self.expected_version,
            "expires_at": self.expires_at.isoformat().replace("+00:00", "Z"),
            "inventory_sha256": self.inventory_sha256,
            "plan_id": self.plan_id,
            "plan_sha256": self.plan_sha256,
            "repository_full_name": self.repository_full_name,
            "step_id": "register_localtuya_repository",
        }
        if include_sha:
            payload["approval_sha256"] = self.sha256
        return payload

    @classmethod
    def from_payload(cls, raw: object) -> AdvancedTaskRepositoryApproval:
        if (
            not isinstance(raw, dict)
            or raw.get("contract") != "EvaAdvancedTaskRepositoryApproval/v1"
            or raw.get("step_id") != "register_localtuya_repository"
        ):
            raise AdvancedTaskError("advanced repository approval is malformed")
        try:
            approval = cls(
                approval_id=str(raw["approval_id"]),
                plan_id=str(raw["plan_id"]),
                plan_sha256=str(raw["plan_sha256"]),
                inventory_sha256=str(raw["inventory_sha256"]),
                repository_full_name=str(raw["repository_full_name"]),
                expected_version=str(raw["expected_version"]),
                approved_by=str(raw["approved_by"]),
                approved_at=datetime.fromisoformat(
                    str(raw["approved_at"]).replace("Z", "+00:00")
                ),
                expires_at=datetime.fromisoformat(
                    str(raw["expires_at"]).replace("Z", "+00:00")
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AdvancedTaskError("advanced repository approval is malformed") from exc
        if raw.get("approval_sha256") != approval.sha256 or approval.to_payload() != raw:
            raise AdvancedTaskError("advanced repository approval hash or shape differs")
        return approval


@dataclass(frozen=True, slots=True)
class AdvancedTaskDevicePreflight:
    preflight_id: str
    plan_id: str
    plan_sha256: str
    inventory_sha256: str
    device_id: str
    config_entry_id: str
    local_device_id_sha256: str
    local_host_sha256: str
    protocol_version: str
    next_schema_sha256: str
    probed_at: datetime

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload(include_sha=False))

    def to_payload(self, *, include_sha: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "config_entry_id": self.config_entry_id,
            "contract": "EvaAdvancedTaskDevicePreflight/v1",
            "device_id": self.device_id,
            "inventory_sha256": self.inventory_sha256,
            "local_device_id_sha256": self.local_device_id_sha256,
            "local_host_sha256": self.local_host_sha256,
            "next_schema_sha256": self.next_schema_sha256,
            "plan_id": self.plan_id,
            "plan_sha256": self.plan_sha256,
            "preflight_id": self.preflight_id,
            "probed_at": self.probed_at.isoformat().replace("+00:00", "Z"),
            "protocol_version": self.protocol_version,
        }
        if include_sha:
            payload["preflight_sha256"] = self.sha256
        return payload

    @classmethod
    def from_payload(cls, raw: object) -> AdvancedTaskDevicePreflight:
        if (
            not isinstance(raw, dict)
            or raw.get("contract") != "EvaAdvancedTaskDevicePreflight/v1"
        ):
            raise AdvancedTaskError("advanced device preflight is malformed")
        try:
            preflight = cls(
                preflight_id=str(raw["preflight_id"]),
                plan_id=str(raw["plan_id"]),
                plan_sha256=str(raw["plan_sha256"]),
                inventory_sha256=str(raw["inventory_sha256"]),
                device_id=str(raw["device_id"]),
                config_entry_id=str(raw["config_entry_id"]),
                local_device_id_sha256=str(raw["local_device_id_sha256"]),
                local_host_sha256=str(raw["local_host_sha256"]),
                protocol_version=str(raw["protocol_version"]),
                next_schema_sha256=str(raw["next_schema_sha256"]),
                probed_at=datetime.fromisoformat(
                    str(raw["probed_at"]).replace("Z", "+00:00")
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AdvancedTaskError("advanced device preflight is malformed") from exc
        if (
            raw.get("preflight_sha256") != preflight.sha256
            or preflight.to_payload() != raw
            or preflight.probed_at.tzinfo is None
            or preflight.probed_at.utcoffset() != timedelta(0)
        ):
            raise AdvancedTaskError("advanced device preflight hash or shape differs")
        return preflight


@dataclass(frozen=True, slots=True)
class AdvancedTaskCanaryProof:
    proof_id: str
    plan_id: str
    plan_sha256: str
    inventory_sha256: str
    approval_id: str
    approval_sha256: str
    preflight_sha256: str
    device_id: str
    configuration_sha256: str
    localtuya_config_entry_id: str
    localtuya_device_id: str
    localtuya_entity_ids: tuple[str, ...]
    source_tuya_entity_ids: tuple[str, ...]
    source_cloud_still_present: bool
    observed_at: datetime

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload(include_sha=False))

    def to_payload(self, *, include_sha: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "approval_id": self.approval_id,
            "approval_sha256": self.approval_sha256,
            "configuration_sha256": self.configuration_sha256,
            "contract": "EvaAdvancedTaskCanaryProof/v1",
            "device_id": self.device_id,
            "inventory_sha256": self.inventory_sha256,
            "localtuya_config_entry_id": self.localtuya_config_entry_id,
            "localtuya_device_id": self.localtuya_device_id,
            "localtuya_entity_ids": list(self.localtuya_entity_ids),
            "observed_at": self.observed_at.isoformat().replace("+00:00", "Z"),
            "plan_id": self.plan_id,
            "plan_sha256": self.plan_sha256,
            "preflight_sha256": self.preflight_sha256,
            "proof_id": self.proof_id,
            "source_cloud_still_present": self.source_cloud_still_present,
            "source_tuya_entity_ids": list(self.source_tuya_entity_ids),
        }
        if include_sha:
            payload["proof_sha256"] = self.sha256
        return payload

    @classmethod
    def from_payload(cls, raw: object) -> AdvancedTaskCanaryProof:
        if (
            not isinstance(raw, dict)
            or raw.get("contract") != "EvaAdvancedTaskCanaryProof/v1"
        ):
            raise AdvancedTaskError("advanced canary proof is malformed")
        try:
            proof = cls(
                proof_id=str(raw["proof_id"]),
                plan_id=str(raw["plan_id"]),
                plan_sha256=str(raw["plan_sha256"]),
                inventory_sha256=str(raw["inventory_sha256"]),
                approval_id=str(raw["approval_id"]),
                approval_sha256=str(raw["approval_sha256"]),
                preflight_sha256=str(raw["preflight_sha256"]),
                device_id=str(raw["device_id"]),
                configuration_sha256=str(raw["configuration_sha256"]),
                localtuya_config_entry_id=str(raw["localtuya_config_entry_id"]),
                localtuya_device_id=str(raw["localtuya_device_id"]),
                localtuya_entity_ids=tuple(
                    str(value) for value in raw["localtuya_entity_ids"]
                ),
                source_tuya_entity_ids=tuple(
                    str(value) for value in raw["source_tuya_entity_ids"]
                ),
                source_cloud_still_present=bool(raw["source_cloud_still_present"]),
                observed_at=datetime.fromisoformat(
                    str(raw["observed_at"]).replace("Z", "+00:00")
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AdvancedTaskError("advanced canary proof is malformed") from exc
        if (
            raw.get("proof_sha256") != proof.sha256
            or proof.to_payload() != raw
            or proof.observed_at.tzinfo is None
            or proof.observed_at.utcoffset() != timedelta(0)
        ):
            raise AdvancedTaskError("advanced canary proof hash or shape differs")
        return proof


def verify_localtuya_canary_readback(
    plan: AdvancedTaskPlan,
    approval: AdvancedTaskApproval,
    preflight: AdvancedTaskDevicePreflight,
    *,
    configuration_sha256: str,
    before: HomeAssistantInventory,
    after: HomeAssistantInventory,
    observed_at: datetime,
) -> AdvancedTaskCanaryProof:
    """Admit one reversible canary only from an independent HA registry readback."""

    if observed_at.tzinfo is None or observed_at.utcoffset() != timedelta(0):
        raise ValueError("advanced canary proof time must be UTC")
    observed_at = observed_at.astimezone(UTC).replace(microsecond=0)
    if observed_at > approval.expires_at:
        raise AdvancedTaskBlocked("advanced canary approval expired before readback")
    if (
        approval.plan_id != plan.plan_id
        or approval.plan_sha256 != plan.sha256
        or approval.inventory_sha256 != plan.inventory_sha256
        or preflight.plan_id != plan.plan_id
        or preflight.plan_sha256 != plan.sha256
        or preflight.inventory_sha256 != plan.inventory_sha256
        or approval.device_id != preflight.device_id
    ):
        raise AdvancedTaskBlocked("advanced canary authority differs")
    if len(configuration_sha256) != 64 or any(
        value not in "0123456789abcdef" for value in configuration_sha256
    ):
        raise ValueError("advanced canary configuration hash is invalid")
    selected = next(
        (
            device
            for device in plan.devices
            if device.device_id == approval.device_id
            and device.route == "localtuya_candidate"
        ),
        None,
    )
    if selected is None:
        raise AdvancedTaskBlocked("advanced canary device is not in the plan")
    before_devices = {device.device_id: device for device in before.devices}
    after_devices = {device.device_id: device for device in after.devices}
    before_entities = {entity.entity_id: entity for entity in before.entities}
    after_entities = {entity.entity_id: entity for entity in after.entities}
    source_before = before_devices.get(selected.device_id)
    source_after = after_devices.get(selected.device_id)
    if source_before is None or source_after != source_before:
        raise AdvancedTaskBlocked("source Tuya device changed during canary")
    source_entity_ids = tuple(sorted(selected.entity_ids))
    if not source_entity_ids or any(
        entity_id not in before_entities
        or before_entities[entity_id].platform != "tuya"
        or after_entities.get(entity_id) != before_entities[entity_id]
        for entity_id in source_entity_ids
    ):
        raise AdvancedTaskBlocked("source Tuya entities changed during canary")
    if not any(
        integration.domain == "localtuya"
        and integration.entry_id == preflight.config_entry_id
        and integration.state == "loaded"
        for integration in after.integrations
    ):
        raise AdvancedTaskBlocked("LocalTuya integration is not loaded after canary")
    before_local_device_ids = {
        device.device_id
        for device in before.devices
        if preflight.config_entry_id in device.config_entry_ids
    }
    new_local_devices = [
        device
        for device in after.devices
        if preflight.config_entry_id in device.config_entry_ids
        and device.device_id not in before_local_device_ids
    ]
    if len(new_local_devices) != 1:
        raise AdvancedTaskBlocked(
            "canary did not create exactly one LocalTuya device group"
        )
    local_device = new_local_devices[0]
    local_entity_ids = tuple(
        sorted(
            entity.entity_id
            for entity in after.entities
            if entity.device_id == local_device.device_id
            and entity.config_entry_id == preflight.config_entry_id
            and entity.platform == "localtuya"
            and entity.entity_id not in before_entities
        )
    )
    if not local_entity_ids:
        raise AdvancedTaskBlocked("canary has no independently read LocalTuya entity")
    unexpected_removed = set(before_entities) - set(after_entities)
    if unexpected_removed:
        raise AdvancedTaskBlocked("canary removed existing Home Assistant entities")
    seed = {
        "after_inventory_sha256": after.sha256,
        "approval_sha256": approval.sha256,
        "configuration_sha256": configuration_sha256,
        "device_id": selected.device_id,
        "localtuya_device_id": local_device.device_id,
        "localtuya_entity_ids": list(local_entity_ids),
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "preflight_sha256": preflight.sha256,
    }
    return AdvancedTaskCanaryProof(
        proof_id="proof:" + hashlib.sha256(canonical_json_bytes(seed)).hexdigest(),
        plan_id=plan.plan_id,
        plan_sha256=plan.sha256,
        inventory_sha256=plan.inventory_sha256,
        approval_id=approval.approval_id,
        approval_sha256=approval.sha256,
        preflight_sha256=preflight.sha256,
        device_id=selected.device_id,
        configuration_sha256=configuration_sha256,
        localtuya_config_entry_id=preflight.config_entry_id,
        localtuya_device_id=local_device.device_id,
        localtuya_entity_ids=local_entity_ids,
        source_tuya_entity_ids=source_entity_ids,
        source_cloud_still_present=True,
        observed_at=observed_at,
    )


def adopt_device_preflight(
    plan: AdvancedTaskPlan,
    *,
    device_id: str,
    config_entry_id: str,
    local_device_id_sha256: str,
    local_host_sha256: str,
    protocol_version: str,
    next_schema_sha256: str,
    probed_at: datetime,
) -> AdvancedTaskDevicePreflight:
    """Bind a successful, aborted-before-write LocalTuya probe to one candidate."""

    if probed_at.tzinfo is None or probed_at.utcoffset() != timedelta(0):
        raise ValueError("advanced device preflight time must be UTC")
    probed_at = probed_at.astimezone(UTC).replace(microsecond=0)
    selected = next(
        (
            device
            for device in plan.devices
            if device.device_id == device_id and device.route == "localtuya_candidate"
        ),
        None,
    )
    if selected is None:
        raise AdvancedTaskBlocked("device is not a LocalTuya candidate")
    forbidden = {
        "localtuya_integration_missing",
        "localtuya_integration_not_loaded",
        "localtuya_package_not_available",
        "localtuya_package_not_installed",
    }
    if forbidden.intersection(plan.blockers):
        raise AdvancedTaskBlocked("LocalTuya package and integration are not ready")
    hashes = (local_device_id_sha256, local_host_sha256, next_schema_sha256)
    if any(
        len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
        for value in hashes
    ):
        raise ValueError("advanced device preflight hash is invalid")
    if protocol_version not in {"3.1", "3.2", "3.3", "3.4"}:
        raise ValueError("advanced device preflight protocol is invalid")
    seed = {
        "config_entry_id": config_entry_id,
        "device_id": device_id,
        "local_device_id_sha256": local_device_id_sha256,
        "local_host_sha256": local_host_sha256,
        "plan_sha256": plan.sha256,
        "probed_at": probed_at.isoformat().replace("+00:00", "Z"),
    }
    return AdvancedTaskDevicePreflight(
        preflight_id="preflight:"
        + hashlib.sha256(canonical_json_bytes(seed)).hexdigest(),
        plan_id=plan.plan_id,
        plan_sha256=plan.sha256,
        inventory_sha256=plan.inventory_sha256,
        device_id=device_id,
        config_entry_id=config_entry_id,
        local_device_id_sha256=local_device_id_sha256,
        local_host_sha256=local_host_sha256,
        protocol_version=protocol_version,
        next_schema_sha256=next_schema_sha256,
        probed_at=probed_at,
    )


def approve_localtuya_package_install(
    plan: AdvancedTaskPlan,
    *,
    repository_id: str,
    version: str,
    approved_by: str,
    approved_at: datetime,
    ttl: timedelta = timedelta(minutes=15),
) -> AdvancedTaskPackageApproval:
    """Authorize one exact HACS package version without authorizing migration."""

    if approved_at.tzinfo is None or approved_at.utcoffset() != timedelta(0):
        raise ValueError("advanced task approval time must be UTC")
    approved_at = approved_at.astimezone(UTC).replace(microsecond=0)
    if not timedelta(minutes=1) <= ttl <= timedelta(hours=1):
        raise ValueError("advanced task approval TTL is outside 1..60 minutes")
    repository = next(
        (item for item in plan.repositories if item.repository_id == repository_id),
        None,
    )
    if repository is None or repository.domain != "localtuya":
        raise AdvancedTaskBlocked("LocalTuya package is not bound to this plan")
    if "localtuya_package_incompatible_with_home_assistant" in plan.blockers:
        raise AdvancedTaskBlocked(
            "LocalTuya package is incompatible with this Home Assistant version"
        )
    if (
        repository.full_name != MAINTAINED_LOCALTUYA_REPOSITORY
        and any(
            item.full_name == MAINTAINED_LOCALTUYA_REPOSITORY
            for item in plan.repositories
        )
    ):
        raise AdvancedTaskBlocked(
            "LocalTuya package is not from the maintained approved repository"
        )
    if repository.installed:
        raise AdvancedTaskBlocked("LocalTuya package is already installed")
    if not repository.can_download or repository.available_version != version:
        raise AdvancedTaskBlocked("LocalTuya package version is not installable")
    if "localtuya_package_not_installed" not in plan.blockers:
        raise AdvancedTaskBlocked("LocalTuya package install is not a current blocker")
    if not approved_by.strip() or len(approved_by) > 240:
        raise ValueError("advanced task approver is invalid")
    seed = {
        "approved_at": approved_at.isoformat().replace("+00:00", "Z"),
        "approved_by": approved_by.strip(),
        "plan_sha256": plan.sha256,
        "repository_id": repository_id,
        "repository_sha256": repository.sha256,
        "version": version,
    }
    return AdvancedTaskPackageApproval(
        approval_id=(
            "approval:" + hashlib.sha256(canonical_json_bytes(seed)).hexdigest()
        ),
        plan_id=plan.plan_id,
        plan_sha256=plan.sha256,
        inventory_sha256=plan.inventory_sha256,
        repository_id=repository_id,
        repository_sha256=repository.sha256,
        version=version,
        approved_by=approved_by.strip(),
        approved_at=approved_at,
        expires_at=approved_at + ttl,
    )


def approve_localtuya_repository_registration(
    plan: AdvancedTaskPlan,
    *,
    repository_full_name: str,
    expected_version: str,
    approved_by: str,
    approved_at: datetime,
    ttl: timedelta = timedelta(minutes=15),
) -> AdvancedTaskRepositoryApproval:
    """Authorize adding only the maintained LocalTuya HACS source."""

    if approved_at.tzinfo is None or approved_at.utcoffset() != timedelta(0):
        raise ValueError("advanced task approval time must be UTC")
    approved_at = approved_at.astimezone(UTC).replace(microsecond=0)
    if not timedelta(minutes=1) <= ttl <= timedelta(hours=1):
        raise ValueError("advanced task approval TTL is outside 1..60 minutes")
    if repository_full_name != MAINTAINED_LOCALTUYA_REPOSITORY:
        raise AdvancedTaskBlocked("LocalTuya repository is not the maintained source")
    if _numeric_version(expected_version) != MINIMUM_MAINTAINED_LOCALTUYA_VERSION:
        raise AdvancedTaskBlocked("LocalTuya repository version is not the approved release")
    if "localtuya_maintained_repository_not_available" not in plan.blockers:
        raise AdvancedTaskBlocked("maintained LocalTuya repository is not a current blocker")
    if any(item.full_name == repository_full_name for item in plan.repositories):
        raise AdvancedTaskBlocked("maintained LocalTuya repository is already registered")
    if not approved_by.strip() or len(approved_by) > 240:
        raise ValueError("advanced task approver is invalid")
    seed = {
        "approved_at": approved_at.isoformat().replace("+00:00", "Z"),
        "approved_by": approved_by.strip(),
        "expected_version": expected_version,
        "plan_sha256": plan.sha256,
        "repository_full_name": repository_full_name,
    }
    return AdvancedTaskRepositoryApproval(
        approval_id=(
            "approval:" + hashlib.sha256(canonical_json_bytes(seed)).hexdigest()
        ),
        plan_id=plan.plan_id,
        plan_sha256=plan.sha256,
        inventory_sha256=plan.inventory_sha256,
        repository_full_name=repository_full_name,
        expected_version=expected_version,
        approved_by=approved_by.strip(),
        approved_at=approved_at,
        expires_at=approved_at + ttl,
    )


def approve_canary(
    plan: AdvancedTaskPlan,
    *,
    device_id: str,
    approved_by: str,
    approved_at: datetime,
    ttl: timedelta = timedelta(minutes=15),
    preflight: AdvancedTaskDevicePreflight | None = None,
) -> AdvancedTaskApproval:
    """Bind human authority to one ready plan and one reversible device."""

    if approved_at.tzinfo is None or approved_at.utcoffset() != timedelta(0):
        raise ValueError("advanced task approval time must be UTC")
    approved_at = approved_at.astimezone(UTC).replace(microsecond=0)
    if not timedelta(minutes=1) <= ttl <= timedelta(hours=1):
        raise ValueError("advanced task approval TTL is outside 1..60 minutes")
    remaining_blockers = set(plan.blockers) - {
        "matter_devices_require_native_matter_review",
        "tuya_scenes_require_explicit_rebuild",
    }
    if preflight is not None and (
        preflight.plan_id == plan.plan_id
        and preflight.plan_sha256 == plan.sha256
        and preflight.inventory_sha256 == plan.inventory_sha256
        and preflight.device_id == device_id
        and timedelta(0) <= approved_at - preflight.probed_at <= timedelta(minutes=15)
    ):
        remaining_blockers.discard("local_device_credentials_not_collected")
    if remaining_blockers:
        raise AdvancedTaskBlocked(
            "canary approval is forbidden while preflight blockers remain"
        )
    if not approved_by.strip() or len(approved_by) > 240:
        raise ValueError("advanced task approver is invalid")
    selected = next(
        (
            device
            for device in plan.devices
            if device.device_id == device_id and device.route == "localtuya_candidate"
        ),
        None,
    )
    if selected is None:
        raise AdvancedTaskBlocked(
            "device is not an approved LocalTuya canary candidate"
        )
    seed = {
        "approved_at": approved_at.isoformat().replace("+00:00", "Z"),
        "approved_by": approved_by.strip(),
        "device_id": device_id,
        "plan_sha256": plan.sha256,
        "step_id": "migrate_canary",
    }
    approval_id = "approval:" + hashlib.sha256(canonical_json_bytes(seed)).hexdigest()
    return AdvancedTaskApproval(
        approval_id=approval_id,
        plan_id=plan.plan_id,
        plan_sha256=plan.sha256,
        inventory_sha256=plan.inventory_sha256,
        step_id="migrate_canary",
        device_id=device_id,
        approved_by=approved_by.strip(),
        approved_at=approved_at,
        expires_at=approved_at + ttl,
    )


class AdvancedTaskStorePort(Protocol):
    def retain(self, plan: AdvancedTaskPlan) -> str: ...


def advanced_plan_id(*, operation_id: str, requested_by: str) -> str:
    if not operation_id.strip() or not requested_by.strip():
        raise ValueError("advanced task identity is incomplete")
    seed = {
        "operation_id": operation_id.strip(),
        "requested_by": requested_by.strip(),
        "task": "migrate_tuya_to_localtuya",
    }
    return f"advanced:{hashlib.sha256(canonical_json_bytes(seed)).hexdigest()}"


def require_current_package_preflight(
    plan: AdvancedTaskPlan, inventory: HomeAssistantInventory
) -> None:
    """Reject package activation when the live registry drifted after planning."""

    current = TuyaToLocalTuyaPlanner().build(
        inventory,
        operation_id=plan.operation_id,
        requested_by=plan.requested_by,
    )
    if (
        current.devices != plan.devices
        or current.repositories != plan.repositories
        or current.blockers != plan.blockers
    ):
        raise AdvancedTaskBlocked(
            "Home Assistant inventory changed after LocalTuya approval"
        )
    local_entry_ids = {
        item.entry_id for item in inventory.integrations if item.domain == "localtuya"
    }
    existing_devices = any(
        local_entry_ids.intersection(device.config_entry_ids)
        for device in inventory.devices
    )
    existing_entities = any(
        entity.config_entry_id in local_entry_ids for entity in inventory.entities
    )
    if existing_devices or existing_entities:
        raise AdvancedTaskBlocked(
            "LocalTuya package activation could revive existing device configuration"
        )


def require_current_repository_preflight(
    plan: AdvancedTaskPlan, inventory: HomeAssistantInventory
) -> None:
    """Reject HACS source registration when live inventory drifted after approval."""

    current = TuyaToLocalTuyaPlanner().build(
        inventory,
        operation_id=plan.operation_id,
        requested_by=plan.requested_by,
    )
    if (
        current.devices != plan.devices
        or current.repositories != plan.repositories
        or current.blockers != plan.blockers
    ):
        raise AdvancedTaskBlocked(
            "Home Assistant inventory changed after repository approval"
        )
    if "localtuya_maintained_repository_not_available" not in current.blockers:
        raise AdvancedTaskBlocked("maintained LocalTuya repository is already available")


class TuyaToLocalTuyaPlanner:
    """Create a safe per-device plan; never infer credentials or mutate HA."""

    def build(
        self,
        inventory: HomeAssistantInventory,
        *,
        operation_id: str,
        requested_by: str,
    ) -> AdvancedTaskPlan:
        tuya_entries = {
            item.entry_id for item in inventory.integrations if item.domain == "tuya"
        }
        local_entries = [
            item for item in inventory.integrations if item.domain == "localtuya"
        ]
        local_repositories = [
            item for item in inventory.repositories if item.domain == "localtuya"
        ]
        entities_by_device: dict[str, list[str]] = {}
        for entity in inventory.entities:
            if entity.device_id is not None:
                entities_by_device.setdefault(entity.device_id, []).append(
                    entity.entity_id
                )
        devices: list[AdvancedTaskDevice] = []
        for device in inventory.devices:
            if not set(device.config_entry_ids) & tuya_entries:
                continue
            entity_ids = tuple(sorted(entities_by_device.get(device.device_id, [])))
            model = (device.model or "").casefold()
            if "tuya scene" in model or any(
                value.startswith("scene.") for value in entity_ids
            ):
                route = "rebuild_scene"
            elif "matter" in model:
                route = "matter_review"
            else:
                route = "localtuya_candidate"
            devices.append(
                AdvancedTaskDevice(device.device_id, device.name, route, entity_ids)
            )
        devices.sort(
            key=lambda value: (value.route, value.name.casefold(), value.device_id)
        )
        blockers: list[str] = []
        if not tuya_entries:
            blockers.append("tuya_integration_missing")
        if not local_entries:
            blockers.append("localtuya_integration_missing")
        elif not any(item.state == "loaded" for item in local_entries):
            blockers.append("localtuya_integration_not_loaded")
        if inventory.repository_inventory_verified:
            compatible_repositories = [
                item
                for item in local_repositories
                if _localtuya_repository_is_compatible(
                    item, home_assistant_version=inventory.home_assistant_version
                )
            ]
            if not local_repositories:
                blockers.append("localtuya_package_not_available")
            if (
                (_numeric_version(inventory.home_assistant_version) or (0, 0, 0))
                >= (2026, 0, 0)
                and not any(
                    item.full_name == MAINTAINED_LOCALTUYA_REPOSITORY
                    for item in local_repositories
                )
            ):
                blockers.append("localtuya_maintained_repository_not_available")
            if compatible_repositories and not any(
                item.installed for item in compatible_repositories
            ):
                blockers.append("localtuya_package_not_installed")
            if local_repositories and not compatible_repositories:
                blockers.append("localtuya_package_incompatible_with_home_assistant")
        if any(item.route == "localtuya_candidate" for item in devices):
            blockers.append("local_device_credentials_not_collected")
        if any(item.route == "matter_review" for item in devices):
            blockers.append("matter_devices_require_native_matter_review")
        if any(item.route == "rebuild_scene" for item in devices):
            blockers.append("tuya_scenes_require_explicit_rebuild")
        steps = (
            AdvancedTaskStep(
                "inventory",
                "Leg Tuya-, LocalTuya-, apparaat- en entiteitsinventaris vast",
                "read_only",
                False,
                "De opnieuw gelezen registerhash is gelijk aan inventory_sha256.",
                None,
            ),
            AdvancedTaskStep(
                "inspect_preflight",
                "Controleer HACS-pakket, LocalTuya-configuratie en actuele registerstaat",
                "read_only",
                False,
                "De HACS-repository, geïnstalleerde versie en integratiestatus zijn onafhankelijk teruggelezen.",
                None,
            ),
            AdvancedTaskStep(
                "install_localtuya_package",
                "Installeer exact de goedgekeurde LocalTuya-versie en laad de integratie",
                "reversible",
                True,
                "HACS meldt exact de gekozen versie installed en Home Assistant kan de integratie laden.",
                "Verwijder alleen de zojuist geïnstalleerde versie en herstel de vorige integratiestatus.",
            ),
            AdvancedTaskStep(
                "collect_local_credentials",
                "Verzamel IP, device-id, local key en protocolversie in de secret store",
                "read_only",
                True,
                "Ieder kandidaatapparaat heeft een hashgebonden secret-reference en slaagt voor een lokale verbindingsproef; geheime waarden staan niet in logs of de tijdlijn.",
                "Verwijder de nieuwe secret-references zonder apparaatconfiguratie te wijzigen.",
            ),
            AdvancedTaskStep(
                "approve_canary",
                "Kies één omkeerbaar kandidaatapparaat en vraag expliciete goedkeuring",
                "reversible",
                True,
                "De goedkeuring bindt planhash, apparaat-id en huidige registerhash.",
                "Geen wijziging uitvoeren zonder geldige goedkeuring.",
            ),
            AdvancedTaskStep(
                "migrate_canary",
                "Voeg alleen het canary-apparaat lokaal toe terwijl Tuya actief blijft",
                "reversible",
                True,
                "Lokale status en een omkeerbare bediening zijn onafhankelijk teruggelezen.",
                "Verwijder de nieuwe lokale configuratie; laat de Tuya-configuratie actief.",
            ),
            AdvancedTaskStep(
                "rewire_references",
                "Zet afhankelijke automatiseringen en dashboards gecontroleerd over",
                "reversible",
                True,
                "Alle verwijzingen resolven en de canary werkt zonder cloudpad.",
                "Herstel de opgeslagen verwijzingen naar de oorspronkelijke Tuya-entiteiten.",
            ),
            AdvancedTaskStep(
                "migrate_remaining",
                "Herhaal apparaat voor apparaat met bewijs en een afzonderlijk herstelpunt",
                "high",
                True,
                "Elk apparaat heeft lokale readback, referentiecontrole en een eigen receipt.",
                "Heractiveer uitsluitend het laatst gewijzigde Tuya-apparaat en herstel zijn verwijzingen.",
            ),
            AdvancedTaskStep(
                "retire_cloud",
                "Schakel de Tuya-cloudintegratie pas uit nadat alles lokaal bewezen is",
                "high",
                True,
                "Een verse inventaris toont nul actieve afhankelijkheden van de cloudentiteiten.",
                "Herlaad Tuya en herstel de laatste bewezen registermapping.",
            ),
        )
        plan_id = advanced_plan_id(operation_id=operation_id, requested_by=requested_by)
        return AdvancedTaskPlan(
            plan_id,
            operation_id,
            "migrate_tuya_to_localtuya",
            inventory.sha256,
            "blocked_preflight" if blockers else "awaiting_approval",
            tuple(blockers),
            tuple(devices),
            steps,
            requested_by,
            inventory.captured_at,
            tuple(local_repositories),
        )


def _numeric_version(value: str | None) -> tuple[int, int, int] | None:
    if value is None:
        return None
    parts = value.removeprefix("v").split(".")
    if len(parts) < 2:
        return None
    parsed: list[int] = []
    for part in parts[:3]:
        digits = "".join(character for character in part if character.isdigit())
        if not digits:
            return None
        parsed.append(int(digits))
    while len(parsed) < 3:
        parsed.append(0)
    return tuple(parsed)  # type: ignore[return-value]


def _localtuya_release_is_compatible(
    *, home_assistant_version: str | None, localtuya_version: str | None
) -> bool:
    """Fail the known LocalTuya 5.2.5 / Home Assistant 2026 boundary closed."""

    home_assistant = _numeric_version(home_assistant_version)
    localtuya = _numeric_version(localtuya_version)
    if home_assistant is None or localtuya is None:
        return True
    return not (home_assistant >= (2026, 0, 0) and localtuya <= (5, 2, 5))


def _localtuya_repository_is_supported(
    repository: HomeAssistantRepository,
) -> bool:
    version = _numeric_version(
        repository.installed_version or repository.available_version
    )
    if version is None:
        return False
    if repository.full_name == MAINTAINED_LOCALTUYA_REPOSITORY:
        return version >= MINIMUM_MAINTAINED_LOCALTUYA_VERSION
    return repository.full_name == "rospogrigio/localtuya"


def _localtuya_repository_is_compatible(
    repository: HomeAssistantRepository, *, home_assistant_version: str | None
) -> bool:
    """Require maintained source, a current release and its declared HA floor."""

    if not _localtuya_repository_is_supported(repository):
        return False
    home_assistant = _numeric_version(home_assistant_version)
    version = _numeric_version(
        repository.installed_version or repository.available_version
    )
    minimum_home_assistant = _numeric_version(
        repository.minimum_home_assistant_version
    )
    if home_assistant is None or version is None:
        return False
    if minimum_home_assistant is not None and home_assistant < minimum_home_assistant:
        return False
    if home_assistant >= (2026, 0, 0):
        return (
            repository.full_name == MAINTAINED_LOCALTUYA_REPOSITORY
            and version >= MINIMUM_MAINTAINED_LOCALTUYA_VERSION
        )
    return _localtuya_release_is_compatible(
        home_assistant_version=home_assistant_version,
        localtuya_version=repository.installed_version or repository.available_version,
    )


__all__ = [
    "AdvancedTaskApproval",
    "AdvancedTaskBlocked",
    "AdvancedTaskCanaryProof",
    "AdvancedTaskDevicePreflight",
    "AdvancedTaskDevice",
    "AdvancedTaskError",
    "AdvancedTaskPlan",
    "AdvancedTaskPackageApproval",
    "AdvancedTaskRepositoryApproval",
    "AdvancedTaskStep",
    "HomeAssistantDevice",
    "HomeAssistantEntity",
    "HomeAssistantIntegration",
    "HomeAssistantInventory",
    "HomeAssistantRepository",
    "MAINTAINED_LOCALTUYA_REPOSITORY",
    "LocalTuyaCanaryConfiguration",
    "LocalTuyaEntityConfiguration",
    "TuyaToLocalTuyaPlanner",
    "advanced_plan_id",
    "adopt_device_preflight",
    "require_current_package_preflight",
    "require_current_repository_preflight",
    "verify_localtuya_canary_readback",
    "approve_canary",
    "approve_localtuya_package_install",
    "approve_localtuya_repository_registration",
]
