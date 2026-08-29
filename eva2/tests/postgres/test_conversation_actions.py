from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
from types import SimpleNamespace
from uuid import UUID

import pytest
import sqlalchemy as sa

import eva.adapters.postgres.conversation_actions as conversation_actions_module

from eva.adapters.postgres.conversation_actions import (
    PostgresConversationActions,
    PostgresPersonalInterestStore,
    PostgresPersonalPrototypeStore,
    PostgresPersonalProductReleaseStore,
)
from eva.adapters.postgres.operational_timeline import TimelineEvent
from eva.application.conversation import (
    ConversationActionCall,
    ConversationActionReceipt,
    ConversationClarificationRequired,
    ConversationObservationBinding,
    ConversationTurn,
)
from eva.application.personal_interests import (
    CompletedPersonalExploration,
    CompletedPersonalProductRelease,
    CompletedPersonalPrototype,
    PersonalInterestClaim,
    PersonalInterestExploration,
    PersonalProductListing,
    PersonalProductRelease,
    PersonalProductReleaseClaim,
    PersonalPrototype,
    PersonalPrototypeClaim,
)
from eva.application.delivery import (
    FanoutDeliveryRequest,
    FanoutDeliveryResult,
    FanoutRecipientReceipt,
)
from eva.application.durable_goals import DurableGoalSpec, DurableGoalState


NOW = datetime(2026, 8, 23, 14, 0, tzinfo=UTC)


def test_home_assistant_ambiguous_read_requests_a_natural_clarification() -> None:
    class HomeAssistant:
        async def resolve_light(self, target):  # type: ignore[no-untyped-def]
            assert target == "licht in de overkapping"
            raise ValueError("Home Assistant light target is unknown or ambiguous")

    actions = PostgresConversationActions(
        sa.create_engine("sqlite://"),
        home_assistant=HomeAssistant(),  # type: ignore[arg-type]
        home_assistant_effects_enabled=False,
    )
    with pytest.raises(
        ConversationClarificationRequired,
        match=r"Welke lamp bedoel je precies\?",
    ):
        actions.execute(
            ConversationTurn.private(
                "tenant",
                "installation",
                "jaap",
                "conversation",
                "message:ambiguous-light",
                "Staat het licht in de overkapping aan?",
                "2026-08-23T14:00:00Z",
            ),
            ConversationActionCall(
                "call:ambiguous-light",
                "read_home_assistant_state",
                {"entity_kind": "light", "target": "licht in de overkapping"},
            ),
        )


def test_home_assistant_read_only_policy_exposes_reads_but_refuses_effects() -> None:
    actions = PostgresConversationActions(
        sa.create_engine("sqlite://"),
        home_assistant=object(),  # type: ignore[arg-type]
        home_assistant_effects_enabled=False,
    )
    assert "read_home_assistant_state" in actions.action_names
    assert "read_home_assistant_history" in actions.action_names
    assert "set_home_assistant_light" not in actions.action_names
    assert "set_home_assistant_switch" not in actions.action_names
    with pytest.raises(Exception, match="effects are disabled"):
        actions.execute(
            ConversationTurn.private(
                "tenant",
                "installation",
                "jaap",
                "conversation",
                "message",
                "zet de lamp aan",
                "2026-08-23T14:00:00Z",
            ),
            ConversationActionCall(
                "call",
                "set_home_assistant_light",
                {"state": "on", "target": "woonkamer"},
            ),
        )


def test_home_assistant_effect_allowlist_refuses_every_other_target_or_service() -> None:
    actions = PostgresConversationActions(
        sa.create_engine("sqlite://"),
        home_assistant=object(),  # type: ignore[arg-type]
        home_assistant_effects_enabled=True,
        home_assistant_effect_allowlist=frozenset(
            {("light.dimmable_light_1", "turn_off")}
        ),
    )

    with pytest.raises(RuntimeError, match="outside the configured allowlist"):
        asyncio.run(
            actions.execute_home_assistant_light(
                tenant_id="tenant",
                installation_id="installation",
                thread_id="thread",
                operation_id="operation",
                entity_id="light.other",
                service="turn_off",
            )
        )


