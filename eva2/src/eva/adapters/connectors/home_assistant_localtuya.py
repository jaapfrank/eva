"""Secret-free LocalTuya connectivity probing through Home Assistant's options flow."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
import re
from typing import Any, Literal
from urllib import error, parse, request as urllib_request

from eva.application.advanced_tasks import (
    AdvancedTaskError,
    LocalTuyaCanaryConfiguration,
    LocalTuyaEntityConfiguration,
)
from eva.contracts.base import JsonValue, validate_json_value
from eva.contracts.canonical_json import canonical_sha256


_CONFIG_ENTRY_ID = re.compile(r"^[A-Z0-9]{20,40}$")
_DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_PROTOCOLS = frozenset({"3.1", "3.2", "3.3", "3.4"})


@dataclass(frozen=True, slots=True)
class LocalTuyaProbeSecret:
    """One-shot secret input; repr and receipts never expose the local key."""

    friendly_name: str
    host: str
    device_id: str
    local_key: str = field(repr=False)
    protocol_version: str = "3.3"

    def __post_init__(self) -> None:
        if not self.friendly_name.strip() or len(self.friendly_name) > 80:
            raise ValueError("LocalTuya friendly name is invalid")
        try:
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
        if _DEVICE_ID.fullmatch(self.device_id) is None:
            raise ValueError("LocalTuya device ID is invalid")
        if (
            not 16 <= len(self.local_key) <= 64
            or not self.local_key.isascii()
            or not self.local_key.isprintable()
        ):
            raise ValueError("LocalTuya local key is invalid")
        if self.protocol_version not in _PROTOCOLS:
            raise ValueError("LocalTuya protocol version is invalid")


@dataclass(frozen=True, slots=True)
class LocalTuyaConnectivityProof:
    config_entry_id: str
    device_id_sha256: str
    host_sha256: str
    protocol_version: str
    next_step_id: str
    next_schema_sha256: str
    flow_aborted: bool

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "config_entry_id": self.config_entry_id,
            "contract": "EvaLocalTuyaConnectivityProof/v1",
            "device_id_sha256": self.device_id_sha256,
            "flow_aborted": self.flow_aborted,
            "host_sha256": self.host_sha256,
            "next_schema_sha256": self.next_schema_sha256,
            "next_step_id": self.next_step_id,
            "protocol_version": self.protocol_version,
        }


@dataclass(frozen=True, slots=True)
class LocalTuyaCreationReceipt:
    configuration_sha256: str
    configured_entity_count: int
    disposition: Literal["created"]

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "configuration_sha256": self.configuration_sha256,
            "configured_entity_count": self.configured_entity_count,
            "contract": "EvaLocalTuyaCreationReceipt/v1",
            "disposition": self.disposition,
        }


class HomeAssistantLocalTuyaAdapter:
    """Validate credentials locally, then abort before LocalTuya stores a device."""

    def __init__(
        self, *, base_url: str, token: str, request_timeout_seconds: int = 12
    ) -> None:
        parsed = parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Home Assistant LocalTuya URL is invalid")
        if not token:
            raise ValueError("Home Assistant LocalTuya token is required")
        if not 2 <= request_timeout_seconds <= 30:
            raise ValueError("Home Assistant LocalTuya timeout is invalid")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = request_timeout_seconds

    async def probe(
        self, *, config_entry_id: str, secret: LocalTuyaProbeSecret
    ) -> LocalTuyaConnectivityProof:
        return await asyncio.to_thread(
            self._probe_sync, config_entry_id=config_entry_id, secret=secret
        )

    async def create_canary(
        self,
        *,
        config_entry_id: str,
        configuration: LocalTuyaCanaryConfiguration,
    ) -> LocalTuyaCreationReceipt:
        """Execute one already approved config flow exactly once."""

        return await asyncio.to_thread(
            self._create_canary_sync,
            config_entry_id=config_entry_id,
            configuration=configuration,
        )

    def _probe_sync(
        self, *, config_entry_id: str, secret: LocalTuyaProbeSecret
    ) -> LocalTuyaConnectivityProof:
        if _CONFIG_ENTRY_ID.fullmatch(config_entry_id) is None:
            raise ValueError("LocalTuya config entry ID is invalid")
        flow_id: str | None = None
        flow_aborted = False
        try:
            current = self._request_json(
                "POST",
                "/api/config/config_entries/options/flow",
                {"handler": config_entry_id},
            )
            flow_id = self._expect_form(current, "init")
            path = "/api/config/config_entries/options/flow/" + parse.quote(
                flow_id, safe=""
            )
            current = self._request_json("POST", path, {"action": "add_device"})
            self._expect_form(current, "add_device", expected_flow_id=flow_id)
            current = self._request_json("POST", path, {"selected_device": "..."})
            self._expect_form(current, "configure_device", expected_flow_id=flow_id)
            current = self._request_json(
                "POST",
                path,
                {
                    "device_id": secret.device_id,
                    "enable_debug": False,
                    "friendly_name": secret.friendly_name.strip(),
                    "host": secret.host,
                    "local_key": secret.local_key,
                    "protocol_version": secret.protocol_version,
                },
            )
            self._expect_form(current, "pick_entity_type", expected_flow_id=flow_id)
            errors = current.get("errors")
            if errors not in (None, {}):
                raise AdvancedTaskError("LocalTuya rejected the local connection")
            schema = current.get("data_schema")
            validate_json_value(schema)
            self._request_json("DELETE", path)
            flow_aborted = True
            return LocalTuyaConnectivityProof(
                config_entry_id=config_entry_id,
                device_id_sha256=hashlib.sha256(secret.device_id.encode()).hexdigest(),
                host_sha256=hashlib.sha256(secret.host.encode()).hexdigest(),
                protocol_version=secret.protocol_version,
                next_step_id="pick_entity_type",
                next_schema_sha256=canonical_sha256(schema),
                flow_aborted=True,
            )
        except error.HTTPError as exc:
            raise AdvancedTaskError(
                f"Home Assistant rejected LocalTuya preflight ({exc.code})"
            ) from exc
        finally:
            if flow_id is not None and not flow_aborted:
                try:
                    self._request_json(
                        "DELETE",
                        "/api/config/config_entries/options/flow/"
                        + parse.quote(flow_id, safe=""),
                    )
                except Exception:
                    pass

    def _create_canary_sync(
        self,
        *,
        config_entry_id: str,
        configuration: LocalTuyaCanaryConfiguration,
    ) -> LocalTuyaCreationReceipt:
        if _CONFIG_ENTRY_ID.fullmatch(config_entry_id) is None:
            raise ValueError("LocalTuya config entry ID is invalid")
        flow_id: str | None = None
        completed = False
        try:
            current = self._request_json(
                "POST",
                "/api/config/config_entries/options/flow",
                {"handler": config_entry_id},
            )
            flow_id = self._expect_form(current, "init")
            path = "/api/config/config_entries/options/flow/" + parse.quote(
                flow_id, safe=""
            )
            current = self._request_json("POST", path, {"action": "add_device"})
            self._expect_form(current, "add_device", expected_flow_id=flow_id)
            current = self._request_json("POST", path, {"selected_device": "..."})
            self._expect_form(current, "configure_device", expected_flow_id=flow_id)
            current = self._request_json(
                "POST",
                path,
                {
                    "device_id": configuration.device_id,
                    "enable_debug": False,
                    "friendly_name": configuration.friendly_name.strip(),
                    "host": configuration.host,
                    "local_key": configuration.local_key,
                    "protocol_version": configuration.protocol_version,
                },
            )
            self._expect_form(current, "pick_entity_type", expected_flow_id=flow_id)
            for entity in configuration.entities:
                current = self._request_json(
                    "POST", path, {"platform_to_add": entity.platform}
                )
                self._expect_form(current, "configure_entity", expected_flow_id=flow_id)
                current = self._request_json("POST", path, entity.values)
                self._expect_form(current, "pick_entity_type", expected_flow_id=flow_id)
            current = self._request_json(
                "POST",
                path,
                {
                    "no_additional_entities": True,
                    "platform_to_add": configuration.entities[-1].platform,
                },
            )
            if current.get("type") != "create_entry" or current.get("errors") not in (
                None,
                {},
            ):
                raise AdvancedTaskError("LocalTuya canary was not created")
            completed = True
            return LocalTuyaCreationReceipt(
                configuration_sha256=configuration.sha256,
                configured_entity_count=len(configuration.entities),
                disposition="created",
            )
        except error.HTTPError as exc:
            raise AdvancedTaskError(
                f"Home Assistant rejected LocalTuya canary ({exc.code})"
            ) from exc
        finally:
            if flow_id is not None and not completed:
                try:
                    self._request_json(
                        "DELETE",
                        "/api/config/config_entries/options/flow/"
                        + parse.quote(flow_id, safe=""),
                    )
                except Exception:
                    pass

    @staticmethod
    def _expect_form(
        value: dict[str, Any],
        step_id: str,
        *,
        expected_flow_id: str | None = None,
    ) -> str:
        if (
            value.get("type") != "form"
            or value.get("step_id") != step_id
            or not isinstance(value.get("flow_id"), str)
            or (
                expected_flow_id is not None
                and value.get("flow_id") != expected_flow_id
            )
        ):
            errors = value.get("errors")
            if isinstance(errors, dict) and errors:
                raise AdvancedTaskError("LocalTuya preflight returned a form error")
            raise AdvancedTaskError(f"LocalTuya preflight expected step {step_id}")
        return str(value["flow_id"])

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, Any]:
        body = None
        if payload is not None:
            validate_json_value(payload)
            body = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        request = urllib_request.Request(
            f"{self._base_url}{path}",
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        with urllib_request.urlopen(request, timeout=self._timeout) as response:
            raw = response.read(1_048_577)
        if len(raw) > 1_048_576:
            raise AdvancedTaskError("LocalTuya preflight response is too large")
        if not raw:
            return {}
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise AdvancedTaskError("LocalTuya preflight response is invalid")
        validate_json_value(value)
        return value


__all__ = [
    "HomeAssistantLocalTuyaAdapter",
    "LocalTuyaCanaryConfiguration",
    "LocalTuyaConnectivityProof",
    "LocalTuyaCreationReceipt",
    "LocalTuyaEntityConfiguration",
    "LocalTuyaProbeSecret",
]
