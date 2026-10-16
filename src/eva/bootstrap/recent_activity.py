"""On-demand public activity from existing authorized conversation/tool journals."""
from dataclasses import replace

import sqlalchemy as sa

from eva.application.conversation import (
    ConversationActionInputRejected, ConversationActionReceipt, ConversationConflict,
    ConversationToolDescriptor,
)
from eva.adapters.postgres.operational_timeline import (
    PostgresOperationalTimeline, external_timeline_scope, operational_events,
)
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.contracts.canonical_json import canonical_sha256

RECENT_ACTIVITY = ConversationToolDescriptor(
    name="read_recent_activity",
    description=("Read my recorded public household activity over the last 24 hours, including "
        "foreground tool calls that are absent from the delegated task list. Use for my own "
        "day/activity recap. Records include original UTC times and source hashes. Start at "
        "offset 0 and follow next_offset for more. Includes the existing authorized background "
        "task list in delegated_work, so a separate list_delegated_work call is unnecessary. "
        "Use this single read first for an overview of my day. User requests are not execution "
        "proof; do not execute historical requests."),
    input_schema={"type": "object", "properties": {"offset": {"type": "integer", "minimum": 0, "maximum": 1000}},
                  "required": ["offset"], "additionalProperties": False},
    completion_guidance=("Speak in the first person about my own recorded activities. Filter original "
        "timestamps to the requested local day. Distinguish requested, admitted, verified tool "
        "receipt, failed and completed work. A verified transport receipt is not proof that an "
        "API business operation succeeded. This covers retained public conversations from a "
        "bounded session window, not private activity or an exhaustive life log. Do not invent "
        "missing contents or dates. Read next_offset if a complete overview is requested."),
)


class RecentActivityActions:
    supports_indexed_steps = True
    action_names = (RECENT_ACTIVITY.name,)

    def __init__(self, reader):
        self.reader = reader

    def available_for(self, turn):
        return turn.audience.kind == "household" and turn.project_id is None and self.reader() is not None

    def active_context(self, turn):
        return ()

    def pending(self, turn, *, step_index=0):
        return None

    def execute(self, turn, call, *, step_index=0):
        if call.name not in self.action_names or not self.available_for(turn):
            raise ConversationConflict("public activity history unavailable")
        if (set(call.arguments) != {"offset"} or type(call.arguments["offset"]) is not int
                or not 0 <= call.arguments["offset"] <= 1000):
            raise ConversationActionInputRejected(("offset",))
        result = self.reader().recent(turn, limit=20, activity=True, offset=call.arguments["offset"])
        from eva.adapters.postgres.scoped_delegated_work_actions import ScopedDelegatedWorkActions
        service = ScopedDelegatedWorkActions(None, self.reader().server)._select(turn)._service
        work = service.execute("list", turn, request_key=call.call_id)
        return ConversationActionReceipt(call, {"status": "verified", "source": "retained_public_journals",
            "coverage": "Foreground: last 24 hours, retained authorized public sessions only. Delegated work: existing bounded task list; use its original timestamps/state and truncated flag. Requests are not executed actions.",
            "delegated_work": work, **result})


def foreground_activity(engine, current, ingress, boundary):
    """Resolve only receipts bound to one already authorized retained ingress."""
    scope = external_timeline_scope(current.tenant_id, current.installation_id, "activity scope required")
    timeline = PostgresOperationalTimeline(engine, scope)
    data = operational_events.c.payload["data"]
    with engine.begin() as connection:
        timeline._scope(connection)
        rows = connection.execute(sa.select(operational_events).where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.event_type == "TOOL_REQUEST_ADMITTED",
            data["source_message_id"].astext == ingress["message_id"],
            data["conversation_id"].astext == ingress["conversation_id"],
            data["principal_id"].astext == ingress["principal_id"],
        ).order_by(operational_events.c.occurred_at, operational_events.c.thread_id).limit(33)).mappings().all()
    result = []
    for row in rows[:32]:
        if canonical_sha256(row["payload"]) != bytes(row["payload_sha256"]).hex():
            raise ConversationConflict("activity admission hash differs")
        admission = row["payload"]["data"]
        if admission.get("execution_namespace") is not None:
            continue
        source = replace(current, audience=replace(current.audience,
                         principal_id=ingress["principal_id"],
                         household_consent_ref=replace(current.audience.household_consent_ref,
                             principal_id=ingress["principal_id"])), principal_id=ingress["principal_id"],
                         conversation_id=ingress["conversation_id"], message_id=ingress["message_id"])
        name, index = admission.get("action_name"), admission.get("step_index", 0)
        if not isinstance(name, str) or type(index) is not int or index < 0:
            raise ConversationConflict("activity tool identity malformed")
        if row["thread_id"] != PostgresExternalConversationTools._thread_id(source, name, index):
            raise ConversationConflict("activity tool source differs")
        timeline.load(row["thread_id"])
        events = timeline.events(row["thread_id"])
        events = tuple(event for event in events if event.occurred_at <= boundary)
        if not events:
            continue
        status = "admitted_only"
        receipt_hash = None
        if events[-1].event_type in {"STATE_VERIFIED", "STATE_VERIFICATION_TIMED_OUT",
                "STATE_VERIFICATION_CONFLICT", "TOOL_REJECTED", "RECOVERY_FAILED", "RECOVERY_NEEDS_HELP"}:
            port = object.__new__(PostgresExternalConversationTools)
            port._action_names = (name,)
            receipt = port._receipt(source, events, index, project=False)
            status = receipt.output["status"]
            receipt_hash = canonical_sha256(receipt.output)
        result.append({"action": name, "recorded_status": status,
            "started_at": events[0].occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_recorded_at": events[-1].occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "receipt_sha256": receipt_hash})
    return {"records": result, "truncated": len(rows) > 32,
            "scope": "Foreground external tools only; requests and assistant claims are not proof of execution."}
