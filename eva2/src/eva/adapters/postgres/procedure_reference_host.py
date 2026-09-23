"""Resolve existing durable save receipts; never redispatch their effects."""
import asyncio
import sqlalchemy as sa
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, operational_events
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.application.procedure_references import ProcedureScope, VerifiedSaveSource, ProcedureReferenceRejected
from eva.application.conversation import ConversationConflict
from eva.application.background_execution import authorize_background_dispatch, background_execution_namespace


class PostgresProcedureReferenceHost:
    def __init__(self, engine, *, turn, authority, gateway_factory, load_source_turn):
        self.engine, self.turn, self.authority = engine, turn, authority
        self.gateway_factory = gateway_factory
        self.timeline = PostgresOperationalTimeline(engine, authority.scope)
        self.load_turn = load_source_turn
        self._selected = None

    def require_current(self):
        self.authority.authorize_turn(self.turn)
        self.authority.authorize_current(phase="execute", operation_kind="read")
        source = {"tenant_id": self.turn.tenant_id, "installation_id": self.turn.installation_id,
                  "principal_id": self.turn.principal_id, "conversation_id": self.turn.conversation_id,
                  "message_id": self.turn.message_id,
                  "audience": {"kind": self.turn.audience.kind, "principal_id": self.turn.principal_id}}
        if self.load_turn(source) != self.turn:
            raise ProcedureReferenceRejected("current ingress differs")
        PostgresConversationJournal(self.engine, self.authority.scope).ensure_running(self.turn)

    def current_save_call_id(self, scope, document_path):
        """Resolve model-known path to one actual current-turn save, never guess an ID."""
        self.require_current()
        e = operational_events
        data = e.c.payload["data"]
        namespace = background_execution_namespace(self.turn)
        with self.engine.connect() as connection:
            self.timeline._scope(connection)
            rows = connection.execute(sa.select(data["action_call_id"].astext.label("call_id")).where(
                e.c.tenant_id == self.authority.scope.tenant_id,
                e.c.installation_id == self.authority.scope.installation_id,
                e.c.event_type == "TOOL_REQUEST_ADMITTED", e.c.sequence == 1,
                data["principal_id"].astext == self.turn.principal_id,
                data["conversation_id"].astext == self.turn.conversation_id,
                data["source_message_id"].astext == self.turn.message_id,
                data["execution_namespace"].astext == namespace if namespace is not None
                    else ~data.has_key("execution_namespace"),
                data["action_arguments"]["path"].astext == document_path,
                data["action_arguments"].has_key("content"),
            ).limit(2)).all()
        if len(rows) != 1 or not isinstance(rows[0].call_id, str):
            raise ProcedureReferenceRejected("current save absent or ambiguous")
        source = self.load_save(scope, rows[0].call_id)
        if (source.admission["conversation_id"] != self.turn.conversation_id
                or source.admission["source_message_id"] != self.turn.message_id
                or source.admission["action_arguments"].get("path") != document_path
                or source.admission.get("execution_namespace") != namespace):
            raise ProcedureReferenceRejected("current save binding differs")
        return rows[0].call_id

    def load_save(self, scope, call_id):
        self.require_current()
        if scope != ProcedureScope(self.turn.tenant_id, self.turn.installation_id,
                                  self.turn.principal_id, self.turn.project_id):
            raise ProcedureReferenceRejected("procedure scope differs")
        e = operational_events
        with self.engine.connect() as connection:
            self.timeline._scope(connection)
            rows = connection.execute(sa.select(e.c.thread_id).where(
                e.c.tenant_id == self.authority.scope.tenant_id,
                e.c.installation_id == self.authority.scope.installation_id,
                e.c.event_type == "TOOL_REQUEST_ADMITTED", e.c.sequence == 1,
                e.c.payload["data"]["principal_id"].astext == scope.principal_id,
                e.c.payload["data"]["action_call_id"].astext == call_id,
            ).limit(2)).all()
        if len(rows) != 1:
            raise ProcedureReferenceRejected("save receipt absent or ambiguous")
        thread_id = rows[0].thread_id
        self.timeline.load(thread_id)
        events = self.timeline.events(thread_id)
        admission = events[0].payload["data"]
        source = {"tenant_id": scope.tenant_id, "installation_id": scope.installation_id,
                  "principal_id": scope.principal_id, "conversation_id": admission["conversation_id"],
                  "message_id": admission["source_message_id"],
                  "audience": {"kind": "private_person", "principal_id": scope.principal_id}}
        original = self.load_turn(source)
        step = admission.get("step_index", 0)
        if (original.project_id != scope.project_id or type(step) is not int or step < 0
                or "tool:" + PostgresExternalConversationTools._historical_operation_id(
                    original, admission["action_name"], admission, step) != thread_id):
            raise ProcedureReferenceRejected("save origin binding differs")
        proof = events[-1].payload.get("data", {}).get("proof")
        if events[-1].event_type != "STATE_VERIFIED" or not isinstance(proof, dict):
            raise ProcedureReferenceRejected("save is not verified")
        self._selected = (original, admission)
        return VerifiedSaveSource(scope, admission, events[-1].event_type, proof)

    def permits(self, scope, path):
        self.require_current()
        return True  # path is checked by the pinned gateway request binding on actual readback

    def source_active(self, scope, message):
        self.require_current()
        events = self.timeline.events("conversation:" + message)
        if not events or events[0].event_type != "INGRESS_COMMITTED":
            return False
        data = events[0].payload.get("data", {})
        source = {"tenant_id": scope.tenant_id, "installation_id": scope.installation_id,
                  "principal_id": scope.principal_id, "conversation_id": data.get("conversation_id"),
                  "message_id": message,
                  "audience": {"kind": "private_person", "principal_id": scope.principal_id}}
        try:
            return self.load_turn(source).project_id == scope.project_id
        except ConversationConflict:
            return False

    def read_current(self, scope, path):
        self.require_current()
        original, admission = self._selected
        if admission["action_arguments"].get("path") != path:
            raise ProcedureReferenceRejected("readback path differs")
        gateway = self.gateway_factory()
        async def observe():
            await gateway.verify_catalog()
            self.require_current()
            wrapper = PostgresExternalConversationTools(self.engine, gateway.planner(), gateway,
                                                       authorize_turn=self.authority.authorize_turn)
            command = wrapper._request_from_admission(original, admission["action_name"],
                                                       admission, admission.get("step_index", 0), historical=True)
            authorize_background_dispatch(self.turn)
            return await gateway.observe(command, None)
        observation = asyncio.run(observe())
        self.require_current()
        if observation.disposition != "verified" or observation.evidence.get("source") != "mcp_gateway_readback":
            raise ProcedureReferenceRejected("not current external readback")
        content = observation.observed.get("content")
        if not isinstance(content, str):
            raise ProcedureReferenceRejected("readback content absent")
        return content.encode("utf-8")
