"""Exact HACS package installation with version-bound readback and rollback."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from websockets.sync.client import connect

from eva.adapters.connectors.home_assistant_advanced_tasks import (
    AdvancedTaskError,
    AdvancedTaskPackageApproval,
    AdvancedTaskRepositoryApproval,
    HomeAssistantRepository,
    MAINTAINED_LOCALTUYA_REPOSITORY,
)


class HomeAssistantHacsAdapter:
    def __init__(self, *, base_url: str, token: str) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Home Assistant HACS URL is invalid")
        if not token:
            raise ValueError("Home Assistant HACS token is required")
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

    async def repository(self, repository_id: str) -> HomeAssistantRepository:
        return await asyncio.to_thread(self._repository_sync, repository_id)

    async def repository_by_full_name(
        self, full_name: str
    ) -> HomeAssistantRepository | None:
        return await asyncio.to_thread(self._repository_by_full_name_sync, full_name)

    async def register_exact(
        self,
        approval: AdvancedTaskRepositoryApproval,
        *,
        now: datetime,
        poll_interval_seconds: float = 0.5,
        timeout_seconds: float = 20.0,
    ) -> HomeAssistantRepository:
        if now >= approval.expires_at:
            raise AdvancedTaskError("LocalTuya repository approval expired")
        if approval.repository_full_name != MAINTAINED_LOCALTUYA_REPOSITORY:
            raise AdvancedTaskError("LocalTuya repository approval source differs")
        existing = await self.repository_by_full_name(approval.repository_full_name)
        if existing is not None:
            self._verify_registered_repository(existing, approval)
            return existing
        await asyncio.to_thread(
            self._command_sync,
            "hacs/repositories/add",
            {
                "category": "integration",
                "repository": approval.repository_full_name,
            },
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while True:
            observed = await self.repository_by_full_name(
                approval.repository_full_name
            )
            if observed is not None:
                self._verify_registered_repository(observed, approval)
                return observed
            if loop.time() >= deadline:
                raise AdvancedTaskError(
                    "LocalTuya repository registration was not verified"
                )
            await asyncio.sleep(poll_interval_seconds)

    async def install_exact(
        self,
        approval: AdvancedTaskPackageApproval,
        *,
        now: datetime,
        poll_interval_seconds: float = 0.5,
        timeout_seconds: float = 15.0,
    ) -> HomeAssistantRepository:
        if now >= approval.expires_at:
            raise AdvancedTaskError("LocalTuya package approval expired")
        before = await self.repository(approval.repository_id)
        if before.sha256 != approval.repository_sha256:
            raise AdvancedTaskError("LocalTuya repository changed after approval")
        if before.available_version != approval.version or not before.can_download:
            raise AdvancedTaskError("approved LocalTuya version is no longer available")
        if before.installed:
            if before.installed_version == approval.version:
                return before
            raise AdvancedTaskError("another LocalTuya version is already installed")
        await asyncio.to_thread(
            self._command_sync,
            "hacs/repository/download",
            {"repository": approval.repository_id, "version": approval.version},
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while True:
            observed = await self.repository(approval.repository_id)
            if observed.installed and observed.installed_version == approval.version:
                return observed
            if loop.time() >= deadline:
                raise AdvancedTaskError("LocalTuya package install was not verified")
            await asyncio.sleep(poll_interval_seconds)

    async def rollback_exact(
        self,
        *,
        repository_id: str,
        expected_installed_sha256: str,
        poll_interval_seconds: float = 0.5,
        timeout_seconds: float = 15.0,
    ) -> HomeAssistantRepository:
        before = await self.repository(repository_id)
        if before.sha256 != expected_installed_sha256 or not before.installed:
            raise AdvancedTaskError("LocalTuya rollback authority differs")
        await asyncio.to_thread(
            self._command_sync,
            "hacs/repository/remove",
            {"repository": repository_id},
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while True:
            observed = await self.repository(repository_id)
            if not observed.installed:
                return observed
            if loop.time() >= deadline:
                raise AdvancedTaskError("LocalTuya package rollback was not verified")
            await asyncio.sleep(poll_interval_seconds)

    def _repository_sync(self, repository_id: str) -> HomeAssistantRepository:
        matches = [
            value
            for value in self._repositories_sync()
            if value.repository_id == repository_id
        ]
        if len(matches) != 1:
            raise AdvancedTaskError("HACS repository identity is not unique")
        return matches[0]

    def _repository_by_full_name_sync(
        self, full_name: str
    ) -> HomeAssistantRepository | None:
        matches = [
            value for value in self._repositories_sync() if value.full_name == full_name
        ]
        if len(matches) > 1:
            raise AdvancedTaskError("HACS repository source identity is not unique")
        return matches[0] if matches else None

    def _repositories_sync(self) -> tuple[HomeAssistantRepository, ...]:
        result = self._command_sync(
            "hacs/repositories/list", {"categories": ["integration"]}
        )
        if not isinstance(result, list):
            raise AdvancedTaskError("HACS repository inventory is invalid")
        repositories: list[HomeAssistantRepository] = []
        for raw in result:
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
        return tuple(repositories)

    @staticmethod
    def _verify_registered_repository(
        repository: HomeAssistantRepository,
        approval: AdvancedTaskRepositoryApproval,
    ) -> None:
        if (
            repository.full_name != approval.repository_full_name
            or repository.domain != "localtuya"
            or not repository.custom
            or not repository.can_download
            or not _versions_equal(
                repository.available_version, approval.expected_version
            )
        ):
            raise AdvancedTaskError("registered LocalTuya repository differs")

    def _command_sync(self, command_type: str, values: dict[str, object]) -> object:
        with connect(
            self._url,
            open_timeout=5,
            close_timeout=2,
            max_size=8_388_608,
            ping_interval=20,
            ping_timeout=10,
        ) as socket:
            if self._decode(socket.recv(timeout=5)).get("type") != "auth_required":
                raise AdvancedTaskError("Home Assistant auth challenge is invalid")
            socket.send(json.dumps({"access_token": self._token, "type": "auth"}))
            if self._decode(socket.recv(timeout=5)).get("type") != "auth_ok":
                raise AdvancedTaskError("Home Assistant authentication failed")
            message = {"id": 1, "type": command_type, **values}
            socket.send(json.dumps(message))
            frame = self._decode(socket.recv(timeout=20))
        if (
            frame.get("type") != "result"
            or frame.get("id") != 1
            or frame.get("success") is not True
        ):
            error = frame.get("error")
            code = error.get("code") if isinstance(error, dict) else "unknown"
            raise AdvancedTaskError(f"Home Assistant rejected {command_type}: {code}")
        return frame.get("result")

    @staticmethod
    def _decode(raw: str | bytes) -> dict[str, Any]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise AdvancedTaskError("Home Assistant HACS frame is invalid")
        return value


def _versions_equal(left: str | None, right: str) -> bool:
    def numeric(value: str | None) -> tuple[int, int, int] | None:
        if value is None:
            return None
        parts = value.removeprefix("v").split(".")
        if len(parts) < 2:
            return None
        try:
            parsed = [int(part) for part in parts[:3]]
        except ValueError:
            return None
        while len(parsed) < 3:
            parsed.append(0)
        return tuple(parsed)  # type: ignore[return-value]

    return numeric(left) is not None and numeric(left) == numeric(right)


__all__ = ["HomeAssistantHacsAdapter"]
