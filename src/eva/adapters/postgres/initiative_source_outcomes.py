"""Read retained native source observations for the next private decision.

This is a historical reader, not a connector, dispatcher or new task authority.
Worker prose remains unconfirmed; only exact original read receipts are exposed.
"""
from datetime import datetime, timedelta
import hashlib
import json
import re

import sqlalchemy as sa

from eva.adapters.postgres.connector_results import ConnectorResults
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.component_improvement_actions import ComponentImprovementActions, READ, _SCHEMAS
from eva.adapters.postgres.delegated_work import DelegatedWorkStoreError
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
from eva.adapters.postgres.operational_timeline import operational_checkpoints, operational_events
from eva.application.background_execution import BackgroundExecutionScope, background_execution
from eva.application.conversation import ConversationConflict
from eva.application.schema_validation import validate_schema_value
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.contracts.delegated_digital_work import DelegatedInitiativeDigitalWorkSpec

_MAX_BYTES = 8192
_READS = 4
_ACTIONS = ("home_assistant_request", "read_home_assistant_inventory")


def _current_work(authority, work_id, now):
    from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
    from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline
    owner = authority.owner
    with authority.owner._engine.begin() as connection:
        owner._authorize(connection, now)
        store = PostgresDelegatedWorkStore(owner._engine, owner._authority)
        PostgresOperationalTimeline(owner._engine, owner._authority)._scope(connection)
        work = store._load_terminal_locked(connection, work_id=work_id,
            missing_message="retained native source work is absent")
        if work.state in {"succeeded", "verified"}:
            return authority._completed_work(connection, work_id, now=now)
        if (type(work.spec) is not DelegatedInitiativeDigitalWorkSpec
                or work.spec.owner_principal_id != str(owner._owner)
                or work.spec.principal_binding_id != str(owner._binding)
                or work.state not in {"failed", "ambiguous", "needs_help"}
                or work.result is not None or work.stop_requested_at is not None
                or work.lease_owner is not None or work.retention_until <= now):
            raise InitiativeMandateDenied("retained native failure source is not current")
        turn = authority._retained_work_source(work, now=now, _connection=connection)
        parent = authority.timeline.load(work.spec.origin.thread_id)
        events = authority.timeline.events(work.spec.origin.thread_id)
        blocked = events[-1] if events else None
        data = {} if blocked is None else blocked.payload.get("data", {})
        if (parent.phase != "initiative_blocked" or parent.status != "needs_help"
                or blocked is None or blocked.event_type != "PERSONAL_INITIATIVE_BLOCKED"
                or canonical_sha256(blocked.payload) != blocked.payload_sha256
                or data.get("work_id") != work.spec.work_id or data.get("spec_sha256") != work.spec.sha256
                or data.get("work_state") != work.state
                or data.get("delegated_usage") != (None if work.usage is None else work.usage.to_payload())):
            raise InitiativeMandateDenied("retained native failure parent differs")
        return work, turn


