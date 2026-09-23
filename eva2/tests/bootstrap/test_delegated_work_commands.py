from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

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
        self.owned_work: list[SimpleNamespace] = []
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

    def list_owned(self, **_values: object) -> tuple[SimpleNamespace, ...]:
        return tuple(self.owned_work)

    def load(self, work_id: str, **_values: object) -> SimpleNamespace:
        from eva.adapters.postgres.delegated_work import DelegatedWorkNotFound
        work = next((work for work in self.owned_work if work.spec.work_id == work_id), None)
        if work is None:
            raise DelegatedWorkNotFound("No work in this test scope")
        return work

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
            "lease_expires_at": NOW + timedelta(seconds=30),
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
    assert first_values["title"] == "uitzoeken welke trein morgen het snelst is"
    assert "trein" not in str(first_values["summary"])
    assert store.validated == [
        {
            "owner_principal_id": OWNER,
            "principal_binding_id": BINDING,
        }
    ]


def test_owner_admission_passes_current_destination_in_same_store_call():
    from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
    store = FakeStore()
    destination = object()
    received = []
    def resolve(turn):
        received.append(turn)
        return destination
    service = DelegatedWorkOwnerService(
        store, owner_principal_id=OWNER, principal_binding_id=BINDING,
        delivery_destination_loader=resolve,
    )
    turn = _turn("Onderzoek dit")
    service.execute("start", turn, task="Onderzoek dit", request_key="return-route")
    assert received == [turn]
    assert len(store.admitted) == 1
    assert store.admitted[0]["delivery_destination"] is destination
    assert len(store.validated) == 2  # Initial composition and current admission.


def test_owner_destination_failure_does_not_admit_unreturnable_work():
    from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
    store = FakeStore()
    def revoked(turn):
        raise RuntimeError("owner route revoked")
    service = DelegatedWorkOwnerService(
        store, owner_principal_id=OWNER, principal_binding_id=BINDING,
        delivery_destination_loader=revoked,
    )
    with pytest.raises(RuntimeError, match="owner route revoked"):
        service.execute("start", _turn("Onderzoek dit"), task="Onderzoek dit", request_key="return-route")
    assert store.admitted == []


def test_owner_gets_latest_task_status_and_can_stop_work_before_call() -> None:
    store = FakeStore()
    store.owned_work = [store._work()]
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
        "Je laatste onderzoek is ingepland.\nOpdracht: Test\n"
        "Taaknummer: work:fake (poging 1).\n"
        "De status van de automatische terugmelding is onbekend."
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


def test_legacy_saved_reply_is_not_released_after_database_binding_revocation():
    import pytest
    from eva.adapters.postgres.delegated_work import DelegatedWorkConflict
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
    from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
    from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings

    class RevocableStore(FakeStore):
        revoked = False
        def validate_admission_authority(self, **values):
            if self.revoked:
                raise DelegatedWorkConflict("binding is not current")
            super().validate_admission_authority(**values)

    bindings = TelegramPrincipalBindings((TelegramPrincipalBinding(
        telegram_user_id=42, principal_id=str(OWNER), allowed_chat_ids=("42",),
        principal_binding_id=str(BINDING),
    ),))
    store = RevocableStore()
    service = DelegatedWorkOwnerService(
        store, owner_principal_id=OWNER, principal_binding_id=BINDING, clock=lambda: NOW,
        private_bridge_principal_loader=lambda: bindings.private_bridge_principal(
            principal_id=str(OWNER), principal_binding_id=BINDING),
    )
    runtime = PostgresHindsightMemoryRuntime.__new__(PostgresHindsightMemoryRuntime)
    runtime._telegram_principal_bindings = bindings
    runtime._telegram_principal_bindings_loader = lambda: bindings
    runtime._delegated_work_commands = DelegatedWorkConversationCommands(service=service)
    saved = []
    journal_access = []
    def commit(turn, reply):
        saved.append(reply)
        return reply
    runtime._journal = lambda turn: SimpleNamespace(
        admit=lambda actual: journal_access.append(actual),
        committed_reply=lambda actual: saved[0] if saved else None,
        commit_reply=commit,
    )
    turn = _turn("Laat een onderzoeker een voorstel schrijven", principal_id="telegram:42")
    first = runtime.respond_telegram_delegated_work(turn, telegram_user_id=42, chat_id="42")
    assert runtime.respond_telegram_delegated_work(turn, telegram_user_id=42, chat_id="42") == first
    assert len(saved) == len(store.admitted) == 1
    previous_access = list(journal_access)
    store.revoked = True  # File and structural alias remain unchanged.
    assert service.available_for(turn)
    with pytest.raises(DelegatedWorkConflict, match="binding is not current"):
        runtime.respond_telegram_delegated_work(turn, telegram_user_id=42, chat_id="42")
    assert journal_access == previous_access
    assert len(saved) == len(store.admitted) == 1


