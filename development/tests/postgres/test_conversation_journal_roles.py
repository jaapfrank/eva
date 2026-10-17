from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.application.conversation import ConversationTurn


TENANT = "66a4aad5-0550-5211-8b36-398eabe5b7be"
INSTALLATION = "0a44a85c-4bbb-5330-8803-60fab86e5a94"
NOW = "2026-10-17T10:00:00Z"


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def begin(self):
        return SimpleNamespace(rollback=lambda: setattr(self, "rolled_back", True))

    def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(all=lambda: self.rows)


@pytest.mark.parametrize("principal", ["person:synthetic-one", "person:synthetic-two"])
@pytest.mark.parametrize("project", [None, "project:synthetic"])
def test_recent_journal_uses_neutral_private_role_with_exact_query_scope(monkeypatch, principal, project):
    turn = ConversationTurn.private(TENANT, INSTALLATION, principal,
        "conversation:synthetic", "message:current", "Current synthetic message", NOW,
        project_id=project)
    # The fake supplies already-selected rows in the database's descending order.
    # This verifies result projection and SQL construction, not database isolation.
    rows = [SimpleNamespace(event_type=kind, occurred_at=datetime(2026, 10, 17, tzinfo=UTC),
        payload={"data": {"text": text, "message_id": "message:previous"}})
        for kind, text in [("REPLY_COMMITTED", "Synthetic reply"),
                           ("INGRESS_COMMITTED", "Synthetic user message")]]
    connection = _Connection(rows)
    journal = PostgresConversationJournal(SimpleNamespace(connect=lambda: connection,
        url=SimpleNamespace(get_backend_name=lambda: "postgresql")),
        TimelineScope(UUID(TENANT), UUID(INSTALLATION)))
    monkeypatch.setattr(journal, "_scope", lambda _connection: None)
    messages = journal.recent(turn)
    assert [message.role for message in messages] == ["Gebruiker", "Eva"]
    assert [message.text for message in messages] == ["Synthetic user message", "Synthetic reply"]
    assert all(message.playback_status is None for message in messages)
    assert all(message.source_turn_sha256 is not None for message in messages)
    assert connection.rolled_back
    compiled = connection.statements[0].compile(dialect=postgresql.dialect())
    params = list(compiled.params.values())
    assert principal in params and turn.conversation_id in params
    assert UUID(TENANT) in params and UUID(INSTALLATION) in params
    if project is not None:
        assert project in params
    else:
        assert "IS NULL" in str(compiled)
