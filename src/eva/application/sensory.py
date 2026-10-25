"""Channel-neutral sensory events and a bounded background ingestion worker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import threading
from typing import Callable, Protocol, runtime_checkable

from eva.contracts.base import JsonValue, validate_json_value


@dataclass(frozen=True, slots=True)
class SensoryEvent:
    source: str
    source_event_id: str
    event_type: str
    occurred_at: datetime
    payload: dict[str, JsonValue]

    def __post_init__(self) -> None:
        if not all((self.source, self.source_event_id, self.event_type)):
            raise ValueError("sensory event identity is incomplete")
        if self.occurred_at.tzinfo is None:
            raise ValueError("sensory event timestamp must be timezone-aware")
        validate_json_value(self.payload)


@dataclass(frozen=True, slots=True)
class SensoryAttentionSource:
    """Provider-owned event coverage, not an action or a subscription grant."""
    source: str
    event_type: str
    selector_key: str
    selector_value: str
    configuration_sha256: str
    coverage: str

    def __post_init__(self):
        import re
        if (any(not isinstance(v, str) or not v or len(v) > 255 for v in
                (self.source, self.event_type, self.selector_key, self.selector_value, self.coverage))
                or not self.selector_key.isidentifier() or len(self.selector_key) > 80
                or not isinstance(self.configuration_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", self.configuration_sha256) is None):
            raise ValueError("source attention coverage is invalid")

    def to_payload(self):
        from dataclasses import asdict
        return asdict(self)


class SensoryEventSourcePort(Protocol):
    def listen(
        self,
        stop: threading.Event,
        handler: Callable[[SensoryEvent], None],
    ) -> None: ...


class SensoryEventStorePort(Protocol):
    def retain(self, event: SensoryEvent) -> str: ...


@runtime_checkable
class SensoryEventRevalidatorPort(Protocol):
    """Read current source evidence; never replay the historical external event."""

    def revalidate(self, event: SensoryEvent, *, now: datetime) -> SensoryEvent: ...


class SensoryIngestionWorker:
    """Keep connector I/O outside PostgreSQL and durably retain each event."""

    def __init__(
        self,
        source: SensoryEventSourcePort,
        store: SensoryEventStorePort,
    ) -> None:
        self._source = source
        self._store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="eva-sensory-ingress",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            # A connector may still be unwinding blocked I/O. Keep ownership so
            # start() cannot clear its stop request and open a second listener.
            if not self._thread.is_alive():
                self._thread = None

    def _run(self) -> None:
        self._source.listen(self._stop, self._store.retain)


__all__ = ["SensoryEvent", "SensoryEventRevalidatorPort", "SensoryIngestionWorker"]