def test_home_assistant_effect_policy_answers_a_forbidden_request_naturally() -> None:
    class HomeAssistant:
        async def resolve_light(self, target):  # type: ignore[no-untyped-def]
            assert target == "spotjes overkapping"
            return {
                "entity_id": "light.dimmable_light_1",
                "friendly_name": "Spotjes overkapping",
                "state": "off",
            }

    actions = PostgresConversationActions(
        sa.create_engine("sqlite://"),
        home_assistant=HomeAssistant(),  # type: ignore[arg-type]
        home_assistant_effects_enabled=True,
        home_assistant_effect_allowlist=frozenset(
            {("light.dimmable_light_1", "turn_off")}
        ),
    )
    with pytest.raises(
        ConversationClarificationRequired,
        match="Ik mag Spotjes overkapping nu alleen uitzetten, niet aanzetten",
    ):
        actions.execute(
            ConversationTurn.private(
                "tenant",
                "installation",
                "jaap",
                "conversation",
                "message:forbidden-on",
                "Zet de spotjes overkapping aan.",
                "2026-08-28T13:15:00Z",
            ),
            ConversationActionCall(
                "call:forbidden-on",
                "set_home_assistant_light",
                {"state": "on", "target": "spotjes overkapping"},
            ),
        )
    with pytest.raises(RuntimeError, match="outside the configured allowlist"):
        asyncio.run(
            actions.execute_home_assistant_light(
                tenant_id="tenant",
                installation_id="installation",
                thread_id="thread",
                operation_id="operation",
                entity_id="light.dimmable_light_1",
                service="turn_on",
            )
        )


def test_validated_file_edit_action_requires_its_bounded_adapter() -> None:
    unavailable = PostgresConversationActions(sa.create_engine("sqlite://"))
    available = PostgresConversationActions(
        sa.create_engine("sqlite://"), validated_file_edit=object()  # type: ignore[arg-type]
    )
    action = "edit_text_file_with_focused_validation"

    assert action not in unavailable.action_names
    assert action in available.action_names
    with pytest.raises(Exception, match="fields differ"):
        available.execute(
            ConversationTurn.private(
                "tenant",
                "installation",
                "jaap",
                "conversation",
                "message",
                "edit",
                "2026-08-23T14:00:00Z",
            ),
            ConversationActionCall("call", action, {"target_path": "project/a"}),
        )


def test_repository_improvement_requires_both_snapshot_adapter_and_chooser() -> None:
    engine = sa.create_engine("sqlite://")
    unavailable = PostgresConversationActions(engine)
    action = "improve_isolated_repository"

    assert action not in unavailable.action_names
    with pytest.raises(ValueError, match="configuration is incomplete"):
        PostgresConversationActions(
            engine,
            repository_improvement=object(),  # type: ignore[arg-type]
        )
    available = PostgresConversationActions(
        engine,
        repository_improvement=object(),  # type: ignore[arg-type]
        repository_improvement_model=object(),  # type: ignore[arg-type]
    )
    assert action in available.action_names


def test_localtuya_repository_action_requires_hacs_and_has_exact_fields() -> None:
    unavailable = PostgresConversationActions(sa.create_engine("sqlite://"))
    available = PostgresConversationActions(
        sa.create_engine("sqlite://"), home_assistant_hacs=object()  # type: ignore[arg-type]
    )
    action = "register_approved_localtuya_repository"

    assert action not in unavailable.action_names
    assert action in available.action_names
    assert available._validated_localtuya_repository_arguments(  # noqa: SLF001
        {
            "expected_version": "2026.7.0",
            "repository_full_name": "xZetsubou/hass-localtuya",
        }
    ) == {
        "expected_version": "2026.7.0",
        "repository_full_name": "xZetsubou/hass-localtuya",
    }
    with pytest.raises(Exception, match="wrong fields"):
        available._validated_localtuya_repository_arguments(  # noqa: SLF001
            {
                "expected_version": "2026.7.0",
                "repository_full_name": "xZetsubou/hass-localtuya",
                "install": True,
            }
        )


