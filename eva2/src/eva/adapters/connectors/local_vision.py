"""Opt-in local image inference feeding the existing sensory event contract."""

from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4

from eva.adapters.connectors.home_assistant_camera import CameraSnapshot
from eva.application.sensory import SensoryEvent


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
