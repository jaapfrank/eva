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
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
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


def test_completed_native_initiative_uses_same_private_outcome_memory(native_digital):
    from eva.application.delegated_work_memory import outcome_document
    from eva.bootstrap.delegated_work_memory import DelegatedWorkMemory
    f = native_digital
    _, work = admitted(f)
    call = started(f, work)
    saved = f.store.record_success(call, result(call), now=NOW)
    selected = f.store.next_memory_outcome(**f.service._owner, now=NOW, completed_since=NOW)
    assert selected == saved
    source = f.auth.work_memory_source(saved, now=NOW)
    document = outcome_document(saved, source, ingress_sha256=canonical_sha256(
        PostgresConversationJournal._ingress_data(source)))
    assert "Aanleiding (eigen initiatief van Eva)" in document.content
    assert "Oorspronkelijke vraag (directe gebruiker)" not in document.content
    assert document.principal_id == str(f.control._owner)
    assert document.trust == "model_inference_unconfirmed"
    assert saved.result.output in document.content
    from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
    outbox = PostgresMemoryOutbox(f.store._engine, tenant_id=source.tenant_id,
        installation_id=source.installation_id)
    submitted = []
    def submit(document):
        submitted.append(document)
        outbox.enqueue(document)
    projection = DelegatedWorkMemory(f.store._engine, f.service, submit, clock=lambda: NOW)
    assert projection.run_once() == "queued"
    assert submitted == [document] and len(outbox.pending()) == 1
    restarted = DelegatedWorkMemory(f.store._engine, f.service, submit, clock=lambda: NOW)
    assert restarted.run_once() == "idle"
    assert submitted == [document] and len(outbox.pending()) == 1
    assert f.store.next_memory_outcome(**f.service._owner, now=NOW, completed_since=NOW) is None
    # Remembering the completed result does not request a new execution grant.
    f.auth.revoke()
    assert f.auth.work_memory_source(saved, now=NOW) == source


@pytest.mark.parametrize("change", ["owner", "source_hash", "expired", "stop", "binding"])
def test_native_memory_cannot_forge_origin_or_retain_late_success(native_digital, change):
    from eva.adapters.postgres.current_catalog import metadata
    f = native_digital
    _, work = admitted(f)
    call = started(f, work)
    if change == "stop":
        f.control.stop(f.claim.intent_id, now=NOW)
    saved = f.store.record_success(call, result(call), now=NOW)
    if change == "owner":
        saved = replace(saved, spec=saved.spec.model_copy(update={"owner_principal_id": "other-owner"}))
    elif change == "source_hash":
        saved = replace(saved, spec=saved.spec.model_copy(update={"source_ingress_sha256": "f"*64}))
    elif change == "expired":
        saved = replace(saved, retention_until=NOW)
    elif change == "binding":
        table = metadata.tables["eva_core.principal_bindings"]
        with f.store._engine.begin() as conn:
            conn.execute(sa.update(table).where(table.c.tenant_id == f.control._authority.tenant_id,
                table.c.principal_binding_id == f.control._binding).values(status="revoked"))
    with pytest.raises(InitiativeMandateDenied):
        f.auth.work_memory_source(saved, now=NOW)


def test_failed_native_initiative_is_remembered_as_failure(native_digital):
    from eva.application.delegated_work_memory import outcome_document
    f = native_digital
    _, work = admitted(f)
    call = started(f, work)
    saved = f.store.record_failure(call, usage=DelegatedWorkUsage.unmeasured(),
        error_code="internal_error", error_fingerprint="e"*64, ambiguous=False, now=NOW)
    source = f.auth.work_memory_source(saved, now=NOW)
    document = outcome_document(saved, source, ingress_sha256=canonical_sha256(
        PostgresConversationJournal._ingress_data(source)))
    assert "Geen bevestigd eindresultaat" in document.content
    assert "Niet als succes onthouden of automatisch herhalen" in document.content
    assert "internal_error" in document.content