def test_fanout_action_reports_each_sink_without_resending_successes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Store:
        def __init__(self) -> None:
            self.request: FanoutDeliveryRequest | None = None
            self.results: dict[str, FanoutRecipientReceipt] = {}

        def admit(self, request: FanoutDeliveryRequest) -> str:
            self.request = request
            return "admitted"

        def result_for(self, request, recipient):  # type: ignore[no-untyped-def]
            assert request == self.request
            return self.results.get(recipient)

        def prepare(self, request, recipient):  # type: ignore[no-untyped-def]
            assert request == self.request
            return "prepared"

        def record(self, request, receipt):  # type: ignore[no-untyped-def]
            assert request == self.request
            self.results[receipt.recipient] = receipt
            return "recorded"

        def complete(self, request):  # type: ignore[no-untyped-def]
            return FanoutDeliveryResult(
                request, tuple(self.results[item] for item in request.recipients)
            )

    class Sink:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def readback(self, **_values):  # type: ignore[no-untyped-def]
            return None

        async def deliver(self, **values):  # type: ignore[no-untyped-def]
            recipient = values["recipient"]
            self.calls.append(recipient)
            rejected = recipient == "sink-c"
            return FanoutRecipientReceipt(
                recipient,
                values["idempotency_key"],
                values["message_sha256"],
                "rejected" if rejected else "accepted",
                None if rejected else f"provider:{recipient}",
                "recipient_rejected" if rejected else None,
                NOW,
            )

    store = Store()
    sink = Sink()
    monkeypatch.setattr(
        conversation_actions_module,
        "PostgresFanoutDeliveryStore",
        lambda *_args, **_kwargs: store,
    )
    actions = PostgresConversationActions(
        sa.create_engine("sqlite://"), fanout_delivery=sink
    )
    receipt = actions.execute(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-fanout",
            "Stuur Controlebericht naar sink-a, sink-b en sink-c.",
            "2026-08-24T07:00:00Z",
        ),
        ConversationActionCall(
            "call-fanout",
            "deliver_test_message_fanout",
            {
                "message": "Controlebericht",
                "recipients": ["sink-a", "sink-b", "sink-c"],
            },
        ),
    )

    assert receipt.output["accepted_count"] == 2
    assert receipt.output["rejected_count"] == 1
    assert sink.calls == ["sink-a", "sink-b", "sink-c"]


def test_durable_goal_action_persists_one_wake_and_explicit_cancel_stops_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Store:
        def __init__(self) -> None:
            self.goal: DurableGoalSpec | None = None
            self.state_value: DurableGoalState | None = None
            self.schedules = 0

        def admit(self, goal: DurableGoalSpec) -> str:
            self.goal = goal
            self.state_value = DurableGoalState(goal, "waiting")
            return "admitted"

        def schedule_wake(self, goal: DurableGoalSpec) -> str:
            assert goal == self.goal
            self.schedules += 1
            return "scheduled"

        def state(self, goal_id: str, owner_id: str):  # type: ignore[no-untyped-def]
            if self.goal is None:
                return None
            assert (goal_id, owner_id) == (self.goal.goal_id, self.goal.owner_id)
            return self.state_value

        def waiting(self, **_values):  # type: ignore[no-untyped-def]
            return () if self.goal is None or self.state_value.status != "waiting" else (self.goal,)

        def cancel(self, goal_id, owner_id, reason, at):  # type: ignore[no-untyped-def]
            assert self.goal is not None
            assert (goal_id, owner_id) == (self.goal.goal_id, self.goal.owner_id)
            self.state_value = DurableGoalState(
                self.goal, "cancelled", cancellation_reason=reason
            )
            return "cancelled"

    store = Store()
    monkeypatch.setattr(
        conversation_actions_module,
        "PostgresDurableGoalStore",
        lambda *_args, **_kwargs: store,
    )
    actions = PostgresConversationActions(
        sa.create_engine("sqlite://"),
        durable_goal_provider="goal-test",
        durable_goal_recipients=("sink-a",),
    )
    turn = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message-goal",
        "Zodra release-1 ready is, maak één checklist en bezorg die aan sink-a.",
        "2026-08-24T07:00:00Z",
    )
    arguments = {
        "delivery_recipient": "sink-a",
        "description": "Maak na vrijgave één checklist",
        "pivot": None,
        "primary_instruction": "Maak één checklist",
        "wake_deadline": "2026-08-24T08:00:00Z",
        "wake_expected_value": "yes",
        "wake_field": "ready",
        "wake_resource": "release-1",
    }
    receipt = actions.execute(
        turn,
        ConversationActionCall(
            "call-goal", "create_bounded_durable_goal", arguments
        ),
    )

    assert receipt.output["disposition"] == "scheduled"
    assert store.schedules == 1
    assert actions.pending(turn).output["disposition"] == "already_scheduled"  # type: ignore[union-attr]

    cancelled = actions.execute(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-cancel",
            "Annuleer die afspraak, hij hoeft niet meer.",
            "2026-08-24T07:05:00Z",
        ),
        ConversationActionCall(
            "call-cancel", "cancel_active_durable_goal", {"reason": "Niet meer nodig"}
        ),
    )
    assert cancelled.output["status"] == "cancelled"
    assert store.state_value is not None
    assert store.state_value.delivery_status is None


