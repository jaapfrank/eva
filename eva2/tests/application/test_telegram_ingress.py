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
from eva.application.memory_audience import TelegramGroupConsentReference


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def _message(
    update_id: int, sender_id: int, text: str | None = "Hoi"
) -> TelegramInboundMessage:
    return TelegramInboundMessage(
        update_id, str(sender_id), update_id + 100, sender_id, NOW, text
    )


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
        def respond(self, message, *, principal_id, audience):  # type: ignore[no-untyped-def]
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
            return TelegramIngressJob(
                job.message, job.principal_id, "reply_prepared", 2, reply
            )

        def fail_processing(self, job, error, *, now):  # type: ignore[no-untyped-def]
            raise AssertionError((job, error, now))

        def claim_delivery(self, job, *, now):  # type: ignore[no-untyped-def]
            return TelegramIngressJob(
                job.message, job.principal_id, "delivery_claimed", 3, job.reply
            )

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


def test_dynamic_private_binding_reaches_normal_core_without_static_fallback() -> None:
    message = _message(10, 303, "Ken je mij nog?")
    core_calls = []
    delivered: list[str] = []

    class Provider:
        def poll(self, *, offset):  # type: ignore[no-untyped-def]
            assert offset == 0
            return (message,)

        def send_reply(self, inbound, text):  # type: ignore[no-untyped-def]
            assert inbound == message
            delivered.append(text)
            return "telegram:303:910"

    class Core:
        def respond(self, inbound, *, principal_id, audience):  # type: ignore[no-untyped-def]
            core_calls.append((inbound, principal_id, audience))
            import hashlib

            text = "Ja, jij bent dezelfde persoon."
            return TelegramCoreReply(
                "telegram:303",
                "telegram:303:110",
                text,
                hashlib.sha256(text.encode()).hexdigest(),
            )

    class Store:
        def next_offset(self):  # type: ignore[no-untyped-def]
            return 0

        def reject(self, inbound, *, reason):  # type: ignore[no-untyped-def]
            raise AssertionError((inbound, reason))

        def admit(self, inbound, *, principal_id):  # type: ignore[no-untyped-def]
            assert principal_id == "person:jaap"
            return TelegramIngressJob(inbound, principal_id, "admitted", 1)

        def prepare_reply(self, job, reply, *, now):  # type: ignore[no-untyped-def]
            return TelegramIngressJob(
                job.message, job.principal_id, "prepared", 2, reply
            )

        def fail_processing(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError((args, kwargs))

        def claim_delivery(self, job, *, now):  # type: ignore[no-untyped-def]
            return TelegramIngressJob(
                job.message, job.principal_id, "claimed", 3, job.reply
            )

        def acknowledge_delivery(self, job, receipt, *, now):  # type: ignore[no-untyped-def]
            assert receipt == "telegram:303:910"

        def mark_delivery_ambiguous(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError((args, kwargs))

    class DynamicBindings:
        def resolve(self, *, telegram_user_id, chat_id):  # type: ignore[no-untyped-def]
            assert (telegram_user_id, chat_id) == (303, "303")
            return "person:jaap"

    static_bindings = TelegramPrincipalBindings(
        (TelegramPrincipalBinding(101, "person:jaap", ("101",)),)
    )
    assert static_bindings.resolve(303, "303") is None
    worker = TelegramIngressWorker(
        Provider(),
        Core(),
        Store(),
        static_bindings,
        dynamic_bindings=DynamicBindings(),
    )  # type: ignore[arg-type]

    assert worker.run_once() == "processed"
    assert len(core_calls) == 1
    assert core_calls[0][1] == "person:jaap"
    assert core_calls[0][2].kind == "private_person"
    assert core_calls[0][2].principal_id == "person:jaap"
    assert delivered == ["Ja, jij bent dezelfde persoon."]


def test_group_only_static_sender_conflicts_with_private_dynamic_binding() -> None:
    message = _message(29, 404, "Dit ben ik privé")
    rejected: list[tuple[int, str]] = []

    class Provider:
        def poll(self, *, offset):  # type: ignore[no-untyped-def]
            return (message,)

        def send_reply(self, inbound, text):  # type: ignore[no-untyped-def]
            raise AssertionError((inbound, text))

    class Core:
        def respond(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError((args, kwargs))

    class Store:
        def next_offset(self):  # type: ignore[no-untyped-def]
            return 0

        def reject(self, inbound, *, reason):  # type: ignore[no-untyped-def]
            rejected.append((inbound.update_id, reason))
            return "rejected"

        def admit(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError((args, kwargs))

    class Pairing:
        def begin(self, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError(kwargs)

        def confirm(self, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError(kwargs)

    class DynamicBindings:
        def resolve(self, *, telegram_user_id, chat_id):  # type: ignore[no-untyped-def]
            assert (telegram_user_id, chat_id) == (404, "404")
            return "person:jaap"

    static_bindings = TelegramPrincipalBindings(
        (TelegramPrincipalBinding(404, "person:frank", ("-42",)),)
    )
    assert static_bindings.resolve_sender(404) == "person:frank"
    assert static_bindings.resolve(404, "404") is None
    worker = TelegramIngressWorker(
        Provider(),
        Core(),
        Store(),
        static_bindings,
        pairing=Pairing(),
        dynamic_bindings=DynamicBindings(),
    )  # type: ignore[arg-type]

    assert worker.run_once() == "processed"
    assert rejected == [(29, "sender_or_chat_not_authorized")]


def test_dynamic_binding_never_grants_group_access_and_conflicts_fail_closed() -> None:
    messages = (
        _message(30, 303, "/link"),
        _message(31, 303, "Privéconflict"),
        TelegramInboundMessage(32, "-42", 132, 303, NOW, "Groepsconflict"),
        TelegramInboundMessage(33, "-42", 133, 404, NOW, "Geen groepsrecht"),
        _message(34, 505, "Onbekend"),
    )
    rejected: list[tuple[int, str]] = []
    resolutions: list[tuple[int, str]] = []

    class Provider:
        def poll(self, *, offset):  # type: ignore[no-untyped-def]
            return messages

        def send_reply(self, message, text):  # type: ignore[no-untyped-def]
            raise AssertionError((message, text))

    class Core:
        def respond(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError((args, kwargs))

    class Pairing:
        def begin(self, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError(kwargs)

        def confirm(self, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError(kwargs)

    class Store:
        def next_offset(self):  # type: ignore[no-untyped-def]
            return 0

        def reject(self, message, *, reason):  # type: ignore[no-untyped-def]
            rejected.append((message.update_id, reason))
            return "rejected"

        def admit(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError((args, kwargs))

    class DynamicBindings:
        def resolve(self, *, telegram_user_id, chat_id):  # type: ignore[no-untyped-def]
            resolutions.append((telegram_user_id, chat_id))
            return {
                303: "person:jaap",
                404: "person:jaap",
            }.get(telegram_user_id)

    worker = TelegramIngressWorker(
        Provider(),
        Core(),
        Store(),
        TelegramPrincipalBindings(
            (
                TelegramPrincipalBinding(303, "person:frank", ("-42", "303")),
                TelegramPrincipalBinding(101, "person:jaap", ("101",)),
            )
        ),
        pairing=Pairing(),
        dynamic_bindings=DynamicBindings(),
    )  # type: ignore[arg-type]

    assert worker.run_once() == "processed"
    assert rejected == [
        (30, "sender_or_chat_not_authorized"),
        (31, "sender_or_chat_not_authorized"),
        (32, "sender_or_chat_not_authorized"),
        (33, "sender_or_chat_not_authorized"),
        (34, "sender_or_chat_not_authorized"),
    ]
    assert resolutions == [
        (303, "303"),
        (303, "303"),
        (303, "303"),
        (404, "404"),
        (505, "505"),
    ]


def test_dynamic_private_binding_does_not_inherit_static_group_consent_scope() -> None:
    message = _message(35, 303, "/share -42")
    delivered: list[str] = []

    class Provider:
        def poll(self, *, offset):  # type: ignore[no-untyped-def]
            return (message,)

        def send_reply(self, inbound, text):  # type: ignore[no-untyped-def]
            delivered.append(text)
            return "telegram:303:935"

    class Core:
        def respond(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError((args, kwargs))

    class Store:
        def next_offset(self):  # type: ignore[no-untyped-def]
            return 0

        def reject(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError((args, kwargs))

        def admit(self, inbound, *, principal_id):  # type: ignore[no-untyped-def]
            return TelegramIngressJob(inbound, principal_id, "admitted", 1)

        def prepare_reply(self, job, reply, *, now):  # type: ignore[no-untyped-def]
            return TelegramIngressJob(
                job.message, job.principal_id, "prepared", 2, reply
            )

        def fail_processing(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError((args, kwargs))

        def claim_delivery(self, job, *, now):  # type: ignore[no-untyped-def]
            return TelegramIngressJob(
                job.message, job.principal_id, "claimed", 3, job.reply
            )

        def acknowledge_delivery(self, job, receipt, *, now):  # type: ignore[no-untyped-def]
            assert receipt == "telegram:303:935"

        def mark_delivery_ambiguous(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError((args, kwargs))

    class ConsentStore:
        def grant(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError((args, kwargs))

        def revoke(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError((args, kwargs))

        def permits(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return False

        def active_reference(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return None

    class DynamicBindings:
        def resolve(self, *, telegram_user_id, chat_id):  # type: ignore[no-untyped-def]
            return (
                "person:jaap" if (telegram_user_id, chat_id) == (303, "303") else None
            )

    from eva.application.telegram_group_consent import TelegramGroupConsentResponder

    worker = TelegramIngressWorker(
        Provider(),
        Core(),
        Store(),
        TelegramPrincipalBindings(
            (TelegramPrincipalBinding(101, "person:jaap", ("-42", "101")),)
        ),
        group_consent=TelegramGroupConsentResponder(ConsentStore()),
        dynamic_bindings=DynamicBindings(),
    )  # type: ignore[arg-type]

    assert worker.run_once() == "processed"
    assert delivered == ["Die groep hoort niet bij jouw gekoppelde Telegram-account."]


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
        def respond(self, message, *, principal_id, audience):  # type: ignore[no-untyped-def]
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
            return TelegramIngressJob(
                job.message, job.principal_id, "prepared", 2, reply
            )

        def fail_processing(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError((args, kwargs))

        def claim_delivery(self, job, *, now):  # type: ignore[no-untyped-def]
            calls.append(("claim", job.message.update_id))
            return TelegramIngressJob(
                job.message, job.principal_id, "claimed", 3, job.reply
            )

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
        assert [
            (name, event)
            for name, event in calls
            if event == update_id
            and name in {"admit", "prepare", "claim", "send", "ack"}
        ] == [
            ("admit", update_id),
            ("prepare", update_id),
            ("claim", update_id),
            ("send", update_id),
            ("ack", update_id),
        ]


def test_group_message_requires_private_person_consent_before_core_or_memory() -> None:
    private = _message(21, 101, "/share -100123")
    group = TelegramInboundMessage(
        22, "-100123", 122, 101, NOW, "Onthoud dit voor de groep"
    )
    calls: list[tuple[str, int | str]] = []
    audiences = []

    class Provider:
        def poll(self, *, offset):  # type: ignore[no-untyped-def]
            return (private, group)

        def send_reply(self, message, text):  # type: ignore[no-untyped-def]
            calls.append(("send", message.update_id))
            return f"telegram:{message.chat_id}:{message.message_id}"

    class Core:
        def respond(self, message, *, principal_id, audience):  # type: ignore[no-untyped-def]
            calls.append(("core", message.update_id))
            audiences.append(audience)
            import hashlib

            return TelegramCoreReply(
                f"telegram:{message.chat_id}",
                f"telegram:{message.chat_id}:{message.message_id}",
                "Groepsantwoord",
                hashlib.sha256(b"Groepsantwoord").hexdigest(),
            )

    class Store:
        def next_offset(self):  # type: ignore[no-untyped-def]
            return 0

        def reject(self, message, *, reason):  # type: ignore[no-untyped-def]
            calls.append((reason, message.update_id))
            return "rejected"

        def admit(self, message, *, principal_id):  # type: ignore[no-untyped-def]
            return TelegramIngressJob(message, principal_id, "admitted", 1)

        def prepare_reply(self, job, reply, *, now):  # type: ignore[no-untyped-def]
            return TelegramIngressJob(
                job.message, job.principal_id, "prepared", 2, reply
            )

        def fail_processing(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError((args, kwargs))

        def claim_delivery(self, job, *, now):  # type: ignore[no-untyped-def]
            return TelegramIngressJob(
                job.message, job.principal_id, "claimed", 3, job.reply
            )

        def acknowledge_delivery(self, job, receipt, *, now):  # type: ignore[no-untyped-def]
            calls.append(("ack", job.message.update_id))

        def mark_delivery_ambiguous(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError((args, kwargs))

    class ConsentStore:
        def __init__(self):
            self.active: tuple[str, str] | None = None
            self.operation_id: str | None = None

        def grant(self, consent, *, operation_id):  # type: ignore[no-untyped-def]
            self.active = (consent.principal_id, consent.group_chat_id)
            self.operation_id = operation_id
            return consent

        def revoke(  # type: ignore[no-untyped-def]
            self, *, principal_id, group_chat_id, operation_id, now
        ):
            if self.active != (principal_id, group_chat_id):
                return False
            self.active = None
            self.operation_id = None
            return True

        def permits(self, *, principal_id, group_chat_id, now):  # type: ignore[no-untyped-def]
            return self.active == (principal_id, group_chat_id)

        def active_reference(self, *, principal_id, group_chat_id, now):  # type: ignore[no-untyped-def]
            if self.active != (principal_id, group_chat_id):
                return None
            assert self.operation_id is not None
            return TelegramGroupConsentReference(
                principal_id=principal_id,
                group_chat_id=group_chat_id,
                operation_id=self.operation_id,
            )

    from eva.application.telegram_group_consent import TelegramGroupConsentResponder

    worker = TelegramIngressWorker(
        Provider(),
        Core(),
        Store(),
        TelegramPrincipalBindings(
            (TelegramPrincipalBinding(101, "person:jaap", ("-100123", "101")),)
        ),
        group_consent=TelegramGroupConsentResponder(ConsentStore()),
    )  # type: ignore[arg-type]

    assert worker.run_once() == "processed"
    assert ("core", 21) not in calls
    assert ("core", 22) in calls
    assert len(audiences) == 1
    assert audiences[0].kind == "telegram_group"
    assert audiences[0].group_chat_id == "-100123"
    assert audiences[0].consent_ref.operation_id == "telegram-group-consent:21"


def test_group_message_without_consent_never_reaches_core() -> None:
    message = TelegramInboundMessage(23, "-100123", 123, 101, NOW, "Hallo groep")
    rejected: list[str] = []

    class Provider:
        def poll(self, *, offset):  # type: ignore[no-untyped-def]
            return (message,)

        def send_reply(self, message, text):  # type: ignore[no-untyped-def]
            raise AssertionError((message, text))

    class Core:
        def respond(self, message, *, principal_id, audience):  # type: ignore[no-untyped-def]
            raise AssertionError((message, principal_id))

    class Store:
        def next_offset(self):  # type: ignore[no-untyped-def]
            return 0

        def reject(self, message, *, reason):  # type: ignore[no-untyped-def]
            raise AssertionError((message, reason))

        def admit(self, message, *, principal_id):  # type: ignore[no-untyped-def]
            return TelegramIngressJob(message, principal_id, "admitted", 1)

        def reject_admitted(self, job, *, reason, now):  # type: ignore[no-untyped-def]
            rejected.append(reason)
            return "rejected"

    worker = TelegramIngressWorker(
        Provider(),
        Core(),
        Store(),
        TelegramPrincipalBindings(
            (TelegramPrincipalBinding(101, "person:jaap", ("-100123", "101")),)
        ),
    )  # type: ignore[arg-type]
    assert worker.run_once() == "processed"
    assert rejected == ["group_consent_required"]
