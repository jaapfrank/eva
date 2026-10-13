"""ContextForge registration and discovery; authority belongs to the caller.

This adapter neither infers permissions from discovery hints nor executes tools.
Registration names are deterministic so an interrupted request can read back its
own registration before attempting another creation.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any
from urllib import error, parse, request

from eva.application.cancellation import check_cancellation


class McpRegistrationError(RuntimeError):
    pass


class McpRegistrationAbsent(McpRegistrationError):
    """A complete authorized catalog read found no matching registration."""


@dataclass(frozen=True, slots=True)
class McpRegistrationConfig:
    base_url: str
    registration_token: str = field(repr=False)
    allow_plain_http: bool = False
    timeout_seconds: int = 30
    maximum_response_bytes: int = 2 * 1024 * 1024
    visibility: str = "private"
    exclusive_owner_gateway: bool = False

    def __post_init__(self) -> None:
        url = parse.urlsplit(self.base_url)
        if (url.scheme not in {"http", "https"} or not url.hostname
                or url.username or url.password or url.query or url.fragment
                or url.path.rstrip("/")
                or (url.scheme == "http" and not self.allow_plain_http)
                or not self.registration_token or len(self.registration_token) > 8192
                or not 2 <= self.timeout_seconds <= 60
                or not 65536 <= self.maximum_response_bytes <= 4 * 1024 * 1024
                or self.visibility not in {"private", "public"}
                or (self.visibility == "public" and not self.exclusive_owner_gateway)):
            raise ValueError("MCP registration configuration is invalid")


def canonical_mcp_endpoint(raw: str) -> str:
    """Normalize syntax only; a syntactically valid URL is not authorization."""
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 2048:
        raise ValueError("MCP endpoint is invalid")
    url = parse.urlsplit(raw)
    if (url.scheme not in {"http", "https"} or not url.hostname
            or url.username is not None or url.password is not None or url.query or url.fragment
            or any(ord(c) < 33 for c in raw)):
        raise ValueError("MCP endpoint is invalid")
    try:
        port = url.port
    except ValueError as exc:
        raise ValueError("MCP endpoint port is invalid") from exc
    host = url.hostname.lower()
    host = f"[{host}]" if ":" in host else host
    authority = host if port is None else f"{host}:{port}"
    return parse.urlunsplit((url.scheme.lower(), authority, url.path or "/", url.query, ""))


def registration_name(owner_key: str, endpoint: str) -> str:
    if not isinstance(owner_key, str) or not owner_key.strip():
        raise ValueError("MCP registration owner is required")
    binding = owner_key + "\0" + canonical_mcp_endpoint(endpoint)
    return "eva_connector_" + hashlib.sha256(binding.encode()).hexdigest()[:32]


class _NoRedirects(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise McpRegistrationError("MCP control plane redirect refused")


class ContextForgeRegistrationClient:
    def __init__(self, config: McpRegistrationConfig) -> None:
        self.config = config
        self._opener = request.build_opener(_NoRedirects())

    def _request(self, method: str, path: str, payload: dict | None = None) -> Any:
        check_cancellation()
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        req = request.Request(self.config.base_url.rstrip("/") + path, data=body,
            method=method, headers={"Authorization": "Bearer " + self.config.registration_token,
                                   "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self._opener.open(req, timeout=self.config.timeout_seconds) as response:
                raw = response.read(self.config.maximum_response_bytes + 1)
        except error.HTTPError as exc:
            # Error bodies can contain upstream credentials or reflected inputs.
            raise McpRegistrationError(f"MCP control plane returned HTTP {exc.code}") from None
        except (error.URLError, TimeoutError, OSError) as exc:
            raise McpRegistrationError("MCP control plane is unavailable") from exc
        check_cancellation()
        if len(raw) > self.config.maximum_response_bytes:
            raise McpRegistrationError("MCP discovery response exceeds its bound")
        try:
            return json.loads(raw)
        except (UnicodeError, ValueError) as exc:
            raise McpRegistrationError("MCP control plane response is invalid") from exc

    @staticmethod
    def _rows(value: Any) -> list[dict]:
        # ContextForge's paginated and non-paginated forms share these records.
        if isinstance(value, dict):
            value = value.get("gateways", value.get("tools", value.get("data")))
        if not isinstance(value, list) or len(value) > 1024 or any(not isinstance(x, dict) for x in value):
            raise McpRegistrationError("MCP discovery catalog is invalid")
        return value

    @staticmethod
    def _validate_gateway(row: dict, *, name: str, endpoint: str) -> dict:
        if (row.get("name") != name or not isinstance(row.get("id"), str)
                or not row["id"] or canonical_mcp_endpoint(row.get("url", "")) != endpoint
                or row.get("transport") != "STREAMABLEHTTP"):
            raise McpRegistrationError("MCP registration identity differs")
        return row

    def find(self, *, owner_key: str, endpoint: str) -> dict | None:
        endpoint = canonical_mcp_endpoint(endpoint)
        name = registration_name(owner_key, endpoint)
        rows = self._rows(self._request("GET", "/gateways?include_pagination=false&limit=0"))
        matches = [row for row in rows if row.get("name") == name]
        if len(matches) > 1:
            raise McpRegistrationError("MCP registration name is ambiguous")
        return None if not matches else self._validate_gateway(matches[0], name=name, endpoint=endpoint)

    def register(self, *, owner_key: str, endpoint: str) -> dict:
        """Register after caller authorization; reuse only an exact own binding."""
        endpoint = canonical_mcp_endpoint(endpoint)
        existing = self.find(owner_key=owner_key, endpoint=endpoint)
        if existing is not None:
            return existing
        name = registration_name(owner_key, endpoint)
        try:
            value = self._request("POST", "/gateways", {
                "name": name, "url": endpoint, "transport": "STREAMABLEHTTP",
                "gateway_mode": "cache", "visibility": self.config.visibility,
            })
        except McpRegistrationError:
            # The POST may have reached the server before its response was lost.
            # Observe once; never blindly repeat a registration POST.
            observed = self.find(owner_key=owner_key, endpoint=endpoint)
            if observed is not None:
                return observed
            raise
        if not isinstance(value, dict):
            raise McpRegistrationError("MCP registration response is invalid")
        return self._validate_gateway(value, name=name, endpoint=endpoint)

    def discover(self, *, owner_key: str, endpoint: str) -> tuple[dict, list[dict]]:
        gateway = self.find(owner_key=owner_key, endpoint=endpoint)
        if gateway is None:
            raise McpRegistrationAbsent("MCP service is not registered")
        gateway_id = gateway["id"]
        query = parse.urlencode({"gateway_id": gateway_id, "include_pagination": "false", "limit": 0})
        rows = self._rows(self._request("GET", "/tools?" + query))
        # Filter even when an older gateway ignores the server-side selector.
        rows = [row for row in rows if row.get("gatewayId", row.get("gateway_id")) == gateway_id]
        names = [row.get("name") for row in rows]
        if not rows or any(not isinstance(name, str) or not name for name in names) or len(names) != len(set(names)):
            raise McpRegistrationError("MCP service tools are absent or ambiguous")
        return gateway, rows
