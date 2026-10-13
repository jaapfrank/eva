"""The shared provider uses admitted household sessions without duplicate tools."""
import asyncio
from dataclasses import replace
from uuid import uuid4
import pytest
from eva.adapters.postgres.scoped_delegated_work_actions import ScopedDelegatedWorkActions
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
from eva.application.conversation import ConversationActionCall, ConversationConflict
from tests.postgres.test_household_public_work import setup, PUBLIC, delegated_engine


class PrivateActions:
    supports_indexed_steps = True
    action_names = tuple(d.name for d in PostgresDelegatedWorkActions.conversation_tools(include_research=True))
    conversation_tools = staticmethod(PostgresDelegatedWorkActions.conversation_tools)
    def available_for(self, turn): return turn.audience.kind == "private_person"
    def pending(self, turn, **kwargs): return None
    def execute(self, turn, call, **kwargs): return "private-result"
    def reply_guard(self, turn, receipts): return None


def test_shared_selector_admits_household_research_and_retains_private_route(delegated_engine):
    x = setup(delegated_engine, "hh-composition")
    scoped = ScopedDelegatedWorkActions(PrivateActions(), x.server)
    actions = PostgresConversationActions(delegated_engine, external_tools=(scoped,))
    names = actions.available_action_names(x.turn)
    assert set(names) & set(scoped.action_names) == {
        "start_background_research", "get_delegated_work", "stop_delegated_work", "resume_delegated_work"}
    assert len(scoped.conversation_tools(include_research=True)) == len(set(scoped.action_names))
    call = ConversationActionCall("call:household", "start_background_research", {"research": PUBLIC})
    receipt = scoped.execute(x.turn, call)
    work_id = receipt.output["operation_result"]["work"]["work_id"]
    assert scoped.pending(x.turn) == receipt
    assert callable(scoped.reply_guard(x.turn, (receipt,)))
    assert asyncio.run(x.worker.run_once()) == "succeeded"
    assert x.service.poll(x.service.control_turn())["work_id"] == work_id
    from eva.application.conversation import ConversationTurn
    private = ConversationTurn.private(x.turn.tenant_id, x.turn.installation_id,
        str(x.service._owner["owner_principal_id"]), "private", "private-message", "Hello", x.turn.occurred_at)
    assert set(scoped.available_action_names(private)) == set(scoped.action_names)
    assert scoped.execute(private, call) == "private-result"
    with pytest.raises(ConversationConflict):
        scoped.execute(x.turn, ConversationActionCall("forged", "start_delegated_work", {"task": "private"}))
    x.service.current("close")
    with pytest.raises((ValueError, RuntimeError)):
        scoped.reply_guard(x.turn, (receipt,))


def test_unadmitted_or_closed_household_turn_cannot_select_provider(delegated_engine):
    x = setup(delegated_engine, "hh-select-closed")
    scoped = ScopedDelegatedWorkActions(PrivateActions(), x.server)
    assert scoped.available_action_names(x.turn)
    forged = replace(x.turn, message_id=x.turn.conversation_id+":request:"+str(uuid4()))
    assert scoped.available_action_names(forged) == ()
    assert scoped.pending(forged) is None
    x.service.current("close")
    assert scoped.available_action_names(x.turn) == ()
    with pytest.raises((ValueError, RuntimeError)):
        scoped.execute(x.turn, ConversationActionCall("closed", "start_background_research", {"research": PUBLIC}))


