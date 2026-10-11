"""Synthetic native-origin handoff on the existing queue; no provider traffic."""
import asyncio
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from eva.adapters.postgres.current_catalog import delegated_work_runs
from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore, DelegatedWorkConflict
from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
from eva.adapters.postgres.operational_timeline import CheckpointConflict
from eva.application.initiative_execution import NativeInitiativeDelegation, current_initiative
from eva.application.personal_interests import PersonalInterestWorker
from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile
from eva.bootstrap.delegated_digital_runner import DelegatedDigitalRunner
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.bootstrap.native_initiative_work import admit_native_initiative
from eva.contracts.delegated_digital_work import DelegatedDigitalWorkResult, PrivateWorkSource
from eva.contracts.delegated_work import DelegatedWorkUsage, load_delegated_work_spec
from eva.contracts.canonical_json import canonical_sha256
from eva.operations.delegated_work_worker import DelegatedWorkWorker
from eva.providers.mcp_autonomy import _SourceTurnLoader
from tests.postgres.test_owner_initiatives import costless_native, NOW


@pytest.fixture
def native_digital(costless_native):
    auth, claim, interests, control, sensory, path, policy = costless_native
    name = "bootstrap/delegated_digital_runner.py"
    root = Path(__file__).resolve().parents[2] / "src/eva"
    profile = DelegatedDigitalProfile("model:synthetic", "a"*64, "b"*64,
        ("write_programming_file", "use_programming_tool"),
        ((name, hashlib.sha256((root / name).read_bytes()).hexdigest()),))
    policy["digital_work"] = {"worker_profile_release_sha256": profile.release.worker_profile_release_sha256,
        "capability_ids": list(profile.capability_ids)}
    path.write_text(json.dumps(policy))
    store = PostgresDelegatedWorkStore(control._engine, control._authority,
        initiative_authority_loader=lambda: auth)
    service = DelegatedWorkOwnerService(store, owner_principal_id=control._owner,
        principal_binding_id=control._binding, digital_profile=profile,
        conversation_scope=control._scope, clock=lambda: NOW)
    try:
        yield SimpleNamespace(auth=auth, claim=claim, interests=interests, control=control,
            sensory=sensory, path=path, policy=policy, store=store, service=service, profile=profile)
    finally:
        # Only the synthetic rows of this own disposable scope. The existing
        # conftest's downgrade intentionally refuses to discard durable work.
        with control._engine.begin() as conn:
            conn.execute(sa.delete(delegated_work_runs).where(
                delegated_work_runs.c.tenant_id == store.scope.tenant_id,
                delegated_work_runs.c.installation_id == store.scope.installation_id))


def admitted(f):
    receipt = admit_native_initiative(f.auth, f.claim, f.service)
    work = f.store.load(receipt.work_id, **f.service._owner)
    return receipt, work


def started(f, work):
    claim = f.store.claim_next(worker_id="synthetic-digital", release=f.profile.release, now=NOW)
    assert claim is not None and claim.spec == work.spec
    return f.store.start_call(claim, now=NOW)


def result(call, text="Exact synthetic final result"):
    usage = DelegatedWorkUsage(contract="DelegatedWorkUsage/v1", schema_version=1, measurement="partial",
        completed_model_responses=1, tool_calls=1, input_tokens=30, output_tokens=10,
        total_tokens=40, cost_usd_micros=None)
    return DelegatedDigitalWorkResult(contract="DelegatedDigitalWorkResult/v4", schema_version=4,
        work_id=call.work_id, call_id=call.call_id, spec_sha256=call.spec.sha256,
        output=text, output_sha256=hashlib.sha256(text.encode()).hexdigest(),
        usage=usage.to_payload(), completed_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))


def test_admission_is_pending_and_real_completion_returns_to_original_parent(native_digital):
    f = native_digital
    receipt, work = admitted(f)
    assert isinstance(receipt, NativeInitiativeDelegation)
    assert work.spec.contract == "DelegatedInitiativeDigitalWork/v1"
    assert load_delegated_work_spec(work.spec.to_payload()) == work.spec
    assert f.auth.timeline.load(f.claim.thread_id).phase == "initiative_work_waiting"
    assert f.interests.claim_next(tenant_id=f.claim.tenant_id,
        installation_id=f.claim.installation_id, now=NOW + timedelta(hours=1)) is None
    with pytest.raises(LookupError):
        f.control.artifact(f.claim.intent_id, now=NOW)
    call = started(f, work)
    assert f.auth.timeline.load(f.claim.thread_id).phase == "initiative_work_waiting"
    outcome = result(call)
    saved = f.store.record_success(call, outcome, now=NOW)
    assert saved.state == "succeeded"
    assert f.control.artifact(f.claim.intent_id, now=NOW)["content"] == outcome.output
    assert f.control.page(now=NOW)["cards"][0]["status"] == "done"
    # Repeated outcome is replayed, never another parent completion/model call.
    assert f.store.record_success(call, outcome, now=NOW) == saved
    events = f.auth.timeline.events(f.claim.thread_id)
    assert sum(e.event_type == "PERSONAL_INITIATIVE_COMPLETED" for e in events) == 1
    assert events[-1].payload["data"]["result_sha256"] == canonical_sha256(outcome.to_payload())
    assert events[-1].payload["data"]["semantic_verification"] is False


