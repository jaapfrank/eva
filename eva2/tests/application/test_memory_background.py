from __future__ import annotations

import ast
from datetime import UTC, datetime
import hashlib
from pathlib import Path
from threading import Event
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from eva.application.memory import (
    BudgetedConversationMemoryWriter,
    ConversationMemoryDocument,
    MemoryRetentionReceipt,
)
from eva.application.memory_audience import (
    MemoryAudience,
    TelegramGroupConsentReference,
)
from eva.application.memory_background import HindsightColdPathWorker


class _RestoreAuthority:
    def __init__(self, calls: list[str], error: Exception | None = None) -> None:
        self._calls = calls
        self._error = error

    def advance_erasure_generation(self, **kwargs):  # type: ignore[no-untyped-def]
        self._calls.append("authority")
        if self._error is not None:
            raise self._error
        return SimpleNamespace(
            authority_sha256="a" * 64,
            erasure_generation=1,
            operation_sha256=kwargs["operation_sha256"],
            replayed=False,
        )


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


def test_no_production_memory_producer_calls_the_raw_provider_directly() -> None:
    source_root = Path(__file__).resolve().parents[2] / "src" / "eva"
    allowed_raw_boundaries = {
        Path("application/memory.py"),
        Path("application/memory_background.py"),
    }
    bypasses: list[str] = []

    for source_path in sorted(source_root.rglob("*.py")):
        relative_path = source_path.relative_to(source_root)
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=source_path)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "retain_conversation"
                and relative_path not in allowed_raw_boundaries
            ):
                bypasses.append(f"{relative_path}:{node.lineno}")

    assert bypasses == []


