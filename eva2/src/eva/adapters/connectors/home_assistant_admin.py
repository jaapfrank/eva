"""Read-only Home Assistant registry inventory for advanced task planning."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from websockets.sync.client import connect

from eva.application.advanced_tasks import (
    HomeAssistantDevice,
    HomeAssistantEntity,
    HomeAssistantIntegration,
    HomeAssistantInventory,
    HomeAssistantRepository,
)


class HomeAssistantAdminAdapter:
    def __init__(self, *, base_url: str, token: str) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Home Assistant admin URL is invalid")
        if not token:
            raise ValueError("Home Assistant admin token is required")
        self._url = urlunsplit(
            (
                "wss" if parsed.scheme == "https" else "ws",
                parsed.netloc,
                f"{parsed.path.rstrip('/')}/api/websocket",
                "",
                "",
            )
        )
        self._token = token

    async def inventory(
        self, *, admitted_domains: tuple[str, ...] = ("localtuya", "tuya")
    ) -> HomeAssistantInventory:
        return await asyncio.to_thread(self._inventory_sync, admitted_domains)

    def _inventory_sync(
        self, admitted_domains: tuple[str, ...]
    ) -> HomeAssistantInventory:
        domains = frozenset(admitted_domains)
        if not domains:
            raise ValueError("Home Assistant inventory domains are empty")
        with connect(
            self._url,
            open_timeout=5,
            close_timeout=2,
            max_size=8_388_608,
            ping_interval=20,
            ping_timeout=10,
        ) as socket:
            if self._decode(socket.recv(timeout=5)).get("type") != "auth_required":
                raise RuntimeError("Home Assistant admin auth challenge is invalid")
            socket.send(json.dumps({"access_token": self._token, "type": "auth"}))
            if self._decode(socket.recv(timeout=5)).get("type") != "auth_ok":
                raise RuntimeError("Home Assistant admin authentication failed")
            entries = self._command(socket, 1, "config_entries/get")
            devices = self._command(socket, 2, "config/device_registry/list")
            entities = self._command(socket, 3, "config/entity_registry/list")
            config = self._command(socket, 4, "get_config")
            repository_inventory_verified = True
            try:
                repositories = self._command(
                    socket,
                    5,
                    "hacs/repositories/list",
                    {"categories": ["integration"]},
                )
            except RuntimeError:
                repository_inventory_verified = False
                repositories = []
        return self._select_inventory(
            entries,
            devices,
            entities,
            repositories,
            domains=domains,
            captured_at=datetime.now(tz=UTC).replace(microsecond=0),
            repository_inventory_verified=repository_inventory_verified,
            home_assistant_version=(
                str(config["version"])
                if isinstance(config, dict) and isinstance(config.get("version"), str)
                else None
            ),
        )

    @classmethod
    def _command(
        cls,
        socket: Any,
        request_id: int,
        command_type: str,
        extra: dict[str, object] | None = None,
    ) -> object:
        message: dict[str, object] = {"id": request_id, "type": command_type}
        if extra is not None:
            message.update(extra)
        socket.send(json.dumps(message))
        frame = cls._decode(socket.recv(timeout=8))
        if (
            frame.get("type") != "result"
            or frame.get("id") != request_id
            or frame.get("success") is not True
        ):
            raise RuntimeError(f"Home Assistant rejected {command_type}")
        return frame.get("result")

    @staticmethod
    def _select_inventory(
        raw_entries: object,
        raw_devices: object,
        raw_entities: object,
        raw_repositories: object,
        *,
        domains: frozenset[str],
        captured_at: datetime,
        repository_inventory_verified: bool,
        home_assistant_version: str | None = None,
    ) -> HomeAssistantInventory:
        if not all(
            isinstance(value, list)
            for value in (raw_entries, raw_devices, raw_entities)
        ):
            raise RuntimeError("Home Assistant registry inventory is invalid")
        entries: list[HomeAssistantIntegration] = []
        for raw in raw_entries:
            if not isinstance(raw, dict) or raw.get("domain") not in domains:
                continue
            entries.append(
                HomeAssistantIntegration(
                    str(raw["domain"]),
                    str(raw["entry_id"]),
                    str(raw.get("state", "unknown")),
                    str(raw.get("title", raw["domain"])),
                )
            )
        entry_ids = {value.entry_id for value in entries}
        devices: list[HomeAssistantDevice] = []
        for raw in raw_devices:
            if not isinstance(raw, dict):
                continue
            config_entries = tuple(
                sorted(
                    value
                    for value in raw.get("config_entries", [])
                    if isinstance(value, str) and value in entry_ids
                )
            )
            if not config_entries:
                continue
            devices.append(
                HomeAssistantDevice(
                    str(raw["id"]),
                    str(raw.get("name_by_user") or raw.get("name") or raw["id"]),
                    raw.get("manufacturer")
                    if isinstance(raw.get("manufacturer"), str)
                    else None,
                    raw.get("model") if isinstance(raw.get("model"), str) else None,
                    config_entries,
                )
            )
        device_ids = {value.device_id for value in devices}
        entities: list[HomeAssistantEntity] = []
        for raw in raw_entities:
            if not isinstance(raw, dict):
                continue
            config_entry_id = raw.get("config_entry_id")
            device_id = raw.get("device_id")
            if config_entry_id not in entry_ids and device_id not in device_ids:
                continue
            entities.append(
                HomeAssistantEntity(
                    str(raw["entity_id"]),
                    str(raw.get("platform", "unknown")),
                    config_entry_id if isinstance(config_entry_id, str) else None,
                    device_id if isinstance(device_id, str) else None,
                    raw.get("disabled_by")
                    if isinstance(raw.get("disabled_by"), str)
                    else None,
                )
            )
        entries.sort(key=lambda value: (value.domain, value.entry_id))
        devices.sort(key=lambda value: value.device_id)
        entities.sort(key=lambda value: value.entity_id)
        repositories: list[HomeAssistantRepository] = []
        if isinstance(raw_repositories, list):
            for raw in raw_repositories:
                if not isinstance(raw, dict) or raw.get("domain") != "localtuya":
                    continue
                repositories.append(
                    HomeAssistantRepository(
                        repository_id=str(raw["id"]),
                        full_name=str(raw["full_name"]),
                        domain="localtuya",
                        installed=raw.get("installed") is True,
                        installed_version=(
                            raw.get("installed_version")
                            if isinstance(raw.get("installed_version"), str)
                            and raw.get("installed_version")
                            else None
                        ),
                        available_version=(
                            raw.get("available_version")
                            if isinstance(raw.get("available_version"), str)
                            and raw.get("available_version")
                            else None
                        ),
                        can_download=raw.get("can_download") is True,
                        minimum_home_assistant_version=(
                            raw.get("homeassistant")
                            if isinstance(raw.get("homeassistant"), str)
                            and raw.get("homeassistant")
                            else None
                        ),
                        custom=raw.get("custom") is True,
                    )
                )
        repositories.sort(key=lambda value: (value.domain, value.full_name))
        return HomeAssistantInventory(
            tuple(entries),
            tuple(devices),
            tuple(entities),
            captured_at,
            tuple(repositories),
            repository_inventory_verified,
            home_assistant_version,
        )

    @staticmethod
    def _decode(raw: str | bytes) -> dict[str, Any]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise RuntimeError("Home Assistant admin frame is invalid")
        return value


__all__ = ["HomeAssistantAdminAdapter"]
