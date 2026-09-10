from datetime import UTC, datetime, timedelta
from dataclasses import replace
import pytest
import sqlalchemy as sa
from eva.adapters.postgres.owner_initiatives import PostgresOwnerInitiatives
from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.adapters.postgres.conversation_actions import (
    PostgresPersonalInterestStore, PostgresPersonalPrototypeStore,
)
from eva.adapters.postgres.operational_timeline import CheckpointConflict
from eva.adapters.postgres.current_catalog import metadata
from eva.application.free_will import FreeWillDecision, PersonalInitiativeProposal
from eva.application.personal_interests import PersonalInterestExploration, PersonalPrototype
from tests.postgres.test_delegated_work_store import _seed_authority, _id
from tests.postgres.test_sensory_admission import _event

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def test_owner_can_read_result_and_stop_claimed_work_without_resurrection(postgres_engine):
    label = "initiative-owner-control"
    authority, _, binding = _seed_authority(postgres_engine, label)
    owner = _id(label, "principal")
    tenant, installation = "initiative-owner-external", "home"
    source = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant,
        installation_id=installation, initiative_principal_id=str(owner))
    def control(who=owner):
        return PostgresOwnerInitiatives(postgres_engine, tenant_id=tenant,
            installation_id=installation, owner=who, binding=binding, authority_scope=authority)
    decision = FreeWillDecision("silence", "Eigen plan", 0, "fixture",
        initiative=PersonalInitiativeProposal("Gezinsplan", "Voor het gezin", "Maak een gezinsplan"))
    def adopt(name, at):
        source.retain(replace(_event(name), occurred_at=at))
        claim = source.mark_model_started(source.claim_next(now=at), now=at)
        source.complete(claim, replace(decision, initiative=PersonalInitiativeProposal(
            "Gezinsplan " + name, "Voor het gezin", "Maak een gezinsplan")), now=at)
        return source._timeline.events(claim.thread_id)[-1].payload["data"]["initiative"]["intent_id"]
    intent = adopt("first", NOW)
    assert control().page(now=NOW)["cards"][0]["intent_id"] == intent
    interest = PostgresPersonalInterestStore(postgres_engine)
    claim = interest.claim_next(tenant_id=tenant, installation_id=installation, now=NOW)
    assert claim is not None
    assert control().stop(intent, now=NOW)["status"] == "stopped"
    assert control().stop(intent, now=NOW)["status"] == "stopped"
    with pytest.raises(CheckpointConflict):
        interest.complete(claim, PersonalInterestExploration("Late result", "fixture"), now=NOW)
    assert interest.claim_next(tenant_id=tenant, installation_id=installation, now=NOW) is None
    assert control().page(now=NOW)["cards"][0]["status"] == "stopped"
    at = NOW + timedelta(seconds=1)
    next_intent = adopt("second", at)
    assert next_intent != intent
    claim = interest.claim_next(tenant_id=tenant, installation_id=installation, now=at)
    completed = interest.complete(claim, PersonalInterestExploration("Planinhoud", "fixture"), now=at)
    interest.mark_projected(completed, now=at)
    prototypes = PostgresPersonalPrototypeStore(postgres_engine)
    prototype_claim = prototypes.claim_next(tenant_id=tenant, installation_id=installation, now=at)
    artifact = PersonalPrototype("Gezinsplan", "gezinsplan.md", "text/markdown", "# Plan\nSamen iets doen.", "fixture")
    prototypes.complete(prototype_claim, artifact, now=at)
    assert control().artifact(next_intent, now=at) == {
        "filename": artifact.filename, "content": artifact.content, "sha256": artifact.content_sha256}
    assert any(card["has_artifact"] for card in control().page(now=at)["cards"])
    with pytest.raises(RuntimeError, match="binding is not current"):
        control(_id(label, "other")).artifact(next_intent, now=at)
    with pytest.raises(LookupError):
        control().stop("personal-interest:" + "0"*64, now=at)
    table = metadata.tables["eva_core.principal_bindings"]
    with postgres_engine.begin() as conn:
        conn.execute(sa.update(table).where(table.c.tenant_id == authority.tenant_id,
            table.c.principal_binding_id == binding).values(status="revoked"))
    with pytest.raises(RuntimeError, match="binding is not current"):
        control().stop(next_intent, now=at)


