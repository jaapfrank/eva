"""Observed HA transport coverage owned by the optional provider, never authority."""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from threading import RLock
import time
from urllib import request
from websockets.exceptions import WebSocketException

from eva.adapters.connectors.home_assistant_admin import HomeAssistantAdminAdapter
from eva.adapters.connectors.home_assistant_mcp import HomeAssistantAssistClient, HomeAssistantMcpError, _NoRedirect, _effect_schema_digest, _EFFECT_SCHEMA_SHA256, _locked, _remaining


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class HomeAssistantCapabilityProfile:
    """One lazy, short-lived observation for a fixed endpoint/account/Eva scope.

    No catalogue observation grants access. Existing audience, effect and durable
    execution guards still decide every action. Unknown effects are never retried.
    HA supplies no reliable catalogue-change push on this endpoint: freshness is
    bounded to 60 seconds, not described as instantaneous invalidation.
    """

    def __init__(self, settings, *, ttl_seconds=60):
        if not 1 <= ttl_seconds <= 60:
            raise ValueError("HA profile freshness must be between one and sixty seconds")
        self._settings = settings
        self._ttl = ttl_seconds
        self._lock = RLock()
        self._expires = 0.0
        self._snapshot = None
        self._client = None
        self._services = {}
        self._scope = _digest({"effects": settings.effects_enabled,
            "allowlist": settings.effect_allowlist,
            "principals": settings.effect_principal_sha256_allowlist,
            "events": settings.event_domains, "api": settings.api_enabled})

    def invalidate(self):
        with self._lock:
            self._expires = 0.0
            self._services = {}
            if self._client is not None:
                self._client.invalidate()

    def _read_config(self, timeout):
        opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
        req = request.Request(self._settings.endpoint_url.rstrip("/") + "/api/config",
            headers={"Authorization": "Bearer " + self._settings.credential})
        with opener.open(req, timeout=timeout) as response:
            raw = response.read(2_097_153)
            if response.status != 200 or len(raw) > 2_097_152:
                raise RuntimeError("HA capability configuration boundary failed")
        value = json.loads(raw)
        if not isinstance(value, dict) or not isinstance(value.get("version"), str):
            raise RuntimeError("HA capability configuration is invalid")
        return value

    def _read_websocket(self, deadline):
        admin = HomeAssistantAdminAdapter(base_url=self._settings.endpoint_url,
                                         token=self._settings.credential)
        with admin._session(deadline=deadline) as socket:
            config = admin._command(socket, 1, "get_config", deadline=deadline)
            account = admin._command(socket, 2, "auth/current_user", deadline=deadline)
            services = admin._command(socket, 3, "get_services", deadline=deadline)
            try:
                exposure = admin._command(socket, 4, "homeassistant/expose_entity/list", deadline=deadline)
            except RuntimeError:
                exposure = None
        if not isinstance(config, dict) or not isinstance(account, dict) or not isinstance(services, dict):
            raise RuntimeError("HA capability WebSocket discovery is invalid")
        return config, account, services, exposure

    def snapshot(self, *, timeout=5):
        deadline = time.monotonic() + timeout
        with _locked(self._lock, deadline):
            if self._snapshot is not None and time.monotonic() < self._expires:
                return json.loads(json.dumps(self._snapshot))
            # Discard old authority observations before any refresh can fail.
            self._snapshot = None
            self._services = {}
            self._expires = 0.0
            if self._client is not None:
                self._client.invalidate()
            self._client = None
            config = self._read_config(_remaining(deadline))
            version = config["version"]
            ws = {"available": False, "access": "unverified"}
            services = {}
            exposure = None
            try:
                ws_config, account, services, exposure = self._read_websocket(deadline)
                if ws_config.get("version") != version:
                    raise ValueError("HA version changed during capability discovery")
                ws = {"available": True, "access": "admin" if account.get("is_admin") else "account",
                      "account_binding_sha256": _digest(account.get("id")),
                      "services_schema_sha256": _digest(services)}
            except (OSError, RuntimeError, WebSocketException) as exc:
                ws["error_type"] = type(exc).__name__
            client = HomeAssistantAssistClient(base_url=self._settings.endpoint_url,
                token=self._settings.credential, server_version=version,
                scope_binding=_digest({"eva_scope": self._scope, "exposure": exposure,
                    "account": ws.get("account_binding_sha256"),
                    "services_schema": ws.get("services_schema_sha256")}),
                catalog_ttl_seconds=self._ttl)
            mcp = {"available": False, "access": "configured Assist exposure; no API fallback"}
            try:
                catalog = client.catalog(timeout=max(.001, deadline-time.monotonic()))
                if any(not isinstance(item, dict) or not isinstance(item.get("name"), str)
                       or not isinstance(item.get("inputSchema"), dict) for item in catalog["tools"]):
                    raise HomeAssistantMcpError("HA MCP tool metadata is malformed")
                mcp.update(available=True, catalog_sha256=catalog["catalog_sha256"],
                    tools=[{"name": item["name"], "schema_sha256": _digest(item.get("inputSchema"))}
                           for item in catalog["tools"]], protocol_version=catalog["protocol_version"])
                self._client = client
            except HomeAssistantMcpError as exc:
                mcp["error_type"] = type(exc).__name__
            from eva.adapters.connectors.home_assistant_api import HomeAssistantApiTool
            envelope = _digest(HomeAssistantApiTool.conversation_tools()[0].input_schema)
            admitted_effects = []
            if self._client is not None:
                for intent in ("HassTurnOn", "HassTurnOff"):
                    matches = [item for item in catalog["tools"] if item["name"] in {intent, "intent__" + intent}]
                    if len(matches) == 1 and _effect_schema_digest(matches[0]["inputSchema"]) == _EFFECT_SCHEMA_SHA256:
                        admitted_effects.append(intent)
            mcp["admitted_effect_tools"] = admitted_effects
            routes = {
                "on_off": {"transport": "mcp", "endpoint": "/api/mcp/assist", "available": len(admitted_effects) == 2,
                    "access": "existing exact effect/principal allowlists AND HA Assist exposure",
                    "schema": "pinned HassTurnOn/HassTurnOff", "verification": "exact MCP target plus existing independent state readback"},
                "states": {"transport": "rest", "endpoint": "/api/states/<entity_id>", "available": True,
                    "access": "existing bound read grant", "schema": "HA entity state; installed version bound",
                    "verification": "fresh entity-bound observation; MCP context is not an exact state receipt"},
                "changes": {"transport": "websocket", "endpoint": "/api/websocket", "available": ws["available"],
                    "access": "existing admitted event domains", "schema": "state_changed subscription",
                    "verification": "existing source revalidation"},
                "history": {"transport": "rest", "endpoint": "/api/history/period", "available": "recorder" in config.get("components", []),
                    "access": "existing bound read grant", "schema": "entity/time-filtered history",
                    "verification": "historical observation never presented as current"},
                "general_api": {"transport": "rest/websocket", "endpoint": "/api/", "available": self._settings.api_enabled,
                    "access": "existing authorized audience and durable execution grant; not Assist scope",
                    "schema": envelope, "verification": "persist response; independently read changes back"},
                "automation_management": {"transport": "rest/websocket", "endpoint": "/api/config/automation/config/<id>",
                    "available": self._settings.api_enabled and ws.get("access") == "admin" and "automation" in config.get("components", []),
                    "access": "existing authorized management route, never inferred from MCP absence",
                    "schema": "installed HA automation configuration plus live service schemas",
                    "verification": "existing pre-read, preserve unrelated fields, readback; no write during discovery"}}
            observed = {"version": version, "rest": {"available": True, "schema_sha256": envelope},
                "websocket": ws, "mcp": mcp, "routes": routes, "scope_sha256": self._scope,
                "exposure_sha256": _digest(exposure), "freshness_seconds": self._ttl,
                "automatic_transport_fallback": False}
            observed["observed_at"] = datetime.now(UTC).isoformat()
            observed["profile_sha256"] = _digest(observed)
            self._services = services if ws["available"] else {}
            self._snapshot = observed
            self._expires = time.monotonic() + self._ttl
            return json.loads(json.dumps(observed))

    def service_definition(self, domain, service, *, timeout=5):
        """Reuse bounded service metadata, never cached entity state or effects."""
        deadline = time.monotonic() + timeout
        with _locked(self._lock, deadline):
            profile = self.snapshot(timeout=_remaining(deadline))
            definition = self._services.get(domain, {}).get(service)
            return {"definition": json.loads(json.dumps(definition)),
                "observed_at": profile["observed_at"], "version": profile["version"],
                "profile_sha256": profile["profile_sha256"],
                "freshness_seconds": self._ttl}

    def require_transport(self, transport, *, timeout=5):
        profile = self.snapshot(timeout=timeout)
        if transport not in {"rest", "websocket"} or not profile[transport]["available"]:
            raise RuntimeError("Selected HA transport is unavailable; no alternative dispatched")
        return {"profile_sha256": profile["profile_sha256"], "version": profile["version"],
                "transport": transport, "automatic_transport_fallback": False}

    def effect_binding(self, timeout):
        deadline = time.monotonic() + timeout
        with _locked(self._lock, deadline):
            try:
                profile = self.snapshot(timeout=_remaining(deadline))
            except (OSError, RuntimeError, ValueError, WebSocketException) as exc:
                raise HomeAssistantMcpError("HA capability discovery failed before effect dispatch") from exc
            if self._client is None or not profile["mcp"]["available"]:
                raise HomeAssistantMcpError("Observed HA Assist MCP is unavailable; no API fallback")
            return self._client, profile["mcp"]["catalog_sha256"]