def test_budgeted_writer_blocks_before_the_raw_memory_provider() -> None:
    calls: list[str] = []
    now = datetime(2026, 8, 29, 7, 0, tzinfo=UTC)

    class Budget:
        def admit_memory(self, document, *, now):  # type: ignore[no-untyped-def]
            calls.append("admit")
            return SimpleNamespace(
                admitted=False,
                retention_until=None,
                reason="person_record_limit_reached",
            )

        def mark_retained(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("blocked memory cannot be marked retained")

    class Memory:
        def retain_conversation(self, document):  # type: ignore[no-untyped-def]
            calls.append("provider")
            raise AssertionError("blocked memory cannot reach Hindsight")

    writer = BudgetedConversationMemoryWriter(
        Memory(),  # type: ignore[arg-type]
        Budget(),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="person_record_limit_reached"):
        writer.retain_bounded(_document(), now=now)

    assert calls == ["admit"]


def test_budgeted_writer_sets_retention_and_marks_only_after_provider_success() -> None:
    calls: list[str] = []
    now = datetime(2026, 8, 29, 7, 0, tzinfo=UTC)
    document = _document()
    receipt = MemoryRetentionReceipt(
        bank_id="eva2-bank",
        document_id=document.document_id,
        content_sha256="a" * 64,
    )

    class Budget:
        def admit_memory(self, submitted, *, now):  # type: ignore[no-untyped-def]
            assert submitted == document
            calls.append("admit")
            return SimpleNamespace(
                admitted=True,
                retention_until="2026-09-05T07:00:00Z",
                reason="reserved",
            )

        def mark_retained(self, submitted, stored, *, now):  # type: ignore[no-untyped-def]
            assert submitted.retention_until == "2026-09-05T07:00:00Z"
            assert stored is receipt
            calls.append("marked")

    class Memory:
        def retain_conversation(self, submitted):  # type: ignore[no-untyped-def]
            assert submitted.retention_until == "2026-09-05T07:00:00Z"
            calls.append("provider")
            return receipt

    writer = BudgetedConversationMemoryWriter(
        Memory(),  # type: ignore[arg-type]
        Budget(),  # type: ignore[arg-type]
    )

    assert writer.retain_bounded(document, now=now) is receipt
    assert calls == ["admit", "provider", "marked"]


def test_budgeted_writer_does_not_mark_a_failed_provider_write() -> None:
    calls: list[str] = []
    now = datetime(2026, 8, 29, 7, 0, tzinfo=UTC)

    class Budget:
        def admit_memory(self, document, *, now):  # type: ignore[no-untyped-def]
            calls.append("admit")
            return SimpleNamespace(
                admitted=True,
                retention_until="2026-09-05T07:00:00Z",
                reason="reserved",
            )

        def mark_retained(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            calls.append("marked")

    class Memory:
        def retain_conversation(self, submitted):  # type: ignore[no-untyped-def]
            assert submitted.retention_until == "2026-09-05T07:00:00Z"
            calls.append("provider")
            raise RuntimeError("temporary Hindsight failure")

    writer = BudgetedConversationMemoryWriter(
        Memory(),  # type: ignore[arg-type]
        Budget(),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="temporary Hindsight failure"):
        writer.retain_bounded(_document(), now=now)

    assert calls == ["admit", "provider"]


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


def test_daily_budget_blocks_reflection_before_memory_provider() -> None:
    retained = Event()
    reflections: list[tuple[str, str, str]] = []
    admissions: list[tuple[str, str, str]] = []

    class Admission:
        admitted = False

    class Budget:
        def admit_background_reflection(
            self, *, tenant_id, installation_id, principal_id, now
        ):  # type: ignore[no-untyped-def]
            assert now.tzinfo is not None
            admissions.append((tenant_id, installation_id, principal_id))
            return Admission()

    class Memory:
        def retain_conversation(self, document):  # type: ignore[no-untyped-def]
            retained.set()
            return MemoryRetentionReceipt(
                bank_id="test-bank",
                document_id=document.document_id,
                content_sha256=(
                    "f463c6c92e8ece20a2151f24ebf174886d150f47cc8b1ae86a837027c25936a4"
                ),
            )

        def maintain_person_scope(self, tenant, installation, principal):  # type: ignore[no-untyped-def]
            reflections.append((tenant, installation, principal))

    worker = HindsightColdPathWorker(
        Memory(),
        idle_seconds=0.03,
        retry_delay_seconds=0,
        reflection_budget=Budget(),  # type: ignore[arg-type]
    )
    worker.start()
    worker.submit(_document())
    assert retained.wait(0.5)
    time.sleep(0.08)
    worker.stop()

    assert admissions == [("tenant", "installation", "jaap")]
    assert reflections == []


def test_storage_budget_blocks_before_outbox_and_provider() -> None:
    calls: list[str] = []

    class Budget:
        def admit_memory(self, document, *, now):  # type: ignore[no-untyped-def]
            assert document == _document()
            assert now.tzinfo is not None
            return SimpleNamespace(
                admitted=False,
                retention_until=None,
                reason="person_byte_limit_reached",
            )

        def mark_retained(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("blocked memory cannot be retained")

        def claim_expired(self, *, now, limit=64):  # type: ignore[no-untyped-def]
            return ()

        def mark_removed(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("blocked memory cannot be removed")

    class Outbox:
        def pending(self, *, limit=256):  # type: ignore[no-untyped-def]
            return ()

        def enqueue(self, document):  # type: ignore[no-untyped-def]
            calls.append("outbox")

    class Memory:
        def retain_conversation(self, document):  # type: ignore[no-untyped-def]
            calls.append("provider")

        def maintain_person_scope(self, *scope):  # type: ignore[no-untyped-def]
            return None

    worker = HindsightColdPathWorker(
        Memory(),
        outbox=Outbox(),  # type: ignore[arg-type]
        storage_budget=Budget(),  # type: ignore[arg-type]
        idle_seconds=60,
    )
    decision = worker.submit(_document())

    assert getattr(decision, "admitted") is False
    assert calls == []


def test_storage_budget_marks_retained_then_removes_expired_document() -> None:
    retained = Event()
    removed = Event()
    marked: list[str] = []
    document = _document()
    principal_sha256 = hashlib.sha256(document.principal_id.encode("utf-8")).hexdigest()
    expiry = SimpleNamespace(
        bank_id="eva2-bank",
        document_id=document.document_id,
        principal_sha256=principal_sha256,
    )

    class Budget:
        expiry_returned = False

        def admit_memory(self, submitted, *, now):  # type: ignore[no-untyped-def]
            assert submitted == document
            return SimpleNamespace(
                admitted=True,
                retention_until="2026-09-01T14:00:00Z",
                reason="reserved",
            )

        def mark_retained(self, submitted, receipt, *, now):  # type: ignore[no-untyped-def]
            assert submitted.retention_until == "2026-09-01T14:00:00Z"
            assert receipt.document_id == document.document_id
            marked.append("retained")

        def claim_expired(self, *, now, limit=64):  # type: ignore[no-untyped-def]
            if not retained.is_set() or self.expiry_returned:
                return ()
            self.expiry_returned = True
            return (expiry,)

        def mark_removed(self, job, *, now):  # type: ignore[no-untyped-def]
            assert job is expiry
            marked.append("removed")
            removed.set()

    class Memory:
        def retain_conversation(self, submitted):  # type: ignore[no-untyped-def]
            retained.set()
            return MemoryRetentionReceipt(
                bank_id="eva2-bank",
                document_id=submitted.document_id,
                content_sha256=(
                    "f463c6c92e8ece20a2151f24ebf174886d150f47cc8b1ae86a837027c25936a4"
                ),
            )

        def remove_conversation_document(  # type: ignore[no-untyped-def]
            self, *, bank_id, document_id, principal_sha256
        ):
            assert (bank_id, document_id, principal_sha256) == (
                "eva2-bank",
                document.document_id,
                hashlib.sha256(document.principal_id.encode("utf-8")).hexdigest(),
            )

        def maintain_person_scope(self, *scope):  # type: ignore[no-untyped-def]
            return None

    worker = HindsightColdPathWorker(
        Memory(),
        storage_budget=Budget(),  # type: ignore[arg-type]
        idle_seconds=0.03,
        retry_delay_seconds=0,
    )
    worker.start()
    worker.submit(document)
    assert retained.wait(0.5)
    assert removed.wait(1)
    worker.stop()

    assert marked == ["retained", "removed"]


def test_explicit_erasure_calls_provider_once_and_marks_only_after_ack() -> None:
    calls: list[str] = []
    principal_sha256 = hashlib.sha256(b"jaap").hexdigest()
    job = SimpleNamespace(
        bank_id="eva2-bank",
        document_id="conversation:1",
        principal_sha256=principal_sha256,
    )
    now = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)

    class Budget:
        returned = False

        def request_erasure(self, *, principal_id, document_id, now):  # type: ignore[no-untyped-def]
            assert (principal_id, document_id) == ("jaap", "conversation:1")
            if self.returned:
                return None
            self.returned = True
            calls.append("leased")
            return job

        def mark_removed(self, removed_job, *, now):  # type: ignore[no-untyped-def]
            assert removed_job is job
            calls.append("marked")

    class Memory:
        def remove_conversation_document(  # type: ignore[no-untyped-def]
            self, *, bank_id, document_id, principal_sha256
        ):
            assert (bank_id, document_id, principal_sha256) == (
                "eva2-bank",
                "conversation:1",
                hashlib.sha256(b"jaap").hexdigest(),
            )
            calls.append("provider")

    worker = HindsightColdPathWorker(
        Memory(),  # type: ignore[arg-type]
        storage_budget=Budget(),  # type: ignore[arg-type]
    )

    assert worker.erase_document(
        principal_id="jaap", document_id="conversation:1", now=now
    )
    assert not worker.erase_document(
        principal_id="jaap", document_id="conversation:1", now=now
    )
    assert calls == ["leased", "provider", "marked"]


def test_explicit_erasure_removes_semantic_and_raw_content_before_marking() -> None:
    calls: list[str] = []
    principal_id = "person:jaap"
    source_message_id = "telegram:jaap:41"
    principal_sha256 = hashlib.sha256(principal_id.encode("utf-8")).hexdigest()
    message_sha256 = hashlib.sha256(source_message_id.encode("utf-8")).hexdigest()
    job = SimpleNamespace(
        bank_id="eva2-bank",
        document_id="conversation:1",
        principal_sha256=principal_sha256,
        source_message_id=source_message_id,
    )
    now = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)

    class Budget:
        def request_erasure(self, **kwargs):  # type: ignore[no-untyped-def]
            assert kwargs == {
                "document_id": "conversation:1",
                "now": now,
                "principal_id": principal_id,
                "source_message_id": source_message_id,
            }
            calls.append("leased")
            return job

        def mark_removed(self, removed_job, *, now):  # type: ignore[no-untyped-def]
            assert removed_job is job
            calls.append("marked")

    class Memory:
        def remove_conversation_document(self, **_kwargs):  # type: ignore[no-untyped-def]
            calls.append("provider")

    class RawConversation:
        def erase_message_content(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append("raw")
            return SimpleNamespace(
                operation_sha256=kwargs["operation_sha256"],
                principal_sha256=principal_sha256,
                message_sha256=message_sha256,
                redacted_event_count=2,
                replayed=False,
            )

    class Outbox:
        def erase_content(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append("outbox")
            return SimpleNamespace(
                operation_sha256=kwargs["operation_sha256"],
                principal_sha256=principal_sha256,
                document_sha256=hashlib.sha256(b"conversation:1").hexdigest(),
                redacted_event_count=1,
                replayed=False,
            )

    worker = HindsightColdPathWorker(
        Memory(),  # type: ignore[arg-type]
        outbox=Outbox(),  # type: ignore[arg-type]
        storage_budget=Budget(),  # type: ignore[arg-type]
        conversation_erasure=RawConversation(),  # type: ignore[arg-type]
        restore_authority_erasure=_RestoreAuthority(calls),  # type: ignore[arg-type]
    )
    assert worker.erase_document(
        principal_id=principal_id,
        document_id="conversation:1",
        source_message_id=source_message_id,
        now=now,
    )
    assert calls == ["leased", "authority", "provider", "raw", "outbox", "marked"]


def test_restore_authority_failure_stops_before_every_content_deletion() -> None:
    calls: list[str] = []
    principal_id = "person:jaap"
    source_message_id = "telegram:jaap:41"
    job = SimpleNamespace(
        bank_id="eva2-bank",
        document_id="conversation:1",
        principal_sha256=hashlib.sha256(principal_id.encode()).hexdigest(),
        source_message_id=source_message_id,
    )

    class Budget:
        def request_erasure(self, **_kwargs):  # type: ignore[no-untyped-def]
            calls.append("leased")
            return job

        def mark_removed(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            calls.append("marked")

    class Memory:
        def remove_conversation_document(self, **_kwargs):  # type: ignore[no-untyped-def]
            calls.append("provider")

    class RawConversation:
        def erase_message_content(self, **_kwargs):  # type: ignore[no-untyped-def]
            calls.append("raw")

    worker = HindsightColdPathWorker(
        Memory(),  # type: ignore[arg-type]
        storage_budget=Budget(),  # type: ignore[arg-type]
        conversation_erasure=RawConversation(),  # type: ignore[arg-type]
        restore_authority_erasure=_RestoreAuthority(
            calls, RuntimeError("authority storage unavailable")
        ),  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="authority storage unavailable"):
        worker.erase_document(
            principal_id=principal_id,
            document_id="conversation:1",
            source_message_id=source_message_id,
            now=datetime(2026, 8, 29, 10, 0, tzinfo=UTC),
        )
    assert calls == ["leased", "authority"]


def test_raw_erasure_failure_keeps_the_durable_lease_unfinished() -> None:
    calls: list[str] = []
    principal_id = "person:jaap"
    job = SimpleNamespace(
        bank_id="eva2-bank",
        document_id="conversation:1",
        principal_sha256=hashlib.sha256(principal_id.encode("utf-8")).hexdigest(),
        source_message_id="telegram:jaap:41",
    )

    class Budget:
        def request_erasure(self, **_kwargs):  # type: ignore[no-untyped-def]
            calls.append("leased")
            return job

        def mark_removed(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            calls.append("marked")

    class Memory:
        def remove_conversation_document(self, **_kwargs):  # type: ignore[no-untyped-def]
            calls.append("provider")

    class RawConversation:
        def erase_message_content(self, **_kwargs):  # type: ignore[no-untyped-def]
            calls.append("raw")
            raise RuntimeError("raw journal unavailable")

    worker = HindsightColdPathWorker(
        Memory(),  # type: ignore[arg-type]
        storage_budget=Budget(),  # type: ignore[arg-type]
        conversation_erasure=RawConversation(),  # type: ignore[arg-type]
        restore_authority_erasure=_RestoreAuthority(calls),  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="raw journal unavailable"):
        worker.erase_document(
            principal_id=principal_id,
            document_id="conversation:1",
            source_message_id="telegram:jaap:41",
            now=datetime(2026, 8, 29, 10, 0, tzinfo=UTC),
        )
    assert calls == ["leased", "authority", "provider", "raw"]


def test_outbox_erasure_failure_keeps_the_durable_lease_unfinished() -> None:
    calls: list[str] = []
    principal_id = "person:jaap"
    source_message_id = "telegram:jaap:41"
    principal_sha256 = hashlib.sha256(principal_id.encode("utf-8")).hexdigest()
    job = SimpleNamespace(
        bank_id="eva2-bank",
        document_id="conversation:1",
        principal_sha256=principal_sha256,
        source_message_id=source_message_id,
    )

    class Budget:
        def request_erasure(self, **_kwargs):  # type: ignore[no-untyped-def]
            calls.append("leased")
            return job

        def mark_removed(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            calls.append("marked")

    class Memory:
        def remove_conversation_document(self, **_kwargs):  # type: ignore[no-untyped-def]
            calls.append("provider")

    class RawConversation:
        def erase_message_content(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append("raw")
            return SimpleNamespace(
                operation_sha256=kwargs["operation_sha256"],
                principal_sha256=principal_sha256,
                message_sha256=hashlib.sha256(source_message_id.encode()).hexdigest(),
                redacted_event_count=2,
                replayed=False,
            )

    class Outbox:
        def erase_content(self, **_kwargs):  # type: ignore[no-untyped-def]
            calls.append("outbox")
            raise RuntimeError("outbox unavailable")

    worker = HindsightColdPathWorker(
        Memory(),  # type: ignore[arg-type]
        outbox=Outbox(),  # type: ignore[arg-type]
        storage_budget=Budget(),  # type: ignore[arg-type]
        conversation_erasure=RawConversation(),  # type: ignore[arg-type]
        restore_authority_erasure=_RestoreAuthority(calls),  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        worker.erase_document(
            principal_id=principal_id,
            document_id="conversation:1",
            source_message_id=source_message_id,
            now=datetime(2026, 8, 29, 10, 0, tzinfo=UTC),
        )
    assert calls == ["leased", "authority", "provider", "raw", "outbox"]


def test_explicit_erasure_keeps_durable_lease_when_provider_fails() -> None:
    calls: list[str] = []
    job = SimpleNamespace(
        bank_id="eva2-bank",
        document_id="conversation:1",
        principal_sha256=hashlib.sha256(b"jaap").hexdigest(),
    )

    class Budget:
        def request_erasure(self, **_kwargs):  # type: ignore[no-untyped-def]
            calls.append("leased")
            return job

        def mark_removed(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            calls.append("marked")

    class Memory:
        def remove_conversation_document(self, **_kwargs):  # type: ignore[no-untyped-def]
            calls.append("provider")
            raise RuntimeError("temporary delete failure")

    worker = HindsightColdPathWorker(
        Memory(),  # type: ignore[arg-type]
        storage_budget=Budget(),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="temporary delete failure"):
        worker.erase_document(
            principal_id="jaap",
            document_id="conversation:1",
            now=datetime(2026, 8, 29, 10, 0, tzinfo=UTC),
        )
    assert calls == ["leased", "provider"]


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
