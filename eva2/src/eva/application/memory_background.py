"""Provider-neutral cold-path delivery of semantic conversation memory."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from queue import Empty, Queue
from threading import Event, Lock, Thread
import time
from typing import Callable, Protocol

from eva.application.memory import (
    ConversationMemoryDocument,
    MemoryRetentionReceipt,
    SemanticMemoryProviderPort,
)


class DurableMemoryOutboxPort(Protocol):
    def enqueue(self, document: ConversationMemoryDocument) -> object: ...

    def pending(self, *, limit: int = 256) -> tuple[object, ...]: ...

    def begin(self, job: object, *, now: datetime | None = None) -> object | None: ...

    def succeed(
        self,
        job: object,
        receipt: MemoryRetentionReceipt,
        *,
        now: datetime | None = None,
    ) -> object: ...

    def fail(
        self, job: object, error: Exception, *, now: datetime | None = None
    ) -> object: ...


class BackgroundReflectionBudgetPort(Protocol):
    def admit_background_reflection(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        principal_id: str,
        now: datetime,
    ) -> object: ...


class MemoryStorageBudgetPort(Protocol):
    def admit_memory(
        self, document: ConversationMemoryDocument, *, now: datetime
    ) -> object: ...

    def mark_retained(
        self,
        document: ConversationMemoryDocument,
        receipt: MemoryRetentionReceipt,
        *,
        now: datetime,
    ) -> object: ...

    def claim_expired(self, *, now: datetime, limit: int = 64) -> tuple[object, ...]: ...

    def mark_removed(self, job: object, *, now: datetime) -> object: ...


@dataclass(frozen=True, slots=True)
class _RetainJob:
    document: ConversationMemoryDocument
    attempt: int = 1
    durable_job: object | None = None


class SemanticMemoryColdPathWorker:
    """Keep retain and maintenance entirely outside the conversational hot path.

    Retain is offered to the selected provider outside the response path.
    Provider maintenance starts only after the configured idle period. A poison
    job is tried at most three times.
    """

    def __init__(
        self,
        memory: SemanticMemoryProviderPort,
        *,
        idle_seconds: float = 300.0,
        retry_delay_seconds: float = 2.0,
        idle_tasks: tuple[Callable[[], object], ...] = (),
        outbox: DurableMemoryOutboxPort | None = None,
        reflection_budget: BackgroundReflectionBudgetPort | None = None,
        storage_budget: MemoryStorageBudgetPort | None = None,
    ) -> None:
        if idle_seconds <= 0 or retry_delay_seconds < 0:
            raise ValueError("cold-path timing is invalid")
        self._memory = memory
        self._idle_seconds = idle_seconds
        self._retry_delay_seconds = retry_delay_seconds
        self._idle_tasks = idle_tasks
        self._outbox = outbox
        self._reflection_budget = reflection_budget
        self._storage_budget = storage_budget
        self._queue: Queue[_RetainJob | None] = Queue()
        self._stop = Event()
        self._thread: Thread | None = None
        self._lock = Lock()
        self._last_activity = time.monotonic()
        self._reflection_scopes: set[tuple[str, str, str]] = set()
        self._last_error: str | None = None

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def start(self) -> None:
        pending_jobs = () if self._outbox is None else self._outbox.pending()
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = Thread(
                target=self._run,
                name="eva-hindsight-cold-path",
                daemon=True,
            )
            self._thread.start()
        if self._outbox is not None:
            for durable_job in pending_jobs:
                document = getattr(durable_job, "document", None)
                if not isinstance(document, ConversationMemoryDocument):
                    raise RuntimeError("durable memory outbox returned an invalid job")
                self._queue.put_nowait(_RetainJob(document, durable_job=durable_job))

    def stop(self, timeout_seconds: float = 5.0) -> None:
        self._stop.set()
        self._queue.put(None)
        thread = self._thread
        if thread is not None:
            thread.join(timeout_seconds)

    def submit(self, document: ConversationMemoryDocument) -> object | None:
        with self._lock:
            self._last_activity = time.monotonic()
        admission = None
        if self._storage_budget is not None:
            admission = self._storage_budget.admit_memory(
                document,
                now=datetime.now(tz=UTC).replace(microsecond=0),
            )
            admitted = getattr(admission, "admitted", None)
            retention_until = getattr(admission, "retention_until", None)
            if not isinstance(admitted, bool):
                raise RuntimeError("memory storage budget returned an invalid decision")
            if not admitted:
                return admission
            if not isinstance(retention_until, str) or not retention_until:
                raise RuntimeError(
                    "admitted memory storage decision lacks a retention deadline"
                )
            document = replace(document, retention_until=retention_until)
        durable_job = None if self._outbox is None else self._outbox.enqueue(document)
        self._queue.put_nowait(_RetainJob(document, durable_job=durable_job))
        return admission

    def _run(self) -> None:
        poll_seconds = min(self._idle_seconds, 1.0)
        while not self._stop.is_set():
            try:
                job = self._queue.get(timeout=poll_seconds)
            except Empty:
                if self._outbox is not None:
                    try:
                        recovered = self._outbox.pending()
                    except Exception as error:
                        with self._lock:
                            self._last_error = f"{type(error).__name__}: {error}"
                        recovered = ()
                    for durable_job in recovered:
                        document = getattr(durable_job, "document", None)
                        if not isinstance(document, ConversationMemoryDocument):
                            with self._lock:
                                self._last_error = (
                                    "RuntimeError: durable memory outbox returned "
                                    "an invalid job"
                                )
                            continue
                        self._queue.put_nowait(
                            _RetainJob(document, durable_job=durable_job)
                        )
                    if recovered:
                        continue
                self._schedule_reflection_if_idle()
                continue
            if job is None:
                return
            active_job = job
            if self._outbox is not None:
                try:
                    durable_job = self._outbox.begin(
                        job.durable_job,
                        now=datetime.now(tz=UTC).replace(microsecond=0),
                    )
                except Exception as error:
                    with self._lock:
                        self._last_error = f"{type(error).__name__}: {error}"
                    if not self._stop.wait(self._retry_delay_seconds):
                        self._queue.put_nowait(job)
                    self._queue.task_done()
                    continue
                if durable_job is None:
                    self._queue.task_done()
                    continue
                active_job = _RetainJob(job.document, durable_job=durable_job)
            try:
                receipt = self._memory.retain_conversation(active_job.document)
                if self._storage_budget is not None:
                    self._storage_budget.mark_retained(
                        active_job.document,
                        receipt,
                        now=datetime.now(tz=UTC).replace(microsecond=0),
                    )
                if self._outbox is not None:
                    self._outbox.succeed(
                        active_job.durable_job,
                        receipt,
                        now=datetime.now(tz=UTC).replace(microsecond=0),
                    )
                with self._lock:
                    self._last_error = None
                    if active_job.document.audience.kind == "private_person":
                        self._reflection_scopes.add(
                            (
                                active_job.document.tenant_id,
                                active_job.document.installation_id,
                                active_job.document.principal_id,
                            )
                        )
            except Exception as error:  # worker boundary: never kill the chat server
                with self._lock:
                    self._last_error = f"{type(error).__name__}: {error}"
                if self._outbox is not None:
                    try:
                        state = self._outbox.fail(
                            active_job.durable_job,
                            error,
                            now=datetime.now(tz=UTC).replace(microsecond=0),
                        )
                    except Exception as outbox_error:
                        with self._lock:
                            self._last_error = (
                                f"{type(outbox_error).__name__}: {outbox_error}"
                            )
                        if not self._stop.wait(self._retry_delay_seconds):
                            self._queue.put_nowait(active_job)
                        continue
                    if (
                        getattr(state, "status", None) != "needs_help"
                        and not self._stop.wait(self._retry_delay_seconds)
                    ):
                        self._queue.put_nowait(
                            _RetainJob(job.document, durable_job=state)
                        )
                elif job.attempt < 3 and not self._stop.wait(
                    self._retry_delay_seconds
                ):
                    self._queue.put_nowait(
                        _RetainJob(job.document, job.attempt + 1)
                    )
            finally:
                self._queue.task_done()
            self._schedule_reflection_if_idle()

    def _schedule_reflection_if_idle(self) -> None:
        with self._lock:
            if time.monotonic() - self._last_activity < self._idle_seconds:
                return
            scopes = tuple(self._reflection_scopes)
            self._reflection_scopes.clear()
        if self._storage_budget is not None:
            now = datetime.now(tz=UTC).replace(microsecond=0)
            try:
                expired = self._storage_budget.claim_expired(now=now)
            except Exception as error:
                with self._lock:
                    self._last_error = f"{type(error).__name__}: {error}"
                expired = ()
            for expiry_job in expired:
                try:
                    bank_id = getattr(expiry_job, "bank_id", None)
                    document_id = getattr(expiry_job, "document_id", None)
                    if not isinstance(bank_id, str) or not isinstance(document_id, str):
                        raise RuntimeError(
                            "memory storage budget returned an invalid expiry job"
                        )
                    self._memory.remove_conversation_document(
                        bank_id=bank_id, document_id=document_id
                    )
                    self._storage_budget.mark_removed(
                        expiry_job,
                        now=datetime.now(tz=UTC).replace(microsecond=0),
                    )
                except Exception as error:
                    with self._lock:
                        self._last_error = f"{type(error).__name__}: {error}"
        for tenant_id, installation_id, principal_id in scopes:
            try:
                if self._reflection_budget is not None:
                    admission = self._reflection_budget.admit_background_reflection(
                        tenant_id=tenant_id,
                        installation_id=installation_id,
                        principal_id=principal_id,
                        now=datetime.now(tz=UTC).replace(microsecond=0),
                    )
                    admitted = getattr(admission, "admitted", None)
                    if not isinstance(admitted, bool):
                        raise RuntimeError(
                            "background reflection budget returned an invalid decision"
                        )
                    if not admitted:
                        continue
                self._memory.maintain_person_scope(
                    tenant_id, installation_id, principal_id
                )
            except Exception as error:  # retried after the next retained exchange
                with self._lock:
                    self._last_error = f"{type(error).__name__}: {error}"
                    self._reflection_scopes.add(
                        (tenant_id, installation_id, principal_id)
                    )
        for task in self._idle_tasks:
            try:
                task()
            except Exception as error:
                with self._lock:
                    self._last_error = f"{type(error).__name__}: {error}"
        with self._lock:
            self._last_activity = time.monotonic()


HindsightColdPathWorker = SemanticMemoryColdPathWorker


__all__ = [
    "BackgroundReflectionBudgetPort",
    "DurableMemoryOutboxPort",
    "HindsightColdPathWorker",
    "MemoryStorageBudgetPort",
    "SemanticMemoryColdPathWorker",
]
