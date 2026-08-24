from __future__ import annotations

from datetime import UTC, datetime

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


def test_sensory_worker_recalls_then_commits_one_shadow_decision() -> None:
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
        def claim_next(self, *, now):  # type: ignore[no-untyped-def]
            return claim

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

    assert worker.run_once(now=NOW) == "silence"
    assert completed[0].disposition == "silence"
    assert recalled[0].query_purpose == "passive_event_decision"
    assert recalled[0].maximum_facts == 8
    assert recalled[0].conversation_id == "sensory:home-assistant"


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
        def claim_next(self, *, now):  # type: ignore[no-untyped-def]
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
