"""One-shot worker that couples Eva's durable fence to Pydantic AI execution."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Protocol

from eva.adapters.postgres.delegated_work import (
    DelegatedWorkReleaseSelector,
    PostgresDelegatedWorkStore,
)
from eva.adapters.subagents.tool_free_child import ToolFreeChildOutcome
from eva.contracts.delegated_work import DelegatedWorkSpec


class ChildRunner(Protocol):
    async def run(
        self,
        spec: DelegatedWorkSpec,
        *,
        call_id: str,
        cancellation_event: object | None = None,
    ) -> ToolFreeChildOutcome: ...


WorkerDisposition = Literal[
    "idle", "succeeded", "failed", "ambiguous", "crashed_before_call", "crashed_after_call"
]
FaultPoint = Literal["after_claim", "after_call_started"]


class DelegatedWorkWorker:
    """Execute at most one claimed task; never infer work outside PostgreSQL."""

    def __init__(
        self,
        store: PostgresDelegatedWorkStore,
        runner: ChildRunner,
        *,
        worker_id: str,
        release: DelegatedWorkReleaseSelector,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._runner = runner
        self._worker_id = worker_id
        self._release = release
        self._clock = clock or (lambda: datetime.now(UTC).replace(microsecond=0))

    async def run_once(
        self,
        *,
        fault_after: FaultPoint | None = None,
        cancellation_event: asyncio.Event | None = None,
    ) -> WorkerDisposition:
        if cancellation_event is not None and cancellation_event.is_set():
            return "idle"
        claim = self._store.claim_next(
            worker_id=self._worker_id,
            release=self._release,
            now=self._now(),
        )
        if claim is None:
            return "idle"
        if fault_after == "after_claim":
            return "crashed_before_call"
        # Let a process stop requested while claim_next was committing become
        # observable before the durable, non-repeatable model-call fence.
        await asyncio.sleep(0)
        # A graceful process stop after the lease but before the durable call fence
        # leaves the admitted task reclaimable when this short lease expires.
        if cancellation_event is not None and cancellation_event.is_set():
            return "crashed_before_call"
        call = self._store.start_call(claim, now=self._now())
        if call is None:
            return "idle"
        if fault_after == "after_call_started":
            return "crashed_after_call"

        outcome = await self._runner.run(
            call.spec,
            call_id=call.call_id,
            cancellation_event=cancellation_event,
        )
        if outcome.result is not None:
            completed_at = _parse_timestamp(outcome.result.completed_at)
            self._store.record_success(call, outcome.result, now=completed_at)
            return "succeeded"
        assert outcome.error_fingerprint is not None
        ambiguous = outcome.status in {
            "cancelled",
            "timed_out",
            "provider_transient",
            "provider_unavailable",
            "internal_error",
        }
        self._store.record_failure(
            call,
            usage=outcome.usage,
            error_code=outcome.status,
            error_fingerprint=outcome.error_fingerprint,
            ambiguous=ambiguous,
            now=self._now(),
        )
        return "ambiguous" if ambiguous else "failed"

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None or value.microsecond:
            raise RuntimeError("delegated worker clock must return aware whole seconds")
        return value.astimezone(UTC)


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


__all__ = ["DelegatedWorkWorker", "WorkerDisposition"]
