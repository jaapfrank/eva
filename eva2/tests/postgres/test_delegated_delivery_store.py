from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
from uuid import UUID

import pytest
import sqlalchemy as sa
from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore, DelegatedWorkConflict, DelegatedWorkNotFound
from eva.adapters.postgres.delegated_work_delivery import PostgresDelegatedWorkDeliveryStore
from eva.adapters.postgres import delegated_work_delivery as delivery_module
from eva.application.conversation import ConversationTurn
from eva.application.delegated_work_delivery import PrivateWorkDestination
from eva.application.telegram_ingress import TelegramPrincipalBindings, TelegramPrincipalBinding
from eva.contracts.delegated_work import DelegatedWorkResult
from eva.contracts.canonical_json import canonical_sha256
from tests.postgres.test_delegated_work_store import delegated_engine, _seed_authority, _id, _spec, _release, _usage, principal_bindings, principals

NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)


def setup(engine, label):
    scope, episode, binding = _seed_authority(engine, label)
    owner = _id(label, "principal")
    store = PostgresDelegatedWorkStore(engine, scope)
    spec = _spec(label, scope, episode, binding)
    current = [TelegramPrincipalBindings((TelegramPrincipalBinding(42,str(owner),("42",),str(binding)),))]
    source = ConversationTurn.private("tenant:"+label,"installation:"+label,"telegram:42",
        "telegram:42","telegram:42:1","A public question","2026-08-26T12:00:00Z")
    destination = PrivateWorkDestination.from_telegram_turn(source,owner_principal_id=owner,
        principal_binding_id=binding,telegram_user_id=42,chat_id="42",bindings=current[0])
    delivery = PostgresDelegatedWorkDeliveryStore(store,bindings_loader=lambda:current[0],clock=lambda:NOW+timedelta(seconds=4))
    authority = dict(owner_principal_id=owner,principal_binding_id=binding)
    return store,spec,destination,delivery,current,authority


def admit(store,spec,destination):
    return store.admit(spec=spec,title="Public research",summary="Public research",worker_role="Research",
        retention_policy_id="retention:test",retention_until=NOW+timedelta(days=1),
        occurred_at=NOW,delivery_destination=destination)


def succeed(store,spec):
    claim = store.claim_next(worker_id="worker:test",release=_release(spec),now=NOW+timedelta(seconds=1))
    call = store.start_call(claim,now=NOW+timedelta(seconds=2))
    output = "Result remains private to its owner."
    result = DelegatedWorkResult(contract="DelegatedWorkResult/v1",schema_version=1,work_id=spec.work_id,
        call_id=call.call_id,output=output,output_sha256=hashlib.sha256(output.encode()).hexdigest(),
        spec_sha256=spec.sha256,usage=_usage().to_payload(),completed_at="2026-08-26T12:00:03Z")
    store.record_success(call,result,now=NOW+timedelta(seconds=3))
    return result


def test_atomic_binding_preserves_spec_and_rejects_backfill_or_retarget(delegated_engine,monkeypatch):
    store,spec,dest,delivery,current,auth = setup(delegated_engine,"destination-atomic")
    before = spec.to_payload()
    original = delivery_module.insert_initial_timeline_thread
    def fail(*a,**kw): raise RuntimeError("simulated transaction interruption")
    monkeypatch.setattr(delivery_module,"insert_initial_timeline_thread",fail)
    with pytest.raises(RuntimeError): admit(store,spec,dest)
    with pytest.raises(DelegatedWorkNotFound): store.load(spec.work_id,**auth)
    monkeypatch.setattr(delivery_module,"insert_initial_timeline_thread",original)
    assert admit(store,spec,dest).disposition == "inserted"
    assert admit(store,spec,dest).disposition == "already_present"
    assert delivery.load(spec.work_id,**auth) == dest
    assert store.load(spec.work_id,**auth).spec.to_payload() == before
    with pytest.raises(ValueError,match="changed or backfilled"):
        admit(store,spec,replace(dest,origin_message_id="telegram:42:2"))
    with pytest.raises(ValueError): admit(store,spec,None)
    old = spec.model_copy(update={"work_id":spec.work_id+":historical"})
    admit(store,old,None)
    assert delivery.load(old.work_id,**auth) is None
    with pytest.raises(ValueError,match="backfilled"): admit(store,old,dest)


