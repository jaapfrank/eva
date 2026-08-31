from __future__ import annotations

import pytest

from eva.application.conversation import (
    ConversationActionCall,
    ConversationActionReceipt,
    ConversationModelResult,
    ConversationModelTiming,
    ConversationModelUsage,
    ConversationTurn,
    IdentityProfile,
    LivingConversation,
)
from eva.application.memory import PresenceMemory, RecallCurrentFacts
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.memory import MemoryRecall


NOW = "2026-08-25T09:00:00Z"


def _usage(
    *, attempts: int = 1, tools: int = 0, input_tokens: int, output_tokens: int
) -> ConversationModelUsage:
    return ConversationModelUsage.measured(
        model_attempts=attempts,
        tool_calls=tools,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


class _EmptyMemory:
    def recall_for_presence(self, query: RecallCurrentFacts) -> PresenceMemory:
        return PresenceMemory(
            MemoryRecall(
                schema_version=1,
                contract="MemoryRecall/v1",
                recall_id=query.recall_id,
                query_purpose=query.query_purpose,
                tenant_id=query.tenant_id,
                principal_id=query.principal_id,
                conversation_id=query.conversation_id,
                project_id=query.project_id,
                requested_valid_at=query.requested_valid_at,
                admitted_fact_ids_sha256=canonical_sha256([]),
                selected=[],
                excluded_reasons={},
                projection_version="usage-test-v1",
                scope_sha256=canonical_sha256(query.scope),
                created_at=NOW,
            ),
            (),
        )


def _turn(message_id: str) -> ConversationTurn:
    return ConversationTurn.private(
        tenant_id="tenant",
        installation_id="installation",
        principal_id="jaap",
        conversation_id="telegram:jaap",
        message_id=message_id,
        text="Doe de lokale leesactie.",
        occurred_at=NOW,
    )


def _conversation(model: object, actions: object | None = None) -> LivingConversation:
    return LivingConversation(
        _EmptyMemory(),
        model,  # type: ignore[arg-type]
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        actions,  # type: ignore[arg-type]
    )


def test_one_reported_model_usage_is_carried_to_the_grounded_reply() -> None:
    reported = _usage(input_tokens=31, output_tokens=7)

    class Model:
        def generate(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return ConversationModelResult("Klaar.", "local-model", usage=reported)

    reply = _conversation(Model()).respond(_turn("message:one-call"))

    assert reply.model_usage == reported


def test_tool_and_followup_model_usage_are_aggregated() -> None:
    call = ConversationActionCall("call:read:1", "read_status", {"target": "lamp"})
    receipt = ConversationActionReceipt(call, {"state": "off"})

    class Actions:
        action_names = ("read_status",)

        def pending(self, turn):  # type: ignore[no-untyped-def]
            return None

        def active_context(self, turn):  # type: ignore[no-untyped-def]
            return ()

        def available_action_names(self, turn):  # type: ignore[no-untyped-def]
            return self.action_names

        def execute(self, turn, proposed):  # type: ignore[no-untyped-def]
            assert proposed == call
            return receipt

    class Model:
        calls = 0

        def generate(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                return ConversationModelResult(
                    None,
                    "local-model",
                    call,
                    _usage(tools=1, input_tokens=40, output_tokens=3),
                    ConversationModelTiming(20, 50),
                )
            assert kwargs["completed_action"] == receipt
            return ConversationModelResult(
                "De lamp is uit.",
                "local-model",
                usage=_usage(input_tokens=24, output_tokens=6),
                timing=ConversationModelTiming(30, 80),
            )

    reply = _conversation(Model(), Actions()).respond(_turn("message:two-calls"))

    assert reply.model_usage == _usage(
        attempts=2,
        tools=1,
        input_tokens=64,
        output_tokens=9,
    )
    assert reply.model_timing == ConversationModelTiming(80, 130)


def test_an_unreported_call_keeps_the_whole_reply_usage_unmeasured() -> None:
    assert (
        ConversationModelUsage.aggregate(
            (
                _usage(input_tokens=8, output_tokens=2),
                ConversationModelUsage.unmeasured(),
            )
        )
        == ConversationModelUsage.unmeasured()
    )


def test_partial_retry_usage_keeps_known_totals_and_partial_measurement() -> None:
    partial = ConversationModelUsage.partial(
        model_attempts=2,
        tool_calls=0,
        input_tokens=21,
        output_tokens=5,
    )

    assert ConversationModelUsage.aggregate(
        (partial, _usage(tools=1, input_tokens=8, output_tokens=2))
    ) == ConversationModelUsage.partial(
        model_attempts=3,
        tool_calls=1,
        input_tokens=29,
        output_tokens=7,
    )


def test_partial_usage_round_trips_without_zeroing_unknown_attempt_cost() -> None:
    partial = ConversationModelUsage.partial(
        model_attempts=2,
        tool_calls=0,
        input_tokens=21,
        output_tokens=5,
    )

    assert partial.to_wire() == {
        "input_tokens": 21,
        "measurement": "partial",
        "model_attempts": 2,
        "output_tokens": 5,
        "tool_calls": 0,
    }
    assert ConversationModelUsage.from_wire(partial.to_wire()) == partial

    unknown_only_attempt = ConversationModelUsage.partial(
        model_attempts=1,
        tool_calls=0,
        input_tokens=0,
        output_tokens=0,
    )
    assert unknown_only_attempt.measurement == "partial"


def test_unmeasured_timing_keeps_the_whole_reply_timing_unmeasured() -> None:
    assert ConversationModelTiming.aggregate(
        (ConversationModelTiming(4, 9), ConversationModelTiming.unmeasured())
    ) == ConversationModelTiming.unmeasured()


@pytest.mark.parametrize(
    "payload",
    [
        {
            "measurement": "unmeasured",
            "model_attempts": 0,
            "tool_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        },
        {
            "measurement": "measured",
            "model_attempts": 1,
            "tool_calls": 0,
            "input_tokens": 1,
        },
        {
            "measurement": "measured",
            "model_attempts": True,
            "tool_calls": 0,
            "input_tokens": 1,
            "output_tokens": 1,
        },
        {
            "measurement": "partial",
            "model_attempts": 0,
            "tool_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        },
        {
            "measurement": "partial",
            "model_attempts": 2,
            "tool_calls": None,
            "input_tokens": 1,
            "output_tokens": 1,
        },
    ],
)
def test_malformed_or_zero_filled_unknown_usage_is_rejected(payload: object) -> None:
    with pytest.raises(ValueError, match="model usage|unmeasured"):
        ConversationModelUsage.from_wire(payload)


def test_unmeasured_usage_has_only_none_values_on_the_wire() -> None:
    unknown = ConversationModelUsage.unmeasured()

    assert unknown.to_wire() == {
        "input_tokens": None,
        "measurement": "unmeasured",
        "model_attempts": None,
        "output_tokens": None,
        "tool_calls": None,
    }
    assert ConversationModelUsage.from_wire(unknown.to_wire()) == unknown
