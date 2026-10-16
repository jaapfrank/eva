"""Shared public speech reads canonical retained turns, never personal shadows."""
from datetime import timedelta
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4
import pytest
from tests.postgres.test_household_public_work import setup
from tests.postgres.test_scheduled_failure_return import database
from eva.bootstrap.household_continuity import HouseholdConversationContext, with_household_continuity
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.application.conversation import ConversationTurn, GroundedReply


def exchange(x, service, question, answer):
    x.clock[0]+=timedelta(seconds=1)
    turn=service.turn(str(uuid4()),question)
    journal=PostgresConversationJournal(x.server.store._engine,x.server.conversation_scope)
    journal.commit_reply(turn,GroundedReply(conversation_id=turn.conversation_id,
        message_id=turn.message_id,text=answer,evidence_memory_ids=(),generated_at=turn.occurred_at,
        provider="fixture:household"))
    return turn


def other(x):
    args=x.args|{"satellite_id":"assist_satellite.office","device_id":"b"*32}
    return x.server.session(**args,session_id=x.server.open_session(**args)["session_id"])


def test_other_device_reads_public_exchange_and_keeps_playback_unconfirmed(database):
    x=setup(database,"context-shared")
    old=exchange(x,x.service,"Welke plant hadden we besproken?","We bespraken de varen.")
    x.service.current("close")
    current=other(x);turn=current.turn(str(uuid4()),"Wat zeiden we daarnet?")
    reader=HouseholdConversationContext(x.server)
    found=reader.recent(turn)
    assert found[-2].text.endswith(old.text) and found[-1].text.endswith("We bespraken de varen.")
    assert x.service.binding["satellite_id"] in found[-2].text
    assert found[-1].playback_status=="unconfirmed"
    assert found[-1].source_turn_sha256==found[-2].source_turn_sha256
    live=(replace(found[-1],playback_status="completed"),)
    merged=with_household_continuity(SimpleNamespace(_household_conversation_context=reader),turn,live)
    assert sum(item.text==found[-1].text for item in merged)==1
    assert merged[-1].playback_status=="completed"


def test_private_channels_and_confirmed_voice_shadow_never_enter_shared_context(database):
    from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline
    x=setup(database,"context-private")
    exchange(x,x.service,"Openbaar onderwerp","Openbaar antwoord")
    timeline=PostgresOperationalTimeline(database,x.server.store.scope)
    timeline.create_thread(thread_id="voice-identity:"+x.service.binding["session_id"],
        checkpoint_id="fixture-confirmed",occurred_at=x.clock[0],event_type="VOICE_IDENTITY_CONFIRMED",
        phase="confirmed",status="completed",data={"speaker_confirmed_at":x.clock[0].strftime("%Y-%m-%dT%H:%M:%SZ")})
    x.clock[0]+=timedelta(seconds=1)
    # The current transport writes this shadow before choosing private voice.
    private_shadow=x.service.turn(str(uuid4()),"PRIVATESHADOW")
    journal=PostgresConversationJournal(database,x.server.conversation_scope)
    for channel in ("voice:private","telegram:12345","whatsapp:private"):
        private=ConversationTurn.private(private_shadow.tenant_id,private_shadow.installation_id,
            str(x.server.authority.policy.owner_principal_id),channel,channel+":message:"+str(uuid4()),
            "PRIVATECHANNEL",private_shadow.occurred_at)
        journal.admit(private)
        journal.commit_reply(private,GroundedReply(conversation_id=channel,message_id=private.message_id,
            text="PRIVATEANSWER",evidence_memory_ids=(),generated_at=private.occurred_at,provider="fixture"))
    current=other(x);turn=current.turn(str(uuid4()),"Wat zeiden we?")
    reader=HouseholdConversationContext(x.server)
    texts=[item.text for item in reader.recent(turn)]
    assert any(text.endswith("Openbaar onderwerp") for text in texts) and any(text.endswith("Openbaar antwoord") for text in texts)
    assert not any("PRIVATE" in text for text in texts)
    assert reader.recent(private)==()