@pytest.fixture
def native_saved_artifact(native_digital, tmp_path, monkeypatch):
    """Real local gateway+journals, synthetic native task; no model transport."""
    from eva.application.conversation import ConversationActionCall, ConversationTurn
    from eva.application.initiative_execution import initiative_execution
    from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
    from eva.providers.procedure_reference_actions import ProcedureReferenceActions
    import eva.providers.procedure_reference_actions as module
    from eva.providers.mcp_owner_authority import McpOwnerAuthority
    from tests.postgres.test_procedure_reference_host import workspace_gateway
    f = native_digital
    # Keep production clocks honest; this entire fixture's authority is historical synthetic.
    monkeypatch.setattr(module, "datetime", __import__("eva.adapters.postgres.initiative_execution", fromlist=["datetime"]).datetime)
    with workspace_gateway("save_workspace_document") as (factory, handler):
        adapter = factory(); asyncio.run(adapter.verify_catalog())
        planner = adapter.planner()
        config_sha = planner._specs["save_workspace_document"].configuration_sha256
        profile = replace(f.profile, capability_ids=("apply_reviewed_component_improvement",
            "read_own_component_source"))
        f.profile = profile; f.service.digital_profile = profile
        f.policy["digital_work"] = {"worker_profile_release_sha256": profile.release.worker_profile_release_sha256,
            "capability_ids": list(profile.capability_ids)}
        path = "/synthetic/own-helper.py"
        cap = f.policy["capabilities"][0]
        cap.update(action_name="save_workspace_document", configuration_sha256=config_sha,
                   tool_name="save", observation_tool_name="read")
        cap["execute_schema"] = {"type": "object", "properties": {
            "path": {"type": "string", "pattern": "/synthetic/own-helper.*[.]py"},
            "content": {"type": "string", "maxLength": 16384}},
            "required": ["path", "content"], "additionalProperties": False}
        cap["observe_schema"] = {"type": "object", "properties": {
            "path": {"type": "string", "pattern": "/synthetic/own-helper.*[.]py"}},
            "required": ["path"], "additionalProperties": False}
        f.path.write_text(json.dumps(f.policy))
        mandate_path = tmp_path / "owner-connector.json"
        mandate_path.write_text(json.dumps({"contract": "OwnerConnectorMandate/v1", "mandate_id": "synthetic-owner",
            "tenant_id": f.claim.tenant_id, "installation_id": f.claim.installation_id,
            "owner_principal_id": f.claim.principal_id, "issued_at": "2020-01-01T00:00:00Z",
            "authorization_source": "explicit synthetic owner authority", "enabled": True,
            "public_https": True, "local_networks": []})); mandate_path.chmod(0o600)
        authority = McpOwnerAuthority(f.control._engine, str(mandate_path))
        actions = ProcedureReferenceActions(f.control._engine, authority=authority, gateway_factory=factory,
            clock=lambda: NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), native_authority_loader=lambda: f.auth)
        tools = PostgresExternalConversationTools(f.control._engine, planner, adapter,
                                                 authorize_turn=authority.authorize_turn)
        _, work = admitted(f)
        active, turn = f.auth.bind_work(work.spec)
        baseline = "VALUE = 1\n"
        until = (NOW + timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Existing owner-saved baseline is the actual supported improvement source.
        owner_turn = replace(turn, conversation_id="owner-baseline", message_id="owner-baseline-message")
        PostgresConversationJournal(f.control._engine, authority.scope).admit(owner_turn)
        assert tools.execute(owner_turn, ConversationActionCall("owner-save", "save_workspace_document",
            {"path": path, "content": baseline})).output["status"] == "verified"
        designation = actions.execute(owner_turn, ConversationActionCall("owner-designate", "designate_reusable_procedure",
            {"document_path": path, "use_when": "Use own useful helper", "retention_until": until}), step_index=1)
        ref = designation.output["reference"]
        target = {"kind": "saved_artifact", "procedure_reference_id": ref["document_id"],
            "scope": {key: getattr(turn, key) for key in ("tenant_id", "installation_id", "principal_id", "project_id")},
            "artifact_path": ref["path"], "content_sha256": ref["content_sha256"],
            "receipt_sha256": ref["receipt_sha256"], "source_call_id": ref["source_call_id"],
            "source_message_id": ref["source_message_id"], "tool_configuration_sha256": ref["tool_configuration_sha256"],
            "retention_until": ref["retention_until"]}
        with initiative_execution(active):
            assert actions.resolve_improvement_source(turn, ref["document_id"])["baseline_content"] == baseline
        call = started(f, work)
        saved = f.store.record_success(call, result(call), now=NOW)
        candidate = "VALUE = 2\n"
        candidate_sha = hashlib.sha256(candidate.encode()).hexdigest()
        publication = {"contract": "EvaSavedArtifactPublication/v1", "review_id": "a" * 64,
            "source_target": target, "baseline_sha256": ref["content_sha256"], "candidate_sha256": candidate_sha,
            "destination_path": "/synthetic/own-helper.improved-" + candidate_sha + ".py", "improvement_description": "Synthetic independently accepted change"}
        yield SimpleNamespace(f=f, turn=turn, actions=actions, tools=tools, handler=handler,
            work=saved, publication=publication, candidate=candidate, baseline=baseline, ref=ref,
            authority=authority, namespace_path=path)


def test_native_reviewed_application_then_later_ordinary_resolve_preserves_native_source(native_saved_artifact, monkeypatch):
    from eva.application.initiative_execution import initiative_execution
    from eva.application.conversation import ConversationActionCall
    from eva.operations.saved_artifact_application import apply_in_core, receipt_confirms_application
    from eva.application.procedure_references import ProcedureScope
    f = native_saved_artifact; auth = f.f.auth
    with pytest.raises(InitiativeMandateDenied):
        auth.bind_work(f.work.spec)  # Finished work still cannot resume general tools.
    from eva.application.conversation import ConversationConflict
    with pytest.raises(ConversationConflict, match="not an owner connector instruction"):
        _SourceTurnLoader(f.f.control._engine, f.authority)(f.work.spec.source.model_dump(mode="json"))
    guard, turn = auth.bind_reviewed_saved_application(f.work.spec.work_id, f.publication, f.candidate)
    from eva.bootstrap.runtime import RuntimeSettings
    from eva.providers.procedure_reference_actions import ProcedureReferenceProviderFactory
    from eva.operations.saved_artifact_application import runtime_apply
    settings = SimpleNamespace(database_dsn=f.f.control._engine.url.render_as_string(hide_password=False),
        dashboard_tenant_id=str(f.f.control._authority.tenant_id),
        dashboard_installation_id=str(f.f.control._authority.installation_id))
    monkeypatch.setattr(RuntimeSettings, "from_environment", lambda: settings)
    monkeypatch.setattr(ProcedureReferenceProviderFactory, "load_settings", lambda: None)
    monkeypatch.setattr(ProcedureReferenceProviderFactory, "build", lambda *args: SimpleNamespace(external_tools=(f.actions,)))
    receipt = runtime_apply({"task_id": f.work.spec.work_id, "publication": f.publication,
        "candidate_content": f.candidate, "scope_sha256": canonical_sha256({key: getattr(turn, key)
            for key in ("tenant_id", "installation_id", "principal_id")})})
    with initiative_execution(guard):
        assert not guard.action_allowed("use_programming_tool")
        with pytest.raises(InitiativeMandateDenied):
            guard.authorize_procedure(ConversationActionCall("escape", "designate_reusable_procedure",
                {"document_path": "other.py", "use_when": "Other", "retention_until": f.ref["retention_until"]}))
    assert receipt_confirms_application(receipt, f.publication, f.work.spec.work_id)
    assert f.handler.documents[f.namespace_path] == f.baseline
    assert f.handler.documents[f.publication["destination_path"]] == f.candidate
    # No fabricated owner ingress for either native save or terminal application.
    assert auth.timeline.events("conversation:" + turn.message_id) == ()
    auth.revoke()  # Historical source remains readable, never grants old execution.
    later = replace(turn, conversation_id="later-owner", message_id="later-owner-message", text="Use the useful helper")
    journal = PostgresConversationJournal(f.f.control._engine, f.authority.scope); journal.admit(later)
    scope = ProcedureScope(turn.tenant_id, turn.installation_id, turn.principal_id, turn.project_id)
    host = f.actions._host(later)
    assert host.source_active(scope, turn.message_id)
    assert host.load_save(scope, receipt["reference"]["source_call_id"]).admission["source_message_id"] == turn.message_id
    resolved = f.actions.execute(later, ConversationActionCall("later-resolve", "resolve_reusable_procedure",
        {"document_id": receipt["reference"]["document_id"]})).output
    assert resolved["status"] == "resolved" and resolved["references"][0]["untrusted_content"] == f.candidate
    with pytest.raises(InitiativeMandateDenied):
        guard.check(turn)


@pytest.mark.parametrize("change", ["owner", "project", "content", "destination"])
def test_native_reviewed_binding_rejects_changed_package_before_effect(native_saved_artifact, change):
    from copy import deepcopy
    f = native_saved_artifact
    publication = deepcopy(f.publication)
    if change == "owner": publication["source_target"]["scope"]["principal_id"] = "someone-else"
    elif change == "project": publication["source_target"]["scope"]["project_id"] = "other-project"
    elif change == "content": publication["candidate_sha256"] = "b" * 64
    else: publication["destination_path"] = "other.py"
    if change in {"owner", "project"}:
        target = publication["source_target"]
        target["procedure_reference_id"] = "procedure-reference:" + canonical_sha256({
            "scope": target["scope"], "receipt": target["receipt_sha256"]})
    before = len(f.handler.calls)
    with pytest.raises((InitiativeMandateDenied, ValueError)):
        f.f.auth.bind_reviewed_saved_application(f.work.spec.work_id, publication, f.candidate)
    assert len(f.handler.calls) == before


@pytest.mark.parametrize("change", ["stop", "result", "retention", "mandate"])
def test_terminal_guard_rechecks_completion_and_current_stop_before_every_dispatch(native_saved_artifact, change, monkeypatch):
    from eva.application.orchestration import ToolRequest
    f = native_saved_artifact
    guard, turn = f.f.auth.bind_reviewed_saved_application(f.work.spec.work_id, f.publication, f.candidate)
    if change == "mandate":
        f.f.auth.revoke()
    elif change == "stop":
        assert f.f.control.stop(f.f.claim.intent_id, now=NOW)["status"] == "stopped"
    elif change == "retention":
        # The admitted retention is immutable; advance the actual guard clock.
        from datetime import datetime
        import eva.adapters.postgres.initiative_execution as module
        expired_at = datetime.fromisoformat(f.publication["source_target"]["retention_until"].replace("Z", "+00:00")) + timedelta(seconds=1)
        assert expired_at < f.work.retention_until
        class ExpiredTime(datetime):
            @classmethod
            def now(cls, tz=None): return expired_at
        monkeypatch.setattr(module, "datetime", ExpiredTime)
    else:
        with f.f.control._engine.begin() as conn:
            f.f.store._scope(conn)
            conn.execute(sa.update(delegated_work_runs).where(
                delegated_work_runs.c.work_id == f.work.spec.work_id).values(
                    result_sha256=bytes.fromhex("f" * 64)))
    request = ToolRequest("test", "operation", "checkpoint", "save",
        {"path": f.publication["destination_path"], "content": f.candidate}, {},
        observation_tool_name="read", observation_arguments={"path": f.publication["destination_path"]},
        tool_configuration_sha256=f.ref["tool_configuration_sha256"])
    before = len(f.handler.calls)
    from eva.application.conversation import ConversationStopped
    from eva.adapters.postgres.delegated_work import DelegatedWorkStoreError
    expected = (ConversationStopped if change == "stop" else
                DelegatedWorkStoreError if change == "result" else InitiativeMandateDenied)
    with pytest.raises(expected):
        guard.authorize_request(request, phase="execute")
    assert len(f.handler.calls) == before


def test_terminal_guard_rejects_other_reads_and_original_overwrite(native_saved_artifact):
    from eva.application.orchestration import ToolRequest
    f = native_saved_artifact
    guard, _ = f.f.auth.bind_reviewed_saved_application(f.work.spec.work_id, f.publication, f.candidate)
    for path, content, phase in ((f.namespace_path, f.baseline, "execute"),
                                 ("other.py", f.candidate, "observe"),
                                 (f.publication["destination_path"], "changed", "execute")):
        request = ToolRequest("test", "operation", "checkpoint", "save", {"path": path, "content": content}, {},
            observation_tool_name="read", observation_arguments={"path": path},
            tool_configuration_sha256=f.ref["tool_configuration_sha256"])
        with pytest.raises(InitiativeMandateDenied, match="accepted package"):
            guard.authorize_request(request, phase=phase)


@pytest.mark.parametrize("when", ["before_bind", "after_bind", "after_save"])
def test_real_owner_stop_blocks_deferred_native_effects_and_keeps_completion(native_saved_artifact, when):
    from eva.application.initiative_execution import initiative_execution
    from eva.application.conversation import ConversationStopped
    from eva.operations.saved_artifact_application import apply_in_core
    f = native_saved_artifact
    previous = f.f.auth.timeline.events(f.f.claim.thread_id)
    before = len(f.handler.calls)
    if when == "before_bind":
        assert f.f.control.stop(f.f.claim.intent_id, now=NOW)["status"] == "stopped"
        with pytest.raises(ConversationStopped):
            f.f.auth.bind_reviewed_saved_application(f.work.spec.work_id, f.publication, f.candidate)
    else:
        guard, turn = f.f.auth.bind_reviewed_saved_application(f.work.spec.work_id, f.publication, f.candidate)
        original = f.tools.execute
        def execute(*args, **kwargs):
            receipt = original(*args, **kwargs)
            assert f.f.control.stop(f.f.claim.intent_id, now=NOW)["status"] == "stopped"
            return receipt
        if when == "after_bind":
            f.f.control.stop(f.f.claim.intent_id, now=NOW)
        else:
            f.tools.execute = execute
        with initiative_execution(guard), pytest.raises(ConversationStopped):
            apply_in_core(f.publication, f.candidate, f.work.spec.work_id, engine=f.f.control._engine,
                procedures=f.actions, tools=f.tools, source_turn=turn, check_ready=lambda _: "ready")
    assert f.f.auth.timeline.events(f.f.claim.thread_id) == previous
    assert f.f.control.page(now=NOW)["cards"][0]["status"] == "done"
    if when != "after_save":
        assert len(f.handler.calls) == before
    else:
        from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
        store = PostgresMemoryOutbox(f.f.control._engine, tenant_id=f.turn.tenant_id, installation_id=f.turn.installation_id)
        refs, _ = store.list_sources(principal_id=f.turn.principal_id, project_id=None)
        assert len(refs) == 1  # Original only; saved candidate has no new designation.


def test_original_reference_erasure_between_save_and_designation_blocks_derived_reference(native_saved_artifact):
    from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
    from eva.application.initiative_execution import initiative_execution
    from eva.operations.saved_artifact_application import apply_in_core
    f = native_saved_artifact
    guard, turn = f.f.auth.bind_reviewed_saved_application(f.work.spec.work_id, f.publication, f.candidate)
    store = PostgresMemoryOutbox(f.f.control._engine, tenant_id=turn.tenant_id, installation_id=turn.installation_id)
    original = f.tools.execute
    def execute(*args, **kwargs):
        receipt = original(*args, **kwargs)
        store.erase_content(principal_sha256=hashlib.sha256(turn.principal_id.encode()).hexdigest(),
            document_id=f.ref["document_id"], operation_sha256="b" * 64, erased_at=NOW)
        return receipt
    f.tools.execute = execute
    with initiative_execution(guard), pytest.raises(InitiativeMandateDenied, match="erased"):
        apply_in_core(f.publication, f.candidate, f.work.spec.work_id, engine=f.f.control._engine,
            procedures=f.actions, tools=f.tools, source_turn=turn, check_ready=lambda _: "ready")
    assert store.resolve_source(f.ref["document_id"], principal_id=turn.principal_id, project_id=None) is None
    refs, _ = store.list_sources(principal_id=turn.principal_id, project_id=None)
    assert refs == ()



def test_legacy_native_completion_without_deferred_application_keeps_already_completed(native_digital):
    f = native_digital
    _, work = admitted(f)
    call = started(f, work)
    f.store.record_success(call, result(call), now=NOW)
    original = f.auth.timeline.events(f.claim.thread_id)
    assert f.control.stop(f.claim.intent_id, now=NOW)["status"] == "already_completed"
    assert f.auth.timeline.events(f.claim.thread_id) == original
    turn = f.auth.work_memory_source(f.store.load(work.spec.work_id, **f.service._owner), now=NOW)
    PostgresConversationJournal(f.control._engine, f.control._scope).ensure_running(turn)