def test_native_queue_receipt_does_not_complete_personal_worker():
    from eva.application.personal_interests import PersonalInterestClaim
    claim = PersonalInterestClaim("t", "i", "owner", "source", "source-message",
        "personal-interest:"+"a"*64, "intent:personal-interest:"+"a"*64,
        "Title", "Reason", "Outcome", NOW, 2, "autonomous_task")
    store = SimpleNamespace(completed_unprojected=lambda **kw: None,
        claim_next=lambda **kw: claim,
        complete_native=lambda *a, **kw: pytest.fail("ACK is not completion"),
        block_native=lambda *a, **kw: pytest.fail("admission is not failure"))
    worker = PersonalInterestWorker(store, None, None,
        execute_native=lambda c: NativeInitiativeDelegation("work:eva-initiative:"+"a"*64))
    assert worker.run_once(tenant_id="t", installation_id="i", now=NOW) == "pending"


@pytest.mark.parametrize("fault", ["queue_insert", "parent_transition"])
def test_handoff_failure_rolls_back_queue_and_parent(native_digital, monkeypatch, fault):
    import eva.bootstrap.native_initiative_work as bridge
    f = native_digital
    def fail(*args, **kwargs): raise RuntimeError("synthetic crash boundary")
    if fault == "queue_insert":
        monkeypatch.setattr(f.store, "admit", fail)
    else:
        monkeypatch.setattr(bridge, "transition_timeline_thread", fail)
    with pytest.raises(RuntimeError, match="crash boundary"):
        admit_native_initiative(f.auth, f.claim, f.service)
    assert f.auth.timeline.load(f.claim.thread_id).phase == "exploration_claimed"
    assert f.store.workboard(**f.service._owner, now=NOW).cards == []


@pytest.mark.parametrize("change", ["no_profile", "profile_hash", "unsafe_capability"])
def test_only_explicit_local_host_profile_can_admit(native_digital, change):
    f = native_digital
    if change == "no_profile": del f.policy["digital_work"]
    elif change == "profile_hash": f.policy["digital_work"]["worker_profile_release_sha256"] = "f"*64
    else: f.policy["digital_work"]["capability_ids"] = ["connect_service"]
    f.path.write_text(json.dumps(f.policy))
    with pytest.raises(InitiativeMandateDenied):
        admit_native_initiative(f.auth, f.claim, f.service)
    assert f.auth.timeline.load(f.claim.thread_id).phase == "exploration_claimed"


@pytest.mark.parametrize("change", ["owner", "source_text", "source_hash", "revision", "scope"])
def test_worker_reconstructs_only_exact_durable_native_origin(native_digital, change):
    f = native_digital
    _, work = admitted(f)
    spec = work.spec
    if change == "owner": spec = spec.model_copy(update={"owner_principal_id": "other-owner"})
    elif change == "source_text": spec = spec.model_copy(update={"source": spec.source.model_copy(update={"message_id": "fake"})})
    elif change == "source_hash": spec = spec.model_copy(update={"source_ingress_sha256": "f"*64})
    elif change == "revision": spec = spec.model_copy(update={"origin": spec.origin.model_copy(update={"claim_revision": 9})})
    else: spec = spec.model_copy(update={"source": spec.source.model_copy(update={"tenant_id": "other-tenant"})})
    with pytest.raises(InitiativeMandateDenied): f.auth.bind_work(spec)
    # The ordinary loader/contract keeps rejecting an owner-shaped native source.
    with pytest.raises(ValueError): PrivateWorkSource.model_validate(work.spec.source.model_dump(mode="json"))
    loader = _SourceTurnLoader(f.store._engine,
        SimpleNamespace(scope=f.control._scope, authorize_turn=f.service.validate_authority))
    with pytest.raises(Exception, match="not an owner"):
        loader(work.spec.source.model_dump(mode="json"))


