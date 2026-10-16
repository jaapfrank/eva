"""Read current owner work counts and dispatched outcomes without changing work."""
import sqlalchemy as sa

from eva.adapters.postgres.current_catalog import delegated_work_runs as runs
from eva.adapters.postgres.delegated_work import DelegatedWorkNotFound
from eva.adapters.postgres.operational_timeline import operational_checkpoints
from eva.adapters.postgres.scheduled_work import ScheduledOwnerWork


OPEN_STATES = frozenset({"admitted", "call_started"})
CLOSED_STATES = frozenset({"succeeded", "verified", "failed", "ambiguous", "needs_help", "stopped", "expired"})


def planning_work_status(service, planning, *, include_current=False):
    scheduler = ScheduledOwnerWork(service)
    items = []
    with service.store._engine.connect() as conn:
        transaction = conn.begin()
        try:
            scheduler._authorize(conn)
            selected = list(planning.get("schedules", ()))
            current_truncated = False
            if include_current:
                # Blocked historical starts may fill the scheduler's existing
                # page. Select genuine pending starts before that history limit.
                current = conn.execute(scheduler._rows().where(
                    operational_checkpoints.c.phase.in_({"scheduled", "launching", "stop_pending"})
                ).order_by(operational_checkpoints.c.updated_at.desc()).limit(21)).all()
                current_truncated = len(current) > 20
                current_views = [scheduler._view(conn, row) for row in current[:20]]
                seen = {item["schedule_id"] for item in current_views}
                selected = current_views + [item for item in selected if item["schedule_id"] not in seen]
            for item in selected:
                presented = dict(item)
                identifier = item.get("work_id")
                if identifier is not None:
                    row = conn.execute(scheduler._rows().where(
                        operational_checkpoints.c.thread_id == item["schedule_id"]
                    )).one_or_none()
                    if row is None or row.operation_id != identifier:
                        raise ValueError("scheduled work reference changed")
                    request = scheduler._request(conn, row.thread_id)
                    linked = {"state": "unavailable", "closed": None, "result_available": False}
                    try:
                        work = scheduler._load_work(identifier, request)
                        if work.retention_until > service.now():
                            linked = {"state": work.state, "closed": work.state not in OPEN_STATES,
                                      "result_available": work.result is not None and work.state in {"succeeded", "verified"}}
                    except DelegatedWorkNotFound:
                        pass
                    presented["linked_work"] = linked
                items.append(presented)
        finally:
            transaction.rollback()
    return {**planning, "schedules": items, "current_truncated": current_truncated}


def owner_work_overview(service, turn, result):
    """Keep bounded history, but count and select open work before the card limit."""
    service.validate_authority(turn)
    store = service.store
    scope = (
        runs.c.tenant_id == store.scope.tenant_id,
        runs.c.installation_id == store.scope.installation_id,
        runs.c.owner_principal_id == service.owner_principal_id,
        runs.c.principal_binding_id == service.principal_binding_id,
        runs.c.spec_payload["audience"].astext == "private",
    )
    with store._engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        with conn.begin():
            store._scope(conn)
            store._validate_owner_binding(conn, **service._owner)
            counts = dict(conn.execute(sa.select(runs.c.state, sa.func.count()).where(
                *scope).group_by(runs.c.state)).all())
            if set(counts) - OPEN_STATES - CLOSED_STATES:
                raise ValueError("owner work contains an unknown state")
            rows = conn.execute(sa.select(runs).where(*scope, runs.c.state.in_(OPEN_STATES))
                .order_by(runs.c.created_at.desc(), runs.c.work_id.desc()).limit(21)).mappings().all()
            open_work = tuple(store._validated(conn, row) for row in rows[:20])
    overview = {"contract": "EvaOwnerWorkOverview/v1", "checked_at": service.now().isoformat(),
                "open_count": sum(counts.get(state, 0) for state in OPEN_STATES),
                "closed_count": sum(counts.get(state, 0) for state in CLOSED_STATES),
                "total_count": sum(counts.values()), "state_counts": counts}
    presented = {**result, "overview": overview,
                 "open_jobs": [service._view(work, include_result=False, turn=turn) for work in open_work],
                 "open_truncated": len(rows) > 20}
    if isinstance(result.get("planning"), dict):
        presented["planning"] = planning_work_status(service, result["planning"], include_current=True)
    service.validate_authority(turn)
    return presented
