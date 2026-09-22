"""Fresh private owner authority shared by memory and authorized task routes."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable
from uuid import UUID

from sqlalchemy.engine import Engine

from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, TimelineScope
from eva.adapters.postgres.people_memory_overview import require_private_owner_binding
from eva.application.telegram_ingress import TelegramPrincipalBindings


class PostgresPrivateOwnerResolver:
    """Resolve only the existing canonical owner and trusted private bridge pair.

    Each call reopens the current database authority and trusted binding file.
    Conversation storage and canonical authority retain their distinct namespaces.
    The caller must recheck after retrieval and before serving cached memories.
    """

    def __init__(self, engine: Engine, *, tenant_id: str, installation_id: str,
                 owner: UUID, binding: UUID, authority_scope: TimelineScope,
                 bindings_loader: Callable[[], TelegramPrincipalBindings],
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._engine = engine
        self._tenant = tenant_id
        self._installation = installation_id
        self._owner = owner
        self._binding = binding
        self._scope = authority_scope
        self._bindings_loader = bindings_loader
        self._clock = clock

    def aliases(self, query) -> tuple[str, ...]:
        if (query.audience.kind != "private_person"
                or query.audience.principal_id != query.principal_id
                or query.tenant_id != self._tenant
                or query.installation_id != self._installation):
            return ()
        bindings = self._bindings_loader()
        alias = bindings.private_bridge_principal(
            principal_id=str(self._owner), principal_binding_id=self._binding,
        )
        if alias is None or query.principal_id not in {str(self._owner), alias}:
            return ()
        if query.principal_id == alias and query.conversation_id != alias:
            return ()
        if not self._current_binding():
            return ()
        return (str(self._owner), alias)

    def _current_binding(self):
        with self._engine.connect() as conn:
            with conn.begin():
                PostgresOperationalTimeline(self._engine, self._scope)._scope(conn)
                authority = require_private_owner_binding(
                    conn, scope=self._scope, owner=self._owner, binding=self._binding,
                    now=self._clock().replace(microsecond=0),
                )
                if (authority.provider_id, authority.channel_id) not in {
                    ("telegram", "private"), ("eva-dashboard", "owner-local"),
                }:
                    return False
        return True

    def __call__(self, turn) -> str | None:
        if (turn.tenant_id != self._tenant or turn.installation_id != self._installation
                or turn.audience.kind != "private_person"
                or turn.audience.principal_id != turn.principal_id):
            return None
        if turn.principal_id == str(self._owner):
            return str(self._owner) if self._current_binding() else None
        aliases = self.aliases(turn)
        return str(self._owner) if aliases else None


