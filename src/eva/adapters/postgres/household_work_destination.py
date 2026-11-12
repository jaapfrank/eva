"""Explicit household announcement destinations on the existing task timeline.

Task origins remain sealed. Destination changes and output claims share one lock;
a claimed output is never automatically offered again at a different endpoint.
"""
import sqlalchemy as sa
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.delegated_work import HouseholdWorkOrigin, DelegatedHouseholdResearchSpec
from eva.contracts.delegated_digital_work import DelegatedHouseholdDigitalWorkSpec
from eva.adapters.postgres.operational_timeline import (
    operational_events as events, operational_checkpoints as checkpoints,
    insert_initial_timeline_thread, transition_timeline_thread,
)
from eva.adapters.postgres.household_work_sessions import verify_origin

EVENT = "HOUSEHOLD_WORK_DESTINATION_SET"
TRANSPORT = "home_assistant_announce"


def output_lock(connection, work_id):
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                       {"key": "household-work-output:" + work_id})


def destination_thread(work_id):
    return "household-work-destination:" + canonical_sha256({"work_id": work_id})


def destination_exists(scope, work_id):
    return sa.exists(sa.select(1).select_from(events).where(
        events.c.tenant_id == scope.tenant_id, events.c.installation_id == scope.installation_id,
        events.c.event_type == EVENT, events.c.payload["data"]["work_id"].astext == work_id))


def read_destination(connection, scope, work_id):
    row = connection.execute(sa.select(events).where(
        events.c.tenant_id == scope.tenant_id, events.c.installation_id == scope.installation_id,
        events.c.thread_id == destination_thread(work_id))
        .order_by(events.c.sequence.desc()).limit(1)).mappings().one_or_none()
    if row is None:
        return None
    data = row["payload"].get("data", {})
    if (row["event_type"] != EVENT or data.get("work_id") != work_id
            or bytes(row["payload_sha256"]).hex() != canonical_sha256(row["payload"])):
        raise ValueError("household destination provenance differs")
    return data


def delivery_status(connection, scope, work_id):
    phase = connection.scalar(sa.select(checkpoints.c.phase).select_from(
        events.join(checkpoints, sa.and_(
            events.c.tenant_id == checkpoints.c.tenant_id,
            events.c.installation_id == checkpoints.c.installation_id,
            events.c.thread_id == checkpoints.c.thread_id))).where(
        events.c.tenant_id == scope.tenant_id, events.c.installation_id == scope.installation_id,
        events.c.event_type == "HOUSEHOLD_WORK_RESULT_CLAIMED",
        events.c.payload["data"]["work_id"].astext == work_id).limit(1))
    return phase if phase in {"claimed", "deferred", "offered", "ambiguous", "completed", "interrupted"} else "not_claimed"


def validate_destination(connection, scope, authority, spec, data):
    authority.validate_current()
    if (not isinstance(spec, (DelegatedHouseholdResearchSpec, DelegatedHouseholdDigitalWorkSpec))
            or not isinstance(data, dict)
            or set(data) != {"work_id", "spec_sha256", "request_origin", "satellite_id", "device_id", "request_key", "transport"}
            or data["work_id"] != spec.work_id or data["spec_sha256"] != spec.sha256
            or data["transport"] != TRANSPORT):
        raise ValueError("household destination differs from its sealed task")
    request = HouseholdWorkOrigin.model_validate(data["request_origin"])
    for origin in (spec.origin, request):
        authority.validate_device(origin.satellite_id, origin.device_id)
        if origin.actor_id != "household-device:" + origin.device_id:
            raise ValueError("household destination actor differs")
        verify_origin(connection, scope, origin, require_open=False, now=authority.clock())
    if any(getattr(spec.origin, key) != getattr(request, key) for key in (
            "tenant_id", "installation_id", "household_id", "consent_operation_id",
            "household_policy_sha256", "research_policy_sha256")):
        raise ValueError("household destination crossed its audience")
    policy = authority.policy
    if (request.research_policy_sha256 != authority.sha256
            or any(getattr(request, key) != getattr(policy, key) for key in (
                "tenant_id", "installation_id", "household_id", "consent_operation_id", "household_policy_sha256"))):
        raise ValueError("household destination authority changed")
    authority.validate_device(data["satellite_id"], data["device_id"])
    return data


def save_destination(connection, scope, authority, spec, request_origin, satellite_id, request_key):
    output_lock(connection, spec.work_id)
    authority.validate_current()
    device_id = authority.policy.devices.get(satellite_id)
    if device_id is None:
        raise ValueError("household destination is not admitted")
    data = dict(work_id=spec.work_id, spec_sha256=spec.sha256,
        request_origin=request_origin.to_payload(), satellite_id=satellite_id,
        device_id=device_id, request_key=request_key, transport=TRANSPORT)
    validate_destination(connection, scope, authority, spec, data)
    old = read_destination(connection, scope, spec.work_id)
    if old is not None and old["request_key"] == request_key:
        if old != data:
            raise ValueError("household destination request changed on replay")
        return data
    claimed = connection.scalar(sa.select(sa.exists(sa.select(1).select_from(events).where(
        events.c.tenant_id == scope.tenant_id, events.c.installation_id == scope.installation_id,
        events.c.event_type == "HOUSEHOLD_WORK_RESULT_CLAIMED",
        events.c.payload["data"]["work_id"].astext == spec.work_id))))
    if claimed:
        raise ValueError("household output already claimed; its destination cannot change")
    thread = destination_thread(spec.work_id)
    if old is None:
        insert_initial_timeline_thread(connection, scope, thread_id=thread,
            checkpoint_id=thread + ":1", occurred_at=authority.clock(),
            event_type=EVENT, phase="selected", status="active", data=data)
    else:
        revision = connection.scalar(sa.select(checkpoints.c.revision).where(
            checkpoints.c.tenant_id == scope.tenant_id, checkpoints.c.installation_id == scope.installation_id,
            checkpoints.c.thread_id == thread))
        transition_timeline_thread(connection, scope, thread_id=thread, expected_revision=revision,
            checkpoint_id=thread + ":" + str(revision + 1), occurred_at=authority.clock(),
            event_type=EVENT, phase="selected", status="active", data=data)
    return data


def admit_with_destination(service, turn, *, destination_satellite_id=None, request_key, **arguments):
    """Admission and optional destination become visible in the same transaction."""
    server, store = service.server, service.store
    if destination_satellite_id is None and not arguments.get("resumes_work_id") and not hasattr(store, "_engine"):
        return store.admit(**arguments)
    with store._engine.begin() as connection:
        store._scope(connection)
        admitted = store.admit(**arguments, _connection=connection)
        parent_id = arguments.get("resumes_work_id")
        inherited = read_destination(connection, store.scope, parent_id) if parent_id else None
        if destination_satellite_id is not None:
            origin = service.origin(turn)
            verify_origin(connection, store.scope, origin, require_open=True, now=server.authority.clock())
            save_destination(connection, store.scope, server.authority, admitted.work.spec,
                origin, destination_satellite_id, request_key)
        elif inherited is not None:
            previous = store._load_terminal_locked(connection, work_id=parent_id,
                missing_message="household destination parent absent")
            validate_destination(connection, store.scope, server.authority, previous.spec, inherited)
            # Continue the explicit destination of the same task lineage. The
            # task's original request/execution identity is never rewritten.
            origin = HouseholdWorkOrigin.model_validate(inherited["request_origin"])
            save_destination(connection, store.scope, server.authority, admitted.work.spec,
                origin, inherited["satellite_id"], "inherit:" + request_key)
        return admitted
