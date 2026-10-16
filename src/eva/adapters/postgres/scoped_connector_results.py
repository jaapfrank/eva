"""Expose same-turn saved bytes without granting private connector authority."""
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.connector_results import READER_PREFIX
from eva.application.conversation import ConversationConflict
from eva.providers.mcp_discovery import _arguments


class _LocalOnlyProvider:
    action_names = ("use_connector_tool",)
    supports_stored_results = True

    async def prepare(self, *args, **kwargs):
        raise ConversationConflict("local result reader cannot prepare an external tool")

    async def execute(self, *args, **kwargs):
        raise ConversationConflict("local result reader cannot invoke an external tool")

    async def observe(self, *args, **kwargs):
        raise ConversationConflict("local result reader cannot observe an external tool")


class LocalConnectorResults(PostgresExternalConversationTools):
    """Reuse durable local selections, refusing all external admission and replay."""

    def execute(self, turn, call, *, step_index=0):
        reader, _ = _arguments(call)
        if call.name != "use_connector_tool" or not reader.startswith(READER_PREFIX):
            raise ConversationConflict("only a saved result from this turn can be read")
        self._ensure_running(turn)
        return super().execute(turn, call, step_index=step_index)

    def can_continue_after(self, turn, receipt):
        self._ensure_running(turn)
        reader, _ = _arguments(receipt.call)
        return (receipt.call.name == "use_connector_tool" and reader.startswith(READER_PREFIX)
                and receipt.output.get("source") == "stored_connector_result"
                and receipt.output.get("external_state_verified") is False
                and receipt.output.get("status") == "selection_invalid")

    def pending(self, turn, *, step_index=0):
        self._validate_step_index(step_index)
        self._ensure_running(turn)
        events = self._timeline(turn).events(self._thread_id(turn, "use_connector_tool", step_index))
        if events and events[0].event_type != "CONNECTOR_SELECTION_ADMITTED":
            raise ConversationConflict("local result reader cannot resume an external tool")
        return super().pending(turn, step_index=step_index)


class ScopedConnectorResults:
    """Keep private dispatch intact; shared conversations can only read saved bytes."""

    supports_indexed_steps = True
    action_names = ("use_connector_tool",)

    def __init__(self, private, local):
        self._private, self._local = private, local

    @classmethod
    def build(cls, engine, private, authorize_local):
        provider = _LocalOnlyProvider()
        return cls(private, LocalConnectorResults(
            engine, provider, provider, authorize_turn=authorize_local))

    def _route(self, turn):
        return self._local if turn.audience.kind == "household" else self._private

    def available_for(self, turn):
        return self._route(turn).available_for(turn)

    def active_context(self, turn):
        return self._route(turn).active_context(turn)

    def execute(self, turn, call, *, step_index=0):
        return self._route(turn).execute(turn, call, step_index=step_index)

    def pending(self, turn, *, step_index=0):
        return self._route(turn).pending(turn, step_index=step_index)

    def can_continue_after(self, turn, receipt):
        permits = getattr(self._route(turn), "can_continue_after", None)
        return callable(permits) and permits(turn, receipt) is True

    def archive_model_context(self, turn, index):
        return self._route(turn).archive_model_context(turn, index)

    def with_preparation_call_resolver(self, resolver):
        return type(self)(self._private.with_preparation_call_resolver(resolver), self._local)
