"""Synthetic PostgreSQL proof of atomic contribution registration and delivery."""
from datetime import timedelta
import hashlib
import pytest
from eva.adapters.postgres.completed_contribution import CompletedContributionActions
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline
from eva.application.conversation import ConversationTurn, ConversationActionCall, ConversationConflict
from tests.postgres.test_owner_initiatives import costless_native, NOW


def test_completed_contribution_atomic_delivery_replay_and_authority(costless_native, monkeypatch):
    auth, old, interests, owner, sensory, path, policy = costless_native
    owner.stop(old.intent_id, now=NOW)
    allowed = [True]
    stopped = [False]
    work_row_locked = [None]
    import sqlalchemy as sa
    def transaction_finished(conn):
        if work_row_locked[0] is conn:
            work_row_locked[0] = None
    sa.event.listen(owner._engine, "commit", transaction_finished)
    sa.event.listen(owner._engine, "rollback", transaction_finished)
    def authorize(turn):
        assert work_row_locked[0] is None, "external authorization reopened the locked work row"
    content = ["Synthetic useful contribution, independently read."]
    dispatched = []
    now = [NOW]
    def resolve(turn):
        return str(owner._owner) if allowed[0] and turn.principal_id == str(owner._owner) else None
    def artifact(turn, work, path):
        return {"content": content[0], "content_sha256": hashlib.sha256(content[0].encode()).hexdigest(),
                "source_provenance": {"work_id": work, "artifact_path": path, "read_receipt_sha256": "a" * 64}}
    def locked(conn):
        work_row_locked[0] = conn
        # Actual work guards use the dashboard UUID scope, while initiatives
        # live in the external timeline scope. Registration must restore it.
        PostgresOperationalTimeline(owner._engine, owner._authority)._scope(conn)
        if stopped[0]:
            raise ConversationConflict("current work stopped")
    actions = CompletedContributionActions(lambda: auth, resolve, artifact, authorize,
        lambda turn: dispatched.append(True), lambda: now[0], authorize_locked=locked,
        execution_namespace="synthetic-current-work", spec_binding={"spec_sha256": "b" * 64})
    turn = ConversationTurn.private(owner._tenant_id, owner._installation_id, str(owner._owner),
        "synthetic-contribution", "synthetic-contribution-message", "Complete the existing contribution.",
        NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
    call = ConversationActionCall("synthetic-register", "register_own_contribution", {
        "title": "Synthetic completed contribution", "motivation": "Synthetic context",
        "source_work_id": "work:synthetic-closed", "artifact_path": "/proof-state/synthetic.md"})
    from eva.adapters.postgres import initiative_delivery, initiative_execution
    from eva.adapters.postgres.initiative_delivery import InitiativeCompletionDelivery
    class Time(initiative_execution.datetime):
        @classmethod
        def now(cls, tz=None): return now[0]
    monkeypatch.setattr(initiative_delivery, "datetime", Time)
    sent = []
    class Delivery:
        def deliver(self, claim):
            sent.append(claim)
            return "synthetic-transport-receipt"
    delivery = InitiativeCompletionDelivery(owner, Delivery(), lambda: auth.policy())
    assert delivery.run_once() == "enabled"
    now[0] += timedelta(seconds=1)
    # A stop after artifact read rolls back both events.
    stopped[0] = True
    with pytest.raises(ConversationConflict, match="stopped"):
        actions.execute(turn, call)
    timeline = PostgresOperationalTimeline(owner._engine, owner._scope)
    thread = "intent:" + actions._id(turn, 0)
    assert timeline.events(thread) == ()
    stopped[0] = False
    from eva.bootstrap.delegated_digital_runner import BackgroundConnectorActions
    consumer = BackgroundConnectorActions((actions,), actions.action_names, lambda turn: None, 12)
    receipt = consumer.execute(turn, call)
    assert consumer.execute_step(turn, call, step_index=0) == receipt
    assert consumer.pending_step(turn, step_index=0) == receipt and len(dispatched) == 1
    assert actions.supports_indexed_steps is True
    events = timeline.events(thread)
    assert [x.event_type for x in events] == ["PERSONAL_INTENT_ADOPTED", "PERSONAL_INITIATIVE_COMPLETED"]
    assert events[0].payload["data"]["execution_mode"] == "completed_contribution"
    assert interests.claim_next(tenant_id=owner._tenant_id, installation_id=owner._installation_id, now=now[0]) is None
    intent = receipt.output["operation_result"]["intent_id"]
    assert owner.artifact(intent, now=now[0])["content"] == content[0]
    now[0] += timedelta(seconds=1)
    assert delivery.run_once() == "delivered"
    assert delivery.run_once() == "idle" and len(sent) == 1
    owner.feedback(intent, "Synthetic feedback only", now=now[0], request_key="synthetic-feedback")
    assert any(x.get("owner_feedback") == "Synthetic feedback only" for x in sensory.recent_initiatives())
    allowed[0] = False
    assert actions.available_for(turn) is False
    assert actions.pending(turn) is None
    assert actions.active_context(turn) == ()
    with pytest.raises(ConversationConflict): actions.execute(turn, call)
    allowed[0] = True
    content[0] = "Different artifact bytes"
    with pytest.raises(ConversationConflict, match="replay data differs"):
        actions.execute(turn, call)
    assert len(timeline.events(thread)) == 3  # Feedback is retained, never overwritten.
    wrong = ConversationTurn.private(turn.tenant_id, turn.installation_id, "other-person",
        "other-conversation", "other-message", "Synthetic request", turn.occurred_at)
    assert actions.available_for(wrong) is False
    assert actions.pending(wrong) is None
    with pytest.raises(ConversationConflict): actions.execute(wrong, call)
