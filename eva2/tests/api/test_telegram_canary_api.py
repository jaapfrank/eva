from __future__ import annotations

from datetime import UTC, datetime
import json

from fastapi.testclient import TestClient
import pytest

from eva.adapters.api.app import create_app
from eva.application.conversation import (
    ConversationTurn,
    GroundedReply,
    publish_conversation_text,
)
from eva.application.memory_audience import TelegramGroupConsentReference
from eva.application.telegram_canary import TelegramCanaryBridge


NOW = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)


class _Observations:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], datetime] = {}

    def observe(self, **values) -> None:  # type: ignore[no-untyped-def]
        self.items[(values["principal_id"], values["group_chat_id"])] = values[
            "expires_at"
        ]

    def recently_observed(self, **values) -> bool:  # type: ignore[no-untyped-def]
        expires = self.items.get((values["principal_id"], values["group_chat_id"]))
        return expires is not None and values["now"] <= expires


class _Consent:
    def __init__(self) -> None:
        self.active: dict[tuple[str, str], object] = {}

    def grant(self, consent, *, operation_id):  # type: ignore[no-untyped-def]
        self.active[(consent.principal_id, consent.group_chat_id)] = consent
        return consent

    def revoke(self, *, principal_id, group_chat_id, operation_id, now):  # type: ignore[no-untyped-def]
        return self.active.pop((principal_id, group_chat_id), None) is not None

    def permits(self, *, principal_id, group_chat_id, now):  # type: ignore[no-untyped-def]
        return self.active_reference(
            principal_id=principal_id, group_chat_id=group_chat_id, now=now
        ) is not None

    def active_reference(self, *, principal_id, group_chat_id, now):  # type: ignore[no-untyped-def]
        consent = self.active.get((principal_id, group_chat_id))
        if consent is None or not consent.permits(  # type: ignore[attr-defined]
            principal_id=principal_id, group_chat_id=group_chat_id, now=now
        ):
            return None
        return TelegramGroupConsentReference(
            principal_id, group_chat_id, "telegram-group-consent:api"
        )


class _Runtime:
    def __init__(self) -> None:
        self.bridge = TelegramCanaryBridge(
            observations=_Observations(), group_consent=_Consent()
        )
        self.turns: list[ConversationTurn] = []
        self.recent: list[tuple[object, ...]] = []
        self.after: list[ConversationTurn] = []

    def admit_telegram_canary(self, **values):  # type: ignore[no-untyped-def]
        return self.bridge.admit(**values)

    def admit_turn_audience(self, turn: ConversationTurn) -> None:
        if turn.audience.kind == "telegram_group" and turn.audience.consent_ref is None:
            raise ValueError("group consent missing")

    def respond(self, turn, recent_messages=()):  # type: ignore[no-untyped-def]
        self.turns.append(turn)
        self.recent.append(recent_messages)
        return GroundedReply(
            turn.conversation_id,
            turn.message_id,
            "Veilig antwoord.",
            (),
            turn.occurred_at,
            "test-model",
        )

    def after_response(self, turn, reply):  # type: ignore[no-untyped-def]
        self.after.append(turn)


def _payload(*, chat_id: str = "7", message_id: int = 1, text: str = "Hoi"):
    return {
        "chat_id": chat_id,
        "installation_id": "home",
        "message_id": message_id,
        "occurred_at": "2026-08-28T10:00:00Z",
        "recent_messages": [],
        "telegram_user_id": 7,
        "tenant_id": "family",
        "text": text,
    }


def test_private_canary_derives_principal_server_side() -> None:
    runtime = _Runtime()
    response = TestClient(create_app(runtime)).post(
        "/internal/v1/telegram/canary/respond", json=_payload()
    )
    assert response.status_code == 200
    assert runtime.turns[0].principal_id == "telegram:7"
    assert runtime.turns[0].audience.audience_id == "person:telegram:7"
    assert response.json()["provider"] == "test-model"


