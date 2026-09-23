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
                limited = True
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
                        "Niet-gekoppelde afspraken staan hier niet bij. "
                        "Bij truncated ontbreken mogelijk voorgangers of vervolgstappen."}
