import asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import pytest

from eva.adapters.api.browser_session import BrowserSession, install_browser_session_routes
from eva.adapters.api.dashboard_access import DashboardAccess


ORIGIN = "https://eva.example"
AUTH = {"Authorization": "Bearer " + "t" * 32, "Origin": ORIGIN}
BASE = "/internal/v1/dashboard/browser"


class Backend:
    def __init__(self):
        self.mode = "stopped"
        self.revision = 0
        self.calls = []
        self.viewer = None

    async def status(self):
        return {"contract": "EvaBrowserSession/v1", "state": self.mode,
                "owner_control": self.mode == "owner", "revision": self.revision}

    async def control(self, action, revision):
        if action == "resume":
            assert self.viewer.closed, "MCP must not resume before upstream owner stream closes"
        self.calls.append(action)
        self.mode = {"start": "eva", "takeover": "owner", "resume": "eva", "stop": "stopped"}[action]
        self.revision += 1
        return await self.status()


def build(backend, token="t" * 32):
    app = FastAPI()
    install_browser_session_routes(app, access=DashboardAccess(token), session=BrowserSession(
        backend, ORIGIN, "http://127.0.0.1:12345", "u" * 32,
    ))
    return app


def test_owner_auth_scope_and_revision_are_checked_before_browser_contact():
    backend = Backend()
    with TestClient(build(backend)) as client:
        assert client.get(BASE + "/status").status_code == 401
        assert client.post(BASE + "/control", headers={**AUTH, "Origin": "https://attacker.example"},
                           json={"action": "start", "revision": 0}).status_code == 403
        for payload in ({"action": {}, "revision": 0}, {"action": "start", "revision": True},
                        {"action": "start", "revision": 0, "container": "other"}):
            assert client.post(BASE + "/control", headers=AUTH, json=payload).status_code == 422
        assert client.post(BASE + "/control?owner=other", headers=AUTH,
                           json={"action": "start", "revision": 0}).status_code == 422
        assert client.get(BASE + "/view/", headers=AUTH).status_code == 409
        assert backend.calls == []
        result = client.post(BASE + "/control", headers=AUTH, json={"action": "start", "revision": 0})
        assert result.json()["state"] == "eva"
        assert result.headers["cache-control"] == "no-store"
        assert client.post(BASE + "/control", headers=AUTH,
                           json={"action": "takeover", "revision": 0}).status_code == 409
    with TestClient(build(Backend(), token=None)) as client:
        assert client.get(BASE + "/status").status_code == 503


@pytest.mark.parametrize("backpressure", [False, True])
def test_return_to_eva_closes_existing_owner_stream_before_backend_resume(monkeypatch, backpressure):
    from threading import Event
    entered_send = Event()
    class Viewer:
        closed = False
        def __init__(self):
            self.messages = asyncio.Queue()
        def __aiter__(self):
            return self
        async def __anext__(self):
            return await self.messages.get()
        async def send(self, message):
            entered_send.set()
            if backpressure:
                await asyncio.Event().wait()
            await self.messages.put(message)
        async def close(self):
            self.closed = True

    backend = Backend()
    async def connect(url, **kwargs):
        assert url == "ws://127.0.0.1:12345" + BASE + "/view/websocket"
        assert kwargs["additional_headers"] == {"Authorization": "Bearer " + "u" * 32}
        assert kwargs["proxy"] is None
        backend.viewer = Viewer()
        return backend.viewer
    monkeypatch.setattr("eva.adapters.api.browser_session.FixedBrowserWebSocket", connect)
    with TestClient(build(backend)) as client:
        client.post(BASE + "/control", headers=AUTH, json={"action": "start", "revision": 0})
        client.post(BASE + "/control", headers=AUTH, json={"action": "takeover", "revision": 1})
        with client.websocket_connect(BASE + "/view/websocket", headers=AUTH) as socket:
            socket.send_text("owner-key")
            if backpressure:
                assert entered_send.wait(3)
            else:
                assert socket.receive_text() == "owner-key"
            result = client.post(BASE + "/control", headers=AUTH, json={"action": "resume", "revision": 2})
            assert result.status_code == 200
            with pytest.raises(WebSocketDisconnect):
                socket.receive_text()
        assert backend.calls == ["start", "takeover", "resume"]
        assert client.get(BASE + "/view/", headers=AUTH).status_code == 409


def test_fixed_viewer_rejects_real_redirect_before_second_origin_contact():
    from eva.adapters.api.browser_session import FixedBrowserWebSocket
    from websockets.exceptions import InvalidStatus

    async def exercise():
        contacted = []
        async def redirected(reader, writer):
            contacted.append("redirected")
            writer.close()
            await writer.wait_closed()
        destination = await asyncio.start_server(redirected, "127.0.0.1", 0)
        destination_port = destination.sockets[0].getsockname()[1]
        async def source(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(("HTTP/1.1 302 Found\r\nLocation: ws://127.0.0.1:" + str(destination_port)
                          + "/stolen\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").encode())
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        first = await asyncio.start_server(source, "127.0.0.1", 0)
        try:
            port = first.sockets[0].getsockname()[1]
            with pytest.raises(InvalidStatus):
                await FixedBrowserWebSocket("ws://127.0.0.1:" + str(port) + "/view", proxy=None)
            assert contacted == []
        finally:
            first.close()
            destination.close()
            await first.wait_closed()
            await destination.wait_closed()
    asyncio.run(exercise())
