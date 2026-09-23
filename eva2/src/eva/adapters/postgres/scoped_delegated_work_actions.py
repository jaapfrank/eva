"""One descriptor namespace; choose the existing service by admitted audience."""
import sqlalchemy as sa
from eva.application.conversation import ConversationConflict
from eva.adapters.postgres.operational_timeline import operational_events
from eva.adapters.postgres.household_work_sessions import verify_origin
from eva.bootstrap.household_work import HouseholdWorkActions


class ScopedDelegatedWorkActions:
    supports_indexed_steps = True

    def __init__(self, private_actions, household_server):
        self.private, self.household = private_actions, household_server

    @property
    def action_names(self):
        private_names = () if self.private is None else self.private.action_names
        return tuple(dict.fromkeys((*private_names,
            *(tool.name for tool in HouseholdWorkActions.conversation_tools()))))

    def conversation_tools(self, *, include_research=False, include_digital=False):
        tools = (() if self.private is None else
                 self.private.conversation_tools(include_research=include_research, include_digital=include_digital))
        return tuple({tool.name: tool for tool in
                      (*tools, *HouseholdWorkActions.conversation_tools())}.values())

    def _select(self, turn):
        if turn.audience.kind != "household":
            if self.private is None or not self.private.available_for(turn):
                raise ConversationConflict("delegated work audience unavailable")
            return self.private
        server = self.household
        server.authority.validate_current()
        with server.store._engine.begin() as connection:
            server.store._scope(connection)
            bindings = connection.execute(sa.select(operational_events.c.payload).where(
                operational_events.c.tenant_id == server.store.scope.tenant_id,
                operational_events.c.installation_id == server.store.scope.installation_id,
                operational_events.c.event_type == "HOUSEHOLD_WORK_SESSION_OPENED",
                operational_events.c.payload["data"]["binding"]["conversation_id"].astext == turn.conversation_id,
                operational_events.c.payload["data"]["binding"]["actor_id"].astext == turn.principal_id,
            )).scalars().all()
            if len(bindings) != 1:
                raise ConversationConflict("household work lacks a server session")
            binding = bindings[0]["data"]["binding"]
            service = server.session(token=server.authority.voice.token,
                satellite_id=binding["satellite_id"], device_id=binding["device_id"],
                session_id=binding["session_id"])
            service.validate_authority(turn)
            verify_origin(connection, server.store.scope, service.origin(turn),
                          require_open=True, now=server.authority.clock())
        return HouseholdWorkActions(server.store._engine, service)

    def available_for(self, turn):
        return bool(self.available_action_names(turn))

    def available_action_names(self, turn):
        try:
            return self._select(turn).action_names
        except (ValueError, RuntimeError, OSError):
            return ()

    def active_context(self, turn):
        try:
            actions = self._select(turn)
        except (ValueError, RuntimeError, OSError):
            return ()
        return actions.active_context(turn)

    def pending(self, turn, *, step_index=0):
        try:
            actions = self._select(turn)
        except (ValueError, RuntimeError, OSError):
            return None
        return actions.pending(turn, step_index=step_index)

    def execute(self, turn, call, *, step_index=0):
        return self._select(turn).execute(turn, call, step_index=step_index)

    def reply_guard(self, turn, receipts):
        if not any(receipt.call.name in self.action_names for receipt in receipts):
            return None
        return self._select(turn).reply_guard(turn, receipts)
