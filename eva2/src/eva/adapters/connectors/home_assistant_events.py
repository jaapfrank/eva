"""Bounded Home Assistant websocket state-event source."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import hashlib
import json
import threading
from urllib.parse import urlsplit, urlunsplit

from websockets.sync.client import connect

from eva.application.sensory import SensoryEvent
from eva.contracts.canonical_json import canonical_json_bytes


class HomeAssistantEventStream:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        admitted_domains: tuple[str, ...] = ("light",),
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Home Assistant event URL is invalid")
        if not token:
            raise ValueError("Home Assistant event token is required")
        domains = tuple(sorted(set(admitted_domains)))
        if not domains or any(not value.isidentifier() for value in domains):
            raise ValueError("Home Assistant event domains are invalid")
        self._url = urlunsplit(
            (
                "wss" if parsed.scheme == "https" else "ws",
                parsed.netloc,
                f"{parsed.path.rstrip('/')}/api/websocket",
                "",
                "",
            )
        )
        self._token = token
        self._domains = frozenset(domains)

    def listen(
        self,
        stop: threading.Event,
        handler: Callable[[SensoryEvent], None],
    ) -> None:
        backoff = 1.0
        while not stop.is_set():
            try:
                self._listen_once(stop, handler)
                backoff = 1.0
            except Exception:
                if stop.wait(backoff):
                    return
                backoff = min(backoff * 2, 30.0)

    def _listen_once(
        self,
        stop: threading.Event,
        handler: Callable[[SensoryEvent], None],
    ) -> None:
        with connect(
            self._url,
            open_timeout=5,
            close_timeout=2,
            max_size=1_048_576,
            ping_interval=20,
            ping_timeout=10,
        ) as socket:
            self._expect_type(socket.recv(timeout=5), "auth_required")
            socket.send(json.dumps({"access_token": self._token, "type": "auth"}))
            self._expect_type(socket.recv(timeout=5), "auth_ok")
            socket.send(
                json.dumps(
                    {"event_type": "state_changed", "id": 1, "type": "subscribe_events"}
                )
            )
            subscribed = self._decode(socket.recv(timeout=5))
            if (
                subscribed.get("type") != "result"
                or subscribed.get("id") != 1
                or subscribed.get("success") is not True
            ):
                raise RuntimeError("Home Assistant event subscription was rejected")
            while not stop.is_set():
                try:
                    raw = socket.recv(timeout=1)
                except TimeoutError:
                    continue
                event = self._event_from_frame(raw)
                if event is not None:
                    handler(event)

    def _event_from_frame(self, raw: str | bytes) -> SensoryEvent | None:
        frame = self._decode(raw)
        if frame.get("type") != "event" or frame.get("id") != 1:
            return None
        envelope = frame.get("event")
        data = envelope.get("data") if isinstance(envelope, dict) else None
        new_state = data.get("new_state") if isinstance(data, dict) else None
        old_state = data.get("old_state") if isinstance(data, dict) else None
        entity_id = data.get("entity_id") if isinstance(data, dict) else None
        if not isinstance(entity_id, str) or "." not in entity_id:
            return None
        if entity_id.split(".", 1)[0] not in self._domains:
            return None
        if not isinstance(new_state, dict) or not isinstance(old_state, dict):
            return None
        new_value = str(new_state.get("state", ""))
        old_value = str(old_state.get("state", ""))
        if not new_value or new_value == old_value:
            return None
        attributes = new_state.get("attributes")
        context = new_state.get("context")
        time_fired = envelope.get("time_fired")
        if not isinstance(time_fired, str):
            return None
        occurred = datetime.fromisoformat(time_fired.replace("Z", "+00:00"))
        occurred = occurred.astimezone(UTC).replace(microsecond=0)
        payload = {
            "context_id": (
                context.get("id")
                if isinstance(context, dict) and isinstance(context.get("id"), str)
                else None
            ),
            "device_class": (
                attributes.get("device_class")
                if isinstance(attributes, dict)
                and isinstance(attributes.get("device_class"), str)
                else None
            ),
            "entity_id": entity_id,
            "friendly_name": (
                attributes.get("friendly_name")
                if isinstance(attributes, dict)
                and isinstance(attributes.get("friendly_name"), str)
                else None
            ),
            "unit_of_measurement": (
                attributes.get("unit_of_measurement")
                if isinstance(attributes, dict)
                and isinstance(attributes.get("unit_of_measurement"), str)
                else None
            ),
            "new_state": new_value,
            "old_state": old_value,
            "time_fired": time_fired,
        }
        identity = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        return SensoryEvent(
            source="home_assistant",
            source_event_id=f"home-assistant:{identity}",
            event_type="state_changed",
            occurred_at=occurred,
            payload=payload,
        )

    @classmethod
    def _expect_type(cls, raw: str | bytes, expected: str) -> None:
        if cls._decode(raw).get("type") != expected:
            raise RuntimeError(f"Home Assistant websocket expected {expected}")

    @staticmethod
    def _decode(raw: str | bytes) -> dict[str, object]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise RuntimeError("Home Assistant websocket frame is invalid")
        return decoded


__all__ = ["HomeAssistantEventStream"]
