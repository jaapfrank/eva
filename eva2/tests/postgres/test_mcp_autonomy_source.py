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


def test_media_ingress_identity_roundtrips_without_replaying_analysis_as_user_text():
    from datetime import UTC, datetime
    from eva.application.conversation_media import ConversationMediaContext
    turn = ConversationTurn.private("media-source", "home", "owner", "telegram:7",
        "telegram:7:12", "Check this screenshot.", "2026-10-12T12:00:00Z",
        media_context=ConversationMediaContext("telegram:7:12", "a" * 64,
            "image/jpeg", "A Tuya event is visible."))
    data = PostgresConversationJournal._ingress_data(turn)
    scope = external_timeline_scope(turn.tenant_id, turn.installation_id, "scope")
    checked = []
    loader = _SourceTurnLoader.__new__(_SourceTurnLoader)
    loader._authority = SimpleNamespace(scope=scope, authorize_turn=checked.append)
    loader._timeline = SimpleNamespace(load=lambda _: None, events=lambda _: (
        SimpleNamespace(event_type="INGRESS_COMMITTED", payload={"data": data},
            occurred_at=datetime(2026, 10, 12, 12, tzinfo=UTC)),))
    restored = loader(_source(turn))
    assert restored.text == turn.text
    assert restored.media_context is None
    assert restored.media_context_sha256 == turn.media_context.context_sha256
    assert PostgresConversationJournal._ingress_data(restored) == data
    assert checked == [restored]
    data["media_context_sha256"] = "invalid"
    with pytest.raises(ConversationConflict, match="invalid"):
        loader(_source(turn))
