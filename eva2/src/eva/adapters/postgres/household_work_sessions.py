"""Server-issued household work sessions on the existing operational journal."""

from datetime import timedelta
from uuid import uuid4
import sqlalchemy as sa
from eva.contracts.canonical_json import canonical_sha256
from eva.adapters.postgres.operational_timeline import (
    operational_events as events,
    operational_checkpoints as checkpoints,
    insert_initial_timeline_thread,
    transition_timeline_thread,
)


def session_thread(session_id):
    from uuid import UUID

    if str(UUID(session_id)) != session_id:
        raise ValueError("household session identity invalid")
    return "household-work-session:" + session_id


def first_session(connection, scope, session_id):
    thread = session_thread(session_id)
    row = (
        connection.execute(
            sa.select(events).where(
                events.c.tenant_id == scope.tenant_id,
                events.c.installation_id == scope.installation_id,
                events.c.thread_id == thread,
                events.c.sequence == 1,
            )
        )
        .mappings()
        .one_or_none()
    )
    if (
        row is None
        or row["event_type"] != "HOUSEHOLD_WORK_SESSION_OPENED"
        or canonical_sha256(row["payload"]) != bytes(row["payload_sha256"]).hex()
    ):
        raise ValueError("household session binding missing or corrupt")
    return row["payload"]["data"]


def verify_origin(connection, scope, origin, *, require_open, now=None):
    from eva.adapters.postgres.operational_timeline import external_timeline_scope
    from eva.contracts.timestamps import parse_utc_timestamp

    if (
        external_timeline_scope(
            origin.tenant_id, origin.installation_id, "household scope missing"
        )
        != scope
    ):
        raise ValueError("household origin storage scope differs")
    first = first_session(connection, scope, origin.session_id)
    ingress = (
        connection.execute(
            sa.select(events).where(
                events.c.tenant_id == scope.tenant_id,
                events.c.installation_id == scope.installation_id,
                events.c.thread_id == "conversation:" + origin.message_id,
                events.c.sequence == 1,
            )
        )
        .mappings()
        .one_or_none()
    )
    if (
        ingress is None
        or ingress["event_type"] != "INGRESS_COMMITTED"
        or bytes(ingress["payload_sha256"]).hex()
        != canonical_sha256(ingress["payload"])
        or ingress["occurred_at"] != parse_utc_timestamp(origin.received_at)
        or any(
            ingress["payload"]["data"].get(key) != value
            for key, value in (
                ("conversation_id", origin.conversation_id),
                ("message_id", origin.message_id),
                ("principal_id", origin.actor_id),
            )
        )
    ):
        raise ValueError("household work lacks its exact canonical ingress")
    if first["binding"] != origin.destination():
        raise ValueError("household origin differs from its server session")
    if require_open:
        state = (
            connection.execute(
                sa.select(checkpoints).where(
                    checkpoints.c.tenant_id == scope.tenant_id,
                    checkpoints.c.installation_id == scope.installation_id,
                    checkpoints.c.thread_id == session_thread(origin.session_id),
                )
            )
            .mappings()
            .one()
        )
        if state["phase"] != "open" or now is None or now >= state["deadline"]:
            raise ValueError("household session closed or expired")
    return first


class HouseholdWorkSessions:
    def __init__(self, store, authority):
        self.store, self.authority = store, authority

    def open(self, satellite_id, device_id):
        self.authority.validate_device(satellite_id, device_id)
        now = self.authority.clock()
        policy = self.authority.policy
        session_id = str(uuid4())
        conversation_id = (
            "household:"
            + policy.household_id
            + ":conversation:"
            + canonical_sha256({"session": session_id, "device": device_id})
        )
        binding = dict(
            tenant_id=policy.tenant_id,
            installation_id=policy.installation_id,
            household_id=policy.household_id,
            actor_id="household-device:" + device_id,
            satellite_id=satellite_id,
            device_id=device_id,
            session_id=session_id,
            session_generation=1,
            conversation_id=conversation_id,
            consent_operation_id=policy.consent_operation_id,
            household_policy_sha256=policy.household_policy_sha256,
            research_policy_sha256=self.authority.sha256,
        )
        from eva.contracts.timestamps import parse_utc_timestamp

        expires = min(
            now + timedelta(minutes=30), parse_utc_timestamp(policy.expires_at)
        )
        thread = session_thread(session_id)
        with self.store._engine.begin() as c:
            self.store._scope(c)
            self.store._validate_owner_binding(
                c,
                owner_principal_id=policy.owner_principal_id,
                principal_binding_id=policy.principal_binding_id,
            )
            insert_initial_timeline_thread(
                c,
                self.store.scope,
                thread_id=thread,
                checkpoint_id=thread + ":open",
                occurred_at=now,
                event_type="HOUSEHOLD_WORK_SESSION_OPENED",
                phase="open",
                status="active",
                deadline=min(expires, now + timedelta(seconds=15)),
                data={"binding": binding, "expires_at": expires.isoformat()},
            )
        return binding

    def read(self, session_id, satellite_id, device_id, *, operation="read"):
        self.authority.validate_device(satellite_id, device_id)
        now = self.authority.clock()
        thread = session_thread(session_id)
        with self.store._engine.begin() as c:
            self.store._scope(c)
            c.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": thread},
            )
            first = first_session(c, self.store.scope, session_id)
            binding = first["binding"]
            p = self.authority.policy
            if (
                binding["satellite_id"] != satellite_id
                or binding["device_id"] != device_id
                or binding["household_id"] != p.household_id
                or binding["research_policy_sha256"] != self.authority.sha256
                or binding["household_policy_sha256"] != p.household_policy_sha256
            ):
                raise ValueError("household session belongs to another authority")
            row = (
                c.execute(
                    sa.select(checkpoints)
                    .where(
                        checkpoints.c.tenant_id == self.store.scope.tenant_id,
                        checkpoints.c.installation_id
                        == self.store.scope.installation_id,
                        checkpoints.c.thread_id == thread,
                    )
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            if operation == "close":
                if row["phase"] == "open":
                    transition_timeline_thread(
                        c,
                        self.store.scope,
                        thread_id=thread,
                        expected_revision=row["revision"],
                        checkpoint_id=thread + ":closed",
                        occurred_at=now,
                        event_type="HOUSEHOLD_WORK_SESSION_CLOSED",
                        phase="closed",
                        status="completed",
                        data={},
                    )
                return binding
            from datetime import datetime

            expires = datetime.fromisoformat(first["expires_at"])
            if row["phase"] != "open" or now >= row["deadline"] or now >= expires:
                raise ValueError("household session expired")
            if operation == "renew":
                transition_timeline_thread(
                    c,
                    self.store.scope,
                    thread_id=thread,
                    expected_revision=row["revision"],
                    checkpoint_id=thread + ":renew:" + str(row["revision"]),
                    occurred_at=now,
                    event_type="HOUSEHOLD_WORK_SESSION_RENEWED",
                    phase="open",
                    status="active",
                    deadline=min(expires, now + timedelta(seconds=15)),
                    data={},
                )
            elif operation != "read":
                raise ValueError("unknown household session operation")
            return binding
