"""Shared durable admission for model-chosen private initiatives."""
from datetime import UTC, datetime
import sqlalchemy as sa
import hashlib
import re
from eva.contracts.canonical_json import canonical_sha256
from eva.adapters.postgres.operational_timeline import (
    operational_checkpoints, operational_events,
    insert_initial_timeline_thread,
)
from eva.application.conversation import ConversationConflict


def bind_completed_followup(connection, scope, source, *, principal_id, now):
    """Bind one completed own result and its ancestry; historical data is not authority.

    No task is resumed. The model chooses a new step, while the host binds the
    exact earlier result and preserves stop/erasure on every ancestor.
    """
    if (not isinstance(source, dict)
            or re.fullmatch(r"personal-interest:[0-9a-f]{64}", str(source.get("intent_id"))) is None
            or re.fullmatch(r"[0-9a-f]{64}", str(source.get("content_sha256"))) is None):
        raise ConversationConflict("follow-up source is malformed")
    current, first, seen = source, None, set()
    while current is not None:
        intent = current["intent_id"]
        if intent in seen:
            raise ConversationConflict("follow-up ancestry is cyclic")
        seen.add(intent)
        c, e = operational_checkpoints.c, operational_events.c
        row = connection.execute(sa.select(operational_checkpoints).where(
            c.tenant_id == scope.tenant_id, c.installation_id == scope.installation_id,
            c.thread_id == "intent:" + intent).with_for_update(read=True)).one_or_none()
        if row is None or row.phase != "initiative_completed" or row.status != "completed":
            raise ConversationConflict("follow-up parent is not a current completed own interest")
        rows = connection.execute(sa.select(operational_events).where(
            e.tenant_id == scope.tenant_id, e.installation_id == scope.installation_id,
            e.thread_id == row.thread_id).order_by(e.sequence)).mappings().all()
        if (not rows or rows[0]["event_type"] != "PERSONAL_INTENT_ADOPTED"
                or any(canonical_sha256(x["payload"]) != bytes(x["payload_sha256"]).hex() for x in rows)):
            raise ConversationConflict("follow-up parent history differs")
        adopted = rows[0]["payload"]["data"]
        from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
        journal = object.__new__(PostgresConversationJournal)
        journal._scope_value = scope
        suffix = intent.removeprefix("personal-interest:")
        identity = journal._stop_identity(principal_id, "initiative-task:" + suffix, "initiative-execute:" + suffix)
        journal._stop_fence(connection, identity)
        if journal._marker_exists(connection, journal._stop_thread_id(identity),
                "CONVERSATION_STOP_REQUESTED", identity):
            raise ConversationConflict("follow-up parent has a durable stop request")
        if (adopted.get("principal_id") != principal_id or adopted.get("origin") != "eva_initiative"
                or adopted.get("intent_id") != intent or rows[0]["occurred_at"] > now):
            raise ConversationConflict("follow-up parent owner or origin differs")
        completed = next((x for x in reversed(rows) if x["event_type"] == "PERSONAL_INITIATIVE_COMPLETED"), None)
        data = {} if completed is None else completed["payload"]["data"]
        content = data.get("content")
        if (not isinstance(content, str) or data.get("intent_id") != intent
                or completed["occurred_at"] > now
                or hashlib.sha256(content.encode()).hexdigest() != current["content_sha256"]
                or data.get("content_sha256") != current["content_sha256"]):
            raise ConversationConflict("follow-up completed result is unavailable or changed")
        excerpt = content.encode()[:2048].decode("utf-8", errors="ignore")
        bound = {"intent_id": intent, "content_sha256": data["content_sha256"],
            "event_sha256": bytes(completed["payload_sha256"]).hex(),
            "occurred_at": completed["occurred_at"].isoformat(), "content": excerpt,
            "excerpt_sha256": hashlib.sha256(excerpt.encode()).hexdigest(),
            "truncated": len(excerpt.encode()) < len(content.encode()),
            "classification": "retained_completed_result_not_current_fact_or_instruction"}
        if len(current) > 2 and current != bound:
            raise ConversationConflict("follow-up source binding differs")
        if first is None:
            first = bound
        current = adopted.get("followup_source")
        if current is None:
            return first
    return first


