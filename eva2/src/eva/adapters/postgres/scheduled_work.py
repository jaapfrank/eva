"""Durable one-shot admission times for the existing owner work service."""
from dataclasses import asdict
from datetime import timedelta
import hashlib

import sqlalchemy as sa

from eva.application.conversation import ConversationTurn, ConversationObservationBinding
from eva.adapters.postgres.operational_timeline import (
    PostgresOperationalTimeline, operational_checkpoints, operational_events,
    insert_initial_timeline_thread, transition_timeline_thread,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.timestamps import parse_utc_timestamp


class ScheduledOwnerWork:
    def __init__(self, service):
        self.service = service
        self.engine = service.store._engine
        self.scope = service.store.scope
        self.timeline = PostgresOperationalTimeline(self.engine, self.scope)
        self.prefix = "scheduled-work:" + str(service.owner_principal_id) + ":"

    def _authorize(self, conn):
        self.timeline._scope(conn)
        self.service.store.validate_admission_authority(**self.service._owner)

    def _rows(self):
        c = operational_checkpoints.c
        return sa.select(operational_checkpoints).where(
            c.tenant_id == self.scope.tenant_id, c.installation_id == self.scope.installation_id,
            c.thread_id.startswith(self.prefix))

    def _request(self, conn, thread_id):
        e = operational_events.c
        payload = conn.execute(sa.select(e.payload, e.payload_sha256).where(
            e.tenant_id == self.scope.tenant_id, e.installation_id == self.scope.installation_id,
            e.thread_id == thread_id, e.sequence == 1)).one()
        if canonical_sha256(payload.payload) != bytes(payload.payload_sha256).hex():
            raise ValueError("scheduled work request hash differs")
        return payload.payload["data"]

    @staticmethod
    def _turn(data):
        source = dict(data["source"])
        source.pop("audience")
        binding = source.pop("observation_binding")
        return ConversationTurn.private(**source, observation_binding=(
            None if binding is None else ConversationObservationBinding(**binding)))

    def schedule(self, turn, *, request_key, request, resume=False):
        self.service.validate_authority(turn)
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 2048:
            raise ValueError("schedule request key is invalid")
        key = "work_id" if resume else "task"
        if (not isinstance(request, dict) or set(request) != {key, "run_at"}
                or not isinstance(request[key], str) or not request[key].strip()
                or len(request[key].encode()) > (240 if resume else 8192)):
            raise ValueError("scheduled work requires its task or work ID and exact UTC time")
        due = parse_utc_timestamp(request["run_at"])
        now = self.service.now()
        thread_id = self.prefix + hashlib.sha256(request_key.encode()).hexdigest()
        data = {"source": asdict(turn), "operation": "resume" if resume else "digital",
                key: request[key], "run_at": request["run_at"]}
        with self.engine.begin() as conn:
            self._authorize(conn)
            conn.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                         {"key": thread_id})
            existing = conn.execute(self._rows().where(
                operational_checkpoints.c.thread_id == thread_id)).one_or_none()
            if existing is not None:
                if self._request(conn, thread_id) != data:
                    raise ValueError("scheduled work changed during replay")
                return self._view(conn, existing)
            if not now < due <= now + timedelta(days=30):
                raise ValueError("schedule time must be in the next 30 days")
            if resume:
                previous = self.service.store.load(request[key], **self.service._owner)
                if previous.state not in {"failed", "ambiguous", "needs_help", "stopped"}:
                    raise ValueError("only stopped or failed work can be scheduled for resumption")
            elif self.service.digital_profile is None:
                raise ValueError("digital work is unavailable")
            insert_initial_timeline_thread(conn, self.scope, thread_id=thread_id,
                checkpoint_id=thread_id + ":scheduled", occurred_at=now, deadline=due,
                phase="scheduled", status="active", event_type="WORK_START_SCHEDULED", data=data)
            row = conn.execute(self._rows().where(operational_checkpoints.c.thread_id == thread_id)).one()
            return self._view(conn, row)

    def _view(self, conn, row):
        request = self._request(conn, row.thread_id)
        return {"schedule_id": row.thread_id, "state": row.phase,
                "run_at": request["run_at"], "task": request.get("task"),
                "resume_work_id": request.get("work_id"),
                "work_id": row.operation_id if row.phase == "dispatched" else None,
                "can_cancel": row.phase in {"scheduled", "launching"}}

    def list(self):
        with self.engine.begin() as conn:
            self._authorize(conn)
            rows = conn.execute(self._rows().order_by(
                operational_checkpoints.c.status == "completed",
                operational_checkpoints.c.updated_at.desc()).limit(21)).all()
            return {"schedules": [self._view(conn, row) for row in rows[:20]],
                    "truncated": len(rows) > 20}

    def cancel(self, schedule_id):
        if not isinstance(schedule_id, str) or not schedule_id.startswith(self.prefix):
            raise ValueError("schedule is outside the owner scope")
        with self.engine.begin() as conn:
            self._authorize(conn)
            row = conn.execute(self._rows().where(
                operational_checkpoints.c.thread_id == schedule_id).with_for_update()).one_or_none()
            if row is None:
                return {"status": "not_found"}
            if row.phase in {"scheduled", "launching"}:
                transition_timeline_thread(conn, self.scope, thread_id=row.thread_id,
                    expected_revision=row.revision, checkpoint_id=row.thread_id + ":cancelled",
                    phase="cancelled", status="completed", event_type="WORK_SCHEDULE_CANCELLED",
                    occurred_at=self.service.now(), data={"reason": "owner_cancelled"})
                return {"status": "cancelled", "schedule_id": schedule_id}
            work_id = row.operation_id if row.phase == "dispatched" else None
        if work_id is not None:
            work = self.service.store.request_stop(work_id, **self.service._owner, now=self.service.now())
            return {"status": "stop_requested", "schedule_id": schedule_id,
                    "work_id": work_id, "work_state": work.state}
        return {"status": row.phase, "schedule_id": schedule_id}

    def run_once(self):
        now = self.service.now()
        c = operational_checkpoints.c
        with self.engine.begin() as conn:
            self._authorize(conn)
            row = conn.execute(self._rows().where(
                c.phase.in_(("scheduled", "launching")), c.deadline <= now,
            ).order_by(c.deadline).with_for_update(skip_locked=True).limit(1)).one_or_none()
            if row is None:
                return "idle"
            if row.phase == "scheduled":
                transition_timeline_thread(conn, self.scope, thread_id=row.thread_id,
                    expected_revision=row.revision, checkpoint_id=row.thread_id + ":launching",
                    phase="launching", status="active", event_type="SCHEDULED_WORK_LAUNCH_RESERVED",
                    occurred_at=now, deadline=row.deadline, data={"admission_time": now.isoformat().replace("+00:00", "Z")})
            thread_id = row.thread_id
        # Hold only the schedule lock during database admission. No model or tool runs here.
        with self.engine.begin() as conn:
            self._authorize(conn)
            row = conn.execute(self._rows().where(c.thread_id == thread_id).with_for_update()).one()
            if row.phase != "launching":
                return row.phase
            request = self._request(conn, thread_id)
            e = operational_events.c
            reserved = conn.execute(sa.select(e.payload).where(
                e.tenant_id == self.scope.tenant_id, e.installation_id == self.scope.installation_id,
                e.thread_id == thread_id, e.event_type == "SCHEDULED_WORK_LAUNCH_RESERVED")).scalar_one()
            try:
                result = self.service._execute(request["operation"], self._turn(request),
                    task=request.get("task"), work_id=request.get("work_id"),
                    request_key=thread_id,
                    admission_time=parse_utc_timestamp(reserved["data"]["admission_time"]))
                work_id = result["work"]["work_id"]
            except Exception as error:
                transition_timeline_thread(conn, self.scope, thread_id=thread_id,
                    expected_revision=row.revision, checkpoint_id=thread_id + ":blocked",
                    phase="blocked", status="needs_help", event_type="SCHEDULED_WORK_BLOCKED",
                    occurred_at=self.service.now(), data={"error_type": type(error).__name__,
                                                        "automatic_retry": False})
                return "blocked"
            transition_timeline_thread(conn, self.scope, thread_id=thread_id,
                expected_revision=row.revision, checkpoint_id=thread_id + ":dispatched",
                phase="dispatched", status="completed", event_type="SCHEDULED_WORK_ADMITTED",
                operation_id=work_id, occurred_at=self.service.now(), data={"work_id": work_id})
            return "dispatched"