def test_private_canary_handles_owner_work_command_before_model_or_stream() -> None:
    class CommandRuntime(_Runtime):
        def respond_telegram_delegated_work(
            self, turn, *, telegram_user_id, chat_id
        ):  # type: ignore[no-untyped-def]
            assert telegram_user_id == 7
            assert chat_id == "7"
            return GroundedReply(
                turn.conversation_id,
                turn.message_id,
                "Onderzoek ingepland.",
                (),
                turn.occurred_at,
                "eva-delegated-work-command",
            )

    runtime = CommandRuntime()
    response = TestClient(create_app(runtime)).post(
        "/internal/v1/telegram/canary/respond",
        headers={"accept": "application/x-ndjson"},
        json=_payload(text="Laat een onderzoeker de release controleren"),
    )
    assert response.status_code == 200
    assert response.json()["provider"] == "eva-delegated-work-command"
    assert runtime.turns == []
    assert runtime.after == []


def test_private_canary_streams_safe_deltas_then_the_durable_final_reply() -> None:
    class StreamingRuntime(_Runtime):
        def respond(self, turn, recent_messages=()):  # type: ignore[no-untyped-def]
            publish_conversation_text("Veilig ", tools_disabled=True)
            publish_conversation_text("antwoord.", tools_disabled=True)
            return super().respond(turn, recent_messages)

    runtime = StreamingRuntime()
    response = TestClient(create_app(runtime)).post(
        "/internal/v1/telegram/canary/respond",
        headers={"accept": "application/x-ndjson"},
        json=_payload(),
    )
    rows = [json.loads(line) for line in response.iter_lines()]
    assert [row["contract"] for row in rows] == [
        "EvaTextDelta/v1",
        "EvaTextDelta/v1",
        "EvaInternalGroundedReply/v1",
    ]
    assert "".join(row["text"] for row in rows[:-1]) == rows[-1]["text"]
    assert runtime.after == runtime.turns


def test_stream_sink_never_emits_unmarked_tool_text() -> None:
    class ToolRuntime(_Runtime):
        def respond(self, turn, recent_messages=()):  # type: ignore[no-untyped-def]
            publish_conversation_text('{"target":"secret"}', tools_disabled=False)
            return super().respond(turn, recent_messages)

    response = TestClient(create_app(ToolRuntime())).post(
        "/internal/v1/telegram/canary/respond",
        headers={"accept": "application/x-ndjson"},
        json=_payload(),
    )
    assert [json.loads(line)["contract"] for line in response.iter_lines()] == [
        "EvaInternalGroundedReply/v1"
    ]


@pytest.mark.parametrize("interrupted", [False, True])
def test_private_canary_transports_real_adapter_final_phase(
    tmp_path, monkeypatch, interrupted,
) -> None:
    # Existing SDK-wire fixture: no real account, network or external tool.
    from tests.model.test_pydantic_conversation_model import (
        _identity,
        _memory,
        _phase_transport_adapter,
    )

    model, _captured, requests, _snapshots = _phase_transport_adapter(
        tmp_path, monkeypatch,
        [("commentary", ("Interne tussenstap.",)), ("final_answer", ("Hoi ", "Jaap!"))],
        fail=interrupted,
    )

    class AdapterRuntime(_Runtime):
        def respond(self, turn, recent_messages=()):
            self.turns.append(turn)
            result = model.generate(turn, _identity(), _memory(), ())
            assert result.action_call is None
            return GroundedReply(
                turn.conversation_id, turn.message_id, result.text, (),
                turn.occurred_at, "gpt-6-astra",
            )

    runtime = AdapterRuntime()
    response = TestClient(create_app(runtime)).post(
        "/internal/v1/telegram/canary/respond",
        headers={"accept": "application/x-ndjson"}, json=_payload(),
    )
    rows = [json.loads(line) for line in response.iter_lines()]
    assert [row["text"] for row in rows[:-1]] == ["Hoi ", "Jaap!"]
    assert "Interne tussenstap" not in response.text
    assert len(requests) == 1
    assert requests[0]["tools"]
    if interrupted:
        assert rows[-1]["contract"] == "EvaTextStreamError/v1"
        assert runtime.after == []
    else:
        assert rows[-1]["contract"] == "EvaInternalGroundedReply/v1"
        assert rows[-1]["text"] == "Hoi Jaap!"
        assert rows[-1]["conversation_id"] == "telegram:7"
        assert rows[-1]["message_id"] == "telegram:7:1"
        assert runtime.after == runtime.turns


