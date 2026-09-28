"""Camera transport boundary: no real camera images or model calls."""

from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib

import pytest

from eva.adapters.connectors.home_assistant_camera import HomeAssistantCameraSource


def install_response(monkeypatch, *, data, content_type="image/png", status=200):
    calls = []
    class Response:
        status_code = status
        headers = {"content-type": content_type}
        def iter_bytes(self):
            yield data[:5]
            yield data[5:]
    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {"timeout": 15, "follow_redirects": False, "trust_env": False}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        @contextmanager
        def stream(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            yield Response()
    monkeypatch.setattr("eva.adapters.connectors.home_assistant_camera.httpx.Client", Client)
    return calls


def test_binary_content_and_provenance_survive_without_text_decoding(monkeypatch):
    data = b"\x89PNG\r\n\x1a\n\xff\xfe\x00"
    calls = install_response(monkeypatch, data=data, content_type="image/png; charset=binary")
    before = datetime.now(UTC)
    snapshot = HomeAssistantCameraSource(base_url="http://ha.local:8123/", token="fixture").snapshot("camera.front")
    assert snapshot.data == data
    assert snapshot.sha256 == hashlib.sha256(data).hexdigest()
    assert snapshot.entity_id == "camera.front"
    assert before <= snapshot.retrieved_at <= datetime.now(UTC)
    assert calls == [("GET", "http://ha.local:8123/api/camera_proxy/camera.front", {"headers": {"Authorization": "Bearer fixture"}})]


@pytest.mark.parametrize("data,media,status", [
    (b"<html>error</html>", "text/html", 200),
    (b"<html>error</html>", "image/png", 200),
    (b"", "image/jpeg", 200),
    (b"redirect", "image/png", 302),
    (b"denied", "image/png", 401),
])
def test_non_images_and_failed_requests_do_not_become_observations(monkeypatch, data, media, status):
    calls = install_response(monkeypatch, data=data, content_type=media, status=status)
    with pytest.raises(RuntimeError):
        HomeAssistantCameraSource(base_url="http://ha.local", token="fixture").snapshot("camera.front")
    assert len(calls) == 1


def test_streaming_size_limit(monkeypatch):
    install_response(monkeypatch, data=b"\x89PNG\r\n\x1a\n" + b"x" * 20)
    with pytest.raises(RuntimeError, match="transport limit"):
        HomeAssistantCameraSource(base_url="http://ha.local", token="fixture", max_bytes=10).snapshot("camera.front")


@pytest.mark.parametrize("entity", ["light.front", "camera.front?token=other", "camera.../other", "https://other", "camera.front\n"])
def test_entity_cannot_change_destination(monkeypatch, entity):
    calls = install_response(monkeypatch, data=b"not reached")
    with pytest.raises(ValueError):
        HomeAssistantCameraSource(base_url="http://ha.local", token="fixture").snapshot(entity)
    assert not calls