def _component_reads(authority, work, turn, now):
    """Reuse the recorded same-spec component reader, without reopening a file."""
    if READ not in work.spec.grant.capability_ids:
        return [], [], False
    port = object.__new__(ComponentImprovementActions)
    port.spec, port.source = work.spec, turn
    port.timeline = authority.timeline

    def authorize(source):
        _, current = _current_work(authority, work.spec.work_id, now)
        if current != source:
            raise InitiativeMandateDenied("component source turn changed")

    port.authorize = authorize
    e, scope = operational_events.c, port.timeline._scope_value
    with authority.owner._engine.begin() as connection:
        port.timeline._scope(connection)
        rows = connection.execute(sa.select(e.thread_id, e.payload, e.payload_sha256, e.occurred_at).where(
            e.tenant_id == scope.tenant_id, e.installation_id == scope.installation_id,
            e.event_type == "COMPONENT_ACTION_RECORDED",
            e.thread_id.in_([port._thread(step) for step in range(work.spec.grant.maximum_tool_calls)]),
            e.payload["data"]["name"].astext == READ,
            e.occurred_at >= now - timedelta(hours=24), e.occurred_at <= now,
        ).order_by(e.occurred_at.desc(), e.thread_id).limit(_READS + 1)).all()
    verified, unavailable, matched = [], [], 0
    for row in rows:
        data = row.payload.get("data", {})
        output = data.get("output", {})
        step = output.get("component_step") if isinstance(output, dict) else None
        if type(step) is not int or step < 0 or row.thread_id != port._thread(step):
            continue  # Other native works in this installation are not our source.
        matched += 1
        if matched > _READS:
            continue
        try:
            if canonical_sha256(row.payload) != bytes(row.payload_sha256).hex():
                raise ConversationConflict("component source receipt hash differs")
            events = port.timeline.events(row.thread_id)
            if (len(events) != 1 or events[0].event_type != "COMPONENT_ACTION_RECORDED"
                    or canonical_sha256(events[0].payload) != events[0].payload_sha256):
                raise ConversationConflict("component source history differs")
            receipt = port.pending(turn, step_index=step)
            if (receipt is None or receipt.call.name != READ or output != receipt.output
                    or output.get("status") != "verified" or output.get("scope") != "source_read"):
                raise ConversationConflict("component read is not verified")
            validate_schema_value(receipt.call.arguments, _SCHEMAS[READ], "retained_component_source")
            value = output.get("operation_result")
            if not isinstance(value, dict) or value.get("path") != receipt.call.arguments["path"]:
                raise ConversationConflict("component read path differs")
            if value.get("kind") == "python":
                content, start, total, following = (value.get(k) for k in
                    ("content", "start_line", "total_lines", "next_line"))
                if (not isinstance(content, str) or len(content.encode()) > 16000
                        or type(start) is not int or start < 1 or type(total) is not int or total < 0
                        or re.fullmatch(r"[0-9a-f]{64}", str(value.get("source_sha256"))) is None):
                    raise ConversationConflict("component source page is invalid")
                lines = content.splitlines(keepends=True)
                expected_next = start + len(lines) if start + len(lines) <= total else None
                if len(lines) > 160 or following != expected_next:
                    raise ConversationConflict("component source page boundary differs")
                if start == 1 and following is None and hashlib.sha256(content.encode()).hexdigest() != value["source_sha256"]:
                    raise ConversationConflict("complete component source hash differs")
                excerpt = content.encode()[:1024].decode("utf-8", errors="ignore")
                observation = {k: value[k] for k in ("kind", "path", "source_sha256", "start_line", "total_lines", "next_line")}
                observation.update(content=excerpt, content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                    excerpt_sha256=hashlib.sha256(excerpt.encode()).hexdigest(),
                    content_truncated=excerpt != content, selection="original_page_utf8_prefix")
            elif value.get("kind") == "directory":
                entries = value.get("entries")
                if not isinstance(entries, list) or len(entries) > 100 or any(
                    not isinstance(v, dict) or set(v) != {"path", "kind"}
                    or not isinstance(v["path"], str) or v["kind"] not in {"directory", "python"} for v in entries):
                    raise ConversationConflict("component directory read is invalid")
                observation = {"kind": "directory", "path": value["path"], "entries": entries[:8],
                    "truncated": value.get("truncated") is True or len(entries) > 8}
            else:
                raise ConversationConflict("component source kind differs")
            verified.append({"action": READ, "observed_at": row.occurred_at.isoformat(),
                "source": {"thread_id": row.thread_id, "call_id": receipt.call.call_id,
                    "step_index": step, "receipt_sha256": canonical_sha256(receipt.output),
                    "event_sha256": events[0].payload_sha256,
                    "source_ingress_sha256": work.spec.source_ingress_sha256},
                "observation": observation, "observation_sha256": canonical_sha256(value),
                "observation_bytes": len(canonical_json_bytes(value)),
                "observation_projection": "bounded_original_component_read",
                "historical": True, "is_current_proof": False, "status": "verified_original_read"})
        except (ConversationConflict, InitiativeMandateDenied, ValueError, KeyError, TypeError):
            unavailable.append({"action": READ, "status": "unavailable",
                "reason": "retained_component_source_binding_or_proof_invalid"})
    return verified, unavailable, matched > _READS


