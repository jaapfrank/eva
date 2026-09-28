"""Real conversation loop with controlled model/tools; no external/model traffic."""
import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
import threading
import time

import pytest

from eva.application.cancellation import check_cancellation
from eva.application.conversation import (
    ConversationActionCall, ConversationActionReceipt, ConversationConflict,
    ConversationModelResult, ConversationModelUsage, ConversationTurn, IdentityProfile, LivingConversation,
)
from eva.application.background_execution import background_execution_namespace
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.delegated_work import _validate_usage
from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile
from eva.bootstrap.delegated_digital_runner import DelegatedDigitalRunner
from eva.contracts.canonical_json import canonical_sha256
from test_conversation import _Memory

NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)

class Journal:
    def __init__(self): self.usages = []
    def begin_model_request(self, turn): return str(len(self.usages))
    def complete_model_request(self, turn, request, usage): self.usages.append(usage)
    def model_usage(self, turn): return ConversationModelUsage.aggregate(tuple(self.usages))

class Store:
    _engine = None
    stopped = False
    def authorize_tool_dispatch(self, call):
        if self.stopped: raise ConversationConflict("work stopped")

class Tools:
    action_names = ("discover_connector_tools", "use_connector_tool")
    def __init__(self):
        self.started = threading.Event(); self.release = threading.Event(); self.calls = []
    def available_for(self, turn): return True
    def active_context(self, turn): return ()
    def pending(self, turn, *, step_index): return None
    def execute(self, turn, call, *, step_index):
        from eva.application.background_execution import authorize_background_dispatch
        authorize_background_dispatch(turn)
        self.calls.append((turn, call.name, step_index, background_execution_namespace(turn)))
        if step_index == 0:
            self.started.set()
            while not self.release.wait(.01): check_cancellation()
        check_cancellation()
        return ConversationActionReceipt(call, {"status":"verified", "result":"read back"})

class Model:
    def generate(self, turn, identity, memory, recent_messages, **kwargs):
        assert any("Compare the documents" in x for x in kwargs['self_context'])
        completed = kwargs.get('completed_action')
        name = "discover_connector_tools" if completed is None else (
            "use_connector_tool" if completed.call.name == "discover_connector_tools" else None)
        return ConversationModelResult(None if name else "The comparison was stored and read back.",
            "controlled", None if name is None else ConversationActionCall(name, name, {}),
            usage=ConversationModelUsage.measured(tool_calls=0 if name is None else 1,
                                                  input_tokens=30, output_tokens=10))

@pytest.fixture(autouse=True)
def controlled_persistence(monkeypatch):
    """Keep these loop tests independent of PostgreSQL-only optional features."""
    from types import SimpleNamespace
    import eva.bootstrap.delegated_digital_runner as module
    import eva.adapters.postgres.work_corrections as corrections
    empty = lambda *args: SimpleNamespace(action_names=(), available_for=lambda turn: False)
    monkeypatch.setattr(module, "BackgroundWorkProgress", empty)
    monkeypatch.setattr(module, "ComponentImprovementActions", empty)
    monkeypatch.setattr(module, "load_work_progress", lambda *args: None)
    monkeypatch.setattr(corrections, "work_corrections", lambda *args: ())


