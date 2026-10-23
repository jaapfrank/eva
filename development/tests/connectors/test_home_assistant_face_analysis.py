"""Existing camera reads can reach configured local face analysis without replay."""
import asyncio
import hashlib
import json
from types import SimpleNamespace
import httpx2 as httpx
import pytest

from eva.adapters.connectors.home_assistant_api import HomeAssistantApiTool
from eva.application.conversation import ConversationStopped
from tests.connectors.test_home_assistant_vision import arguments, install_transport


class Faces:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def analyze(self, snapshot):
        self.calls.append(snapshot.sha256)
        if self.error: raise self.error
        return {"image_sha256": snapshot.sha256, "faces": [],
                "evidence_kind": "face_similarity_candidate_not_authentication"}


def test_face_only_camera_read_and_observation_do_not_replay(monkeypatch):
    calls = install_transport(monkeypatch)
    faces = Faces()
    tool = HomeAssistantApiTool(base_url="http://ha", token="private", faces=faces)
    request = SimpleNamespace(arguments=arguments())
    result = asyncio.run(tool.execute(request))
    observed = asyncio.run(tool.observe(request, result)).evidence["observation"]
    value = json.loads(observed["response_json"])
    assert observed["success"] is True
    assert observed["external_state_verified"] is False
    assert value["recognition_available"] is True and value["scene_available"] is False
    assert value["recognition"]["faces"] == []
    assert value["identity_is_not_authentication"] is True
    assert faces.calls == [value["image_sha256"]]
    assert len(calls) == 1
    assert "private" not in json.dumps(observed)


def test_unreachable_scene_does_not_hide_available_face_result(monkeypatch):
    install_transport(monkeypatch)
    class Offline:
        def analyze(self, snapshot): raise httpx.ConnectError("private endpoint")
    tool = HomeAssistantApiTool(base_url="http://ha",token="private",vision=Offline(),faces=Faces())
    result = tool._request(arguments())
    value = json.loads(result["response_json"])
    assert result["success"] is True
    assert value["scene_error"] == "local_vision_unreachable"
    assert value["recognition_available"] is True
    assert "private" not in json.dumps(value)


def test_unreachable_face_does_not_claim_recognition(monkeypatch):
    install_transport(monkeypatch)
    tool = HomeAssistantApiTool(base_url="http://ha",token="private",faces=Faces(httpx.ConnectError("secret")))
    result = tool._request(arguments())
    assert result["success"] is False
    value = json.loads(result["response_json"])
    assert value["recognition_available"] is False
    assert value["recognition_error"] == "local_face_unreachable"
    assert "recognition" not in value


def test_stop_is_checked_before_face_dispatch(monkeypatch):
    install_transport(monkeypatch)
    faces = Faces()
    count = 0
    def check():
        nonlocal count
        count += 1
        if count == 2: raise ConversationStopped("stop")
    monkeypatch.setattr("eva.adapters.postgres.connector_results.check_capture_authority", check)
    with pytest.raises(ConversationStopped):
        HomeAssistantApiTool(base_url="http://ha",token="private",faces=faces)._request(arguments())
    assert not faces.calls


def test_provider_binds_existing_local_face_settings_and_hides_secret(monkeypatch, tmp_path):
    from eva.providers.home_assistant import HomeAssistantProviderFactory
    for key in ("EVA_LOCAL_VISION_URL", "EVA_LOCAL_VISION_MODEL", "EVA_LOCAL_VISION_REVISION", "EVA_LOCAL_FACE_TOKEN", "EVA_HOME_ASSISTANT_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    for name, secret in [("ha", "ha-private"), ("face", "f" * 40)]:
        p=tmp_path/name;p.write_text(secret);p.chmod(0o600)
    monkeypatch.setenv("EVA_HOME_ASSISTANT_URL", "http://ha")
    monkeypatch.setenv("EVA_HOME_ASSISTANT_TOKEN_FILE", str(tmp_path/"ha"))
    monkeypatch.setenv("EVA_LOCAL_FACE_URL", "http://127.0.0.1:44741")
    monkeypatch.setenv("EVA_LOCAL_FACE_TOKEN_FILE", str(tmp_path/"face"))
    monkeypatch.setenv("EVA_LOCAL_FACE_REVISION", "revision")
    settings=HomeAssistantProviderFactory.load_settings()
    assert settings.face_revision == "revision"
    assert settings.face_credential == "f" * 40
    assert "f" * 40 not in repr(settings)
