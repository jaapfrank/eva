"""Compact authorized task metadata for conversation continuity."""
from datetime import timedelta


def work_context_metadata(rows, now):
    """The caller supplies already-authorized, retained task rows."""
    cutoff = now - timedelta(hours=24)
    return {"jobs": [{"work_id": work.spec.work_id, "title": work.title,
                      "state": work.state,
                      "stop_requested": work.stop_requested_at is not None,
                      "recent_outcome": work.state not in {"admitted", "call_started"}
                          and cutoff <= work.updated_at <= now,
                      "result_available": work.result is not None
                          and work.state in {"succeeded", "verified"},
                      "created_at": work.spec.created_at,
                      "completed_at": None if work.result is None else work.result.completed_at,
                      "updated_at": work.updated_at.isoformat()}
                     for work in rows[:20]], "truncated": len(rows) > 20}
