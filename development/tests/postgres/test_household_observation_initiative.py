"""Real PostgreSQL source-to-decision handoff; synthetic model and no network."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
import hashlib

import pytest
import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import CheckpointConflict, operational_events
from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.application.conversation import ConversationModelUsage, IdentityProfile
from eva.application.free_will import FreeWillDecision, PersonalInitiativeProposal, SensoryDecisionWorker
from eva.application.household_initiative import HouseholdInitiativeBinding, HouseholdInitiativeContext
from eva.application.memory import PresenceMemory
from eva.application.sensory import SensoryEvent
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.memory import MemoryRecall
from tests.postgres.test_owner_initiatives import costless_native, NOW


def _binding(tenant, device="a" * 32):
    return HouseholdInitiativeBinding(tenant, "home", "family", "household-device:" + device,
        "assist_satellite.room_" + device[0], device, "household-consent:fixture", "b" * 64)


def _shared(engine, tenant, binding=None, authorize=None, owner="owner"):
    return PostgresSensoryEventStore(engine, tenant_id=tenant, installation_id="home",
        initiative_principal_id=owner, initiative_authorizer=authorize or (lambda: ({"capabilities": []}, None)),
        context_reviews_only=True, household_binding=binding or _binding(tenant))


def _signal(name, *, at=NOW, domain="binary_sensor"):
    return SensoryEvent("home_assistant", "home-assistant:" + hashlib.sha256(name.encode()).hexdigest(),
        "state_changed", at, {"entity_id": domain + ".synthetic_signal", "old_state": "off",
            "new_state": "on", "friendly_name": "Synthetic household signal",
            "attributes": {"secret": "must-not-enter-shared-context"}, "private_context": "private-text"})


def _empty_shared_memory(query):
    return PresenceMemory(MemoryRecall.model_validate({
        "contract": "MemoryRecall/v1", "schema_version": 1,
        **{key: getattr(query, key) for key in ("recall_id", "query_purpose", "tenant_id", "principal_id",
            "conversation_id", "project_id", "requested_valid_at", "projection_version")},
        "admitted_fact_ids_sha256": canonical_sha256([]), "selected": [], "excluded_reasons": {},
        "scope_sha256": canonical_sha256(query.scope), "created_at": query.requested_valid_at,
    }), ())


def test_shared_observation_uses_household_memory_and_adopts_existing_private_action(costless_native):
    auth, old, interests, owner, originals, path, policy = costless_native
    owner.stop(old.intent_id, now=NOW)
    auth.policy()
    binding = _binding(old.tenant_id)
    shared = _shared(owner._engine, old.tenant_id, binding, auth.policy, old.principal_id)
    signal = _signal("shared-decision", at=NOW - timedelta(seconds=3))
    originals.retain(signal)
    assert shared.next_household_observation(now=NOW) == signal
    assert shared.schedule_household_observation(signal, now=NOW) == "scheduled"
    wrapped = binding.observation_event(signal)
    stored = shared._event_from_thread("sensory:" + wrapped.source_event_id)
    assert stored == wrapped  # The original event timestamp survives routing delay.
    assert binding.matches_event(stored)
    calls, queries, admissions = [], [], []

    def memory(query):
        queries.append(query)
        assert query.audience.kind == "household" and query.principal_id == binding.actor_id
        assert query.subject_ids == (binding.actor_id,)
        return _empty_shared_memory(query)

    def forbidden_history():
        raise AssertionError("shared model must not load private owner history")

    shared.recent_initiatives = forbidden_history

    class Model:
        def evaluate_sensory_event(self, event, identity, memory, attention=None,
                                   initiative_history=(), initiative_capabilities=()):
            calls.append(event)
            assert event == wrapped and memory.confirmed_facts == ()
            assert initiative_history == ()
            assert initiative_capabilities == ("save_private_result",)
            assert "private-text" not in str(event.payload) and "must-not-enter" not in str(event.payload)
            return FreeWillDecision("respond", "A grounded shared signal merits a response", 1,
                "synthetic", "Ik heb het signaal gezien.",
                usage=ConversationModelUsage.measured(model_attempts=1, tool_calls=1, input_tokens=40, output_tokens=20),
                initiative=PersonalInitiativeProposal("A new permitted contribution", "Shared observed context",
                    "Use the existing permitted private action to prepare a useful result"))

    def admit(claim, *, now):
        admissions.append(claim)
        assert shared.context_review_claim_is_current(claim, now=now)
        auth.policy()
        return SimpleNamespace(admitted=True)

    worker = SensoryDecisionWorker(shared, Model(), SimpleNamespace(recall_for_presence=memory),
        IdentityProfile("Eva", "Family companion", ("Be honest",), 80),
        tenant_id=old.tenant_id, installation_id="home", principal_id=old.principal_id,
        household_context=HouseholdInitiativeContext(binding, auth.policy),
        context_review_authorizer=auth.policy, context_review_admission=admit)
    assert worker.run_once(now=NOW) == "respond"
    assert len(calls) == len(queries) == len(admissions) == 1
    assert worker.run_once(now=NOW) == "idle"
    claim = shared.claim_next_household_expression(binding=binding.to_payload(), now=NOW)
    assert claim is not None and claim.text == "Ik heb het signaal gezien."
    shared.authorize_household_expression(claim, binding=binding.to_payload(), now=NOW)
    shared.acknowledge_household_expression(claim, binding=binding.to_payload(),
        disposition="offered", provider_message_id="synthetic-offer", now=NOW)
    assert shared.claim_next_household_expression(binding=binding.to_payload(), now=NOW) is None
    native = interests.claim_next(tenant_id=old.tenant_id, installation_id="home", now=NOW)
    assert native is not None and native.execution_mode == "autonomous_task"
    guard, turn = auth.bind(native)
    assert turn.audience.kind == "private_person" and turn.principal_id == old.principal_id
    assert any(card["title"] == "A new permitted contribution" for card in owner.page(now=NOW)["cards"])
    assert shared.schedule_household_observation(signal, now=NOW) == "already_routed"
    assert originals.claim_next(now=NOW) is None
    assert len(calls) == 1


def test_one_original_is_routed_once_across_two_speakers(postgres_engine):
    tenant = "household-route-once"
    original = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant, installation_id="home")
    signal = _signal(tenant)
    original.retain(signal)
    stores = [_shared(postgres_engine, tenant, _binding(tenant, character * 32)) for character in "ac"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda store: store.schedule_household_observation(signal, now=NOW), stores))
    assert sorted(outcomes) == ["already_routed", "scheduled"]
    events = original._timeline.events("sensory:" + signal.source_event_id)
    assert events[-1].event_type == "SENSORY_EVENT_HOUSEHOLD_ROUTED"
    assert events[-1].payload["data"]["replayed"] is False
    assert sum(len(store._timeline.list_checkpoints(thread_prefix=store._decision_prefix)) for store in stores) == 1


def test_old_claimed_and_uncertain_sources_are_not_replayed_and_lights_stay_model_free(postgres_engine):
    tenant = "household-source-selection"
    original = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant, installation_id="home")
    shared = _shared(postgres_engine, tenant)
    old = _signal("old", at=NOW - timedelta(minutes=6))
    light = _signal("routine", domain="light", at=NOW - timedelta(seconds=4))
    uncertain = _signal("uncertain", at=NOW - timedelta(seconds=2))
    fresh = _signal("fresh", at=NOW - timedelta(seconds=1))
    for signal in (old, light, uncertain, fresh):
        original.retain(signal)
    state = original._timeline.load("sensory:" + uncertain.source_event_id)
    original._timeline.transition(thread_id=state.thread_id, expected_revision=state.revision,
        checkpoint_id=state.thread_id + ":uncertain", phase="will_usage_uncertain", status="needs_help",
        event_type="WILL_USAGE_UNCERTAIN", occurred_at=NOW, data={"usage": None})
    before = original._timeline.events(state.thread_id)
    assert shared.next_household_observation(now=NOW) == fresh
    light_state = original._timeline.load("sensory:" + light.source_event_id)
    assert light_state.phase == "will_decided"
    assert original._timeline.events(light_state.thread_id)[-1].payload["data"]["provider"] == "eva:bounded-prefilter"
    assert original._timeline.events(state.thread_id) == before
    assert original._timeline.load("sensory:" + old.source_event_id).phase == "sensory_observed"
    with pytest.raises(ValueError, match="not fresh"):
        shared.schedule_household_observation(old, now=NOW)
    assert shared.schedule_household_observation(uncertain, now=NOW) == "source_unavailable"


def test_forged_or_cross_scope_source_is_never_admitted(postgres_engine):
    tenant = "household-source-proof"
    original = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant, installation_id="home")
    shared = _shared(postgres_engine, tenant)
    signal = _signal(tenant)
    original.retain(signal)
    with pytest.raises(ValueError, match="retained source"):
        shared.schedule_household_observation(replace(signal, payload={**signal.payload, "new_state": "off"}), now=NOW)
    foreign = _shared(postgres_engine, "other-household-source-proof")
    assert foreign.schedule_household_observation(signal, now=NOW) == "source_unavailable"
    assert original._timeline.load("sensory:" + signal.source_event_id).phase == "sensory_observed"
    assert not shared._timeline.list_checkpoints(thread_prefix=shared._decision_prefix)


def test_corrupted_original_hash_blocks_routing_before_shared_model(postgres_engine):
    tenant = "household-source-hash"
    original = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant, installation_id="home")
    shared = _shared(postgres_engine, tenant)
    signal = _signal(tenant)
    original.retain(signal)
    with postgres_engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE eva_core.operational_events DISABLE TRIGGER USER")
        try:
            connection.execute(sa.update(operational_events).where(
                operational_events.c.thread_id == "sensory:" + signal.source_event_id
            ).values(payload_sha256=b"\0" * 32))
        finally:
            connection.exec_driver_sql("ALTER TABLE eva_core.operational_events ENABLE TRIGGER USER")
    with pytest.raises(RuntimeError, match="source hash differs"):
        shared.next_household_observation(now=NOW)
    assert not shared._timeline.list_checkpoints(thread_prefix=shared._decision_prefix)


def test_revoked_owner_mandate_cannot_adopt_or_prepare_shared_output(costless_native):
    auth, old, interests, owner, originals, path, policy = costless_native
    owner.stop(old.intent_id, now=NOW)
    auth.policy()
    binding = _binding(old.tenant_id)
    shared = _shared(owner._engine, old.tenant_id, binding, auth.policy, old.principal_id)
    signal = _signal("owner-revoked")
    originals.retain(signal)
    assert shared.schedule_household_observation(signal, now=NOW) == "scheduled"
    claim = shared.mark_model_started(shared.claim_next(now=NOW), now=NOW)
    auth.revoke()
    with pytest.raises(InitiativeMandateDenied, match="revoked"):
        shared.complete(claim, FreeWillDecision("respond", "Observed context", 1, "synthetic",
            "This must not be offered.", initiative=PersonalInitiativeProposal(
                "Not allowed after revocation", "Observed context", "Prepare a result")), now=NOW)
    assert shared._timeline.load(claim.thread_id).phase == "will_model_started"
    assert not any(card["title"] == "Not allowed after revocation" for card in owner.page(now=NOW)["cards"])


@pytest.mark.parametrize("household", [False, True])
def test_foreground_arriving_during_recall_defers_without_provider_and_resumes_once(costless_native, household):
    auth, old, interests, owner, originals, path, policy = costless_native
    owner.stop(old.intent_id, now=NOW)
    auth.policy()
    binding = _binding(old.tenant_id)
    if household:
        store = _shared(owner._engine, old.tenant_id, binding, auth.policy, old.principal_id)
        signal = _signal("foreground-shared")
        originals.retain(signal)
        assert store.schedule_household_observation(signal, now=NOW) == "scheduled"
    else:
        store = PostgresSensoryEventStore(owner._engine, tenant_id=old.tenant_id, installation_id="home",
            initiative_principal_id=old.principal_id, initiative_authorizer=auth.policy, context_reviews_only=True)
        assert store.schedule_context_review(now=NOW) == "scheduled"
    foreground, calls, admissions, recalled = [False], [], [], []

    def recall(query):
        recalled.append(query)
        if len(recalled) == 1:
            foreground[0] = True
        return _empty_shared_memory(query)

    def admit(claim, *, now):
        assert store.context_review_claim_is_current(claim, now=now)
        admissions.append(claim)
        return SimpleNamespace(admitted=True)

    class Model:
        def evaluate_sensory_event(self, *args, **kwargs):
            calls.append(args[0])
            return FreeWillDecision("silence", "No useful response now", 0, "synthetic",
                usage=ConversationModelUsage.measured(model_attempts=1, tool_calls=0, input_tokens=40, output_tokens=20))

    worker = SensoryDecisionWorker(store, Model(), SimpleNamespace(recall_for_presence=recall),
        IdentityProfile("Eva", "Family companion", ("Be honest",), 80),
        tenant_id=old.tenant_id, installation_id="home", principal_id=old.principal_id,
        household_context=HouseholdInitiativeContext(binding, auth.policy) if household else None,
        context_review_authorizer=auth.policy, context_review_admission=admit,
        dispatch_ready=lambda: not foreground[0])
    assert worker.run_once(now=NOW) == "deferred"
    assert calls == []
    deferred = store._timeline.load(admissions[0].thread_id)
    assert (deferred.phase, deferred.status, deferred.retry_count) == ("will_waiting_admission", "waiting_for_proof", 0)
    events = store._timeline.events(deferred.thread_id)
    assert events[-1].event_type == "WILL_DISPATCH_DEFERRED"
    assert events[-1].payload["data"]["provider_dispatched"] is False
    assert not any(event.event_type == "WILL_USAGE_UNCERTAIN" for event in events)
    foreground[0] = False
    assert worker.run_once(now=NOW + timedelta(seconds=1)) == "silence"
    assert len(calls) == 1 and len(admissions) == 2
    assert admissions[0].thread_id == admissions[1].thread_id
    assert admissions[0].operation_id == admissions[1].operation_id
    assert admissions[1].revision > admissions[0].revision
    assert worker.run_once(now=NOW + timedelta(seconds=2)) == "idle"
    assert len(calls) == 1
    current = store._timeline.load(deferred.thread_id)
    before = store._timeline.events(current.thread_id)
    with pytest.raises(CheckpointConflict, match="no longer current"):
        store.defer_before_dispatch(admissions[0], reason="foreground_active", now=NOW + timedelta(seconds=2))
    assert store._timeline.events(current.thread_id) == before


def test_unknown_provider_outcome_cannot_be_reclassified_as_pre_dispatch(costless_native):
    auth, old, interests, owner, originals, path, policy = costless_native
    owner.stop(old.intent_id, now=NOW)
    auth.policy()
    store = _shared(owner._engine, old.tenant_id, authorize=auth.policy, owner=old.principal_id)
    signal = _signal("unknown-cannot-defer")
    originals.retain(signal)
    store.schedule_household_observation(signal, now=NOW)
    claim = store.mark_model_started(store.claim_next(now=NOW), now=NOW)
    store.fail(claim, RuntimeError("lost provider response"), now=NOW)
    before = store._timeline.events(claim.thread_id)
    with pytest.raises(CheckpointConflict):
        store.defer_before_dispatch(claim, reason="foreground_active", now=NOW)
    assert store._timeline.events(claim.thread_id) == before
    assert store._timeline.load(claim.thread_id).phase == "will_usage_uncertain"
    assert store.claim_next(now=NOW + timedelta(minutes=6)) is None
    later = NOW + timedelta(days=1)
    new_signal = _signal("new-signal-after-unknown", at=later)
    originals.retain(new_signal)
    assert store.next_household_observation(now=later) is None
    assert store.schedule_context_review(now=later) == "idle"
    assert store._timeline.events(claim.thread_id) == before


def test_private_review_cannot_prepare_household_speech_or_enter_shared_route(costless_native):
    auth, old, interests, owner, originals, path, policy = costless_native
    owner.stop(old.intent_id, now=NOW)
    auth.policy()
    private = PostgresSensoryEventStore(owner._engine, tenant_id=old.tenant_id, installation_id="home",
        initiative_principal_id=old.principal_id, initiative_authorizer=auth.policy, context_reviews_only=True)
    assert private.schedule_context_review(now=NOW) == "scheduled"
    claim = private.mark_model_started(private.claim_next(now=NOW), now=NOW)
    shared = _shared(owner._engine, old.tenant_id, authorize=auth.policy, owner=old.principal_id)
    with pytest.raises(ValueError, match="source differs"):
        shared.schedule_household_observation(claim.event, now=NOW)
    usage = ConversationModelUsage.measured(model_attempts=1, tool_calls=0, input_tokens=40, output_tokens=20)
    private.complete(claim, FreeWillDecision("respond", "A private owner matter", 1, "synthetic",
        "This private content must never be announced", usage=usage), now=NOW)
    assert private._timeline.load(claim.thread_id).phase == "will_decided"
    data = private._timeline.events(claim.thread_id)[-1].payload["data"]
    assert data["disposition"] == "silence" and data["expression"] is None
    assert data["usage"]["input_tokens"] == 40
    assert private.claim_next_expression(now=NOW) is None
    assert not shared._timeline.list_checkpoints(thread_prefix=shared._decision_prefix)


@pytest.mark.parametrize("stage", ["pending", "deferred", "during_recall"])
def test_stale_shared_observation_expires_without_provider_or_replay(postgres_engine, stage):
    tenant = "household-stale-before-dispatch-" + stage
    binding = _binding(tenant)
    originals = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant, installation_id="home")
    store = _shared(postgres_engine, tenant, binding)
    clock, foreground, calls = [NOW], [stage == "deferred"], []
    signal = _signal(tenant, at=NOW - timedelta(seconds=299) if stage == "during_recall" else NOW)
    originals.retain(signal)
    store.schedule_household_observation(signal, now=NOW)
    wrapped = binding.observation_event(signal)

    def recall(query):
        if stage == "during_recall":
            clock[0] += timedelta(seconds=2)
        return _empty_shared_memory(query)

    class Model:
        def evaluate_sensory_event(self, *args, **kwargs):
            calls.append(args)
            raise AssertionError("stale observation must never reach provider")

    worker = SensoryDecisionWorker(store, Model(), SimpleNamespace(recall_for_presence=recall),
        IdentityProfile("Eva", "Family companion", ("Be honest",), 80),
        tenant_id=tenant, installation_id="home", principal_id="owner",
        household_context=HouseholdInitiativeContext(binding, lambda: None),
        context_review_authorizer=lambda: None,
        context_review_admission=lambda *args, **kwargs: SimpleNamespace(admitted=True),
        dispatch_ready=lambda: not foreground[0], clock=lambda: clock[0])
    if stage == "deferred":
        assert worker.run_once(now=NOW) == "deferred"
    if stage != "during_recall":
        clock[0] += timedelta(days=1)
        foreground[0] = False
    assert worker.run_once(now=clock[0]) == ("expired" if stage == "during_recall" else "idle")
    thread = "sensory:" + wrapped.source_event_id
    assert store._timeline.load(thread).phase == "will_observation_expired"
    history = store._timeline.events(thread)
    assert history[-1].event_type == "WILL_OBSERVATION_EXPIRED"
    assert history[-1].payload["data"]["provider_dispatched"] is False
    assert not any(event.event_type == "WILL_USAGE_UNCERTAIN" for event in history)
    assert store._event_from_thread(thread) == wrapped
    assert worker.run_once(now=clock[0] + timedelta(days=1)) == "idle"
    assert calls == []
    assert store.schedule_context_review(now=clock[0] + timedelta(days=1)) == "scheduled"


def test_sustained_signals_share_one_window_then_latest_states_are_batched(postgres_engine):
    tenant = "household-window-batch"
    binding = _binding(tenant)
    originals = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant, installation_id="home")
    store = _shared(postgres_engine, tenant, binding)
    calls = []
    class Model:
        def evaluate_sensory_event(self, event, *args, **kwargs):
            calls.append(event)
            return FreeWillDecision("silence", "Nothing else needed", 0, "synthetic",
                usage=ConversationModelUsage.measured(model_attempts=1, tool_calls=0, input_tokens=40, output_tokens=20))
    worker = SensoryDecisionWorker(store, Model(), SimpleNamespace(recall_for_presence=_empty_shared_memory),
        IdentityProfile("Eva", "Family companion", ("Be honest",), 80),
        tenant_id=tenant, installation_id="home", principal_id="owner",
        household_context=HouseholdInitiativeContext(binding, lambda: None), context_review_authorizer=lambda: None,
        context_review_admission=lambda *args, **kwargs: SimpleNamespace(admitted=True))
    first = _signal("window-first")
    originals.retain(first)
    store.schedule_household_observation(first, now=NOW)
    assert worker.run_once(now=NOW) == "silence"
    retained = []
    for offset in range(1, 21):
        at = NOW + timedelta(seconds=offset)
        event = replace(_signal(f"window-{offset}", at=at), payload={
            **_signal("unused").payload, "entity_id": "binary_sensor.generic_" + str(offset % 2)})
        originals.retain(event)
        retained.append(event)
        assert store.next_household_observation(now=at) is None
        assert store.schedule_context_review(now=at) == "idle"  # Clock fallback cannot bypass the gate.
        assert worker.run_once(now=at) == "idle"
    other_device = _shared(postgres_engine, tenant, _binding(tenant, "c" * 32))
    assert other_device.next_household_observation(now=NOW + timedelta(seconds=299)) is None
    assert len(calls) == 1
    after = NOW + timedelta(seconds=300)
    batch = store.next_household_observation(now=after)
    assert batch.event_type == "household_observation_batch"
    members = batch.payload["observations"]
    assert len(members) == 2
    assert {member["source_event_id"] for member in members} == {event.source_event_id for event in retained[-2:]}
    assert "private-text" not in str(batch.payload) and "must-not-enter" not in str(batch.payload)
    assert binding.matches_event(binding.observation_event(batch))
    for event in retained:
        history = originals._timeline.events("sensory:" + event.source_event_id)
        assert originals._event_from_thread(history[0].thread_id) == event
        assert history[-1].event_type == "SENSORY_EVENT_HOUSEHOLD_GROUPED"
        assert history[-1].payload["data"]["source_payload_sha256"] == history[0].payload_sha256
        assert history[-1].payload["data"]["replayed"] is False
    assert store.next_household_observation(now=after) == batch  # Interrupted batch creation resumes the same source.
    assert store.schedule_household_observation(batch, now=after) == "scheduled"
    assert worker.run_once(now=after) == "silence"
    assert len(calls) == 2
    assert store.next_household_observation(now=after + timedelta(seconds=1)) is None


def test_competing_household_destinations_cannot_dispatch_two_reviews_in_one_window(postgres_engine):
    tenant = "household-window-dispatch-race"
    originals = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant, installation_id="home")
    stores = [_shared(postgres_engine, tenant, _binding(tenant, char * 32)) for char in "ac"]
    claims = []
    for index, store in enumerate(stores):
        signal = _signal(f"competing-{index}")
        originals.retain(signal)
        assert store.schedule_household_observation(signal, now=NOW) == "scheduled"
        claims.append(store.claim_next(now=NOW))
    with ThreadPoolExecutor(max_workers=2) as pool:
        started = list(pool.map(lambda item: item[0].mark_model_started(item[1], now=NOW), zip(stores, claims)))
    assert sum(value is not None for value in started) == 1
    pending = next((store, claim) for store, claim, result in zip(stores, claims, started) if result is None)
    state = pending[0]._timeline.load(pending[1].thread_id)
    assert (state.phase, state.status) == ("will_waiting_admission", "waiting_for_proof")
    data = pending[0]._timeline.events(state.thread_id)[-1].payload["data"]
    assert data["provider_dispatched"] is False and data["reason"] == "observation_window_active"


def test_batch_bounds_and_tampered_source_or_private_member_are_rejected(postgres_engine):
    tenant = "household-window-batch-bounds"
    binding = _binding(tenant)
    originals = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant, installation_id="home")
    store = _shared(postgres_engine, tenant, binding)
    for index in range(20):
        signal = _signal(f"bounded-{index}")
        originals.retain(replace(signal, payload={**signal.payload, "entity_id": f"binary_sensor.generic_{index}"}))
    batch = store.next_household_observation(now=NOW)
    assert 1 <= len(batch.payload["observations"]) <= 16
    from eva.contracts.canonical_json import canonical_json_bytes
    assert len(canonical_json_bytes(batch.payload["observations"])) <= 6144
    forged = replace(batch, payload={**batch.payload, "observations": batch.payload["observations"][:-1]})
    with pytest.raises(ValueError, match="retained source"):
        store.schedule_household_observation(forged, now=NOW)
    member = batch.payload["observations"][0]
    private = {**member, "payload": {**member["payload"], "attributes": {"private": "must-not-pass"}}}
    with pytest.raises(ValueError, match="unnormalized"):
        binding.observation_event(replace(batch, payload={**batch.payload, "observations": [private]}))
    assert store.schedule_household_observation(batch, now=NOW) == "scheduled"


@pytest.mark.parametrize("age_minutes,same_entity,urgency,quiet", [
    (4, False, 1, True), (6, False, 1, False), (20, True, 3, True), (31, True, 3, False)])
def test_existing_cooldowns_include_offered_shared_expression(postgres_engine, age_minutes, same_entity, urgency, quiet):
    tenant = f"household-offered-cooldown-{age_minutes}"
    originals = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant, installation_id="home")
    store = _shared(postgres_engine, tenant)
    binding = _binding(tenant)
    signal = _signal(tenant)
    originals.retain(signal)
    store.schedule_household_observation(signal, now=NOW)
    claim = store.claim_next(now=NOW)
    store.complete(claim, FreeWillDecision("respond", "Grounded first signal", 1, "synthetic", "First response"), now=NOW)
    expression = store.claim_next_household_expression(binding=binding.to_payload(), now=NOW)
    store.acknowledge_household_expression(expression, binding=binding.to_payload(),
        disposition="offered", provider_message_id="synthetic-offered", now=NOW)
    after = NOW + timedelta(minutes=age_minutes)
    next_signal = _signal(tenant + "-next", at=after)
    if not same_entity:
        next_signal = replace(next_signal, payload={**next_signal.payload, "entity_id": "binary_sensor.other_synthetic_signal"})
    originals.retain(next_signal)
    store.schedule_household_observation(next_signal, now=after)
    current = store.claim_next(now=after)
    store.complete(current, FreeWillDecision("respond", "Grounded later signal", urgency, "synthetic", "Later response"), now=after)
    final = store._timeline.load(current.thread_id)
    assert final.phase == ("will_decided" if quiet else "expression_pending")
    data = store._timeline.events(current.thread_id)[-1].payload["data"]
    assert data["entity_id"] == next_signal.payload["entity_id"]
    assert (data["provider"] == "eva:proactive-cooldown") is quiet
