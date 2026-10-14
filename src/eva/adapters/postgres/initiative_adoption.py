"""Shared durable admission for model-chosen private initiatives."""
from datetime import UTC, datetime
import sqlalchemy as sa
from eva.adapters.postgres.operational_timeline import (
    operational_checkpoints, operational_events,
    insert_initial_timeline_thread,
)
from eva.application.conversation import ConversationConflict


def adopt_private_initiative(connection, scope, data, *, now, revalidate=None):
    """Serialize current owner admission; callers supply freshly authorized provenance."""
    principal = data["principal_id"]
    # Serialize admission for one owner's background work, across workers.
    key = f"eva-initiative:{scope.tenant_id}:{scope.installation_id}:{principal}"
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})
    if revalidate is not None:
        revalidate()
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
    if previous is not None:
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
