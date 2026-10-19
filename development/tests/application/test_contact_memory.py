"""The first contact decision can use context without waiting for history."""
from dataclasses import replace

import pytest

from eva.application.cancellation import CancellationSignal, WorkInterrupted, cancellation_scope
from eva.application.conversation import (
    ConversationActionCall, ConversationActionReceipt, ConversationModelResult,
    ConversationObservationBinding, LivingConversation, MEMORY_SEARCH_ACTION,
    RecentConversationMessage,
)
from eva.application.memory_audience import HouseholdConsentReference, MemoryAudience
from eva.contracts.canonical_json import canonical_sha256
from tests.application.test_contact_model import Actions, IDENTITY, Model, turn
from tests.application.test_conversation import _Memory, NOW


def test_first_contact_preserves_context_and_never_starts_historical_retrieval():
    class UnavailableMemory:
        def recall_for_presence(self, query):
            pytest.fail("ordinary contact must not wait for historical retrieval")
        recall_for_presence_fast = recall_for_presence

    class Context(Actions):
        def active_context(self, source):
            assert source is request
            return ("There is already an admitted task; it is not completed.",)

    request = turn(text="Hoe gaat het?")
    recent = (RecentConversationMessage("Eva", "Ik ben bezig met je opdracht.", NOW),)
    contact = Model(lambda *_: "Het gaat goed; ik ben nog bezig met je opdracht.")
    work = Model(lambda *_: pytest.fail("no second model decision"))
    reply = LivingConversation(UnavailableMemory(), work, IDENTITY, Context(),
        contact_model=contact).respond(request, recent)
    args, options = contact.calls[0]
    assert args[0] is request and args[1] is IDENTITY and args[3] is recent
    assert options["self_context"][0].startswith("There is already an admitted task")
    assert options["available_actions"] == Context.action_names
    assert args[2].confirmed_facts == () and args[2].style_mirror is None
    assert len(contact.calls) == 1 and reply.model_usage.model_attempts == 1
    assert reply.evidence_memory_ids == ()


def test_private_style_remains_available_and_journaled_without_historical_recall():
    import hashlib
    style = "Kort en direct, met ruimte voor een grap."
    source = turn()
    class Memory:
        def read_style_context(self, query):
            assert query.principal_id == source.principal_id and query.audience is source.audience
            assert query.scope == {"kind": "person", "principal_id": source.principal_id}
            return style
        def recall_for_presence(self, query):
            pytest.fail("the style read must not imply a historical lookup")
        recall_for_presence_fast = recall_for_presence
    class Journal:
        def __init__(self):
            self.styles = []
            self.usages = []
        def begin_model_request(self, source):return "request:one"
        def record_model_memory_context(self, source, request, *, style_mirror_sha256):
            self.styles.append(style_mirror_sha256)
        def complete_model_request(self, source, request, usage):self.usages.append(usage)
        def model_usage(self, source):
            from eva.application.conversation import ConversationModelUsage
            return ConversationModelUsage.aggregate(tuple(self.usages))
    contact, journal = Model(lambda *_: "Hoi!"), Journal()
    LivingConversation(Memory(), contact, IDENTITY, contact_model=contact).respond(source, model_journal=journal)
    assert len(contact.calls) == 1 and contact.calls[0][0][2].style_mirror == style
    assert journal.styles == [hashlib.sha256(style.encode()).hexdigest()]


def test_private_style_authority_failure_does_not_start_a_model():
    class Memory:
        def read_style_context(self, query):raise ValueError("style authority changed")
    contact = Model(lambda *_: pytest.fail("unvalidated style must not be bypassed"))
    with pytest.raises(ValueError, match="style authority changed"):
        LivingConversation(Memory(), contact, IDENTITY, contact_model=contact).respond(turn())