@pytest.mark.parametrize(("state", "stop_requested", "expected"), [
    ("call_started", False, "gestart; er is nog geen bevestigde uitkomst"),
    ("needs_help", False, "vraagt aandacht; er is geen geslaagd resultaat"),
    ("call_started", True, "stopopdracht bewaard; het resultaat wordt niet geleverd"),
    ("stopped", True, "gestopt; wordt alleen op jouw verzoek hervat"),
    ("succeeded", False, "klaar; je kunt het resultaat opvragen"),
    ("ambiguous", False, "uitkomst onzeker; wordt niet automatisch opnieuw uitgevoerd"),
])
def test_status_explains_latest_lifecycle_without_claiming_cancellation(state, stop_requested, expected):
    store = FakeStore()
    store.owned_work = [store._work(state=state, stop_requested_at=NOW if stop_requested else None)]
    reply = _commands(store).handle(_turn("Hoe staat mijn onderzoek?"), principal_binding_id=BINDING)
    assert reply.text.startswith(f"Je laatste onderzoek is {expected}.\n")


@pytest.mark.parametrize(("output", "expected"), [
    (json.dumps({"summary": "Een rustige gezinsdag.", "findings": ["Wandel samen.", "Kook samen."]}),
     "Een rustige gezinsdag.\n\n- Wandel samen.\n- Kook samen."),
    (json.dumps({"summary": "Alleen een samenvatting.", "findings": []}), "Alleen een samenvatting."),
    ("Ouder tekstresultaat", "Ouder tekstresultaat"),
    ('{"summary":"Kort","findings":[],"extra":"Behouden"}', '{"summary":"Kort","findings":[],"extra":"Behouden"}'),
    ('{"summary":"Kort","findings":[42]}', '{"summary":"Kort","findings":[42]}'),
])
def test_result_is_readable_without_changing_or_discarding_stored_content(output, expected):
    store = FakeStore()
    result = SimpleNamespace(output=output, output_sha256="hash", completed_at="2026-08-26T14:00:00Z")
    store.owned_work = [store._work(state="succeeded", result=result)]
    reply = _commands(store).handle(_turn("Toon het resultaat van mijn onderzoek"), principal_binding_id=BINDING)
    assert reply.text == expected + "\n\nDe status van de automatische terugmelding is onbekend."
    assert result.output == output
    assert store.admitted == []


@pytest.mark.parametrize("lease_expires_at", [None, NOW - timedelta(seconds=1), NOW, NOW + timedelta(seconds=30)])
@pytest.mark.parametrize("stop_requested", [False, True])
def test_status_does_not_infer_live_worker_activity_from_persisted_call(lease_expires_at, stop_requested):
    store = FakeStore()
    store.owned_work = [store._work(
        state="call_started", lease_expires_at=lease_expires_at,
        stop_requested_at=NOW if stop_requested else None,
    )]
    reply = _commands(store).handle(_turn("Hoe staat mijn onderzoek?"), principal_binding_id=BINDING)
    assert "bezig" not in reply.text
    assert "actief" not in reply.text
    if stop_requested:
        assert "stopopdracht bewaard; het resultaat wordt niet geleverd" in reply.text
    else:
        assert "gestart; er is nog geen bevestigde uitkomst" in reply.text