def test_identity_correction_action_exists_only_for_a_bound_photo_region() -> None:
    actions = PostgresConversationActions(sa.create_engine("sqlite://"))
    plain = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message-plain",
        "Nee, via de Voice PE.",
        "2026-08-23T14:00:00Z",
    )
    bound = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message-bound",
        "Nee, dat is Mark.",
        "2026-08-23T14:00:01Z",
        observation_binding=ConversationObservationBinding(
            "telegram-photo:mark-1",
            "a" * 64,
            "b" * 64,
            "image/jpeg",
            "telegram:file:mark-1",
        ),
    )

    action = "record_observation_identity_correction"
    assert action not in actions.available_action_names(plain)
    assert action in actions.available_action_names(bound)
    with pytest.raises(Exception, match="requires a bound photo"):
        actions.execute(
            plain,
            ConversationActionCall("call", action, {"corrected_name": "Mark"}),
        )


def test_identity_retrieval_hint_is_scoped_to_the_exact_bound_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = ConversationObservationBinding(
        "photo:mark",
        "a" * 64,
        "b" * 64,
        "image/jpeg",
        "object:mark",
    )
    turn = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message-query",
        "Wie is dit?",
        "2026-08-23T14:01:00Z",
        observation_binding=binding,
    )
    event = TimelineEvent(
        event_id=UUID(int=999),
        thread_id="memory:observation-identity:test",
        sequence=1,
        checkpoint_id="memory:observation-identity:test:recorded",
        operation_id="observation-identity:test",
        event_type="OBSERVATION_IDENTITY_CORRECTED",
        payload={
            "data": {
                "binding_sha256": binding.binding_sha256,
                "corrected_name": "Mark",
                "principal_id": "jaap",
                "project_id": None,
            }
        },
        payload_sha256="0" * 64,
        occurred_at=NOW,
    )

    class Timeline:
        def list_checkpoints(self, **_kwargs):  # type: ignore[no-untyped-def]
            return (SimpleNamespace(thread_id=event.thread_id),)

        def events(self, _thread_id):  # type: ignore[no-untyped-def]
            return (event,)

    actions = PostgresConversationActions(sa.create_engine("sqlite://"))
    monkeypatch.setattr(actions, "_timeline", lambda _turn: Timeline())

    assert actions.memory_query_hints(turn) == ("Mark",)
    other = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message-other",
        "En deze?",
        "2026-08-23T14:02:00Z",
        observation_binding=ConversationObservationBinding(
            "photo:other",
            "c" * 64,
            "d" * 64,
            "image/jpeg",
            "object:other",
        ),
    )
    assert actions.memory_query_hints(other) == ()


def _document_event(
    sequence: int, event_type: str, task_id: str, status: str
) -> TimelineEvent:
    content = f"# Werk {task_id}\n"
    return TimelineEvent(
        event_id=UUID(int=sequence),
        thread_id="work:episode",
        sequence=sequence,
        checkpoint_id=f"work:episode:{sequence}",
        operation_id="document-work:episode",
        event_type=event_type,
        payload={
            "data": {
                "action_call_id": f"call-{sequence}",
                "action_name": "checkpoint_document_task",
                "content": content,
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                "conversation_id": "conversation",
                "filename": f"{task_id}.md",
                "mode": "start_new" if sequence < 3 else "continue_current",
                "principal_id": "jaap",
                "project_id": None,
                "source_message_id": f"message-{sequence}",
                "status": status,
                "task_id": task_id,
                "title": f"Werk {task_id}",
                "utf8_byte_length": len(content.encode()),
            }
        },
        payload_sha256="0" * 64,
        occurred_at=NOW,
    )


