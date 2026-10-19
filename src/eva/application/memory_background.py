"""Provider-neutral cold-path delivery of semantic conversation memory."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import hashlib
from queue import Empty, Queue
import re
from threading import Event, Lock, RLock, Thread, current_thread
import time
from typing import Callable, Protocol

from eva.application.memory import (
    ConversationMemoryDocument,
    MemoryRetentionReceipt,
    SemanticMemoryProviderPort,
)


from eva.application.memory_source_lifecycle import CompactedPrivateMemorySource


class DurableMemoryOutboxPort(Protocol):
    def enqueue(self, document: ConversationMemoryDocument) -> object: ...

    def pending(self, *, limit: int = 256) -> tuple[object, ...]: ...

    def pending_reflection_scopes(self, *, now: datetime | None = None
                                  ) -> tuple[tuple[str, str, str], ...]: ...

    def claim_reflection_scope(self, tenant_id: str, installation_id: str,
                               principal_id: str, *, now: datetime) -> bool: ...

    def begin(self, job: object, *, now: datetime | None = None) -> object | None: ...

    def erase_content(
        self,
        *,
        principal_sha256: str,
        document_id: str,
        operation_sha256: str,
        erased_at: datetime,
    ) -> object: ...

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

    def claim_expired(
        self, *, now: datetime, limit: int = 64
    ) -> tuple[object, ...]: ...

    def request_erasure(
        self,
        *,
        principal_id: str,
        document_id: str,
        now: datetime,
        source_message_id: str | None = None,
    ) -> object | None: ...

    def mark_removed(self, job: object, *, now: datetime) -> object: ...


class ConversationContentErasurePort(Protocol):
    def erase_message_content(
        self,
        *,
        principal_sha256: str,
        message_id: str,
        operation_sha256: str,
        erased_at: datetime,
    ) -> object: ...


class RestoreAuthorityErasurePort(Protocol):
    def advance_erasure_generation(
        self, *, operation_sha256: str, erased_at: datetime
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class _RetainJob:
    document: ConversationMemoryDocument
    attempt: int = 1
    durable_job: object | None = None


class SemanticMemoryColdPathWorker:
    """Keep retain and maintenance entirely outside the conversational hot path.

    Retain is offered to the selected provider outside the response path.
    Provider maintenance starts only after the configured idle period. A poison
    job is tried at most three times. Admitted-work service callbacks use one
    separate serial thread, independent of the conversation idle timer.
    """

    def __init__(
        self,
        memory: SemanticMemoryProviderPort,
        *,
        idle_seconds: float = 300.0,
        retry_delay_seconds: float = 2.0,
        idle_tasks: tuple[Callable[[], object], ...] = (),
        idle_capacity: Callable[[], bool] | None = None,
        service_tasks: tuple[Callable[[], object], ...] = (),
        outbox: DurableMemoryOutboxPort | None = None,
        reflection_budget: BackgroundReflectionBudgetPort | None = None,
        storage_budget: MemoryStorageBudgetPort | None = None,
        conversation_erasure: ConversationContentErasurePort | None = None,
        restore_authority_erasure: RestoreAuthorityErasurePort | None = None,
    ) -> None:
        if idle_seconds <= 0 or retry_delay_seconds < 0:
            raise ValueError("cold-path timing is invalid")
        self._memory = memory
        self._idle_seconds = idle_seconds
        self._retry_delay_seconds = retry_delay_seconds
        self._idle_tasks = idle_tasks
        self._idle_capacity = idle_capacity
        self._foreground_count = 0
        self._service_tasks = service_tasks
        self._outbox = outbox
        self._reflection_budget = reflection_budget
        self._storage_budget = storage_budget
        self._conversation_erasure = conversation_erasure
        self._restore_authority_erasure = restore_authority_erasure
        self._queue: Queue[_RetainJob | None] = Queue()
        self._stop = Event()
        self._thread: Thread | None = None
        self._service_thread: Thread | None = None
        self._stopping = False
        self._lock = Lock()
        self._retention_fence = RLock()
        self._dream_pending: dict[str, _RetainJob] = {}
        self._last_activity = time.monotonic()
        self._reflection_scopes: set[tuple[str, str, str]] = set()
        self._last_error: str | None = None

    @contextmanager
    def foreground(self):
        """Prioritize overlapping conversations without waiting for an idle task."""
        with self._lock:
            self._foreground_count += 1
            self._last_activity = time.monotonic()
        try:
            yield
        finally:
            with self._lock:
                self._foreground_count -= 1
                self._last_activity = time.monotonic()

    @property
    def foreground_active(self) -> bool:
        """Read current conversation pressure without imposing an idle interval."""
        with self._lock:
            return self._foreground_count > 0

    def _idle_due(self) -> bool:
        with self._lock:
            return (not self._stop.is_set() and not self._foreground_count
                    and time.monotonic() - self._last_activity >= self._idle_seconds)

    def _idle_ready(self, *, check_capacity: bool = True) -> bool:
        if not self._idle_due():
            return False
        if check_capacity and self._idle_capacity is not None:
            try:
                if self._idle_capacity() is not True:
                    return False
            except Exception as error:
                with self._lock:
                    self._last_error = f"idle capacity unavailable: {type(error).__name__}"
                return False
        # Capacity/database reads must not conceal a newly arrived conversation.
        return self._idle_due()

    def _reflection_has_capacity(self) -> bool:
        """Do not scan exhausted reflection scopes before unrelated idle work."""
        reader = getattr(self._reflection_budget, "has_installation_capacity", None)
        if not callable(reader):
            return True  # Older budget ports retain their per-scope admission.
        try:
            available = reader(now=datetime.now(tz=UTC).replace(microsecond=0))
            if type(available) is not bool:
                raise RuntimeError("reflection capacity returned an invalid decision")
            return available
        except Exception as error:
            with self._lock:
                self._last_error = f"reflection capacity unavailable: {type(error).__name__}"
            return False

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def start(self) -> None:
        pending_jobs = () if self._outbox is None else self._outbox.pending()
        recover = getattr(self._outbox, "pending_reflection_scopes", None)
        recovered_scopes = () if not callable(recover) else recover()
        if recovered_scopes and not callable(getattr(self._outbox, "claim_reflection_scope", None)):
            raise RuntimeError("durable reflection recovery lacks a dispatch claim")
        if any(not isinstance(scope, tuple) or len(scope) != 3
               or any(not isinstance(value, str) or not value.strip() for value in scope)
               for scope in recovered_scopes):
            raise RuntimeError("durable reflection scope is invalid")
        with self._lock:
            if self._stopping or any(thread is not None and thread.is_alive()
                                     for thread in (self._thread, self._service_thread)):
                return
            self._reflection_scopes.update(recovered_scopes)
            self._stop.clear()
            self._thread = Thread(
                target=self._run,
                name="eva-hindsight-cold-path",
                daemon=True,
            )
            self._thread.start()
            if self._service_tasks:
                self._service_thread = Thread(target=self._run_services,
                    name="eva-background-services", daemon=True)
                self._service_thread.start()
        if self._outbox is not None:
            for durable_job in pending_jobs:
                document = getattr(durable_job, "document", None)
                if not isinstance(document, ConversationMemoryDocument):
                    raise RuntimeError("durable memory outbox returned an invalid job")
                self._queue.put_nowait(_RetainJob(document, durable_job=durable_job))

    def stop(self, timeout_seconds: float = 5.0) -> None:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        with self._lock:
            self._stopping = True
            self._stop.set()
            self._queue.put(None)
            threads = (self._thread, self._service_thread)
        try:
            for thread in threads:
                if thread is not None and thread is not current_thread():
                    thread.join(max(0.0, deadline - time.monotonic()))
        finally:
            with self._lock:
                self._stopping = False

    def _run_services(self) -> None:
        """Advance admitted work and returns independently of memory maintenance.

        Each callback owns durable claims and its bounded operation. There is
        one serial lane: a slow callback is never overlapped or forcibly retried.
        """
        while not self._stop.is_set():
            for task in self._service_tasks:
                if self._stop.is_set():
                    return
                try:
                    task()
                except Exception as error:
                    with self._lock:
                        self._last_error = f"{type(error).__name__}: {error}"
            if self._stop.wait(1.0):
                return

    def submit(self, document: ConversationMemoryDocument) -> object | None:
        if document.retention_mode == "dream" and not callable(getattr(self._outbox, "prepare_dream_batch", None)):
            raise RuntimeError("dream memory requires the existing durable outbox")
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
        if durable_job is not None:
            document = getattr(durable_job, "document", document)
        self._queue.put_nowait(_RetainJob(document, durable_job=durable_job))
        return admission

    def erase_document(
        self,
        *,
        principal_id: str,
        document_id: str,
        now: datetime,
        source_message_id: str | None = None,
        document: ConversationMemoryDocument | CompactedPrivateMemorySource | None = None,
    ) -> bool:
        """Remove semantic and optional raw source content under one durable lease."""

        if self._storage_budget is None:
            raise RuntimeError("memory erasure requires a durable storage budget")
        if source_message_id is not None and self._conversation_erasure is None:
            raise RuntimeError("raw conversation erasure is not configured")
        if source_message_id is not None and self._restore_authority_erasure is None:
            raise RuntimeError("restore authority erasure is not configured")
        request = {
            "principal_id": principal_id,
            "document_id": document_id,
            "now": now,
        }
        if source_message_id is not None:
            request["source_message_id"] = source_message_id
        with self._retention_fence, self._durable_retention_guard():
            if document is None:
                resolve = getattr(self._outbox, "pending_erasure_document", None)
                if callable(resolve):
                    document = resolve(principal_id=principal_id, document_id=document_id,
                        source_message_id=source_message_id)
            if isinstance(document, CompactedPrivateMemorySource) and (
                    document.document_id != document_id or document.principal_id != principal_id
                    or document.source_ref != source_message_id):
                raise RuntimeError("compacted erasure source binding differs")
            if isinstance(document, ConversationMemoryDocument) and document.retention_mode == "dream":
                if (document.document_id != document_id or document.principal_id != principal_id
                        or document.source_ref != source_message_id):
                    raise RuntimeError("pending erasure source binding differs")
                address = getattr(self._memory, "document_retention_address", None)
                pending = getattr(self._storage_budget, "request_pending_erasure", None)
                if not callable(address) or not callable(pending):
                    raise RuntimeError("pending memory erasure is not configured")
                bank_id, native_document_id = address(document)
                if native_document_id != document_id:
                    raise RuntimeError("pending native erasure address differs")
                job = pending(document, bank_id=bank_id, now=now, source_message_id=source_message_id)
            else:
                job = self._storage_budget.request_erasure(**request)
            if job is None:
                return False
            durable_source = getattr(job, "source_message_id", None)
            if source_message_id is not None and durable_source != source_message_id:
                raise RuntimeError("memory erasure source binding differs")
            self._remove_job_fenced(job, now=now)
        return True

    @staticmethod
    def _erasure_operation_sha256(
        *,
        principal_sha256: str,
        document_id: str,
        source_message_id: str | None,
    ) -> str:
        source_binding = (
            source_message_id
            if source_message_id is not None
            else "retention-expiry"
        )
        payload = (
            "EvaConversationMemoryErasure/v1\0"
            f"{principal_sha256}\0{document_id}\0{source_binding}"
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _remove_job(self, job: object, *, now: datetime) -> None:
        # A forget operation must follow an already dispatched retention stage,
        # never race its later write. This lock is independent of foreground I/O.
        with self._retention_fence, self._durable_retention_guard():
            self._remove_job_fenced(job, now=now)

    def _remove_job_fenced(self, job: object, *, now: datetime) -> None:
        if self._storage_budget is None:
            raise RuntimeError("memory erasure requires a durable storage budget")
        bank_id = getattr(job, "bank_id", None)
        document_id = getattr(job, "document_id", None)
        source_message_id = getattr(job, "source_message_id", None)
        principal_sha256 = getattr(job, "principal_sha256", None)
        if not isinstance(bank_id, str) or not isinstance(document_id, str):
            raise RuntimeError("memory storage budget returned an invalid erasure job")
        if source_message_id is not None and (
            not isinstance(source_message_id, str) or not source_message_id
        ):
            raise RuntimeError("memory erasure source binding is invalid")
        if not isinstance(principal_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", principal_sha256
        ):
            raise RuntimeError("memory erasure principal binding is missing")
        if source_message_id is not None and self._conversation_erasure is None:
            raise RuntimeError("raw conversation erasure is not configured")
        operation_sha256 = self._erasure_operation_sha256(
            principal_sha256=principal_sha256,
            document_id=document_id,
            source_message_id=source_message_id,
        )
        if source_message_id is not None:
            if self._restore_authority_erasure is None:
                raise RuntimeError("restore authority erasure is not configured")
            authority_receipt = (
                self._restore_authority_erasure.advance_erasure_generation(
                    operation_sha256=operation_sha256,
                    erased_at=now,
                )
            )
            if (
                getattr(authority_receipt, "operation_sha256", None)
                != operation_sha256
                or not isinstance(
                    getattr(authority_receipt, "erasure_generation", None), int
                )
                or authority_receipt.erasure_generation < 1
                or not isinstance(
                    getattr(authority_receipt, "authority_sha256", None), str
                )
                or re.fullmatch(
                    r"[0-9a-f]{64}", authority_receipt.authority_sha256
                )
                is None
                or not isinstance(getattr(authority_receipt, "replayed", None), bool)
            ):
                raise RuntimeError("restore authority erasure receipt is invalid")
        self._settle_dream_erasure(document_id=document_id, now=now)
        self._memory.remove_conversation_document(
            bank_id=bank_id,
            document_id=document_id,
            principal_sha256=principal_sha256,
        )
        if source_message_id is not None:
            assert self._conversation_erasure is not None
            receipt = self._conversation_erasure.erase_message_content(
                principal_sha256=principal_sha256,
                message_id=source_message_id,
                operation_sha256=operation_sha256,
                erased_at=now,
            )
            expected_message = hashlib.sha256(
                source_message_id.encode("utf-8")
            ).hexdigest()
            if (
                getattr(receipt, "operation_sha256", None) != operation_sha256
                or getattr(receipt, "principal_sha256", None) != principal_sha256
                or getattr(receipt, "message_sha256", None) != expected_message
                or not isinstance(getattr(receipt, "replayed", None), bool)
                or not isinstance(getattr(receipt, "redacted_event_count", None), int)
                or not 0 <= receipt.redacted_event_count <= 2
                or (receipt.replayed and receipt.redacted_event_count != 0)
                or (not receipt.replayed and receipt.redacted_event_count == 0)
            ):
                raise RuntimeError("raw conversation erasure receipt is invalid")
        if self._outbox is not None:
            outbox_receipt = self._outbox.erase_content(
                principal_sha256=principal_sha256,
                document_id=document_id,
                operation_sha256=operation_sha256,
                erased_at=now,
            )
            expected_document = hashlib.sha256(
                document_id.encode("utf-8")
            ).hexdigest()
            if (
                getattr(outbox_receipt, "operation_sha256", None)
                != operation_sha256
                or getattr(outbox_receipt, "principal_sha256", None)
                != principal_sha256
                or getattr(outbox_receipt, "document_sha256", None)
                != expected_document
                or not isinstance(getattr(outbox_receipt, "replayed", None), bool)
                or not isinstance(
                    getattr(outbox_receipt, "redacted_event_count", None), int
                )
                or not 0 <= outbox_receipt.redacted_event_count <= 1
                or (
                    outbox_receipt.replayed
                    and outbox_receipt.redacted_event_count != 0
                )
                or (
                    not outbox_receipt.replayed
                    and outbox_receipt.redacted_event_count != 1
                )
            ):
                raise RuntimeError("memory outbox erasure receipt is invalid")
        self._storage_budget.mark_removed(job, now=now)

    def _run(self) -> None:
        poll_seconds = min(self._idle_seconds, 1.0)
        while not self._stop.is_set():
            try:
                job = self._queue.get(timeout=poll_seconds)
            except Empty:
                added = False
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
                        if document.document_id in self._dream_pending:
                            continue
                        self._queue.put_nowait(
                            _RetainJob(document, durable_job=durable_job)
                        )
                        added = True
                    if added:
                        continue
                try:
                    self._flush_dream_if_idle()
                except Exception as error:
                    with self._lock:
                        self._last_error = f"dream memory: {type(error).__name__}: {error}"
                    self._stop.wait(self._retry_delay_seconds)
                self._schedule_reflection_if_idle()
                continue
            if job is None:
                self._queue.task_done()
                if self._stop.is_set():
                    return
                continue  # A completed previous stop may have left its sentinel queued.
            if job.document.retention_mode == "dream":
                self._dream_pending.setdefault(job.document.document_id, job)
                self._queue.task_done()
                continue
            try:
                with self._retention_fence, self._durable_retention_guard():
                    self._process_immediate(job)
            except Exception as error:
                with self._lock:
                    self._last_error = f"{type(error).__name__}: {error}"
                if not self._stop.wait(self._retry_delay_seconds):
                    self._queue.put_nowait(job)
            finally:
                self._queue.task_done()
            self._schedule_reflection_if_idle()

    def _durable_retention_guard(self):
        guard = getattr(self._outbox, "retention_guard", None)
        return guard() if callable(guard) else nullcontext()

    def _process_immediate(self, job: _RetainJob) -> None:
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
                return
            if durable_job is None:
                return
            active_job = _RetainJob(job.document, durable_job=durable_job)
        try:
            receipt = self._memory.retain_conversation(active_job.document)
            self._accept_retention(active_job, receipt)
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
                    return
                if getattr(
                    state, "status", None
                ) != "needs_help" and not self._stop.wait(
                    self._retry_delay_seconds
                ):
                    self._queue.put_nowait(
                        # begin() reloads the persisted checkpoint from the job.
                        active_job
                    )
            elif job.attempt < 3 and not self._stop.wait(self._retry_delay_seconds):
                self._queue.put_nowait(_RetainJob(job.document, job.attempt + 1))

    def _accept_retention(self, active_job: _RetainJob, receipt: MemoryRetentionReceipt) -> None:
        now = datetime.now(tz=UTC).replace(microsecond=0)
        if self._storage_budget is not None:
            self._storage_budget.mark_retained(active_job.document, receipt, now=now)
        if self._outbox is not None:
            self._outbox.succeed(active_job.durable_job, receipt, now=now)
        with self._lock:
            self._last_error = None
            if (active_job.document.audience.kind == 'private_person'
                    and getattr(receipt, 'disposition', 'retained') == 'retained'):
                self._reflection_scopes.add((active_job.document.tenant_id,
                    active_job.document.installation_id, active_job.document.principal_id))

    def _flush_dream_if_idle(self) -> None:
        if not self._idle_ready():
            return
        prepare = getattr(self._outbox, 'prepare_dream_batch', None)
        if not callable(prepare):
            return
        with self._retention_fence, self._durable_retention_guard():
            batch = None
            active = []
            stage_started = False
            try:
                now = datetime.now(tz=UTC).replace(microsecond=0)
                batch = prepare(tuple(job.durable_job for job in self._dream_pending.values()), now=now)
                if batch is None:
                    self._dream_pending.clear()  # Originals remain in the durable outbox.
                    return
                if any(job.state.status == 'needs_help' for job in batch.jobs):
                    self._outbox.block_dream_batch(batch.state.thread_id,
                        RuntimeError("dream cohort needs help; no new provider dispatch"), now=now)
                    return
                if all(job.state.status == 'completed' for job in batch.jobs):
                    self._outbox.finish_dream_batch(batch, now=now)
                    for job in batch.jobs:
                        self._dream_pending.pop(job.document.document_id, None)
                    return
                checked = self._outbox.read_dream_batch(batch.state.thread_id)
                if not self._idle_ready():
                    return
                identity = getattr(self._memory, "dream_operation_identity", None)
                retain = getattr(self._memory, "retain_dream_conversations", None)
                if not callable(identity) or not callable(retain):
                    raise RuntimeError("selected memory provider does not support dream batches")
                bank_id, operation_id = identity(tuple(job.document for job in checked.jobs))
                stage_started = True
                for job in checked.jobs:
                    if job.state.status == 'completed':
                        continue  # Keep its document in the same native replay.
                    claimed = self._outbox.begin(job, now=now)
                    if claimed is None:
                        raise RuntimeError('dream source claim changed')
                    active.append(_RetainJob(claimed.document, durable_job=claimed))
                # This stage is now started. A later foreground arrival prevents the
                # next stage, but does not burn a retry merely by deferring this one.
                # The exact native operation is durable before its first POST.
                self._outbox.bind_dream_operation(batch, bank_id=bank_id,
                    operation_id=operation_id, now=now)
                receipts = retain(tuple(job.document for job in checked.jobs))
                expected = {job.document.document_id: job for job in checked.jobs}
                if (not isinstance(receipts, tuple) or len(receipts) != len(expected)
                        or any(not isinstance(receipt, MemoryRetentionReceipt) for receipt in receipts)
                        or {receipt.document_id for receipt in receipts} != set(expected)):
                    raise RuntimeError('dream provider receipts differ from exact cohort')
                self._outbox.bind_dream_receipts(batch, receipts, now=now)
                claimed_by_id = {job.document.document_id: job for job in active}
                for receipt in receipts:
                    job = claimed_by_id.get(receipt.document_id)
                    if job is None:
                        # A previously acknowledged source is replayed only for
                        # exact receipt validation, never re-accounted in storage.
                        self._outbox.succeed(expected[receipt.document_id], receipt, now=now)
                    else:
                        self._accept_retention(job, receipt)
                        active.remove(job)
                self._outbox.finish_dream_batch(batch, now=datetime.now(tz=UTC).replace(microsecond=0))
                for job in checked.jobs:
                    self._dream_pending.pop(job.document.document_id, None)
            except Exception as error:
                with self._lock:
                    self._last_error = 'dream memory: ' + type(error).__name__ + ': ' + str(error)
                if batch is not None and not stage_started:
                    self._outbox.block_dream_batch(batch.state.thread_id, error,
                        now=datetime.now(tz=UTC).replace(microsecond=0))
                for job in active:
                    try:
                        state = self._outbox.fail(job.durable_job, error,
                            now=datetime.now(tz=UTC).replace(microsecond=0))
                        if state.status == "needs_help" and batch is not None:
                            self._outbox.block_dream_batch(batch.state.thread_id, error,
                                now=datetime.now(tz=UTC).replace(microsecond=0))
                    except Exception:
                        # Keep the immutable manifest and original source claims.
                        # Recovery resumes them rather than constructing new work.
                        pass
                self._dream_pending.clear()  # Recovery reloads canonical, still-pending sources.
                self._stop.wait(self._retry_delay_seconds)

    def _settle_dream_erasure(self, *, document_id: str, now: datetime) -> None:
        find = getattr(self._outbox, "dream_batches_for_erasure", None)
        if not callable(find):
            return
        for state, binding in find(document_id):
            thread_id = state.thread_id
            if state.phase == "memory_dream_cancelled":
                continue
            if binding is None:
                self._outbox.cancel_undispatched_dream_batch(thread_id, now=now)
                continue
            # Stop ordinary recovery before probing a possibly still running remote
            # operation. Forget remains pending if settlement cannot be proven.
            self._outbox.block_dream_batch(thread_id,
                RuntimeError("source erasure requested; settle native operation first"), now=now)
            settled = self._memory.settle_dream_operation(**binding)
            if settled == "not_found":
                self._outbox.cancel_absent_dream_batch(thread_id, **binding, now=now)
            elif settled == "completed":
                if state.phase == "memory_dream_completed":
                    continue
                batch = self._outbox.read_dream_batch(thread_id)
                receipts = self._memory.read_dream_operation_receipts(
                    tuple(job.document for job in batch.jobs), **binding)
                expected = {job.document.document_id: job for job in batch.jobs}
                if (not isinstance(receipts, tuple) or len(receipts) != len(expected)
                        or any(not isinstance(receipt, MemoryRetentionReceipt) for receipt in receipts)
                        or {receipt.document_id for receipt in receipts} != set(expected)):
                    raise RuntimeError("settled dream receipts differ from exact cohort")
                self._outbox.bind_dream_receipts(batch, receipts, now=now)
                for receipt in receipts:
                    durable = self._outbox.reconcile_dream_receipt(batch, receipt, now=now)
                    if durable.document.document_id == document_id or durable.state.status == "completed":
                        self._outbox.succeed(durable, receipt, now=now)
                    else:
                        self._accept_retention(_RetainJob(durable.document, durable_job=durable), receipt)
                self._outbox.finish_dream_batch(batch, now=now)
                for source in batch.jobs:
                    self._dream_pending.pop(source.document.document_id, None)
            elif settled != "failed":
                raise RuntimeError("native dream operation has not safely settled")
            # A failed, settled operation cannot write again. Its other members stay
            # bound and visibly unresolved, rather than being silently re-extracted.
        self._dream_pending.pop(document_id, None)

    def _schedule_reflection_if_idle(self) -> None:
        if self._stop.is_set():
            return
        with self._lock:
            if time.monotonic() - self._last_activity < self._idle_seconds:
                return
        if self._storage_budget is not None:
            now = datetime.now(tz=UTC).replace(microsecond=0)
            try:
                expired = self._storage_budget.claim_expired(now=now)
            except Exception as error:
                with self._lock:
                    self._last_error = f"{type(error).__name__}: {error}"
                expired = ()
            for expiry_job in expired:
                if self._stop.is_set():
                    return
                try:
                    self._remove_job(
                        expiry_job,
                        now=datetime.now(tz=UTC).replace(microsecond=0),
                    )
                except Exception as error:
                    with self._lock:
                        self._last_error = f"{type(error).__name__}: {error}"
        if not self._idle_ready():
            return
        compact = getattr(self._outbox, "compact_day_sources", None)
        if callable(compact):
            # No provider/model call: expire acknowledged prior-day raw text only.
            # The existing cross-process retention fence also serializes forgetting.
            try:
                with self._retention_fence:
                    if self._idle_ready():
                        compact(now=datetime.now(tz=UTC).replace(microsecond=0), limit=32)
            except Exception as error:
                with self._lock:
                    self._last_error = "day text lifecycle: " + type(error).__name__ + ": " + str(error)
        with self._lock:
            scopes = tuple(self._reflection_scopes)
            self._reflection_scopes.clear()
        for index, (tenant_id, installation_id, principal_id) in enumerate(scopes):
            if not self._idle_ready():
                with self._lock:
                    self._reflection_scopes.update(scopes[index:])
                return
            if not self._reflection_has_capacity():
                # Keep sources pending for a later allowance. The installation
                # limit cannot be fixed by reserving every other person's scope.
                # Initiative admission has its own current authority and budget.
                with self._lock:
                    self._reflection_scopes.update(scopes[index:])
                break
            if not self._idle_due():
                # The capacity read can block while a conversation arrives.
                with self._lock:
                    self._reflection_scopes.update(scopes[index:])
                return
            durably_claimed = False
            try:
                admission = None
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
                # The current reservation may have filled the last allowance.
                # Recheck activity, not budget, after its blocking admission.
                if not self._idle_ready(check_capacity=False):
                    with self._lock:
                        self._reflection_scopes.update(scopes[index:])
                    return
                claim = getattr(self._outbox, "claim_reflection_scope", None)
                if callable(claim):
                    if not claim(tenant_id, installation_id, principal_id,
                                 now=datetime.now(tz=UTC).replace(microsecond=0)):
                        continue
                    durably_claimed = True
                if not self._idle_due():
                    continue  # A durable claim is not evidence of provider completion.
                receipt = self._memory.maintain_person_scope(
                    tenant_id, installation_id, principal_id
                )
            except Exception as error:  # retried after the next retained exchange
                with self._lock:
                    self._last_error = f"{type(error).__name__}: {error}"
                    if not durably_claimed:
                        self._reflection_scopes.add(
                            (tenant_id, installation_id, principal_id)
                        )
                continue
            # A settled provider operation must never be repeated because its
            # local receipt could not be stored. Admission and completion are
            # different facts; the existing timeline records the latter too.
            recorder = getattr(self._reflection_budget, 'record_reflection_completed', None)
            if callable(recorder) and admission is not None:
                try:
                    recorder(tenant_id=tenant_id, installation_id=installation_id,
                        principal_id=principal_id, admission=admission, receipt=receipt,
                        now=datetime.now(tz=UTC).replace(microsecond=0))
                except Exception as error:
                    with self._lock:
                        self._last_error = f'reflection completion recording failed: {type(error).__name__}'
        for task in self._idle_tasks:
            # Finish an already started stage, then yield before another
            # optional model run or delivery. Durable tasks retain their state.
            if not self._idle_ready():
                return
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
    "ConversationContentErasurePort",
    "DurableMemoryOutboxPort",
    "HindsightColdPathWorker",
    "MemoryStorageBudgetPort",
    "RestoreAuthorityErasurePort",
    "SemanticMemoryColdPathWorker",
]