def test_stopped_initiative_context_is_owner_scoped_and_same_title_cannot_restart(postgres_engine):
    label = "initiative-no-replay"
    authority, _, binding = _seed_authority(postgres_engine, label)
    owner = _id(label, "principal")
    tenant, installation = "initiative-no-replay-external", "home"
    source = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant,
        installation_id=installation, initiative_principal_id=str(owner))
    decision = FreeWillDecision("silence", "Private idea", 0, "fixture",
        initiative=PersonalInitiativeProposal("A useful plan", "Known interest", "Create a proposal"))
    source.retain(replace(_event("no-replay-first"), occurred_at=NOW))
    claim = source.claim_next(now=NOW)
    source.complete(claim, decision, now=NOW)
    control = PostgresOwnerInitiatives(postgres_engine, tenant_id=tenant,
        installation_id=installation, owner=owner, binding=binding, authority_scope=authority)
    intent_id = control.page(now=NOW)["cards"][0]["intent_id"]
    control.stop(intent_id, now=NOW + timedelta(seconds=1))
    def runtime_role(connection):
        connection.exec_driver_sql("SET LOCAL ROLE eva_dbos")
    sa.event.listen(postgres_engine, "begin", runtime_role)
    try:
        assert source.recent_initiatives() == ({"title": "A useful plan",
            "question": "Create a proposal", "phase": "personal_stopped"},)
        other = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant,
            installation_id=installation, initiative_principal_id=str(_id(label, "other")))
        assert other.recent_initiatives() == ()
    finally:
        sa.event.remove(postgres_engine, "begin", runtime_role)
    source.retain(replace(_event("no-replay-next"), occurred_at=NOW + timedelta(seconds=2)))
    claim = source.claim_next(now=NOW + timedelta(seconds=2))
    source.complete(claim, replace(decision, initiative=PersonalInitiativeProposal(
        " A USEFUL PLAN ", "Another trigger", "Do it again")), now=NOW + timedelta(seconds=2))
    cards = control.page(now=NOW + timedelta(seconds=2))["cards"]
    assert len(cards) == 1 and cards[0]["status"] == "stopped"


@pytest.fixture
def costless_native(postgres_engine, tmp_path, monkeypatch):
    import json
    from eva.adapters.postgres import initiative_execution as native
    label = "costless-" + tmp_path.name
    authority, _, binding = _seed_authority(postgres_engine, label)
    owner = _id(label, "principal")
    tenant, installation = "native-" + label, "home"
    source = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant,
        installation_id=installation, initiative_principal_id=str(owner))
    source.retain(replace(_event(label), occurred_at=NOW))
    sensory_claim = source.claim_next(now=NOW)
    source.complete(sensory_claim, FreeWillDecision("silence", "Relevant idea", 0, "fixture",
        initiative=PersonalInitiativeProposal("Useful contribution", "Known owner context", "Prepare useful material")), now=NOW)
    store = PostgresPersonalInterestStore(postgres_engine)
    claim = store.claim_next(tenant_id=tenant, installation_id=installation, now=NOW)
    control = PostgresOwnerInitiatives(postgres_engine, tenant_id=tenant,
        installation_id=installation, owner=owner, binding=binding, authority_scope=authority)
    class FixedTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(native, "datetime", FixedTime)
    args = {"type": "object", "properties": {"path": {"type": "string", "const": str(tmp_path / "result.txt")},
        "content": {"type": "string", "maxLength": 1000}}, "required": ["path", "content"], "additionalProperties": False}
    policy = {"contract": "EvaCostlessInitiativeMandate/v1", "mandate_id": "owner-costless-v1",
        "owner_principal_id": str(owner), "tenant_scope": str(control._scope.tenant_id),
        "installation_scope": str(control._scope.installation_id), "enabled": True,
        "authorization_source": "explicit owner test mandate", "capabilities": [{
            "action_name": "save_private_result", "configuration_sha256": "a" * 64,
            "tool_name": "private.write", "observation_tool_name": "private.read",
            "new_cost_eur_cents": 0, "new_obligation": False,
            "data_scope": "owner_private_local", "communication_scope": "none",
            "cost_evidence": "local filesystem, no metered provider or external recipient",
            "execute_schema": args, "observe_schema": args}]}
    path = tmp_path / "mandate.json"
    path.write_text(json.dumps(policy)); path.chmod(0o600)
    auth = native.PostgresInitiativeAuthority(control, str(path))
    return auth, claim, store, control, source, path, policy


