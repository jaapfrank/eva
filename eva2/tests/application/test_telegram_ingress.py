from __future__ import annotations

from datetime import UTC, datetime

from eva.application.telegram_ingress import (
    TelegramCoreReply,
    TelegramInboundMessage,
    TelegramIngressJob,
    TelegramIngressWorker,
    TelegramPrincipalBinding,
    TelegramPrincipalBindings,
)


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def _message(update_id: int, sender_id: int, text: str | None = "Hoi") -> TelegramInboundMessage:
    return TelegramInboundMessage(update_id, str(sender_id), update_id + 100, sender_id, NOW, text)


def test_worker_binds_each_sender_to_its_own_principal_and_rejects_unknown() -> None:
    messages = (_message(1, 101), _message(2, 202), _message(3, 303))
    calls: list[tuple[int, str]] = []
    rejected: list[tuple[int, str]] = []
    delivered: list[tuple[int, str]] = []

    class Provider:
        def poll(self, *, offset: int):  # type: ignore[no-untyped-def]
            assert offset == 0
            return messages

        def send_reply(self, message, text):  # type: ignore[no-untyped-def]
            delivered.append((message.update_id, text))
            return f"telegram:{message.chat_id}:900{message.update_id}"

    class Core:
        def respond(self, message, *, principal_id):  # type: ignore[no-untyped-def]
            calls.append((message.update_id, principal_id))
            text = f"Dag {principal_id}"
            import hashlib

            return TelegramCoreReply(
                f"telegram:{message.chat_id}",
                f"telegram:{message.chat_id}:{message.message_id}",
                text,
                hashlib.sha256(text.encode()).hexdigest(),
            )

    class Store:
        def next_offset(self):  # type: ignore[no-untyped-def]
            return 0

        def reject(self, message, *, reason):  # type: ignore[no-untyped-def]
            rejected.append((message.update_id, reason))
            return "rejected"

        def admit(self, message, *, principal_id):  # type: ignore[no-untyped-def]
            return TelegramIngressJob(message, principal_id, "ingress_admitted", 1)

        def prepare_reply(self, job, reply, *, now):  # type: ignore[no-untyped-def]
            return TelegramIngressJob(job.message, job.principal_id, "reply_prepared", 2, reply)

        def fail_processing(self, job, error, *, now):  # type: ignore[no-untyped-def]
            raise AssertionError((job, error, now))

        def claim_delivery(self, job, *, now):  # type: ignore[no-untyped-def]
            return TelegramIngressJob(job.message, job.principal_id, "delivery_claimed", 3, job.reply)

        def acknowledge_delivery(self, job, receipt, *, now):  # type: ignore[no-untyped-def]
            assert receipt.endswith(f"900{job.message.update_id}")

        def mark_delivery_ambiguous(self, job, error, *, now):  # type: ignore[no-untyped-def]
            raise AssertionError((job, error, now))

    bindings = TelegramPrincipalBindings(
        (
            TelegramPrincipalBinding(101, "person:jaap", ("101",)),
            TelegramPrincipalBinding(202, "person:frank", ("202",)),
        )
    )
    worker = TelegramIngressWorker(Provider(), Core(), Store(), bindings)  # type: ignore[arg-type]

    assert worker.run_once() == "processed"
    assert calls == [(1, "person:jaap"), (2, "person:frank")]
    assert delivered == [(1, "Dag person:jaap"), (2, "Dag person:frank")]
    assert rejected == [(3, "sender_or_chat_not_authorized")]


def test_binding_requires_both_trusted_sender_and_allowed_chat() -> None:
    bindings = TelegramPrincipalBindings(
        (TelegramPrincipalBinding(101, "person:jaap", ("-42", "101")),)
    )

    assert bindings.resolve(101, "101") == "person:jaap"
    assert bindings.resolve(101, "-42") == "person:jaap"
    assert bindings.resolve(101, "999") is None
    assert bindings.resolve(202, "101") is None


def test_one_person_can_bind_multiple_distinct_telegram_accounts() -> None:
    bindings = TelegramPrincipalBindings(
        (
            TelegramPrincipalBinding(101, "person:jaap", ("101",)),
            TelegramPrincipalBinding(404, "person:jaap", ("404",)),
            TelegramPrincipalBinding(202, "person:frank", ("202",)),
        )
    )

    assert bindings.resolve(101, "101") == "person:jaap"
    assert bindings.resolve(404, "404") == "person:jaap"
    assert bindings.resolve(202, "202") == "person:frank"
    assert bindings.resolve(404, "101") is None


def test_pairing_commands_bypass_core_but_use_the_normal_delivery_ledger() -> None:
    messages = (
        _message(11, 303, "/link"),
        _message(12, 101, "/link EVA-ABCDEFGHJKLM"),
        _message(13, 101, "Hoe gaat het?"),
    )
    calls: list[tuple[str, int]] = []

    class Provider:
        def poll(self, *, offset):  # type: ignore[no-untyped-def]
            return messages

        def send_reply(self, message, text):  # type: ignore[no-untyped-def]
            calls.append(("send", message.update_id))
            return f"telegram:{message.chat_id}:{message.message_id}"

    class Core:
        def respond(self, message, *, principal_id):  # type: ignore[no-untyped-def]
            calls.append(("core", message.update_id))
            import hashlib

            text = "Normaal antwoord"
            return TelegramCoreReply(
                f"telegram:{message.chat_id}",
                f"telegram:{message.chat_id}:{message.message_id}",
                text,
                hashlib.sha256(text.encode()).hexdigest(),
            )

    class Store:
        def next_offset(self):  # type: ignore[no-untyped-def]
            return 0

        def reject(self, message, *, reason):  # type: ignore[no-untyped-def]
            raise AssertionError((message, reason))

        def admit(self, message, *, principal_id):  # type: ignore[no-untyped-def]
            calls.append(("admit", message.update_id))
            return TelegramIngressJob(message, principal_id, "admitted", 1)

        def prepare_reply(self, job, reply, *, now):  # type: ignore[no-untyped-def]
            calls.append(("prepare", job.message.update_id))
            return TelegramIngressJob(job.message, job.principal_id, "prepared", 2, reply)

        def fail_processing(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError((args, kwargs))

        def claim_delivery(self, job, *, now):  # type: ignore[no-untyped-def]
            calls.append(("claim", job.message.update_id))
            return TelegramIngressJob(job.message, job.principal_id, "claimed", 3, job.reply)

        def acknowledge_delivery(self, job, receipt, *, now):  # type: ignore[no-untyped-def]
            calls.append(("ack", job.message.update_id))

        def mark_delivery_ambiguous(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError((args, kwargs))

    class Pairing:
        def begin(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(("begin", kwargs["update_id"]))
            return "Koppelcode"

        def confirm(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(("confirm", kwargs["sender_id"]))
            return "Gekoppeld."

    bindings = TelegramPrincipalBindings(
        (TelegramPrincipalBinding(101, "person:jaap", ("101",)),)
    )
    worker = TelegramIngressWorker(Provider(), Core(), Store(), bindings, Pairing())  # type: ignore[arg-type]

    assert worker.run_once() == "processed"
    assert ("core", 11) not in calls and ("core", 12) not in calls
    assert calls.count(("core", 13)) == 1
    for update_id in (11, 12, 13):
        assert [(name, event) for name, event in calls if event == update_id and name in {"admit", "prepare", "claim", "send", "ack"}] == [
            ("admit", update_id), ("prepare", update_id), ("claim", update_id), ("send", update_id), ("ack", update_id)
        ]
