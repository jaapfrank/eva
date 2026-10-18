"""Bounded retained context for one private initiative review, never live proof.

The caller owns current owner authorization and the already-scoped connection.
No connector is contacted, task resumed, or observation promoted to current truth.
"""
from datetime import UTC, datetime, timedelta
import re
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    TimelineScope, operational_checkpoints, operational_events,
)
from eva.application.durable_goals import DurableGoalSpec
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256

_OBSERVATIONS = 8
_COMMITMENTS = 4
_MAX_BYTES = 8192
_ENTITY = re.compile(r"^[a-z][a-z0-9_]*\.[a-zA-Z0-9_]+$")


def _verified_data(payload, digest):
    if canonical_sha256(payload) != bytes(digest).hex():
        raise RuntimeError("initiative context source hash differs")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("initiative context source is malformed")
    return data


def _text(value, limit):
    return value[:limit] if isinstance(value, str) else None


def _ordinary_work_snapshot(value, now):
    """Keep only the host's bounded current private work metadata, never authority."""
    unavailable = {"available": False, "reason": "private_work_context_unavailable",
                   "is_execution_permission": False}
    if (not isinstance(value, dict) or value.get("available") is not True
            or value.get("source") != "current_private_owner_work_store"
            or not isinstance(value.get("as_of"), str) or len(value["as_of"]) > 50
            or not isinstance(value.get("jobs"), (list, tuple))
            or ("coverage" in value and not isinstance(value["coverage"], str))):
        return unavailable
    try:
        at = datetime.fromisoformat(value["as_of"].replace("Z", "+00:00"))
        if at.utcoffset() != timedelta(0) or at != now:
            return unavailable
        jobs = list(value["jobs"][:3])
        for job in jobs:
            if (not isinstance(job, dict)
                    or not isinstance(job.get("work_id"), str) or not 0 < len(job["work_id"]) <= 240
                    or any(not isinstance(job.get(key), str)
                           or re.fullmatch(r"[0-9a-f]{64}", job[key]) is None
                           for key in ("spec_sha256", "source_ingress_sha256"))
                    or job.get("source_conversation_kind") not in {"telegram", "dashboard", "voice"}
                    or any(key in job and (type(job[key]) is not int or not 0 <= job[key] <= 65)
                           for key in ("earlier_attempts", "earlier_uncertain_attempts"))):
                return unavailable
        snapshot = {"available": True, "source": value["source"], "as_of": value["as_of"],
            "jobs": jobs, "truncated": value.get("truncated") is True or len(value["jobs"]) > 3,
            "is_execution_permission": False,
            **({"coverage": value["coverage"][:160]} if "coverage" in value else {})}
        # The caller's owner service bounds individual metadata fields. Retain no
        # opaque envelope fields or raw conversation/instruction alongside them.
        while len(canonical_json_bytes(snapshot)) > 6144:
            if not jobs:
                return unavailable
            jobs.pop()
            snapshot["truncated"] = True
        return snapshot
    except (ValueError, TypeError, OverflowError):
        return unavailable