def test_first_group_turn_stays_out_of_conversation_and_returns_instruction() -> None:
    runtime = _Runtime()
    response = TestClient(create_app(runtime)).post(
        "/internal/v1/telegram/canary/respond",
        json=_payload(chat_id="-42", text="Groepsgeheim"),
    )
    assert response.status_code == 200
    assert response.json()["provider"] == "telegram-canary-local"
    assert "/share -42" in response.json()["text"]
    assert runtime.turns == []
    assert runtime.after == []


def test_private_share_admits_group_but_never_forwards_raw_group_history() -> None:
    runtime = _Runtime()
    client = TestClient(create_app(runtime))
    client.post(
        "/internal/v1/telegram/canary/respond",
        json=_payload(chat_id="-42", message_id=2, text="observeer"),
    )
    shared = client.post(
        "/internal/v1/telegram/canary/respond",
        json=_payload(message_id=3, text="/share -42"),
    )
    payload = _payload(chat_id="-42", message_id=4, text="nu toegestaan")
    payload["recent_messages"] = [
        {
            "occurred_at": "2026-08-28T09:59:00Z",
            "role": "ander persoon",
            "text": "mag niet mee",
        }
    ]
    admitted = client.post("/internal/v1/telegram/canary/respond", json=payload)
    assert shared.json()["provider"] == "telegram-canary-local"
    assert admitted.status_code == 200
    assert runtime.turns[0].audience.kind == "telegram_group"
    assert runtime.recent == [()]


def test_caller_cannot_supply_a_different_principal_or_audience() -> None:
    runtime = _Runtime()
    payload = _payload()
    payload["principal_id"] = "someone-else"
    response = TestClient(create_app(runtime)).post(
        "/internal/v1/telegram/canary/respond", json=payload
    )
    assert response.status_code == 422
    assert runtime.turns == []


def test_telegram_stream_overlong_model_reply_has_one_bounded_final():
    from eva.application.conversation import ConversationModelResult, IdentityProfile, LivingConversation
    from tests.application.test_conversation import _Memory
    calls = []
    class Model:
        def generate(self, *args, **kwargs):
            calls.append(1)
            text = "Dit is de eerste volledige zin.\n\n" + "toelichting " * 170
            publish_conversation_text(text, tools_disabled=True)
            return ConversationModelResult(text, "fixture-model")
    conversation = LivingConversation(_Memory(), Model(),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 160))
    class Runtime(_Runtime):
        def respond(self, turn, recent_messages=()):
            self.turns.append(turn)
            return conversation.respond(turn, recent_messages)
    runtime = Runtime()
    response = TestClient(create_app(runtime)).post(
        "/internal/v1/telegram/canary/respond", headers={"accept": "application/x-ndjson"}, json=_payload())
    rows = [json.loads(line) for line in response.iter_lines()]
    assert calls == [1]
    assert not any(r["contract"] == "EvaTextStreamError/v1" for r in rows)
    finals = [r for r in rows if r["contract"] == "EvaInternalGroundedReply/v1"]
    assert len(finals) == 1
    assert len(finals[0]["text"].split()) <= 160
    assert finals[0]["text"].startswith("Dit antwoord is ingekort;")
    assert finals[0]["text"].endswith("Dit is de eerste volledige zin.")
    assert finals[0]["message_id"] == "telegram:7:1"
    assert runtime.after == runtime.turns
