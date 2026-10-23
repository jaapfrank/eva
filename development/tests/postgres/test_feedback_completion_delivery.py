"""Private initiative completion returns to attention, not an unconditional send."""
from datetime import datetime, timedelta
from eva.application.conversation import GroundedReply
from eva.application.free_will import PersonalInitiativeProposal, FreeWillDecision
from tests.postgres.test_subscription_initiative_reviews import review_setup
from tests.postgres.test_owner_initiatives import costless_native, NOW
from tests.postgres.test_next_attention import finish, worker
from tests.postgres.test_initiative_source_feedback import SOURCE
import pytest
import json

@pytest.mark.parametrize("disposition", ["silence", "respond"])
def test_private_completion_is_available_to_review_without_direct_notification(costless_native, monkeypatch, disposition):
    from eva.adapters.postgres import initiative_delivery as module
    from tests.postgres.test_private_initiative_contact import _setup
    auth, old, control, path, policy, store = _setup(costless_native)
    interests = costless_native[2]
    at = [NOW]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return at[0]
    monkeypatch.setattr(module, "datetime", Clock)
    sent = []
    class Transport:
        def deliver(self, message):
            sent.append(message)
            return "test-receipt"
    delivery = module.InitiativeCompletionDelivery(control, Transport(), auth.policy)
    assert delivery.run_once() == "enabled"
    finish(store, initiative=PersonalInitiativeProposal("Explore available tools", "Actual gap", "Read capabilities only"))
    claim = interests.claim_next(tenant_id=old.tenant_id, installation_id=old.installation_id, now=NOW)
    guard, turn = auth.bind(claim)
    at[0] += timedelta(seconds=1)
    interests.complete_native(claim, GroundedReply(turn.conversation_id, turn.message_id,
        "No useful capability found; keep the result quietly.", (), turn.occurred_at, "synthetic"), now=at[0])
    assert delivery.run_once() == "idle"
    assert sent == []
    assert control.artifact(claim.intent_id, now=at[0])["content"].startswith("No useful")
    # This synthetic outcome separately exercises both existing review choices;
    # the installed same-work handoff is verified in the live evidence.
    assert store.schedule_context_review(now=at[0], source_outcomes=SOURCE) == "scheduled"
    seen = []
    class Model:
        def evaluate_sensory_event(self, event, *args, **kwargs):
            seen.append(event)
            return FreeWillDecision(disposition, "Consider actual usefulness", 1 if disposition == "respond" else 0,
                "synthetic", expression="A useful finding" if disposition == "respond" else None)
    # Existing private-contact permission is independently exercised by its suite.
    assert worker(store, auth, Model()).run_once(now=at[0]) == disposition
    assert len(seen) == 1 and "native_completion_source" in seen[0].payload
    assert sent == []
    decision = store._timeline.events("sensory:"+seen[0].source_event_id)[-1].payload["data"]
    assert decision["disposition"] == disposition
