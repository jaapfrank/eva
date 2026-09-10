import hashlib
import json
from fastapi.testclient import TestClient
from eva.voice_service import server


def test_empty_landing_and_protected_owner_session(tmp_path, monkeypatch):
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
    auth = {"Authorization": "Bearer test-owner-secret"}
    with TestClient(server.create_app()) as client:
        landing = client.get("/")
        assert landing.status_code == 200
        assert 'id="login"' in landing.text
        assert "test-owner-secret" not in landing.text
        assert client.get("/livekit-client.js").status_code == 200
        for method, path in [("GET", "/session/ready"), ("POST", "/session"), ("POST", "/session/end")]:
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