def test_revoked_device_and_old_context_are_excluded(database,monkeypatch):
    x=setup(database,"context-revoked")
    exchange(x,x.service,"Afgevoerd apparaat","Niet langer zichtbaar")
    current=other(x);turn=current.turn(str(uuid4()),"Hoi")
    authority=x.server.authority
    # Same policy-generation fixture: live admission list has removed device A.
    monkeypatch.setattr(authority,"policy",authority.policy.model_copy(update={"devices":{"assist_satellite.office":"b"*32}}))
    assert HouseholdConversationContext(x.server).recent(turn)==()


def test_erased_source_is_not_reintroduced(database):
    x=setup(database,"context-erased")
    old=exchange(x,x.service,"ERASEDQUESTION","ERASEDANSWER")
    journal=PostgresConversationJournal(database,x.server.conversation_scope)
    journal.erase_message_content(principal_id=old.principal_id,message_id=old.message_id,
        operation_sha256="f"*64,erased_at=x.clock[0])
    current=other(x);turn=current.turn(str(uuid4()),"Wat zeiden we?")
    assert not any("ERASED" in item.text for item in HouseholdConversationContext(x.server).recent(turn))


def test_context_window_and_current_authority_expiry(database):
    x=setup(database,"context-window")
    exchange(x,x.service,"OUDERDAN24UUR","OUDANTWOORD")
    x.service.current("close")
    x.clock[0]+=timedelta(hours=25)
    current=other(x)
    turn=current.turn(str(uuid4()),"Hoi")
    assert HouseholdConversationContext(x.server).recent(turn)==()
    x.policies[0]=x.policies[0].model_copy(update={"state":"revoked"})
    with pytest.raises(ValueError):HouseholdConversationContext(x.server).recent(turn)
    recent=()
    assert with_household_continuity(SimpleNamespace(_household_conversation_context=HouseholdConversationContext(x.server)),turn,recent)==recent


def test_context_bounded_and_forged_audience_rejected(database):
    x=setup(database,"context-bounded")
    for index in range(8):exchange(x,x.service,"Vraag "+str(index),"Antwoord "+str(index))
    current=other(x);turn=current.turn(str(uuid4()),"Vraag nu")
    reader=HouseholdConversationContext(x.server)
    found=reader.recent(turn)
    assert len(found)==12
    assert found[0].text.endswith("Vraag 2") and found[-1].text.endswith("Antwoord 7")
    for key,value in (("tenant_id","other-tenant"),("installation_id","other-install"),("principal_id","household-device:"+"c"*32)):
        with pytest.raises((ValueError,RuntimeError)):
            reader.recent(replace(turn,**{key:value}))


def test_current_session_playback_is_not_duplicated_by_durable_model_output(database):
    x=setup(database,"context-current")
    old=exchange(x,x.service,"Vraag in huidige sessie","Volledig gegenereerd antwoord")
    turn=x.service.turn(str(uuid4()),"Volgende vraag")
    from eva.application.conversation import RecentConversationMessage
    live=(RecentConversationMessage("Eva","Alleen het afgespeelde begin",old.occurred_at,playback_status="interrupted"),)
    reader=HouseholdConversationContext(x.server)
    assert reader.recent(turn)==()
    assert with_household_continuity(SimpleNamespace(_household_conversation_context=reader),turn,live)==live


def test_erased_recent_records_do_not_hide_older_valid_context(database):
    x=setup(database,"context-erased-window")
    exchange(x,x.service,"Nog geldige vraag","Nog geldig antwoord")
    journal=PostgresConversationJournal(database,x.server.conversation_scope)
    for index in range(7):
        old=exchange(x,x.service,"Verwijderde proef "+str(index),"Verwijderd antwoord")
        journal.erase_message_content(principal_id=old.principal_id,message_id=old.message_id,
            operation_sha256="e"*64,erased_at=x.clock[0])
    current=other(x);turn=current.turn(str(uuid4()),"Wat weet je nog?")
    found=HouseholdConversationContext(x.server).recent(turn)
    assert any("Nog geldige vraag" in item.text for item in found)
    assert not any("Verwijderde proef" in item.text for item in found)
