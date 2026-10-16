"""Admission-bound scheduled failures for existing private completion transports."""
from dataclasses import dataclass
from datetime import datetime
import hashlib
from uuid import UUID

import sqlalchemy as sa
from eva.adapters.postgres.operational_timeline import operational_checkpoints as checkpoints, operational_events as events
from eva.contracts.canonical_json import canonical_sha256


def schedule_return_binding(service, turn):
    from eva.adapters.postgres.private_work_return import private_episode
    from eva.application.channel_routes import is_whatsapp_conversation
    service.validate_authority(turn)
    destination = service._delivery_arguments(turn).get("delivery_destination")
    if destination is not None:
        if (destination.owner_principal_id != service.owner_principal_id
                or destination.principal_binding_id != service.principal_binding_id
                or any(getattr(destination, "origin_" + key) != getattr(turn, key)
                       for key in ("tenant_id", "installation_id", "principal_id", "conversation_id", "message_id"))):
            raise ValueError("scheduled destination differs from its original owner turn")
        channel = "telegram_private"
    elif (turn.conversation_id == "dashboard:" + str(service.principal_binding_id)
          or turn.conversation_id.startswith("voice:") or is_whatsapp_conversation(turn.conversation_id)):
        channel = "private_session"
    else:
        return None
    return {"contract": "EvaScheduledFailureBinding/v1", "channel": channel,
            "owner_principal_id": str(service.owner_principal_id),
            "principal_binding_id": str(service.principal_binding_id),
            "conversation_id": turn.conversation_id, "episode_id": private_episode(turn),
            "destination": None if destination is None else destination.to_payload(),
            "source": {key: getattr(turn, key) for key in ("tenant_id", "installation_id", "principal_id", "conversation_id", "message_id")}}


@dataclass(frozen=True)
class ScheduledFailure:
    goal_id: str
    definition_sha256: str
    binding: dict
    artifact_payload: dict
    output: str
    created_at: datetime
    completed_at: str
    title: str
    outcome: str = "needs_help"

    def to_payload(self):
        return self.artifact_payload

    @property
    def output_sha256(self):
        return hashlib.sha256(self.output.encode()).hexdigest()

    @property
    def result_sha256(self):
        return canonical_sha256(self.artifact_payload)


class ScheduledFailureSource:
    def __init__(self, work_store):
        self.work = work_store

    def _where(self, table):
        return (table.c.tenant_id == self.work.scope.tenant_id,
                table.c.installation_id == self.work.scope.installation_id)

    def candidates(self):
        with self.work._engine.begin() as connection:
            self.work._scope(connection)
            rows = connection.execute(sa.select(checkpoints.c.thread_id).where(
                *self._where(checkpoints), checkpoints.c.thread_id.startswith("scheduled-work:"),
                checkpoints.c.phase == "blocked").order_by(checkpoints.c.updated_at, checkpoints.c.thread_id))
            for row in rows:
                yield row.thread_id

    def load(self, schedule_id):
        if not isinstance(schedule_id, str) or not schedule_id.startswith("scheduled-work:"):
            return None
        with self.work._engine.begin() as connection:
            self.work._scope(connection)
            state = connection.execute(sa.select(checkpoints).where(
                *self._where(checkpoints), checkpoints.c.thread_id == schedule_id)).mappings().one_or_none()
            if state is None or state["phase"] != "blocked":
                return None
            rows = connection.execute(sa.select(events).where(*self._where(events),
                events.c.thread_id == schedule_id, events.c.sequence.in_((1, state["revision"])))
                .order_by(events.c.sequence)).mappings().all()
            if (len(rows) != 2 or rows[0]["event_type"] != "WORK_START_SCHEDULED"
                    or rows[-1]["event_type"] != "SCHEDULED_WORK_BLOCKED"
                    or any(canonical_sha256(row["payload"]) != bytes(row["payload_sha256"]).hex() for row in rows)):
                raise ValueError("scheduled failure history differs")
            request = rows[0]["payload"]["data"]
            binding = request.get("return_binding")
            if binding is None:
                return None  # Historical schedules never acquire a new destination.
            owner, principal_binding = UUID(binding["owner_principal_id"]), UUID(binding["principal_binding_id"])
            if (binding.get("contract") != "EvaScheduledFailureBinding/v1"
                    or not schedule_id.startswith("scheduled-work:" + str(owner) + ":")
                    or binding["conversation_id"] != request["source"]["conversation_id"]):
                raise ValueError("scheduled failure private binding differs")
            self.work._validate_owner_binding(connection, owner_principal_id=owner,
                                              principal_binding_id=principal_binding)
            title = request.get("task") or "Geplande hervatting"
            reason = rows[-1]["payload"]["data"].get("reason") or "De geplande opdracht kon niet worden gestart."
            completed_at = rows[-1]["occurred_at"].isoformat().replace("+00:00", "Z")
            output = title[:240] + "\n\n" + reason[:500] + " Ik heb deze geplande stap niet automatisch opnieuw gestart."
            payload = {"contract": "EvaScheduledFailureNotice/v1", "schedule_id": schedule_id,
                       "request_sha256": canonical_sha256(request), "state": "blocked",
                       "completed_at": completed_at, "output": output}
            return ScheduledFailure(schedule_id, canonical_sha256(request), binding, payload, output,
                                    rows[0]["occurred_at"], completed_at, title[:240])


class PrivateCompletionSources:
    """Reuse the established completion claim/ack path for both source types."""
    def __init__(self, work_store, documents=None):
        self.schedules = ScheduledFailureSource(work_store)
        self.documents = documents

    def candidates(self):
        yield from self.schedules.candidates()
        if self.documents is not None:
            yield from self.documents.candidates()

    def load(self, identifier):
        if identifier.startswith("scheduled-work:"):
            return self.schedules.load(identifier)
        return None if self.documents is None else self.documents.load(identifier)
