"""One installation-scoped activation watermark prevents historical failure bursts."""
import sqlalchemy as sa
from eva.adapters.postgres.operational_timeline import (
    operational_checkpoints, insert_initial_timeline_thread,
)
from eva.application.work_failure_notice import FAILURE_STATES


def failure_notice_cutoff(connection, store, now):
    thread = "work-failure-notices:v1"
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                       {"key": str(store.scope.tenant_id) + ":" + str(store.scope.installation_id) + ":" + thread})
    row = connection.execute(sa.select(operational_checkpoints.c.deadline).where(
        operational_checkpoints.c.tenant_id == store.scope.tenant_id,
        operational_checkpoints.c.installation_id == store.scope.installation_id,
        operational_checkpoints.c.thread_id == thread)).one_or_none()
    if row is not None:
        return row.deadline
    insert_initial_timeline_thread(connection, store.scope, thread_id=thread,
        checkpoint_id=thread + ":enabled", occurred_at=now,
        event_type="WORK_FAILURE_NOTICES_ENABLED", phase="enabled", status="completed",
        deadline=now, data={"enabled_at": now.isoformat()})
    return now


def deliverable_states(runs, cutoff):
    return sa.or_(runs.c.state.in_(("succeeded", "verified")), sa.and_(
        runs.c.state.in_(tuple(FAILURE_STATES)), runs.c.updated_at > cutoff))
