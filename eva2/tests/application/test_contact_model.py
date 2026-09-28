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


@pytest.mark.parametrize("action", [
    "inspect_item", "designate_reusable_procedure", "run_reusable_procedure", MEMORY_SEARCH_ACTION,
])
def test_real_tool_continuation_uses_work_but_memory_search_stays_contact(action):
    class RoutedActions(Actions):
        action_names = (*Actions.action_names, "designate_reusable_procedure", "run_reusable_procedure")

    contact = Model(lambda t, k: ConversationActionCall("call", action,
        {"query_text": "voorkeur"} if action == MEMORY_SEARCH_ACTION else {})
        if k["completed_action"] is None else "Dit weet ik uit je geheugen.")
    work = Model(lambda t, k: "Gelezen en gecontroleerd.")
    reply = LivingConversation(_Memory(), work, IDENTITY, RoutedActions(), contact_model=contact).respond(turn())
    assert len(work.calls) == (0 if action == MEMORY_SEARCH_ACTION else 1)
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


@pytest.mark.parametrize("lookup", ["list_reusable_procedures", "resolve_reusable_procedure"])
@pytest.mark.parametrize("permitted, revoked", [(True, False), (False, False), (True, True)])
def test_provider_lookup_can_handoff_only_with_current_continuation_permission(lookup, permitted, revoked):
    status = "candidates" if lookup == "list_reusable_procedures" else "resolved"

    class LookupActions(Actions):
        action_names = (lookup, "start_digital_work")
        def __init__(self):
            super().__init__()
            self.looked_up = False
        def available_action_names(self, turn):
            return ((lookup,) if self.looked_up and revoked else self.action_names)
        def can_continue_after(self, turn, receipt):
            assert receipt.output["status"] == status
            return permitted
        def execute(self, turn, call):
            assert call.name == lookup
            self.looked_up = True
            return ConversationActionReceipt(call, {"status": status, "candidates": ["saved-source"]})
        def execute_step(self, turn, call, *, step_index):
            assert call.name == "start_digital_work" and step_index == 1
            self.jobs[turn.message_id] = "admitted"
            return ConversationActionReceipt(call, {"status": "verified", "work_id": turn.message_id})
    actions = LookupActions()
    def next_step(t, k):
        if k["completed_action"] is None:
            return ConversationActionCall("lookup", lookup, {})
        if k["completed_action"].call.name == "start_digital_work":
            assert k["available_actions"] == ()
            return "De opdracht is aangenomen."
        if "start_digital_work" in k["available_actions"]:
            return ConversationActionCall("handoff", "start_digital_work", {"task": "Use saved-source"})
        return "Geen uitvoer gestart."
    contact = Model(next_step)
    work = Model(lambda t, k: pytest.fail("procedure discovery must stay on the contact model"))
    LivingConversation(_Memory(), work, IDENTITY, actions, contact_model=contact).respond(turn())
    assert bool(actions.jobs) is (permitted and not revoked)
    assert len(contact.calls) == (3 if permitted and not revoked else 2)


