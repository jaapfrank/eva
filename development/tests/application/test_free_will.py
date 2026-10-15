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

        def recent_initiatives(self):
            return ({"title": "Prior plan", "question": "Done", "phase": "personal_stopped"},)

        def native_capabilities(self):
            return ("read_home_assistant_inventory", "save_workspace_document")

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
        def evaluate_sensory_event(self, actual, identity, memory, attention=None, initiative_history=(), initiative_capabilities=()):
            assert initiative_capabilities == ("read_home_assistant_inventory", "save_workspace_document")
            assert initiative_history == ({"title": "Prior plan", "question": "Done", "phase": "personal_stopped"},)
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
    assert recalled[0].principal_id == "jaap"
    assert recalled[0].subject_ids == ("jaap",)
    assert "Persoonlijke plannen" in recalled[0].query_text
    assert "interesses en voorkeuren" in recalled[0].query_text
    assert "binary_sensor.achterdeur" in recalled[0].query_text
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


def test_spontaneous_initiative_retains_own_origin_and_event_identity():
    from types import SimpleNamespace
    from eva.application.free_will import PersonalInitiativeProposal
    proposal = PersonalInitiativeProposal("Gezinsidee", "Zelf gekozen", "Maak iets bruikbaars")
    claim = SimpleNamespace(event=SimpleNamespace(source="home-assistant", source_event_id="event-1"),
                            thread_id="sensory:event-1", operation_id="decision-1")
    first = proposal.adoption(claim, principal_id="owner")
    assert first == proposal.adoption(claim, principal_id="owner")
    assert first["origin"] == "eva_initiative"
    assert first["source_message_id"] == "sensory:event-1"
    assert first["authority"] == "current_host_costless_mandate_required"
    assert first["execution_mode"] == "autonomous_task"
    assert first["intent_id"] != proposal.adoption(claim, principal_id="another-owner")["intent_id"]
    claim.event.source_event_id = "event-2"
    assert first["intent_id"] != proposal.adoption(claim, principal_id="owner")["intent_id"]


def test_spontaneous_initiative_rejects_missing_owner_or_unbounded_proposal():
    from types import SimpleNamespace
    from eva.application.free_will import PersonalInitiativeProposal
    import pytest
    with pytest.raises(ValueError):
        PersonalInitiativeProposal("", "why", "what")
    with pytest.raises(ValueError):
        PersonalInitiativeProposal("x" * 161, "why", "what")
    with pytest.raises(ValueError):
        PersonalInitiativeProposal("title", "why", "what").adoption(SimpleNamespace(), principal_id="")


def test_initiative_memory_uses_trusted_owner_alias_and_rechecks_revocation(tmp_path):
    from uuid import UUID
    import json
    from eva.bootstrap.telegram_ingress import load_principal_bindings

    owner = "33333333-3333-3333-8333-333333333333"
    binding_id = "44444444-4444-4444-8444-444444444444"
    binding_file = tmp_path / "bindings.json"
    def write_binding(bound_owner):
        binding_file.write_text(json.dumps({"contract": "EvaTelegramPrincipalBindings/v2", "bindings": [{
            "telegram_user_id": 123, "allowed_chat_ids": ["123"],
            "principal_id": bound_owner, "principal_binding_id": binding_id,
        }]}))
    write_binding(owner)
    binding_file.chmod(0o600)
    def resolve():
        return load_principal_bindings(str(binding_file)).private_bridge_principal(
            principal_id=owner, principal_binding_id=UUID(binding_id)) or owner
    event = SensoryEvent("home-assistant", "alias-event", "state_changed", NOW,
                         {"entity_id": "binary_sensor.door", "old_state": "off", "new_state": "on"})
    claim = SensoryDecisionClaim(event, "sensory:alias-event", "alias-event", 2, 0)
    queries = []
    class Store:
        def claim_next(self, **kwargs): return claim
        def mark_model_started(self, actual, **kwargs): return actual
        def complete(self, actual, decision, **kwargs): pass
        def fail(self, actual, error, **kwargs): raise error
    class Memory:
        def recall_for_presence(self, query):
            queries.append(query)
            return object()
    class Model:
        def evaluate_sensory_event(self, *args):
            return FreeWillDecision("silence", "Privately considered", 0, "test")
    worker = SensoryDecisionWorker(Store(), Model(), Memory(),
        IdentityProfile("Eva", "Fixture", ("Be honest",), 80),
        tenant_id="tenant", installation_id="home", principal_id=owner,
        memory_principal_resolver=resolve)
    assert worker.run_once(now=NOW) == "silence"
    write_binding("55555555-5555-5555-8555-555555555555")
    assert worker.run_once(now=NOW) == "silence"
    assert [q.principal_id for q in queries] == ["telegram:123", owner]
    assert [q.subject_ids for q in queries] == [("telegram:123",), (owner,)]
    assert [q.audience.principal_id for q in queries] == ["telegram:123", owner]
    assert worker._principal_id == owner  # Initiative ownership is not rewritten.


def test_native_work_cannot_forge_user_ingress_or_direct_user_memory():
    from eva.application.conversation import ConversationTurn, ConversationConflict
    from eva.application.initiative_execution import require_initiative_context
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
    turn = ConversationTurn.private("tenant", "installation", "owner",
        "initiative-task:" + "a"*64, "initiative-execute:" + "a"*64,
        "Eva koos dit zelf", "2026-09-10T12:00:00Z")
    import pytest
    with pytest.raises(ConversationConflict, match="durable host"):
        require_initiative_context(turn)
    # No initialized memory writer exists: native output must return before it.
    runtime = object.__new__(PostgresHindsightMemoryRuntime)
    runtime.after_response(turn, None)
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    assert PostgresConversationJournal._ingress_data(turn)["origin"] == "eva_initiative"
    from eva.providers.mcp_autonomy import _SourceTurnLoader
    with pytest.raises(ConversationConflict, match="not an owner"):
        _SourceTurnLoader.__call__(object(), {
            "tenant_id": turn.tenant_id, "installation_id": turn.installation_id,
            "principal_id": turn.principal_id, "conversation_id": turn.conversation_id,
            "message_id": turn.message_id, "audience": {"kind": "private_person"}})


def test_private_opportunity_recall_keeps_routine_observations_out_of_personal_search():
    from eva.application.free_will import _private_review_recall_context
    from dataclasses import replace
    private = SensoryEvent("initiative-review", "review:fixture", "private_opportunity_review", NOW,
        {"life_context": {"observed_at_utc": NOW.isoformat(), "observations": [
            {"entity_id": "sensor.workspace", "friendly_name": "Workspace", "state": "occupied",
             "historical": True}], "coverage": {"calendar": "not_read"}}})
    hint = _private_review_recall_context(private)
    assert hint == ""
    assert private.payload["life_context"]["observations"][0]["state"] == "occupied"
    assert _private_review_recall_context(replace(private, source="household-initiative")) == ""
    assert _private_review_recall_context(replace(private, source="home-assistant")) == ""
    assert _private_review_recall_context(replace(private, payload={})) == ""


def test_private_opportunity_recall_bounds_search_context_but_preserves_original_snapshot():
    from eva.application.free_will import _private_review_recall_context
    payload = {"life_context": {"observations": [{"friendly_name": "x" * 8000}]}}
    event = SensoryEvent("initiative-review", "review:fixture", "private_opportunity_review", NOW, payload)
    hint = _private_review_recall_context(event)
    assert len(hint) < 1800
    assert len(event.payload["life_context"]["observations"][0]["friendly_name"]) == 8000
