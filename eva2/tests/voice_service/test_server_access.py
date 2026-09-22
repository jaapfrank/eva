import hashlib
import json
import pytest
from fastapi.testclient import TestClient
from eva.voice_service import server


@pytest.fixture
def voice_config(tmp_path, monkeypatch):
    def file(name, value):
        path = tmp_path / name
        path.write_text(value)
        return str(path)
    grant = json.dumps({"state": "enabled", "effective_at": "2020-01-01T00:00:00Z", "expires_at": "2099-01-01T00:00:00Z"})
    config = dict(owner_token_file=file("owner", "test-owner-secret"),
        ha_token_file=file("ha", "test-ha"), livekit_api_key="test-key",
        livekit_secret_file=file("lk", "test-secret"), core_url="http://localhost:1",
        ha_url="http://localhost:2", pipeline_id="test", stt_engine="test", language="nl",
        tts_engine="test", tts_language="nl-NL", authority_file=file("grant", grant),
        identity_file=file("identity", "test-identity"),
        authority_sha256=hashlib.sha256(grant.encode()).hexdigest(),
        identity_sha256=hashlib.sha256(b"test-identity").hexdigest(),
        origin="http://localhost:44716", client_js=file("client.js", "// pinned client"),
        livekit_url="http://localhost:3", public_livekit_url="ws://localhost:3")
    monkeypatch.setenv("EVA_VOICE_CONFIG", file("config", json.dumps(config)))
    monkeypatch.setattr(server.silero.VAD, "load", lambda **kwargs: None)
    async def ready(self):
        return True
    monkeypatch.setattr(server.CoreClient, "ready", ready)
    return config


