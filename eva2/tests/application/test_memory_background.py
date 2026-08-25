from __future__ import annotations

from threading import Event
import time
from dataclasses import replace

from eva.application.memory import ConversationMemoryDocument
from eva.application.memory import MemoryRetentionReceipt
from eva.application.memory_audience import (
    MemoryAudience,
    TelegramGroupConsentReference,
)
from eva.application.memory_background import HindsightColdPathWorker


def _document() -> ConversationMemoryDocument:
    return ConversationMemoryDocument(
        tenant_id="tenant",
        installation_id="installation",
        principal_id="jaap",
        conversation_id="telegram:jaap",
        document_id="conversation:1",
        content="Gebruiker: hoi\nEva: hoi Jaap",
        occurred_at="2026-08-23T14:00:00Z",
        source_ref="telegram:1",
        evidence_ref="conversation-reply:1",
        privacy_class="internal",
        trust="direct_user",
        audience=MemoryAudience.private_person(principal_id="jaap"),
    )


def test_retain_is_fire_and_forget_and_reflect_waits_for_idle() -> None:
    retain_entered = Event()
    release_retain = Event()
    reflections: list[tuple[str, str, str]] = []

    class Memory:
        def retain_conversation(self, document):  # type: ignore[no-untyped-def]
            retain_entered.set()
            assert release_retain.wait(1)

        def maintain_person_scope(self, tenant, installation, principal):  # type: ignore[no-untyped-def]
            reflections.append((tenant, installation, principal))

    worker = HindsightColdPathWorker(Memory(), idle_seconds=0.05, retry_delay_seconds=0)
    worker.start()
    started = time.monotonic()
    worker.submit(_document())
    assert time.monotonic() - started < 0.02
    assert retain_entered.wait(0.5)
    assert reflections == []
    release_retain.set()
    deadline = time.monotonic() + 1
    while not reflections and time.monotonic() < deadline:
        time.sleep(0.01)
    worker.stop()

    assert reflections == [("tenant", "installation", "jaap")]


def test_group_memory_never_schedules_a_person_style_reflection() -> None:
    retained = Event()
    reflections: list[tuple[str, str, str]] = []

    class Memory:
        def retain_conversation(self, document):  # type: ignore[no-untyped-def]
            retained.set()
            return MemoryRetentionReceipt(
                bank_id="test-bank",
                document_id=document.document_id,
                content_sha256="a" * 64,
            )

        def maintain_person_scope(self, tenant, installation, principal):  # type: ignore[no-untyped-def]
            reflections.append((tenant, installation, principal))

    document = replace(
        _document(),
        conversation_id="telegram:-100123",
        audience=MemoryAudience.telegram_group(
            principal_id="jaap",
            group_chat_id="-100123",
            consent_ref=TelegramGroupConsentReference(
                principal_id="jaap",
                group_chat_id="-100123",
                operation_id="telegram-group-consent:22",
            ),
        ),
    )
    worker = HindsightColdPathWorker(Memory(), idle_seconds=0.03, retry_delay_seconds=0)
    worker.start()
    worker.submit(document)
    assert retained.wait(0.5)
    time.sleep(0.08)
    worker.stop()

    assert reflections == []


def test_poison_retain_stops_after_three_attempts() -> None:
    attempts = 0

    class Memory:
        def retain_conversation(self, document):  # type: ignore[no-untyped-def]
            nonlocal attempts
            attempts += 1
            raise RuntimeError("bad memory input")

        def maintain_person_scope(self, *scope):  # type: ignore[no-untyped-def]
            raise AssertionError("failed retain must not trigger reflection")

    worker = HindsightColdPathWorker(Memory(), idle_seconds=60, retry_delay_seconds=0)
    worker.start()
    worker.submit(_document())
    deadline = time.monotonic() + 1
    while attempts < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    worker.stop()

    assert attempts == 3
    assert worker.last_error == "RuntimeError: bad memory input"


def test_durable_pending_job_is_rehydrated_on_worker_start() -> None:
    retained = Event()
    succeeded = Event()
    document = _document()

    class State:
        status = "waiting_for_proof"

    class Job:
        def __init__(self) -> None:
            self.document = document
            self.state = State()

    job = Job()

    class Outbox:
        calls = 0

        def pending(self, *, limit=256):  # type: ignore[no-untyped-def]
            assert limit == 256
            self.calls += 1
            return (job,) if self.calls == 1 else ()

        def enqueue(self, submitted):  # type: ignore[no-untyped-def]
            raise AssertionError("startup recovery must not enqueue again")

        def begin(self, pending, *, now=None):  # type: ignore[no-untyped-def]
            assert pending is job
            assert now is not None
            return pending

        def succeed(self, pending, receipt, *, now=None):  # type: ignore[no-untyped-def]
            assert pending is job
            assert receipt.document_id == document.document_id
            assert now is not None
            succeeded.set()
            return State()

        def fail(self, pending, error, *, now=None):  # type: ignore[no-untyped-def]
            raise AssertionError("healthy retain must not fail")

    class Memory:
        def retain_conversation(self, submitted):  # type: ignore[no-untyped-def]
            assert submitted == document
            retained.set()
            return MemoryRetentionReceipt(
                bank_id="bank",
                document_id=document.document_id,
                content_sha256=(
                    "f463c6c92e8ece20a2151f24ebf174886d150f47cc8b1ae86a837027c25936a4"
                ),
            )

        def maintain_person_scope(self, *scope):  # type: ignore[no-untyped-def]
            return None

    worker = HindsightColdPathWorker(
        Memory(),
        outbox=Outbox(),  # type: ignore[arg-type]
        idle_seconds=60,
        retry_delay_seconds=0,
    )
    worker.start()
    assert retained.wait(1)
    assert succeeded.wait(1)
    worker.stop()