@pytest.mark.parametrize("audience", ["private_person", "household", "project"])
@pytest.mark.parametrize("found", [False, True])
def test_requested_history_keeps_scope_and_replaces_unqueried_state(audience, found):
    request = turn(text="Wat had ik daar eerder over afgesproken?")
    scope = {"kind": "person", "principal_id": request.principal_id}
    if audience == "household":
        request = replace(request, principal_id="shared-speaker",
            conversation_id="household:home:conversation:one",
            message_id="household:home:conversation:one:request",
            audience=MemoryAudience.household(principal_id="shared-speaker", household_id="home",
                consent_ref=HouseholdConsentReference("shared-speaker", "home", "household-consent:one")))
        scope = {"kind": "household", "household_id": "home"}
    elif audience == "project":
        request = replace(request, project_id="project:one")
        scope = {"kind": "person_project", "principal_id": request.principal_id, "project_id": "project:one"}
    sequence, queries, history_calls = [], [], []

    class Memory(_Memory):
        def recall_for_presence_fast(self, query):
            pytest.fail("no automatic retrieval before the first choice")
        def recall_for_presence(self, query):
            sequence.append("recall")
            queries.append(query)
            result = super().recall_for_presence(query)
            if not found:
                result = replace(result, confirmed_facts=(),
                    recall=result.recall.model_copy(update={"selected": [],
                        "admitted_fact_ids_sha256": canonical_sha256([])}))
            return replace(result, style_mirror="Spreek eenvoudig.")

    class Contact(Model):
        def generate(self, *args, **kwargs):
            sequence.append("contact")
            return super().generate(*args, **kwargs)

    def choose(source, context):
        assert source is request
        if context["completed_action"] is None:
            return ConversationActionCall("memory", MEMORY_SEARCH_ACTION, {"query_text": "eerdere afspraak"})
        assert context["completed_action"].output["result_count"] == int(found)
        return "Ik heb je afspraak gevonden." if found else "Ik vind die afspraak niet terug."

    def history(source, query):
        history_calls.append((source, query))
        return ()

    model = Contact(choose)
    reply = LivingConversation(Memory(), Model(lambda *_: pytest.fail("search stays contact")),
        IDENTITY, contact_model=model, history_search=history).respond(request)
    assert sequence == ["contact", "recall", "contact"]
    assert len(queries) == 1
    query = queries[0]
    assert query.principal_id == request.principal_id and query.audience is request.audience
    assert query.conversation_id == request.conversation_id and query.project_id == request.project_id
    assert query.scope == scope and query.requested_valid_at == request.occurred_at
    assert query.query_text == request.text + "\nSemantische zoekconcepten: eerdere afspraak"
    before, after = model.calls[0][0][2], model.calls[1][0][2]
    assert before.recall.projection_version == "conversation-not-retrieved-v1"
    assert before.recall.scope_sha256 == canonical_sha256(scope)
    assert after.recall.projection_version != before.recall.projection_version
    assert bool(after.confirmed_facts) is found and after.style_mirror == "Spreek eenvoudig."
    assert history_calls == ([(request, "eerdere afspraak")] if audience == "private_person" else [])
    assert reply.evidence_memory_ids == (("hindsight:memory-1",) if found else ())
    assert reply.model_usage.model_attempts == 2


def test_escalated_work_still_receives_memory_before_its_existing_model_call():
    sequence = []
    class Memory(_Memory):
        def recall_for_presence_fast(self, query):
            sequence.append("recall")
            return super().recall_for_presence(query)
    class Tool(Actions):
        def execute(self, source, call):
            sequence.append("tool")
            return super().execute(source, call)
    class Contact(Model):
        def generate(self, *args, **kwargs):
            sequence.append("contact")
            assert not args[2].confirmed_facts
            return super().generate(*args, **kwargs)
    class Work(Model):
        def generate(self, *args, **kwargs):
            sequence.append("work")
            assert args[2].confirmed_facts
            return super().generate(*args, **kwargs)
    reply = LivingConversation(Memory(), Work(lambda *_: "Uitgezocht."), IDENTITY,
        Tool(), contact_model=Contact(lambda *_: ConversationActionCall("inspect", "inspect_item", {}))).respond(turn())
    assert sequence == ["contact", "tool", "recall", "work"]
    assert reply.model_usage.model_attempts == 2