@pytest.mark.parametrize("content", [
    "Boodschappen voorbereiden: vergelijk voorraad voordat je iets koopt.",
    "Creatief gezinsidee: laat ieder een eigen spelregel toevoegen.",
    "Ondernemingsverkenning: bespreek eerst probleem en doelgroep; geen inkomstenclaim.",
])
def test_native_executes_profiled_tool_and_recovery_reads_same_result(costless_native, content):
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from eva.application.initiative_execution import initiative_execution
    from eva.application.conversation import ConversationActionCall, ConversationToolPreparation, GroundedReply
    from eva.application.orchestration import ToolDispatchResult, ToolProofObservation
    from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    auth, claim, store, control, source, path, policy = costless_native
    guard, turn = auth.bind(claim)
    output_path = policy["capabilities"][0]["execute_schema"]["properties"]["path"]["const"]
    arguments = {"path": output_path, "content": content}
    class Planner:
        action_names = ("save_private_result",)
        async def prepare(self, turn, call):
            return ConversationToolPreparation(tool_name="private.write", arguments=call.arguments,
                expected_proof={"content": call.arguments["content"]},
                observation_tool_name="private.read", observation_arguments=call.arguments,
                tool_configuration_sha256="a" * 64, receipt_output={})
    class Provider:
        writes = 0
        async def execute(self, request):
            self.writes += 1
            Path(request.arguments["path"]).write_text(request.arguments["content"])
            return ToolDispatchResult(True, "local-result", {})
        async def observe(self, request, dispatch):
            text = Path(request.observation_arguments["path"]).read_text()
            return ToolProofObservation("verified", {"content": text}, {"readback": True})
    provider = Provider()
    journal = PostgresConversationJournal(auth.owner._engine, auth.owner._scope)
    journal.admit(turn)
    tools = PostgresExternalConversationTools(auth.owner._engine, Planner(), provider)
    call = ConversationActionCall("native-call", "save_private_result", arguments)
    with initiative_execution(guard):
        receipt = tools.execute(turn, call)
        assert receipt.output["status"] == "verified"
    # A new context is reconstructed from durable intent and mandate, not model text.
    recovered, recovered_turn = auth.bind(claim)
    with initiative_execution(recovered):
        assert tools.execute(recovered_turn, call) == receipt
    assert provider.writes == 1 and Path(output_path).read_text() == content
    reply = GroundedReply(turn.conversation_id, turn.message_id, content, (), turn.occurred_at, "fixture")
    store.complete_native(claim, reply, now=NOW)
    assert control.page(now=NOW)["cards"][0]["status"] == "done"
    assert control.artifact(claim.intent_id, now=NOW)["content"] == content
    assert PostgresPersonalPrototypeStore(auth.owner._engine).claim_next(
        tenant_id=claim.tenant_id, installation_id=claim.installation_id, now=NOW) is None


