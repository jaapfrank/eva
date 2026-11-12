"""Host-only native handoff through the existing digital work queue."""
from datetime import UTC, datetime, timedelta
from functools import partial
import hashlib
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.adapters.postgres.initiative_execution import PostgresInitiativeAuthority, InitiativeMandateDenied
from eva.adapters.postgres.operational_timeline import transition_timeline_thread
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.contracts.canonical_json import canonical_sha256
from eva.bootstrap.native_work_assets import prepare_native_work_assets


from eva.application.initiative_execution import NativeInitiativeDelegation, unstarted_profile_refresh, certified_preparation_evidence


def configured_initiative_authority(owner_store):
    path = os.environ.get("EVA_INITIATIVE_MANDATE_FILE")
    if not path:
        base = os.environ.get("EVA_MCP_AUTONOMY_MANDATE_FILE")
        if not base:
            raise InitiativeMandateDenied("initiative mandate unavailable")
        path = str(Path(base).with_name("initiative-mandate.json"))
    return PostgresInitiativeAuthority(owner_store,
        partial(read_runtime_secret_file, path, setting_name="EVA_INITIATIVE_MANDATE_FILE"))


def admit_native_initiative(authority, claim, service):
    """Atomically commit queue admission and the parent's pending outcome."""
    if service is None or service.digital_profile is None:
        raise InitiativeMandateDenied("native digital work is not composed")
    guard, turn = authority.bind(claim)
    profile = service.digital_profile
    grant = guard.restrict_work_grant(authority.digital_grant(profile))
    now = service.now()
    origin = {"kind": "eva_initiative", "intent_id": claim.intent_id,
        "thread_id": claim.thread_id, "claim_revision": claim.revision,
        "mandate_sha256": guard.mandate_sha}
    work_id = "work:eva-initiative:" + claim.intent_id.removeprefix("personal-interest:")
    spec = profile.build_spec(initiative_origin=origin, grant=grant,
        source={key: getattr(turn, key) for key in (
            "tenant_id", "installation_id", "principal_id", "conversation_id", "message_id")} |
            {"audience": {"kind": "private_person", "principal_id": turn.principal_id}},
        source_ingress_sha256=canonical_sha256(PostgresConversationJournal._ingress_data(turn)),
        work_id=work_id, execution_id=work_id,
        episode_id=str(uuid5(NAMESPACE_URL, "initiative-digital-work:" + canonical_sha256(origin))),
        generation=1, parent_task_id=claim.intent_id,
        owner_principal_id=str(service.owner_principal_id),
        principal_binding_id=str(service.principal_binding_id),
        task=turn.text, created_at=now, deadline=now + timedelta(seconds=grant.timeout_seconds))
    if (service.store._engine is not authority.owner._engine
            or service.owner_principal_id != authority.owner._owner
            or service.principal_binding_id != authority.owner._binding
            or service.conversation_scope != authority.owner._scope
            or service.store.scope != authority.owner._authority):
        raise InitiativeMandateDenied("native handoff differs from its host owner scope")
    with authority.owner._engine.begin() as conn:
        guard.check_locked(conn)
        prepare_native_work_assets(service, spec, turn)
        admission = service.store.admit(spec=spec, title="Eigen initiatief van Eva",
            summary="Native initiatief; resultaat wacht op digitale uitvoering.",
            worker_role="digital-worker", retention_policy_id="initiative-private-work:v1",
            retention_until=now + timedelta(days=7), occurred_at=now, _connection=conn,
            _native_guard=guard)
        authority.timeline._scope(conn)
        admission_checkpoint = transition_timeline_thread(conn, authority.owner._scope, thread_id=claim.thread_id,
            expected_revision=claim.revision, checkpoint_id=claim.thread_id + ":digital-admitted",
            phase="initiative_work_waiting", status="waiting_for_proof",
            event_type="PERSONAL_INITIATIVE_WORK_ADMITTED", occurred_at=now,
            data={"intent_id": claim.intent_id, "origin": "eva_initiative",
                  "work_id": spec.work_id, "spec_sha256": spec.sha256,
                  "mandate_sha256": guard.mandate_sha,
                  # Immutable admission snapshot; current status is projected
                  # from the existing persisted queue row, never this old copy.
                  **admission.work.effect_state})
        if admission.work.state == "needs_help":
            service.store._settle_native_admission_block(conn, admission.work,
                admission_revision=admission_checkpoint.revision, now=now)
    return NativeInitiativeDelegation(spec.work_id)