def _retained_reads(authority, work, turn, now):
    port = object.__new__(PostgresExternalConversationTools)
    port._engine, port._action_names = authority.owner._engine, _ACTIONS
    timeline = port._timeline(turn)
    scope, e = timeline._scope_value, operational_events.c
    with authority.owner._engine.begin() as connection:
        timeline._scope(connection)
        rows = connection.execute(sa.select(e.thread_id, e.payload, e.payload_sha256).where(
            e.tenant_id == scope.tenant_id, e.installation_id == scope.installation_id,
            e.event_type == "TOOL_REQUEST_ADMITTED",
            e.payload["data"]["principal_id"].astext == turn.principal_id,
            e.payload["data"]["conversation_id"].astext == turn.conversation_id,
            e.payload["data"]["source_message_id"].astext == turn.message_id,
            e.payload["data"]["action_name"].astext.in_(_ACTIONS),
        ).order_by(e.occurred_at.desc(), e.thread_id).limit(_READS + 1)).all()

    def authorize(source):
        _, current = _current_work(authority, work.spec.work_id, now)
        if current != source:
            raise InitiativeMandateDenied("retained source turn changed")

    verified, unavailable = [], []
    # This scope supplies only the original namespace. No dispatch callback is
    # installed; an accidental connector dispatch therefore cannot be authorized.
    with background_execution(BackgroundExecutionScope(turn, work.spec.execution_id)):
        reader = ConnectorResults(timeline, turn, authorize)
        for row in rows[:_READS]:
            try:
                if canonical_sha256(row.payload) != bytes(row.payload_sha256).hex():
                    raise ConversationConflict("retained read admission hash differs")
                admission = row.payload["data"]
                name, step = admission["action_name"], admission.get("step_index", 0)
                arguments = admission.get("action_arguments", {})
                if name == "home_assistant_request" and not (
                    arguments.get("transport") == "rest" and arguments.get("method") == "GET"
                ):
                    unavailable.append({"status": "not_a_read", "reason": "only_retained_rest_get_or_inventory"})
                    continue
                if type(step) is not int or step < 0 or row.thread_id != port._thread_id(turn, name, step):
                    raise ConversationConflict("retained read namespace differs")
                events = timeline.events(row.thread_id)
                if any(canonical_sha256(event.payload) != event.payload_sha256 for event in events):
                    raise ConversationConflict("retained read event hash differs")
                if not events or events[-1].event_type != "STATE_VERIFIED":
                    unavailable.append({"action": name, "status": "unverified",
                        "reason": "no_original_state_verified_receipt"})
                    continue
                if any(event.occurred_at > now for event in events):
                    raise ConversationConflict("retained read is from the future")
                receipt = port._receipt(turn, events, step, project=False)
                key = "inventory" if name == "read_home_assistant_inventory" else "result"
                proof = receipt.output.get("proof")
                source = {"thread_id": row.thread_id, "call_id": receipt.call.call_id,
                    "step_index": step, "observation_key": key,
                    "receipt_sha256": canonical_sha256(receipt.output),
                    "proof_sha256": canonical_sha256(proof),
                    "source_ingress_sha256": canonical_sha256(PostgresConversationJournal._ingress_data(turn))}
                raw = reader._retained_bytes(source)
                observation = json.loads(raw)
                item = {"action": name, "observed_at": events[-1].occurred_at.isoformat(),
                    "source": source, "observation_sha256": hashlib.sha256(raw).hexdigest(),
                    "observation_bytes": len(raw), "historical": True,
                    "is_current_proof": False, "status": "verified_original_read"}
                if len(raw) <= 1536:
                    item["observation"] = observation
                else:
                    item["omitted_reason"] = "original_observation_exceeds_inline_bound"
                # A verified transport receipt can still contain an HTTP failure
                # or a partial selection. Preserve those flags, never call it a
                # verified external state or complete source coverage.
                item["source_status"] = {key: observation[key] for key in (
                    "success", "http_status", "truncated", "selection_error",
                    "external_state_verified", "has_more") if key in observation}
                verified.append(item)
            except (ConversationConflict, InitiativeMandateDenied, ValueError, KeyError, TypeError):
                unavailable.append({"status": "unavailable", "reason": "retained_read_binding_or_proof_invalid"})
    return verified, unavailable, len(rows) > _READS


