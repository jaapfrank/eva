"""A short lookup uses real indexed receipts, usage and no-replay recovery."""
from types import SimpleNamespace
import pytest
from eva.application.conversation import ConversationActionCall, ConversationModelUsage, ConversationConflict
from eva.bootstrap.public_lookup import PublicLookupActions
from tests.postgres.test_owner_research_continuation import Research, setup
from tests.postgres.test_operational_timeline import timeline_engine


class LookupResearch(Research):
    def native_research_timeout_seconds(self): return 20


@pytest.mark.parametrize("fail", [False, True])
def test_lookup_receipt_replays_without_new_provider_call_and_accounts_usage(timeline_engine, fail):
    turn, journal, _, _, _, _ = setup(timeline_engine, "short-lookup-" + str(fail))
    model = LookupResearch(fail)
    authority = SimpleNamespace(available_action_names=lambda _: ("start_background_research",))
    actions = PublicLookupActions(timeline_engine, model, authority)
    call = ConversationActionCall("lookup", "search_public_info", {
        "question": "What does the public specification say?", "public_review": "public_unlinked"})
    receipt = actions.execute(turn, call)
    assert receipt.output["status"] == ("failed" if fail else "verified")
    if not fail:
        assert receipt.output["result"]["sources"]
        assert receipt.output["result"]["content"] == "PRIMARY_SOURCE_FINDING"
    restarted = PublicLookupActions(timeline_engine, model, authority)
    assert restarted.pending(turn) == receipt
    assert restarted.execute(turn, call) == receipt
    assert model.calls == 1
    usage = journal.model_usage(turn)
    assert usage.measurement == "measured" and usage.model_attempts == 1
    assert usage.input_tokens == (39122 if fail else 500)


def test_revoked_lookup_never_dispatches(timeline_engine):
    turn, journal, _, _, _, _ = setup(timeline_engine, "short-lookup-revoked")
    model = LookupResearch()
    actions = PublicLookupActions(timeline_engine, model,
        SimpleNamespace(available_action_names=lambda _: ()))
    with pytest.raises(ConversationConflict):
        actions.execute(turn, ConversationActionCall("lookup", "search_public_info", {
            "question": "Public specification", "public_review": "public_unlinked"}))
    assert model.calls == 0
