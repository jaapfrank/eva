"""Real ledger, normal HA preparation/dispatch/proof and source-bound reuse."""
import hashlib
from dataclasses import replace
from datetime import UTC,datetime,timedelta
from uuid import uuid4
from types import SimpleNamespace
import pytest
import sqlalchemy as sa
from eva.adapters.connectors import home_assistant as module
from eva.adapters.connectors.home_assistant import HomeAssistantConfig,HomeAssistantToolAdapter
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.operational_timeline import external_timeline_scope,operational_events
from eva.application.conversation import (ConversationActionCall,ConversationConflict,ConversationStopped,ConversationTurn,
    LivingConversation,silent_completion_scope)
from eva.application.memory_audience import MemoryAudience
from eva.providers.home_assistant_command_cache import HomeAssistantCommandCache
from tests.application.test_contact_model import Model,IDENTITY
from tests.application.test_conversation import _Memory
from tests.postgres.test_scheduled_failure_return import database

class Devices:
    binding="a"*64
    def snapshot(self):return {"binding_sha256":self.binding}
class Profile:
    def snapshot(self):return {"supported":True,"ha_version":"2026.9.2"}

@pytest.fixture
def rig(database,monkeypatch):
    tenant,installation=str(uuid4()),str(uuid4());now=datetime.now(UTC).replace(microsecond=0)
    states=[{"entity_id":"light.yard","state":"off","attributes":{"friendly_name":"Tuin"}}]
    adapter=HomeAssistantToolAdapter(HomeAssistantConfig("http://ha.test","synthetic"))
    monkeypatch.setattr(adapter,"_light_catalog_sync",lambda:states)
    device=Devices();profile=Profile();cache=HomeAssistantCommandCache(database,
        configuration_sha256="a"*64,device_context=device,capability_profile=profile)
    adapter._command_cache=cache
    planner=adapter.conversation_effect_planner(allowlist=frozenset({("light.yard","turn_on"),("light.yard","turn_off")}),
        principal_sha256_allowlist=frozenset({hashlib.sha256(b"owner").hexdigest()}))
    effects=[];reads=[];revoked=[False];fail=[False]
    def send(**kwargs):
        effects.append(kwargs["entity_id"])
        if fail[0]:raise module.HomeAssistantMcpError("synthetic uncertain response")
        states[0]["state"]="on" if kwargs["service"]=="turn_on" else "off"
        return {}
    monkeypatch.setattr(module,"call_assist_effect",send)
    async def read(method,path,*a,**k):
        reads.append(path);return dict(states[0])
    monkeypatch.setattr(adapter,"_request_json",read)
    def authorize(t):
        if revoked[0]:raise ConversationConflict("grant revoked")
    port=PostgresExternalConversationTools(database,planner,adapter,authorize_turn=authorize,configuration_sha256="b"*64)
    class Actions:
        action_names=planner.action_names
        def pending(self,t):return port.pending(t)
        def active_context(self,t):return ()
        def available_action_names(self,t):return self.action_names
        def continuation_action_names(self,t):return self.action_names
        def execute(self,t,call):return port.execute(t,call)
        def reuse_verified_action(self,t):return port.reuse_verified_action(t)
        def silent_completion_allowed(self,t,r):return port.silent_completion_allowed(t,r)
        def contact_reply_allowed(self,t,r):return True
    model=Model(lambda t,k:ConversationActionCall("qualified-"+t.message_id,"set_home_assistant_light",
        {"target":"light.yard","state":"on" if t.text.endswith("aan") else "off",
         "finish_without_reply":True,"reusable_command":True}) if not k["completed_action"] else "Dat lukte niet.")
    runtime=LivingConversation(_Memory(),Model(lambda *a:pytest.fail("work model")),IDENTITY,Actions(),contact_model=model)
    journal=PostgresConversationJournal(database,external_timeline_scope(tenant,installation,"scope"))
    turns=[]
    def turn(text="Zet de lamp light.yard uit",**changes):
        t=ConversationTurn.private(tenant,installation,"owner","conversation",str(uuid4()),text,
            (now+timedelta(seconds=len(turns))).strftime("%Y-%m-%dT%H:%M:%SZ"))
        if "principal_id" in changes:changes["audience"]=MemoryAudience.private_person(principal_id=changes["principal_id"])
        t=replace(t,**changes);turns.append(t);return t
    def respond(t):
        journal.admit(t)
        old=journal.committed_reply(t)
        if old:return old
        with silent_completion_scope():reply=runtime.respond(t,model_journal=journal)
        return journal.commit_reply(t,reply)
    return SimpleNamespace(**locals())


