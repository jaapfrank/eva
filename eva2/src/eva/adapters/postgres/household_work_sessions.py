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

    first = first_session(connection, scope, origin.session_id)
    if first["binding"] != origin.destination():
        raise ValueError("household origin differs from its server session")
    conversation_scope = external_timeline_scope(
        origin.tenant_id, origin.installation_id, "household scope missing"
    )
    # The session and sponsor live in task storage; the admitted conversation
    # lives in its own namespace. Read only the already-bound origin and restore
    # the caller's task scope before any subsequent work operation.
    def select_scope(value):
        for name, identifier in (("eva.tenant_id", value.tenant_id),
                                 ("eva.installation_id", value.installation_id)):
            connection.execute(sa.text("SELECT set_config(:name, :value, true)"),
                               {"name": name, "value": str(identifier)})
    try:
        select_scope(conversation_scope)
        ingress = (
            connection.execute(
                sa.select(events).where(
                    events.c.tenant_id == conversation_scope.tenant_id,
                    events.c.installation_id == conversation_scope.installation_id,
                    events.c.thread_id == "conversation:" + origin.message_id,
                    events.c.sequence == 1,
                )
            )
            .mappings()
            .one_or_none()
        )
    finally:
        select_scope(scope)
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

    def open(self, satellite_id, device_id, *, native_conversation_key=None, native_delivery=None):
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
                deadline=expires if native_conversation_key is not None else min(expires, now + timedelta(seconds=15)),
                data={"binding": binding, "expires_at": expires.isoformat(),
                      **({"native_conversation_key": native_conversation_key} if native_conversation_key is not None else {}),
                      **({"native_delivery": native_delivery} if native_delivery is not None else {})},
            )
        return binding

    def latest_native(self):
        """Select a current native destination independently of pending task output."""
        self.authority.validate_current()
        policy = self.authority.policy
        current = {name: getattr(policy, name) for name in (
            "tenant_id", "installation_id", "household_id", "consent_operation_id",
            "household_policy_sha256")}
        current["research_policy_sha256"] = self.authority.sha256
        data = events.c.payload["data"]
        devices = [data["binding"].contains({"satellite_id": satellite,
            "device_id": device, "actor_id": "household-device:" + device})
            for satellite, device in policy.devices.items()]
        if not devices:
            return None
        with self.store._engine.begin() as connection:
            self.store._scope(connection)
            binding = connection.execute(sa.select(data["binding"]).select_from(
                events.join(checkpoints, sa.and_(
                    events.c.tenant_id == checkpoints.c.tenant_id,
                    events.c.installation_id == checkpoints.c.installation_id,
                    events.c.thread_id == checkpoints.c.thread_id))
            ).where(
                events.c.tenant_id == self.store.scope.tenant_id,
                events.c.installation_id == self.store.scope.installation_id,
                events.c.event_type == "HOUSEHOLD_WORK_SESSION_OPENED",
                events.c.sequence == 1,
                data["native_conversation_key"].astext.is_not(None),
                data["binding"].contains(current), sa.or_(*devices),
                sa.func.coalesce(data["native_delivery"]["mode"].astext, "speaker") == "speaker",
                checkpoints.c.phase == "open",
                checkpoints.c.deadline > self.authority.clock(),
            ).order_by(events.c.occurred_at.desc(), events.c.thread_id.desc())
                .limit(1)).scalar_one_or_none()
        if binding is None:
            return None
        # Recheck durable provenance and session validity after selection.
        return self.read(binding["session_id"], binding["satellite_id"], binding["device_id"])

    def native(self, satellite_id, device_id, conversation_id, *, delivery_mode="speaker"):
        """Reuse a bounded HTTP conversation session without a media heartbeat.

        Native HA has no persistent audio connection. Its authenticated turns
        may use the existing thirty-minute session lifetime; this is not proof
        of an identified speaker or permission for unsolicited audio.
        """
        self.authority.validate_device(satellite_id, device_id)
        if delivery_mode not in {"speaker", "automation"}:
            raise ValueError("native delivery mode is invalid")
        if not isinstance(conversation_id, str) or not 1 <= len(conversation_id) <= 200:
            raise ValueError("native conversation identity is invalid")
        # Keep existing speaker keys stable; the same caller ID cannot change
        # the destination of work already admitted in another delivery mode.
        key = canonical_sha256({"satellite": satellite_id, "device": device_id,
                                "conversation": conversation_id,
                                "authority": self.authority.sha256,
                                **({"delivery_mode": delivery_mode} if delivery_mode != "speaker" else {})})
        with self.store._engine.begin() as c:
            self.store._scope(c)
            c.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                      {"key": "native-household-work:" + key})
            first = c.execute(sa.select(events.c.payload).where(
                events.c.tenant_id == self.store.scope.tenant_id,
                events.c.installation_id == self.store.scope.installation_id,
                events.c.event_type == "HOUSEHOLD_WORK_SESSION_OPENED",
                events.c.payload["data"]["native_conversation_key"].astext == key,
            ).order_by(events.c.occurred_at.desc(), events.c.thread_id.desc()).limit(1)).scalar_one_or_none()
            if first is not None:
                session_id = first["data"]["binding"]["session_id"]
                state = c.execute(sa.select(checkpoints.c.phase, checkpoints.c.deadline).where(
                    checkpoints.c.tenant_id == self.store.scope.tenant_id,
                    checkpoints.c.installation_id == self.store.scope.installation_id,
                    checkpoints.c.thread_id == session_thread(session_id),
                )).one()
                if state.phase == "open" and self.authority.clock() < state.deadline:
                    return self.read(session_id, satellite_id, device_id)
            return self.open(satellite_id, device_id, native_conversation_key=key,
                native_delivery=({"mode": "automation", "conversation_id": conversation_id}
                                 if delivery_mode == "automation" else None))

    def native_delivery(self, binding):
        """Read the original output destination through the same checked session."""
        self.authority.validate_device(binding["satellite_id"], binding["device_id"])
        with self.store._engine.begin() as connection:
            self.store._scope(connection)
            first = self._read_binding(connection, binding["session_id"],
                                       binding["satellite_id"], binding["device_id"])
            if first["binding"] != binding or first.get("native_conversation_key") is None:
                raise ValueError("native delivery binding differs")
            destination = first.get("native_delivery")
            if destination is None:
                return {"mode": "speaker"}
            if (not isinstance(destination, dict)
                    or set(destination) != {"mode", "conversation_id"}
                    or destination["mode"] != "automation"
                    or not isinstance(destination["conversation_id"], str)
                    or not 1 <= len(destination["conversation_id"]) <= 200):
                raise ValueError("native delivery metadata is invalid")
            return destination

    def read_retained_native(self, session_id, satellite_id, device_id):
        """Read an original native destination without renewing its foreground lease."""
        self.authority.validate_device(satellite_id, device_id)
        with self.store._engine.begin() as connection:
            self.store._scope(connection)
            policy = self.authority.policy
            self.store._validate_owner_binding(connection,
                owner_principal_id=policy.owner_principal_id,
                principal_binding_id=policy.principal_binding_id)
            first = self._read_binding(connection, session_id, satellite_id, device_id)
            if first.get("native_conversation_key") is None:
                raise ValueError("retained household binding requires an original native session")
            return first["binding"]

    def continuity_bindings(self, binding, *, work_id=None, parent_ids=None, limit=65):
        """Select this device's retained native tasks, not unrelated session history."""
        if type(limit) is not int or not 1 <= limit <= 65:
            raise ValueError("household continuity selection exceeds its task bound")
        current = self.read(binding["session_id"], binding["satellite_id"], binding["device_id"])
        if current != binding:
            raise ValueError("household session binding changed")
        from eva.adapters.postgres.current_catalog import delegated_work_runs as runs
        with self.store._engine.begin() as connection:
            self.store._scope(connection)
            first = first_session(connection, self.store.scope, binding["session_id"])
            if first.get("native_conversation_key") is None:
                return (binding,)
            data = events.c.payload["data"]
            stored_binding = data["binding"]
            selected = connection.execute(sa.select(stored_binding).select_from(events.join(
                runs, sa.and_(events.c.tenant_id == runs.c.tenant_id,
                    events.c.installation_id == runs.c.installation_id,
                    stored_binding["session_id"].astext == runs.c.spec_payload["origin"]["session_id"].astext)
            )).where(
                events.c.tenant_id == self.store.scope.tenant_id,
                events.c.installation_id == self.store.scope.installation_id,
                events.c.event_type == "HOUSEHOLD_WORK_SESSION_OPENED",
                events.c.sequence == 1,
                data["native_conversation_key"].astext.is_not(None),
                stored_binding.contains({key: binding[key] for key in (
                    "tenant_id", "installation_id", "household_id", "actor_id",
                    "satellite_id", "device_id", "consent_operation_id",
                    "household_policy_sha256", "research_policy_sha256")}),
                runs.c.owner_principal_id == self.authority.policy.owner_principal_id,
                runs.c.principal_binding_id == self.authority.policy.principal_binding_id,
                runs.c.spec_payload["audience"].astext == "household",
                runs.c.retention_until > self.authority.clock(),
                sa.true() if work_id is None else runs.c.work_id == work_id,
                sa.true() if parent_ids is None else runs.c.parent_task_id.in_(parent_ids),
            ).group_by(stored_binding).order_by(sa.func.max(runs.c.updated_at).desc()).limit(limit)).scalars().all()
        result = []
        for item in selected:
            verified = self.read_retained_native(item["session_id"], binding["satellite_id"], binding["device_id"])
            if verified != item:
                raise ValueError("native household continuity binding changed")
            result.append(verified)
        if self.read(binding["session_id"], binding["satellite_id"], binding["device_id"]) != binding:
            raise ValueError("household session binding changed")
        return tuple(result)

    def _read_binding(self, connection, session_id, satellite_id, device_id):
        first = first_session(connection, self.store.scope, session_id)
        binding, policy = first["binding"], self.authority.policy
        expected = {
            "tenant_id": policy.tenant_id, "installation_id": policy.installation_id,
            "household_id": policy.household_id, "actor_id": "household-device:" + device_id,
            "satellite_id": satellite_id, "device_id": device_id,
            "consent_operation_id": policy.consent_operation_id,
            "research_policy_sha256": self.authority.sha256,
            "household_policy_sha256": policy.household_policy_sha256,
        }
        if any(binding.get(key) != value for key, value in expected.items()):
            raise ValueError("household session belongs to another authority")
        return first

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
            first = self._read_binding(c, session_id, satellite_id, device_id)
            binding = first["binding"]
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
                    deadline=expires if first.get("native_conversation_key") else min(expires, now + timedelta(seconds=15)),
                    data={},
                )
            elif operation != "read":
                raise ValueError("unknown household session operation")
            return binding
