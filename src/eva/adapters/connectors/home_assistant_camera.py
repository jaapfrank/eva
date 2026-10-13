"""Optional binary camera input using Eva's configured Home Assistant connection.

This is the same camera_proxy endpoint used by Eva1. It does not interpret
images, publish sensory events, or claim that retrieval time is capture time.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import re

import httpx2 as httpx


@dataclass(frozen=True, slots=True)
class CameraSnapshot:
    entity_id: str
    content_type: str
    data: bytes = field(repr=False)
    retrieved_at: datetime

    def __post_init__(self) -> None:
        data = self.data
        valid = (
            (self.content_type == "image/jpeg" and data.startswith(b"\xff\xd8\xff"))
            or (self.content_type == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n"))
            or (self.content_type == "image/webp" and data.startswith(b"RIFF") and data[8:12] == b"WEBP")
        )
        if not valid or len(data) > 2_097_152:
            raise RuntimeError("camera response does not match its image media type or size limit")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


class HomeAssistantCameraSource:
    """Fetch one byte-bounded still image selected by the caller.

    The HTTP timeout limits individual network operations, not total duration.
    A scheduling caller must own cancellation and the overall attempt deadline.
    """

    def __init__(self, *, base_url: str, token: str, max_bytes: int = 2_097_152):
        if type(max_bytes) is not int or not 1 <= max_bytes <= 2_097_152:
            raise ValueError("camera byte limit must be within 1..2097152")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._max_bytes = max_bytes

    def snapshot(self, entity_id: str) -> CameraSnapshot:
        if not isinstance(entity_id, str) or not re.fullmatch(r"camera\.[a-z0-9_]+", entity_id):
            raise ValueError("a Home Assistant camera entity is required")
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
            with client.stream(
                "GET", self._base_url + "/api/camera_proxy/" + entity_id,
                headers={"Authorization": "Bearer " + self._token},
            ) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"camera snapshot returned HTTP {response.status_code}")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type not in {"image/jpeg", "image/png", "image/webp"}:
                    raise RuntimeError("camera snapshot is not a supported still image")
                chunks = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > self._max_bytes:
                        raise RuntimeError("camera snapshot exceeds the transport limit")
                    chunks.append(chunk)
                data = b"".join(chunks)
        # Signature checking is not image decoding; the selected vision backend
        # must reject corrupt or unsupported image content before inference.
        return CameraSnapshot(entity_id, content_type, data, datetime.now(UTC))