def read_recent_source_outcomes(authority, *, now, work_id=None) -> tuple[dict, ...]:
    """At most three exact-owner outcomes/24h, at most 8192 encoded bytes.

    Caller must use this only for a private owner decision. No shared-audience
    projection is provided. Old execution rights are neither readmitted nor used.
    """
    if (not isinstance(now, datetime) or now.tzinfo is None
            or now.utcoffset() != timedelta(0) or now.microsecond):
        raise ValueError("source outcomes require a whole-second UTC clock")
    if work_id is not None and (not isinstance(work_id, str)
            or re.fullmatch(r"work:eva-initiative:[0-9a-f]{64}", work_id) is None):
        raise ValueError("source outcome target must be an exact native work ID")
    owner, c = authority.owner, operational_checkpoints.c
    completion = operational_events.alias("native_source_completion")
    with owner._engine.begin() as connection:
        owner._authorize(connection, now)
        selection = owner._query().join(completion, sa.and_(
            completion.c.tenant_id == c.tenant_id,
            completion.c.installation_id == c.installation_id,
            completion.c.thread_id == c.thread_id,
        )).add_columns(completion.c.payload.label("completion"),
            completion.c.payload_sha256.label("completion_sha")).where(
            c.phase.in_(("initiative_completed", "initiative_blocked")),
            c.status.in_(("completed", "needs_help")),
            c.updated_at <= now,
            completion.c.event_type.in_(("PERSONAL_INITIATIVE_COMPLETED", "PERSONAL_INITIATIVE_BLOCKED")),
            completion.c.payload["data"]["work_id"].astext.is_not(None),
            completion.c.occurred_at >= now - timedelta(hours=24),
            completion.c.occurred_at <= now,
        )
        if work_id is not None:
            selection = selection.where(completion.c.payload["data"]["work_id"].astext == work_id)
        rows = connection.execute(selection.order_by(
            completion.c.occurred_at.desc(), c.thread_id).limit(4)).all()
        candidates = []
        for row in rows[:3]:
            if canonical_sha256(row.completion) != bytes(row.completion_sha).hex():
                raise ConversationConflict("initiative completion hash differs")
            data = row.completion["data"]
            if isinstance(data.get("work_id"), str):
                candidates.append(data["work_id"])
    items = []
    for work_id in candidates:
        try:
            work, turn = _current_work(authority, work_id, now)
            if (work.completed_at is None or not now - timedelta(hours=24) <= work.completed_at <= now):
                continue
            verified, unavailable, more = _retained_reads(authority, work, turn, now)
            component, component_errors, more_component = _component_reads(authority, work, turn, now)
            verified.extend(component)
            unavailable.extend(component_errors)
            more = more or more_component
            _current_work(authority, work_id, now)  # Stop/revoke of owner wins a concurrent read.
            output = None if work.result is None else work.result.output
            outcome = {"state": work.state, "completed_at": work.completed_at.isoformat(),
                "error_code": work.error_code, "error_fingerprint": work.error_fingerprint,
                "usage": None if work.usage is None else work.usage.to_payload(),
                "result_sha256": None if work.result is None else canonical_sha256(work.result.to_payload())}
            item = {"work_id": work_id, "spec_sha256": work.spec.sha256,
                "result_sha256": outcome["result_sha256"], "outcome_sha256": canonical_sha256(outcome),
                "work_outcome": outcome,
                "completed_at": work.completed_at.isoformat(),
                "reported_output": None if output is None else {"text": output[:384],
                    "output_sha256": work.result.output_sha256,
                    "truncated": len(output) > 384, "semantic_verification": False},
                "verified_reads": verified, "unavailable_reads": unavailable,
                "coverage": {"more_reads_omitted": more, "more_outcomes_omitted": len(rows) > 3,
                    "historical_not_current_state": True, "context_truncated": False,
                    "read_status": ("verified_receipt_available" if verified else
                        "unverified_or_failed" if unavailable else "no_retained_ha_reads")}}
        except (ConversationConflict, InitiativeMandateDenied, DelegatedWorkStoreError,
                ValueError, KeyError, TypeError):
            item = {"work_id": work_id, "status": "unavailable",
                "reason": "completed_native_owner_source_or_retention_invalid",
                "verified_reads": [], "reported_output": None}
        while len(canonical_json_bytes([*items, item])) > _MAX_BYTES and item["verified_reads"]:
            item["verified_reads"].pop()
            item["coverage"]["context_truncated"] = True
            item["coverage"]["omitted_reason"] = "whole_context_byte_bound"
        if len(canonical_json_bytes([*items, item])) > _MAX_BYTES:
            item = {"work_id": work_id, "status": "omitted", "reason": "whole_context_byte_bound",
                    "verified_reads": [], "reported_output": None}
        if len(canonical_json_bytes([*items, item])) <= _MAX_BYTES:
            items.append(item)
    return tuple(items)
