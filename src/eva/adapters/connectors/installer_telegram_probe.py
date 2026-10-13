"""Read-only Telegram ``getMe`` probe for the local installer verifier."""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
from urllib.parse import urlsplit

from eva.application.installer import (
    InstallerProviderProbeError,
    InstallerProviderProbeObservation,
)


_TOKEN_RE = re.compile(rb"[0-9]{6,12}:[A-Za-z0-9_-]{30,64}")
_MAX_RESPONSE_BYTES = 16_384


class TelegramInstallerReadOnlyProbe:
    """Verify one bot credential without reading updates or sending messages."""

    def __init__(
        self,
        *,
        api_base_url: str = "https://api.telegram.org",
        allow_insecure_loopback_test: bool = False,
    ) -> None:
        parsed = urlsplit(api_base_url)
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or parsed.hostname is None
        ):
            raise ValueError("Telegram verification origin is invalid")
        if parsed.scheme == "https":
            if (
                parsed.hostname.casefold() != "api.telegram.org"
                or parsed.port not in {None, 443}
            ):
                raise ValueError("Telegram verification origin is not official")
        elif parsed.scheme == "http" and allow_insecure_loopback_test:
            try:
                if not ipaddress.ip_address(parsed.hostname).is_loopback:
                    raise ValueError
            except ValueError as exc:
                raise ValueError(
                    "Telegram test verification origin must be loopback"
                ) from exc
        else:
            raise ValueError("Telegram verification origin must use HTTPS")
        self._scheme = parsed.scheme
        self._host = parsed.hostname
        self._port = parsed.port

    def read_only_probe(
        self, *, secret: memoryview, timeout_seconds: float
    ) -> InstallerProviderProbeObservation:
        token = bytes(secret)
        if _TOKEN_RE.fullmatch(token) is None:
            raise InstallerProviderProbeError("unauthorized")
        try:
            token_text = token.decode("ascii", errors="strict")
        except UnicodeDecodeError:
            raise InstallerProviderProbeError("unauthorized") from None
        connection_type = (
            http.client.HTTPSConnection
            if self._scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_type(
            self._host,
            port=self._port,
            timeout=timeout_seconds,
        )
        try:
            connection.request(
                "POST",
                f"/bot{token_text}/getMe",
                body=b"{}",
                headers={
                    "Accept": "application/json",
                    "Content-Length": "2",
                    "Content-Type": "application/json",
                    "User-Agent": "Eva2-Installer-Verification/1",
                },
            )
            response = connection.getresponse()
            status = response.status
            content_type = response.getheader("Content-Type", "")
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except TimeoutError:
            raise InstallerProviderProbeError("timeout") from None
        except (OSError, http.client.HTTPException):
            raise InstallerProviderProbeError("unreachable") from None
        finally:
            connection.close()
        if status in {401, 403, 404}:
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
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            if isinstance(payload, dict) and payload.get("error_code") in {401, 403}:
                raise InstallerProviderProbeError("unauthorized")
            raise InstallerProviderProbeError("invalid_response")
        result = payload.get("result")
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("id"), int)
            or isinstance(result.get("id"), bool)
            or result["id"] <= 0
            or result.get("is_bot") is not True
            or not isinstance(result.get("username"), str)
            or not result["username"].strip()
            or len(result["username"]) > 64
        ):
            raise InstallerProviderProbeError("invalid_response")
        return InstallerProviderProbeObservation(
            check_id="telegram.get_me",
            outcome="ready",
            reason_code="verified",
        )


__all__ = ["TelegramInstallerReadOnlyProbe"]