@pytest.mark.parametrize("separate_scope", [False, True])
def test_http_real_ingress_reaches_shared_provider_and_original_result_session(delegated_engine, separate_scope):
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from eva.adapters.api.household_voice_session import install_household_voice_session
    from eva.application.conversation import GroundedReply
    from tests.postgres.test_household_public_work import SAT, DEVICE
    x = setup(delegated_engine, "hh-http-composed-"+str(separate_scope),
              conversation_label="hh-separate-conversation" if separate_scope else None)
    assert (x.server.store.scope != x.server.conversation_scope) is separate_scope
    scoped = ScopedDelegatedWorkActions(PrivateActions(), x.server)
    work_ids=[]; admitted=[]
    class Runtime:
        def admit_turn_audience(self, turn):
            assert turn.audience.kind == "household" and turn.principal_id == "household-device:"+DEVICE
        def respond(self, turn):
            admitted.append(turn)
            assert "start_delegated_work" not in scoped.available_action_names(turn)
            receipt=scoped.execute(turn,ConversationActionCall("request:research","start_background_research",{"research":PUBLIC}))
            work_ids.append(receipt.output["operation_result"]["work"]["work_id"])
            return GroundedReply(turn.conversation_id,turn.message_id,"Onderzoek aangenomen",(),turn.occurred_at,"fixture")
        def after_response(self, turn, reply):pass
    app=FastAPI();install_household_voice_session(app,runtime=Runtime(),authority=x.server.authority.voice,work_server=x.server)
    client=TestClient(app);path="/internal/v1/ha-voice/session";headers={"Authorization":"Bearer test-token"}
    device={"satellite_id":SAT,"device_id":DEVICE}
    session=client.post(path+'/work/open',headers=headers,json=device).json()['session_id']
    payload={**device,"session_id":session,"message_id":str(uuid4()),"text":PUBLIC['question']}
    reply=client.post(path+'/respond',headers=headers,json=payload)
    assert reply.status_code==200 and json.loads(reply.text.splitlines()[-1])['type']=='final'
    assert len(work_ids)==1 and asyncio.run(x.worker.run_once())=='succeeded'
    result=client.post(path+'/work/poll',headers=headers,json={**device,"session_id":session}).json()['result']
    assert result['work_id']==work_ids[0] and result['origin_message_id']==admitted[0].message_id
    assert client.post(path+'/respond',headers=headers,json={**payload,"session_id":str(uuid4())}).status_code==403


def test_explicit_composition_reloads_parent_authority_and_rejects_scope(delegated_engine, monkeypatch):
    import json
    from eva.bootstrap import household_work_composition as composition
    from eva.contracts.canonical_json import canonical_json_bytes
    x = setup(delegated_engine, "hh-config")
    parent = x.server.authority
    config = {"contract": "HouseholdWorkConfiguration/v1", "schema_version": 1,
        "task_tenant_id": str(x.server.store.scope.tenant_id),
        "task_installation_id": str(x.server.store.scope.installation_id),
        **{name: "/run/eva/"+name for name in composition.HouseholdWorkConfiguration.model_fields
           if name.endswith("_file")}}
    parent.voice._path = config["voice_policy_file"]
    parent.voice._token_path = config["voice_token_file"]
    files = {"/run/eva/household.json": json.dumps(config).encode(),
        config["voice_token_file"]: b"test-token",
        config["budget_file"]: canonical_json_bytes(parent._budget_loader().to_payload())}
    monkeypatch.setattr(composition, "read_private_runtime_file_bytes", lambda path, **kwargs: files[path])
    monkeypatch.setattr(composition, "_read_policy", lambda path: parent.voice.policy)
    monkeypatch.setattr(composition, "read_household_research_policy", lambda path: x.policies[0])
    leases = []
    monkeypatch.setattr(composition, "validate_delegated_model_access_lease", lambda path: leases.append(path))
    original = composition.HouseholdResearchAuthority
    monkeypatch.setattr(composition, "HouseholdResearchAuthority",
        lambda **kwargs: original(**kwargs, clock=lambda: x.clock[0]))
    kwargs = dict(scope=x.server.store.scope, profile=parent.profile,
                  model_lease_file="/run/eva/lease", voice_authority=parent.voice)
    assert composition.load_household_work_authority(None, **kwargs) is None
    assert not leases
    authority = composition.load_household_work_authority("/run/eva/household.json", **kwargs)
    assert authority.voice is parent.voice and leases
    authority.validate_current()
    with pytest.raises(ValueError, match="scope differs"):
        composition.load_household_work_authority("/run/eva/household.json", **(kwargs | {"scope": None}))
    files[config["voice_token_file"]] = b"different-token"
    with pytest.raises(ValueError, match="voice authority"):
        composition.load_household_work_authority("/run/eva/household.json", **kwargs)
    x.policies[0] = x.policies[0].model_copy(update={"state": "revoked"})
    with pytest.raises(ValueError, match="authority changed"):
        authority.validate_current()


def test_household_server_rejects_missing_sponsor_before_advertising_readiness(delegated_engine):
    from uuid import uuid4
    from eva.bootstrap.household_work import HouseholdWorkServer
    x = setup(delegated_engine, "hh-startup-sponsor")
    # A valid voice/research policy is insufficient when its referenced sponsor
    # binding is absent from the task storage namespace. No grants are seeded.
    original = x.server.authority.policy
    x.server.authority.policy = original.model_copy(update={"principal_binding_id": uuid4()})
    x.server.authority.validate_current = lambda: None
    with pytest.raises(RuntimeError, match="owner binding is not current"):
        HouseholdWorkServer(delegated_engine, x.server.store.scope, x.server.authority)