@pytest.mark.parametrize("interruption", ["stop", "revoke", "file_disabled", "binding_revoked"])
def test_stop_or_revocation_blocks_next_dispatch_and_late_completion(native_digital, interruption):
    from eva.adapters.postgres.current_catalog import metadata
    f = native_digital
    _, work = admitted(f)
    call = started(f, work)
    guard, turn = f.auth.bind_work(work.spec)
    f.store.authorize_tool_dispatch(call)
    if interruption == "stop": f.control.stop(f.claim.intent_id, now=NOW)
    elif interruption == "revoke": f.auth.revoke()
    elif interruption == "file_disabled":
        f.policy["enabled"] = False; f.path.write_text(json.dumps(f.policy))
    else:
        table = metadata.tables["eva_core.principal_bindings"]
        with f.store._engine.begin() as conn:
            conn.execute(sa.update(table).where(table.c.tenant_id == f.control._authority.tenant_id,
                table.c.principal_binding_id == f.control._binding).values(status="revoked"))
    with pytest.raises((InitiativeMandateDenied, RuntimeError)): guard.check(turn)
    with pytest.raises(DelegatedWorkConflict): f.store.authorize_tool_dispatch(call)
    assert not f.store.execution_authority_current(call)
    f.store.record_success(call, result(call), now=NOW)
    state = f.auth.timeline.load(f.claim.thread_id)
    assert state.phase == ("personal_stopped" if interruption == "stop" else "initiative_blocked")
    assert not any(e.event_type == "PERSONAL_INITIATIVE_COMPLETED"
                   for e in f.auth.timeline.events(f.claim.thread_id))


def test_failed_work_preserves_failure_and_never_completes_parent(native_digital):
    f = native_digital
    _, work = admitted(f)
    call = started(f, work)
    f.store.record_failure(call, usage=DelegatedWorkUsage.unmeasured(), error_code="internal_error",
        error_fingerprint="e"*64, ambiguous=True, now=NOW)
    assert f.auth.timeline.load(f.claim.thread_id).phase == "initiative_blocked"
    assert f.control.page(now=NOW)["cards"][0]["status"] != "done"


def test_no_model_route_is_used_when_native_owner_stop_precedes_claim(native_digital):
    f = native_digital
    _, work = admitted(f)
    f.control.stop(f.claim.intent_id, now=NOW)
    assert f.store.claim_next(worker_id="synthetic", release=f.profile.release, now=NOW) is None
    assert f.store.load(work.spec.work_id, **f.service._owner).state == "needs_help"
    assert f.auth.timeline.load(f.claim.thread_id).phase == "personal_stopped"


def test_existing_runner_completes_native_programming_and_parent_without_owner_ingress(native_digital, tmp_path, monkeypatch):
    from eva.application.conversation import ConversationActionCall, ConversationModelResult, ConversationModelUsage, IdentityProfile
    from eva.adapters.projection.hindsight import HindsightMemoryEngine
    import eva.adapters.postgres.programming_actions as programming
    f = native_digital
    _, work = admitted(f)
    path = tmp_path / "created.py"
    dispatched = []
    class Client:
        def request(self, session, workspace, operation, **kwargs):
            assert current_initiative() is not None
            current_initiative().check(current_initiative().turn)
            if operation == "status": return {"idle": True}
            name = kwargs["name"]; args = kwargs["arguments"]
            dispatched.append(name)
            if name == "write_file": path.write_text(args["content"]); return {"isError": False}
            assert name == "read_file"
            return {"isError": False, "text": path.read_text()}
        def close(self, session): pass
    monkeypatch.setattr(programming, "configured_programming_workspace", lambda: Client())
    class Memory:
        def recall_for_presence(self, query): return HindsightMemoryEngine._empty_presence(query)
    class Model:
        def generate(self, turn, identity, memory, recent_messages, **kwargs):
            assert turn.conversation_id.startswith("initiative-task:")
            assert current_initiative() is not None
            prior = kwargs.get("completed_action")
            if prior is None:
                call = ConversationActionCall("write", "write_programming_file",
                    {"path": "/workspace/created.py", "content": "VALUE = 2\n", "mode": "rewrite"})
            elif prior.call.name == "write_programming_file":
                call = ConversationActionCall("read", "use_programming_tool",
                    {"tool_name": "read_file", "arguments_json": '{"path":"/workspace/created.py"}'})
            else:
                assert prior.output["programming_result"]["text"] == "VALUE = 2\n"
                call = None
            return ConversationModelResult(None if call else "Created source and read back VALUE = 2.",
                "synthetic-test", call, usage=ConversationModelUsage.measured(
                    input_tokens=30, output_tokens=10, tool_calls=int(call is not None)))
    def no_owner_loader(source): pytest.fail("native origin must not be loaded as owner ingress")
    runner = DelegatedDigitalRunner(f.store, f.profile, source_loader=no_owner_loader,
        memory=Memory(), model=Model(), identity=IdentityProfile("Eva", "Helpful", ("Be honest",)),
        connector_tools=(), clock=lambda: NOW)
    worker = DelegatedWorkWorker(f.store, runner, worker_id="synthetic-native",
        release=f.profile.release, clock=lambda: NOW)
    assert asyncio.run(worker.run_once()) == "succeeded"
    assert current_initiative() is None  # The next ordinary owner turn inherits no native authority.
    assert dispatched == ["write_file", "read_file"]
    artifact = f.control.artifact(f.claim.intent_id, now=NOW)
    assert artifact["content"] == "Created source and read back VALUE = 2."
    assert f.store.load(work.spec.work_id, **f.service._owner).result.output == artifact["content"]
