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


def test_native_existing_household_reader_keeps_host_binding_and_stop(costless_native):
    import json
    from eva.adapters.connectors.home_assistant_admin import HomeAssistantHouseholdTool
    from eva.providers.home_assistant import home_assistant_read_configuration_sha256
    from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
    from eva.application.initiative_execution import initiative_execution
    from eva.application.conversation import ConversationActionCall, ConversationConflict
    from eva.application.orchestration import ToolDispatchResult
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    auth, claim, store, control, source, path, policy = costless_native
    digest = home_assistant_read_configuration_sha256("http://existing-home:8123/")
    schema = {"type": "object", "properties": {
        "section": {"type": "string", "enum": ["overview", "entities"]},
        "area_id": {"type": "string", "maxLength": 100},
        "offset": {"type": "integer", "minimum": 0, "maximum": 10000},
        "snapshot_sha256": {"type": "string", "maxLength": 64},
        "automation_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 0}},
        "required": ["section", "area_id", "offset", "snapshot_sha256", "automation_ids"],
        "additionalProperties": False}
    cap = {**policy["capabilities"][0], "action_name": "read_home_assistant_inventory",
           "configuration_sha256": digest, "tool_name": "read_home_assistant_inventory",
           "observation_tool_name": None, "execute_schema": schema, "observe_schema": schema,
           "cost_evidence": "Existing authenticated local HA read; no service calls or metered service"}
    policy["capabilities"] = [cap]; path.write_text(json.dumps(policy))
    class Reader:
        reads = 0
        async def household_page(self, **arguments):
            self.reads += 1
            return {"section": arguments["section"], "entities": [{"entity_id": "light.study", "state": "off"}], "has_more": False}
    reader = Reader(); provider = HomeAssistantHouseholdTool(reader)
    tools = PostgresExternalConversationTools(auth.owner._engine, provider, provider,
                                             configuration_sha256=digest)
    guard, turn = auth.bind(claim)
    arguments = {"section": "entities", "area_id": "", "offset": 0, "snapshot_sha256": "", "automation_ids": []}
    call = ConversationActionCall("read-house", "read_home_assistant_inventory", arguments)
    from eva.providers.home_assistant import HomeAssistantProviderFactory, HomeAssistantProviderSettings
    contribution = HomeAssistantProviderFactory.build(HomeAssistantProviderSettings(
        endpoint_url="http://existing-home:8123", credential="fixture-only", effects_enabled=False,
        effect_allowlist=(), effect_principal_sha256_allowlist=("0" * 64,), event_domains=("light",)), auth.owner._engine)
    inventory = next(tool for tool in contribution.external_tools if tool.action_names == ("read_home_assistant_inventory",))
    with initiative_execution(guard):
        # Native owner authority is distinct from ordinary effect/alias authority.
        inventory._authorize(turn)
        PostgresConversationJournal(auth.owner._engine, control._scope).admit(turn)
        receipt = tools.execute(turn, call)
        assert "light.study" in json.dumps(receipt.output)
        assert reader.reads == 1
        assert tools.execute(turn, call) == receipt
        assert reader.reads == 1
        assert not guard.action_allowed("control_home_assistant_light")
        # A saved request cannot be dispatched against a changed host destination.
        request = tools._request(turn, call.name, __import__("asyncio").run(provider.prepare(turn, call)))
        changed = PostgresExternalConversationTools(auth.owner._engine, provider, provider,
            configuration_sha256=home_assistant_read_configuration_sha256("http://other-home:8123"))
        wrapped = changed._orchestrator(turn, changed._timeline(turn))._tool
        with pytest.raises(ConversationConflict, match="configuration changed"):
            __import__("asyncio").run(wrapped.observe(request, ToolDispatchResult(True, "unused", {})))
        assert reader.reads == 1
        control.stop(claim.intent_id, now=NOW)
        with pytest.raises(ConversationConflict):
            inventory._authorize(turn)
        with pytest.raises(ConversationConflict):
            tools.execute(turn, call, step_index=1)
        assert reader.reads == 1


@pytest.mark.parametrize("action", ["read_home_assistant_state", "read_home_assistant_history"])
def test_native_household_target_planning_performs_no_unadmitted_network_read(costless_native, action):
    import asyncio
    from eva.adapters.connectors.home_assistant import HomeAssistantConversationReadPlanner
    from eva.application.initiative_execution import initiative_execution
    from eva.application.conversation import ConversationActionCall, ConversationConflict
    auth, claim, *_ = costless_native
    guard, turn = auth.bind(claim)
    class Provider:
        async def resolve_light(self, *args): raise AssertionError("unadmitted network resolution")
        async def resolve_switch(self, *args): raise AssertionError("unadmitted network resolution")
        async def resolve_history_targets(self, *args): raise AssertionError("unadmitted network resolution")
    planner = HomeAssistantConversationReadPlanner(Provider())
    args = {"target": "light.study", "entity_kind": "light"}
    if action.endswith("history"): args.update(period="today", transition_to="any")
    with initiative_execution(guard):
        prepared = asyncio.run(planner.prepare(turn, ConversationActionCall("native-id", action, args)))
        assert (prepared.arguments.get("entity_id") or prepared.arguments.get("entity_ids")) in ("light.study", ["light.study"])
        with pytest.raises(ConversationConflict, match="exact entity ID"):
            asyncio.run(planner.prepare(turn, ConversationActionCall("native-name", action, {**args, "target": "study"})))


def test_owner_mandate_revision_is_append_only_and_does_not_upgrade_running_work(costless_native):
    import json
    from eva.application.conversation import ConversationConflict
    from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
    from eva.application.initiative_execution import initiative_execution
    from eva.contracts.canonical_json import canonical_sha256
    auth, claim, store, control, source, path, policy = costless_native
    guard, turn = auth.bind(claim)
    original = auth.timeline.events(auth.thread)
    previous = guard.mandate_sha
    policy["authorization_source"] = "Owner explicitly extends existing costless mandate"
    policy["capabilities"][0]["execute_schema"]["properties"]["content"]["maxLength"] = 1100
    candidate = canonical_sha256({k: v for k, v in policy.items() if k != "enabled"})
    path.write_text(json.dumps(policy))
    with pytest.raises(InitiativeMandateDenied, match="revision changed"):
        auth.policy()
    with pytest.raises(InitiativeMandateDenied, match="host file"):
        auth.admit_revision(previous, "b" * 64)
    with pytest.raises(InitiativeMandateDenied, match="previous"):
        auth.admit_revision("b" * 64, candidate)
    with initiative_execution(guard), pytest.raises(InitiativeMandateDenied, match="own mandate"):
        auth.admit_revision(previous, candidate)
    assert auth.admit_revision(previous, candidate)["status"] == "revised"
    assert auth.admit_revision(previous, candidate)["status"] == "unchanged"
    events = auth.timeline.events(auth.thread)
    assert events[:len(original)] == original and len(events) == len(original) + 1
    assert events[-1].payload["data"]["previous_mandate_sha256"] == previous
    assert auth.policy()[1] == candidate
    with pytest.raises(InitiativeMandateDenied, match="changed"):
        guard.check(turn)
    with pytest.raises(ConversationConflict):
        auth.bind(claim)
    auth.revoke()
    with pytest.raises(InitiativeMandateDenied, match="revoked"):
        auth.admit_revision(candidate, candidate)
    with pytest.raises(InitiativeMandateDenied, match="revoked"):
        auth.policy()


def test_native_dispatch_serializes_owner_mandate_revision(costless_native, monkeypatch):
    import json
    from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
    from eva.application.orchestration import ToolRequest
    from eva.contracts.canonical_json import canonical_sha256
    auth, claim, store, control, source, path, policy = costless_native
    guard, turn = auth.bind(claim)
    read_policy = auth.policy
    calls = 0
    def revise_between_check_and_dispatch():
        nonlocal calls
        snapshot = read_policy()
        calls += 1
        if calls == 2:
            policy["authorization_source"] = "Owner revises during admission"
            path.write_text(json.dumps(policy))
            candidate = canonical_sha256({k: v for k, v in policy.items() if k != "enabled"})
            auth.admit_revision(guard.mandate_sha, candidate)
        return snapshot
    monkeypatch.setattr(auth, "policy", revise_between_check_and_dispatch)
    arguments = {"path": policy["capabilities"][0]["execute_schema"]["properties"]["path"]["const"], "content": "safe"}
    request = ToolRequest("tools:test", "operation:test", "checkpoint:test", "private.write",
        arguments, {}, observation_tool_name="private.read", observation_arguments=arguments,
        tool_configuration_sha256="a" * 64)
    with pytest.raises(InitiativeMandateDenied, match="revised before dispatch"):
        guard.authorize_request(request, phase="execute")
    assert not any(e.event_type == "INITIATIVE_DISPATCH_ADMITTED" for e in auth.timeline.events(guard.thread))


@pytest.mark.parametrize("interrupted", [False, True])
def test_native_public_research_uses_indexed_journal_and_never_repeats_uncertain_call(costless_native, interrupted):
    import json
    from eva.adapters.model.research_tools import NativeResearchTool
    from eva.adapters.postgres.conversation_actions import PostgresConversationActions
    from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    from eva.application.conversation import ConversationActionCall, ConversationModelUsage
    from eva.application.initiative_execution import initiative_execution
    from eva.application.research import ResearchArtifact, ResearchSource
    from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
    auth, claim, store, control, source, path, policy = costless_native
    question = "Welke mogelijkheden en beperkingen beschrijven deze openbare bronnen?"
    title = "Openbare documentatie onderzoeken"
    url = "https://playwright.dev/docs/intro"
    args = {"question": question, "title": title, "options": [url]}
    schema = {"type": "object", "properties": {
        "question": {"type": "string", "const": question},
        "title": {"type": "string", "const": title},
        "options": {"type": "array", "minItems": 1, "maxItems": 5,
                    "items": {"type": "string", "enum": [url]}}},
        "required": ["question", "title", "options"], "additionalProperties": False}
    policy["capabilities"].append({**policy["capabilities"][0],
        "action_name": "research_current_options", "configuration_sha256": "b" * 64,
        "tool_name": "research.public_sources", "observation_tool_name": "research.public_sources",
        "data_scope": "public_no_private_input", "execute_schema": schema, "observe_schema": schema})
    path.write_text(json.dumps(policy))
    class Research:
        calls = 0
        def research_configuration_sha256(self): return "b" * 64
        def research_current_options(self, **kwargs):
            self.calls += 1
            assert kwargs["question"] == question and kwargs["options"] == (url,)
            if interrupted: raise RuntimeError("response lost after provider may have run")
            return ResearchArtifact(title=kwargs["title"], question=kwargs["question"],
                content="Publieke documentatie beschrijft browserautomatisering.",
                sources=(ResearchSource("Playwright", url),), researched_at=kwargs["researched_at"],
                provider="fixture", usage=ConversationModelUsage.measured(
                    model_attempts=1, tool_calls=1, input_tokens=150, output_tokens=40))
    research = Research()
    adapter = NativeResearchTool(research, PostgresConversationActions._validated_research_arguments)
    tools = PostgresExternalConversationTools(auth.owner._engine, adapter, adapter)
    actions = PostgresConversationActions(auth.owner._engine, research=research, native_research=tools)
    guard, turn = auth.bind(claim)
    PostgresConversationJournal(auth.owner._engine, auth.owner._scope).admit(turn)
    assert actions.action_names.count("research_current_options") == 1
    with initiative_execution(guard):
        assert "research_current_options" in actions.continuation_action_names(turn)
        private = ConversationActionCall("private", "research_current_options", {**args, "question": "Private secret from memory"})
        denied = actions.execute_step(turn, private, step_index=1)
        assert denied.output["status"] in {"failed", "needs_help"}
        assert research.calls == 0
    # A rejected operation remains bound to its original arguments: use a later step.
    call = ConversationActionCall("public", "research_current_options", args)
    with initiative_execution(guard):
        try:
            receipt = actions.execute_step(turn, call, step_index=2)
        except RuntimeError:
            assert interrupted
            receipt = actions.pending_step(turn, step_index=2)
        recovered = actions.pending_step(turn, step_index=2)
    assert research.calls == 1
    assert receipt == recovered
    if interrupted:
        assert receipt.output["status"] in {"failed", "needs_help"}
    else:
        assert receipt.output["status"] == "verified"
        assert receipt.output["result"]["model_usage"]["input_tokens"] == 150
        assert receipt.output["result"]["sources"][0]["url"] == url
    ordinary = replace(turn, conversation_id="owner-research", message_id="owner-research")
    assert "research_current_options" not in actions.continuation_action_names(ordinary)


def test_public_native_profile_rejects_free_text_even_when_labelled_public(costless_native):
    import json
    from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
    auth, claim, store, control, source, path, policy = costless_native
    policy["capabilities"][0]["data_scope"] = "public_no_private_input"
    path.write_text(json.dumps(policy))
    with pytest.raises(InitiativeMandateDenied, match="host-selected"):
        auth.policy()
