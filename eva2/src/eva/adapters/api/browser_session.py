"""Owner controls and a revocable Selkies view on the existing cockpit surface.

The same route installer is used by the core (fixed remote client) and the
single-process browser host (local lifecycle backend). Selkies must use SUBFOLDER
/internal/v1/dashboard/browser/view/. Its HTTP/WebSocket ports stay private.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
import json
import os
from typing import Protocol
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, Response
from websockets.asyncio.client import connect

from eva.adapters.api.dashboard_access import DashboardAccess, validate_dashboard_access_token
from eva.adapters.api.installer_request_json import read_small_json_request
from eva.adapters.connectors.browser_session import (
    BROWSER_PATH, BrowserSessionClient, BrowserSessionConflict, BrowserSessionError,
    fixed_origin, read_browser_http,
)
from eva.adapters.credentials.runtime_files import read_runtime_secret_file


VIEW_PATH = BROWSER_PATH + "/view/"
_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer"}


class FixedBrowserWebSocket(connect):
    """A configured viewer is one origin; even same-origin redirects are refused."""

    def process_redirect(self, error):
        return error


class BrowserSessionBackend(Protocol):
    async def status(self) -> dict: ...
    async def control(self, action: str, revision: int) -> dict: ...


@dataclass(frozen=True)
class BrowserSession:
    backend: BrowserSessionBackend
    allowed_origin: str
    viewer_origin: str
    upstream_token: str | None = None
    upstream_basic_authorization: str | None = None
    upstream_websocket_token: str | None = None

    def __post_init__(self):
        fixed_origin(self.allowed_origin)
        fixed_origin(self.viewer_origin)
        if self.upstream_token is not None:
            validate_dashboard_access_token(self.upstream_token)
        if self.upstream_basic_authorization is not None:
            if (self.upstream_token is not None or not self.upstream_basic_authorization.startswith("Basic ")
                    or any(c in self.upstream_basic_authorization for c in "\r\n")):
                raise ValueError("invalid upstream browser authorization")
        if self.upstream_websocket_token is not None:
            validate_dashboard_access_token(self.upstream_websocket_token)


def browser_session_from_environment(environment=None) -> BrowserSession | None:
    """Bootstrap hook; an absent configuration leaves all browser routes disabled."""
    env = os.environ if environment is None else environment
    names = ("EVA_BROWSER_CONTROL_URL", "EVA_BROWSER_CONTROL_TOKEN_FILE", "EVA_BROWSER_OWNER_ORIGIN")
    values = [env.get(name, "") for name in names]
    if not any(values):
        return None
    if not all(values):
        raise ValueError("complete browser service configuration is required")
    origin, token_file, owner_origin = values
    token = validate_dashboard_access_token(read_runtime_secret_file(
        token_file, setting_name="EVA_BROWSER_CONTROL_TOKEN_FILE",
    ))
    return BrowserSession(BrowserSessionClient(origin, token), owner_origin, origin, token)


def install_browser_session_routes(app: FastAPI, *, access: DashboardAccess,
                                   session: BrowserSession | None) -> None:
    # One event loop owns this gate and its viewer connections. The host additionally
    # holds a lifetime flock, so a second worker cannot bypass connection revocation.
    gate = asyncio.Lock()
    views: dict[asyncio.Event, asyncio.Event] = {}

    def require(request, *, write=False):
        access.require(request)
        if access.token is None or session is None:
            raise HTTPException(503, "browser service is not configured")
        origin = request.headers.get("origin")
        if origin is not None and origin != session.allowed_origin:
            raise HTTPException(403, "browser origin is not admitted")
        # CLI-to-host calls use an explicit bearer credential; Basic browser writes
        # require Origin, preventing ambient-auth cross-site requests.
        if write and origin is None and not request.headers.get("authorization", "").startswith("Bearer "):
            raise HTTPException(403, "browser control requires an admitted origin")

    async def status():
        try:
            return await session.backend.status()
        except BrowserSessionError as error:
            raise HTTPException(503, "browser status unavailable") from error

    async def require_owner():
        value = await status()
        if not value.get("owner_control"):
            raise HTTPException(409, "take over the browser before opening its controls")

    async def close_views():
        active = tuple(views.items())
        for stop, _closed in active:
            stop.set()
        if active:
            await asyncio.gather(*(closed.wait() for _stop, closed in active))

    @app.get(BROWSER_PATH + "/status")
    async def browser_status(request: Request):
        require(request)
        if request.query_params:
            raise HTTPException(422, "browser scope is server-bound")
        async with gate:
            return JSONResponse(await status(), headers=_HEADERS)

    @app.post(BROWSER_PATH + "/control")
    async def browser_control(request: Request):
        require(request, write=True)
        payload = await read_small_json_request(request, 256, "browser control")
        if (request.query_params or not isinstance(payload, dict)
                or set(payload) != {"action", "revision"}
                or not isinstance(payload["action"], str)
                or payload["action"] not in {"start", "takeover", "resume", "stop"}
                or type(payload["revision"]) is not int or payload["revision"] < 0):
            raise HTTPException(422, "invalid browser control")
        async with gate:
            current = await status()
            if payload["revision"] != current["revision"]:
                raise HTTPException(409, "browser state changed; refresh status")
            if payload["action"] in {"resume", "stop"}:
                # Await closing upstream streams, not merely a UI flag or a queued
                # disconnect. No owner input can overlap a subsequent MCP start.
                await close_views()
            try:
                result = await session.backend.control(payload["action"], payload["revision"])
            except BrowserSessionConflict as error:
                raise HTTPException(409, "browser state changed; refresh status") from error
            except BrowserSessionError as error:
                raise HTTPException(503, "browser control incomplete; read current status") from error
            return JSONResponse(result, headers=_HEADERS)

    @app.get(VIEW_PATH + "{path:path}")
    async def browser_view(request: Request, path: str):
        require(request)
        if (any(part in {".", ".."} for part in path.split("/")) or "\\" in path
                or request.query_params or len(path) > 2048):
            raise HTTPException(422, "invalid browser view path")
        async with gate:
            await require_owner()
            # GET assets only: no arbitrary proxy methods, URLs, owner credentials,
            # cookies, redirects or file-upload endpoint are forwarded.
            try:
                code, headers, content = await asyncio.to_thread(
                    read_browser_http, session.viewer_origin + VIEW_PATH + quote(path, safe="/"),
                    token=session.upstream_token, maximum=32 * 1024 * 1024, timeout=10,
                    authorization=session.upstream_basic_authorization,
                )
            except BrowserSessionError as error:
                raise HTTPException(502, "browser view unavailable") from error
        if not 200 <= code < 300:
            raise HTTPException(502, "browser view unavailable")
        headers = {name.lower(): value for name, value in headers.items()}
        # Selkies workers/codecs may need cross-origin isolation. Preserve those
        # response policies, never upstream cookies, redirects or credentials.
        policies = {name: headers[name] for name in (
            "cross-origin-opener-policy", "cross-origin-embedder-policy",
            "cross-origin-resource-policy", "permissions-policy", "content-encoding",
        ) if name in headers}
        return Response(content, status_code=code, headers={
            **_HEADERS, **policies, "Content-Type": headers.get("content-type", "application/octet-stream"),
            "Content-Security-Policy": "frame-ancestors 'self'; base-uri 'self'",
        })

    @app.websocket(VIEW_PATH + "websocket")
    async def browser_socket(socket: WebSocket):
        upstream = None
        pumps = []
        stop, closed = asyncio.Event(), asyncio.Event()
        try:
            require(socket)
            if socket.query_params or socket.headers.get("origin") != session.allowed_origin:
                raise HTTPException(403, "browser origin is not admitted")
            async with gate:
                await require_owner()
                url = session.viewer_origin.replace("http", "ws", 1) + VIEW_PATH + "websocket"
                auth = None if session.upstream_token is None else {
                    "Authorization": "Bearer " + session.upstream_token,
                }
                if session.upstream_basic_authorization is not None:
                    auth = {"Authorization": session.upstream_basic_authorization}
                if session.upstream_websocket_token is not None:
                    auth = {**(auth or {}), "X-Eva-Browser-Session": session.upstream_websocket_token}
                upstream = await FixedBrowserWebSocket(url, origin=session.allowed_origin,
                                         additional_headers=auth, proxy=None,
                                         open_timeout=10, close_timeout=3,
                                         max_size=16 * 1024 * 1024, max_queue=4)
                await socket.accept()
                views[stop] = closed

            async def from_owner():
                while True:
                    message = await socket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    data = message.get("bytes")
                    if data is None:
                        data = message.get("text", "")
                    if len(data) > 1024 * 1024:
                        return
                    async with gate:
                        # A peer that stops reading must not hold the owner-control
                        # gate indefinitely, preventing resume/stop from closing it.
                        await asyncio.wait_for(upstream.send(data), timeout=2)

            async def to_owner():
                async for data in upstream:
                    if isinstance(data, bytes):
                        await socket.send_bytes(data)
                    else:
                        # Selkies includes its private master-token setting in
                        # server_settings. It is an upstream credential, never
                        # part of the owner's browser UI or either proxy hop.
                        if data.startswith("{"):
                            with suppress(ValueError, TypeError):
                                decoded = json.loads(data)
                                if decoded.get("type") == "server_settings" and isinstance(decoded.get("settings"), dict):
                                    decoded["settings"].pop("master_token", None)
                                    decoded["settings"].pop("control_port", None)
                                    data = json.dumps(decoded)
                        await socket.send_text(data)

            async def watch_owner():
                while True:
                    await asyncio.sleep(1)
                    async with gate:
                        await require_owner()

            pumps = [asyncio.create_task(call()) for call in (from_owner, to_owner, watch_owner)]
            pumps.append(asyncio.create_task(stop.wait()))
            await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        except (Exception, asyncio.CancelledError):
            pass  # Never return upstream text, credentials or desktop content as errors.
        finally:
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            if upstream is not None:
                with suppress(Exception):
                    await asyncio.wait_for(upstream.close(), timeout=4)
                # Closing must be an actual transport boundary even if a peer
                # never answers the WebSocket close handshake.
                transport = getattr(upstream, "transport", None)
                if transport is not None:
                    transport.abort()
            with suppress(Exception):
                await asyncio.wait_for(socket.close(code=1000 if upstream is not None else 1008), timeout=2)
            views.pop(stop, None)
            closed.set()