@pytest.mark.parametrize("lookup", ["inspect_item", "list_reusable_procedures", "resolve_reusable_procedure"])
@pytest.mark.parametrize("stopped", [False, True])
def test_acceptance_after_lookup_needs_no_post_admission_model_call(lookup, stopped):
    contact_continuation = lookup != "inspect_item"
    status = {"list_reusable_procedures": "candidates", "resolve_reusable_procedure": "resolved"}.get(lookup, "verified")

    class AdmittedAfterLookup(Actions):
        action_names = (lookup, "start_digital_work")
        def __init__(self):
            super().__init__()
            self.guarded = []
        def execute(self, turn, call):
            assert call.name == lookup
            return ConversationActionReceipt(call, {"status": status})
        def can_continue_after(self, turn, receipt):
            assert receipt.output["status"] == status
            return True
        def execute_step(self, turn, call, *, step_index):
            assert step_index == 1 and call.name == "start_digital_work"
            self.jobs[turn.message_id] = "admitted"
            return ConversationActionReceipt(call, {"status": "verified",
                "acceptance_message": call.arguments["acceptance_message"]})
        def reply_guard(self, turn, receipts):
            if not any(r.call.name == "start_digital_work" for r in receipts):
                return None
            assert len(receipts) == 2
            def guard(text):
                self.guarded.append(text)
                return "De opdracht is gestopt." if stopped else text
            return guard
    actions = AdmittedAfterLookup()
    def accept(t, k):
        assert k["completed_action"].call.name == lookup, "No extra call after admission"
        return ConversationActionCall("start", "start_digital_work",
            {"task": "Apply the found method", "acceptance_message": "Ik ga ermee aan de slag."})
    def choose(t, k):
        if k["completed_action"] is None:
            return ConversationActionCall("lookup", lookup, {})
        assert contact_continuation, "Effectful tool continuation must use the work model"
        return accept(t, k)
    contact = Model(choose)
    def work_step(t, k):
        assert not contact_continuation, "Procedure discovery must not promote to the work model"
        return accept(t, k)
    work = Model(work_step)
    reply = LivingConversation(_Memory(), work, IDENTITY, actions, contact_model=contact).respond(turn())
    assert len(contact.calls) == (2 if contact_continuation else 1)
    assert len(work.calls) == (0 if contact_continuation else 1)
    assert reply.model_usage.model_attempts == 2
    assert actions.jobs == {"one": "admitted"}
    assert actions.guarded == ["Ik ga ermee aan de slag."]
    assert reply.text == ("De opdracht is gestopt." if stopped else "Ik ga ermee aan de slag.")


@pytest.mark.parametrize("lookup", [None, "list_reusable_procedures", "resolve_reusable_procedure"])
@pytest.mark.parametrize("household", [False, True])
def test_procedure_execution_uses_available_worker_before_or_after_contact_lookup(lookup, household):
    source = turn(text="Apply the saved method and preserve the result.")
    if household:
        source = replace(source, principal_id="household-device",
            conversation_id="household:home:conversation:speaker", message_id="household:home:conversation:speaker:one",
            audience=MemoryAudience.household(
            principal_id="household-device", household_id="home",
            consent_ref=HouseholdConsentReference("household-device", "home", "household-consent:one")))
    class ProcedureActions(Actions):
        action_names = ("list_reusable_procedures", "resolve_reusable_procedure",
                        "run_reusable_procedure", "start_digital_work")
        def __init__(self):
            super().__init__()
            self.executed = []
        def available_action_names(self, t):
            assert t is source
            return self.action_names
        def can_continue_after(self, t, receipt): return receipt.call.name == lookup
        def execute(self, t, call):
            assert t is source and call.name != "run_reusable_procedure"
            self.executed.append(call.name)
            if call.name == "start_digital_work":
                self.jobs[t.message_id] = "admitted"
                return ConversationActionReceipt(call, {"status":"verified", "work_id":t.message_id,
                    "acceptance_message":call.arguments["acceptance_message"]})
            return ConversationActionReceipt(call, {"status":"resolved" if lookup == "resolve_reusable_procedure" else "candidates"})
        def execute_step(self, t, call, *, step_index):
            assert step_index == 1
            return self.execute(t, call)
        def reply_guard(self, t, receipts):
            return (lambda text: text) if any(r.call.name == "start_digital_work" for r in receipts) else None
    def choose(t, context):
        assert t is source
        assert "run_reusable_procedure" not in context["available_actions"]
        assert {"list_reusable_procedures", "resolve_reusable_procedure", "start_digital_work"} <= set(context["available_actions"])
        if lookup is not None and context["completed_action"] is None:
            return ConversationActionCall("lookup", lookup, {})
        return ConversationActionCall("admit", "start_digital_work", {
            "task":t.text, "acceptance_message":"Ik ga ermee aan de slag."})
    actions = ProcedureActions()
    contact = Model(choose)
    work = Model(lambda *_: pytest.fail("worker execution must not run in foreground"))
    reply = LivingConversation(_Memory(), work, IDENTITY, actions, contact_model=contact).respond(source)
    assert actions.executed == ([lookup] if lookup else []) + ["start_digital_work"]
    assert actions.jobs == {source.message_id:"admitted"}
    assert len(contact.calls) == (2 if lookup else 1)
    assert reply.text == "Ik ga ermee aan de slag."


