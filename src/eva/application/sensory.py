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
        self._thread = None

    def _run(self) -> None:
        self._source.listen(self._stop, self._store.retain)


__all__ = ["SensoryEvent", "SensoryEventRevalidatorPort", "SensoryIngestionWorker"]
