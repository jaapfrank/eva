"""Owner corrections retained with a digital execution across explicit resumptions."""
from contextlib import contextmanager
import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, external_timeline_scope, CheckpointConflict
from eva.contracts.canonical_json import canonical_sha256


def _timeline(engine, spec):
    return PostgresOperationalTimeline(engine, external_timeline_scope(
        spec.source.tenant_id, spec.source.installation_id, "correction scope required"))


def _thread(spec):
    return "work-corrections:" + canonical_sha256({"execution_id": spec.execution_id,
        "source": spec.source.model_dump(mode="json")})


def work_corrections(engine, spec):
    return [{"instruction": event.payload["data"]["instruction"],
             "recorded_at": event.occurred_at.isoformat(),
             "work_id": event.payload["data"]["work_id"]}
            for event in _timeline(engine, spec).events(_thread(spec))]


def record_work_correction(engine, spec, *, instruction, request_key, now):
    if not isinstance(instruction, str) or not 1 <= len(instruction.strip()) <= 2000:
        raise ValueError("work correction requires 1..2000 characters")
    timeline, thread = _timeline(engine, spec), _thread(spec)
    for _ in range(3):
        events = timeline.events(thread)
        for event in events:
            if event.payload["data"]["request_key"] == request_key:
                if event.payload["data"]["instruction"] != instruction.strip():
                    raise ValueError("work correction changed during replay")
                return event.occurred_at
        if len(events) >= 16:
            raise ValueError("start a new task after sixteen corrections")
        values = dict(thread_id=thread, checkpoint_id=thread + ":" + str(len(events) + 1),
            occurred_at=now, phase="correction_recorded", status="completed",
            event_type="OWNER_WORK_CORRECTION_RECORDED",
            data={"request_key": request_key, "instruction": instruction.strip(),
                  "work_id": spec.work_id})
        try:
            if events:
                timeline.transition(expected_revision=events[-1].sequence, **values)
            else:
                timeline.create_thread(**values)
            return now
        except CheckpointConflict:
            continue
    raise ValueError("work corrections changed concurrently; retry the same request")


def correction_lock_key(spec):
    """Share the correction boundary with dependent schedule evaluation."""
    return "work-correction-lock:" + canonical_sha256({
        "execution_id": spec.execution_id, "source": spec.source.model_dump(mode="json")})


@contextmanager
def correction_boundary(engine, spec):
    # Keep stop/save/schedule indivisible to dependency readers without changing
    # the existing durable operations. Readers try the lock rather than waiting,
    # so they cannot deadlock against the scheduler's owner-level admission lock.
    with engine.begin() as connection:
        connection.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                           {"key": correction_lock_key(spec)})
        yield