def test_delivery_authority_is_current_and_never_exposes_other_owner_result(delegated_engine):
    store,spec,dest,delivery,current,auth = setup(delegated_engine,"destination-authority")
    admit(store,spec,dest)
    assert delivery.authorize_delivery(spec.work_id,**auth) is None
    result = succeed(store,spec)
    with pytest.raises(DelegatedWorkConflict):
        delivery.authorize_delivery(spec.work_id,owner_principal_id=UUID(int=99),
                                    principal_binding_id=auth["principal_binding_id"])
    allowed = delivery.authorize_delivery(spec.work_id,**auth)
    assert allowed.output == result.output and allowed.result_sha256 == canonical_sha256(result.to_payload())
    current[0] = TelegramPrincipalBindings((TelegramPrincipalBinding(42,str(auth["owner_principal_id"]),("-100",),str(auth["principal_binding_id"])),))
    with pytest.raises(ValueError,match="unavailable"): delivery.authorize_delivery(spec.work_id,**auth)
    current[0] = TelegramPrincipalBindings((TelegramPrincipalBinding(42,str(auth["owner_principal_id"]),("42",),str(auth["principal_binding_id"])),))
    with delegated_engine.begin() as connection:
        connection.execute(sa.update(principal_bindings).where(principal_bindings.c.tenant_id==store.scope.tenant_id,
            principal_bindings.c.principal_binding_id==auth["principal_binding_id"]).values(status="revoked"))
    with pytest.raises(DelegatedWorkConflict): delivery.authorize_delivery(spec.work_id,**auth)


def test_one_claim_ack_is_durable_and_never_claimed_again(delegated_engine):
    store,spec,dest,delivery,current,auth = setup(delegated_engine,"destination-concurrent")
    admit(store,spec,dest); result=succeed(store,spec)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims=list(pool.map(lambda _:delivery.claim_next(now=NOW+timedelta(seconds=4)),range(2)))
    actual=[c for c in claims if c is not None]
    assert len(actual)==1
    claim=actual[0]
    assert claim.operation_id==delivery.authorize_delivery(spec.work_id,**auth).operation_id
    with pytest.raises(ValueError): delivery.acknowledge(replace(claim,claim_id="forged"),"42:7",now=NOW+timedelta(seconds=5))
    assert delivery.acknowledge(claim,"42:7",now=NOW+timedelta(seconds=5))=="acknowledged"
    assert delivery.acknowledge(claim,"42:7",now=NOW+timedelta(seconds=6))=="already_recorded"
    with pytest.raises(ValueError): delivery.acknowledge(claim,"42:8",now=NOW+timedelta(seconds=7))
    assert delivery.claim_next(now=NOW+timedelta(days=1)) is None
    assert delivery.status(spec.work_id,**auth)=="delivery_acknowledged"
    assert store.load(spec.work_id,**auth).result==result


@pytest.mark.parametrize("finish",["crash","timeout","late_ack"])
def test_uncertain_delivery_never_retries_and_preserves_result(delegated_engine,finish):
    store,spec,dest,delivery,current,auth = setup(delegated_engine,"destination-"+finish)
    admit(store,spec,dest); result=succeed(store,spec)
    claim=delivery.claim_next(now=NOW+timedelta(seconds=4),lease_seconds=2)
    if finish=="timeout":
        assert delivery.mark_ambiguous(claim,"TimeoutError",now=NOW+timedelta(seconds=5))=="ambiguous"
    elif finish=="late_ack":
        assert delivery.acknowledge(claim,"42:9",now=NOW+timedelta(seconds=6))=="ambiguous"
    restarted=PostgresDelegatedWorkDeliveryStore(store,bindings_loader=lambda:current[0],clock=lambda:NOW+timedelta(seconds=4))
    assert restarted.claim_next(now=NOW+timedelta(seconds=7)) is None
    assert restarted.status(spec.work_id,**auth)=="delivery_ambiguous"
    assert restarted.claim_next(now=NOW+timedelta(days=1)) is None
    assert store.load(spec.work_id,**auth).result==result


@pytest.mark.parametrize("expired", ["work", "binding", "person", "future_verification"])
def test_expired_authority_or_result_is_not_returned(delegated_engine,expired):
    store,spec,dest,delivery,current,auth=setup(delegated_engine,"destination-expiry-"+expired)
    admit(store,spec,dest); succeed(store,spec)
    with delegated_engine.begin() as connection:
        if expired == "work":
            from eva.adapters.postgres.current_catalog import delegated_work_runs
            connection.execute(sa.update(delegated_work_runs).where(
                delegated_work_runs.c.tenant_id==store.scope.tenant_id,
                delegated_work_runs.c.work_id==spec.work_id).values(retention_until=NOW+timedelta(seconds=3)))
        elif expired == "person":
            connection.execute(sa.update(principals).where(principals.c.tenant_id==store.scope.tenant_id,
                principals.c.principal_id==auth["owner_principal_id"]).values(retention_until=NOW+timedelta(seconds=3)))
        else:
            values = ({"verified_at":NOW+timedelta(seconds=10)} if expired=="future_verification"
                      else {"retention_until":NOW+timedelta(seconds=3)})
            connection.execute(sa.update(principal_bindings).where(principal_bindings.c.tenant_id==store.scope.tenant_id,
                principal_bindings.c.principal_binding_id==auth["principal_binding_id"]).values(**values))
    with pytest.raises(ValueError): delivery.authorize_delivery(spec.work_id,**auth)
    with pytest.raises(ValueError): delivery.load(spec.work_id,**auth)
    if expired=="work":
        assert delivery.claim_next(now=NOW+timedelta(seconds=4)) is None
