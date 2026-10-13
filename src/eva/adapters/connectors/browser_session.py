"""Fixed browser-management client; no browser tools or Docker authority."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


BROWSER_PATH = "/internal/v1/dashboard/browser"


class BrowserSessionError(RuntimeError):
    pass


class BrowserSessionConflict(BrowserSessionError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def fixed_origin(value: str) -> str:
    """Deployment chooses an origin; requests never choose a destination."""
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment
            or any(c.isspace() or ord(c) < 32 for c in value)):
        raise ValueError("browser origin must be an exact HTTP(S) origin")
    _ = parsed.port
    return value


def read_browser_http(url: str, *, token: str | None = None,
                      authorization: str | None = None,
                      payload: dict | None = None, maximum: int = 65536,
                      timeout: float = 120) -> tuple[int, dict[str, str], bytes]:
    headers = {"Accept-Encoding": "identity"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    if authorization is not None:
        if token is not None:
            raise ValueError("one browser authorization source is required")
        headers["Authorization"] = authorization
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=None if payload is None else json.dumps(payload).encode(),
                      headers=headers)
    try:
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        try:
            response = opener.open(request, timeout=timeout)
        except HTTPError as error:
            response = error
        with response:
            content = response.read(maximum + 1)
            if len(content) > maximum:
                raise BrowserSessionError("browser response exceeds its bound")
            return response.status, dict(response.headers.items()), content
    except (OSError, ValueError) as error:
        raise BrowserSessionError("browser service unavailable") from error


@dataclass(frozen=True)
class BrowserSessionClient:
    origin: str
    token: str

    def __post_init__(self):
        fixed_origin(self.origin)

    def _request_sync(self, payload=None, *, timeout=120):
        status, _headers, content = read_browser_http(
            self.origin + BROWSER_PATH +
            ("/control" if payload is not None else "/status"),
            token=self.token, payload=payload, timeout=timeout,
        )
        if status == 409:
            raise BrowserSessionConflict("browser state changed; refresh status")
        if status != 200:
            raise BrowserSessionError("browser service unavailable")
        try:
            result = json.loads(content)
            if not isinstance(result, dict) or result.get("contract") != "EvaBrowserSession/v1":
                raise ValueError()
            return result
        except (ValueError, TypeError) as error:
            raise BrowserSessionError("invalid browser service response") from error

    def status_snapshot(self, *, timeout=1):
        """Bounded read for a synchronous worker; never changes browser control."""
        return self._request_sync(timeout=timeout)

    async def status(self):
        return await asyncio.to_thread(self._request_sync)

    async def control(self, action: str, revision: int):
        return await asyncio.to_thread(self._request_sync, {"action": action, "revision": revision})
