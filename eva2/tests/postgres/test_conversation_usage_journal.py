from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.application.conversation import (
    ConversationConflict,
    ConversationModelTiming,
    ConversationModelUsage,
    ConversationTurn,
    GroundedReply,
)
from eva.contracts.canonical_json import canonical_json_bytes


NOW = "2026-08-25T09:00:00Z"


def _turn() -> ConversationTurn:
    return ConversationTurn.private(
        tenant_id="tenant",
        installation_id="installation",
        principal_id="jaap",
        conversation_id="telegram:jaap",
        message_id="message:usage-replay",
        text="Wat is de status?",
        occurred_at=NOW,
    )


def _reply(
    usage: ConversationModelUsage,
    timing: ConversationModelTiming | None = None,
) -> GroundedReply:
    turn = _turn()
    return GroundedReply(
        conversation_id=turn.conversation_id,
        message_id=turn.message_id,
        text="De status is groen.",
        evidence_memory_ids=("memory:1",),
        generated_at=NOW,
        provider="local-model",
        model_usage=usage,
        model_timing=timing or ConversationModelTiming.unmeasured(),
    )


def _journal_with_reply_data(data: dict[str, object]) -> PostgresConversationJournal:
    event = SimpleNamespace(
        event_type="REPLY_COMMITTED",
        occurred_at=datetime(2026, 8, 25, 9, 0, tzinfo=UTC),
        payload={"data": data},
    )
    timeline = SimpleNamespace(events=lambda _thread_id: [event])
    journal = object.__new__(PostgresConversationJournal)
    journal._timeline = timeline  # type: ignore[attr-defined]  # noqa: SLF001
    return journal


def test_reported_usage_round_trips_through_the_durable_reply_payload() -> None:
    turn = _turn()
    reply = _reply(
        ConversationModelUsage.measured(
            model_attempts=2,
            tool_calls=1,
            input_tokens=64,
            output_tokens=9,
        )
    )
    data = PostgresConversationJournal._reply_data(turn, reply)  # noqa: SLF001

    restored = _journal_with_reply_data(data).committed_reply(turn)

    assert restored == reply
    assert restored is not None
    replay_data = PostgresConversationJournal._reply_data(  # noqa: SLF001
        turn, restored
    )
    assert canonical_json_bytes(replay_data) == canonical_json_bytes(data)


def test_legacy_reply_without_usage_replays_as_unmeasured_not_zero() -> None:
    turn = _turn()
    reply = _reply(ConversationModelUsage.unmeasured())
    data = PostgresConversationJournal._reply_data(turn, reply)  # noqa: SLF001
    data.pop("model_usage")

    restored = _journal_with_reply_data(data).committed_reply(turn)

    assert restored is not None
    assert restored.model_usage == ConversationModelUsage.unmeasured()
    assert restored.model_usage.input_tokens is None
    assert restored.model_usage.output_tokens is None


def test_measured_timing_round_trips_and_legacy_timing_stays_unmeasured() -> None:
    turn = _turn()
    reply = _reply(ConversationModelUsage.unmeasured(), ConversationModelTiming(25, 70))
    data = PostgresConversationJournal._reply_data(turn, reply)  # noqa: SLF001
    assert _journal_with_reply_data(data).committed_reply(turn) == reply
    data.pop("model_timing")
    restored = _journal_with_reply_data(data).committed_reply(turn)
    assert restored is not None
    assert restored.model_timing == ConversationModelTiming.unmeasured()


@pytest.mark.parametrize(
    "model_usage",
    [
        {"measurement": "measured"},
        {
            "input_tokens": -1,
            "measurement": "measured",
            "model_attempts": 1,
            "output_tokens": 1,
            "tool_calls": 0,
        },
        {
            "input_tokens": 0,
            "measurement": "unmeasured",
            "model_attempts": 0,
            "output_tokens": 0,
            "tool_calls": 0,
        },
    ],
)
def test_malformed_durable_usage_fails_closed(model_usage: object) -> None:
    turn = _turn()
    data = PostgresConversationJournal._reply_data(  # noqa: SLF001
        turn, _reply(ConversationModelUsage.unmeasured())
    )
    data["model_usage"] = model_usage

    with pytest.raises(ConversationConflict, match="reply usage is invalid"):
        _journal_with_reply_data(data).committed_reply(turn)


def test_changed_reported_usage_is_not_a_byte_equal_reply_replay() -> None:
    durable = _reply(
        ConversationModelUsage.measured(
            model_attempts=1,
            tool_calls=0,
            input_tokens=10,
            output_tokens=2,
        )
    )
    changed = _reply(
        ConversationModelUsage.measured(
            model_attempts=1,
            tool_calls=0,
            input_tokens=11,
            output_tokens=2,
        )
    )

    with pytest.raises(ConversationConflict, match="different reply"):
        PostgresConversationJournal._require_same_reply(durable, changed)  # noqa: SLF001
