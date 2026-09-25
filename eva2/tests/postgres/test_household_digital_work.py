"""Household HA work uses the existing digital lifecycle and result return."""
import asyncio
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from eva.adapters.connectors.home_assistant_api import HomeAssistantApiTool
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.delegated_work import DelegatedWorkNotFound
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.scoped_delegated_work_actions import ScopedDelegatedWorkActions
from eva.adapters.projection.hindsight import HindsightMemoryEngine
from eva.application.conversation import ConversationActionCall, ConversationModelResult, ConversationModelUsage, GroundedReply, IdentityProfile
from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile
from eva.bootstrap.delegated_digital_runner import DelegatedDigitalRunner
from eva.bootstrap.household_digital_work import with_household_digital, HouseholdDigitalSourceLoader
from eva.bootstrap.household_work import HouseholdWorkActions
from tests.postgres.test_household_public_work import setup, DEVICE
from tests.postgres.test_scheduled_failure_return import database


def digital_setup(database, label):
    x = setup(database, label)
    root = Path(__file__).resolve().parents[2] / "src/eva"
    name = "bootstrap/delegated_digital_runner.py"
    profile = DelegatedDigitalProfile("model:controlled", "d" * 64, "e" * 64,
        ("home_assistant_request", "save_work_progress"),
        ((name, hashlib.sha256((root / name).read_bytes()).hexdigest()),))
    settings = SimpleNamespace(api_enabled=True,
        effect_principal_sha256_allowlist=(hashlib.sha256(("household-device:"+DEVICE).encode()).hexdigest(),))
    authority = with_household_digital(x.server.authority, profile, lambda: settings)
    x.server.authority = authority
    x.server.store._household_authorizer = authority.validate_spec
    x.service = x.server.session(**x.args, session_id=x.service.binding["session_id"])
    x.profile = profile
    return x


