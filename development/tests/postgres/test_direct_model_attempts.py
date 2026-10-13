"""Direct Pydantic attempts against real disposable PG, no provider network."""

from dataclasses import replace
import json
import os
from pathlib import Path
from uuid import UUID

import pytest
import sqlalchemy as sa

from eva.adapters.model.contracts import ConversationModelError
from eva.adapters.model.openai_codex_provider import CodexProviderError
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.application.conversation import (
    ConversationConflict,
    ConversationModelUsage,
    GroundedReply,
    observe_model_requests,
)
from tests.model.test_pydantic_conversation_model import _adapter, _generate, _turn
from tests.postgres.test_people_memory_overview import people_memory_engine  # noqa: F401
from tests.support.scripted_pydantic_conversation import (
    ScriptedPhase,
    ScriptedPydanticConversation,
)


@pytest.fixture
def journal(request):
    pg_engine = request.getfixturevalue("people_memory_engine")
    turn = _turn()
    scope = external_timeline_scope(
        turn.tenant_id + request.node.name, turn.installation_id, "missing"
    )
    value = PostgresConversationJournal(pg_engine, scope)
    yield value
    # Independent SQL readback also excludes exception text and provider credentials.
    with pg_engine.connect() as connection:
        rows = (
            connection.execute(
                sa.text(
                    "SELECT event_type, payload FROM eva_core.operational_events "
                    "WHERE event_type IN ('CONVERSATION_MODEL_ADMITTED','CONVERSATION_MODEL_RECORDED') "
                    "AND tenant_id=:tenant AND installation_id=:installation "
                    "ORDER BY event_type, thread_id, sequence"
                ),
                {"tenant": scope.tenant_id, "installation": scope.installation_id},
            )
            .mappings()
            .all()
        )
    data = [dict(row) for row in rows]
    assert "SECRET" not in json.dumps(data)
    if os.environ.get("CP001_ATTEMPT_EVIDENCE_DIR"):
        (
            Path(os.environ["CP001_ATTEMPT_EVIDENCE_DIR"])
            / (request.node.name + ".json")
        ).write_text(json.dumps(data, indent=2, default=str) + "\n")


def records(journal):
    with journal._engine.connect() as connection:
        return (
            connection.execute(
                sa.text(
                    "SELECT payload->'data' FROM eva_core.operational_events "
                    "WHERE event_type='CONVERSATION_MODEL_RECORDED' "
                    "AND tenant_id=:tenant AND installation_id=:installation ORDER BY thread_id"
                ),
                {
                    "tenant": journal._scope_value.tenant_id,
                    "installation": journal._scope_value.installation_id,
                },
            )
            .scalars()
            .all()
        )


def test_retry_terminal_ids_reconnect_and_reply_replay(journal, tmp_path):
    scripted = ScriptedPydanticConversation(
        (
            ScriptedPhase.failure(CodexProviderError("unavailable")),
            ScriptedPhase.text("Hallo Jaap."),
        )
    )
    with observe_model_requests(_turn(), journal):
        result = _generate(_adapter(tmp_path, scripted))
    rows = records(journal)
    assert len(rows) == 2 and len({UUID(r["request_id"]) for r in rows}) == 2
    failed = next(r for r in rows if r["outcome"] == "failed")
    assert failed["usage"] == ConversationModelUsage.unmeasured().to_wire()
    total = journal.model_usage(_turn())
    assert total.measurement == "partial" and total.model_attempts == 2
    assert total.input_tokens == result.usage.input_tokens
    assert total.output_tokens == result.usage.output_tokens
    # A new engine/pool reloads durable totals, not an in-memory ledger.
    engine = sa.create_engine(journal._engine.url)
    try:
        restored = PostgresConversationJournal(engine, journal._scope_value)
        assert restored.model_usage(_turn()) == total
        reply = GroundedReply(
            conversation_id=_turn().conversation_id,
            message_id=_turn().message_id,
            text=result.text,
            evidence_memory_ids=(),
            generated_at=_turn().occurred_at,
            provider="offline-fixture",
            model_usage=total,
        )
        restored.commit_reply(_turn(), reply)
        assert restored.committed_reply(_turn()) == reply
        for row in rows:
            restored.complete_model_request(
                _turn(),
                row["request_id"],
                ConversationModelUsage.from_wire(row["usage"]),
                outcome=row["outcome"],
            )
        assert len(records(restored)) == 2 and restored.model_usage(_turn()) == total
        with pytest.raises(ConversationConflict):
            restored.begin_model_request(_turn())
        assert len(scripted.captures) == 2
    finally:
        engine.dispose()


@pytest.mark.parametrize("known", [False, True])
def test_failure_records_only_known_usage_and_preserves_exception(
    journal, tmp_path, known
):
    usage = (
        ConversationModelUsage.measured(
            model_attempts=1, tool_calls=0, input_tokens=40, output_tokens=8
        )
        if known
        else None
    )
    error = ConversationModelError("SECRET original budget failure", model_usage=usage)
    scripted = ScriptedPydanticConversation((ScriptedPhase.failure(error),))
    with (
        observe_model_requests(_turn(), journal),
        pytest.raises(ConversationModelError) as caught,
    ):
        _generate(_adapter(tmp_path, scripted))
    assert caught.value is error and len(scripted.captures) == 1
    (row,) = records(journal)
    assert row["outcome"] == "failed"
    assert row["usage"] == (usage or ConversationModelUsage.unmeasured()).to_wire()
    if known:
        assert journal.model_usage(_turn()) == usage
    else:
        assert row["usage"]["input_tokens"] is None
        assert journal.model_usage(_turn()).measurement == "partial"


