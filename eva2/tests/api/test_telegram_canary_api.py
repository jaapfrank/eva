from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from eva.adapters.api.app import create_app
from eva.application.conversation import ConversationTurn, GroundedReply
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
