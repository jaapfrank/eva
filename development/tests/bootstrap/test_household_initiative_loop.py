"""Composition checks for actual shared initiative wiring; no provider or network."""
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from eva.application.household_initiative import HouseholdInitiativeBinding
from eva.application.sensory import SensoryEvent
from eva.bootstrap import household_initiative as composition

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)
SAT = "assist_satellite.living_room"
SECOND = "assist_satellite.second_room"
DEVICE = "a" * 32
OTHER = "b" * 32


class Policy:
    tenant_id = "tenant"
    installation_id = "home"
    household_id = "family"
    consent_operation_id = "household-consent:fixture"

    def __init__(self):
        self.devices = {SAT: DEVICE, SECOND: OTHER}
        self.version = 1

    def model_dump(self, **kwargs):
        return {"tenant_id": self.tenant_id, "installation_id": self.installation_id,
                "household_id": self.household_id, "consent_operation_id": self.consent_operation_id,
                "devices": self.devices, "version": self.version}


def configured_loop(*, recent=None, source_store=None, can_decide=lambda: True):
    policy = Policy()
    return composition.HouseholdInitiativeLoop(
        engine=object(), voice=SimpleNamespace(policy=policy, validate_current=lambda: None),
        server=SimpleNamespace(sessions=SimpleNamespace(latest_native=lambda: recent),
                               authority=SimpleNamespace(validate_current=lambda: None)),
        provider=object(), model=SimpleNamespace(autonomous_admission_blocker=lambda: None),
        memory=object(), identity=object(), budget=object(), principal_id="owner",
        authorize_initiative=lambda: ({"capabilities": []}, None),
        source_store=source_store, can_decide=can_decide)


def test_configured_household_output_does_not_require_a_recent_session():
    loop = configured_loop()
    assert loop._selected() == {"satellite_id": SAT, "device_id": DEVICE}
    loop.voice.policy.devices.clear()
    assert loop._selected() is None


@pytest.mark.parametrize("recent, expected", [
    ({"satellite_id": SECOND, "device_id": OTHER, "session_id": "actual-recent"}, SECOND),
    ({"satellite_id": SECOND, "device_id": DEVICE, "session_id": "wrong-device"}, SAT),
    ({"satellite_id": "assist_satellite.unconfigured", "device_id": OTHER}, SAT),
])
def test_only_current_configured_recent_output_is_preferred(recent, expected):
    loop = configured_loop(recent=recent)
    assert loop._selected()["satellite_id"] == expected


def _compose_without_network(monkeypatch, *, available=True, delivery_result="idle", original=None):
    calls, workers, stores = [], [], []
    allow = [available]
    raw = SimpleNamespace(_timeline=SimpleNamespace(_scope_value=("tenant", "home")))
    def wrong_source(**kwargs):
        raise AssertionError("raw source store must not select the household-bound event")
    raw.next_household_observation = wrong_source

    class Store:
        def __init__(self, engine, **kwargs):
            self._timeline = SimpleNamespace(_scope_value=(kwargs["tenant_id"], kwargs["installation_id"]))
            self.binding = kwargs["household_binding"]
            stores.append(self)

        def next_household_observation(self, **kwargs):
            calls.append("bound-source")
            return original

        def schedule_household_observation(self, event, **kwargs):
            assert event is original
            calls.append("schedule-observation")

        def schedule_context_review(self, **kwargs):
            calls.append("schedule-clock")

    class Worker:
        def __init__(self, store, *args, **kwargs):
            self.dispatch_ready = kwargs["dispatch_ready"]
            self.household_context = kwargs["household_context"]
            workers.append(self)

        def run_once(self, **kwargs):
            calls.append("decision")
            return "silence"

    class Delivery:
        def __init__(self, *args, **kwargs):
            pass

        def run_once(self):
            calls.append("pending-delivery")
            return delivery_result

    class Clock:
        @classmethod
        def now(cls, tz):
            return NOW

    monkeypatch.setattr(composition, "PostgresSensoryEventStore", Store)
    monkeypatch.setattr(composition, "SensoryDecisionWorker", Worker)
    monkeypatch.setattr(composition, "HouseholdInitiativeDelivery", Delivery)
    monkeypatch.setattr(composition, "datetime", Clock)
    loop = configured_loop(source_store=raw, can_decide=lambda: allow[0])
    return loop, allow, calls, workers, stores


