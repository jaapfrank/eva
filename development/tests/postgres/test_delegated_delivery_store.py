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


def _digital_return_chain(engine, label):
    from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile
    from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
    store, _, _, delivery, current, auth = setup(engine, label)
    owner, binding = auth["owner_principal_id"], auth["principal_binding_id"]
    source = ConversationTurn.private("tenant:" + label, "installation:" + label,
        str(owner), "telegram:42", "telegram:42:1", "Prepare a private read-only proposal",
        NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
    destination = PrivateWorkDestination.from_telegram_turn(source,
        owner_principal_id=owner, principal_binding_id=binding,
        telegram_user_id=42, chat_id="42", bindings=current[0])
    now = [NOW]
    from pathlib import Path
    runner = Path(__file__).resolve().parents[3] / "src/eva/bootstrap/delegated_digital_runner.py"
    profile = DelegatedDigitalProfile("model:controlled", "a" * 64, "b" * 64,
        ("discover_connector_tools", "use_connector_tool"),
        (("bootstrap/delegated_digital_runner.py", hashlib.sha256(runner.read_bytes()).hexdigest()),))
    service = DelegatedWorkOwnerService(store, **auth, clock=lambda: now[0],
        digital_profile=profile, delivery_destination_loader=lambda turn:
            destination if turn.message_id == source.message_id else None)
    first_id = service.execute("digital", source, task=source.text,
        request_key="digital:first")["work"]["work_id"]
    specs = [store.load(first_id, **auth).spec]
    # Establish the real failure-notice cutoff before either historical failure.
    assert delivery.claim_next(now=NOW) is None
    for index in range(2):
        second = index * 4
        claim = store.claim_next(worker_id="worker:controlled", release=profile.release,
            now=NOW + timedelta(seconds=second + 1))
        call = store.start_call(claim, now=NOW + timedelta(seconds=second + 2))
        store.record_failure(call, usage=_usage(), error_code="incomplete_work",
            error_fingerprint="ab" * 32, ambiguous=False,
            now=NOW + timedelta(seconds=second + 3))
        now[0] = NOW + timedelta(seconds=second + 4)
        turn = replace(source, conversation_id="dashboard:" + str(binding),
            message_id="resume:" + str(index), text="Continue the same task",
            occurred_at=now[0].strftime("%Y-%m-%dT%H:%M:%SZ"))
        successor_id = service.execute("resume", turn, work_id=specs[-1].work_id,
            request_key="resume:" + str(index))["work"]["work_id"]
        specs.append(store.load(successor_id, **auth).spec)
    # Later bound Telegram messages are valid direct routes, but are not the
    # original source: inheritance must traverse this one and find generation 1.
    later_destination = replace(destination, origin_message_id="telegram:42:2")
    with engine.begin() as connection:
        store._scope(connection)
        delivery_module.record_admission_destination(connection, store.scope,
            spec=specs[1], destination=later_destination,
            occurred_at=NOW + timedelta(seconds=4), existing=False)
    latest = specs[-1]
    claim = store.claim_next(worker_id="worker:controlled", release=profile.release,
        now=NOW + timedelta(seconds=9))
    call = store.start_call(claim, now=NOW + timedelta(seconds=10))
    from eva.contracts.delegated_digital_work import DelegatedDigitalWorkResult
    output = "The requested private proposal is complete and independently read back."
    result = DelegatedDigitalWorkResult(contract="DelegatedDigitalWorkResult/v4",
        schema_version=4, work_id=latest.work_id, call_id=call.call_id,
        output=output, output_sha256=hashlib.sha256(output.encode()).hexdigest(),
        spec_sha256=latest.sha256, usage=_usage().to_payload(),
        completed_at="2026-08-26T12:00:11Z")
    store.record_success(call, result, now=NOW + timedelta(seconds=11))
    delivery._clock = lambda: NOW + timedelta(seconds=12)
    return store, specs, destination, delivery, current, auth, result


def test_dashboard_resume_inherits_original_private_return_and_claims_only_latest_once(delegated_engine):
    store, specs, destination, delivery, current, auth, result = _digital_return_chain(
        delegated_engine, "private-return-chain")
    # More than the queue's concurrency retry count of unrelated unbound tasks
    # must not starve the one current result with a proven source destination.
    for number in range(17):
        other = specs[0].model_copy(update={"work_id": "work:unbound:" + str(number),
            "execution_id": "work:unbound:" + str(number)})
        admit(store, other, None)
        at = NOW + timedelta(seconds=12 + number * 3)
        worker = store.claim_next(worker_id="worker:unbound", release=_release(other), now=at)
        call = store.start_call(worker, now=at + timedelta(seconds=1))
        completed = result.model_copy(update={"work_id": other.work_id, "call_id": call.call_id,
            "spec_sha256": other.sha256,
            "completed_at": (at + timedelta(seconds=2)).strftime("%Y-%m-%dT%H:%M:%SZ")})
        store.record_success(call, completed, now=at + timedelta(seconds=2))
    delivery._clock = lambda: NOW + timedelta(seconds=80)
    before = [store.load(spec.work_id, **auth) for spec in specs]
    with delegated_engine.begin() as connection:
        store._scope(connection)
        rows_before = connection.execute(sa.select(delivery_module.operational_events).where(
            delivery_module.operational_events.c.event_type == delivery_module._EVENT)).mappings().all()
        assert delivery_module._read(connection, store.scope, specs[-1]) is None
    assert delivery.load(specs[-1].work_id, **auth) == destination
    authorized = delivery.authorize_delivery(specs[-1].work_id, **auth)
    assert authorized.work_id == specs[-1].work_id and authorized.output == result.output
    assert authorized.spec_sha256 == specs[-1].sha256
    claim = delivery.claim_next(now=NOW + timedelta(seconds=80))
    assert claim.work_id == specs[-1].work_id and claim.operation_id == authorized.operation_id
    assert delivery.acknowledge(claim, "42:7", now=NOW + timedelta(seconds=81)) == "acknowledged"
    assert delivery.acknowledge(claim, "42:7", now=NOW + timedelta(seconds=82)) == "already_recorded"
    assert delivery.claim_next(now=NOW + timedelta(seconds=83)) is None
    assert [store.load(spec.work_id, **auth) for spec in specs] == before
    with delegated_engine.begin() as connection:
        store._scope(connection)
        rows_after = connection.execute(sa.select(delivery_module.operational_events).where(
            delivery_module.operational_events.c.event_type == delivery_module._EVENT)).mappings().all()
        assert rows_after == rows_before  # effective inheritance never backfills
        assert delivery_module._read(connection, store.scope, specs[-1]) is None


@pytest.mark.parametrize("damage", ["source", "retention", "event", "recipient"])
def test_inherited_private_return_rejects_changed_source_expiry_event_and_recipient(delegated_engine, monkeypatch, damage):
    store, specs, destination, delivery, current, auth, _ = _digital_return_chain(
        delegated_engine, "private-return-" + damage)
    if damage == "recipient":
        current[0] = TelegramPrincipalBindings((TelegramPrincipalBinding(42,
            str(auth["owner_principal_id"]), ("-100",), str(auth["principal_binding_id"])),))
    elif damage == "source":
        load = store._load_locked
        def altered_parent(connection, work_id):
            work = load(connection, work_id)
            return replace(work, spec=work.spec.model_copy(update={
                "source_ingress_sha256": "dd" * 32})) if work_id == specs[1].work_id else work
        monkeypatch.setattr(store, "_load_locked", altered_parent)
    elif damage == "event":
        original_read = delivery_module._read
        def corrupted_read(connection, scope, spec):
            if spec.work_id != specs[0].work_id:
                return original_read(connection, scope, spec)
            class CorruptRead:
                def execute(self, statement):
                    rows = [dict(row) for row in connection.execute(statement).mappings().all()]
                    rows[0]["payload_sha256"] = bytes.fromhex("ff" * 32)
                    class Result:
                        def mappings(self): return self
                        def all(self): return rows
                    return Result()
            return original_read(CorruptRead(), scope, spec)
        monkeypatch.setattr(delivery_module, "_read", corrupted_read)
    else:
        load = store._load_locked
        def expired_parent(connection, work_id):
            work = load(connection, work_id)
            return replace(work, retention_until=NOW + timedelta(seconds=11))\
                if work_id == specs[1].work_id else work
        monkeypatch.setattr(store, "_load_locked", expired_parent)
    with pytest.raises(ValueError):
        delivery.authorize_delivery(specs[-1].work_id, **auth)


@pytest.mark.parametrize("key", ["owner_principal_id", "principal_binding_id", "episode_id",
    "execution_id", "source", "source_ingress_sha256", "grant", "task", "generation", "work_id"])
def test_effective_return_requires_every_original_parent_identity(monkeypatch, key):
    from types import SimpleNamespace
    from uuid import uuid5
    installation = UUID(int=9)
    source = SimpleNamespace(tenant_id="tenant", installation_id="installation",
        principal_id="person", conversation_id="telegram:42", message_id="telegram:42:1")
    parent_spec = dict(contract="DelegatedDigitalWork/v4", audience="private", work_id="work:first",
        parent_task_id="chat:first", generation=1, owner_principal_id="owner",
        principal_binding_id="binding", episode_id="episode", execution_id="work:first",
        source=source, source_ingress_sha256="a" * 64, grant={"maximum_model_runs": 8}, task="Task")
    child_spec = dict(parent_spec, work_id="work:eva-resume:" + str(uuid5(installation, "work:first:2")),
                      parent_task_id="work:first", generation=2)
    child_spec[key] = 3 if key == "generation" else "wrong"
    work = SimpleNamespace(spec=SimpleNamespace(**child_spec), created_at=NOW + timedelta(seconds=4))
    parent = SimpleNamespace(spec=SimpleNamespace(**parent_spec), state="failed", created_at=NOW,
                             retention_until=NOW + timedelta(days=1))
    store = SimpleNamespace(scope=SimpleNamespace(installation_id=installation),
                             _load_locked=lambda connection, work_id: parent)
    monkeypatch.setattr(delivery_module, "_read", lambda *args: None)
    with pytest.raises(ValueError, match="ancestry"):
        delivery_module._effective_destination(None, store, work, NOW + timedelta(seconds=12))
