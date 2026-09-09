from dataclasses import replace
from types import SimpleNamespace

import pytest

from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.application.conversation import ConversationConflict, ConversationTurn
from eva.providers.mcp_autonomy import _SourceTurnLoader
from eva.providers.mcp_registration import _source
from tests.postgres.test_operational_timeline import timeline_engine  # noqa: F401


def test_connector_source_reloads_actual_ingress_and_rejects_changed_reference(timeline_engine):  # noqa: F811
    turn = ConversationTurn.private(
        "mcp-source-test", "home", "owner", "original-conversation", "original-message",
        "Find this service and finish my actual request.", "2026-09-09T12:34:56Z",
        project_id="original-project",
    )
    scope = external_timeline_scope(turn.tenant_id, turn.installation_id, "scope required")
    checked = []
    authority = SimpleNamespace(scope=scope, owner_principal_id="owner", authorize_turn=checked.append)
    PostgresConversationJournal(timeline_engine, scope).admit(turn)
    loader = _SourceTurnLoader(timeline_engine, authority)
    assert loader(_source(turn)) == turn
    assert checked == [turn]
    with pytest.raises(ConversationConflict, match="differs"):
        loader(_source(replace(turn, conversation_id="another-conversation")))
    with pytest.raises(ConversationConflict, match="absent"):
        loader(_source(replace(turn, message_id="not-admitted")))
    reference = _source(turn)
    reference["audience"] = {"kind": "group", "principal_id": "owner"}
    with pytest.raises(ConversationConflict, match="private owner"):
        loader(reference)