def resume_native_initiative(service, previous, *, created, recovery_ingress_sha256=None,
        recovery_conversation_id=None, recovery_message_id=None, _profile_refresh=False,
        _certified_prepare_recovery=False, _repair_rate_limit_recovery=False):
    """Retain the native namespace for explicit recovery or proven unstarted refresh."""
    import sqlalchemy as sa
    from eva.adapters.postgres.current_catalog import delegated_work_runs
    from eva.adapters.postgres.delegated_work import DelegatedWorkAdmission, DelegatedWorkConflict
    from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline
    from eva.adapters.postgres.operational_timeline import operational_checkpoints
    from eva.contracts.delegated_digital_work import DelegatedInitiativeDigitalWorkSpec
    from eva.contracts.timestamps import parse_utc_timestamp
    loader = service.store._initiative_authority_loader
    if loader is None or service.digital_profile is None:
        raise InitiativeMandateDenied("native recovery authority is not composed")
    authority = loader()
    if (service.store._engine is not authority.owner._engine
            or service.owner_principal_id != authority.owner._owner
            or service.principal_binding_id != authority.owner._binding
            or service.conversation_scope != authority.owner._scope
            or service.store.scope != authority.owner._authority):
        raise InitiativeMandateDenied("native recovery differs from its host owner scope")
    now = service.now()
    old = previous.spec
    if type(old) is not DelegatedInitiativeDigitalWorkSpec:
        raise InitiativeMandateDenied("native recovery requires its original typed source")
    _, mandate_sha = authority.policy()
    previous_mandate_sha = authority._work_mandate(old)
    preparation = certified_preparation_evidence(previous) if _certified_prepare_recovery else None
    if _repair_rate_limit_recovery and (_profile_refresh or _certified_prepare_recovery):
        raise InitiativeMandateDenied("automatic repair recovery reasons cannot be mixed")
    if _certified_prepare_recovery and (_profile_refresh or preparation is None):
        raise InitiativeMandateDenied("certified recovery requires its exact historical preparation failure")
    if _profile_refresh or _certified_prepare_recovery:
        from eva.adapters.postgres.delegated_work import DelegatedWorkReleaseSelector
        old_release = DelegatedWorkReleaseSelector.from_spec(old)
        current_release = service.digital_profile.release
        if ((_profile_refresh and not unstarted_profile_refresh(previous)) or mandate_sha != previous_mandate_sha
                or old_release.worker_program_sha256 == current_release.worker_program_sha256
                or any(getattr(old_release, name) != getattr(current_release, name) for name in (
                    "worker_profile_release_sha256", "model_profile_release_sha256",
                    "engine_release_sha256", "output_schema_sha256"))):
            raise InitiativeMandateDenied("automatic refresh requires unstarted obsolete work under the same mandate")
    elif _repair_rate_limit_recovery:
        if mandate_sha != previous_mandate_sha:
            raise InitiativeMandateDenied("automatic repair recovery mandate changed")
    elif not all(isinstance(v, str) and v for v in (
            recovery_ingress_sha256, recovery_conversation_id, recovery_message_id)):
        raise InitiativeMandateDenied("explicit native recovery requires its owner ingress")
    current_grant = authority.digital_grant(service.digital_profile)
    if not set(old.grant.capability_ids) <= set(current_grant.capability_ids):
        raise InitiativeMandateDenied("native recovery grant no longer authorized")
    next_id = "work:eva-resume:" + str(uuid5(service.store.scope.installation_id,
        f"{old.work_id}:{old.generation + 1}"))
    spec = service.digital_profile.build_spec(initiative_origin=old.origin.model_dump(mode="json"),
        grant=old.grant, source=old.source.model_dump(mode="json"),
        source_ingress_sha256=old.source_ingress_sha256, execution_id=old.execution_id,
        work_id=next_id, episode_id=old.episode_id, generation=old.generation + 1,
        parent_task_id=old.work_id, owner_principal_id=old.owner_principal_id,
        principal_binding_id=old.principal_binding_id, task=old.task, created_at=created,
        deadline=created + timedelta(seconds=old.grant.timeout_seconds))
    queue = PostgresOperationalTimeline(service.store._engine, service.store.scope)
    with authority.owner._engine.begin() as conn:
        authority.owner._authorize(conn, now)
        row = conn.execute(authority.owner._query().where(
            operational_checkpoints.c.thread_id == old.origin.thread_id)
            .with_for_update(of=operational_checkpoints)).one_or_none()
        if row is None or row.phase == "personal_stopped" or row.status == "stopped":
            raise InitiativeMandateDenied("native recovery source is absent or stopped")
        queue._scope(conn)
        # Parent lock serializes duplicate requests before any new queue admission.
        # A read-only duplicate must not invert the running queue-to-parent lock order.
        existing_row = conn.execute(sa.select(delegated_work_runs).where(
            delegated_work_runs.c.tenant_id == service.store.scope.tenant_id,
            delegated_work_runs.c.installation_id == service.store.scope.installation_id,
            delegated_work_runs.c.work_id == next_id)).mappings().one_or_none()
        existing = (None if existing_row is None else
                    service.store._validated(conn, existing_row))
        if existing is not None:
            actual = existing.spec
            if (type(actual) is not DelegatedInitiativeDigitalWorkSpec
                    or actual.origin != old.origin or actual.parent_task_id != old.work_id
                    or actual.generation != old.generation + 1 or actual.source != old.source
                    or actual.source_ingress_sha256 != old.source_ingress_sha256
                    or actual.execution_id != old.execution_id or actual.grant != old.grant
                    or actual.task != old.task or actual.episode_id != old.episode_id
                    or actual.owner_principal_id != old.owner_principal_id
                    or actual.principal_binding_id != old.principal_binding_id
                    or existing.stop_requested_at is not None):
                raise DelegatedWorkConflict("native successor identity differs")
            authority.timeline._scope(conn)
            authority._retained_work_source(existing, now=now, _connection=conn)
            return DelegatedWorkAdmission("already_present", existing)
        current = service.store._load_terminal_locked(conn, work_id=old.work_id,
            missing_message="native previous attempt absent")
        if current.spec.sha256 != old.sha256:
            raise DelegatedWorkConflict("native previous attempt changed")
        if parse_utc_timestamp(spec.deadline) <= now:
            raise InitiativeMandateDenied("native recovery admission deadline expired")
        if _profile_refresh and not unstarted_profile_refresh(current):
            raise InitiativeMandateDenied("unstarted refresh evidence changed before admission")
        if _certified_prepare_recovery and certified_preparation_evidence(current) != preparation:
            raise InitiativeMandateDenied("certified preparation evidence changed before admission")
        authority.timeline._scope(conn)
        guard, source_turn = authority.bind_resume_work(current, spec, now=now, _connection=conn)
        if guard.mandate_sha != mandate_sha:
            raise InitiativeMandateDenied("native recovery mandate changed during admission")
        rate_limit_evidence = None
        if _repair_rate_limit_recovery:
            rate_limit_evidence = _repair_rate_limit_evidence(
                authority, current, source_turn, connection=conn, now=now)
            # The receipt reader uses the original execution scope. Restore the
            # admission scope before continuing within this same transaction.
            authority.timeline._scope(conn)
        prepare_native_work_assets(service, spec, source_turn)
        admission = service.store.admit(spec=spec, title=current.title, summary=current.summary,
            worker_role=current.worker_role, retention_policy_id=current.retention_policy_id,
            retention_until=current.retention_until, occurred_at=created,
            resumes_work_id=old.work_id, _connection=conn, _native_guard=guard,
            _native_preparation_evidence=preparation)
        authority.timeline._scope(conn)
        admission_checkpoint = transition_timeline_thread(conn, authority.owner._scope, thread_id=old.origin.thread_id,
            expected_revision=row.revision,
            checkpoint_id=old.origin.thread_id + ":digital-resumed:" + str(spec.generation),
            phase="initiative_work_waiting", status="waiting_for_proof",
            event_type="PERSONAL_INITIATIVE_WORK_RESUMED", occurred_at=created,
            data={"intent_id": old.origin.intent_id, "origin": "eva_initiative",
                "work_id": spec.work_id, "spec_sha256": spec.sha256,
                "mandate_sha256": mandate_sha, "previous_mandate_sha256": previous_mandate_sha,
                "generation": spec.generation,
                **admission.work.effect_state,
                "previous_work_id": old.work_id, "previous_spec_sha256": old.sha256,
                "source_ingress_sha256": old.source_ingress_sha256,
                "recovery_kind": ("host_repair_rate_limit_recovery" if _repair_rate_limit_recovery else
                    "host_certified_prepare_recovery" if _certified_prepare_recovery else
                    "host_unstarted_profile_refresh" if _profile_refresh else "explicit_private_owner_native_resume"),
                **({"rate_limit_evidence": rate_limit_evidence,
                    "rate_limit_evidence_sha256": canonical_sha256(rate_limit_evidence)}
                    if rate_limit_evidence is not None else {}),
                **({"preparation_evidence": preparation,
                    "preparation_evidence_sha256": canonical_sha256(preparation)} if _certified_prepare_recovery else {}),
                **({"unstarted_evidence_sha256": canonical_sha256({
                    "work_id": old.work_id, "spec_sha256": old.sha256,
                    "claim_count": 0, "call_id": None, "call_started_at": None,
                    "error_code": "unstarted_profile_superseded"})} if _profile_refresh else {}),
                "recovery_ingress_sha256": (canonical_sha256(rate_limit_evidence) if rate_limit_evidence is not None else canonical_sha256({
                    "work_id": old.work_id, "spec_sha256": old.sha256,
                    "current_program_sha256": spec.worker_program_sha256})
                    if (_profile_refresh or _certified_prepare_recovery) else recovery_ingress_sha256),
                "recovery_conversation_id": recovery_conversation_id,
                "recovery_message_id": recovery_message_id})
        if admission.work.state == "needs_help":
            service.store._settle_native_admission_block(conn, admission.work,
                admission_revision=admission_checkpoint.revision, now=created)
        return admission


