"""Opt-in local image inference feeding the existing sensory event contract."""

from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4
import base64
import ipaddress
from urllib.parse import urlsplit

import httpx2 as httpx

from eva.adapters.connectors.home_assistant_camera import CameraSnapshot
from eva.application.sensory import SensoryEvent


class LocalVisionHttpAnalyzer:
    """Explicitly configured local server; images never enter the chat model."""

    def __init__(self, *, endpoint: str, model: str, revision: str):
        parsed = urlsplit(endpoint)
        host = parsed.hostname or ""
        try:
            address = ipaddress.ip_address(host)
            local = any(address in ipaddress.ip_network(network) for network in (
                "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fc00::/7"))
        except ValueError:
            local = host == "localhost"
        if (parsed.scheme != "http" or not local or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError("vision endpoint must be an explicit local HTTP service")
        if any(not isinstance(value, str) or not value.strip() or len(value) > 200
               for value in (model, revision)):
            raise ValueError("vision model and revision are required")
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.revision = revision

    def analyze(self, snapshot: CameraSnapshot) -> SensoryEvent:
        encoded = base64.b64encode(snapshot.data).decode("ascii")
        body = {"model": self.model, "messages": [{"role": "user", "content": [
            {"type": "text", "text": "Describe only visible objects, colours and positions. Mention uncertainty. Do not infer identities, intentions, dates or unseen events. Keep it brief."},
            {"type": "image_url", "image_url": {"url": f"data:{snapshot.content_type};base64,{encoded}"}},
        ]}], "max_tokens": 120, "temperature": 0}
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
            with client.stream("POST", self.endpoint + "/v1/chat/completions", json=body) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"local vision returned HTTP {response.status_code}")
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 32_768:
                        raise RuntimeError("local vision response exceeds limit")
        result = json.loads(data)
        if result.get("model") != self.model:
            raise RuntimeError("local vision model differs from configured model")
        choice = result["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise RuntimeError("local vision did not complete its interpretation")
        description = choice["message"]["content"]
        return visual_interpretation(snapshot, {
            "image_sha256": snapshot.sha256, "model": self.model,
            "revision": self.revision, "description": description,
        })


class LocalVisionAnalyzer:
    """Run an explicitly configured local backend, without shell or cloud routing.

    The command consumes an image filename as its final argument and emits one
    JSON object to stdout. Timeout covers communication with the direct process,
    including inference, not OS process creation or arbitrary child processes.
    This adapter runs trusted backend code: output is captured before validation,
    not sandboxed or limited while streaming. No polling, automatic retries,
    sensory persistence or initiative is started.
    """

    def __init__(self, command: tuple[str, ...], *, timeout_seconds: int = 60):
        if not command or any(not isinstance(arg, str) or not arg for arg in command):
            raise ValueError("local vision command is required")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
            raise ValueError("local vision deadline must be within 1..300 seconds")
        self._command = command
        self._timeout = timeout_seconds

    def analyze(self, snapshot: CameraSnapshot) -> SensoryEvent:
        # Private temporary directory; raw images are never part of the event.
        with tempfile.TemporaryDirectory(prefix="eva-vision-") as directory:
            path = Path(directory) / "snapshot.image"
            path.write_bytes(snapshot.data)
            completed = subprocess.run(
                [*self._command, str(path)], capture_output=True, check=True,
                timeout=self._timeout,
            )
        if len(completed.stdout) > 32_768:
            raise ValueError("local vision result exceeds limit")
        result = json.loads(completed.stdout)
        return visual_interpretation(snapshot, result)


def visual_interpretation(snapshot: CameraSnapshot, result: dict) -> SensoryEvent:
    if not isinstance(result, dict) or result.get("image_sha256") != snapshot.sha256:
        raise ValueError("local vision result is not bound to the supplied image")
    for key, limit in (("model", 200), ("revision", 200), ("description", 8_000)):
        if not isinstance(result.get(key), str) or not result[key].strip() or len(result[key]) > limit:
            raise ValueError(f"local vision {key} is invalid")
    now = datetime.now(UTC)
    return SensoryEvent(
        source="local_vision",
        source_event_id="vision:" + str(uuid4()),
        event_type="visual_interpretation",
        occurred_at=now,
        payload={
            "entity_id": snapshot.entity_id,
            "image_sha256": snapshot.sha256,
            "media_type": snapshot.content_type,
            "retrieved_at": snapshot.retrieved_at.isoformat(),
            "captured_at": None,
            "analyzed_at": now.isoformat(),
            "model": result["model"],
            "model_revision": result["revision"],
            "description": result["description"],
            "evidence_kind": "model_interpretation_not_verified_fact",
        },
    )
