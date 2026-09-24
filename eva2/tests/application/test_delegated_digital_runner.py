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
