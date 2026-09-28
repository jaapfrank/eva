"""On-demand image interpretation through the existing authorized API tool."""
import asyncio
import base64
from contextlib import contextmanager
import json
from types import SimpleNamespace

import pytest

from eva.adapters.connectors.home_assistant_api import HomeAssistantApiTool
from eva.adapters.connectors.local_vision import LocalVisionHttpAnalyzer


def arguments():
    return dict(transport="rest", method="GET", path="/api/camera_proxy/camera.front",
                body_json="", response_pointer="", offset=0, limit=10)


def install_transport(monkeypatch, *, model="fixture", vision_status=200, finish_reason="stop", image=b"\x89PNG\r\n\x1a\nfixture"):
    calls = []
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        @contextmanager
        def stream(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if method == "GET":
                data = image
                media = "image/png"
                status = 200
            else:
                assert "headers" not in kwargs  # HA credential stays with HA.
                assert kwargs["json"]["messages"][0]["content"][1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(image).decode()
                data = json.dumps({"model": model, "choices": [{"finish_reason": finish_reason, "message": {"content": "A red object; details unclear."}}]}).encode()
                media = "application/json"
                status = vision_status
            yield SimpleNamespace(status_code=status, headers={"content-type": media}, iter_bytes=lambda: iter([data]))
    monkeypatch.setattr("eva.adapters.connectors.local_vision.httpx.Client", Client)
    return calls


@pytest.mark.parametrize("enabled", [False, True])
def test_existing_durable_api_returns_interpretation_or_explicit_unavailability(monkeypatch, enabled):
    calls = install_transport(monkeypatch)
    vision = LocalVisionHttpAnalyzer(endpoint="http://127.0.0.1:8080", model="fixture", revision="rev1") if enabled else None
    tool = HomeAssistantApiTool(base_url="http://ha", token="fixture-secret", vision=vision)
    request = SimpleNamespace(arguments=arguments())
    dispatch = asyncio.run(tool.execute(request))
    observation = asyncio.run(tool.observe(request, dispatch))
    result = observation.evidence["observation"]
    value = json.loads(result["response_json"])
    assert result["success"] is enabled
    assert len(calls) == (2 if enabled else 1)  # observe does not replay.
    assert "fixture-secret" not in json.dumps(result)
    if enabled:
        assert value["entity_id"] == "camera.front"
        assert value["evidence_kind"] == "model_interpretation_not_verified_fact"
        assert value["description"] == "A red object; details unclear."
        assert value["model_revision"] == "rev1"
        assert value["captured_at"] is None
    else:
        assert value["error"] == "local_vision_not_configured"


@pytest.mark.parametrize("model,status", [("wrong", 200), ("fixture", 500)])
def test_backend_failure_does_not_become_successful_camera_observation(monkeypatch, model, status):
    install_transport(monkeypatch, model=model, vision_status=status)
    tool = HomeAssistantApiTool(base_url="http://ha", token="fixture",
        vision=LocalVisionHttpAnalyzer(endpoint="http://127.0.0.1:8080", model="fixture", revision="rev1"))
    with pytest.raises(RuntimeError): tool._request(arguments())


def test_truncated_interpretation_does_not_become_complete_observation(monkeypatch):
    install_transport(monkeypatch, finish_reason="length")
    tool = HomeAssistantApiTool(base_url="http://ha", token="fixture",
        vision=LocalVisionHttpAnalyzer(endpoint="http://127.0.0.1:8080", model="fixture", revision="rev1"))
    with pytest.raises(RuntimeError, match="did not complete"):
        tool._request(arguments())


def test_invalid_image_header_is_rejected_before_model_call(monkeypatch):
    calls = install_transport(monkeypatch, image=b"<html>not an image</html>")
    tool = HomeAssistantApiTool(base_url="http://ha", token="fixture",
        vision=LocalVisionHttpAnalyzer(endpoint="http://127.0.0.1:8080", model="fixture", revision="rev1"))
    with pytest.raises(RuntimeError, match="media type"):
        tool._request(arguments())
    assert len(calls) == 1


@pytest.mark.parametrize("changes,seconds", [({}, 45),
    ({"path": "/api/states"}, 25),
    ({"method": "POST", "path": "/api/services/light/turn_on"}, 25)])
def test_only_camera_get_receives_vision_execution_budget(changes, seconds):
    tool = HomeAssistantApiTool(base_url="http://ha", token="fixture", vision=object())
    call = SimpleNamespace(name="home_assistant_request", arguments={**arguments(), **changes})
    prepared = asyncio.run(tool.prepare(None, call))
    assert prepared.execution_timeout_seconds == seconds


@pytest.mark.parametrize("endpoint", ["https://api.example.com", "http://8.8.8.8", "http://user:pass@localhost", "http://localhost#fragment", "http://134744072", "http://arbitrary-name"])
def test_vision_cannot_silently_select_cloud_endpoint(endpoint):
    with pytest.raises(ValueError):
        LocalVisionHttpAnalyzer(endpoint=endpoint, model="fixture", revision="rev1")


def test_provider_requires_complete_explicit_opt_in(monkeypatch, tmp_path):
    from eva.providers.home_assistant import HomeAssistantProviderFactory
    token = tmp_path / "ha-token"
    token.write_text("fixture")
    token.chmod(0o600)
    monkeypatch.setenv("EVA_HOME_ASSISTANT_URL", "http://ha")
    monkeypatch.setenv("EVA_HOME_ASSISTANT_TOKEN_FILE", str(token))
    monkeypatch.setenv("EVA_HOME_ASSISTANT_API_ENABLED", "true")
    monkeypatch.delenv("EVA_HOME_ASSISTANT_TOKEN", raising=False)
    for name in ("EVA_LOCAL_VISION_URL", "EVA_LOCAL_VISION_MODEL", "EVA_LOCAL_VISION_REVISION"):
        monkeypatch.delenv(name, raising=False)
    assert HomeAssistantProviderFactory.load_settings().vision_endpoint == ""
    monkeypatch.setenv("EVA_LOCAL_VISION_URL", "http://127.0.0.1:8080")
    with pytest.raises(RuntimeError, match="complete"):
        HomeAssistantProviderFactory.load_settings()
    monkeypatch.setenv("EVA_LOCAL_VISION_MODEL", "fixture")
    monkeypatch.setenv("EVA_LOCAL_VISION_REVISION", "rev1")
    assert HomeAssistantProviderFactory.load_settings().vision_revision == "rev1"
