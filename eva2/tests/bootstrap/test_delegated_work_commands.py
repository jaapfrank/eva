from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.application.conversation import ConversationTurn
from eva.bootstrap.delegated_work_commands import DelegatedWorkConversationCommands

OWNER = UUID("33333333-3333-3333-3333-333333333333")
BINDING = UUID("44444444-4444-4444-4444-444444444444")
NOW = datetime(2026, 8, 26, 14, 0, tzinfo=UTC)


class FakeStore:
    def __init__(self) -> None:
        self.scope = external_timeline_scope(
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222", "test scope",
        )
        self.admitted: list[dict[str, object]] = []
        self.cards: list[SimpleNamespace] = []
        self.active_work_id: str | None = None
        self.call_started = False
        self.stopped: list[dict[str, object]] = []
        self.validated: list[dict[str, object]] = []

    def validate_admission_authority(self, **values: object) -> None:
        self.validated.append(values)

    def admit(self, **values: object) -> SimpleNamespace:
        self.admitted.append(values)
        return SimpleNamespace(
            disposition="already_present" if len(self.admitted) > 1 else "inserted",
            work=self._work(spec=values["spec"]),
        )

    def workboard(self, **_values: object) -> SimpleNamespace:
        return SimpleNamespace(cards=self.cards)

    def latest_stoppable_work_id(self, **_values: object) -> str | None:
        return self.active_work_id

    def request_stop(self, work_id: str, **values: object) -> SimpleNamespace:
        self.stopped.append({"work_id": work_id} | values)
        return self._work(
            state="call_started" if self.call_started else "stopped",
            stop_requested_at=NOW if self.call_started else None,
            call_id="call:active" if self.call_started else None,
        )

    @staticmethod
    def _work(**values: object) -> SimpleNamespace:
        defaults = {
            "spec": SimpleNamespace(work_id="work:fake", generation=1, parent_task_id="parent:fake",
                                    task="Test", created_at="2026-08-26T14:00:00Z",
                                    deadline="2026-08-26T14:05:00Z"),
            "title": "Onderzoek", "state": "admitted", "call_id": None,
            "stop_requested_at": None, "result": None, "usage": None,
            "error_code": None, "error_fingerprint": None,
        }
        return SimpleNamespace(**(defaults | values))


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
        clock=lambda: NOW,
    )


def test_owner_can_spawn_one_sealed_replay_safe_research_task() -> None:
    store = FakeStore()
    commands = _commands(store)
    turn = _turn("Laat een onderzoeker uitzoeken welke trein morgen het snelst is")

    first = commands.handle(turn, principal_binding_id=BINDING)
    replay = commands.handle(turn, principal_binding_id=BINDING)

    assert first is not None
    assert first.text == (
        "Ik heb één afgeschermde onderzoeker ingepland. "
        "Je ziet de taak op het werkbord."
    )
    assert replay == first
    assert len(store.admitted) == 2
    first_values, replay_values = store.admitted
    assert first_values == replay_values
    spec = first_values["spec"]
    assert spec.work_id.startswith("work:eva-chat:")
    assert UUID(spec.episode_id).version == 5
    assert spec.generation == 1
    assert spec.owner_principal_id == str(OWNER)
    assert spec.principal_binding_id == str(BINDING)
    assert spec.task == "uitzoeken welke trein morgen het snelst is"
    assert spec.grant.capability_ids == []
    assert spec.grant.child_network_access is False
    assert spec.grant.maximum_model_runs == 1
    assert first_values["title"] == "Onderzoek vanuit Eva-chat"
    assert "trein" not in str(first_values["summary"])
    assert store.validated == [
        {
            "owner_principal_id": OWNER,
            "principal_binding_id": BINDING,
        }
    ]


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

    status = commands.handle(
        _turn("Hoe staat mijn onderzoek?", message_id="status"),
        principal_binding_id=BINDING,
    )
    stopped = commands.handle(
        _turn("Stop mijn onderzoek", message_id="stop"),
        principal_binding_id=BINDING,
    )

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


def test_started_call_gets_a_durable_honest_stop_request() -> None:
    store = FakeStore()
    store.active_work_id = "work:eva-chat:started"
    store.call_started = True

    reply = _commands(store).handle(
        _turn("Stop het onderzoek"), principal_binding_id=BINDING
    )

    assert reply is not None
    assert reply.text == (
        "De stopopdracht is bewaard. Het resultaat van deze actieve modelcall "
        "wordt niet aan Eva geleverd en Eva start hem niet opnieuw."
    )
    assert store.stopped == [
        {
            "work_id": "work:eva-chat:started",
            "owner_principal_id": OWNER,
            "principal_binding_id": BINDING,
            "now": NOW,
        }
    ]


def test_commands_are_private_to_the_exact_owner_and_ignore_normal_chat() -> None:
    store = FakeStore()
    commands = _commands(store)

    assert (
        commands.handle(
            _turn("Vertel me iets leuks"), principal_binding_id=BINDING
        )
        is None
    )
    assert (
        commands.handle(
            _turn(
                "Laat een onderzoeker dit doen",
                principal_id="66666666-6666-6666-6666-666666666666",
            ),
            principal_binding_id=BINDING,
        )
        is None
    )
    assert (
        commands.handle(
            _turn("Laat een onderzoeker dit doen"),
            principal_binding_id=UUID("77777777-7777-7777-7777-777777777777"),
        )
        is None
    )
    assert store.admitted == []


def test_private_bridge_command_reloads_binding_and_preserves_turn_identity():
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
    from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
    from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings

    original = TelegramPrincipalBindings((TelegramPrincipalBinding(
        telegram_user_id=42, principal_id=str(OWNER), allowed_chat_ids=("42",),
        principal_binding_id=str(BINDING),
    ),))
    current = [original]
    store = FakeStore()
    service = DelegatedWorkOwnerService(
        store, owner_principal_id=OWNER, principal_binding_id=BINDING, clock=lambda: NOW,
        private_bridge_principal_loader=lambda: current[0].private_bridge_principal(
            principal_id=str(OWNER), principal_binding_id=BINDING,
        ),
    )
    runtime = PostgresHindsightMemoryRuntime.__new__(PostgresHindsightMemoryRuntime)
    runtime._telegram_principal_bindings = original
    runtime._telegram_principal_bindings_loader = lambda: current[0]
    runtime._delegated_work_commands = DelegatedWorkConversationCommands(service=service)
    observed = []
    runtime._journal = lambda turn: SimpleNamespace(
        admit=lambda actual: observed.append(actual),
        committed_reply=lambda actual: None,
        commit_reply=lambda actual, reply: reply,
    )
    turn = _turn("Laat een onderzoeker drie ideeën bedenken", principal_id="telegram:42")
    reply = runtime.respond_telegram_delegated_work(turn, telegram_user_id=42, chat_id="42")
    assert reply is not None
    assert observed == [turn] and observed[0].principal_id == "telegram:42"
    assert store.admitted[0]["spec"].owner_principal_id == str(OWNER)
    current[0] = TelegramPrincipalBindings((TelegramPrincipalBinding(
        telegram_user_id=42, principal_id=str(OWNER), allowed_chat_ids=("-100",),
        principal_binding_id=str(BINDING),
    ),))
    assert runtime.respond_telegram_delegated_work(turn, telegram_user_id=42, chat_id="42") is None
    assert len(store.admitted) == 1