def test_empty_landing_and_protected_owner_session(voice_config):
    config = voice_config
    auth = {"Authorization": "Bearer test-owner-secret"}
    with TestClient(server.create_app()) as client:
        landing = client.get("/")
        assert landing.status_code == 200
        assert 'id="login"' in landing.text
        assert "test-owner-secret" not in landing.text
        assert client.get("/livekit-client.js").status_code == 200
        for method, path in [("GET", "/session/ready"), ("POST", "/session"), ("POST", "/session/end"), ("POST", "/session/leased"), ("POST", "/session/end/unknown"), ("POST", "/session/renew/unknown")]:
            denied = client.request(method, path)
            assert denied.status_code == 401
            assert "www-authenticate" not in denied.headers
            assert client.request(method, path, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get("/session/ready", headers=auth).json() == {"ready": True}
        assert client.post("/session", headers={**auth, "Origin": "http://evil.invalid"}).status_code == 403
        assert client.post("/session/end", headers={**auth, "Origin": config["origin"]}).status_code == 200
        from pathlib import Path
        Path(config["authority_file"]).unlink()
        assert client.get("/session/ready", headers=auth).status_code == 403
        assert client.get("/").status_code == 200


def enable_local_access(voice_config):
    import os
    from pathlib import Path
    Path(os.environ["EVA_VOICE_CONFIG"]).write_text(json.dumps({**voice_config, "local_browser_access": True}))


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_local_browser_needs_no_copied_token_but_keeps_grant_and_origin(voice_config, host):
    from pathlib import Path
    enable_local_access(voice_config)
    origin = f"http://{host}:44716"
    # TestClient's transport cannot parse bracketed IPv6 URL authorities.
    # Supply the actual Host header to exercise admission for that alias too.
    with TestClient(server.create_app(), base_url="http://localhost:44716",
                    headers={"Host": f"{host}:44716"}, client=("127.0.0.1", 1234)) as client:
        page = client.get("/")
        assert "const localAccess=true;" in page.text
        assert "test-owner-secret" not in page.text
        assert client.get("/session/ready").json() == {"ready": True}
        assert client.post("/session/end").status_code == 403
        assert client.post("/session/end", headers={"Origin": origin}).status_code == 200
        Path(voice_config["authority_file"]).unlink()
        assert client.get("/session/ready").status_code == 403


@pytest.mark.parametrize("peer,host,headers", [
    ("192.168.1.10", "localhost:44716", {}),
    ("127.0.0.1", "evil.invalid:44716", {}),
    ("127.0.0.1", "localhost:44717", {}),
    ("127.0.0.1", "localhost:44716", {"Origin": "https://evil.invalid"}),
    ("127.0.0.1", "localhost:44716", {"Sec-Fetch-Site": "cross-site"}),
    ("127.0.0.1", "localhost:44716", {"X-Forwarded-For": "127.0.0.1"}),
    ("127.0.0.1", "localhost:44716", {"Forwarded": "for=127.0.0.1"}),
])
def test_local_access_does_not_open_remote_or_cross_origin_routes(voice_config, peer, host, headers):
    enable_local_access(voice_config)
    with TestClient(server.create_app(), base_url="http://" + host, client=(peer, 1234)) as client:
        assert client.get("/session/ready", headers=headers).status_code == 401
        assert client.post("/session", headers=headers).status_code == 401


def test_room_bound_close_and_phone_lease_expires_without_renewal(voice_config, monkeypatch):
    import time
    closed = []
    class Session:
        def __init__(self, **kwargs): pass
        def on(self, event): return lambda callback: callback
        async def start(self, *args, **kwargs): pass
        async def aclose(self): pass
    class Room:
        remote_participants = {"owner": object()}
        def on(self, event): return lambda callback: callback
        async def connect(self, *args, **kwargs): pass
        async def disconnect(self): pass
    class Control:
        def __init__(self, *args, **kwargs): self.room = self
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def delete_room(self, request): closed.append(request.room)
    monkeypatch.setattr(server, "AgentSession", Session)
    monkeypatch.setattr(server.rtc, "Room", Room)
    monkeypatch.setattr(server.api, "LiveKitAPI", Control)
    auth = {"Authorization":"Bearer test-owner-secret", "Origin":voice_config["origin"]}
    with TestClient(server.create_app()) as client:
        first = client.post("/session", headers=auth).json()["room"]
        assert client.post("/session/renew/"+first, headers=auth).status_code == 410
        assert first not in closed  # A normal laptop session needs no renewal.
        client.post("/session/end/"+first, headers=auth)
        second = client.post("/session/leased", headers=auth).json()["room"]
        client.post("/session/end/"+first, headers=auth)
        assert second not in closed  # A stale close must not affect the new room.
        assert client.post("/session/renew/"+second, headers=auth).status_code == 200
        deadline = time.monotonic()+13
        while second not in closed and time.monotonic()<deadline:
            time.sleep(.1)
        assert second in closed  # Connected owner still expires without renewal.
        assert client.post("/session/renew/"+second, headers=auth).status_code == 410


def test_relay_failure_does_not_create_a_room_or_leak_credentials(voice_config,monkeypatch):
    from pathlib import Path
    config_file=Path(__import__("os").environ["EVA_VOICE_CONFIG"])
    config_file.write_text(json.dumps({**voice_config,"cloud_relay":True}))
    async def failed(*args):raise ValueError("provider-private-token")
    monkeypatch.setattr(server,"home_assistant_ice_servers",failed)
    def forbidden():raise AssertionError("Room created despite unavailable relay")
    monkeypatch.setattr(server.rtc,"Room",forbidden)
    with TestClient(server.create_app()) as client:
        response=client.post("/session",headers={"Authorization":"Bearer test-owner-secret","Origin":voice_config["origin"]})
        assert response.status_code==503
        assert "provider-private-token" not in response.text


def test_private_return_lease_opens_after_media_setup(voice_config, monkeypatch):
    order = []
    elapsed = [0]
    opened = []

    class Session:
        def __init__(self, **kwargs): pass
        def on(self, event): return lambda callback: callback
        async def start(self, *args, **kwargs):
            elapsed[0] += 1
            order.append('session_started')
        async def aclose(self): order.append('session_closed')

    class Room:
        remote_participants = {'owner': object()}
        def on(self, event): return lambda callback: callback
        async def connect(self, *args, **kwargs):
            elapsed[0] += 16
            order.append('room_connected')
        async def disconnect(self): order.append('room_disconnected')

    class Control:
        def __init__(self, *args, **kwargs): self.room = self
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def delete_room(self, request): order.append('room_deleted')

    async def ready(self):
        self.private_work_returns = True
        return True

    async def work_return(self, operation, session_id, **identity):
        if operation == 'open':
            opened.append((session_id, elapsed[0]))
            order.append('return_open')
        if operation == 'close': order.append('return_close')
        return {'status': 'idle', 'result': None}

    monkeypatch.setattr(server, 'AgentSession', Session)
    monkeypatch.setattr(server.rtc, 'Room', Room)
    monkeypatch.setattr(server.api, 'LiveKitAPI', Control)
    monkeypatch.setattr(server.CoreClient, 'ready', ready)
    monkeypatch.setattr(server.CoreClient, 'work_return', work_return)
    auth = {'Authorization': 'Bearer test-owner-secret', 'Origin': voice_config['origin']}
    with TestClient(server.create_app()) as client:
        response = client.post('/session', headers=auth)
        assert response.status_code == 200
        assert order[:3] == ['room_connected', 'session_started', 'return_open']
        assert opened[0][1] == 17
        assert client.post('/session/end/' + response.json()['room'], headers=auth).status_code == 200
        assert order.index('return_close') > order.index('session_closed')
    assert order.count('return_open') == order.count('return_close') == 1
