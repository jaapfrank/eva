"""TTL-bounded one-time outbound media attachment claims for messaging transports."""

from __future__ import annotations

import base64
from collections import OrderedDict
import threading
import time
from typing import Any

_OUTBOUND_LOCK = threading.Lock()
_OUTBOUND_CLAIMS: OrderedDict[str, dict[str, Any]] = OrderedDict()
_OUTBOUND_TTL_SECONDS = 300.0


def queue_telegram_outbound_photo(
    durable_message_id: str,
    *,
    meta: dict[str, Any],
    raw_bytes: bytes,
) -> None:
    if not isinstance(durable_message_id, str) or not durable_message_id.startswith("telegram:"):
        return
    now_mono = time.monotonic()
    ext = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }.get(str(meta.get("content_type", "image/jpeg")), "jpg")
    item = {
        "expires_at": now_mono + _OUTBOUND_TTL_SECONDS,
        "snapshot_id": meta.get("snapshot_id"),
        "camera_entity_id": meta.get("camera_entity_id"),
        "captured_at": meta.get("captured_at"),
        "content_type": meta.get("content_type", "image/jpeg"),
        "filename": f"{meta.get('snapshot_id') or 'camera'}.{ext}",
        "image_base64": base64.b64encode(raw_bytes).decode("ascii"),
    }
    with _OUTBOUND_LOCK:
        stale = [k for k, v in _OUTBOUND_CLAIMS.items() if v["expires_at"] <= now_mono]
        for k in stale:
            _OUTBOUND_CLAIMS.pop(k, None)
        _OUTBOUND_CLAIMS[durable_message_id] = item
        while len(_OUTBOUND_CLAIMS) > 24:
            _OUTBOUND_CLAIMS.popitem(last=False)


def claim_telegram_outbound_photo(durable_message_id: str) -> dict[str, Any] | None:
    if not isinstance(durable_message_id, str) or not durable_message_id.startswith("telegram:"):
        return None
    now_mono = time.monotonic()
    with _OUTBOUND_LOCK:
        stale = [k for k, v in _OUTBOUND_CLAIMS.items() if v["expires_at"] <= now_mono]
        for k in stale:
            _OUTBOUND_CLAIMS.pop(k, None)
        found = _OUTBOUND_CLAIMS.pop(durable_message_id, None)
    if found is None:
        return None
    return {k: v for k, v in found.items() if k != "expires_at"}


__all__ = ["claim_telegram_outbound_photo", "queue_telegram_outbound_photo"]