def refresh_one_unstarted_native(service):
    """Existing idle-loop maintenance; no model call, new planner or owner ingress."""
    if service is None or service.digital_profile is None:
        return "idle"
    previous = service.store.retire_one_unstarted_native(
        release=service.digital_profile.release, owner_principal_id=service.owner_principal_id,
        principal_binding_id=service.principal_binding_id, now=service.now())
    if previous is None:
        return "idle"
    from eva.adapters.postgres.delegated_work import DelegatedWorkConflict
    try:
        admission = resume_native_initiative(service, previous, created=service.now(),
            _profile_refresh=True)
    except (InitiativeMandateDenied, DelegatedWorkConflict):
        # No execution started. Preserve that fact without retrying a changed
        # authority forever or preventing the next ordinary model review.
        service.store.withdraw_unstarted_refresh(previous, now=service.now())
        return "refresh_not_authorized"
    return "profile_refreshed" if admission.disposition == "inserted" else "already_present"


def _repair_rate_limit_evidence(authority, previous, source, *, connection, now):
    """Prove a rejected first attempt and no actions under the admission locks."""
    from eva.adapters.postgres.initiative_execution import RATE_LIMIT_FINGERPRINT
    from eva.adapters.postgres.self_repair import current_repair_binding
    from eva.bootstrap.delegated_digital_runner import _recorded_action_slots
    from eva.contracts.timestamps import format_required_utc_timestamp
    if (previous.spec.generation != 1 or previous.state != "failed"
            or previous.error_code != "provider_transient"
            or previous.error_fingerprint != RATE_LIMIT_FINGERPRINT
            or previous.usage is None or previous.usage.tool_calls != 0
            or previous.result is not None or previous.stop_requested_at is not None
            or previous.lease_owner is not None or previous.lease_expires_at is not None
            or previous.call_id is None or previous.call_started_at is None
            or previous.completed_at is None
            or now < previous.completed_at + timedelta(minutes=5)
            or now >= previous.retention_until):
        raise InitiativeMandateDenied("repair rate-limit recovery is not eligible")
    events = authority.timeline.events(previous.spec.origin.thread_id)
    adopted = next((e for e in events if e.event_type == "PERSONAL_INTENT_ADOPTED"), None)
    incident = None if adopted is None else adopted.payload["data"].get("repair_incident_id")
    if incident is None:
        raise InitiativeMandateDenied("automatic rate-limit recovery requires native self-repair")
    binding = current_repair_binding(authority, incident, now=now, _connection=connection)
    if _recorded_action_slots(authority.owner._engine, previous.spec, source, connection=connection):
        raise InitiativeMandateDenied("repair rate-limit recovery has prior action receipts")
    return {
        "contract": "EvaNativeRepairRateLimitRecovery/v1",
        "work_id": previous.spec.work_id, "spec_sha256": previous.spec.sha256,
        "incident_id": incident, "incident_event_sha256": binding["incident_event_sha256"],
        "scope_sha256": binding["scope_sha256"],
        "usage_sha256": canonical_sha256(previous.usage.to_payload()),
        "error_fingerprint": previous.error_fingerprint, "tool_calls": 0, "action_slots": 0,
        "completed_at": format_required_utc_timestamp(previous.completed_at),
        "not_before": format_required_utc_timestamp(previous.completed_at + timedelta(minutes=5)),
        "resumed_at": format_required_utc_timestamp(now),
    }


