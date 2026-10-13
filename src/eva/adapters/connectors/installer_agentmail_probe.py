"""Read-only AgentMail inbox probe for the local installer verifier."""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
from urllib.parse import quote, urlsplit

from eva.application.installer import (
    InstallerProviderProbeError,
    InstallerProviderProbeObservation,
)


_INBOX_ID_RE = re.compile(r"[A-Za-z0-9@._:+-]{3,240}")
_MAX_RESPONSE_BYTES = 16_384
_MIN_KEY_BYTES = 16
_MAX_KEY_BYTES = 8_192


class AgentMailInstallerReadOnlyProbe:
    """Verify one fixed inbox without reading messages or changing mail state."""

    def __init__(
        self,
        *,
        inbox_id: str,
        api_base_url: str = "https://api.agentmail.to/v0",
        allow_insecure_loopback_test: bool = False,
    ) -> None:
        if _INBOX_ID_RE.fullmatch(inbox_id) is None:
            raise ValueError("AgentMail verification inbox ID is invalid")
        parsed = urlsplit(api_base_url)
        if (
            parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"/v0", "/v0/"}
        ):
            raise ValueError("AgentMail verification origin is invalid")
        if parsed.scheme == "https":
            if (
                parsed.hostname.casefold() != "api.agentmail.to"
                or parsed.port not in {None, 443}
            ):
                raise ValueError("AgentMail verification origin is not official")
        elif parsed.scheme == "http" and allow_insecure_loopback_test:
            try:
                if not ipaddress.ip_address(parsed.hostname).is_loopback:
                    raise ValueError
            except ValueError as exc:
                raise ValueError(
                    "AgentMail test verification origin must be loopback"
                ) from exc
        else:
            raise ValueError("AgentMail verification origin must use HTTPS")
        self._inbox_id = inbox_id
        self._scheme = parsed.scheme
        self._host = parsed.hostname
        self._port = parsed.port

    def read_only_probe(
        self, *, secret: memoryview, timeout_seconds: float
    ) -> InstallerProviderProbeObservation:
        raw_key = bytes(secret)
        if not _MIN_KEY_BYTES <= len(raw_key) <= _MAX_KEY_BYTES or any(
            value <= 0x20 or value == 0x7F for value in raw_key
        ):
            raise InstallerProviderProbeError("unauthorized")
        try:
            api_key = raw_key.decode("ascii", errors="strict")
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
                "GET",
                "/v0/inboxes/" + quote(self._inbox_id, safe="@._+-"),
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {api_key}",
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
        if not isinstance(payload, dict):
            raise InstallerProviderProbeError("invalid_response")
        returned_inbox = payload.get("inbox_id")
        email = payload.get("email")
        pod_id = payload.get("pod_id")
        if (
            returned_inbox != self._inbox_id
            or not isinstance(email, str)
            or not 3 <= len(email) <= 320
            or "@" not in email
            or not isinstance(pod_id, str)
            or not 1 <= len(pod_id) <= 240
        ):
            raise InstallerProviderProbeError("invalid_response")
        return InstallerProviderProbeObservation(
            check_id="email.identity.read",
            outcome="ready",
            reason_code="verified",
        )


__all__ = ["AgentMailInstallerReadOnlyProbe"]
