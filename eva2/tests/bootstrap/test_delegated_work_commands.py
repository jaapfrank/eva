from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from eva.adapters.postgres.delegated_work import DelegatedWorkConflict
from eva.application.conversation import ConversationTurn
from eva.bootstrap.delegated_work_commands import DelegatedWorkConversationCommands

OWNER = UUID("33333333-3333-3333-3333-333333333333")
BINDING = UUID("44444444-4444-4444-4444-444444444444")
EPISODE = UUID("55555555-5555-5555-5555-555555555555")
NOW = datetime(2026, 8, 26, 14, 0, tzinfo=UTC)


class FakeStore:
    def __init__(self) -> None:
        self.admitted: list[dict[str, object]] = []
        self.cards: list[SimpleNamespace] = []
        self.active_work_id: str | None = None
        self.call_started = False
        self.stopped: list[dict[str, object]] = []

    def admit(self, **values: object) -> SimpleNamespace:
        self.admitted.append(values)
        return SimpleNamespace(
            disposition="already_present" if len(self.admitted) > 1 else "inserted"
        )

    def workboard(self, **_values: object) -> SimpleNamespace:
        return SimpleNamespace(cards=self.cards)

    def latest_active_work_id(self, **_values: object) -> str | None:
        return self.active_work_id

    def stop_before_call(self, work_id: str, **values: object) -> SimpleNamespace:
        if self.call_started:
            raise DelegatedWorkConflict("call started")
        self.stopped.append({"work_id": work_id} | values)
        return SimpleNamespace(state="stopped")


def _turn(
    text: str,
    *,
    principal_id: str = str(OWNER),
    message_id: str = "telegram:owner:101",
) -> ConversationTurn:
    return ConversationTurn.private(
        tenant_id="11111111-1111-1111-1111-111111111111",
        installation_id="22222222-2222-2222-2222-222222222222",
        principal_id=principal_id,
        conversation_id="telegram:owner",
        message_id=message_id,
        text=text,
        occurred_at="2026-08-26T14:00:00Z",
    )


def _commands(store: FakeStore) -> DelegatedWorkConversationCommands:
    return DelegatedWorkConversationCommands(
        store,  # type: ignore[arg-type]
        owner_principal_id=OWNER,
        principal_binding_id=BINDING,
        episode_id=EPISODE,
        generation=7,
        clock=lambda: NOW,
    )


def test_owner_can_spawn_one_sealed_replay_safe_research_task() -> None:
    store = FakeStore()
    commands = _commands(store)
    turn = _turn("Laat een onderzoeker uitzoeken welke trein morgen het snelst is")

    first = commands.handle(turn)
    replay = commands.handle(turn)

    assert first is not None
    assert first.text == (
        "Ik heb één afgeschermde onderzoeker ingepland. "
        "Je ziet de taak op het werkbord."
    )
    assert replay is not None
    assert "dezelfde taak" in replay.text
    assert len(store.admitted) == 2
    first_values, replay_values = store.admitted
    assert first_values == replay_values
    spec = first_values["spec"]
    assert spec.work_id.startswith("work:eva-chat:")
    assert spec.episode_id == str(EPISODE)
    assert spec.generation == 7
    assert spec.owner_principal_id == str(OWNER)
    assert spec.principal_binding_id == str(BINDING)
    assert spec.task == "uitzoeken welke trein morgen het snelst is"
    assert spec.grant.capability_ids == []
    assert spec.grant.child_network_access is False
    assert spec.grant.maximum_model_runs == 1
    assert first_values["title"] == "Onderzoek vanuit Eva-chat"
    assert "trein" not in str(first_values["summary"])


def test_owner_gets_board_counts_and_can_stop_latest_work_before_call() -> None:
    store = FakeStore()
    store.cards = [
        SimpleNamespace(column="todo"),
        SimpleNamespace(column="doing"),
        SimpleNamespace(column="attention"),
        SimpleNamespace(column="review"),
        SimpleNamespace(column="done"),
        SimpleNamespace(column="done"),
    ]
    store.active_work_id = "work:eva-chat:latest"
    commands = _commands(store)

    status = commands.handle(_turn("Hoe staat mijn onderzoek?", message_id="status"))
    stopped = commands.handle(_turn("Stop mijn onderzoek", message_id="stop"))

    assert status is not None
    assert status.text == (
        "Je werkbord: 1 wachtend, 1 bezig, 1 vraagt aandacht, "
        "1 te controleren en 2 klaar."
    )
    assert stopped is not None
    assert stopped.text == (
        "Gestopt voordat een modelcall begon. Deze taak wordt niet alsnog uitgevoerd."
    )
    assert store.stopped == [
        {
            "work_id": "work:eva-chat:latest",
            "owner_principal_id": OWNER,
            "principal_binding_id": BINDING,
            "now": NOW,
        }
    ]


def test_started_call_is_never_falsely_reported_as_stopped() -> None:
    store = FakeStore()
    store.active_work_id = "work:eva-chat:started"
    store.call_started = True

    reply = _commands(store).handle(_turn("Stop het onderzoek"))

    assert reply is not None
    assert "niet dat hij gestopt is" in reply.text
    assert store.stopped == []


def test_commands_are_private_to_the_exact_owner_and_ignore_normal_chat() -> None:
    store = FakeStore()
    commands = _commands(store)

    assert commands.handle(_turn("Vertel me iets leuks")) is None
    assert (
        commands.handle(
            _turn(
                "Laat een onderzoeker dit doen",
                principal_id="66666666-6666-6666-6666-666666666666",
            )
        )
        is None
    )
    assert store.admitted == []
