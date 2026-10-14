"""Real PostgreSQL admission across ordinary chat and passive initiative sources."""
from dataclasses import replace
from datetime import timedelta
import pytest
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.initiative_adoption import PostgresOwnerConversationInitiativeAdopter
from eva.application.conversation import ConversationTurn, ConversationActionCall, ConversationConflict
from eva.application.free_will import FreeWillDecision, PersonalInitiativeProposal
from tests.postgres.test_owner_initiatives import costless_native, NOW
from tests.postgres.test_sensory_admission import _event


def test_chat_adoption_replays_native_claim_and_shares_sensor_guards(costless_native, monkeypatch):
    from eva.adapters.postgres import initiative_execution, initiative_adoption
    monkeypatch.setattr(initiative_adoption, "datetime", initiative_execution.datetime)
    auth, old, interests, owner, sensory, path, policy = costless_native
    owner.stop(old.intent_id, now=NOW)
    principal = str(owner._owner)
    alias = "telegram:54321"
    permitted = [True]
    loads = []
    def loader():
        loads.append(True)
        return auth
    def resolve(turn):
        return principal if permitted[0] and turn.principal_id in (principal, alias) else None
    actions = PostgresConversationActions(owner._engine,
        initiative_adopter=PostgresOwnerConversationInitiativeAdopter(loader, resolve))
    turn = ConversationTurn.private(owner._tenant_id, owner._installation_id, alias,
        alias, "chat-fixture-1", "Choose your own useful contribution.", NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
    journal = PostgresConversationJournal(owner._engine, owner._scope)
    journal.admit(turn)
    call = ConversationActionCall("choice-1", "adopt_personal_interest",
        {"title":"A distinct private idea", "motivation":"Fixture context", "next_exploration":"Prepare it"})
    # A revocation during the actual PostgreSQL owner-lock acquisition must
    # abort before any adoption row, not merely fail a later replay.
    import sqlalchemy as sa
    def revoke_at_lock(conn, cursor, statement, parameters, context, executemany):
        if "pg_advisory_xact_lock" in statement:
            permitted[0] = False
    sa.event.listen(owner._engine, "after_cursor_execute", revoke_at_lock)
    try:
        with pytest.raises(ConversationConflict, match="owner authority"):
            actions.execute(turn, call)
    finally:
        sa.event.remove(owner._engine, "after_cursor_execute", revoke_at_lock)
    assert actions._timeline(turn).events("intent:" + actions._native_intent_id(turn)) == ()
    permitted[0] = True
    first = actions.execute(turn, call)
    replay = actions.execute(turn, call)
    assert first.output["intent_id"] == replay.output["intent_id"]
    events = actions._timeline(turn).events("intent:" + first.output["intent_id"])
    assert len(events) == 1
    data = events[0].payload["data"]
    assert data["principal_id"] == principal and data["source_principal_id"] == alias
    assert data["origin"] == "eva_initiative" and data["execution_mode"] == "autonomous_task"
    assert data["source_message_id"] == turn.message_id and data["action_call_id"] == call.call_id
    assert data["adoption_source"] == "ordinary_conversation"
    claim = interests.claim_next(tenant_id=owner._tenant_id, installation_id=owner._installation_id, now=NOW)
    assert claim.intent_id == first.output["intent_id"] and claim.execution_mode == "autonomous_task"
    guard, native_turn = auth.bind(claim)
    journal.admit(native_turn)
    assert native_turn.principal_id == principal
    assert any(x["intent_id"] == claim.intent_id for x in owner.page(now=NOW)["cards"])
    # The genuine sensory callsite sees the chat's same owner lock/pending set.
    sensory.retain(replace(_event("pending-from-chat"), occurred_at=NOW))
    pending = sensory.claim_next(now=NOW)
    decision = FreeWillDecision("silence", "Fixture", 0, "fixture",
        initiative=PersonalInitiativeProposal("Different sensor idea", "Fixture", "Prepare it"))
    sensory.complete(pending, decision, now=NOW)
    assert sensory._timeline.events(pending.thread_id)[-1].payload["data"]["initiative"]["reason"] == "active_initiative_exists"
    # Same native completion is visible to delivery, artifact readback and feedback.
    from eva.adapters.postgres import initiative_delivery
    from eva.adapters.postgres.initiative_delivery import InitiativeCompletionDelivery
    from eva.application.conversation import GroundedReply
    sent = []
    class Delivery:
        def deliver(self, value):
            sent.append(value)
            return "synthetic-private-transport-receipt"
    delivery_now = [NOW]
    class DeliveryTime(initiative_execution.datetime):
        @classmethod
        def now(cls, tz=None): return delivery_now[0]
    monkeypatch.setattr(initiative_delivery, "datetime", DeliveryTime)
    delivery = InitiativeCompletionDelivery(owner, Delivery(), lambda: auth.policy())
    assert delivery.run_once() == "enabled"
    interests.complete_native(claim, GroundedReply(native_turn.conversation_id,
        native_turn.message_id, "Synthetic completed contribution.", (), native_turn.occurred_at,
        "fixture"), now=NOW + timedelta(seconds=1))
    assert owner.artifact(claim.intent_id, now=NOW)["content"] == "Synthetic completed contribution."
    delivery_now[0] = NOW + timedelta(seconds=2)
    assert delivery.run_once() == "delivered"
    assert delivery.run_once() == "idle" and len(sent) == 1
    owner.feedback(claim.intent_id, "Synthetic fixture feedback only", now=NOW + timedelta(seconds=3),
        request_key="fixture-feedback")
    history = sensory.recent_initiatives()
    assert any(x.get("owner_feedback") == "Synthetic fixture feedback only" for x in history)
    owner.stop(claim.intent_id, now=NOW + timedelta(seconds=4))
    turn2 = replace(turn, message_id="chat-fixture-2")
    journal.admit(turn2)
    with pytest.raises(ConversationConflict, match="initiative_already_attempted"):
        actions.execute(turn2, call)
    legacy_turn = replace(turn, message_id="legacy-fixture")
    journal.admit(legacy_turn)
    legacy_actions = PostgresConversationActions(owner._engine)
    legacy = legacy_actions.execute(legacy_turn, call)
    assert len(legacy.output["intent_id"].removeprefix("personal-interest:")) == 32
    legacy_events = legacy_actions._timeline(legacy_turn).events("intent:" + legacy.output["intent_id"])
    assert actions.pending(legacy_turn).output["intent_id"] == legacy.output["intent_id"]
    assert actions.execute(legacy_turn, call).output["intent_id"] == legacy.output["intent_id"]
    assert legacy_actions._timeline(legacy_turn).events("intent:" + legacy.output["intent_id"]) == legacy_events
    permitted[0] = False
    with pytest.raises(ConversationConflict, match="owner authority"):
        actions.pending(turn)
    with pytest.raises(ConversationConflict, match="owner authority"):
        actions.execute(turn2, replace(call, arguments={**call.arguments,"title":"Another idea"}))
    assert loads