def test_repeat_has_zero_models_new_operation_and_fresh_readback(rig):
    first=rig.turn();a=rig.respond(first);second=rig.turn();b=rig.respond(second)
    assert a.response_mode==b.response_mode=="silent"
    assert len(rig.model.calls)==1 and b.provider=="eva:verified-reuse"
    assert b.model_usage.model_attempts==0 and len(rig.effects)==len(rig.reads)==2
    assert rig.respond(second)==b and len(rig.effects)==2
    with rig.database.connect() as c:
        rows=c.execute(sa.select(operational_events.c.thread_id).where(
            operational_events.c.tenant_id==rig.journal._timeline._scope_value.tenant_id,
            operational_events.c.event_type=="TOOL_REQUEST_ADMITTED")).scalars().all()
    assert len(rows)==2
    assert len(set(rows))==2


@pytest.mark.parametrize("change",["binding","target","question","backdated","configuration","erasure","principal"])
def test_changed_context_source_or_authority_is_a_miss_before_effect(rig,change):
    source=rig.turn();rig.respond(source)
    if change=="binding":rig.device.binding="c"*64
    elif change=="target":rig.states[0]["entity_id"]="light.other"
    elif change=="question":rig.journal.admit(rig.turn("Nee, ik bedoelde die andere lamp"))
    elif change=="backdated":rig.journal.admit(rig.turn("Eerdere correctie",occurred_at=(rig.now-timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")))
    elif change=="configuration":rig.port._configuration_sha256="d"*64
    elif change=="erasure":
        rig.journal.erase_message_content(principal_id=source.principal_id,message_id=source.message_id,operation_sha256="e"*64,erased_at=rig.now)
    current=rig.turn(**({"principal_id":"someone-else"} if change=="principal" else {}))
    rig.journal.admit(current)
    assert rig.port.reuse_verified_action(current) is None
    assert len(rig.effects)==1 and len(rig.reads)==1


def test_revoked_authority_propagates_and_cannot_dispatch(rig):
    rig.respond(rig.turn());rig.revoked[0]=True;current=rig.turn();rig.journal.admit(current)
    with pytest.raises(ConversationConflict,match="grant revoked"):rig.port.reuse_verified_action(current)
    assert len(rig.effects)==1


def test_unrelated_cache_miss_does_not_require_a_home_assistant_grant(rig):
    rig.respond(rig.turn());rig.revoked[0]=True
    current=rig.turn("Hoe gaat het met je?");rig.journal.admit(current)
    assert rig.port.reuse_verified_action(current) is None
    assert len(rig.effects)==1


def test_qualified_alternating_commands_and_rebuilt_index_can_reuse(rig):
    rig.respond(rig.turn());rig.respond(rig.turn("Zet de lamp light.yard aan"))
    fresh=HomeAssistantCommandCache(rig.database,configuration_sha256="a"*64,device_context=rig.device,capability_profile=rig.profile)
    rig.planner._command_cache=fresh
    reply=rig.respond(rig.turn())
    assert reply.provider=="eva:verified-reuse" and len(rig.model.calls)==2 and len(rig.effects)==3


def test_uncertain_reused_effect_is_not_silent_or_sent_twice(rig):
    rig.respond(rig.turn());rig.fail[0]=True
    reply=rig.respond(rig.turn())
    assert reply.response_mode=="reply" and len(rig.effects)==2 and len(rig.model.calls)==2

@pytest.mark.parametrize("change",["context","erasure","grant","stop"])
def test_changes_during_prepare_cannot_dispatch_cached_effect(rig,monkeypatch,change):
    source=rig.turn();rig.respond(source);current=rig.turn();rig.journal.admit(current)
    original=rig.planner.prepare
    async def prepare(t,call):
        result=await original(t,call)
        if change=="context":rig.device.binding="f"*64
        elif change=="erasure":rig.journal.erase_message_content(principal_id=source.principal_id,
            message_id=source.message_id,operation_sha256="f"*64,erased_at=rig.now)
        elif change=="grant":rig.revoked[0]=True
        else:rig.journal.request_turn_stop(current)
        return result
    monkeypatch.setattr(rig.planner,"prepare",prepare)
    if change in {"grant","stop"}:
        with pytest.raises(ConversationStopped if change=="stop" else ConversationConflict):rig.port.reuse_verified_action(current)
    else:assert rig.port.reuse_verified_action(current) is None
    assert len(rig.effects)==len(rig.reads)==1
