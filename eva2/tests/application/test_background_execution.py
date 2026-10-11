"""Background jobs keep source authorization and independent durable identities."""
from dataclasses import replace
import hashlib

import pytest

from eva.adapters.postgres.conversation_actions import _turn_id
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.application.background_execution import (
    BackgroundExecutionScope, background_execution, background_execution_namespace,
)
from eva.application.conversation import ConversationConflict, ConversationTurn
from eva.contracts.canonical_json import canonical_json_bytes


def source_turn():
    return ConversationTurn.private(
        "tenant", "installation", "owner", "telegram:12", "telegram:12:34",
        "Compare my two documents", "2026-09-23T12:00:00Z",
    )


def identities(turn):
    return (
        _turn_id(turn, "current_research", "current-research:", "person"),
        PostgresExternalConversationTools._thread_id(turn, "read_document"),
        PostgresExternalConversationTools._operation_id(turn, "read_document", 1),
        PostgresExternalConversationTools._checkpoint_prefix(turn, "read_document", 1),
    )


def test_jobs_from_same_source_have_distinct_stable_action_identities():
    turn = source_turn()
    foreground = identities(turn)
    jobs = []
    for work_id in ("first", "second", "first"):
        with background_execution(BackgroundExecutionScope(turn, work_id)):
            jobs.append(identities(turn))
            assert turn.message_id == "telegram:12:34"
    assert jobs[0] == jobs[2]
    assert all(len({foreground[i], jobs[0][i], jobs[1][i]}) == 3 for i in range(4))
    assert identities(turn) == foreground


def test_foreground_external_identity_retains_deployed_hash():
    turn = source_turn()
    payload = {
        "action_name": "read_document", "installation_id": turn.installation_id,
        "message_id": turn.message_id, "tenant_id": turn.tenant_id,
    }
    expected = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()[:32]
    assert PostgresExternalConversationTools._identity(turn, "read_document") == expected
    assert background_execution_namespace(turn) is None


def test_scope_resets_on_error_and_rejects_nesting():
    turn = source_turn()
    scope = BackgroundExecutionScope(turn, "first")
    with pytest.raises(RuntimeError, match="interrupted"):
        with background_execution(scope):
            with pytest.raises(ConversationConflict, match="nested"):
                with background_execution(BackgroundExecutionScope(turn, "second")):
                    pytest.fail("nested execution was admitted")
            assert background_execution_namespace(turn) == scope.namespace
            raise RuntimeError("interrupted")
    assert background_execution_namespace(turn) is None


@pytest.mark.parametrize("change", [
    {"message_id": "telegram:12:35"}, {"text": "Changed instruction"},
    {"conversation_id": "telegram:13"}, {"occurred_at": "2026-09-23T12:01:00Z"},
])
def test_scope_rejects_a_different_source_turn(change):
    turn = source_turn()
    with background_execution(BackgroundExecutionScope(turn, "first")):
        with pytest.raises(ConversationConflict, match="source turn differs"):
            identities(replace(turn, **change))


def test_later_step_identity_still_conflicts_across_competing_providers():
    turn = source_turn()
    with background_execution(BackgroundExecutionScope(turn, "first")):
        assert PostgresExternalConversationTools._identity(turn, "read_document", 1) == (
            PostgresExternalConversationTools._identity(turn, "write_document", 1)
        )


@pytest.mark.parametrize("value", [0, -1, True, "300", float("inf"), float("nan")])
def test_invalid_time_allowance_rejected(value):
    from eva.application.background_execution import background_model_time_limit
    turn = source_turn()
    with background_execution(BackgroundExecutionScope(
            turn, "work:time", remaining_model_time_seconds=lambda _: value)):
        with pytest.raises(ConversationConflict, match="time allowance"):
            background_model_time_limit(turn)


def test_time_allowance_refreshes_exact_source_and_resets():
    from eva.application.background_execution import background_model_time_limit
    turn = source_turn()
    remaining = iter((300, 140, 0.5))
    assert background_model_time_limit(turn) is None
    with background_execution(BackgroundExecutionScope(
            turn, "work:time", remaining_model_time_seconds=lambda _: next(remaining))):
        assert [background_model_time_limit(turn) for _ in range(3)] == [300, 140, 0.5]
        with pytest.raises(ConversationConflict, match="source turn"):
            background_model_time_limit(replace(turn, message_id="other"))
    assert background_model_time_limit(turn) is None