def resume_one_rate_limited_repair(service):
    """One bounded continuation via the existing service lane, never a retry loop."""
    if service is None or service.digital_profile is None or service.store._initiative_authority_loader is None:
        return "idle"
    import sqlalchemy as sa
    from eva.adapters.postgres.current_catalog import delegated_work_runs
    from eva.adapters.postgres.delegated_work import DelegatedWorkConflict
    from eva.adapters.postgres.initiative_execution import RATE_LIMIT_FINGERPRINT
    from eva.application.conversation import ConversationConflict
    from eva.adapters.postgres.operational_timeline import operational_events
    authority = service.store._initiative_authority_loader()
    table = delegated_work_runs
    later = table.alias("repair_successor")
    now = service.now()
    with service.store._engine.connect() as connection:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        authority.timeline._scope(connection)
        events = operational_events.c
        repair_threads = connection.execute(sa.select(events.thread_id).where(
            events.tenant_id == authority.owner._scope.tenant_id,
            events.installation_id == authority.owner._scope.installation_id,
            events.event_type == "PERSONAL_INTENT_ADOPTED",
            events.payload["data"]["repair_incident_id"].astext.is_not(None),
        )).scalars().all()
    if not repair_threads:
        return "idle"
    # Candidate discovery spans two immutable scopes. Only the final admission
    # shares the locked transaction; discovery must not rebind an RLS scope.
    with service.store._engine.connect() as connection:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        service.store._scope(connection)
        ids = connection.execute(sa.select(table.c.work_id).where(
            table.c.spec_payload["origin"]["thread_id"].astext.in_(repair_threads),
            table.c.tenant_id == service.store.scope.tenant_id,
            table.c.installation_id == service.store.scope.installation_id,
            table.c.owner_principal_id == service.owner_principal_id,
            table.c.principal_binding_id == service.principal_binding_id,
            table.c.generation == 1, table.c.state == "failed",
            table.c.error_code == "provider_transient",
            table.c.error_fingerprint == bytes.fromhex(RATE_LIMIT_FINGERPRINT),
            table.c.completed_at <= now - timedelta(minutes=5),
            table.c.retention_until > now, table.c.stop_requested_at.is_(None),
            table.c.spec_payload["contract"].astext == "DelegatedInitiativeDigitalWork/v1",
            table.c.usage_payload["tool_calls"].astext == "0",
            ~sa.exists(sa.select(later.c.work_id).where(
                later.c.tenant_id == table.c.tenant_id,
                later.c.installation_id == table.c.installation_id,
                later.c.parent_task_id == table.c.work_id)),
        ).order_by(table.c.completed_at).limit(20)).scalars().all()
    for work_id in ids:
        try:
            previous = service.store.load(work_id, **service._owner)
            admission = resume_native_initiative(service, previous, created=now,
                _repair_rate_limit_recovery=True)
        except (InitiativeMandateDenied, DelegatedWorkConflict, ConversationConflict, ValueError):
            # Retain the original failure. No new work, false proof or allowance.
            continue
        return "repair_resumed" if admission.disposition == "inserted" else "already_present"
    return "idle"


