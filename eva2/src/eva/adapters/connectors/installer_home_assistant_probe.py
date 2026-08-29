"""Read-only Home Assistant root probe for the local installer verifier."""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
from urllib.parse import urlsplit

from eva.operations.installer_provider_verification import (
    InstallerProviderProbeError,
    InstallerProviderProbeObservation,
)


_HOSTNAME_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
)
_MAX_RESPONSE_BYTES = 16_384
_MIN_TOKEN_BYTES = 16
_MAX_TOKEN_BYTES = 8_192


def _local_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise InstallerProviderProbeError("unreachable") from exc
    if (
        address.is_unspecified
        or address.is_multicast
        or address.is_reserved
        or not (address.is_private or address.is_loopback or address.is_link_local)
    ):
        raise InstallerProviderProbeError("unreachable")
    return address


class HomeAssistantInstallerReadOnlyProbe:
    """Verify one local Home Assistant token without reading entity data."""

    def __init__(self, *, base_url: str) -> None:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname
        if (
            parsed.scheme not in {"http", "https"}
            or hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or "%" in hostname
        ):
            raise ValueError("Home Assistant verification origin is invalid")
        checked_hostname = hostname.casefold()
        try:
            literal = ipaddress.ip_address(checked_hostname)
        except ValueError:
            literal = None
            if _HOSTNAME_RE.fullmatch(checked_hostname) is None:
                raise ValueError(
                    "Home Assistant verification hostname is invalid"
                ) from None
        else:
            try:
                _local_address(str(literal))
            except InstallerProviderProbeError as exc:
                raise ValueError(
                    "Home Assistant verification address must be local"
                ) from exc
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Home Assistant verification port is invalid") from exc
        self._scheme = parsed.scheme
        self._hostname = checked_hostname
        self._port = port if port is not None else (443 if parsed.scheme == "https" else 8123)

    def _resolve(self) -> tuple[str, int]:
        try:
            rows = socket.getaddrinfo(
                self._hostname,
                self._port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except OSError:
            raise InstallerProviderProbeError("unreachable") from None
        addresses: list[str] = []
        for row in rows:
            address = row[4][0]
            checked = _local_address(address)
            normalized = str(checked)
            if normalized not in addresses:
                addresses.append(normalized)
        if not addresses:
            raise InstallerProviderProbeError("unreachable")
        return addresses[0], self._port

    def read_only_probe(
        self, *, secret: memoryview, timeout_seconds: float
    ) -> InstallerProviderProbeObservation:
        raw_token = bytes(secret)
        if not _MIN_TOKEN_BYTES <= len(raw_token) <= _MAX_TOKEN_BYTES or any(
            value <= 0x20 or value == 0x7F for value in raw_token
        ):
            raise InstallerProviderProbeError("unauthorized")
        try:
            token = raw_token.decode("ascii", errors="strict")
        except UnicodeDecodeError:
            raise InstallerProviderProbeError("unauthorized") from None
        address = self._resolve()
        connection = http.client.HTTPConnection(
            self._hostname,
            port=self._port,
            timeout=timeout_seconds,
        )
        connected: socket.socket | None = None
        try:
            connected = socket.create_connection(address, timeout=timeout_seconds)
            if self._scheme == "https":
                connected = ssl.create_default_context().wrap_socket(
                    connected,
                    server_hostname=self._hostname,
                )
            connection.sock = connected
            connected = None
            connection.request(
                "GET",
                "/api/",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "Eva2-Installer-Verification/1",
                },
            )
            response = connection.getresponse()
            status = response.status
            content_type = response.getheader("Content-Type", "")
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except (TimeoutError, socket.timeout):
            raise InstallerProviderProbeError("timeout") from None
        except (OSError, ssl.SSLError, http.client.HTTPException):
            raise InstallerProviderProbeError("unreachable") from None
        finally:
            if connected is not None:
                connected.close()
            connection.close()
        if status in {401, 403}:
            raise InstallerProviderProbeError("unauthorized")
        if status != 200:
            raise InstallerProviderProbeError("unreachable")
        if len(raw) > _MAX_RESPONSE_BYTES or not content_type.casefold().startswith(
            "application/json"
        ):
            raise InstallerProviderProbeError("invalid_response")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise InstallerProviderProbeError("invalid_response") from None
        if not isinstance(payload, dict) or payload.get("message") != "API running.":
            raise InstallerProviderProbeError("invalid_response")
        return InstallerProviderProbeObservation(
            check_id="home_assistant.api.read",
            outcome="ready",
            reason_code="verified",
        )


__all__ = ["HomeAssistantInstallerReadOnlyProbe"]