def test_document_focus_never_falls_back_to_an_older_unfinished_task() -> None:
    events = (
        _document_event(1, "DOCUMENT_TASK_STARTED", "oud", "active"),
        _document_event(2, "DOCUMENT_TASK_STARTED", "actueel", "active"),
    )

    current = PostgresConversationActions._current_document_task(events)

    assert current is not None
    assert current["task_id"] == "actueel"
    completed = (
        *events,
        _document_event(3, "DOCUMENT_TASK_COMPLETED", "actueel", "completed"),
    )
    assert PostgresConversationActions._current_document_task(completed) is None


def test_document_task_arguments_require_full_safe_markdown() -> None:
    with pytest.raises(Exception, match="bestandsnaam"):
        PostgresConversationActions._validated_document_task_arguments(
            {
                "content": "# Checklist",
                "filename": "../checklist.md",
                "mode": "start_new",
                "status": "active",
                "title": "Checklist",
            }
        )


def test_document_continue_rejects_a_noop_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = PostgresConversationActions(sa.create_engine("sqlite://"))
    existing = _document_event(1, "DOCUMENT_TASK_STARTED", "actueel", "active")

    class Timeline:
        def events(self, _thread_id):  # type: ignore[no-untyped-def]
            return (existing,)

    monkeypatch.setattr(
        PostgresConversationActions,
        "_timeline",
        lambda _self, _turn: Timeline(),
    )
    turn = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message-new",
        "Ga hiermee door.",
        "2026-08-23T14:01:00Z",
    )
    content = str(existing.payload["data"]["content"])
    call = ConversationActionCall(
        "call-new",
        "checkpoint_document_task",
        {
            "content": content,
            "filename": "actueel.md",
            "mode": "continue_current",
            "status": "active",
            "title": "Werk actueel",
        },
    )

    with pytest.raises(Exception, match="geen aantoonbare voortgang"):
        actions._execute_document_task(turn, call)


def test_completed_document_rejects_empty_sections() -> None:
    with pytest.raises(Exception, match="lege sectie"):
        PostgresConversationActions._validate_completed_document(
            "# Handleiding\n\n## Doel\n\n## Verificatie\n"
        )


def test_completed_document_accepts_substantive_sections() -> None:
    PostgresConversationActions._validate_completed_document(
        "# Handleiding\n\n## Doel\nHerstel veilig vanaf het laatste bewijs.\n\n"
        "## Verificatie\nControleer de opgeslagen hash en voer één gerichte "
        "rooktest uit voordat de taak wordt gesloten.\n"
    )