@pytest.mark.parametrize("contact_enabled, initially_available, later_available", [
    (False, True, True), (True, False, False), (True, False, True), (True, True, False)])
def test_procedure_gate_rechecks_current_worker_offer_and_preserves_fallback(contact_enabled, initially_available, later_available):
    class ChangingActions(Actions):
        action_names = ("resolve_reusable_procedure", "run_reusable_procedure", "start_digital_work")
        def __init__(self):
            super().__init__()
            self.resolved = False
        def available_action_names(self, t):
            available = later_available if self.resolved else initially_available
            return self.action_names if available else self.action_names[:-1]
        def can_continue_after(self, t, receipt): return True
        def execute(self, t, call):
            assert call.name == "resolve_reusable_procedure"
            self.resolved = True
            return ConversationActionReceipt(call, {"status":"resolved"})
    def choose(t, context):
        completed = context["completed_action"] is not None
        worker_available = later_available if completed else initially_available
        assert ("run_reusable_procedure" in context["available_actions"]) is not (contact_enabled and worker_available)
        if not completed:
            return ConversationActionCall("lookup", "resolve_reusable_procedure", {})
        return "Current route checked."
    model = Model(choose)
    reply = LivingConversation(_Memory(), model, IDENTITY, ChangingActions(),
                               contact_model=model if contact_enabled else None).respond(turn())
    assert reply.text == "Current route checked." and len(model.calls) == 2


def test_recovered_procedure_receipt_is_preserved_without_replaying_execution():
    resolved = ConversationActionReceipt(ConversationActionCall("lookup", "resolve_reusable_procedure", {}),
                                          {"status":"verified", "source":"saved-source"})
    executed = ConversationActionReceipt(ConversationActionCall("executed", "run_reusable_procedure", {}),
                                          {"status":"verified", "result":"saved-result"})
    class RecoveredActions(Actions):
        action_names = ("run_reusable_procedure", "start_digital_work", "save_workspace_document")
        def pending(self, t): return resolved
        def pending_step(self, t, *, step_index): return executed if step_index == 1 else None
        def execute(self, *args): pytest.fail("retained effect must not be repeated")
        def execute_step(self, *args, **kwargs): pytest.fail("retained effect must not be repeated")
    def finish(t, context):
        assert context["completed_action"] is executed
        assert context["prior_actions"] == (resolved,)
        assert "run_reusable_procedure" not in context["available_actions"]
        assert "save_workspace_document" in context["available_actions"]
        return "The existing result remains available."
    contact = Model(lambda *_: pytest.fail("a completed effect keeps its work-model continuation"))
    work = Model(finish)
    reply = LivingConversation(_Memory(), work, IDENTITY, RecoveredActions(), contact_model=contact).respond(turn())
    assert reply.text == "The existing result remains available." and len(work.calls) == 1


@pytest.mark.parametrize("contact_enabled, research_available", [(True, True), (True, False), (False, True)])
def test_existing_synchronous_research_gate_remains_conditional(contact_enabled, research_available):
    class ResearchActions(Actions):
        action_names = ("research_current_options", "start_background_research") if research_available else ("research_current_options",)
    def choose(t, context):
        assert ("research_current_options" in context["available_actions"]) is not (contact_enabled and research_available)
        return "Route checked."
    model = Model(choose)
    LivingConversation(_Memory(), model, IDENTITY, ResearchActions(),
                       contact_model=model if contact_enabled else None).respond(turn())


def test_unoffered_foreground_procedure_is_rejected_before_execution():
    class ProcedureActions(Actions):
        action_names = ("run_reusable_procedure", "start_digital_work")
        def execute(self, *args): pytest.fail("unoffered procedure must not execute inline")
    contact = Model(lambda t, context: ConversationActionCall("attempt", "run_reusable_procedure", {}))
    work = Model(lambda *_: pytest.fail("unoffered action must not promote to work model"))
    with pytest.raises(RuntimeError, match="unavailable action"):
        LivingConversation(_Memory(), work, IDENTITY, ProcedureActions(), contact_model=contact).respond(turn())
