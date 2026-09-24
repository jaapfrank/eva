"""Contact routing preserves the ordinary shared conversation authority path."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest

from eva.application.conversation import (
    ConversationActionCall, ConversationActionReceipt, ConversationModelResult,
    ConversationModelUsage, ConversationTurn, IdentityProfile, LivingConversation,
    MEMORY_SEARCH_ACTION, RecentConversationMessage,
)
from eva.application.memory_audience import MemoryAudience, HouseholdConsentReference
from eva.application.voice_context import VoiceContext, voice_context
from tests.application.test_conversation import _Memory, NOW


IDENTITY = IdentityProfile("Eva", "Huisgenoot.", ("Wees eerlijk.",), 160)


def turn(message="one", text="Hallo", principal="jaap"):
    return ConversationTurn.private("tenant", "installation", principal,
                                    "conversation", message, text, NOW)


class Model:
    def __init__(self, choose):
        self.choose = choose
        self.calls = []

    def generate(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        answer = self.choose(args[0], kwargs)
        return ConversationModelResult(
            text=answer if isinstance(answer, str) else None, provider="fixture",
            action_call=None if isinstance(answer, str) else answer,
            usage=ConversationModelUsage.measured(input_tokens=10, output_tokens=5, tool_calls=0),
        )


class Actions:
    action_names = ("start_delegated_work", "stop_delegated_work", "inspect_item")

    def __init__(self):
        self.jobs = {}

    def pending(self, turn):
        return None

    def active_context(self, turn):
        return (repr(self.jobs),)

    def available_action_names(self, turn):
        return self.action_names if turn.audience.kind == "private_person" else ()

    def continuation_action_names(self, turn):
        return self.available_action_names(turn)

    def execute(self, turn, call):
        if call.name == "start_delegated_work":
            self.jobs[turn.message_id] = "admitted"
        elif call.name == "stop_delegated_work":
            self.jobs[call.arguments["work_id"]] = "stopped"
        return ConversationActionReceipt(call, {"status": "verified", "work_id": turn.message_id})


def test_simple_turn_uses_one_contact_call_with_same_authorized_context():
    contact = Model(lambda t, k: "Hallo!")
    work = Model(lambda t, k: pytest.fail("work model must not run"))
    memory = _Memory()
    recent = (RecentConversationMessage("user", "Vorige vraag", NOW),)
    reply = LivingConversation(memory, work, IDENTITY, contact_model=contact).respond(turn(), recent)
    assert len(contact.calls) == 1
    args, kwargs = contact.calls[0]
    assert args[1] is IDENTITY and args[3] is recent
    assert args[2].confirmed_facts[0].subject == "jaap"
    assert memory.query.principal_id == "jaap"
    assert reply.model_usage.model_attempts == 1


@pytest.mark.parametrize("action", ["inspect_item", MEMORY_SEARCH_ACTION])
def test_real_tool_continuation_uses_work_but_memory_search_stays_contact(action):
    contact = Model(lambda t, k: ConversationActionCall("call", action,
        {"query_text": "voorkeur"} if action == MEMORY_SEARCH_ACTION else {})
        if k["completed_action"] is None else "Dit weet ik uit je geheugen.")
    work = Model(lambda t, k: "Gelezen en gecontroleerd.")
    reply = LivingConversation(_Memory(), work, IDENTITY, Actions(), contact_model=contact).respond(turn())
    assert len(work.calls) == (1 if action == "inspect_item" else 0)
    assert reply.model_usage.model_attempts == 2
    if work.calls:
        assert work.calls[0][0][1] is IDENTITY
        assert work.calls[0][1]["completed_action"].call.name == action


def test_two_background_tasks_allow_chat_and_targeted_stop_without_waiting_for_worker():
    actions = Actions()
    worker_started, release_worker = Event(), Event()

    def choose(t, k):
        if k["completed_action"] is not None:
            assert t.message_id in actions.jobs or t.text == "stop"
            if k["completed_action"].call.name == "start_delegated_work":
                assert k["available_actions"] == ()
            return "De opgeslagen taakstatus is bijgewerkt."
        if t.text == "schrijf":
            return ConversationActionCall(t.message_id, "start_delegated_work", {"task": "schrijf"})
        if t.text == "stop":
            return ConversationActionCall(t.message_id, "stop_delegated_work", {"work_id": "one"})
        return "Vier."

    def worker():
        worker_started.set()
        assert release_worker.wait(5)

    work = Model(lambda t, k: pytest.fail("queued worker must not run inline"))
    conversation = LivingConversation(_Memory(), work, IDENTITY, actions, contact_model=Model(choose))
    with ThreadPoolExecutor(max_workers=2) as executor:
        running = executor.submit(worker)
        try:
            assert worker_started.wait(1)
            conversation.respond(turn("one", "schrijf"))
            conversation.respond(turn("two", "schrijf"))
            reply = executor.submit(conversation.respond, turn("three", "2+2")).result(timeout=1)
            assert reply.text == "Vier." and not running.done()
            conversation.respond(turn("four", "stop"))
            assert actions.jobs == {"one": "stopped", "two": "admitted"}
            conversation.respond(turn("five", "schrijf"))
            assert actions.jobs["five"] == "admitted"
        finally:
            release_worker.set()
        running.result()


def test_household_contact_receives_household_scope_without_owner_tools():
    household = replace(turn(principal="household-device"),
                        conversation_id="household:home:conversation:speaker",
                        message_id="household:home:conversation:speaker:one",
                        audience=MemoryAudience.household(principal_id="household-device", household_id="home",
                            consent_ref=HouseholdConsentReference("household-device", "home", "household-consent:one")))
    memory = _Memory()
    contact = Model(lambda t, k: "Dag!")
    LivingConversation(memory, contact, IDENTITY, Actions(), contact_model=contact).respond(household)
    assert memory.query.scope == {"kind": "household", "household_id": "home"}
    assert memory.query.principal_id == "household-device"
    assert contact.calls[0][1]["available_actions"] == ()


def test_spoken_first_part_hands_over_to_work_model_with_receipt():
    events = []
    contact = Model(lambda t, k: ConversationActionCall("speak", "speak_and_continue",
                                                       {"text": "Ik vergelijk de opties."}))
    work = Model(lambda t, k: "Dit is het vervolg.")
    with voice_context(VoiceContext((), (), lambda _: None, events.append)):
        LivingConversation(_Memory(), work, IDENTITY, contact_model=contact).respond(turn())
    assert len(contact.calls) == len(work.calls) == 1
    assert events == [{"type": "spoken_update", "text": "Ik vergelijk de opties."}]
    assert work.calls[0][1]["completed_action"].output["heard"] is False


def test_failed_admission_never_generates_acceptance():
    class Refused(Actions):
        def execute(self, turn, call):
            raise RuntimeError("authority revoked")
    contact = Model(lambda t, k: ConversationActionCall("start", "start_delegated_work", {"task": "x"}))
    with pytest.raises(RuntimeError, match="authority revoked"):
        LivingConversation(_Memory(), contact, IDENTITY, Refused(), contact_model=contact).respond(turn())
    assert len(contact.calls) == 1


def test_provider_journal_follows_selected_model_without_double_counting():
    from eva.application.conversation import begin_current_model_request, complete_current_model_request
    class Journal:
        def __init__(self):
            self.admitted = []
            self.completed = []
        def begin_model_request(self, t):
            key = str(len(self.admitted))
            self.admitted.append(key)
            return key
        def complete_model_request(self, t, key, usage):
            self.completed.append((key, usage))
        def model_usage(self, t):
            return ConversationModelUsage.aggregate(tuple(u for _, u in self.completed))
    class Journaled(Model):
        journals_provider_requests = True
        def generate(self, *args, **kwargs):
            key = begin_current_model_request()
            reply = super().generate(*args, **kwargs)
            complete_current_model_request(key, reply.usage)
            return reply
    contact = Journaled(lambda t, k: ConversationActionCall("inspect", "inspect_item", {}))
    work = Model(lambda t, k: "Klaar.")
    journal = Journal()
    reply = LivingConversation(_Memory(), work, IDENTITY, Actions(), contact_model=contact).respond(
        turn(), model_journal=journal)
    assert journal.admitted == ["0", "1"]
    assert [key for key, _ in journal.completed] == journal.admitted
    assert reply.model_usage.model_attempts == 2
    assert reply.model_usage.input_tokens == 20


def test_autonomous_work_keeps_existing_work_model():
    from eva.application.initiative_execution import initiative_execution
    class Guard:
        def check(self, turn):
            pass
        def instructions(self):
            return "Voer alleen de toegestane achtergrondtaak uit."
    work = Model(lambda t, k: "Achtergrondantwoord.")
    contact = Model(lambda t, k: pytest.fail("autonomous work must keep its work model"))
    with initiative_execution(Guard()):
        LivingConversation(_Memory(), work, IDENTITY, contact_model=contact).respond(
            replace(turn(), conversation_id="initiative-task:one"))
    assert len(work.calls) == 1


@pytest.mark.parametrize("stopped", [False, True])
def test_admitted_acceptance_uses_one_contact_call_and_current_reply_guard(stopped):
    class Admitted(Actions):
        def execute(self, turn, call):
            assert call.arguments["acceptance_message"] == "Ik zoek het voor je uit."
            return ConversationActionReceipt(call, {"status": "verified",
                "acceptance_message": call.arguments["acceptance_message"]})

        def reply_guard(self, turn, receipts):
            if not receipts:
                return None
            assert len(receipts) == 1
            return lambda text: "Je opdracht is gestopt." if stopped else text

    contact = Model(lambda t, k: ConversationActionCall("start", "start_delegated_work",
        {"task": "Onderzoek", "acceptance_message": "Ik zoek het voor je uit."}))
    work = Model(lambda t, k: pytest.fail("work model must not run inline"))
    reply = LivingConversation(_Memory(), work, IDENTITY, Admitted(), contact_model=contact).respond(turn())
    assert len(contact.calls) == 1
    assert reply.model_usage.model_attempts == 1
    assert reply.text == ("Je opdracht is gestopt." if stopped else "Ik zoek het voor je uit.")


@pytest.mark.parametrize("permitted, revoked", [(True, False), (False, False), (True, True)])
def test_provider_lookup_can_handoff_only_with_current_continuation_permission(permitted, revoked):
    class LookupActions(Actions):
        action_names = ("list_reusable_procedures", "start_digital_work")
        def __init__(self):
            super().__init__()
            self.looked_up = False
        def available_action_names(self, turn):
            return (("list_reusable_procedures",) if self.looked_up and revoked else self.action_names)
        def can_continue_after(self, turn, receipt):
            assert receipt.output["status"] == "candidates"
            return permitted
        def execute(self, turn, call):
            assert call.name == "list_reusable_procedures"
            self.looked_up = True
            return ConversationActionReceipt(call, {"status": "candidates", "candidates": ["saved-source"]})
        def execute_step(self, turn, call, *, step_index):
            assert call.name == "start_digital_work" and step_index == 1
            self.jobs[turn.message_id] = "admitted"
            return ConversationActionReceipt(call, {"status": "verified", "work_id": turn.message_id})
    actions = LookupActions()
    contact = Model(lambda t, k: ConversationActionCall("lookup", "list_reusable_procedures", {}))
    def next_step(t, k):
        if k["completed_action"].call.name == "start_digital_work":
            assert k["available_actions"] == ()
            return "De opdracht is aangenomen."
        if "start_digital_work" in k["available_actions"]:
            return ConversationActionCall("handoff", "start_digital_work", {"task": "Use saved-source"})
        return "Geen uitvoer gestart."
    work = Model(next_step)
    LivingConversation(_Memory(), work, IDENTITY, actions, contact_model=contact).respond(turn())
    assert bool(actions.jobs) is (permitted and not revoked)
    assert len(contact.calls) == 1