def fixture():
    source = ConversationTurn.private("tenant", "install", "owner", "chat", "message",
                                      "Compare the documents", NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
    root = Path(__file__).resolve().parents[2] / "src/eva"
    name = "bootstrap/delegated_digital_runner.py"
    profile = DelegatedDigitalProfile("model:test", "a"*64, "b"*64, Tools.action_names,
        ((name, hashlib.sha256((root/name).read_bytes()).hexdigest()),))
    spec = profile.build_spec(source={k:getattr(source,k) for k in (
        "tenant_id","installation_id","principal_id","conversation_id","message_id")} |
        {"audience":{"kind":"private_person","principal_id":"owner"}},
        source_ingress_sha256=canonical_sha256(PostgresConversationJournal._ingress_data(source)),
        work_id="work:one",episode_id="episode:one",generation=1,parent_task_id="task:one",
        owner_principal_id="owner",principal_binding_id="binding:one",task="Compare the documents",
        created_at=NOW,deadline=NOW+timedelta(seconds=300))
    store,tools,journal=Store(),Tools(),Journal()
    identity=IdentityProfile("Eva", "Helpful", ("Be honest",))
    runner=DelegatedDigitalRunner(store, profile, source_loader=lambda _:source, memory=_Memory(),
        model=Model(),identity=identity,connector_tools=(tools,),journal_factory=lambda *_:journal,clock=lambda:NOW)
    return source,spec,store,tools,journal,runner,identity

def test_two_tool_steps_finish_while_foreground_conversation_continues():
    async def run():
        source,spec,store,tools,journal,runner,identity=fixture()
        task=asyncio.create_task(runner.run(spec,call_id="call:one"))
        assert await asyncio.to_thread(tools.started.wait,2)
        class Contact:
            def generate(self,*args,**kwargs):return ConversationModelResult("Goed, dank je!", "controlled")
        reply=await asyncio.wait_for(asyncio.to_thread(LivingConversation(_Memory(),Contact(),identity).respond,
            ConversationTurn.private("tenant","install","owner","chat","other","Hoe gaat het?",source.occurred_at)),2)
        assert reply.text=="Goed, dank je!" and not task.done()
        tools.release.set();outcome=await asyncio.wait_for(task,3)
        assert outcome.status=="succeeded",outcome
        assert [c[2] for c in tools.calls]==[0,1]
        assert all(c[0]==source and c[3] for c in tools.calls)
        assert outcome.usage.completed_model_responses==3 and outcome.usage.tool_calls==2
        assert outcome.usage.cost_usd_micros is None
        _validate_usage(spec,outcome.usage,successful=True)
    asyncio.run(run())

def test_outer_cancel_waits_for_connector_cleanup_without_second_step():
    async def run():
        _,spec,_,tools,_,runner,_=fixture()
        task=asyncio.create_task(runner.run(spec,call_id="call:cancel"))
        assert await asyncio.to_thread(tools.started.wait,2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(task,3)
        assert len(tools.calls)==1
    asyncio.run(run())


def test_revoked_job_stops_between_tool_steps():
    async def run():
        _,spec,store,tools,_,runner,_=fixture()
        task=asyncio.create_task(runner.run(spec,call_id="call:stop"))
        assert await asyncio.to_thread(tools.started.wait,2)
        store.stopped=True
        tools.release.set()
        outcome=await asyncio.wait_for(task,3)
        assert outcome.result is None
        assert len(tools.calls)==1
    asyncio.run(run())


def test_copied_release_hash_cannot_expand_grant():
    _,spec,_,tools,_,runner,_=fixture()
    forged=spec.model_copy(update={"grant":spec.grant.model_copy(update={"maximum_tool_calls":32})})
    outcome=asyncio.run(runner.run(forged,call_id="call:forged"))
    assert outcome.result is None and not tools.calls


def test_stop_guard_is_checked_at_external_dispatch(monkeypatch):
    from types import SimpleNamespace
    import eva.adapters.postgres.external_conversation_tools as module
    from eva.application.background_execution import BackgroundExecutionScope, background_execution
    source,_,_,_,_,_,_=fixture()
    calls=[]
    class Provider:
        async def execute(self,request):calls.append(request)
    monkeypatch.setattr(module,'PostgresConversationJournal',lambda *_:SimpleNamespace(admit_dispatch=lambda *_:None))
    monkeypatch.setattr(module,'VerifiedToolOrchestrator',lambda timeline,provider:provider)
    tool=module.PostgresExternalConversationTools(None,SimpleNamespace(action_names=('use_connector_tool',)),Provider())
    def revoked(turn):raise ConversationConflict('stopped after prepare')
    with background_execution(BackgroundExecutionScope(source,'work:stop',revoked)):
        with pytest.raises(ConversationConflict,match='stopped after prepare'):
            asyncio.run(tool._orchestrator(source,None).execute(SimpleNamespace(operation_id='operation:test')))
    assert not calls


def test_foreground_returns_receipt_without_polling_digital_work():
    source,_,_,_,_,_,identity=fixture()
    calls=[]
    class Actions:
        action_names=("start_digital_work","get_delegated_work")
        def pending(self,turn):return None
        def active_context(self,turn):return ()
        def available_action_names(self,turn):return self.action_names
        def continuation_action_names(self,turn):return self.action_names
        def execute(self,turn,call):
            calls.append(call.name)
            return ConversationActionReceipt(call,{"status":"verified","work_id":"work:one"})
    class Contact:
        def generate(self,*args,**kwargs):
            if kwargs['completed_action'] is None:
                return ConversationModelResult(None,"contact",ConversationActionCall("admit","start_digital_work",{"task":"Compare the documents"}))
            assert kwargs['available_actions']==()
            return ConversationModelResult("Ik ga ermee aan de slag.","contact")
    class Slow:
        def generate(self,*args,**kwargs):raise AssertionError("receipt should stay on contact model")
    reply=LivingConversation(_Memory(),Slow(),identity,Actions(),contact_model=Contact()).respond(source)
    assert reply.text=="Ik ga ermee aan de slag."
    assert calls==["start_digital_work"]


def test_resume_reuses_verified_first_step_instead_of_repeating_effect():
    async def run():
        source,spec,store,tools,_,runner,_=fixture()
        saved={}
        execute=tools.execute
        def persist(turn,call,*,step_index):
            receipt=execute(turn,call,step_index=step_index)
            saved[(background_execution_namespace(turn),step_index)]=receipt
            if step_index==0:store.stopped=True
            return receipt
        tools.execute=persist
        tools.pending=lambda turn,step_index: saved.get((background_execution_namespace(turn),step_index))
        tools.release.set()
        first=await runner.run(spec,call_id="call:first")
        assert first.result is None and len(tools.calls)==1
        store.stopped=False
        runner.journal_factory=lambda *_:Journal()
        resumed=spec.model_copy(update={"work_id":"work:second-attempt","generation":2,"parent_task_id":spec.work_id})
        result=await runner.run(resumed,call_id="call:resume")
        assert result.status=="succeeded",result
        assert [call[2] for call in tools.calls]==[0,1]
        assert tools.calls[0][3]==tools.calls[1][3]
        assert result.usage.tool_calls == 1
    asyncio.run(run())


@pytest.mark.parametrize("interruption, expected", [
    (None, "provider_unavailable"),
    ("cancel", "cancelled"),
    ("deadline", "timed_out"),
])
def test_gateway_transport_failure_is_sanitized_without_retry(monkeypatch, interruption, expected):
    from urllib.error import URLError
    from eva.adapters.connectors import mcp_gateway as gateway
    from eva.application.background_execution import authorize_background_dispatch

    _, spec, _, tools, _, runner, _ = fixture()
    cancellation = threading.Event()
    original = URLError("https://private-host.invalid/?token=private-secret")
    requests = []
    def fail(request, **kwargs):
        requests.append(request)
        if interruption == "cancel":
            cancellation.set()
        if interruption == "deadline":
            runner.clock = lambda: NOW + timedelta(days=1)
        raise original
    monkeypatch.setattr(gateway.urllib_request, "urlopen", fail)
    adapter = gateway.McpGatewayToolAdapter.__new__(gateway.McpGatewayToolAdapter)
    adapter._config = gateway.McpGatewayConfig("http://gateway.invalid", "secret", allow_plain_http=True)
    def execute(turn, call, *, step_index):
        authorize_background_dispatch(turn)
        tools.calls.append((call.name, step_index))
        return adapter._request_json("GET", "/tools", None)
    tools.execute = execute
    outcome = asyncio.run(runner.run(spec, call_id="call:unavailable", cancellation_event=cancellation))
    assert outcome.status == expected
    assert outcome.result is None
    assert len(requests) == len(tools.calls) == 1
    assert outcome.usage.completed_model_responses == 1
    assert outcome.usage.tool_calls == 1
    assert outcome.usage.input_tokens == 30
    assert outcome.error_fingerprint == hashlib.sha256(
        b"McpGatewayUnavailable:MCP gateway request failed"
    ).hexdigest()


def test_saved_procedure_read_can_continue_to_a_verified_connector_action():
    from dataclasses import replace
    from eva.application.background_execution import authorize_background_dispatch
    source, old_spec, store, _, journal, runner, _ = fixture()
    calls = []
    class ProcedureTools:
        action_names = ("list_reusable_procedures", "resolve_reusable_procedure", "use_connector_tool")
        def available_for(self, turn): return True
        def active_context(self, turn): return ()
        def pending(self, turn, *, step_index): return None
        def execute(self, turn, call, *, step_index):
            calls.append(call.name)
            if call.name != "list_reusable_procedures": authorize_background_dispatch(turn)
            if call.name == "list_reusable_procedures":
                output = {"contract": "ProcedureReferenceAction/v1", "status": "candidates", "candidates": [{"document_id": "saved"}]}
            elif call.name == "resolve_reusable_procedure":
                output = {"contract": "ProcedureReferenceAction/v1", "status": "resolved", "references": [{"untrusted_content": "Compare both totals."}]}
            else: output = {"status": "verified", "result": "Comparison saved and read back"}
            return ConversationActionReceipt(call, output)
    class ProcedureModel:
        def generate(self, turn, identity, memory, recent_messages, **kwargs):
            index = len(calls)
            if index == 2:
                assert kwargs['completed_action'].output['operation_result']['references'][0]['untrusted_content'] == "Compare both totals."
            name = ProcedureTools.action_names[index] if index < 3 else None
            if name: assert name in kwargs['available_actions']
            return ConversationModelResult(None if name else "Comparison complete.", "controlled",
                None if name is None else ConversationActionCall(str(index), name, {}),
                usage=ConversationModelUsage.measured(tool_calls=int(name is not None), input_tokens=30, output_tokens=10))
    profile = replace(runner.profile, capability_ids=ProcedureTools.action_names)
    spec = profile.build_spec(source=old_spec.source.model_dump(mode="json"), source_ingress_sha256=old_spec.source_ingress_sha256,
        work_id=old_spec.work_id, episode_id=old_spec.episode_id, generation=1, parent_task_id=old_spec.parent_task_id,
        owner_principal_id=old_spec.owner_principal_id, principal_binding_id=old_spec.principal_binding_id,
        task=old_spec.task, created_at=NOW, deadline=NOW+timedelta(seconds=300))
    runner.profile, runner.connector_tools, runner.model = profile, (ProcedureTools(),), ProcedureModel()
    outcome = asyncio.run(runner.run(spec, call_id="call:procedure"))
    assert outcome.status == "succeeded", outcome
    assert calls == list(ProcedureTools.action_names)
    assert outcome.usage.tool_calls == 2


def test_procedure_discovery_or_missing_source_is_not_completed_task_evidence():
    from eva.bootstrap.delegated_digital_runner import BackgroundConnectorActions, _has_completed_tool_evidence
    def receipt(name, status, **extra):
        return BackgroundConnectorActions._receipt(ConversationActionReceipt(
            ConversationActionCall("read", name, {}), {"contract": "ProcedureReferenceAction/v1", "status": status, **extra}))
    assert not _has_completed_tool_evidence([receipt("list_reusable_procedures", "candidates")])
    assert not _has_completed_tool_evidence([receipt("resolve_reusable_procedure", "unavailable")])
    assert not _has_completed_tool_evidence([receipt("resolve_reusable_procedure", "resolved", references=[])])
    assert _has_completed_tool_evidence([receipt("resolve_reusable_procedure", "resolved", references=[{"untrusted_content": "Read data"}])])


def test_designation_requires_a_verified_reference_before_counting_completion():
    from eva.bootstrap.delegated_digital_runner import BackgroundConnectorActions, _has_completed_tool_evidence
    from eva.application.conversation import ConversationActionCall, ConversationActionReceipt
    call = ConversationActionCall("save-reference", "designate_reusable_procedure", {})
    def receipt(status, **fields):
        return BackgroundConnectorActions._receipt(ConversationActionReceipt(call,
            {"contract": "ProcedureReferenceAction/v1", "status": status, **fields}))
    assert not _has_completed_tool_evidence([receipt("designated")])
    assert not _has_completed_tool_evidence([receipt("unavailable")])
    assert _has_completed_tool_evidence([receipt("designated", reference={"document_id": "verified-save-reference"})])


@pytest.mark.parametrize("limit", ["input", "output", "models", "request_admission"])
def test_exhausted_attempt_is_budget_failure_with_preserved_usage(monkeypatch, limit):
    from types import SimpleNamespace
    from eva.application.conversation import ConversationExecutionLimitReached
    import eva.bootstrap.delegated_digital_runner as module
    import eva.adapters.postgres.work_corrections as corrections

    source, spec, store, tools, journal, runner, _ = fixture()
    store._engine = None
    actual = SimpleNamespace(measurement="measured",
        model_attempts=spec.grant.maximum_model_runs + 1 if limit == "models" else 5,
        input_tokens=66910 if limit == "input" else 60,
        output_tokens=spec.grant.maximum_output_tokens + 1 if limit == "output" else 521)
    journal.model_usage = lambda turn: actual
    monkeypatch.setattr(module, "BackgroundWorkProgress", lambda *a: SimpleNamespace(action_names=()))
    monkeypatch.setattr(module, "ComponentImprovementActions", lambda *a: SimpleNamespace(action_names=()))
    monkeypatch.setattr(corrections, "work_corrections", lambda *a: ())

    class ControlledConversation:
        def __init__(self, *args): pass
        def respond(self, turn, *, before_step, model_journal):
            if limit == "request_admission":
                raise ConversationExecutionLimitReached("conversation request limit reached")
            before_step(turn, actual)
            raise AssertionError("exhausted checkpoint must not continue")

    monkeypatch.setattr(module, "LivingConversation", ControlledConversation)
    outcome = asyncio.run(runner.run(spec, call_id="call:budget"))
    assert outcome.status == "budget_exceeded"
    assert outcome.result is None and outcome.retryable is False
    assert outcome.usage.completed_model_responses == actual.model_attempts
    assert outcome.usage.input_tokens == actual.input_tokens
    assert outcome.usage.output_tokens == actual.output_tokens
    assert outcome.usage.cost_usd_micros is None
    assert not tools.calls


@pytest.mark.parametrize("statuses, expected", [
    (["execution_failed"], False),
    (["execution_completed", "execution_failed"], False),
    (["execution_failed", "execution_completed"], True),
    (["execution_completed"], True),
])
def test_failed_procedure_is_not_hidden_by_preparation_or_saved_report(statuses, expected):
    from eva.bootstrap.delegated_digital_runner import BackgroundConnectorActions, _has_completed_tool_evidence
    def saved(identifier):
        return ConversationActionReceipt(ConversationActionCall(identifier, "save_workspace_document", {}),
                                         {"status": "verified"})
    receipts = [saved("prepare")]
    for index, status in enumerate(statuses):
        receipts.append(BackgroundConnectorActions._receipt(ConversationActionReceipt(
            ConversationActionCall(str(index), "run_reusable_procedure", {}),
            {"contract": "ProcedureReferenceAction/v1", "status": status})))
    receipts.append(saved("report"))
    assert _has_completed_tool_evidence(receipts) is expected


def test_unfinished_tool_execution_returns_actionable_incomplete_state(monkeypatch):
    from types import SimpleNamespace
    import eva.bootstrap.delegated_digital_runner as module
    import eva.adapters.postgres.work_corrections as corrections
    source, spec, store, tools, journal, runner, _ = fixture()
    monkeypatch.setattr(module, "BackgroundWorkProgress", lambda *a: SimpleNamespace(action_names=()))
    monkeypatch.setattr(module, "ComponentImprovementActions", lambda *a: SimpleNamespace(action_names=()))
    monkeypatch.setattr(module, "load_work_progress", lambda *a: None)
    monkeypatch.setattr(corrections, "work_corrections", lambda *a: ())
    class ControlledConversation:
        def __init__(self, memory, model, identity, actions): self.actions = actions
        def respond(self, turn, *, before_step, model_journal):
            self.actions.receipts.extend([
                ConversationActionReceipt(ConversationActionCall("save", "save_workspace_document", {}),
                                          {"status": "verified"}),
                module.BackgroundConnectorActions._receipt(ConversationActionReceipt(
                    ConversationActionCall("run", "run_reusable_procedure", {}),
                    {"contract": "ProcedureReferenceAction/v1", "status": "execution_failed"}))])
            return SimpleNamespace(text="The procedure failed.")
    monkeypatch.setattr(module, "LivingConversation", ControlledConversation)
    outcome = asyncio.run(runner.run(spec, call_id="call:failed-procedure"))
    assert outcome.status == "incomplete_work"
    assert outcome.result is None and outcome.retryable is False
    assert outcome.usage.measurement == "unmeasured"


def test_reading_previous_result_does_not_complete_the_new_assignment():
    from eva.bootstrap.delegated_digital_runner import _has_completed_tool_evidence
    read = ConversationActionReceipt(ConversationActionCall("prior", "get_delegated_work", {"work_id": "work:prior"}),
        {"status": "verified", "operation_result": {"work": {"state": "succeeded", "result": {"output": "Old result"}}}})
    assert not _has_completed_tool_evidence([read])
    new_result = ConversationActionReceipt(ConversationActionCall("save", "save_workspace_document", {}),
        {"status": "verified", "result": "New task result saved and verified"})
    assert _has_completed_tool_evidence([read, new_result])


def _automation_receipt(method, identifier="first", *, arguments=None, result=None):
    args = {"transport": "rest", "method": method,
            "path": "/api/config/automation/config/" + identifier,
            "body_json": "", "response_pointer": "", "offset": 0, "limit": 50}
    args.update(arguments or {})
    response = {"success": True, "truncated": False, "response_json": '{"alias":"Example"}',
                "total": 1, "next_offset": None, "external_state_verified": False}
    response.update(result or {})
    return ConversationActionReceipt(ConversationActionCall(method+":"+identifier,
        "home_assistant_request", args), {"status": "verified", "result": response})


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
def test_automation_write_requires_later_full_matching_readback(method):
    from eva.bootstrap.delegated_digital_runner import _has_completed_tool_evidence as completed
    write, read = _automation_receipt(method), _automation_receipt("GET")
    assert not completed([write])
    assert not completed([read, write])
    assert not completed([write, _automation_receipt("GET", "other")])
    assert completed([write, read])
    assert not completed([write, read, write])
    assert completed([write, read, write, read])
    saved = ConversationActionReceipt(ConversationActionCall("save", "save_workspace_document", {}), {"status": "verified"})
    assert not completed([write, saved])


@pytest.mark.parametrize("arguments,result", [
    ({}, {"success": False}),
    ({}, {"truncated": True}),
    ({}, {"selection_error": "missing"}),
    ({"response_pointer": "/alias"}, {}),
    ({"offset": 1}, {}),
    ({}, {"next_offset": 1}),
    ({}, {"total": 2}),
    ({}, {"response_json": '"partial text"'}),
    ({}, {"response_json": "invalid"}),
    ({"path": "/api/config/automation/config/first?view=partial"}, {}),
])
def test_incomplete_automation_read_is_not_configuration_verification(arguments, result):
    from eva.bootstrap.delegated_digital_runner import _has_completed_tool_evidence
    assert not _has_completed_tool_evidence([
        _automation_receipt("POST"), _automation_receipt("GET", arguments=arguments, result=result)])


def test_multiple_automation_targets_require_their_own_readbacks():
    from eva.bootstrap.delegated_digital_runner import _has_completed_tool_evidence as completed
    writes = [_automation_receipt("POST"), _automation_receipt("POST", "second")]
    assert not completed([*writes, _automation_receipt("GET")])
    assert completed([*writes, _automation_receipt("GET"), _automation_receipt("GET", "second")])
    assert completed([_automation_receipt("POST", "%66irst"), _automation_receipt("GET")])
    # Other endpoint protocols and DELETE are outside this readback guarantee.
    assert completed([_automation_receipt("POST", arguments={"path": "/api/services/light/turn_on"})])
    assert completed([_automation_receipt("DELETE")])
    assert not completed([*writes, _automation_receipt("POST", arguments={"path": "/api/services/light/turn_on"})])


def _automation_batch(*, requested=None, rows=None, section="automation_configurations"):
    return ConversationActionReceipt(ConversationActionCall("batch", "read_home_assistant_inventory",
        {"section": section, "offset": 0, "automation_ids": requested or ["first"],
         "area_id": "", "snapshot_sha256": ""}),
        {"status": "verified", "inventory": {"automations": rows if rows is not None else [
            {"automation_id": "first", "configuration_read": True,
             "configuration_json": '{"alias":"Example"}', "coverage": {"complete": True}}]}})


def test_preferred_full_batch_readback_discharges_only_matching_writes():
    from eva.bootstrap.delegated_digital_runner import _has_completed_tool_evidence as completed
    write, batch = _automation_receipt("POST"), _automation_batch()
    assert completed([write, batch])
    assert not completed([batch, write])
    assert not completed([write, batch, write])
    assert not completed([write, _automation_batch(section="automation_details")])
    assert not completed([write, _automation_batch(requested=["other"])])
    full = batch.output["inventory"]["automations"][0]
    partial = {"automation_id": "second", "configuration_read": False, "coverage": {"complete": False}}
    mixed = _automation_batch(requested=["first", "second"], rows=[full, partial])
    assert completed([write, mixed])
    assert not completed([write, _automation_receipt("POST", "second"), mixed])
    assert completed([_automation_receipt("POST", "%66irst"), batch])


@pytest.mark.parametrize("change", [
    {"automation_id": "other"}, {"configuration_read": False},
    {"coverage": {"complete": False}}, {"configuration_json": None},
    {"configuration_json": '"projection"'}, {"truncated": True},
])
def test_partial_or_wrong_batch_row_does_not_discharge_write(change):
    from eva.bootstrap.delegated_digital_runner import _has_completed_tool_evidence
    row = _automation_batch().output["inventory"]["automations"][0]
    assert not _has_completed_tool_evidence([
        _automation_receipt("POST"), _automation_batch(rows=[{**row, **change}])])


@pytest.mark.parametrize("overrun_on_final", [True, False])
def test_budget_failure_keeps_only_a_returned_final_reply(overrun_on_final):
    _, spec, _, tools, journal, runner, _ = fixture()
    tools.release.set()
    class OverrunModel(Model):
        def generate(self, *args, **kwargs):
            from dataclasses import replace
            result = super().generate(*args, **kwargs)
            if (result.action_call is None) == overrun_on_final:
                return replace(result, usage=ConversationModelUsage.measured(
                    tool_calls=int(result.action_call is not None),
                    input_tokens=spec.grant.maximum_input_tokens + 1, output_tokens=10))
            return result
    runner.model = OverrunModel()
    outcome = asyncio.run(runner.run(spec, call_id="call:budget-report"))
    assert outcome.status == "budget_exceeded" and outcome.result is None
    assert outcome.failure_report == ("The comparison was stored and read back."
                                      if overrun_on_final else None)
    assert len(journal.usages) == (3 if overrun_on_final else 1)
    assert len(tools.calls) == (2 if overrun_on_final else 0)
    assert outcome.usage.input_tokens > spec.grant.maximum_input_tokens


def test_cancelled_final_reply_is_not_released_as_failure_report():
    _, spec, store, tools, _, runner, _ = fixture()
    tools.release.set()
    stopped = threading.Event()
    class CancelAtFinal(Model):
        def generate(self, *args, **kwargs):
            result = super().generate(*args, **kwargs)
            if result.action_call is None:
                store.stopped = True
                stopped.set()
            return result
    runner.model = CancelAtFinal()
    outcome = asyncio.run(runner.run(spec, call_id="call:cancel-report", cancellation_event=stopped))
    assert outcome.status == "cancelled" and outcome.result is None
    assert outcome.failure_report is None


def _budget_snapshot(context):
    import json
    item = next(value for value in context if value.startswith("Resterend budget van deze poging"))
    return json.JSONDecoder().raw_decode(item[item.index("{"):])[0]


def test_model_sees_updated_budget_without_extra_requests():
    _, spec, _, tools, journal, runner, _ = fixture()
    tools.release.set()
    snapshots = []
    class ObservingModel(Model):
        def generate(self, *args, **kwargs):
            snapshots.append(_budget_snapshot(kwargs["self_context"]))
            return super().generate(*args, **kwargs)
    runner.model = ObservingModel()
    outcome = asyncio.run(runner.run(spec, call_id="call:budget-guidance"))
    assert outcome.status == "succeeded"
    assert len(snapshots) == len(journal.usages) == 3
    assert snapshots[0]["measurement"] == "unmeasured"
    assert snapshots[0]["remaining"]["input_tokens"] is None
    for index in (1, 2):
        snapshot = snapshots[index]
        assert snapshot["used"]["model_runs"] == index
        assert snapshot["used"]["input_tokens"] == index * 30
        assert snapshot["used"]["output_tokens"] == index * 10
        assert snapshot["used"]["tool_calls"] == index
        assert snapshot["used"]["tool_dispatches"] == index
        assert snapshot["remaining"]["model_runs"] == spec.grant.maximum_model_runs - index
        assert snapshot["remaining"]["input_tokens"] == spec.grant.maximum_input_tokens - index * 30
        assert snapshot["remaining"]["tool_calls"] == spec.grant.maximum_tool_calls - index


def test_partial_measurement_does_not_invent_remaining_token_capacity():
    from eva.bootstrap.delegated_digital_runner import _remaining_budget_context
    _, spec, *_ = fixture()
    actual = ConversationModelUsage.partial(model_attempts=2, tool_calls=1, input_tokens=30, output_tokens=10)
    snapshot = _budget_snapshot((_remaining_budget_context(spec.grant, actual, calls=1, dispatches=3),))
    assert snapshot["used"]["input_tokens"] == 30
    assert snapshot["remaining"]["input_tokens"] is None
    assert snapshot["remaining"]["output_tokens"] is None
    assert snapshot["remaining"]["model_runs"] is None
    assert snapshot["remaining"]["tool_calls"] == max(0, spec.grant.maximum_tool_calls - 3)


def _budget_spec(profile, previous, *, grant=None, resumed=False):
    return profile.build_spec(source=previous.source.model_dump(mode="json"),
        source_ingress_sha256=previous.source_ingress_sha256, grant=grant,
        execution_id=previous.execution_id, work_id="work:resumed" if resumed else previous.work_id,
        episode_id=previous.episode_id, generation=2 if resumed else 1,
        parent_task_id=previous.work_id if resumed else previous.parent_task_id,
        owner_principal_id=previous.owner_principal_id, principal_binding_id=previous.principal_binding_id,
        task=previous.task, created_at=NOW, deadline=NOW+timedelta(seconds=300))


@pytest.mark.parametrize("legacy, expected", [(False, "succeeded"), (True, "budget_exceeded")])
def test_response_budget_is_not_reused_as_whole_task_budget(legacy, expected):
    from dataclasses import replace
    _, old, _, tools, _, runner, _ = fixture()
    runner.profile = replace(runner.profile, maximum_response_output_tokens=1200, maximum_output_tokens=9600)
    grant = runner.profile.grant()
    if legacy:
        grant = grant.model_copy(update={"maximum_output_tokens":1200,
                                        "maximum_total_tokens":grant.maximum_input_tokens+1200})
    spec = _budget_spec(runner.profile, old, grant=grant)
    class LargerOutput(Model):
        def generate(self, *args, **kwargs):
            result = super().generate(*args, **kwargs)
            return replace(result, usage=ConversationModelUsage.measured(
                tool_calls=int(result.action_call is not None), input_tokens=30, output_tokens=450))
    runner.model = LargerOutput()
    tools.release.set()
    outcome = asyncio.run(runner.run(spec, call_id="call:output-budget"))
    assert outcome.status == expected, outcome
    assert outcome.usage.output_tokens == 1350
    assert len(tools.calls) == 2


@pytest.mark.parametrize("correct_input", [False, True])
def test_legacy_resume_keeps_receipts_and_new_release_without_budget_expansion(correct_input):
    from dataclasses import replace
    async def run():
        _, old, store, tools, _, runner, _ = fixture()
        legacy_profile = replace(runner.profile, maximum_output_tokens=1200)
        runner.profile = legacy_profile
        spec = _budget_spec(legacy_profile, old)
        saved = {}
        execute = tools.execute
        def persist(turn, call, *, step_index):
            receipt = execute(turn, call, step_index=step_index)
            saved[(background_execution_namespace(turn), step_index)] = receipt
            if step_index == 0: store.stopped = True
            return receipt
        tools.execute = persist
        tools.pending = lambda turn, step_index: saved.get((background_execution_namespace(turn), step_index))
        tools.release.set()
        assert (await runner.run(spec, call_id="call:old")).result is None
        store.stopped = False
        runner.profile = replace(legacy_profile, maximum_response_output_tokens=1200, maximum_output_tokens=9600,
            **({"maximum_response_input_tokens": legacy_profile.maximum_input_tokens,
                "maximum_input_tokens": legacy_profile.maximum_input_tokens * 8} if correct_input else {}))
        runner.journal_factory = lambda *_: Journal()
        resumed = _budget_spec(runner.profile, spec, grant=spec.grant, resumed=True)
        assert resumed.grant == spec.grant
        assert resumed.worker_profile_release_sha256 == runner.profile.release.worker_profile_release_sha256
        assert resumed.worker_profile_release_sha256 != spec.worker_profile_release_sha256
        result = await runner.run(resumed, call_id="call:new")
        assert result.status == "succeeded", result
        assert [call[2] for call in tools.calls] == [0, 1]
        assert result.usage.tool_calls == 1
    asyncio.run(run())


def test_cumulative_task_cap_still_stops_before_unverified_completion():
    from dataclasses import replace
    _, old, _, tools, _, runner, _ = fixture()
    runner.profile = replace(runner.profile, maximum_response_output_tokens=1200,
        maximum_completed_model_responses=2, maximum_output_tokens=2400)
    spec = _budget_spec(runner.profile, old)
    class LargerOutput(Model):
        def generate(self, *args, **kwargs):
            result = super().generate(*args, **kwargs)
            return replace(result, usage=ConversationModelUsage.measured(
                tool_calls=int(result.action_call is not None), input_tokens=30, output_tokens=900))
    runner.model = LargerOutput()
    tools.release.set()
    outcome = asyncio.run(runner.run(spec, call_id="call:cumulative-cap"))
    assert outcome.status == "budget_exceeded", outcome
    assert outcome.result is None and outcome.usage.output_tokens == 2700


@pytest.mark.parametrize("prior_attempt, advance, expected_responses, expected_status", [
    (True, True, 3, "succeeded"),
    (False, True, 1, "incomplete_work"),
    (True, False, 2, "incomplete_work"),
])
def test_resume_reassesses_prior_blocker_but_current_or_unchanged_blocker_stops(
        monkeypatch, prior_attempt, advance, expected_responses, expected_status):
    import json
    import eva.bootstrap.delegated_digital_runner as module
    _, spec, _, tools, journal, runner, _ = fixture()
    spec = spec.model_copy(update={"work_id": "work:resume", "generation": 2,
                                  "parent_work_id": spec.work_id})
    progress = {"work_id": "work:one" if prior_attempt else spec.work_id,
                "revision": 1, "steps": [{"state": "blocked", "evidence": "Previous attempt tool budget exhausted"}]}
    monkeypatch.setattr(module, "load_work_progress", lambda *args: progress)
    tools.release.set()
    original_execute = tools.execute
    def execute(turn, call, *, step_index):
        receipt = original_execute(turn, call, step_index=step_index)
        progress.update(work_id=spec.work_id, revision=2, steps=[{"state": "done"}])
        return receipt
    tools.execute = execute
    class ResumeModel:
        responses = 0
        def generate(self, turn, identity, memory, recent_messages, **kwargs):
            self.responses += 1
            context = kwargs["self_context"]
            assert any(spec.work_id in line and "eerdere poging" in line for line in context)
            if self.responses == 1:
                budget_line = next(line for line in context if line.startswith("Resterend budget"))
                budget = json.JSONDecoder().raw_decode(budget_line[budget_line.index("{"):])[0]
                assert budget["used"]["tool_calls"] == 0
                assert budget["remaining"]["tool_calls"] == spec.grant.maximum_tool_calls
            call = (ConversationActionCall("resume-action", "use_connector_tool", {})
                    if self.responses == 2 and advance else None)
            return ConversationModelResult(None if call else "Saved blocker noted.", "controlled", call,
                usage=ConversationModelUsage.measured(tool_calls=int(call is not None), input_tokens=30, output_tokens=10))
    model = ResumeModel()
    runner.model = model
    outcome = asyncio.run(runner.run(spec, call_id="call:resume"))
    assert outcome.status == expected_status, outcome
    assert model.responses == expected_responses
    assert len(tools.calls) == int(prior_attempt and advance)
    assert len(journal.usages) == expected_responses


@pytest.mark.parametrize("failure", ["bounds", "pointer", "hash", "authority"])
def test_background_stored_selection_can_be_corrected_but_security_errors_stop(failure):
    import json
    from types import SimpleNamespace
    from eva.adapters.postgres.connector_results import ConnectorResults, result_capture
    from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
    from eva.application.background_execution import BackgroundExecutionScope, background_execution
    from eva.bootstrap.delegated_digital_runner import BackgroundConnectorActions
    from tests.connectors.test_connector_results import setup
    from tests.connectors.test_mcp_discovery import TURN
    adapter, command, provider_calls, initial = setup({"text": "Complete saved content " + "x" * 90000})
    dispatches = []
    with background_execution(BackgroundExecutionScope(TURN, "work:original", lambda turn: dispatches.append(turn))):
        capture = ConnectorResults(initial.timeline, TURN, lambda turn: None)
        with result_capture(capture):
            manifest = asyncio.run(adapter.execute(command)).details["invocation_response"]
        revoked = [False]
        def authorize(turn):
            if revoked[0]:
                raise ConversationConflict("owner authority revoked")
        route = PostgresExternalConversationTools(None,
            SimpleNamespace(action_names=("use_connector_tool",)),
            SimpleNamespace(supports_stored_results=True), authorize_turn=authorize)
        route._timeline = lambda turn: capture.timeline
        route._ensure_running = lambda turn: None
        route.pending = lambda turn, step_index=0: None
        actions = BackgroundConnectorActions((route,), ("use_connector_tool",), lambda turn: None, 6)
        class SelectionModel:
            responses = 0
            def generate(self, *args, **kwargs):
                self.responses += 1
                reader = manifest["reader"]["tool_id"]
                if self.responses == 1:
                    revoked[0] = failure == "authority"
                    if failure == "hash":
                        reader = reader[:-1] + ("a" if reader[-1] != "a" else "b")
                    selection = {"pointer": "/missing" if failure == "pointer" else "/text",
                                 "offset": 0, "limit": 8193 if failure == "bounds" else 100, "query": None}
                elif self.responses == 2:
                    assert "use_connector_tool" in kwargs["available_actions"]
                    assert kwargs["completed_action"].output["status"] == "selection_invalid"
                    selection = {"pointer": "/text", "offset": 0, "limit": 100, "query": None}
                else:
                    assert kwargs["completed_action"].output["result"]["text"].startswith("Complete saved content")
                    return ConversationModelResult("Saved content read.", "controlled")
                return ConversationModelResult(None, "controlled", ConversationActionCall(
                    "selection:" + str(self.responses), "use_connector_tool",
                    {"tool_id": reader, "arguments_json": json.dumps(selection)}))
        model = SelectionModel()
        conversation = LivingConversation(_Memory(), model, IdentityProfile("Eva", "Helpful", ("Be honest",)), actions)
        if failure in {"bounds", "pointer"}:
            reply = conversation.respond(TURN)
            assert reply.text == "Saved content read."
            assert model.responses == 3 and actions.calls == 2
            assert len(dispatches) == 2
            saved = [event for rows in capture.timeline.rows.values() for event in rows
                     if event.event_type == "CONNECTOR_SELECTION_READ"]
            assert len(saved) == 1
            data = saved[0].payload["data"]
            assert data["call_id"] == "selection:2"
            assert data["arguments"] == {"pointer": "/text", "offset": 0, "limit": 100, "query": None}
            assert data["output"]["sha256"] == manifest["sha256"]
            assert data["output"]["text"].startswith("Complete saved content")
            from eva.adapters.postgres.connector_results import _encoded
            assert data["output_sha256"] == hashlib.sha256(_encoded(data["output"])).hexdigest()
        else:
            with pytest.raises(ConversationConflict):
                conversation.respond(TURN)
            assert model.responses == 1
        assert len(provider_calls) == 1


@pytest.mark.parametrize("legacy, expected", [(False, "succeeded"), (True, "budget_exceeded")])
def test_multiple_responses_use_cumulative_input_without_expanding_legacy_grant(legacy, expected):
    from dataclasses import replace
    _, old, _, tools, _, runner, _ = fixture()
    runner.profile = replace(runner.profile, maximum_response_input_tokens=100,
                             maximum_input_tokens=800)
    grant = runner.profile.grant()
    if legacy:
        grant = grant.model_copy(update={"maximum_input_tokens": 100,
                                        "maximum_total_tokens": 100 + grant.maximum_output_tokens})
    spec = _budget_spec(runner.profile, old, grant=grant)
    snapshots = []
    class InputModel(Model):
        def generate(self, *args, **kwargs):
            snapshots.append(_budget_snapshot(kwargs["self_context"]))
            result = super().generate(*args, **kwargs)
            return replace(result, usage=ConversationModelUsage.measured(
                tool_calls=int(result.action_call is not None), input_tokens=45, output_tokens=10))
    runner.model = InputModel()
    tools.release.set()
    outcome = asyncio.run(runner.run(spec, call_id="call:input-budget"))
    assert outcome.status == expected, outcome
    assert outcome.usage.input_tokens == 135
    assert len(tools.calls) == 2
    assert snapshots[-1]["limits"]["input_tokens_per_response"] == 100
    assert snapshots[-1]["limits"]["input_tokens"] == (100 if legacy else 800)


@pytest.mark.parametrize("historical", [False, True])
def test_configured_envelope_completes_learning_path_and_final_answer(monkeypatch, historical):
    """Exercise the real runner/loop across discovery, source, skill and result steps."""
    from dataclasses import replace
    from types import SimpleNamespace
    import eva.bootstrap.delegated_digital_runner as module
    from eva.bootstrap.delegated_digital_composition import build_digital_profile
    from tests.bootstrap.test_delegated_digital_composition import configuration

    settings, bundle = configuration()
    names = ("discover_connector_tools", "use_connector_tool", "save_workspace_document",
             "designate_reusable_procedure", "run_reusable_procedure")
    _, previous, _, _, journal, runner, _ = fixture()
    runner.profile = replace(build_digital_profile(settings, bundle), capability_ids=names)
    # The production profile includes optional progress/component tools; this
    # deterministic fixture replaces only their persistence, not the task loop.
    monkeypatch.setattr(module, "ComponentImprovementActions", lambda *args: SimpleNamespace(
        action_names=(), available_for=lambda turn: False, resolve_document_call=lambda call: call))
    calls = (
        ConversationActionCall("discover", names[0], {}),
        ConversationActionCall("documentation", names[1], {"operation": "documentation"}),
        ConversationActionCall("input", names[1], {"operation": "input"}),
        ConversationActionCall("save-method", names[2], {"document": "method"}),
        ConversationActionCall("register", names[3], {}),
        ConversationActionCall("execute", names[4], {}),
        ConversationActionCall("readback", names[1], {"operation": "readback"}),
        ConversationActionCall("save-result", names[2], {"document": "result"}),
    )
    state = {"calls": [], "documents": {}}

    class LearningTools:
        action_names = names
        def available_for(self, turn): return True
        def active_context(self, turn): return ()
        def pending(self, turn, *, step_index): return None
        def with_preparation_call_resolver(self, resolver): return self
        def execute(self, turn, call, *, step_index):
            from eva.application.background_execution import authorize_background_dispatch
            authorize_background_dispatch(turn)
            assert call == calls[step_index]
            state["calls"].append(call.call_id)
            if step_index == 1:
                state["documentation"] = "Return the sum of the supplied values."
            elif step_index == 2:
                state["input"] = [4, 7, 6]
            elif step_index == 3:
                state["documents"]["method"] = state["documentation"]
            elif step_index == 4:
                assert state["documents"]["method"]
                return ConversationActionReceipt(call, {"contract": "ProcedureReferenceAction/v1",
                    "status": "designated", "reference": "procedure:controlled"})
            elif step_index == 5:
                state["result"] = sum(state["input"])
                return ConversationActionReceipt(call, {"contract": "ProcedureReferenceAction/v1",
                    "status": "execution_completed", "output": state["result"]})
            elif step_index == 6:
                assert state["result"] == 17
                state["verified"] = True
            elif step_index == 7:
                assert state["verified"]
                state["documents"]["result"] = state["result"]
            return ConversationActionReceipt(call, {"status": "verified"})

    class LearningModel:
        responses = 0
        def generate(self, *args, **kwargs):
            index = self.responses
            self.responses += 1
            call = calls[index] if index < len(calls) else None
            if call is None:
                assert state["documents"]["result"] == 17
            return ConversationModelResult(None if call else "Saved verified result: 17.", "controlled", call,
                usage=ConversationModelUsage.measured(tool_calls=int(call is not None),
                    input_tokens=100, output_tokens=20))

    grant = None
    if historical:
        grant = runner.profile.historical_grants[0].model_copy(update={"capability_ids": list(names)})
    spec = _budget_spec(runner.profile, previous, grant=grant)
    runner.model, runner.connector_tools = LearningModel(), (LearningTools(),)
    outcome = asyncio.run(runner.run(spec, call_id="call:complete-path"))
    if historical:
        assert outcome.status == "budget_exceeded", outcome
        assert spec.grant.maximum_model_runs == 8 and spec.grant.maximum_tool_calls == 6
        assert len(state["calls"]) == 6
        assert "result" not in state["documents"] and outcome.result is None
    else:
        assert outcome.status == "succeeded", outcome
        assert state["documents"]["result"] == 17
        assert outcome.result.output == "Saved verified result: 17."
        assert len(state["calls"]) == outcome.usage.tool_calls == 8
        assert len(journal.usages) == outcome.usage.completed_model_responses == 9
        _validate_usage(spec, outcome.usage, successful=True)


@pytest.mark.parametrize("variant", [0, 1, 2])
def test_new_envelope_resumes_exact_old_grant_without_replaying_completed_effect(variant):
    from dataclasses import replace
    from eva.bootstrap.delegated_digital_composition import build_digital_profile
    from tests.bootstrap.test_delegated_digital_composition import configuration

    source, previous, store, tools, _, runner, _ = fixture()
    settings, bundle = configuration()
    current = replace(build_digital_profile(settings, bundle), capability_ids=Tools.action_names)
    grant = current.historical_grants[variant].model_copy(update={"capability_ids": list(Tools.action_names)})
    old_profile = replace(runner.profile, maximum_input_tokens=grant.maximum_input_tokens,
        maximum_output_tokens=grant.maximum_output_tokens)
    old = _budget_spec(old_profile, previous, grant=grant)
    runner.profile = old_profile
    saved = {}
    execute = tools.execute
    def persist(turn, call, *, step_index):
        receipt = execute(turn, call, step_index=step_index)
        saved[(background_execution_namespace(turn), step_index)] = receipt
        if step_index == 0: store.stopped = True
        return receipt
    tools.execute = persist
    tools.pending = lambda turn, step_index: saved.get((background_execution_namespace(turn), step_index))
    tools.release.set()
    assert asyncio.run(runner.run(old, call_id="call:old-envelope")).result is None
    store.stopped = False
    runner.profile = current
    runner.journal_factory = lambda *_: Journal()
    resumed = _budget_spec(current, old, grant=old.grant, resumed=True)
    assert resumed.grant.to_payload() == old.grant.to_payload()
    assert resumed.execution_id == old.execution_id
    assert resumed.worker_profile_release_sha256 != old.worker_profile_release_sha256
    outcome = asyncio.run(runner.run(resumed, call_id="call:resumed-envelope"))
    assert outcome.status == "succeeded", outcome
    assert [call[2] for call in tools.calls] == [0, 1]
    assert outcome.usage.tool_calls == 1



def test_runtime_allowance_usage_survives_final_result_and_workboard_serialization():
    from dataclasses import replace
    from eva.bootstrap.delegated_digital_composition import build_digital_profile
    from eva.contracts.workboard import WorkBoardUsage
    from tests.bootstrap.test_delegated_digital_composition import configuration
    _, previous, _, tools, _, runner, _ = fixture()
    settings, bundle = configuration()
    settings.runtime_resource_budget = replace(settings.runtime_resource_budget, max_input_tokens=65536)
    runner.profile = replace(build_digital_profile(settings, bundle), capability_ids=Tools.action_names)
    spec = _budget_spec(runner.profile, previous)
    class ModelAtDeclaredLimits:
        responses = 0
        def generate(self, *args, **kwargs):
            self.responses += 1
            call = (ConversationActionCall("action:" + str(self.responses), "use_connector_tool", {})
                    if self.responses < 32 else None)
            return ConversationModelResult(None if call else "Verified task result.", "controlled", call,
                usage=ConversationModelUsage.measured(tool_calls=int(call is not None),
                    input_tokens=65536, output_tokens=1200))
    runner.model = ModelAtDeclaredLimits()
    tools.release.set()
    outcome = asyncio.run(runner.run(spec, call_id="call:configured-allowance"))
    assert outcome.status == "succeeded", outcome
    assert outcome.usage.completed_model_responses == 32 and outcome.usage.tool_calls == 31
    assert outcome.usage.input_tokens == 2097152
    assert outcome.usage.output_tokens == 38400
    _validate_usage(spec, outcome.usage, successful=True)
    usage = WorkBoardUsage(contract="EvaWorkBoardUsage/v1", schema_version=1,
        **{name: getattr(outcome.usage, name) for name in ("measurement", "input_tokens",
            "output_tokens", "total_tokens", "cost_usd_micros")})
    assert WorkBoardUsage.model_validate(usage.model_dump(mode="json")).total_tokens == 2135552


@pytest.mark.parametrize("typed, cancelled, expected", [
    (True, False, "timed_out"),
    (True, True, "cancelled"),
    (False, False, "internal_error"),
])
def test_model_deadline_keeps_usage_and_prior_effect_without_retry(typed, cancelled, expected):
    from eva.adapters.model.contracts import ConversationModelDeadlineExceeded, ConversationModelError
    _, spec, _, tools, journal, runner, _ = fixture()
    cancellation = threading.Event()
    grant_before = spec.grant.to_payload()
    error_type = ConversationModelDeadlineExceeded if typed else ConversationModelError
    class DeadlineAfterEffect(Model):
        requests = 0
        def generate(self, *args, **kwargs):
            self.requests += 1
            if kwargs.get("completed_action") is not None:
                if cancelled:
                    cancellation.set()
                raise error_type("conversation provider deadline exceeded")
            return super().generate(*args, **kwargs)
    model = DeadlineAfterEffect()
    runner.model = model
    tools.release.set()
    outcome = asyncio.run(runner.run(spec, call_id="call:model-deadline",
                                    cancellation_event=cancellation))
    assert runner.clock() == NOW  # The overall admitted task still has time.
    assert outcome.status == expected and outcome.result is None
    assert outcome.failure_report is None
    assert model.requests == 2 and len(tools.calls) == 1
    assert len(journal.usages) == outcome.usage.completed_model_responses == 1
    assert outcome.usage.tool_calls == 1
    assert outcome.usage.input_tokens == 30 and outcome.usage.output_tokens == 10
    assert outcome.error_fingerprint == hashlib.sha256(
        (error_type.__name__ + ":conversation provider deadline exceeded").encode()).hexdigest()
    assert spec.grant.to_payload() == grant_before


@pytest.mark.parametrize("legacy, prior_output, expected", [
    (False, 0, "succeeded"), (True, 0, "internal_error"), (False, 37_200, "internal_error")])
def test_real_adapter_uses_remaining_task_output_for_longer_tool_request(tmp_path, legacy, prior_output, expected):
    from dataclasses import replace
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    from eva.application.conversation import ConversationToolDescriptor
    from tests.model.test_pydantic_conversation_model import _runtime_budget, _TaskOutputUsageModel
    from tests.support.scripted_pydantic_conversation import ScriptedPydanticConversation, ScriptedPhase, ScriptedCall
    _, old, _, tools, _, runner, _ = fixture()
    runner.profile = replace(runner.profile, maximum_response_output_tokens=1_200,
        maximum_response_input_tokens=65_536, maximum_input_tokens=2_097_152,
        maximum_output_tokens=38_400, maximum_model_runs=32,
        maximum_completed_model_responses=32, maximum_tool_calls=31)
    grant = runner.profile.grant()
    if legacy:
        grant = runner.profile.compatible_grants()[-1]
    spec = _budget_spec(runner.profile, old, grant=grant)
    scripted = ScriptedPydanticConversation((
        ScriptedPhase.deferred(ScriptedCall("discover", "discover_connector_tools", {})),
        ScriptedPhase.deferred(ScriptedCall("perform", "use_connector_tool", {})),
        ScriptedPhase.text("The comparison was stored and read back.")))
    scripted.model = _TaskOutputUsageModel(scripted.model, (82, 1_634, 63))
    class DurableJournal(Journal):
        def complete_model_request(self, turn, request, usage, **kwargs): self.usages.append(usage)
    journal = DurableJournal()
    if prior_output:
        journal.usages.append(ConversationModelUsage.measured(
            model_attempts=1, tool_calls=0, input_tokens=100, output_tokens=prior_output))
    runner.journal_factory = lambda *_: journal
    runner.model = PydanticConversationModel(auth_file=tmp_path/"unused.json", model_backend=scripted.model,
        resource_budget=_runtime_budget(max_input_tokens=65_536), conversation_tools=tuple(
            ConversationToolDescriptor(name, "Controlled task tool", {"type":"object", "properties":{}, "required":[], "additionalProperties":False})
            for name in tools.action_names))
    tools.release.set()
    outcome = asyncio.run(runner.run(spec, call_id="call:long-output"))
    assert outcome.status == expected, outcome
    assert [call[2] for call in tools.calls] == ([0, 1] if expected == "succeeded" else [0])
    assert len(scripted.captures) == (3 if expected == "succeeded" else 2)
    assert spec.grant == grant
    if expected == "succeeded":
        assert outcome.result is not None and 1_779 <= outcome.usage.output_tokens < grant.maximum_output_tokens
        assert runner.model._resource_budget.max_output_tokens == 1_200


@pytest.mark.parametrize("prior", ["exhausted", "unknown"])
def test_background_unknown_or_exhausted_usage_stops_before_new_admission(tmp_path, prior):
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    from tests.model.test_pydantic_conversation_model import _runtime_budget
    from tests.support.scripted_pydantic_conversation import ScriptedPydanticConversation, ScriptedPhase
    _, spec, _, tools, journal, runner, _ = fixture()
    # Production journals convert an unknown admitted request to partial zeros.
    journal.usages.append((ConversationModelUsage.partial if prior == "unknown" else ConversationModelUsage.measured)(
        model_attempts=1, tool_calls=0, input_tokens=0,
        output_tokens=0 if prior == "unknown" else spec.grant.maximum_output_tokens))
    admissions = []
    journal.begin_model_request = lambda turn: admissions.append(turn)
    scripted = ScriptedPydanticConversation((ScriptedPhase.text("No call allowed."),))
    runner.model = PydanticConversationModel(auth_file=tmp_path/"unused.json", model_backend=scripted.model,
        resource_budget=_runtime_budget(max_input_tokens=65_536))
    # No offered descriptors are needed to establish the pre-admission bound.
    tools.available_for = lambda turn: False
    outcome = asyncio.run(runner.run(spec, call_id="call:blocked-output"))
    assert outcome.status == "budget_exceeded", outcome
    assert admissions == scripted.captures == tools.calls == []



def test_unknown_usage_after_unavailable_failure_cannot_start_internal_retry(tmp_path):
    from eva.adapters.model.openai_codex_provider import CodexProviderError
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    from tests.model.test_pydantic_conversation_model import _runtime_budget
    from tests.support.scripted_pydantic_conversation import ScriptedPydanticConversation, ScriptedPhase
    _, spec, _, tools, _, runner, _ = fixture()
    admissions = []
    class DurableJournal(Journal):
        def begin_model_request(self, turn):
            admissions.append(turn)
            return str(len(admissions))
        def complete_model_request(self, turn, request, usage, **kwargs): self.usages.append(usage)
        def model_usage(self, turn):
            return ConversationModelUsage.aggregate(tuple(
                ConversationModelUsage.partial(model_attempts=1, tool_calls=0, input_tokens=0, output_tokens=0)
                if usage.measurement == "unmeasured" else usage for usage in self.usages))
    journal = DurableJournal()
    runner.journal_factory = lambda *_: journal
    scripted = ScriptedPydanticConversation((ScriptedPhase.failure(CodexProviderError("unavailable")),
                                            ScriptedPhase.text("Retry must not start.")))
    runner.model = PydanticConversationModel(auth_file=tmp_path/"unused.json", model_backend=scripted.model,
        resource_budget=_runtime_budget(max_input_tokens=65_536))
    tools.available_for = lambda turn: False
    outcome = asyncio.run(runner.run(spec, call_id="call:unknown-retry"))
    assert outcome.status == "budget_exceeded", outcome
    assert len(admissions) == len(scripted.captures) == 1
    assert tools.calls == [] and journal.usages == [ConversationModelUsage.unmeasured()]


@pytest.mark.parametrize("boundary", ["task_deadline", "shorter_deadline", "legacy_grant", "owner_stop", "expired_before_admission"])
def test_task_boundary_closes_blocked_provider_with_longer_phase_limit(tmp_path, monkeypatch, boundary):
    """Exercise runner watcher through the real provider transport without networking."""
    from dataclasses import replace
    import httpx2
    from eva.adapters.model.openai_codex_provider import OpenAICodexProvider
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    from tests.model.test_pydantic_conversation_model import _runtime_budget
    _, original, _, tools, _, runner, _ = fixture()
    grant = runner.profile.grant()
    if boundary == "legacy_grant":
        grant = replace(runner.profile, timeout_seconds=30).grant()
        runner.profile = replace(runner.profile, historical_grants=(grant,))
    spec = _budget_spec(runner.profile, original, grant=grant)
    if boundary == "shorter_deadline":
        spec = spec.model_copy(update={"deadline": (NOW + timedelta(seconds=20)).strftime("%Y-%m-%dT%H:%M:%SZ")})
    grant_before = spec.grant.to_payload()
    clock = [NOW + timedelta(seconds=301) if boundary == "expired_before_admission" else NOW]
    runner.clock = lambda: clock[0]
    started, closed, owner_stop = threading.Event(), threading.Event(), threading.Event()
    requests, admissions, provider_limits, sessions = [], [], [], []
    class BlockedStream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            await asyncio.Event().wait()
            yield b"unreachable"
        async def aclose(self): closed.set()
    async def respond(request):
        requests.append(request)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                               stream=BlockedStream(), request=request)
    class LocalProvider(OpenAICodexProvider):
        def __init__(self, settings, *, credential_loader):
            provider_limits.append(settings.timeout_seconds)
            super().__init__(settings, credential_loader=lambda: ("test", "test"),
                             network_transport=httpx2.MockTransport(respond))
        async def __aenter__(self):
            session = await super().__aenter__()
            sessions.append(session)
            return session
    class DurableJournal(Journal):
        def begin_model_request(self, turn):
            admissions.append(turn)
            return str(len(admissions))
        def complete_model_request(self, turn, request, usage, **kwargs): self.usages.append(usage)
    monkeypatch.setattr("eva.adapters.model.pydantic_conversation.OpenAICodexProvider", LocalProvider)
    journal = DurableJournal()
    runner.journal_factory = lambda *_: journal
    runner.model = PydanticConversationModel(auth_file=tmp_path / "unused.json", timeout_seconds=120,
        resource_budget=_runtime_budget(max_input_tokens=65_536))
    tools.available_for = lambda turn: False
    async def run():
        task = asyncio.create_task(runner.run(spec, call_id="call:phase-boundary", cancellation_event=owner_stop))
        if boundary != "expired_before_admission":
            assert await asyncio.to_thread(started.wait, 3)
            if boundary == "owner_stop":
                owner_stop.set()
            else:
                clock[0] = NOW + timedelta(seconds={"task_deadline":301, "shorter_deadline":21, "legacy_grant":31}[boundary])
        outcome = await asyncio.wait_for(task, 3)
        assert outcome.status == ("cancelled" if boundary == "owner_stop" else "timed_out"), outcome
        assert outcome.result is None
    asyncio.run(run())
    expected_calls = 0 if boundary == "expired_before_admission" else 1
    assert len(requests) == len(admissions) == len(journal.usages) == expected_calls
    assert provider_limits == [120] * expected_calls
    assert tools.calls == [] and spec.grant.to_payload() == grant_before
    if expected_calls:
        assert closed.is_set() and sessions[0].local_stream_closed is True

def test_background_research_uses_single_request_context():
    from eva.application.background_execution import BackgroundExecutionScope, background_execution
    from eva.adapters.model.pydantic_conversation import _native_research_context
    source = ConversationTurn.private("tenant", "install", "owner", "chat", "message",
        "Research", "2026-09-28T19:00:00Z")
    assert not _native_research_context()
    with background_execution(BackgroundExecutionScope(source, "work:research")):
        assert _native_research_context()
    assert not _native_research_context()