def test_household_digital_stop_resume_preserves_effects_and_returns_to_speaker(database, monkeypatch):
    x = digital_setup(database, "hh-digital-flow")
    turn = x.service.turn(str(uuid4()), "Zet de verwarming op 20 graden en controleer dit")
    actions = ScopedDelegatedWorkActions(None, x.server)
    assert "start_digital_work" in actions.available_action_names(turn)
    receipt = actions.execute(turn, ConversationActionCall("start-home", "start_digital_work", {"task":turn.text}))
    first_id = receipt.output["operation_result"]["work"]["work_id"]
    store = x.server.store
    first = store.load(first_id, **x.service._owner, household_origin=x.service.origin(turn)).spec
    journal = PostgresConversationJournal(database, x.server.conversation_scope)
    foreground = GroundedReply(turn.conversation_id, turn.message_id, "Ik ga ermee aan de slag.", (), turn.occurred_at, "contact")
    journal.commit_reply(turn, foreground)
    dispatched = []
    api = HomeAssistantApiTool(base_url="http://ha.invalid", token="fixture")
    def request(arguments):
        dispatched.append(arguments["method"])
        return {"success":True,"response_json":json.dumps({"temperature":20}),"external_state_verified":False}
    api._request = request
    class Memory:
        def recall_for_presence(self, query):
            assert query.audience.kind == "household"
            return HindsightMemoryEngine._empty_presence(query)
    class Model:
        stop_after_write = True
        def generate(self, source, identity, memory, recent_messages, *, completed_action=None, **kwargs):
            assert source == turn and source.audience.kind == "household"
            if completed_action is None:
                method, path, body = "POST", "/api/services/climate/set_temperature", '{"entity_id":"climate.lounge","temperature":20}'
            elif completed_action.call.arguments["method"] == "POST":
                if self.stop_after_write:
                    self.stop_after_write = False
                    # Another foreground request is admitted during active work.
                    small = x.service.turn(str(uuid4()), "Hoe gaat het?")
                    assert actions.available_action_names(small)
                    assert x.service.execute("list",small,request_key="list")["jobs"][0]["work_id"]==first_id
                    x.service.execute("stop", small, request_key="stop", work_id=first_id)
                method, path, body = "GET", "/api/states/climate.lounge", ""
            else:
                method = None
            call = None if method is None else ConversationActionCall("ha:"+method, "home_assistant_request", dict(
                transport="rest",method=method,path=path,body_json=body,response_pointer="",offset=0,limit=10))
            return ConversationModelResult("De verwarming staat op 20 graden." if call is None else None,
                "controlled",call,usage=ConversationModelUsage.measured(tool_calls=int(call is not None),input_tokens=30,output_tokens=10))
    runner = DelegatedDigitalRunner(store,x.profile,
        source_loader=HouseholdDigitalSourceLoader(database,x.server.conversation_scope,x.server.authority),
        memory=Memory(),model=Model(),identity=IdentityProfile("Eva","Warm",("Be honest",)),
        connector_tools=(PostgresExternalConversationTools(database,api,api,authorize_turn=x.service.validate_authority),),
        clock=lambda:x.clock[0])
    x.clock[0] += timedelta(seconds=1)
    claim = store.claim_next(worker_id="worker:hh-digital",release=x.profile.release,now=x.clock[0])
    call = store.start_call(claim,now=x.clock[0])
    outcome = asyncio.run(runner.run(first,call_id=call.call_id))
    assert outcome.result is None and dispatched == ["POST"], outcome
    store.record_stopped(call,usage=outcome.usage,now=x.clock[0])
    x.clock[0] += timedelta(seconds=1)
    resume = x.service.turn(str(uuid4()),"Ga verder")
    result = x.service.execute("resume",resume,request_key="resume",work_id=first_id)
    second = store.load(result["work"]["work_id"],**x.service._owner,household_origin=first.origin).spec
    assert second.execution_id==first.execution_id and second.source==first.source
    claim = store.claim_next(worker_id="worker:hh-digital",release=x.profile.release,now=x.clock[0])
    call = store.start_call(claim,now=x.clock[0])
    outcome = asyncio.run(runner.run(second,call_id=call.call_id))
    assert outcome.status=="succeeded",outcome
    assert dispatched==["POST","GET"]
    store.record_success(call,outcome.result,now=x.clock[0])
    delivered=x.service.poll(turn)
    assert delivered["result"]==outcome.result.output and delivered["origin_message_id"]==turn.message_id
    x.service.result_delivery(turn,"ack",work_id=second.work_id,result_sha256=delivered["result_sha256"],disposition="completed")
    assert x.service.poll(turn) is None
    assert journal.committed_reply(turn)==foreground
    with pytest.raises(DelegatedWorkNotFound):store.load(second.work_id,**x.service._owner)
    current_release = x.profile.release
    monkeypatch.setattr(DelegatedDigitalProfile, "release", property(lambda self:
        replace(current_release, worker_program_sha256="f" * 64)))
    assert store.load(second.work_id, **x.service._owner, household_origin=second.origin).result == outcome.result
    with pytest.raises(ValueError):x.server.authority.validate_spec(second)


def test_household_digital_policy_revocation_stops_admitted_work(database):
    x=digital_setup(database,"hh-digital-revoke")
    work=x.service.execute("digital",x.turn,request_key="one",task="Controleer automatiseringen")["work"]
    spec=x.server.store.load(work["work_id"],**x.service._owner,household_origin=x.service.origin(x.turn)).spec
    x.policies[0]=x.policies[0].model_copy(update={"state":"revoked"})
    assert not x.server.store.household_authority_current(SimpleNamespace(spec=spec))
    with pytest.raises((ValueError,RuntimeError)):
        x.service.execute("get",x.turn,request_key="read",work_id=work["work_id"])


def test_existing_public_research_remains_readable_after_program_update(database, monkeypatch):
    from eva.bootstrap.household_research_policy import HouseholdResearchAuthority
    from tests.postgres.test_household_public_work import start
    x=setup(database,"hh-research-preserved")
    identifier=start(x)
    old=x.server.authority
    release=old.profile.release
    monkeypatch.setattr(type(old.profile),"release",property(lambda self:
        replace(release,worker_program_sha256="f"*64)))
    current=HouseholdResearchAuthority(voice_authority=old.voice,
        policy_loader=old._policy_loader,budget_loader=old._budget_loader,
        provider_validator=old._provider_validator,profile=old.profile,clock=old.clock)
    x.server.authority=current
    x.server.store._household_authorizer=current.validate_spec
    work=x.server.store.load(identifier,**x.service._owner,household_origin=x.service.origin(x.turn))
    assert work.spec.worker_program_sha256==release.worker_program_sha256
    with pytest.raises(ValueError):current.validate_spec(work.spec)
    assert x.service.execute("stop",x.turn,request_key="stop-old",work_id=identifier)["work"]["state"]=="stopped"
