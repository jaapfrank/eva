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
from eva.application.sensory import SensoryEvent
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
    # The optimized adoption-first query must return the same rows, including
    # completed/stopped work and exact adoption payloads, as the original join.
    from eva.adapters.postgres.operational_timeline import operational_checkpoints, operational_events
    instance = control()
    c, e = operational_checkpoints.c, operational_events.c
    original = sa.select(operational_checkpoints, e.payload.label("adoption")).join(
        operational_events, sa.and_(c.tenant_id == e.tenant_id,
            c.installation_id == e.installation_id, c.thread_id == e.thread_id)
    ).where(c.tenant_id == instance._scope.tenant_id,
        c.installation_id == instance._scope.installation_id,
        e.event_type == "PERSONAL_INTENT_ADOPTED",
        e.payload["data"]["principal_id"].astext == str(owner),
        e.payload["data"]["origin"].astext == "eva_initiative")
    with postgres_engine.begin() as conn:
        instance._authorize(conn, at)
        order = (c.updated_at.desc(), c.thread_id)
        expected = conn.execute(original.order_by(*order).limit(4)).mappings().all()
        actual = conn.execute(instance._query().order_by(*order).limit(4)).mappings().all()
        assert actual == expected and len(actual) == 2

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
        assert source.recent_initiatives() == ({"intent_id": intent_id, "title": "A useful plan",
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
    from types import SimpleNamespace
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
    monkeypatch.setenv("EVA_INITIATIVE_MANDATE_FILE", str(path))
    auth = PostgresHindsightMemoryRuntime._initiative_authority(
        SimpleNamespace(_owner_initiatives=lambda: control))
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


@pytest.mark.parametrize("change", ["disabled", "missing", "unsafe", "symlink", "invalid_json"])
def test_host_mandate_reader_is_fresh_and_bound_before_dispatch(costless_native, monkeypatch, change):
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
    from eva.application.conversation import ConversationActionCall, ConversationToolPreparation
    from eva.application.initiative_execution import initiative_execution
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime

    auth, claim, store, control, source, path, policy = costless_native
    guard, turn = auth.bind(claim)
    decoy = path.with_name("other-enabled-mandate.json")
    decoy.write_text(json.dumps(policy)); decoy.chmod(0o600)
    # The already composed reader must keep its original path, even if the
    # process environment later names another valid, enabled mandate.
    monkeypatch.setenv("EVA_INITIATIVE_MANDATE_FILE", str(decoy))
    output = policy["capabilities"][0]["execute_schema"]["properties"]["path"]["const"]
    arguments = {"path": output, "content": "must not be written"}

    class Planner:
        action_names = ("save_private_result",)
        preparations = 0

        async def prepare(self, turn, call):
            self.preparations += 1
            if change == "disabled":
                path.write_text(json.dumps({**policy, "enabled": False}))
            elif change == "missing":
                path.unlink()
            elif change == "unsafe":
                path.chmod(0o644)
            elif change == "symlink":
                path.unlink(); path.symlink_to(decoy)
            else:
                path.write_text("{")
            return ConversationToolPreparation(tool_name="private.write", arguments=call.arguments,
                expected_proof={}, observation_tool_name="private.read", observation_arguments=call.arguments,
                tool_configuration_sha256="a" * 64, receipt_output={})

    class Provider:
        dispatches = 0

        async def execute(self, request):
            self.dispatches += 1
            raise AssertionError("changed mandate must prevent provider dispatch")

        async def observe(self, request, dispatch):
            raise AssertionError("no effect was admitted")

    planner, provider = Planner(), Provider()
    journal = PostgresConversationJournal(auth.owner._engine, auth.owner._scope)
    journal.admit(turn)
    tools = PostgresExternalConversationTools(auth.owner._engine, planner, provider)
    with initiative_execution(guard), pytest.raises(InitiativeMandateDenied):
        tools.execute(turn, ConversationActionCall("fresh-mandate", "save_private_result", arguments))
    assert planner.preparations == 1 and provider.dispatches == 0
    assert not Path(output).exists()
    assert not any(e.event_type == "INITIATIVE_DISPATCH_ADMITTED" for e in auth.timeline.events(guard.thread))

    monkeypatch.setenv("EVA_INITIATIVE_MANDATE_FILE", str(path))
    recovered = PostgresHindsightMemoryRuntime._initiative_authority(
        SimpleNamespace(_owner_initiatives=lambda: control))
    with pytest.raises(InitiativeMandateDenied):
        recovered.bind(claim)


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
@pytest.mark.parametrize("general", [False, True])
def test_native_public_research_uses_indexed_journal_and_never_repeats_uncertain_call(costless_native, interrupted, general):
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
    if general:
        question = "Hoe kunnen betaalbare vegetarische gezinsmaaltijden gevarieerd blijven?"
        args = {"question": question, "title": title, "options": ["Voedingscentrum"],
                "public_review": "public_unlinked", "research_mode": "topic"}
        schema["properties"]["question"] = {"type": "string", "minLength": 1, "maxLength": 2000}
        schema["properties"]["title"] = {"type": "string", "minLength": 1, "maxLength": 200}
        schema["properties"]["options"]["items"] = {"type": "string", "minLength": 1, "maxLength": 120}
        schema["properties"]["public_review"] = {"type": "string", "const": "public_unlinked"}
        schema["properties"]["research_mode"] = {"type": "string", "enum": ["topic", "comparison"]}
        schema["required"].extend(["public_review", "research_mode"])
    policy["capabilities"].append({**policy["capabilities"][0],
        "action_name": "research_current_options", "configuration_sha256": "b" * 64,
        "tool_name": "research.public_sources", "observation_tool_name": "research.public_sources",
        "data_scope": "public_research_minimized" if general else "public_no_private_input",
        "execute_schema": schema, "observe_schema": schema})
    path.write_text(json.dumps(policy))
    class Research:
        calls = 0
        def research_configuration_sha256(self): return "b" * 64
        def research_request_payload(self, *, question, options, research_mode="comparison"):
            return {"input": question, "sources": list(options), "mode": research_mode, "tools": [{"type": "web_search"}]}
        def research_current_options(self, **kwargs):
            self.calls += 1
            assert kwargs["question"] == question and kwargs["options"] == tuple(args["options"])
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
        from eva.application.conversation import ConversationConflict, ConversationActionInputRejected
        try:
            denied = actions.execute_step(turn, private, step_index=1)
        except (ConversationConflict, ConversationActionInputRejected):
            assert general
        else:
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
        assert receipt.output["provider_request_body"] == research.research_request_payload(
            question=question, options=tuple(args["options"]), research_mode="topic" if general else "comparison")
        assert "public_review" not in receipt.output["provider_request_body"]
    ordinary = replace(turn, conversation_id="owner-research", message_id="owner-research")
    assert "research_current_options" in actions.continuation_action_names(ordinary)


def test_public_native_profile_rejects_free_text_even_when_labelled_public(costless_native):
    import json
    from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
    auth, claim, store, control, source, path, policy = costless_native
    policy["capabilities"][0]["data_scope"] = "public_no_private_input"
    path.write_text(json.dumps(policy))
    with pytest.raises(InitiativeMandateDenied, match="host-selected"):
        auth.policy()


@pytest.mark.parametrize("outcome", ["idea", "silence", "unknown", "budget_denied", "revoked_during_recall", "expired_during_recall"])
def test_periodic_private_review_reuses_native_claim_budget_and_owner_context(costless_native, outcome):
    from concurrent.futures import ThreadPoolExecutor
    from eva.application.free_will import SensoryDecisionWorker
    from eva.application.conversation import IdentityProfile, ConversationModelUsage
    from eva.adapters.postgres.runtime_resource_usage import PostgresDailyReflectionBudget
    from tests.postgres.test_runtime_resource_usage import _budget
    auth, old_claim, interests, control, source, path, policy = costless_native
    def review_store():
        return PostgresSensoryEventStore(auth.owner._engine,
            tenant_id=old_claim.tenant_id, installation_id=old_claim.installation_id,
            initiative_principal_id=old_claim.principal_id, initiative_authorizer=auth.policy,
            context_reviews_only=True)
    observations = PostgresSensoryEventStore(auth.owner._engine,
        tenant_id=old_claim.tenant_id, installation_id=old_claim.installation_id)
    observations.retain(SensoryEvent("home_assistant", "home-assistant:review-source",
        "state_changed", NOW - timedelta(seconds=10),
        {"entity_id": "sensor.review_context", "friendly_name": "Relevant context fixture",
         "old_state": "unavailable", "new_state": "available"}))
    reviews = review_store()
    assert reviews.schedule_context_review(now=NOW) == "idle"  # Existing native work wins.
    control.stop(old_claim.intent_id, now=NOW)
    auth.policy()
    with ThreadPoolExecutor(max_workers=2) as pool:
        scheduled = list(pool.map(lambda _: review_store().schedule_context_review(now=NOW), range(2)))
    assert sorted(scheduled) == ["idle", "scheduled"]
    restarted = review_store()
    assert restarted.schedule_context_review(now=NOW + timedelta(minutes=20)) == "idle"
    budget = PostgresDailyReflectionBudget(auth.owner._engine,
        tenant_id=old_claim.tenant_id, installation_id=old_claim.installation_id,
        resource_budget=replace(_budget(), tenant_id=old_claim.tenant_id, installation_id=old_claim.installation_id))
    if outcome == "budget_denied":
        for _ in range(4):
            assert budget.admit_background_reflection(tenant_id=old_claim.tenant_id,
                installation_id=old_claim.installation_id, principal_id=old_claim.principal_id, now=NOW).admitted
    recalled, calls = [], []
    clock = [NOW + timedelta(minutes=20)]
    class Memory:
        def recall_for_presence(self, query):
            recalled.append(query)
            assert query.principal_id == old_claim.principal_id
            assert query.conversation_id == "sensory:initiative-review"
            assert query.scope["kind"] == "private_opportunity"
            # Ordinary recall searches personal context. The original sensor is
            # still checked below in the stamped model event, not in the search hint.
            assert "Relevant context fixture" not in query.query_text
            assert "Persoonlijke plannen" in query.query_text
            if outcome == "revoked_during_recall": auth.revoke()
            if outcome == "expired_during_recall": clock[0] += timedelta(minutes=2)
            return object()
    class Model:
        def evaluate_sensory_event(self, event, identity, memory, attention=None, **kwargs):
            calls.append(event)
            assert event.source == "initiative-review"
            assert event.event_type == "private_opportunity_review"
            assert set(event.payload) == {"review_window", "life_context"}
            assert event.payload["life_context"]["observed_at_utc"]
            observation = next(item for item in event.payload["life_context"]["observations"]
                               if item["entity_id"] == "sensor.review_context")
            assert observation["friendly_name"] == "Relevant context fixture"
            assert observation["is_current_proof"] is False
            original = restarted._timeline.events("sensory:" + event.source_event_id)[0]
            assert original.payload["data"]["payload"] == event.payload
            if outcome == "unknown": raise RuntimeError("lost model response")
            return FreeWillDecision("respond", "Eigen nuttig idee" if outcome == "idea" else "Nu niets nodig", 1,
                "fixture", expression="This must never be delivered",
                usage=ConversationModelUsage.measured(model_attempts=1, tool_calls=1, input_tokens=100, output_tokens=30),
                initiative=PersonalInitiativeProposal("Nieuwe eigen bijdrage", "Zelf gekozen", "Maak bruikbaar resultaat")
                    if outcome == "idea" else None)
    worker = SensoryDecisionWorker(restarted, Model(), Memory(), IdentityProfile("Eva", "Huisgenoot", ("Eerlijk",), 80),
        tenant_id=old_claim.tenant_id, installation_id=old_claim.installation_id,
        principal_id=old_claim.principal_id, context_review_budget=budget,
        context_review_authorizer=auth.policy, clock=lambda: clock[0])
    failures = []
    original_fail = restarted.fail
    def capture_failure(claim, error, *, now):
        failures.append(repr(error))
        return original_fail(claim, error, now=now)
    restarted.fail = capture_failure
    result = worker.run_once(now=NOW + timedelta(minutes=20))
    if outcome in {"idea", "silence"}:
        assert result != "failed", failures
    elif outcome == "unknown":
        assert failures == ["RuntimeError('lost model response')"]
    assert len(calls) == (0 if outcome in {"budget_denied", "revoked_during_recall", "expired_during_recall"} else 1)
    assert len(recalled) == (0 if outcome == "budget_denied" else 1)
    if outcome in {"revoked_during_recall", "expired_during_recall"}:
        # Existing pre-dispatch recovery bounds preparation, with no model call.
        for _ in range(2):
            assert worker.run_once(now=NOW + timedelta(minutes=21)) == "failed"
        assert calls == []
    assert worker.run_once(now=NOW + timedelta(minutes=21)) == "idle"
    assert review_store().schedule_context_review(now=NOW + timedelta(minutes=50)) in {"idle", "mandate_unavailable"}
    rows = restarted._timeline.list_checkpoints(thread_prefix=restarted._decision_prefix, limit=10)
    assert len(rows) == 1
    events = restarted._timeline.events(rows[0].thread_id)
    assert not any(e.event_type == "PROACTIVE_EXPRESSION_PREPARED" for e in events)
    if outcome == "unknown":
        assert rows[0].phase == "will_usage_uncertain"
        assert review_store().schedule_context_review(now=NOW + timedelta(days=1)) in {"idle", "mandate_unavailable"}
    elif outcome in {"revoked_during_recall", "expired_during_recall"}:
        assert rows[0].phase == "will_preparation_failed" and rows[0].retry_count == 3
        assert rows[0].status == "needs_help"
        failures = [e for e in events if e.event_type == "WILL_PREPARATION_FAILED"]
        assert len(failures) == 3
        assert all(e.payload["data"]["provider_dispatched"] is False for e in failures)
        assert all(e.payload["data"]["usage"] is None for e in failures)
    else:
        assert rows[0].phase == "will_decided"
        assert events[-1].payload["data"]["expression"] is None
    if outcome == "idea":
        claim = interests.claim_next(tenant_id=old_claim.tenant_id, installation_id=old_claim.installation_id,
                                     now=NOW + timedelta(minutes=21))
        assert claim.execution_mode == "autonomous_task"
        guard, turn = auth.bind(claim)
        assert turn.conversation_id.startswith("initiative-task:")
        adoption = auth.timeline.events(claim.thread_id)[0].payload["data"]
        assert adoption["conversation_id"] == "initiative:initiative-review"
        assert review_store().schedule_context_review(now=NOW + timedelta(hours=2)) == "idle"
    if outcome == "silence":
        assert review_store().schedule_context_review(now=NOW + timedelta(days=3)) == "scheduled"
        assert len(restarted._timeline.list_checkpoints(thread_prefix=restarted._decision_prefix, limit=10)) == 2
    reservation_events = budget._timeline.events("runtime-reflections:" + NOW.date().isoformat())
    assert reservation_events[-1].operation_id == rows[0].thread_id


def test_context_review_resumes_model_admission_without_ha_revalidation(costless_native):
    auth, old_claim, interests, control, source, path, policy = costless_native
    control.stop(old_claim.intent_id, now=NOW)
    reviews = PostgresSensoryEventStore(auth.owner._engine,
        tenant_id=old_claim.tenant_id, installation_id=old_claim.installation_id,
        initiative_principal_id=old_claim.principal_id, initiative_authorizer=auth.policy,
        context_reviews_only=True)
    assert reviews.schedule_context_review(now=NOW) == "scheduled"
    claim = reviews.claim_next(now=NOW)
    reviews.wait_for_admission(claim, "unverified_model_cost", now=NOW)
    assert reviews.claim_next(now=NOW, include_waiting=False) is None
    reclaimed = reviews.claim_next(now=NOW + timedelta(days=1), include_waiting=True)
    assert reclaimed is not None and reclaimed.event.source == "initiative-review"
    assert reviews._timeline.load(reclaimed.thread_id).phase == "will_claimed"
    assert reviews.claim_next_revalidation(now=NOW + timedelta(days=1)) is None


@pytest.mark.parametrize("outcome", ["silence", "budget", "adopted", "refused"])
def test_owner_sees_private_review_before_adoption(postgres_engine, outcome):
    label = "review-view-" + outcome
    authority, _, binding = _seed_authority(postgres_engine, label)
    owner = _id(label, "principal")
    tenant, installation = label, "home"
    allow = [True]
    def authorize():
        if not allow[0]:
            raise RuntimeError("mandate revoked")
        return {}, None
    source = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant,
        installation_id=installation, initiative_principal_id=str(owner),
        initiative_authorizer=authorize, context_reviews_only=True)
    control = PostgresOwnerInitiatives(postgres_engine, tenant_id=tenant,
        installation_id=installation, owner=owner, binding=binding, authority_scope=authority)
    assert control.page(now=NOW)["latest_review"] is None
    assert source.schedule_context_review(now=NOW) == "scheduled"
    assert control.page(now=NOW)["latest_review"]["state"] == "scheduled"
    claim = source.mark_model_started(source.claim_next(now=NOW), now=NOW)
    assert control.page(now=NOW)["latest_review"]["state"] == "working"
    proposal = PersonalInitiativeProposal("Own idea", "Useful", "Explore") if outcome in ("adopted", "refused") else None
    allow[0] = outcome != "refused"
    source.complete(claim, FreeWillDecision("silence", "No further action needed", 0,
        "eva:background-budget" if outcome == "budget" else "fixture", initiative=proposal), now=NOW)
    page = control.page(now=NOW)
    review = page["latest_review"]
    assert review["state"] == {"budget": "waiting", "refused": "not_admitted"}.get(outcome, outcome)
    assert review["updated_at"] == NOW.isoformat()
    assert review["reason"] == "No further action needed"
    assert review["blocker"] == {"budget": "background_budget", "refused": "initiative_mandate_unavailable"}.get(outcome)
    assert len(page["cards"]) == (1 if outcome == "adopted" else 0)
    if outcome == "adopted":
        assert review["intent_id"] == page["cards"][0]["intent_id"]
    else:
        assert review["intent_id"] is None


def test_private_review_is_owner_installation_scoped_current_and_hash_checked(postgres_engine):
    from eva.adapters.postgres.operational_timeline import operational_events
    label = "private-review-isolation"
    authority, _, binding = _seed_authority(postgres_engine, label)
    owner = _id(label, "principal")
    tenant = label
    def source(principal, installation):
        return PostgresSensoryEventStore(postgres_engine, tenant_id=tenant,
            installation_id=installation, initiative_principal_id=str(principal),
            initiative_authorizer=lambda: ({}, None), context_reviews_only=True)
    def decide(store, reason, at):
        assert store.schedule_context_review(now=at) == "scheduled"
        claim = store.mark_model_started(store.claim_next(now=at), now=at)
        store.complete(claim, FreeWillDecision("silence", reason, 0, "fixture"), now=at)
        return claim
    own = source(owner, "home")
    claim = decide(own, "Own private assessment", NOW)
    decide(source(_id(label, "other"), "home"), "Other person's secret", NOW + timedelta(seconds=1))
    decide(source(owner, "elsewhere"), "Other installation secret", NOW + timedelta(seconds=2))
    control = PostgresOwnerInitiatives(postgres_engine, tenant_id=tenant,
        installation_id="home", owner=owner, binding=binding, authority_scope=authority)
    page = control.page(now=NOW + timedelta(seconds=3))
    assert page["latest_review"]["reason"] == "Own private assessment"
    assert "secret" not in str(page)
    table = metadata.tables["eva_core.principal_bindings"]
    with postgres_engine.begin() as conn:
        conn.execute(sa.update(table).where(table.c.tenant_id == authority.tenant_id,
            table.c.principal_binding_id == binding).values(status="revoked"))
    with pytest.raises(RuntimeError, match="binding is not current"):
        control.page(now=NOW)
    with postgres_engine.begin() as conn:
        conn.execute(sa.update(table).where(table.c.tenant_id == authority.tenant_id,
            table.c.principal_binding_id == binding).values(status="verified"))
        # Corruption injection only in the disposable PostgreSQL fixture.
        conn.exec_driver_sql("ALTER TABLE eva_core.operational_events DISABLE TRIGGER USER")
        conn.execute(sa.update(operational_events).where(
            operational_events.c.tenant_id == own._timeline._scope_value.tenant_id,
            operational_events.c.installation_id == own._timeline._scope_value.installation_id,
            operational_events.c.thread_id == claim.thread_id,
            operational_events.c.event_type == "WILL_DECIDED").values(payload_sha256=bytes(32)))
        conn.exec_driver_sql("ALTER TABLE eva_core.operational_events ENABLE TRIGGER USER")
    with pytest.raises(RuntimeError, match="review payload hash differs"):
        control.page(now=NOW)


def test_owner_abandons_only_exact_uncertain_review_and_preserves_usage(postgres_engine):
    from eva.application.free_will import AutonomousUsageUncertain
    label = "abandon-private-review"
    authority, _, binding = _seed_authority(postgres_engine, label)
    owner = _id(label, "principal")
    source = PostgresSensoryEventStore(postgres_engine, tenant_id=label,
        installation_id="home", initiative_principal_id=str(owner),
        initiative_authorizer=lambda: ({}, None), context_reviews_only=True)
    control = PostgresOwnerInitiatives(postgres_engine, tenant_id=label,
        installation_id="home", owner=owner, binding=binding, authority_scope=authority)
    assert source.schedule_context_review(now=NOW) == "scheduled"
    claim = source.mark_model_started(source.claim_next(now=NOW), now=NOW)
    with pytest.raises(ValueError):
        control.abandon_uncertain_review(claim.thread_id, claim.revision, now=NOW)
    source.fail(claim, AutonomousUsageUncertain("lost response"), now=NOW)
    state = source._timeline.load(claim.thread_id)
    before = source._timeline.events(claim.thread_id)
    assert source.schedule_context_review(now=NOW + timedelta(days=1)) == "idle"
    with pytest.raises(ValueError):
        control.abandon_uncertain_review(claim.thread_id, state.revision - 1, now=NOW)
    foreign = PostgresOwnerInitiatives(postgres_engine, tenant_id=label,
        installation_id="home", owner=_id(label, "other"), binding=binding, authority_scope=authority)
    with pytest.raises(ValueError):
        foreign.abandon_uncertain_review(claim.thread_id, state.revision, now=NOW)
    bindings = metadata.tables["eva_core.principal_bindings"]
    with postgres_engine.begin() as conn:
        conn.execute(sa.update(bindings).where(bindings.c.tenant_id == authority.tenant_id,
            bindings.c.principal_binding_id == binding).values(status="revoked"))
    with pytest.raises(RuntimeError, match="binding is not current"):
        control.abandon_uncertain_review(claim.thread_id, state.revision, now=NOW)
    with postgres_engine.begin() as conn:
        conn.execute(sa.update(bindings).where(bindings.c.tenant_id == authority.tenant_id,
            bindings.c.principal_binding_id == binding).values(status="verified"))
    assert source._timeline.events(claim.thread_id) == before
    result = control.abandon_uncertain_review(claim.thread_id, state.revision,
        now=NOW + timedelta(minutes=10))
    assert result["usage"] == "unknown" and result["replayed"] is False
    after = source._timeline.events(claim.thread_id)
    assert after[:-1] == before and after[-1].payload["data"]["usage"] is None
    assert control.page(now=NOW + timedelta(minutes=10))["latest_review"]["state"] == "abandoned"
    with pytest.raises(ValueError):
        control.abandon_uncertain_review(claim.thread_id, state.revision, now=NOW)
    restarted = PostgresSensoryEventStore(postgres_engine, tenant_id=label,
        installation_id="home", initiative_principal_id=str(owner),
        initiative_authorizer=lambda: ({}, None), context_reviews_only=True)
    assert restarted.claim_next(now=NOW + timedelta(minutes=30)) is None
    assert restarted.schedule_context_review(now=NOW + timedelta(minutes=59)) == "idle"
    assert restarted.schedule_context_review(now=NOW + timedelta(hours=1)) == "scheduled"
    fresh = restarted.claim_next(now=NOW + timedelta(hours=1))
    assert fresh.thread_id != claim.thread_id


@pytest.mark.parametrize("path_value", ["/api/camera_proxy/camera.garden", "/api/calendars/calendar.family?start=2026-09-28T00%3A00%3A00Z&end=2026-09-29T00%3A00%3A00Z"])
def test_native_camera_calendar_read_uses_existing_guard_and_preserves_receipt(costless_native, monkeypatch, path_value):
    import json
    from pathlib import Path
    from eva.providers.home_assistant import HomeAssistantProviderFactory, HomeAssistantProviderSettings
    from eva.application.initiative_execution import initiative_execution
    from eva.application.conversation import ConversationActionCall, ConversationConflict
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    auth, claim, store, control, source, path, policy = costless_native
    contribution = HomeAssistantProviderFactory.build(HomeAssistantProviderSettings(
        endpoint_url="http://existing-home:8123", credential="fixture-only", effects_enabled=False,
        effect_allowlist=(), effect_principal_sha256_allowlist=("0"*64,), event_domains=("light",),
        api_enabled=True), auth.owner._engine)
    api = next(tool for tool in contribution.external_tools if tool.action_names == ("home_assistant_request",))
    cap = json.loads((Path(__file__).parents[3] / "deploy/local-vision/initiative-read-profile.json").read_text())
    cap["configuration_sha256"] = api._configuration_sha256
    policy["capabilities"] = [cap]; path.write_text(json.dumps(policy))
    calls = []
    def read(arguments):
        calls.append(dict(arguments))
        return {"http_status": 200, "success": True, "response_json": "fixture interpretation or calendar response"}
    monkeypatch.setattr(api._provider, "_request", read)
    guard, turn = auth.bind(claim)
    args = dict(transport="rest", method="GET", path=path_value, body_json="", response_pointer="", offset=0, limit=10)
    call = ConversationActionCall("read-context", "home_assistant_request", args)
    with pytest.raises(ConversationConflict): api._authorize(turn)
    with initiative_execution(guard):
        PostgresConversationJournal(auth.owner._engine, control._scope).admit(turn)
        receipt = api.execute(turn, call)
        assert "fixture interpretation" in json.dumps(receipt.output)
        assert api.execute(turn, call) == receipt and len(calls) == 1
        control.stop(claim.intent_id, now=NOW)
        with pytest.raises(ConversationConflict): api.execute(turn, call, step_index=1)
        assert len(calls) == 1


@pytest.mark.parametrize("changes", [
    {"method": "POST"}, {"method": "DELETE"}, {"transport": "websocket"},
    {"path": "/api/services/light/turn_on"}, {"path": "/api/states/person.owner"},
    {"path": "/api/calendars/../states"}, {"path": "http://external.invalid/api/calendars"},
    {"body_json": "{}"}, {"limit": 51},
])
def test_initiative_context_profile_does_not_grant_general_api_effects(changes):
    import json
    from pathlib import Path
    from eva.adapters.postgres.initiative_execution import _matches_profile, _check_profile_schema
    cap = json.loads((Path(__file__).parents[3] / "deploy/local-vision/initiative-read-profile.json").read_text())
    for key in ("execute_schema", "observe_schema"):
        schema = cap[key]; _check_profile_schema(schema)
        args = dict(transport="rest", method="GET", path="/api/calendars", body_json="", response_pointer="", offset=0, limit=10)
        assert _matches_profile(args, schema)
        assert not _matches_profile({**args, **changes}, schema)


@pytest.mark.parametrize("uncertain_send", [False, True])
def test_completed_native_result_delivery_keeps_artifact_and_never_repeats_send(
    costless_native, monkeypatch, uncertain_send
):
    from eva.adapters.postgres import initiative_delivery as delivery_module
    from eva.application.conversation import GroundedReply
    auth, claim, store, control, source, path, policy = costless_native
    now = NOW

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(delivery_module, "datetime", Clock)
    sent = []

    class Transport:
        def deliver(self, message):
            sent.append(message)
            if uncertain_send:
                raise TimeoutError("Transport may have sent the message")
            return "private-transport-receipt"

    delivery = delivery_module.InitiativeCompletionDelivery(control, Transport(), auth.policy)
    assert delivery.run_once() == "enabled"
    guard, turn = auth.bind(claim)
    content = "A completed contribution with its actual result."
    now = NOW + timedelta(seconds=1)
    reply = GroundedReply(turn.conversation_id, turn.message_id, content, (), turn.occurred_at, "fixture")
    store.complete_native(claim, reply, now=now)
    now += timedelta(seconds=1)
    assert delivery.run_once() == ("delivery_uncertain" if uncertain_send else "delivered")
    assert len(sent) == 1 and content in sent[0].text
    assert control.artifact(claim.intent_id, now=now)["content"] == content
    restarted = delivery_module.InitiativeCompletionDelivery(control, Transport(), auth.policy)
    assert restarted.run_once() == "idle"
    assert len(sent) == 1