def subscription_initiative_available(authority, model):
    """Current private owner authority plus the existing subscription, not quota headroom."""
    owner = authority.owner
    predicate = getattr(model, "existing_subscription_autonomy", None)
    if (not callable(predicate) or not predicate(
            tenant_id=owner._tenant_id, installation_id=owner._installation_id)):
        return False
    authority.policy()  # Rechecks verified owner, host policy and durable revocation.
    return True


def admit_initiative_review(authority, model, budget, claim, *, now):
    """Use the review's durable dispatch claim instead of an unrelated memory quota.

    The original reflection ledger and its source hash remain immutable. Usage is
    accounted on this same review's WILL_MODEL_STARTED/decision events. Missing
    subscription authority retains the ordinary pinned reflection admission.
    """
    owner = authority.owner
    authority.policy()
    prefix = "sensory:initiative-review:" + hashlib.sha256(
        str(owner._owner).encode()).hexdigest()[:24] + ":"
    if (claim.event.source != "initiative-review"
            or claim.event.event_type != "private_opportunity_review"
            or not claim.thread_id.startswith(prefix)
            or not claim.thread_id[len(prefix):].isdigit()
            or claim.event.source_event_id != claim.thread_id.removeprefix("sensory:")
            or claim.operation_id != claim.event.source_event_id):
        raise InitiativeMandateDenied("private review differs from its host owner source")
    state = authority.timeline.load(claim.thread_id)
    if (state.phase != "will_model_started" or state.status != "active"
            or state.revision != claim.revision or state.deadline is None
            or state.deadline <= now):
        raise InitiativeMandateDenied("private review dispatch claim is not current")
    events = authority.timeline.events(claim.thread_id)
    original = {"event_type": claim.event.event_type, "source": claim.event.source,
        "source_event_id": claim.event.source_event_id, "payload": claim.event.payload}
    if not events or events[0].payload.get("data") != original:
        raise InitiativeMandateDenied("private review original source differs")
    if not subscription_initiative_available(authority, model):
        return budget.admit_background_reflection(tenant_id=owner._tenant_id,
            installation_id=owner._installation_id, principal_id=str(owner._owner),
            now=now, reservation_id=claim.thread_id, spread_over_day=True)
    from eva.adapters.postgres.runtime_resource_usage import BackgroundReflectionAdmission
    person_used, installation_used = budget.read_usage_counts(
        principal_id=str(owner._owner), now=now)
    return BackgroundReflectionAdmission(True, budget._day(now), person_used,
        installation_used, "existing_subscription_review")


def private_contact_delivery(store, delivery, *, can_send):
    """Run the existing guarded private expression transport in the service loop."""
    from eva.application.free_will import ProactiveExpressionWorker
    clock = lambda: datetime.now(UTC).replace(microsecond=0)
    def authorize(claim):
        if not can_send():
            raise InitiativeMandateDenied("private contact deferred by current foreground activity")
        store.authorize_private_expression(claim, now=clock())
        if not can_send():
            raise InitiativeMandateDenied("private contact deferred by new foreground activity")
    worker = ProactiveExpressionWorker(store, delivery, channel="telegram", before_send=authorize)
    return lambda: worker.run_once(now=clock())
