"""Validate disclosure, failure accounting and current authority without network."""
from types import SimpleNamespace
import pytest
from eva.application.conversation import ConversationActionInputRejected, ConversationConflict, ConversationModelUsage
from eva.bootstrap.public_lookup import public_arguments, JournaledLookup, PublicLookupActions
from eva.adapters.model.public_lookup import PublicLookupModel
from eva.adapters.model.contracts import ConversationModelError
from tests.application.test_contact_model import turn


@pytest.mark.parametrize("question", ["File A1 Amsterdam Amersfoort", "Wanneer gras voeden in Nederland?", "Temperatuur Amsterdam 15 oktober 2026"])
def test_unrelated_topics_use_same_public_contract(question):
    assert public_arguments({"question": question, "public_review": "public_unlinked"})["question"] == question


@pytest.mark.parametrize("raw", [
    {"question": "Hallo", "public_review": "uncertain"},
    {"question": "Mijn huis aan de Dorpsstraat 12", "public_review": "public_unlinked"},
    {"question": "a" * 501, "public_review": "public_unlinked"},
    {"question": "https://localhost/a", "public_review": "public_unlinked"},
    {"question": "mail person@example.com", "public_review": "public_unlinked"},
    {"question": "Hallo", "public_review": "public_unlinked", "memory": "private"},
])
def test_bad_projection_rejected_before_provider(raw):
    with pytest.raises(ConversationActionInputRejected):
        public_arguments(raw)


def test_envelope_contains_only_public_question(tmp_path):
    model = PublicLookupModel(auth_file=tmp_path/"unused", model="gpt-6-sol", reasoning_effort="none",
        research_timeout_seconds=20, research_maximum_tool_calls=2)
    body = model.research_request_payload(question="Public question", options=("Never forward",))
    assert body["input"][0]["content"] == "Public question"
    assert body["model"] == "gpt-6-sol" and body["reasoning"]["effort"] == "none"
    assert body["tools"] == [{"type": "web_search", "search_context_size": "low"}]
    assert "max_tool_calls" not in body


def test_eager_lookup_admission_rejects_altered_descriptor(tmp_path):
    from eva.bootstrap.public_lookup import PUBLIC_LOOKUP
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel, _descriptor_tool
    from tests.model.test_pydantic_conversation_model import _runtime_budget
    model = PydanticConversationModel(auth_file=tmp_path/"unused",
        resource_budget=_runtime_budget(), conversation_tools=(PUBLIC_LOOKUP,))
    body = model._phase_admission_body(prompt="Question", instructions="Answer",
        tools=(_descriptor_tool(PUBLIC_LOOKUP),))
    assert body["tools"][0]["defer_loading"] is False
    model._admit_request(body, autonomous=False)
    import copy
    body = copy.deepcopy(body)
    body["tools"][0]["parameters"]["properties"]["unregistered"] = {"type": "string"}
    with pytest.raises(ConversationModelError, match="catalog is invalid"):
        model._admit_request(body, autonomous=False)


class Journal:
    def __init__(self): self.events = []
    def begin_model_request(self, t): self.events.append("begin"); return "request"
    def complete_model_request(self, t, request, usage, **kwargs): self.events.append((usage, kwargs))


@pytest.mark.parametrize("failure", [None, "measured", "unknown"])
def test_lookup_records_one_request_and_retains_failure_usage(failure):
    usage = ConversationModelUsage.measured(input_tokens=50, output_tokens=10, tool_calls=1)
    journal, checks = Journal(), []
    def run(**kwargs):
        assert journal.events == ["begin"]
        if failure:
            raise ConversationModelError("DO_NOT_LOG_PRIVATE", **({"model_usage": usage} if failure == "measured" else {}))
        return SimpleNamespace(usage=usage)
    research = JournaledLookup(SimpleNamespace(research_current_options=run), turn(), journal, checks.append)
    if failure:
        with pytest.raises(ConversationModelError): research.research_current_options()
    else:
        assert research.research_current_options().usage == usage
    assert len(journal.events) == 2
    assert journal.events[1][0].measurement == ("unmeasured" if failure == "unknown" else "measured")
    assert len(checks) == (1 if failure else 2)


@pytest.mark.parametrize("scoped", [False, True])
def test_authority_checked_again_at_reply(scoped):
    allowed = [True]
    authority = (SimpleNamespace(available_action_names=lambda _: ("start_background_research",) if allowed[0] else ())
        if scoped else SimpleNamespace(action_names=("start_background_research",), available_for=lambda _: allowed[0]))
    actions = PublicLookupActions(None, None, authority)
    assert actions.available_for(turn())
    guard = actions.reply_guard(turn(), [SimpleNamespace(call=SimpleNamespace(name="search_public_info"))])
    allowed[0] = False
    assert not actions.available_for(turn())
    with pytest.raises(ConversationConflict): guard("No stale result")


@pytest.mark.parametrize('variant', ['missing_optin', 'false_optin', 'failed', 'no_sources', 'altered_content', 'no_proof', 'too_long', 'multiple'])
def test_direct_voice_result_requires_explicit_complete_verified_evidence(variant):
    from eva.application.conversation import ConversationActionCall, ConversationActionReceipt
    import hashlib
    text = 'Sourced fact.'
    args = {'question':'Public question', 'public_review':'public_unlinked', 'answer_directly': True}
    result = {'content':text, 'content_sha256':hashlib.sha256(text.encode()).hexdigest(),
              'sources':[{'url':'https://example.org'}], 'verification_scope':'research_artifact_only'}
    status = 'verified'
    if variant == 'missing_optin': args.pop('answer_directly')
    if variant == 'false_optin': args['answer_directly'] = False
    if variant == 'failed': status = 'failed'
    if variant == 'no_sources': result['sources'] = []
    if variant == 'altered_content': result['content'] = 'Altered content'
    if variant == 'no_proof': result.pop('verification_scope')
    if variant == 'too_long': result['content'] = 'word ' * 121
    receipt = ConversationActionReceipt(ConversationActionCall('one','search_public_info',args), {'status':status,'result':result})
    provider = PublicLookupActions(None, None, SimpleNamespace(available_action_names=lambda _: ('start_background_research',)))
    assert provider.direct_voice_reply(turn(), (receipt,)*(2 if variant=='multiple' else 1)) is None


def test_direct_answer_option_does_not_leak_into_public_query():
    args = public_arguments({'question':'Public question', 'public_review':'public_unlinked','answer_directly':True})
    assert 'answer_directly' not in args
    with pytest.raises(ConversationActionInputRejected):
        public_arguments({'question':'Public question', 'public_review':'public_unlinked','answer_directly':'yes'})