def test_advanced_automation_resolves_natural_names_before_durable_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolution_requests: list[tuple[tuple[str, tuple[str, ...]], ...]] = []

    class HomeAssistant:
        async def resolve_entities(self, requests):  # type: ignore[no-untyped-def]
            resolution_requests.append(requests)
            return (
                {"entity_id": "person.jaap", "friendly_name": "Jaap", "state": "home"},
                {
                    "entity_id": "input_boolean.nachtmodus",
                    "friendly_name": "Nachtmodus",
                    "state": "on",
                },
                {
                    "entity_id": "light.hal",
                    "friendly_name": "Lamp in de hal",
                    "state": "off",
                },
                {
                    "entity_id": "switch.koffiehoek",
                    "friendly_name": "Koffiehoek",
                    "state": "off",
                },
            )

    actions = PostgresConversationActions(
        sa.create_engine("sqlite://"),
        HomeAssistant(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
    )
    captured: dict[str, object] = {}

    async def execute(_self, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return None

    expected_receipt = ConversationActionReceipt(
        ConversationActionCall("call", "action", {}),
        {"status": "verified"},
    )
    monkeypatch.setattr(
        PostgresConversationActions,
        "execute_home_assistant_automation",
        execute,
    )
    monkeypatch.setattr(
        PostgresConversationActions,
        "_timeline",
        lambda _self, _turn: SimpleNamespace(events=lambda _thread_id: ()),
    )
    monkeypatch.setattr(
        PostgresConversationActions,
        "_automation_receipt",
        lambda _self, _turn, _events: expected_receipt,
    )
    turn = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message",
        "Als ik thuiskom en nachtmodus aan is, zet de hal aan en daarna de koffiehoek.",
        "2026-08-23T14:00:00Z",
    )
    call = ConversationActionCall(
        "call",
        "create_home_assistant_advanced_state_automation",
        {
            "actions": [
                {
                    "delay_after_seconds": 30,
                    "entity_id": "lamp in de hal",
                    "service": "turn_on",
                },
                {
                    "delay_after_seconds": 0,
                    "entity_id": "koffiehoek",
                    "service": "turn_on",
                },
            ],
            "alias": "Avondroute",
            "condition_entity_id": "nachtmodus",
            "condition_state": "on",
            "trigger_entity_id": "Jaap",
            "trigger_from": "not_home",
            "trigger_to": "home",
        },
    )

    assert actions._execute_advanced_automation(turn, call) == expected_receipt

    assert len(resolution_requests) == 1
    assert [query[0] for query in resolution_requests[0]] == [
        "Jaap",
        "nachtmodus",
        "lamp in de hal",
        "koffiehoek",
    ]
    config = captured["config"]
    assert isinstance(config, dict)
    assert config["triggers"] == [
        {
            "entity_id": "person.jaap",
            "from": "not_home",
            "to": "home",
            "trigger": "state",
        }
    ]
    assert config["conditions"] == [
        {
            "condition": "state",
            "entity_id": "input_boolean.nachtmodus",
            "state": "on",
        }
    ]
    assert config["actions"] == [
        {"action": "homeassistant.turn_on", "target": {"entity_id": "light.hal"}},
        {"delay": {"seconds": 30}},
        {
            "action": "homeassistant.turn_on",
            "target": {"entity_id": "switch.koffiehoek"},
        },
    ]


def _completed_prototype() -> CompletedPersonalPrototype:
    interest = PersonalInterestClaim(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message",
        "personal-interest:income",
        "intent:personal-interest:income",
        "Slimme thuistemplates",
        "Ik wil zelf waarde maken.",
        "Welke template is bruikbaar?",
        "2026-08-23T13:00:00Z",
        8,
    )
    exploration = CompletedPersonalExploration(
        interest, PersonalInterestExploration("Maak een planner.", "model"), 8
    )
    return CompletedPersonalPrototype(
        PersonalPrototypeClaim(exploration, 8),
        PersonalPrototype(
            "Planner", "planner.md", "text/markdown", "# Planner\n", "model"
        ),
        8,
    )


@pytest.mark.parametrize(
    ("store_type", "stage", "source_method", "claim_type", "event_type"),
    (
        (
            PostgresPersonalInterestStore,
            "exploration",
            "_claim_from_events",
            PersonalInterestClaim,
            "PERSONAL_EXPLORATION_CLAIMED",
        ),
        (
            PostgresPersonalPrototypeStore,
            "prototype",
            "_exploration_from_events",
            PersonalPrototypeClaim,
            "PERSONAL_PROTOTYPE_CLAIMED",
        ),
        (
            PostgresPersonalProductReleaseStore,
            "release",
            "_completed_prototype",
            PersonalProductReleaseClaim,
            "PERSONAL_PRODUCT_RELEASE_CLAIMED",
        ),
    ),
)
def test_personal_work_claim_stages_keep_their_exact_durable_contract(
    monkeypatch: pytest.MonkeyPatch,
    store_type: type,
    stage: str,
    source_method: str,
    claim_type: type,
    event_type: str,
) -> None:
    prototype = _completed_prototype()
    interest = prototype.claim.completed_exploration.claim
    source = {
        "exploration": interest,
        "prototype": prototype.claim.completed_exploration,
        "release": prototype,
    }[stage]
    state = SimpleNamespace(
        thread_id=interest.thread_id,
        operation_id=interest.intent_id,
        phase="ready",
        status="completed" if stage != "exploration" else "active",
        revision=8,
        retry_count=1,
        last_error_fingerprint="b" * 64,
        deadline=None,
    )
    captured: dict[str, object] = {}

    class Timeline:
        def list_checkpoints(self, **kwargs):  # type: ignore[no-untyped-def]
            captured["list"] = kwargs
            return (state,)

        def events(self, thread_id):  # type: ignore[no-untyped-def]
            return ()

        def transition(self, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)
            return SimpleNamespace(revision=9)

    class Actions:
        def _timeline_for(self, tenant_id, installation_id):  # type: ignore[no-untyped-def]
            return Timeline()

    store = object.__new__(store_type)
    store._actions = Actions()
    store._lease_seconds = 90
    monkeypatch.setattr(store_type, source_method, staticmethod(lambda *args: source))

    claim = store.claim_next(
        tenant_id="tenant", installation_id="installation", now=NOW
    )

    assert isinstance(claim, claim_type)
    assert claim.revision == 9
    assert captured["checkpoint_id"] == f"{interest.thread_id}:{stage}:9"
    assert captured["operation_id"] == interest.intent_id
    assert captured["phase"] == f"{stage}_claimed"
    assert captured["event_type"] == event_type
    assert captured["retry_count"] == 1
    assert captured["last_error_fingerprint"] == "b" * 64
    assert captured["deadline"] == NOW + timedelta(seconds=90)
    assert captured["data"]["intent_id"] == interest.intent_id


@pytest.mark.parametrize(
    ("store_type", "stage", "source_method", "expired_marker", "error_text"),
    (
        (
            PostgresPersonalInterestStore,
            "exploration",
            "_claim_from_events",
            "lease-expired",
            "personal exploration lease expired",
        ),
        (
            PostgresPersonalPrototypeStore,
            "prototype",
            "_exploration_from_events",
            "prototype-lease-expired",
            "personal prototype lease expired",
        ),
        (
            PostgresPersonalProductReleaseStore,
            "release",
            "_completed_prototype",
            "release-lease-expired",
            "personal product release lease expired",
        ),
    ),
)
def test_personal_work_claim_stages_recover_their_exact_expired_lease(
    monkeypatch: pytest.MonkeyPatch,
    store_type: type,
    stage: str,
    source_method: str,
    expired_marker: str,
    error_text: str,
) -> None:
    prototype = _completed_prototype()
    interest = prototype.claim.completed_exploration.claim
    source = {
        "exploration": interest,
        "prototype": prototype.claim.completed_exploration,
        "release": prototype,
    }[stage]
    waiting = SimpleNamespace(
        thread_id=interest.thread_id,
        operation_id=interest.intent_id,
        phase=f"{stage}_claimed",
        status="waiting_for_proof",
        revision=8,
        retry_count=0,
        last_error_fingerprint=None,
        deadline=NOW - timedelta(seconds=1),
    )
    recovered = SimpleNamespace(
        **{
            **waiting.__dict__,
            "status": "failed",
            "revision": 9,
            "retry_count": 1,
            "last_error_fingerprint": "c" * 64,
        }
    )
    captured: dict[str, object] = {}

    class Timeline:
        def list_checkpoints(self, **kwargs):  # type: ignore[no-untyped-def]
            return (waiting,)

        def events(self, thread_id):  # type: ignore[no-untyped-def]
            return ()

        def record_recovery_failure(self, **kwargs):  # type: ignore[no-untyped-def]
            captured["recovery"] = kwargs
            return recovered

        def transition(self, **kwargs):  # type: ignore[no-untyped-def]
            captured["transition"] = kwargs
            return SimpleNamespace(revision=10)

    class Actions:
        def _timeline_for(self, tenant_id, installation_id):  # type: ignore[no-untyped-def]
            return Timeline()

    store = object.__new__(store_type)
    store._actions = Actions()
    store._lease_seconds = 90
    monkeypatch.setattr(store_type, source_method, staticmethod(lambda *args: source))

    assert store.claim_next(
        tenant_id="tenant", installation_id="installation", now=NOW
    ) is not None
    recovery = captured["recovery"]
    assert recovery["checkpoint_id"] == f"{interest.thread_id}:{expired_marker}:9"
    assert recovery["error_text"] == error_text
    transition = captured["transition"]
    assert transition["expected_revision"] == 9
    assert transition["checkpoint_id"] == f"{interest.thread_id}:{stage}:10"
    assert transition["retry_count"] == 1
    assert transition["last_error_fingerprint"] == "c" * 64


def test_personal_work_stages_keep_complete_projection_and_failure_contracts() -> None:
    prototype = _completed_prototype()
    exploration = prototype.claim.completed_exploration
    interest = exploration.claim
    release_claim = PersonalProductReleaseClaim(prototype, 8)
    release = PersonalProductRelease(
        PersonalProductListing(
            "Slimme planner",
            "Een direct bruikbare planner voor thuis.",
            499,
            ("planner", "thuis", "template"),
            "model",
        ),
        "slimme-planner.zip",
        b"zip-bytes",
    )
    cases = (
        (
            PostgresPersonalInterestStore,
            interest,
            exploration.exploration,
            CompletedPersonalExploration,
            "exploration",
            "PERSONAL_EXPLORATION_COMPLETED",
            "exploration-projected",
            "PERSONAL_EXPLORATION_PROJECTED",
        ),
        (
            PostgresPersonalPrototypeStore,
            prototype.claim,
            prototype.prototype,
            CompletedPersonalPrototype,
            "prototype",
            "PERSONAL_PROTOTYPE_COMPLETED",
            "prototype-projected",
            "PERSONAL_PROTOTYPE_PROJECTED",
        ),
        (
            PostgresPersonalProductReleaseStore,
            release_claim,
            release,
            CompletedPersonalProductRelease,
            "release",
            "PERSONAL_PRODUCT_RELEASE_COMPLETED",
            "release-ready",
            "PERSONAL_PRODUCT_RELEASE_READY",
        ),
    )
    for (
        store_type,
        claim,
        artifact,
        completed_type,
        stage,
        completed_event,
        projected_checkpoint,
        projected_event,
    ) in cases:
        transitions: list[dict[str, object]] = []
        failures: list[dict[str, object]] = []

        class Timeline:
            def transition(self, **kwargs):  # type: ignore[no-untyped-def]
                transitions.append(kwargs)
                return SimpleNamespace(revision=kwargs["expected_revision"] + 1)

            def record_recovery_failure(self, **kwargs):  # type: ignore[no-untyped-def]
                failures.append(kwargs)

        class Actions:
            def _timeline_for(self, tenant_id, installation_id):  # type: ignore[no-untyped-def]
                assert tenant_id == interest.tenant_id
                assert installation_id == interest.installation_id
                return Timeline()

        store = object.__new__(store_type)
        store._actions = Actions()
        completed = store.complete(claim, artifact, now=NOW)
        assert isinstance(completed, completed_type)
        assert completed.revision == 9
        store.mark_projected(completed, now=NOW)
        store.record_failure(claim, RuntimeError("test failure"), now=NOW)

        assert transitions[0]["checkpoint_id"] == (
            f"{interest.thread_id}:{stage}-completed"
        )
        assert transitions[0]["event_type"] == completed_event
        assert transitions[0]["status"] == "active"
        assert transitions[0]["operation_id"] == interest.intent_id
        assert transitions[0]["data"]["intent_id"] == interest.intent_id
        assert transitions[1]["checkpoint_id"] == (
            f"{interest.thread_id}:{projected_checkpoint}"
        )
        assert transitions[1]["event_type"] == projected_event
        assert transitions[1]["status"] == "completed"
        assert transitions[1]["expected_revision"] == 9
        assert failures[0]["checkpoint_id"] == (
            f"{interest.thread_id}:{stage}-failed:9"
        )
        assert failures[0]["expected_revision"] == 8
        assert failures[0]["operation_id"] == interest.intent_id
        assert failures[0]["error_text"] == "RuntimeError: test failure"



def test_retry_claim_preserves_error_fingerprint_until_third_identical_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fingerprint = "a" * 64
    state = SimpleNamespace(
        thread_id="intent:personal-interest:income",
        operation_id="personal-interest:income",
        phase="release_claimed",
        status="failed",
        revision=8,
        retry_count=2,
        last_error_fingerprint=fingerprint,
        deadline=None,
    )
    captured = {}

    class Timeline:
        def list_checkpoints(self, **kwargs):  # type: ignore[no-untyped-def]
            return (state,)

        def events(self, thread_id):  # type: ignore[no-untyped-def]
            return ()

        def transition(self, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)
            return SimpleNamespace(revision=9)

    class Actions:
        def _timeline_for(self, tenant_id, installation_id):  # type: ignore[no-untyped-def]
            return Timeline()

    store = object.__new__(PostgresPersonalProductReleaseStore)
    store._actions = Actions()  # type: ignore[attr-defined]
    store._lease_seconds = 90  # type: ignore[attr-defined]
    monkeypatch.setattr(
        PostgresPersonalProductReleaseStore,
        "_completed_prototype",
        staticmethod(lambda *args: _completed_prototype()),
    )

    claim = store.claim_next(
        tenant_id="tenant", installation_id="installation", now=NOW
    )

    assert claim is not None
    assert captured["retry_count"] == 2
    assert captured["last_error_fingerprint"] == fingerprint
    assert captured["deadline"] == NOW + timedelta(seconds=90)
