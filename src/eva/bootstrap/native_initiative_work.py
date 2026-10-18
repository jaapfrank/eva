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


from eva.application.initiative_execution import NativeInitiativeDelegation


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
    grant = authority.digital_grant(profile)
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
        service.store.admit(spec=spec, title="Eigen initiatief van Eva",
            summary="Native initiatief; resultaat wacht op digitale uitvoering.",
            worker_role="digital-worker", retention_policy_id="initiative-private-work:v1",
            retention_until=now + timedelta(days=7), occurred_at=now, _connection=conn,
            _native_guard=guard)
        authority.timeline._scope(conn)
        transition_timeline_thread(conn, authority.owner._scope, thread_id=claim.thread_id,
            expected_revision=claim.revision, checkpoint_id=claim.thread_id + ":digital-admitted",
            phase="initiative_work_waiting", status="waiting_for_proof",
            event_type="PERSONAL_INITIATIVE_WORK_ADMITTED", occurred_at=now,
            data={"intent_id": claim.intent_id, "origin": "eva_initiative",
                  "work_id": spec.work_id, "spec_sha256": spec.sha256,
                  "mandate_sha256": guard.mandate_sha})
    return NativeInitiativeDelegation(spec.work_id)


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
