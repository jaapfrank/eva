"""Completion metadata cannot bypass scope, state proof or transport arguments."""
import asyncio,hashlib
from types import SimpleNamespace
import pytest
from eva.application.conversation import ConversationActionCall,ConversationActionReceipt
from tests.connectors.test_home_assistant_room_lights import fixture


def setup(monkeypatch):
    adapter,_,_=fixture(monkeypatch,grouped=True)
    planner=adapter.conversation_effect_planner(allowlist=frozenset({("light.woonkamer","turn_on")}),
        principal_sha256_allowlist=frozenset({hashlib.sha256(b"owner").hexdigest()}))
    call=ConversationActionCall("call","set_home_assistant_light",{"state":"on","target":"Woonkamer","finish_without_reply":True})
    turn=SimpleNamespace(principal_id="owner")
    return planner,call,turn


def test_completion_choice_is_bound_to_action_but_not_sent_as_service_data(monkeypatch):
    planner,call,turn=setup(monkeypatch)
    prepared=asyncio.run(planner.prepare(turn,call))
    assert "finish_without_reply" not in prepared.arguments and prepared.arguments["service_data"]=={}
    output={"status":"verified","entity_id":"light.woonkamer","requested_state":"on",
        "proof":{"expected":{"entity_id":"light.woonkamer","state":"on"},"observed":{"entity_id":"light.woonkamer","state":"on"}}}
    assert planner.silent_completion_allowed(turn,ConversationActionReceipt(call,output))


@pytest.mark.parametrize("change",["partial","state","target","principal","legacy","unproved"])
def test_no_silence_for_partial_mismatched_unproved_or_foreign_receipt(monkeypatch,change):
    planner,call,turn=setup(monkeypatch)
    output={"status":"verified","entity_id":"light.woonkamer","requested_state":"on",
        "proof":{"expected":{"entity_id":"light.woonkamer","state":"on"},"observed":{"entity_id":"light.woonkamer","state":"on"}}}
    if change=="partial":output["status"]="failed"
    if change=="state":output["proof"]["observed"]["state"]="off"
    if change=="target":output["entity_id"]="light.foreign"
    if change=="principal":turn=SimpleNamespace(principal_id="foreign")
    if change=="legacy":call=ConversationActionCall("call",call.name,{"state":"on","target":"Woonkamer"})
    if change=="unproved":output["proof"]={}
    assert not planner.silent_completion_allowed(turn,ConversationActionReceipt(call,output))


def test_port_rechecks_exact_saved_receipt_and_current_authority_before_silence():
    from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
    from eva.application.conversation import ConversationConflict
    call=ConversationActionCall('call','bounded',{'finish_without_reply':True})
    durable=ConversationActionReceipt(call,{'status':'verified'})
    port=object.__new__(PostgresExternalConversationTools);port._action_names=('bounded',);port._configuration_sha256=None
    checked=[]
    port._ensure_running=lambda turn:checked.append(turn)
    port._planner=SimpleNamespace(silent_completion_allowed=lambda turn,receipt:True)
    port._timeline=lambda turn:SimpleNamespace(events=lambda _: [SimpleNamespace(event_type='TOOL_REQUEST_ADMITTED',payload={'data':{}}),SimpleNamespace(event_type='STATE_VERIFIED')])
    port._thread_id=lambda *a:'exact'
    port._receipt=lambda *a,**k:durable
    assert port.silent_completion_allowed('current',[durable]) and checked==['current','current']
    altered=ConversationActionReceipt(ConversationActionCall('other','bounded',call.arguments),durable.output)
    with pytest.raises(ConversationConflict,match='receipt changed'):port.silent_completion_allowed('current',[altered])
    def stop(turn):raise ConversationConflict('stopped')
    port._ensure_running=stop
    with pytest.raises(ConversationConflict,match='stopped'):port.silent_completion_allowed('current',[durable])