def test_native_mandate_denies_unknown_cost_destination_and_rebinding(costless_native):
    import json
    from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
    from eva.application.orchestration import ToolRequest
    auth, claim, store, control, source, path, policy = costless_native
    guard, turn = auth.bind(claim)
    assert not guard.action_allowed("adopt_personal_interest")
    assert not guard.action_allowed("register_connector")
    assert not guard.action_allowed("send_message")
    request = ToolRequest("tools:test", "operation:test", "checkpoint:test", "private.write",
        {"path": "/private/someone-else", "content": "secret"}, {},
        observation_tool_name="private.read", observation_arguments={}, tool_configuration_sha256="a"*64)
    with pytest.raises(InitiativeMandateDenied, match="outside"):
        guard.authorize_request(request, phase="execute")
    with pytest.raises(InitiativeMandateDenied, match="differs"):
        guard.check(replace(turn, text="new instructions"))
    policy["capabilities"][0]["new_cost_eur_cents"] = None
    path.write_text(json.dumps(policy))
    with pytest.raises(InitiativeMandateDenied, match="zero-cost"):
        guard.check(turn)
    policy["capabilities"][0]["new_cost_eur_cents"] = 1
    path.write_text(json.dumps(policy))
    with pytest.raises(InitiativeMandateDenied, match="zero-cost"):
        guard.check(turn)
    policy["capabilities"][0]["new_cost_eur_cents"] = 0
    policy["capabilities"][0]["execute_schema"]["properties"]["path"] = {"type": "string"}
    path.write_text(json.dumps(policy))
    with pytest.raises(InitiativeMandateDenied, match="revision changed"):
        guard.check(turn)


def test_native_stop_feedback_and_revocation_survive_new_context(costless_native):
    from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    from eva.application.conversation import ConversationStopped
    auth, claim, store, control, source, path, policy = costless_native
    guard, turn = auth.bind(claim)
    journal = PostgresConversationJournal(auth.owner._engine, auth.owner._scope)
    journal.admit(turn)
    control.feedback(claim.intent_id, "Dit onderwerp liever niet opnieuw; liever een creatief idee.", now=NOW)
    with pytest.raises(InitiativeMandateDenied, match="stopped"):
        guard.check(turn)
    with pytest.raises(ConversationStopped):
        journal.ensure_running(turn)
    assert source.recent_initiatives()[0]["owner_feedback"].startswith("Dit onderwerp")
    with pytest.raises(InitiativeMandateDenied):
        auth.bind(claim)
    auth.revoke()
    with pytest.raises(InitiativeMandateDenied, match="revoked"):
        auth.policy()
    assert store.claim_next(tenant_id=claim.tenant_id, installation_id=claim.installation_id, now=NOW) is None


@pytest.mark.parametrize("interruption", ["stop", "revoke"])
def test_native_dispatch_rechecks_stop_inside_admission_transaction(costless_native, monkeypatch, interruption):
    from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
    from eva.application.orchestration import ToolRequest
    auth, claim, store, control, source, path, policy = costless_native
    guard, turn = auth.bind(claim)
    read_policy = auth.policy
    calls = 0
    def stop_between_check_and_admission():
        nonlocal calls
        snapshot = read_policy()
        calls += 1
        if calls == 2:
            if interruption == "stop": control.stop(claim.intent_id, now=NOW)
            else: auth.revoke()
        return snapshot
    monkeypatch.setattr(auth, "policy", stop_between_check_and_admission)
    arguments = {"path": policy["capabilities"][0]["execute_schema"]["properties"]["path"]["const"], "content": "safe"}
    request = ToolRequest("tools:test", "operation:test", "checkpoint:test", "private.write",
        arguments, {}, observation_tool_name="private.read", observation_arguments=arguments,
        tool_configuration_sha256="a"*64)
    with pytest.raises(InitiativeMandateDenied, match="before dispatch"):
        guard.authorize_request(request, phase="execute")
    assert not any(e.event_type == "INITIATIVE_DISPATCH_ADMITTED" for e in auth.timeline.events(guard.thread))


def test_costless_schema_enforces_patterns_and_rejects_unsupported_constraints():
    from eva.adapters.postgres.initiative_execution import _matches_profile, _check_profile_schema, InitiativeMandateDenied
    schema = {"type": "object", "properties": {"path": {"type": "string", "pattern": r"^/private/[a-z0-9_-]+\.md$"}},
              "required": ["path"], "additionalProperties": False}
    _check_profile_schema(schema)
    assert _matches_profile({"path": "/private/result.md"}, schema)
    for path in ["/private/../other.md", "/another/result.md", "/private/result.md\n"]:
        assert not _matches_profile({"path": path}, schema)
    schema["properties"]["path"]["format"] = "uri"
    with pytest.raises(InitiativeMandateDenied, match="unsupported"):
        _check_profile_schema(schema)
