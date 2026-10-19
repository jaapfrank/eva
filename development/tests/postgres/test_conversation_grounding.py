"""Real retained sources carry tool grounding, not new authority or actions."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.postgres.test_scheduled_failure_return import database
from tests.postgres.test_delegated_work_store import _seed_authority
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.adapters.postgres.conversation_grounding import recent_tool_context
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline
from eva.application.conversation import ConversationTurn, ConversationConflict, GroundedReply
from eva.application.memory import ConversationMemoryDocument
from eva.application.memory_audience import MemoryAudience, HouseholdConsentReference


def fixture(engine, *, household=False):
    label = "grounding-" + uuid4().hex
    scope, _, _ = _seed_authority(engine, label)
    now = datetime.now(UTC).replace(microsecond=0)
    turn = ConversationTurn.private("tenant:" + label, "installation:" + label,
        "owner", "conversation:" + label, "original", "Read the source",
        (now - timedelta(seconds=2)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    if household:
        audience = MemoryAudience.household(principal_id="device", household_id="home",
            consent_ref=HouseholdConsentReference("device", "home", "household-consent:fixture"))
        conversation = "household:home:conversation:" + label
        turn = replace(turn, principal_id="device", audience=audience,
                       conversation_id=conversation, message_id=conversation + ":request:original")
    timeline = PostgresOperationalTimeline(engine, scope)
    journal = PostgresConversationJournal(engine, scope)
    checked = []
    route = PostgresExternalConversationTools(engine,
        SimpleNamespace(action_names=("read_forecast", "read_train_times")), None,
        authorize_turn=checked.append, configuration_sha256="c" * 64)
    actions = PostgresConversationActions(engine, external_tools=(route,))
    return SimpleNamespace(engine=engine, scope=scope, now=now, turn=turn, timeline=timeline,
        journal=journal, route=route, actions=actions, checked=checked)


def save(x, *, name="read_forecast", result=None, arguments=None, turn=None,
         retention_until=None, audience=None, terminal=True, state="STATE_VERIFIED"):
    turn = turn or x.turn
    x.journal.admit(turn)
    x.journal.commit_reply(turn, GroundedReply(conversation_id=turn.conversation_id,
        message_id=turn.message_id, text="Here is the answer.", evidence_memory_ids=(),
        generated_at=turn.occurred_at, provider="fixture"))
    document = ConversationMemoryDocument(tenant_id=turn.tenant_id,
        installation_id=turn.installation_id, principal_id=turn.principal_id,
        conversation_id=turn.conversation_id,
        document_id=turn.conversation_id + ":exchange:" + turn.message_id,
        source_ref=turn.message_id, evidence_ref=turn.message_id + ":evidence",
        content="Gebruiker (directe bron): " + turn.text, occurred_at=turn.occurred_at, privacy_class="internal",
        trust="direct_user", audience=audience or turn.audience,
        project_id=turn.project_id, retention_until=retention_until)
    outbox = PostgresMemoryOutbox(x.engine, tenant_id=turn.tenant_id,
                                 installation_id=turn.installation_id)
    outbox.enqueue(document)
    thread = x.route._thread_id(turn, name, 0)
    admission = {"action_call_id": "fixture", "action_name": name,
        "action_arguments": arguments or {"source": "Forecast Home"},
        "receipt_output": {}, "source_message_id": turn.message_id,
        "conversation_id": turn.conversation_id, "principal_id": turn.principal_id,
        "request_tool_configuration_sha256": "c" * 64, "observation_output_key": "result"}
    at = datetime.fromisoformat(turn.occurred_at.replace("Z", "+00:00"))
    x.timeline.create_thread(thread_id=thread, checkpoint_id=thread + ":admitted",
        occurred_at=at, event_type="TOOL_REQUEST_ADMITTED", data=admission)
    if terminal:
        x.timeline.transition(thread_id=thread, expected_revision=1,
            checkpoint_id=thread + ":done", phase="verified", status="completed",
            event_type=state, occurred_at=at,
            data={"proof": {"evidence": {"observation": result or {
                "source": "Forecast Home", "temperature": 14, "city": None}}}})
    return document, thread, admission


def current(x, **kwargs):
    return replace(x.turn, message_id=x.turn.message_id + ":next", text="Which source?",
                   occurred_at=x.now.strftime("%Y-%m-%dT%H:%M:%SZ"), **kwargs)


def records(context):
    return json.loads(context[0][context[0].index('[{"'):])


@pytest.mark.parametrize("household", [False, True])
@pytest.mark.parametrize("name,result", [
    ("read_forecast", {"source": "Forecast Home", "temperature": 14, "city": None}),
    ("read_train_times", {"station": "Synthetic Central", "departure": "10:05"}),
])
def test_same_conversation_followup_keeps_actual_source_without_repeating_action(database, household, name, result):
    x = fixture(database, household=household)
    save(x, name=name, result=result)
    following = current(x)
    rows = records(x.actions.active_context(following))
    assert len(rows) == 1 and rows[0]["action"] == name
    assert json.loads(rows[0]["observation"]["text"]) == result
    assert rows[0]["recorded_status"] == "verified"
    assert x.checked and all(t == following for t in x.checked)
    # No provider exists: preparation, execution or observation would fail.
    assert x.route._provider is None


@pytest.mark.parametrize("scope", ["tenant_id", "installation_id", "principal_id", "conversation_id", "project_id", "audience"])
def test_other_scope_cannot_borrow_previous_source(database, scope):
    x = fixture(database)
    save(x)
    changes = {scope: "other"}
    if scope == "principal_id":
        changes["audience"] = MemoryAudience.private_person(principal_id="other")
    if scope == "audience":
        changes = {"audience": MemoryAudience.household(principal_id=x.turn.principal_id,
            household_id="other", consent_ref=HouseholdConsentReference(
                x.turn.principal_id, "other", "household-consent:other"))}
        # Same conversation is not legal for a household turn; test exact source
        # audience through an otherwise valid private source on household ingress.
        x = fixture(database, household=True)
        save(x, audience=replace(x.turn.audience, household_consent_ref=
            replace(x.turn.audience.household_consent_ref, operation_id="household-consent:old")))
        changes = {}
    assert recent_tool_context(x.actions, current(x, **changes)) == ()


def test_erased_ingress_or_expired_source_has_no_context(database):
    x = fixture(database)
    save(x)
    x.journal.erase_message_content(principal_id=x.turn.principal_id,
        message_id=x.turn.message_id, operation_sha256="e" * 64, erased_at=x.now)
    assert recent_tool_context(x.actions, current(x)) == ()
    x = fixture(database)
    save(x, retention_until=x.turn.occurred_at)
    assert recent_tool_context(x.actions, current(x)) == ()


def test_current_revocation_blocks_historical_contents(database):
    x = fixture(database)
    save(x)
    def revoked(turn):
        raise ConversationConflict("revoked")
    x.route._authorize_turn = revoked
    with pytest.raises(ConversationConflict, match="revoked"):
        recent_tool_context(x.actions, current(x))


def test_configuration_change_and_pending_action_are_not_replayed(database):
    x = fixture(database)
    save(x)
    x.route._configuration_sha256 = "d" * 64
    assert recent_tool_context(x.actions, current(x)) == ()
    x = fixture(database)
    save(x, terminal=False)
    assert recent_tool_context(x.actions, current(x)) == ()


def test_large_source_stays_bounded_and_failure_never_promotes_observation(database):
    x = fixture(database)
    save(x, result={"text": "x" * 10000})
    rows = records(recent_tool_context(x.actions, current(x)))
    assert rows[0]["observation"]["truncated"] is True
    assert len(rows[0]["observation"]["text"]) == 2000
    x = fixture(database)
    save(x, state="STATE_VERIFICATION_CONFLICT", result={"unsafe_unverified": "do not expose"})
    rows = records(recent_tool_context(x.actions, current(x)))
    assert rows[0]["recorded_status"] == "failed" and rows[0]["observation"] is None


def test_erasure_during_read_discards_context(database):
    x = fixture(database)
    save(x)
    read = x.route.historical_receipt
    def erase_after_read(*args):
        result = read(*args)
        x.journal.erase_message_content(principal_id=x.turn.principal_id,
            message_id=x.turn.message_id, operation_sha256="f" * 64, erased_at=x.now)
        return result
    x.route.historical_receipt = erase_after_read
    assert recent_tool_context(x.actions, current(x)) == ()