@pytest.mark.parametrize("permission", [True, False, None, "true"])
def test_provider_can_authorize_a_contact_confirmation_without_core_tool_names(permission):
    class Module(Actions):
        action_names = ("module_apply",)
        def contact_reply_allowed(self, source, receipt):
            assert source is request and receipt.call.name == "module_apply"
            return permission
    request = turn(text="Voer de toegestane bediening uit.")
    memory = _Memory()
    contact = Model(lambda source, context: ConversationActionCall("apply", "module_apply", {})
        if context["completed_action"] is None else "De bediening is gecontroleerd.")
    work = Model(lambda *_: "De gewone werkroute heeft het resultaat beoordeeld.")
    LivingConversation(memory, work, IDENTITY, Module(), contact_model=contact).respond(request)
    assert len(contact.calls) == (2 if permission is True else 1)
    assert len(work.calls) == (0 if permission is True else 1)
    assert (memory.query is None) is (permission is True)


def test_provider_contact_policy_cannot_bypass_a_revoked_turn():
    class Revoked(Actions):
        action_names = ("module_apply",)
        def contact_reply_allowed(self, source, receipt):
            raise ValueError("authority revoked")
    contact = Model(lambda *_: ConversationActionCall("apply", "module_apply", {}))
    work = Model(lambda *_: pytest.fail("authority loss cannot fall back to another model"))
    with pytest.raises(ValueError, match="authority revoked"):
        LivingConversation(_Memory(), work, IDENTITY, Revoked(), contact_model=contact).respond(turn())
    assert len(contact.calls) == 1


def test_later_simple_receipt_does_not_downgrade_prior_substantive_work():
    class Module(Actions):
        action_names = ("inspect_item", "module_apply")
        def contact_reply_allowed(self, source, receipt):
            return receipt.call.name == "module_apply"
        def execute_step(self, source, call, *, step_index):
            assert step_index == 1
            return self.execute(source, call)
    contact = Model(lambda *_: ConversationActionCall("inspect", "inspect_item", {}))
    work = Model(lambda source, context: ConversationActionCall("apply", "module_apply", {})
        if context["completed_action"].call.name == "inspect_item" else "Het werk is afgerond.")
    LivingConversation(_Memory(), work, IDENTITY, Module(), contact_model=contact).respond(turn())
    assert len(contact.calls) == 1 and len(work.calls) == 2


@pytest.mark.parametrize("mode", ["observation", "replay"])
def test_bound_observation_and_replayed_work_keep_eager_memory(mode):
    request = turn()
    class Pending(Actions):
        def pending(self, source):
            if mode == "replay":
                return ConversationActionReceipt(ConversationActionCall("read", "read_home_assistant_state", {}),
                    {"status": "verified"})
    if mode == "observation":
        request = replace(request, observation_binding=ConversationObservationBinding(
            "observation:one", "a"*64, "b"*64, "image/png", "photo:one"))
    memory = _Memory()
    contact = Model(lambda *_: "Dit weet ik.")
    LivingConversation(memory, contact, IDENTITY, Pending(), contact_model=contact).respond(request)
    assert memory.query is not None and contact.calls[0][0][2].confirmed_facts
    if mode == "observation":
        assert request.observation_binding.model_context in memory.query.query_text


@pytest.mark.parametrize("stage", ["active_context", "retrieval"])
def test_cancellation_prevents_a_contact_answer_after_slow_context(stage):
    signal = CancellationSignal()
    class Context(Actions):
        def active_context(self, source):
            if stage == "active_context":signal.cancel()
            return ()
    class Memory(_Memory):
        def recall_for_presence(self, query):
            signal.cancel()
            return super().recall_for_presence(query)
    contact = Model(lambda *_: ConversationActionCall("memory", MEMORY_SEARCH_ACTION, {"query_text": "afspraak"}))
    with cancellation_scope(signal), pytest.raises(WorkInterrupted):
        LivingConversation(Memory(), contact, IDENTITY, Context(), contact_model=contact).respond(turn())
    assert len(contact.calls) == (0 if stage == "active_context" else 1)
