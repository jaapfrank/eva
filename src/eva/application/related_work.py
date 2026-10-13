"""Bounded overview of stored task ancestry and descendants, without new work."""
from collections import Counter


def related_work(work_id, *, load, children, render, missing):
    current = load(work_id)
    ancestry = {current.spec.work_id}
    limited = False
    for _ in range(16):
        parent = current.spec.parent_task_id
        if not parent.startswith("work:"):
            break
        if parent in ancestry:
            limited = True
            break
        try:
            current = load(parent)
        except missing:
            limited = True
            break
        ancestry.add(current.spec.work_id)
    else:
        limited = True
    root = current.spec.work_id
    found = {root: current}
    frontier = (root,)
    for _ in range(16):
        rows = children(frontier)
        following = []
        for row in rows:
            identifier = row.spec.work_id
            if identifier in found:
                continue
            if len(found) >= 64:
                limited = True
                break
            found[identifier] = row
            following.append(identifier)
        if not following:
            break
        frontier = tuple(following)
    else:
        limited = True
    # Retain the requested task even when an unusually broad/deep tree is clipped.
    if work_id not in found:
        limited = True
        if len(found) >= 64:
            found.pop(next(reversed(found)))
        found[work_id] = load(work_id)
    values = sorted(found.values(), key=lambda row: (row.spec.created_at, row.spec.work_id))
    parents = {row.spec.parent_task_id for row in values}
    leaves = [row for row in values if row.spec.work_id not in parents]
    return {"root_work_id": root, "selected_work_id": work_id,
            "jobs": [render(row) for row in values], "truncated": limited,
            "counts": dict(Counter(row.state for row in values)),
            "open_leaf_work_ids": [row.spec.work_id for row in leaves
                                  if row.state not in {"succeeded", "verified", "stopped"}],
            "scope": "stored_parent_links",
            "guidance": "Dit overzicht telt opgeslagen gekoppelde taken, geen productvoltooiing. "
                        "Planning is alleen inbegrepen als die afzonderlijk in dit antwoord staat. "
                        "Bij truncated ontbreken mogelijk voorgangers of vervolgstappen."}


def stop_related_work(work_id, *, inspect, stop, cancel_schedule=None):
    """Stop an explicitly requested linked family through existing authorities.

    This is a bounded snapshot operation, not a ban on future owner admissions.
    Exceptions preserve completed stop writes; the action journal permits replay.
    """
    before = inspect(work_id)
    if not before.get("jobs"):
        return before
    planning = before.get("planning", {})
    cancelled = []
    if cancel_schedule is not None:
        for schedule in planning.get("schedules", ()):
            result = cancel_schedule(schedule["schedule_id"])
            cancelled.append(result)
        # Include a task that started just before its schedule was cancelled.
        current = inspect(work_id)
    else:
        current = before
    requested = []
    for job in current.get("jobs", ()):
        if job["state"] in {"admitted", "call_started"}:
            stop(job["work_id"])
            requested.append(job["work_id"])
    after = inspect(work_id)
    limited = any(value.get("truncated", False) for value in (before, current, after))
    unresolved = any(job["state"] in {"admitted", "call_started"}
                     and not job.get("stop_requested", False) for job in after.get("jobs", ()))
    unresolved = unresolved or any(item.get("status") not in {"chain_stop_recorded", "stop_pending"}
                                  for item in cancelled)
    return {**after, "status": "partial_stop" if limited or unresolved else "stop_recorded",
            "requested_work_ids": requested, "schedule_stops": cancelled,
            "in_flight_effects_may_complete": True,
            "guidance": "Stopverzoeken voor het gevonden samenhangende werk zijn verwerkt. "
                        "Al begonnen acties kunnen nog afronden; resultaten blijven bewaard. "
                        "Bij partial_stop is niet al het gekoppelde werk bevestigd gestopt. "
                        "Dit blokkeert geen nieuwe opdracht die de eigenaar later zelf geeft."}