def test_outcome_dedup_conflict_owner_scope_and_legacy_success(journal):
    turn = _turn()
    attempt = journal.begin_model_request(turn)
    usage = ConversationModelUsage.measured(
        model_attempts=1, tool_calls=0, input_tokens=5, output_tokens=2
    )
    journal.complete_model_request(turn, attempt, usage)
    journal.complete_model_request(turn, attempt, usage)
    with pytest.raises(ConversationConflict):
        journal.complete_model_request(turn, attempt, usage, outcome="failed")
    wrong = replace(
        turn,
        principal_id="other-owner",
        audience=replace(
            turn.audience, principal_id="other-owner", audience_id="person:other-owner"
        ),
    )
    with pytest.raises(ConversationConflict):
        journal.complete_model_request(wrong, attempt, usage)
    assert journal.model_usage(wrong).measurement == "unmeasured"
    assert len(records(journal)) == 1
    # Legacy event format is still accepted on an identical completion replay.
    events = journal._timeline.events("conversation-model:" + attempt)
    legacy = replace(
        events[1],
        payload={
            **events[1].payload,
            "data": {
                k: v for k, v in events[1].payload["data"].items() if k != "outcome"
            },
        },
    )
    original = journal._timeline.events
    journal._timeline.events = lambda _thread: [events[0], legacy]
    try:
        journal.complete_model_request(turn, attempt, usage)
    finally:
        journal._timeline.events = original


def test_journal_write_failure_does_not_retry_or_hide_original(
    journal, tmp_path, monkeypatch
):
    error = ConversationModelError("SECRET original")
    scripted = ScriptedPydanticConversation((ScriptedPhase.failure(error),))

    def fail(*args, **kwargs):
        raise OSError("fixture journal write unavailable")

    monkeypatch.setattr(journal, "complete_model_request", fail)
    with (
        observe_model_requests(_turn(), journal),
        pytest.raises(ConversationModelError) as caught,
    ):
        _generate(_adapter(tmp_path, scripted))
    assert caught.value is error and len(scripted.captures) == 1
    assert records(journal) == []
    assert journal.model_usage(_turn()).measurement == "partial"


def test_deadline_records_terminal_unknown_without_retry(journal, tmp_path):
    scripted = ScriptedPydanticConversation((ScriptedPhase.stall(),))
    with (
        observe_model_requests(_turn(), journal),
        pytest.raises(ConversationModelError),
    ):
        _generate(_adapter(tmp_path, scripted, timeout_seconds=1))
    (row,) = records(journal)
    assert row["outcome"] == "failed"
    assert row["usage"] == ConversationModelUsage.unmeasured().to_wire()
    assert len(scripted.captures) == 1


def test_failure_retains_unique_raw_attestation_without_claiming_tool_total(
    journal, tmp_path, monkeypatch
):
    from eva.adapters.model import pydantic_conversation as module
    from eva.adapters.model.openai_codex_provider import RawUsageAttestation

    error = ConversationModelError("SECRET malformed answer after terminal usage")
    scripted = ScriptedPydanticConversation((ScriptedPhase.failure(error),))

    class Session:
        model = scripted.model

        def consume_raw_usage(self):
            return RawUsageAttestation(12, 8, 20)

    class Provider:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(module, "OpenAICodexProvider", Provider)
    model = _adapter(tmp_path, scripted)
    model._model_backend = None
    with (
        observe_model_requests(_turn(), journal),
        pytest.raises(ConversationModelError) as caught,
    ):
        _generate(model)
    assert caught.value is error
    (row,) = records(journal)
    assert row["outcome"] == "failed"
    assert (
        row["usage"]
        == ConversationModelUsage.partial(
            model_attempts=1, tool_calls=0, input_tokens=12, output_tokens=8
        ).to_wire()
    )
    assert journal.model_usage(_turn()).measurement == "partial"


def test_record_failure_after_owner_stop_does_not_admit_next_attempt(journal):
    from eva.application.conversation import ConversationStopped

    turn = _turn()
    attempt = journal.begin_model_request(turn)
    journal.request_turn_stop(turn)
    journal.complete_model_request(
        turn, attempt, ConversationModelUsage.unmeasured(), outcome="failed"
    )
    with pytest.raises(ConversationStopped):
        journal.begin_model_request(turn)
    (row,) = records(journal)
    assert row["request_id"] == attempt and row["outcome"] == "failed"


def test_consumed_attestation_mismatch_preserves_raw_usage_once(
    journal, tmp_path, monkeypatch
):
    from eva.adapters.model import pydantic_conversation as module
    from eva.adapters.model.openai_codex_provider import RawUsageAttestation
    from tests.model.test_pydantic_conversation_model import _ExactStreamUsageModel

    scripted = ScriptedPydanticConversation((ScriptedPhase.text("Hallo Jaap."),))

    class Session:
        model = _ExactStreamUsageModel(scripted.model, input_tokens=3)
        consumes = 0

        def consume_raw_usage(self):
            self.consumes += 1
            if self.consumes > 1:
                raise CodexProviderError("protocol")
            return RawUsageAttestation(18, 7, 25)

    session = Session()

    class Provider:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return session

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(module, "OpenAICodexProvider", Provider)
    model = _adapter(tmp_path, scripted)
    model._model_backend = None
    with (
        observe_model_requests(_turn(), journal),
        pytest.raises(ConversationModelError, match="attestation differs"),
    ):
        _generate(model)
    (row,) = records(journal)
    assert row["outcome"] == "failed"
    assert (
        row["usage"]
        == ConversationModelUsage.partial(
            model_attempts=1, tool_calls=0, input_tokens=18, output_tokens=7
        ).to_wire()
    )
    assert session.consumes == 1 and len(scripted.captures) == 1
    total = journal.model_usage(_turn())
    assert (
        total.measurement == "partial"
        and total.input_tokens == 18
        and total.output_tokens == 7
    )
