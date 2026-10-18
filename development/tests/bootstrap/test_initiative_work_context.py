"""Ordinary current work joins private orientation through the existing owner service."""
import hashlib
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace as NS
import pytest
from eva.application.memory_audience import MemoryAudience
from eva.adapters.postgres.delegated_work import DelegatedWorkConflict
from tests.bootstrap.test_initiative_conversation_context import context_service, OWNER, ALIAS, BINDING, NOW


def work(identifier="ordinary", principal=ALIAS, conversation=ALIAS, tenant="home", origin=None):
    return NS(spec=NS(work_id="work:"+identifier,created_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        source=NS(principal_id=principal,conversation_id=conversation,tenant_id=tenant,installation_id="eva",
            audience=MemoryAudience.private_person(principal_id=principal)),
        origin=origin,sha256="a"*64,source_ingress_sha256=hashlib.sha256(identifier.encode()).hexdigest()),
        title="An actual household project",state="succeeded",stop_requested_at=None,
        updated_at=NOW,retention_until=NOW+timedelta(days=7),result=NS(completed_at=NOW.isoformat()))


def test_actual_completed_project_is_context_not_an_open_request(context_service):
    service, authority, _, _ = context_service
    row=work()
    service.store.list_owned=lambda **kw: [row]
    value=service.private_initiative_work_context(now=NOW)
    assert value["available"] is True and value["is_execution_permission"] is False
    job,=value["jobs"]
    assert job["state"]=="succeeded" and job["result_available"] is True
    assert job["spec_sha256"]==row.spec.sha256 and job["source_ingress_sha256"]==row.spec.source_ingress_sha256
    assert job["source_conversation_kind"]=="telegram"


@pytest.mark.parametrize("wrong",["person","conversation","scope","native","future"])
def test_unrelated_private_or_native_work_is_not_owner_life_context(context_service,wrong):
    service, _, _, _=context_service
    kwargs={"person":{"principal":"someone-else"},"conversation":{"conversation":"telegram:other"},
            "scope":{"tenant":"another-home"},"native":{"origin":NS(kind="eva_initiative")},"future":{}}[wrong]
    row=work(**kwargs)
    if wrong=="future":row.updated_at=NOW+timedelta(seconds=1)
    service.store.list_owned=lambda **kw:[row]
    assert service.private_initiative_work_context(now=NOW)["jobs"]==[]


def test_alias_revocation_during_read_discards_snapshot(context_service):
    service, authority, _, _=context_service
    def read(**kw):
        authority["alias"]=None
        return [work()]
    service.store.list_owned=read
    with pytest.raises(DelegatedWorkConflict,match="alias changed"):
        service.private_initiative_work_context(now=NOW)


def test_snapshot_is_small_and_keeps_stopped_state(context_service):
    service, _, _, _=context_service
    rows=[work(str(i)) for i in range(5)];rows[0].state="stopped"
    def read(**kw):
        assert kw["limit"]==65 and kw["active_first"] is True
        return rows
    service.store.list_owned=read
    value=service.private_initiative_work_context(now=NOW)
    assert len(value["jobs"])==3 and value["truncated"] is True
    assert value["jobs"][0]["state"]=="stopped"


@pytest.mark.parametrize("offset",[0,-1])
def test_expired_work_never_returns_to_autonomous_context(context_service,offset):
    service, _, _, _=context_service
    row=work();row.retention_until=NOW+timedelta(seconds=offset)
    service.store.list_owned=lambda **kw:[row]
    assert service.private_initiative_work_context(now=NOW)["jobs"]==[]


def test_latest_request_generation_does_not_hide_older_unknown_attempt(context_service):
    service, _, _, _=context_service
    old,new=work("old"),work("new")
    old.spec.created_at=(NOW-timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old.updated_at=NOW-timedelta(minutes=1);old.state="ambiguous"
    old.spec.source_ingress_sha256=new.spec.source_ingress_sha256
    service.store.list_owned=lambda **kw:[old,new]
    job,=service.private_initiative_work_context(now=NOW)["jobs"]
    assert job["work_id"]==new.spec.work_id and job["state"]=="succeeded"
    assert job["earlier_attempts"]==1 and job["earlier_uncertain_attempts"]==1


def test_verified_private_chat_project_survives_newer_local_test_activity(context_service):
    service, _, _, _=context_service
    household=work("household")
    household.spec.created_at=(NOW-timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    household.updated_at=NOW-timedelta(days=1)
    local=[work("local"+str(i),principal=str(OWNER),conversation="dashboard:"+str(BINDING)) for i in range(12)]
    service.store.list_owned=lambda **kw:[*local,household]
    context=service.private_initiative_work_context(now=NOW)
    assert context["jobs"][0]["work_id"]==household.spec.work_id
    assert len(context["jobs"])==3 and context["truncated"] is True