def read_private_initiative_context(connection, scope: TimelineScope,
                                   principal_id: str, now: datetime, *, ordinary_work=None) -> dict:
    """Snapshot admitted household history and exact-owner durable commitments.

    Ordinary delegated work has a separate current owner/source binding. A host
    reader may supply its bounded metadata; otherwise coverage stays unknown.
    Retained status is context, never permission to resume or repeat that work.
    """
    if not isinstance(principal_id, str) or not 0 < len(principal_id) <= 240:
        raise ValueError("initiative context requires its current private owner")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("initiative context clock must be timezone-aware")
    now = now.astimezone(UTC)
    e, c = operational_events, operational_checkpoints
    source = e.c.payload["data"]["source"].astext
    entity = e.c.payload["data"]["payload"]["entity_id"].astext
    latest_entities = sa.select(e).where(
        e.c.tenant_id == scope.tenant_id, e.c.installation_id == scope.installation_id,
        e.c.event_type == "SENSORY_EVENT_OBSERVED",
        source.in_(("home_assistant", "home-assistant")),
        e.c.payload["data"]["event_type"].astext == "state_changed",
        e.c.occurred_at >= now - timedelta(hours=24), e.c.occurred_at <= now,
        entity.is_not(None),
    ).distinct(source, entity).order_by(source, entity, e.c.occurred_at.desc(), e.c.sequence.desc()).subquery()
    rows = connection.execute(sa.select(latest_entities).order_by(
        latest_entities.c.occurred_at.desc(), latest_entities.c.thread_id
    ).limit(_OBSERVATIONS + 1)).mappings().all()
    observations = []
    for row in rows[:_OBSERVATIONS]:
        data = _verified_data(row["payload"], row["payload_sha256"])
        payload = data.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError("initiative household observation is malformed")
        entity_id = payload.get("entity_id")
        if not isinstance(entity_id, str) or len(entity_id) > 255 or not _ENTITY.fullmatch(entity_id):
            continue
        observations.append({
            "source": data["source"],
            "source_event_id": _text(data.get("source_event_id"), 240),
            "source_sha256": bytes(row["payload_sha256"]).hex(),
            "observed_at": row["occurred_at"].isoformat(),
            "entity_id": entity_id,
            "friendly_name": _text(payload.get("friendly_name"), 80),
            "state": _text(payload.get("new_state"), 80),
            "device_class": _text(payload.get("device_class"), 40),
            "unit_of_measurement": _text(payload.get("unit_of_measurement"), 24),
            "historical": True,
            "is_current_proof": False,
        })

    original, latest = e.alias("commitment_original"), e.alias("commitment_latest")
    goals = connection.execute(sa.select(
        original.c.payload.label("original"), original.c.payload_sha256.label("original_sha"),
        latest.c.payload.label("latest"), latest.c.payload_sha256.label("latest_sha"),
        c.c.thread_id, c.c.phase, c.c.status, c.c.revision, c.c.updated_at,
    ).select_from(original.join(c, sa.and_(
        c.c.tenant_id == original.c.tenant_id, c.c.installation_id == original.c.installation_id,
        c.c.thread_id == original.c.thread_id,
    )).join(latest, sa.and_(
        latest.c.tenant_id == c.c.tenant_id, latest.c.installation_id == c.c.installation_id,
        latest.c.thread_id == c.c.thread_id, latest.c.sequence == c.c.revision,
    ))).where(
        original.c.tenant_id == scope.tenant_id, original.c.installation_id == scope.installation_id,
        original.c.event_type == "GOAL_ADMITTED",
        original.c.payload["data"]["owner_id"].astext == principal_id,
        original.c.occurred_at <= now, c.c.updated_at <= now,
    ).order_by(c.c.updated_at.desc(), c.c.thread_id).limit(_COMMITMENTS + 1)).mappings().all()
    own_work = []
    for row in goals[:_COMMITMENTS]:
        data = _verified_data(row["original"], row["original_sha"])
        _verified_data(row["latest"], row["latest_sha"])
        goal = DurableGoalSpec.from_payload(data)
        if goal.owner_id != principal_id:
            raise RuntimeError("initiative commitment owner differs")
        checkpoint = row["latest"].get("checkpoint", {})
        if any(checkpoint.get(k) != row[k] for k in ("phase", "status", "revision")):
            raise RuntimeError("initiative commitment projection differs")
        updated = checkpoint.get("updated_at")
        if (not isinstance(updated, str)
                or datetime.fromisoformat(updated.replace("Z", "+00:00")) != row["updated_at"]):
            raise RuntimeError("initiative commitment projection timestamp differs")
        own_work.append({
            "kind": "durable_commitment", "goal_id": goal.goal_id,
            "title": goal.description[:256], "phase": row["phase"], "status": row["status"],
            "updated_at": row["updated_at"].isoformat(), "deadline": goal.wake.deadline.isoformat(),
            "source_sha256": bytes(row["original_sha"]).hex(),
            "state_sha256": bytes(row["latest_sha"]).hex(),
            "is_execution_permission": False,
        })
    result = {
        "observed_at_utc": now.isoformat(),
        "local_time": now.astimezone(ZoneInfo("Europe/Amsterdam")).isoformat(),
        "timezone": "Europe/Amsterdam", "observations": observations, "own_work": own_work,
        "coverage": {
            "household_observation_window_hours": 24,
            "observations_are_current_proof": False,
            "observations_truncated": len(rows) > _OBSERVATIONS,
            "commitments_truncated": len(goals) > _COMMITMENTS,
            "own_work_source": "same-scope exact-owner admitted durable commitments only",
            "missing": ["live_connected_service_inventory", "live_calendar_events",
                        "ordinary_delegated_work_requires_separate_owner_binding",
                        "owner_alias_commitments_not_checked", "unretained_household_observations"],
            "byte_budget_truncated": False,
        },
    }
    if ordinary_work is not None:
        ordinary = _ordinary_work_snapshot(ordinary_work, now)
        result["ordinary_work"] = ordinary
        result["coverage"]["ordinary_work_available"] = ordinary["available"]
        missing = result["coverage"]["missing"]
        marker = "ordinary_delegated_work_requires_separate_owner_binding"
        missing.remove(marker)
        if not ordinary["available"]:
            missing.append("ordinary_delegated_work_current_status_unknown")
        else:
            result["coverage"]["ordinary_work_truncated"] = ordinary["truncated"]
    while len(canonical_json_bytes(result)) > _MAX_BYTES:
        if observations:
            observations.pop()
            result["coverage"]["observations_truncated"] = True
        elif own_work:
            own_work.pop()
            result["coverage"]["commitments_truncated"] = True
        elif result.get("ordinary_work", {}).get("jobs"):
            result["ordinary_work"]["jobs"].pop()
            result["ordinary_work"]["truncated"] = True
            result["coverage"]["ordinary_work_truncated"] = True
        else:
            raise RuntimeError("initiative context envelope exceeds its bound")
        result["coverage"]["byte_budget_truncated"] = True
    return result
