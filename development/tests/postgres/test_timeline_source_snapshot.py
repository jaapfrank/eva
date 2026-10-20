"""Fresh source reads retain integrity, scope and one-statement consistency."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    OperationalTimelineError, PostgresOperationalTimeline, TimelineNotFound,
    external_timeline_scope, operational_checkpoints, operational_events,
)
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.application.conversation import ConversationConflict, ConversationTurn
from eva.contracts.canonical_json import canonical_sha256
from eva.providers.mcp_autonomy import _SourceTurnLoader
from eva.providers.mcp_registration import _source
from tests.postgres.test_operational_timeline import timeline_engine  # noqa: F401

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)


def admitted(engine, label):
    turn = ConversationTurn.private("snapshot-source", label, "owner", "chat",
                                    "source:" + label, "Original source", NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
    scope = external_timeline_scope(turn.tenant_id, turn.installation_id, "scope")
    journal = PostgresConversationJournal(engine, scope)
    journal.admit(turn)
    return PostgresOperationalTimeline(engine, scope), journal, turn


def advance(timeline, turn):
    thread = "conversation:" + turn.message_id
    state = timeline.load(thread)
    return timeline.transition(thread_id=thread, expected_revision=state.revision,
        checkpoint_id=thread + ":next", phase="claimed", status="active",
        event_type="SOURCE_TEST_ADVANCED", occurred_at=NOW, data={})


def test_source_snapshot_matches_full_history_and_hides_other_installations(timeline_engine):
    timeline, journal, turn = admitted(timeline_engine, "complete")
    advance(timeline, turn)
    state, history = timeline.load_with_events("conversation:" + turn.message_id)
    assert state == timeline.load(state.thread_id)
    assert history == timeline.events(state.thread_id) and len(history) > 1
    other = PostgresOperationalTimeline(timeline_engine,
        external_timeline_scope(turn.tenant_id, "other", "scope"))
    with pytest.raises(TimelineNotFound):
        other.load_with_events(state.thread_id)


@pytest.mark.parametrize("damage", ["hash", "projection", "missing-events", "sequence"])
def test_source_snapshot_rejects_corrupt_history_or_projection(timeline_engine, damage):
    timeline, journal, turn = admitted(timeline_engine, damage)
    advance(timeline, turn)
    thread = "conversation:" + turn.message_id
    # Only this explicitly disposable test database is modified as administrator.
    with timeline_engine.begin() as c:
        c.exec_driver_sql("SET LOCAL session_replication_role = replica")
        event_filter = operational_events.c.thread_id == thread
        if damage == "hash":
            c.execute(sa.update(operational_events).where(event_filter).values(payload_sha256=b"x" * 32))
        elif damage == "projection":
            c.execute(sa.update(operational_checkpoints).where(
                operational_checkpoints.c.thread_id == thread).values(phase="different"))
        elif damage == "missing-events":
            c.execute(sa.delete(operational_events).where(event_filter))
        else:
            c.execute(sa.delete(operational_events).where(event_filter,
                                                        operational_events.c.sequence == 1))
    with pytest.raises(OperationalTimelineError):
        timeline.load_with_events(thread)


@pytest.mark.parametrize("change", ["append", "erase"])
def test_source_snapshot_cannot_mix_concurrent_append_or_erasure(timeline_engine, change):
    timeline, journal, turn = admitted(timeline_engine, "race-" + change)
    thread = "conversation:" + turn.message_id
    changed = False

    def concurrent_change(connection, cursor, statement, parameters, context, executemany):
        nonlocal changed
        if not changed and "history_payload" in statement and statement.lstrip().startswith("SELECT"):
            changed = True
            if change == "append":
                advance(timeline, turn)
            else:
                journal.erase_message_content(principal_id=turn.principal_id,
                    message_id=turn.message_id, operation_sha256=canonical_sha256({"own_test": change}),
                    erased_at=NOW)

    sa.event.listen(timeline_engine, "after_cursor_execute", concurrent_change)
    try:
        state, history = timeline.load_with_events(thread)
    finally:
        sa.event.remove(timeline_engine, "after_cursor_execute", concurrent_change)
    assert changed and history[0].payload["data"]["text"] == turn.text
    after, current_history = timeline.load_with_events(thread)
    if change == "append":
        assert after.revision > state.revision
    else:
        assert "text" not in current_history[0].payload["data"]


def test_source_loader_rechecks_authority_after_fresh_source_read(timeline_engine):
    timeline, _, turn = admitted(timeline_engine, "revoked")

    def revoked(actual):
        assert actual == turn
        raise ConversationConflict("authority revoked")

    loader = _SourceTurnLoader(timeline_engine,
        SimpleNamespace(scope=timeline._scope_value, authorize_turn=revoked))
    with pytest.raises(ConversationConflict, match="revoked"):
        loader(_source(turn))