@pytest.mark.parametrize("state,phrase", [
    ("delivery_acknowledged", "afgeleverd in je gekoppelde privéchat"),
    ("delivery_ambiguous", "onzeker; ik stuur niet automatisch opnieuw"),
    ("unbound", "Geen automatische Telegram-terugmelding gekoppeld"),
])
def test_shared_owner_view_and_telegram_show_delivery_separately_from_result(state,phrase):
    from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
    store = FakeStore()
    result = SimpleNamespace(output="Het opgeslagen resultaat met bron.",output_sha256="hash",
                             completed_at="2026-08-26T14:00:00Z")
    store.owned_work=[store._work(state="succeeded",result=result)]
    calls=[]
    def load(work_id):
        calls.append(work_id)
        return state
    service=DelegatedWorkOwnerService(store,owner_principal_id=OWNER,principal_binding_id=BINDING,
                                     clock=lambda:NOW,delivery_status_loader=load)
    turn=_turn("Hoe staat mijn onderzoek?")
    view=service.execute("get",turn,work_id="work:fake",request_key="get")["work"]
    assert view["state"]=="succeeded" and view["result_available"]
    assert view["result"]["output"]==result.output
    assert view["delivery"]["state"]==state and phrase in view["delivery"]["description"]
    commands=DelegatedWorkConversationCommands(service=service)
    for question in ("Hoe staat mijn onderzoek?", "Toon mijn onderzoeken", "Toon het resultaat van mijn onderzoek"):
        reply=commands.handle(_turn(question),principal_binding_id=BINDING)
        assert phrase in reply.text
        if "resultaat" in question:
            assert reply.text.startswith(result.output+"\n\n")
    assert set(calls)=={"work:fake"} and store.admitted==[]


@pytest.mark.parametrize("unavailable", ["raises", "unknown_value", "invalid_type"])
def test_delivery_lookup_failure_does_not_hide_valid_result_or_leak_details(unavailable):
    from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
    store=FakeStore()
    result=SimpleNamespace(output="A valid saved result",output_sha256="hash",completed_at="2026-08-26T14:00:00Z")
    store.owned_work=[store._work(state="succeeded",result=result)]
    def load(work_id):
        if unavailable=="raises": raise ValueError("private-chat:secret credential expired")
        return "private-chat:secret" if unavailable=="unknown_value" else {"secret":"route"}
    service=DelegatedWorkOwnerService(store,owner_principal_id=OWNER,principal_binding_id=BINDING,
                                     clock=lambda:NOW,delivery_status_loader=load)
    work=service.execute("result",_turn("result"),work_id="work:fake",request_key="result")["work"]
    assert work["result"]["output"]==result.output and work["result_available"]
    assert work["delivery"]["state"]==("blocked" if unavailable=="raises" else "unknown")
    assert "secret" not in json.dumps(work) and "credential" not in json.dumps(work)


def test_latest_status_does_not_select_an_older_active_job_from_display_order():
    store = FakeStore()
    older = store._work()
    newer = store._work(state="succeeded", spec=SimpleNamespace(
        **(vars(older.spec) | {"work_id": "work:newer", "task": "Newer completed task"})))
    store.owned_work = [newer, older]
    store.list_owned = lambda **kwargs: tuple(
        [older, newer] if kwargs.get("active_first") else [newer, older]
    )[:kwargs.get("limit", 20)]
    reply = _commands(store).handle(_turn("Hoe staat mijn onderzoek?"), principal_binding_id=BINDING)
    assert "work:newer" in reply.text
    assert "Newer completed task" in reply.text
    assert "work:fake" not in reply.text
    assert "klaar; je kunt het resultaat opvragen" in reply.text


@pytest.mark.parametrize("text", ["Hoe staat mijn onderzoek?", "Toon het resultaat van mijn onderzoek"])
def test_legacy_task_reply_includes_shared_failure_explanation(text):
    store = FakeStore()
    store.owned_work = [store._work(state="ambiguous", error_code="provider_unavailable")]
    reply = _commands(store).handle(_turn(text), principal_binding_id=BINDING)
    assert "Een benodigde dienst was niet bereikbaar." in reply.text
    assert "Controleer eerdere bestanden of acties voordat je hervat." in reply.text
    assert not store.admitted and not store.stopped