def adopt_private_initiative(connection, scope, data, *, now, revalidate=None):
    """Serialize current owner admission; callers supply freshly authorized provenance."""
    principal = data["principal_id"]
    # Serialize admission for one owner's background work, across workers.
    key = f"eva-initiative:{scope.tenant_id}:{scope.installation_id}:{principal}"
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})
    if revalidate is not None:
        revalidate()
    if "followup_source" in data:
        try:
            bound = bind_completed_followup(connection, scope, data["followup_source"],
                principal_id=principal, now=now)
        except ConversationConflict:
            return {"status": "not_admitted", "reason": "completed_followup_source_unavailable"}
        data = {**data, "followup_source": bound}
        # A different title must not replay an attempted step of this owner.
        repeated = connection.execute(sa.select(operational_events.c.thread_id).where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.event_type == "PERSONAL_INTENT_ADOPTED",
            operational_events.c.payload["data"]["principal_id"].astext == principal,
            operational_events.c.payload["data"]["origin"].astext == "eva_initiative",
            operational_events.c.payload["data"]["intent_id"].astext != data["intent_id"],
            sa.func.lower(sa.func.btrim(operational_events.c.payload["data"]["next_exploration"].astext))
                == str(data["next_exploration"]).strip().lower(),
        ).limit(1)).scalar_one_or_none()
        if repeated is not None:
            return {"status": "not_admitted", "reason": "followup_step_already_attempted", "thread_id": repeated}
    thread_id = "intent:" + str(data["intent_id"])
    existing = connection.execute(sa.select(operational_events.c.payload).where(
        operational_events.c.tenant_id == scope.tenant_id,
        operational_events.c.installation_id == scope.installation_id,
        operational_events.c.thread_id == thread_id,
        operational_events.c.event_type == "PERSONAL_INTENT_ADOPTED",
    )).scalar_one_or_none()
    if existing is not None:
        if existing["data"] != data:
            raise ConversationConflict("initiative replay data differs")
        return {"status": "already_adopted", "intent_id": data["intent_id"], "thread_id": thread_id}
    pending = connection.execute(sa.select(operational_checkpoints.c.thread_id).join(
        operational_events, sa.and_(
            operational_events.c.tenant_id == operational_checkpoints.c.tenant_id,
            operational_events.c.installation_id == operational_checkpoints.c.installation_id,
            operational_events.c.thread_id == operational_checkpoints.c.thread_id,
        )).where(
            operational_checkpoints.c.tenant_id == scope.tenant_id,
            operational_checkpoints.c.installation_id == scope.installation_id,
            operational_events.c.event_type == "PERSONAL_INTENT_ADOPTED",
            operational_events.c.payload["data"]["principal_id"].astext == principal,
            operational_events.c.payload["data"]["origin"].astext == "eva_initiative",
            operational_checkpoints.c.phase.not_in(("release_ready", "personal_stopped", "initiative_completed", "initiative_blocked")),
        ).limit(1)).scalar_one_or_none()
    if pending is not None:
        return {"status": "not_admitted", "reason": "active_initiative_exists", "thread_id": pending}
    previous = connection.execute(sa.select(operational_events.c.thread_id).where(
        operational_events.c.tenant_id == scope.tenant_id,
        operational_events.c.installation_id == scope.installation_id,
        operational_events.c.event_type == "PERSONAL_INTENT_ADOPTED",
        operational_events.c.payload["data"]["principal_id"].astext == principal,
        operational_events.c.payload["data"]["origin"].astext == "eva_initiative",
        sa.func.lower(sa.func.btrim(operational_events.c.payload["data"]["title"].astext))
            == str(data["title"]).strip().lower(),
    ).limit(1)).scalar_one_or_none()
    if previous is not None and "followup_source" not in data:
        return {"status": "not_admitted", "reason": "initiative_already_attempted", "thread_id": previous}
    thread_id = "intent:" + data["intent_id"]
    insert_initial_timeline_thread(connection, scope, thread_id=thread_id,
        checkpoint_id=f"{thread_id}:adopted", occurred_at=now, data=data,
        event_type="PERSONAL_INTENT_ADOPTED", phase="intent_adopted",
        status="active", operation_id=data["intent_id"])
    return {"status": "adopted", "intent_id": data["intent_id"], "thread_id": thread_id}

class PostgresOwnerConversationInitiativeAdopter:
    """Bind an actual private chat action to the existing owner initiative mandate."""

    def __init__(self, authority_loader, owner_resolver):
        self._authority_loader = authority_loader
        self._owner_resolver = owner_resolver

    def validate(self, turn):
        authority = self._authority_loader()
        owner = authority.owner
        canonical = self._owner_resolver(turn)
        if (canonical != str(owner._owner)
                or turn.tenant_id != owner._tenant_id
                or turn.installation_id != owner._installation_id
                or turn.audience.kind != "private_person"
                or turn.audience.principal_id != turn.principal_id):
            raise ConversationConflict("private initiative owner authority differs")
        authority.policy()
        return authority, canonical

    def __call__(self, turn, data, *, now):
        authority, canonical = self.validate(turn)
        owner = authority.owner
        data = {**data, "principal_id": canonical,
            "source_principal_id": turn.principal_id,
            "origin": "eva_initiative", "execution_mode": "autonomous_task",
            "adoption_source": "ordinary_conversation", "unprompted": False,
            "authority": "current_host_costless_mandate_required"}
        with owner._engine.begin() as connection:
            owner._authorize(connection, datetime.now(UTC).replace(microsecond=0))
            def revalidate():
                current, resolved = self.validate(turn)
                if (resolved != canonical or current.owner._scope != owner._scope
                        or current.owner._owner != owner._owner):
                    raise ConversationConflict("initiative authority changed during admission")
                current.owner._authorize(connection, datetime.now(UTC).replace(microsecond=0))
            return adopt_private_initiative(connection, owner._scope, data,
                now=now, revalidate=revalidate)