def test_real_composition_uses_bound_source_and_late_foreground_gate(monkeypatch):
    signal = object()
    loop, allow, calls, workers, stores = _compose_without_network(monkeypatch, original=signal)
    assert loop.run_once() == "silence"
    assert calls == ["pending-delivery", "bound-source", "schedule-observation", "decision"]
    assert workers[0].household_context.binding == stores[0].binding
    assert workers[0].dispatch_ready() is True
    allow[0] = False
    assert workers[0].dispatch_ready() is False  # Same live callback, not a frozen pre-call value.


def test_foreground_yields_new_work_but_does_not_prevent_pending_delivery(monkeypatch):
    loop, allow, calls, workers, stores = _compose_without_network(monkeypatch, available=False)
    assert loop.run_once() == "idle"
    assert calls == ["pending-delivery"]
    loop, allow, calls, workers, stores = _compose_without_network(
        monkeypatch, available=False, delivery_result="offered")
    assert loop.run_once() == "offered"
    assert calls == ["pending-delivery"]


def test_source_scope_mismatch_stops_before_delivery_or_model(monkeypatch):
    loop, allow, calls, workers, stores = _compose_without_network(monkeypatch)
    loop.source_store._timeline._scope_value = ("foreign-tenant", "home")
    assert loop.run_once() == "unavailable"
    assert calls == [] and workers == []


def test_shared_wrapper_strips_private_data_and_detects_tamper_without_breaking_clock():
    binding = HouseholdInitiativeBinding("tenant", "home", "family", "household-device:" + DEVICE,
        SAT, DEVICE, "household-consent:fixture", "c" * 64)
    clock = binding.event(NOW)
    assert binding.matches_event(clock)
    original = SensoryEvent("home_assistant", "home-assistant:retained-fixture", "state_changed", NOW,
        {"entity_id": "binary_sensor.room", "friendly_name": "Room", "old_state": "off", "new_state": "on",
         "attributes": {"access_token": "private-token"}, "private_context": "private-owner-content",
         "camera_bytes": "private-frame"})
    wrapped = binding.observation_event(original)
    assert binding.matches_event(wrapped)
    assert wrapped.source_event_id.startswith(clock.source_event_id.rsplit(":", 1)[0] + ":observation:")
    text = str(wrapped.payload)
    assert "private-token" not in text and "private-owner-content" not in text and "private-frame" not in text
    changed = {**wrapped.payload, "observation": {**wrapped.payload["observation"], "payload": {
        **wrapped.payload["observation"]["payload"], "new_state": "off"}}}
    assert not binding.matches_event(replace(wrapped, payload=changed))
    assert not binding.matches_event(replace(wrapped, payload={**wrapped.payload,
        "household_binding": {**binding.to_payload(), "device_id": OTHER}}))
    assert original.payload["attributes"]["access_token"] == "private-token"


def test_actual_hindsight_projection_version_preserves_household_scope():
    from eva.application.household_initiative import HouseholdInitiativeContext
    from tests.postgres.test_household_observation_initiative import _empty_shared_memory
    binding = HouseholdInitiativeBinding("tenant", "home", "family", "household-device:" + DEVICE,
        SAT, DEVICE, "household-consent:fixture", "c" * 64)
    def recall(query):
        memory = _empty_shared_memory(query)
        return replace(memory, recall=memory.recall.model_copy(update={"projection_version": "hindsight-0.9.1-v1"}))
    port = SimpleNamespace(recall_for_presence=recall)
    result, provenance = HouseholdInitiativeContext(binding, lambda: None)(binding.event(NOW), NOW, port)
    assert result.recall.projection_version == "hindsight-0.9.1-v1"
    assert provenance == binding.to_payload()
    for change in ({"principal_id": "foreign"}, {"scope_sha256": "0" * 64}, {"conversation_id": "foreign"}):
        def foreign(query):
            memory = recall(query)
            return replace(memory, recall=memory.recall.model_copy(update=change))
        with pytest.raises(ValueError, match="memory response scope differs"):
            HouseholdInitiativeContext(binding, lambda: None)(binding.event(NOW), NOW,
                SimpleNamespace(recall_for_presence=foreign))
