from __future__ import annotations

from datetime import UTC, datetime

import pytest

from eva.application.conversation import IdentityProfile
from eva.application.free_will import (
    FreeWillDecision,
    ProactiveExpressionClaim,
    ProactiveExpressionWorker,
    SensoryDecisionClaim,
    SensoryDecisionWorker,
)
from eva.application.sensory import SensoryEvent


NOW = datetime(2026, 8, 23, 14, 0, tzinfo=UTC)


def test_sensory_worker_clock_does_not_move_backwards() -> None:
    from datetime import timedelta
    worker = SensoryDecisionWorker(object(), object(), object(),
        IdentityProfile("Eva", "Fixture", ("Be honest",), 80),
        tenant_id="tenant", installation_id="home", principal_id="owner",
        clock=lambda: NOW - timedelta(seconds=1))
    assert worker._current_time(NOW) == NOW
    worker._clock = lambda: NOW.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        worker.run_once(now=NOW)


@pytest.mark.parametrize("current_state", [None, "off", "23.5"])
@pytest.mark.parametrize("admit_dispatch", [True, False])
def test_sensory_worker_recalls_then_commits_one_shadow_decision(current_state, admit_dispatch) -> None:
    event = SensoryEvent(
        "home-assistant",
        "home-assistant:event-1",
        "state_changed",
        NOW,
        {
            "device_class": "door",
            "entity_id": "binary_sensor.achterdeur",
            "friendly_name": "Achterdeur",
            "new_state": "on",
            "old_state": "off",
        },
    )
    if current_state is not None:
        from dataclasses import replace
        event = replace(event, event_type="observation_revalidated", payload={
            "entity_id": "binary_sensor.achterdeur",
            "current_state": current_state,
            "historical_observation": {"payload": event.payload},
        })
    claim = SensoryDecisionClaim(
        event,
        "sensory:home-assistant:event-1",
        "home-assistant:event-1",
        2,
        0,
    )
    completed: list[FreeWillDecision] = []
    recalled = []

    class Store:
        def claim_next(self, *, now, include_waiting=True):  # type: ignore[no-untyped-def]
            return claim

        def mark_model_started(self, actual, *, now):
            assert actual == claim and len(recalled) == 1
            return claim if admit_dispatch else None

        def complete(self, actual, decision, *, now):  # type: ignore[no-untyped-def]
            assert actual == claim
            completed.append(decision)

        def fail(self, actual, error, *, now):  # type: ignore[no-untyped-def]
            raise AssertionError(f"unexpected decision failure: {error}")

    class Memory:
        def recall_for_presence(self, query):  # type: ignore[no-untyped-def]
            recalled.append(query)
            return object()

    class Model:
        def evaluate_sensory_event(self, actual, identity, memory, attention=None):  # type: ignore[no-untyped-def]
            assert admit_dispatch
            assert actual == event
            assert identity.name == "Eva"
            return FreeWillDecision(
                "silence",
                "De deurbeweging vraagt nu niet om aandacht.",
                0,
                "test-model",
            )

    worker = SensoryDecisionWorker(
        Store(),
        Model(),
        Memory(),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        tenant_id="tenant",
        installation_id="installation",
        principal_id="jaap",
    )

    assert worker.run_once(now=NOW) == ("silence" if admit_dispatch else "idle")
    if not admit_dispatch:
        assert completed == []
        return
    assert completed[0].disposition == "silence"
    assert recalled[0].query_purpose == "passive_event_decision"
    assert recalled[0].maximum_facts == 8
    assert recalled[0].conversation_id == "sensory:home-assistant"
    if current_state is None:
        assert recalled[0].query_text.endswith("off naar on")
    else:
        assert recalled[0].query_text.endswith(f"actuele toestand: {current_state}")
        assert event.payload["historical_observation"]["payload"]["new_state"] == "on"


def test_sensory_worker_silences_routine_light_without_memory_or_model() -> None:
    event = SensoryEvent(
        "home-assistant",
        "home-assistant:event-routine",
        "state_changed",
        NOW,
        {
            "entity_id": "light.kantoor_links",
            "friendly_name": "Kantoor links",
            "new_state": "off",
            "old_state": "on",
        },
    )
    claim = SensoryDecisionClaim(
        event,
        "sensory:home-assistant:event-routine",
        "home-assistant:event-routine",
        2,
        0,
    )
    completed: list[FreeWillDecision] = []

    class Store:
        def claim_next(self, *, now, include_waiting=True):  # type: ignore[no-untyped-def]
            return claim

        def complete(self, actual, decision, *, now):  # type: ignore[no-untyped-def]
            assert actual == claim
            completed.append(decision)

        def fail(self, actual, error, *, now):  # type: ignore[no-untyped-def]
            raise AssertionError(f"unexpected decision failure: {error}")

    class MustNotRun:
        def __getattr__(self, name):  # type: ignore[no-untyped-def]
            raise AssertionError(f"routine event unexpectedly called {name}")

    worker = SensoryDecisionWorker(
        Store(),
        MustNotRun(),
        MustNotRun(),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        tenant_id="tenant",
        installation_id="installation",
        principal_id="jaap",
    )

    assert worker.run_once(now=NOW) == "silence"
    assert completed == [
        FreeWillDecision(
            "silence",
            "Gewone bediening van een lamp of schakelaar vraagt niet om aandacht.",
            0,
            "eva:bounded-prefilter",
        )
    ]


def test_proactive_worker_commits_receipt_only_after_delivery() -> None:
    claim = ProactiveExpressionClaim(
        "sensory:home-assistant:water",
        "home-assistant:water",
        4,
        0,
        "home-assistant:water",
        "De watermelder in de kelder is actief.",
        3,
    )
    order: list[str] = []

    class Store:
        def claim_next_expression(self, *, now):  # type: ignore[no-untyped-def]
            order.append("claim")
            return claim

        def acknowledge_expression(
            self,
            actual,
            *,
            channel,
            provider_message_id,
            now,  # type: ignore[no-untyped-def]
        ):
            assert actual == claim
            assert channel == "telegram"
            assert provider_message_id == "telegram:42:5001"
            order.append("ack")

        def fail_expression(self, actual, error, *, now):  # type: ignore[no-untyped-def]
            raise AssertionError(f"unexpected delivery failure: {error}")

    class Delivery:
        def deliver(self, actual):  # type: ignore[no-untyped-def]
            assert actual == claim
            order.append("provider")
            return "telegram:42:5001"

    worker = ProactiveExpressionWorker(
        Store(),
        Delivery(),
        channel="telegram",  # type: ignore[arg-type]
    )

    assert worker.run_once(now=NOW) == "delivered"
    assert order == ["claim", "provider", "ack"]
