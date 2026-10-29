"""Resolve existing durable save receipts; never redispatch their effects."""
import asyncio
import sqlalchemy as sa
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, operational_events
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.application.procedure_references import ProcedureScope, VerifiedSaveSource, ProcedureReferenceRejected, verified_save_arguments
from eva.application.conversation import ConversationConflict
from eva.application.background_execution import authorize_background_dispatch, background_execution_namespace


class PostgresProcedureReferenceHost:
    def __init__(self, engine, *, turn, authority, gateway_factory, load_source_turn, private_owner_resolver=None):
        self.engine, self.turn, self.authority = engine, turn, authority
        self.gateway_factory = gateway_factory
        self.timeline = PostgresOperationalTimeline(engine, authority.scope)
        self.load_turn = load_source_turn
        self._selected = None
        self.private_owner_resolver = private_owner_resolver

    def require_current(self):
        self.authority.authorize_turn(self.turn)
        self.authority.authorize_current(phase="execute", operation_kind="read")
        source = {"tenant_id": self.turn.tenant_id, "installation_id": self.turn.installation_id,
                  "principal_id": self.turn.principal_id, "conversation_id": self.turn.conversation_id,
                  "message_id": self.turn.message_id,
                  "audience": {"kind": self.turn.audience.kind, "principal_id": self.turn.principal_id}}
        if self.load_turn(source) != self.turn:
            raise ProcedureReferenceRejected("current ingress differs")
        if self.turn.conversation_id.startswith("initiative-task:"):
            from eva.application.initiative_execution import require_initiative_context
            require_initiative_context(self.turn)
        else:
            PostgresConversationJournal(self.engine, self.authority.scope).ensure_running(self.turn)

    def require_scope(self, scope):
        self.require_current()
        principals = (self.turn.principal_id,)
        if self.private_owner_resolver is not None:
            principals += self.private_owner_resolver.aliases(self.turn)
        if (scope.tenant_id != self.turn.tenant_id
                or scope.installation_id != self.turn.installation_id
                or scope.project_id != self.turn.project_id
                or scope.principal_id not in principals):
            raise ProcedureReferenceRejected("procedure scope differs")

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
                data["request_arguments"]["path"].astext == document_path,
                data["request_arguments"].has_key("content"),
            ).limit(2)).all()
        if len(rows) != 1 or not isinstance(rows[0].call_id, str):
            raise ProcedureReferenceRejected("current save absent or ambiguous")
        source = self.load_save(scope, rows[0].call_id)
        if (source.admission["conversation_id"] != self.turn.conversation_id
                or source.admission["source_message_id"] != self.turn.message_id
                or verified_save_arguments(source.admission).get("path") != document_path
                or source.admission.get("execution_namespace") != namespace):
            raise ProcedureReferenceRejected("current save binding differs")
        return rows[0].call_id

    def load_save(self, scope, call_id):
        self.require_scope(scope)
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
        self.require_scope(scope)
        return True  # path is checked by the pinned gateway request binding on actual readback

    def source_active(self, scope, message):
        self.require_scope(scope)
        if message.startswith("initiative-execute:"):
            try:
                return self.load_turn.native_source_active(scope, message)
            except (ConversationConflict, RuntimeError):
                return False
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
        self.require_scope(scope)
        original, admission = self._selected
        if verified_save_arguments(admission).get("path") != path:
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
            from eva.application.initiative_execution import require_initiative_context
            guard = require_initiative_context(self.turn)
            if guard is not None:
                guard.authorize_request(command, phase="observe")
            return await gateway.observe(command, None)
        observation = asyncio.run(observe())
        self.require_current()
        if observation.disposition != "verified" or observation.evidence.get("source") != "mcp_gateway_readback":
            raise ProcedureReferenceRejected("not current external readback")
        content = observation.observed.get("content")
        if not isinstance(content, str):
            raise ProcedureReferenceRejected("readback content absent")
        return content.encode("utf-8")


    def later_use(self, scope, reference, *, created_at, now):
        """Project actual same-owner, same-version executions; never infer benefit.

        No second counter/store, execution, model call or external file read.
        Legacy background receipts without a bound namespace remain uncounted.
        """
        from eva.application.procedure_references import _instant, procedure_action_thread_id
        from eva.contracts.canonical_json import canonical_sha256
        self.require_scope(scope)
        unknown = {"available": False, "observed_later_executions": None,
                   "last_used_at": None, "evidence_refs": [], "truncated": False}
        try:
            created, until = _instant(created_at), _instant(now)
            admitted, completed = operational_events.alias("use_admitted"), operational_events.alias("use_completed")
            data = admitted.c.payload["data"]
            with self.engine.connect() as connection:
                self.timeline._scope(connection)
                rows = connection.execute(sa.select(admitted.c.thread_id).join(completed,
                    sa.and_(completed.c.tenant_id == admitted.c.tenant_id,
                            completed.c.installation_id == admitted.c.installation_id,
                            completed.c.thread_id == admitted.c.thread_id)).where(
                    admitted.c.tenant_id == self.authority.scope.tenant_id,
                    admitted.c.installation_id == self.authority.scope.installation_id,
                    admitted.c.event_type == "PROCEDURE_ACTION_ADMITTED", admitted.c.sequence == 1,
                    data["name"].astext == "run_reusable_procedure",
                    data["principal_id"].astext.in_((scope.principal_id, self.turn.principal_id)),
                    data["arguments"]["document_id"].astext == reference["document_id"],
                    data["message_id"].astext != reference["source_message_id"],
                    admitted.c.occurred_at > created, admitted.c.occurred_at <= until,
                    completed.c.event_type == "PROCEDURE_ACTION_COMPLETED",
                    completed.c.occurred_at <= until,
                ).order_by(completed.c.occurred_at.desc(), admitted.c.thread_id).limit(9)).all()
            evidence = []
            for row in rows[:8]:
                state = self.timeline.load(row.thread_id)
                events = self.timeline.events(row.thread_id)
                if (state.phase != "procedure_action_completed" or state.status != "completed"
                        or [event.event_type for event in events] != ["PROCEDURE_ACTION_ADMITTED",
                            "PROCEDURE_EXECUTION_STARTED", "PROCEDURE_ACTION_COMPLETED"]
                        or any(canonical_sha256(event.payload) != event.payload_sha256 for event in events)):
                    return unknown
                source = events[0].payload["data"]
                identity = {key: source[key] for key in
                            ("principal_id", "conversation_id", "message_id", "step_index")}
                if "execution_namespace" in source:
                    identity["execution_namespace"] = source["execution_namespace"]
                if procedure_action_thread_id(identity) != row.thread_id:
                    continue  # An old background receipt cannot borrow a foreground identity.
                if source["conversation_id"].startswith(("initiative-task:", "household:", "background-accounting:")):
                    continue  # Eva's own testing/use is not later reuse by the original person.
                original = self.load_turn({"tenant_id": scope.tenant_id,
                    "installation_id": scope.installation_id, "principal_id": source["principal_id"],
                    "conversation_id": source["conversation_id"], "message_id": source["message_id"],
                    "audience": {"kind": "private_person", "principal_id": source["principal_id"]}})
                if (original.project_id != scope.project_id
                        or original.audience.kind != "private_person"
                        or _instant(original.occurred_at) != events[0].occurred_at):
                    return unknown
                result = events[-1].payload["data"].get("result")
                if (not isinstance(result, dict)
                        or canonical_sha256(result) != events[-1].payload["data"].get("result_sha256")):
                    return unknown
                process = result.get("report", {}).get("result", {})
                if (result.get("contract") != "ProcedureReferenceAction/v1"
                        or result.get("status") != "execution_completed"
                        or result.get("document_id") != reference["document_id"]
                        or result.get("content_sha256") != reference["content_sha256"]
                        or result.get("semantic_success_verified") is not False
                        or process.get("exit_code") != 0
                        or any(process.get(key) is not False for key in
                               ("timed_out", "cancelled", "stdout_truncated", "stderr_truncated"))):
                    continue
                evidence.append({"used_at": events[-1].occurred_at.isoformat(),
                                 "receipt_sha256": events[-1].payload_sha256})
            self.require_scope(scope)
            return {"available": True, "observed_later_executions": len(evidence),
                    "last_used_at": evidence[0]["used_at"] if evidence else None,
                    "evidence_refs": [item["receipt_sha256"] for item in evidence[:2]],
                    "truncated": len(rows) > 8}
        except (KeyError, TypeError, ValueError, RuntimeError, sa.exc.SQLAlchemyError):
            return unknown
