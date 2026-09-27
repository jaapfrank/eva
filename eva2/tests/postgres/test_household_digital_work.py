"""Household HA work uses the existing digital lifecycle and result return."""
import asyncio
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from eva.adapters.connectors.home_assistant_api import HomeAssistantApiTool
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.delegated_work import DelegatedWorkConflict, DelegatedWorkNotFound
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.scoped_delegated_work_actions import ScopedDelegatedWorkActions
from eva.adapters.projection.hindsight import HindsightMemoryEngine
from eva.application.conversation import ConversationActionCall, ConversationModelResult, ConversationModelUsage, GroundedReply, IdentityProfile
from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile
from eva.bootstrap.delegated_digital_runner import DelegatedDigitalRunner
from eva.bootstrap.household_digital_work import with_household_digital, HouseholdDigitalSourceLoader
from eva.bootstrap.household_work import HouseholdWorkActions
from tests.postgres.test_household_public_work import setup, DEVICE
from tests.postgres.test_scheduled_failure_return import database


def digital_setup(database, label):
    x = setup(database, label)
    root = Path(__file__).resolve().parents[2] / "src/eva"
    name = "bootstrap/delegated_digital_runner.py"
    profile = DelegatedDigitalProfile("model:controlled", "d" * 64, "e" * 64,
        ("home_assistant_request", "save_work_progress"),
        ((name, hashlib.sha256((root / name).read_bytes()).hexdigest()),))
    settings = SimpleNamespace(api_enabled=True,
        effect_principal_sha256_allowlist=(hashlib.sha256(("household-device:"+DEVICE).encode()).hexdigest(),))
    authority = with_household_digital(x.server.authority, profile, lambda: settings)
    x.server.authority = authority
    x.server.store._household_authorizer = authority.validate_spec
    x.service = x.server.session(**x.args, session_id=x.service.binding["session_id"])
    x.profile = profile
    return x


def test_household_digital_stop_resume_preserves_effects_and_returns_to_speaker(database, monkeypatch):
    x = digital_setup(database, "hh-digital-flow")
    turn = x.service.turn(str(uuid4()), "Zet de verwarming op 20 graden en controleer dit")
    actions = ScopedDelegatedWorkActions(None, x.server)
    assert "start_digital_work" in actions.available_action_names(turn)
    receipt = actions.execute(turn, ConversationActionCall("start-home", "start_digital_work", {"task":turn.text}))
    first_id = receipt.output["operation_result"]["work"]["work_id"]
    store = x.server.store
    first = store.load(first_id, **x.service._owner, household_origin=x.service.origin(turn)).spec
    journal = PostgresConversationJournal(database, x.server.conversation_scope)
    foreground = GroundedReply(turn.conversation_id, turn.message_id, "Ik ga ermee aan de slag.", (), turn.occurred_at, "contact")
    journal.commit_reply(turn, foreground)
    dispatched = []
    api = HomeAssistantApiTool(base_url="http://ha.invalid", token="fixture")
    def request(arguments):
        dispatched.append(arguments["method"])
        return {"success":True,"response_json":json.dumps({"temperature":20}),"external_state_verified":False}
    api._request = request
    class Memory:
        def recall_for_presence(self, query):
            assert query.audience.kind == "household"
            return HindsightMemoryEngine._empty_presence(query)
    class Model:
        stop_after_write = True
        def generate(self, source, identity, memory, recent_messages, *, completed_action=None, **kwargs):
            assert source == turn and source.audience.kind == "household"
            if completed_action is None:
                method, path, body = "POST", "/api/services/climate/set_temperature", '{"entity_id":"climate.lounge","temperature":20}'
            elif completed_action.call.arguments["method"] == "POST":
                if self.stop_after_write:
                    self.stop_after_write = False
                    # Another foreground request is admitted during active work.
                    small = x.service.turn(str(uuid4()), "Hoe gaat het?")
                    assert actions.available_action_names(small)
                    assert x.service.execute("list",small,request_key="list")["jobs"][0]["work_id"]==first_id
                    x.service.execute("stop", small, request_key="stop", work_id=first_id)
                method, path, body = "GET", "/api/states/climate.lounge", ""
            else:
                method = None
            call = None if method is None else ConversationActionCall("ha:"+method, "home_assistant_request", dict(
                transport="rest",method=method,path=path,body_json=body,response_pointer="",offset=0,limit=10))
            return ConversationModelResult("De verwarming staat op 20 graden." if call is None else None,
                "controlled",call,usage=ConversationModelUsage.measured(tool_calls=int(call is not None),input_tokens=30,output_tokens=10))
    runner = DelegatedDigitalRunner(store,x.profile,
        source_loader=HouseholdDigitalSourceLoader(database,x.server.conversation_scope,x.server.authority),
        memory=Memory(),model=Model(),identity=IdentityProfile("Eva","Warm",("Be honest",)),
        connector_tools=(PostgresExternalConversationTools(database,api,api,authorize_turn=x.service.validate_authority),),
        clock=lambda:x.clock[0])
    x.clock[0] += timedelta(seconds=1)
    claim = store.claim_next(worker_id="worker:hh-digital",release=x.profile.release,now=x.clock[0])
    call = store.start_call(claim,now=x.clock[0])
    outcome = asyncio.run(runner.run(first,call_id=call.call_id))
    assert outcome.result is None and dispatched == ["POST"], outcome
    store.record_stopped(call,usage=outcome.usage,now=x.clock[0])
    x.clock[0] += timedelta(seconds=1)
    resume = x.service.turn(str(uuid4()),"Ga verder")
    result = x.service.execute("resume",resume,request_key="resume",work_id=first_id)
    second = store.load(result["work"]["work_id"],**x.service._owner,household_origin=first.origin).spec
    assert second.execution_id==first.execution_id and second.source==first.source
    claim = store.claim_next(worker_id="worker:hh-digital",release=x.profile.release,now=x.clock[0])
    call = store.start_call(claim,now=x.clock[0])
    outcome = asyncio.run(runner.run(second,call_id=call.call_id))
    assert outcome.status=="succeeded",outcome
    assert dispatched==["POST","GET"]
    store.record_success(call,outcome.result,now=x.clock[0])
    delivered=x.service.poll(turn)
    assert delivered["result"]==outcome.result.output and delivered["origin_message_id"]==turn.message_id
    x.service.result_delivery(turn,"ack",work_id=second.work_id,result_sha256=delivered["result_sha256"],disposition="completed")
    assert x.service.poll(turn) is None
    assert journal.committed_reply(turn)==foreground
    with pytest.raises(DelegatedWorkNotFound):store.load(second.work_id,**x.service._owner)
    current_release = x.profile.release
    monkeypatch.setattr(DelegatedDigitalProfile, "release", property(lambda self:
        replace(current_release, worker_program_sha256="f" * 64)))
    assert store.load(second.work_id, **x.service._owner, household_origin=second.origin).result == outcome.result
    with pytest.raises(ValueError):x.server.authority.validate_spec(second)


def test_household_digital_policy_revocation_stops_admitted_work(database):
    x=digital_setup(database,"hh-digital-revoke")
    work=x.service.execute("digital",x.turn,request_key="one",task="Controleer automatiseringen")["work"]
    spec=x.server.store.load(work["work_id"],**x.service._owner,household_origin=x.service.origin(x.turn)).spec
    x.policies[0]=x.policies[0].model_copy(update={"state":"revoked"})
    assert not x.server.store.household_authority_current(SimpleNamespace(spec=spec))
    with pytest.raises((ValueError,RuntimeError)):
        x.service.execute("get",x.turn,request_key="read",work_id=work["work_id"])


def test_existing_public_research_remains_readable_after_program_update(database, monkeypatch):
    from eva.bootstrap.household_research_policy import HouseholdResearchAuthority
    from tests.postgres.test_household_public_work import start
    x=setup(database,"hh-research-preserved")
    identifier=start(x)
    old=x.server.authority
    release=old.profile.release
    monkeypatch.setattr(type(old.profile),"release",property(lambda self:
        replace(release,worker_program_sha256="f"*64)))
    current=HouseholdResearchAuthority(voice_authority=old.voice,
        policy_loader=old._policy_loader,budget_loader=old._budget_loader,
        provider_validator=old._provider_validator,profile=old.profile,clock=old.clock)
    x.server.authority=current
    x.server.store._household_authorizer=current.validate_spec
    work=x.server.store.load(identifier,**x.service._owner,household_origin=x.service.origin(x.turn))
    assert work.spec.worker_program_sha256==release.worker_program_sha256
    with pytest.raises(ValueError):current.validate_spec(work.spec)
    assert x.service.execute("stop",x.turn,request_key="stop-old",work_id=identifier)["work"]["state"]=="stopped"



@pytest.mark.parametrize("close_original", [False, True], ids=["open-origin", "closed-origin"])
def test_household_correction_survives_new_session_close_and_resumes_once(database, monkeypatch, close_original):
    from eva.adapters.postgres import scheduled_work as schedule_module
    from eva.adapters.postgres.work_corrections import work_corrections
    from eva.bootstrap.household_work_corrections import HouseholdScheduledWork
    from tests.postgres.test_native_task_continuity import native

    x = digital_setup(database, "hh-digital-correction-" + str(close_original))
    original = native(x, "original")
    turn = original.service.turn(str(uuid4()), "Zet de verwarming op 20 graden en controleer dit")
    actions = ScopedDelegatedWorkActions(None, x.server)
    first_id = actions.execute(turn, ConversationActionCall("start", "start_digital_work",
        {"task": turn.text})).output["operation_result"]["work"]["work_id"]
    store = x.server.store
    first_work = original.service._load(first_id, original.service.origin(turn))
    first = first_work.spec
    instruction = "Gebruik 19 graden in plaats van 20 en controleer de uiteindelijke temperatuur."
    corrected = []
    dispatched = []
    temperature = [18]
    api = HomeAssistantApiTool(base_url="http://ha.invalid", token="fixture")

    def request(arguments):
        if arguments["method"] == "POST":
            temperature[0] = json.loads(arguments["body_json"])["temperature"]
        dispatched.append((arguments["method"], temperature[0]))
        return {"success": True, "response_json": json.dumps({"temperature": temperature[0]}),
                "external_state_verified": False}
    api._request = request

    class Memory:
        def recall_for_presence(self, query):
            assert query.audience.kind == "household"
            return HindsightMemoryEngine._empty_presence(query)

    class Model:
        def generate(self, source, identity, memory, recent_messages, *,
                     completed_action=None, self_context=(), **kwargs):
            assert source == turn and source.audience.kind == "household"
            revised = any(instruction in item for item in self_context)
            if completed_action is None:
                name, target = "original-write", 20
            elif not corrected:
                current = native(x, "correction")
                correction_turn = current.service.turn(str(uuid4()), instruction)
                revision = ConversationActionCall("revise", "revise_digital_work",
                    {"revision": {"work_id": first_id, "instruction": instruction}})
                assert "revise_digital_work" in actions.available_action_names(correction_turn)
                receipt = actions.execute(correction_turn, revision)
                result = receipt.output["operation_result"]
                assert result["status"] == "correction_saved", result
                assert actions.pending(correction_turn) == receipt
                assert len(work_corrections(database, first)) == 1
                assert HouseholdScheduledWork(x.server).run_once() == "idle"
                corrected.append(result["schedule"]["schedule_id"])
                current.service.current("close")
                name, target = "blocked-read", None
            elif completed_action.call.arguments["method"] == "POST":
                assert revised, "The resumed worker must receive the persisted household correction"
                if json.loads(completed_action.call.arguments["body_json"])["temperature"] == 20:
                    name, target = "corrected-write", 19
                else:
                    name, target = "verify-correction", None
            else:
                assert revised
                return ConversationModelResult("De verwarming staat gecontroleerd op 19 graden.",
                    "controlled", usage=ConversationModelUsage.measured(tool_calls=0, input_tokens=30, output_tokens=10))
            call = ConversationActionCall(name, "home_assistant_request", dict(
                transport="rest", method="GET" if target is None else "POST",
                path="/api/states/climate.lounge" if target is None else "/api/services/climate/set_temperature",
                body_json="" if target is None else json.dumps({"entity_id": "climate.lounge", "temperature": target}),
                response_pointer="", offset=0, limit=10))
            return ConversationModelResult(None, "controlled", call,
                usage=ConversationModelUsage.measured(tool_calls=1, input_tokens=30, output_tokens=10))

    def authorize_worker_turn(source):
        assert source.audience.kind == "household"
        assert source.audience.principal_id == source.principal_id
        x.server.authority.validate_actor(source.principal_id)

    runner = DelegatedDigitalRunner(store, x.profile,
        source_loader=HouseholdDigitalSourceLoader(database, x.server.conversation_scope, x.server.authority),
        memory=Memory(), model=Model(), identity=IdentityProfile("Eva", "Warm", ("Be honest",)),
        connector_tools=(PostgresExternalConversationTools(database, api, api,
            authorize_turn=authorize_worker_turn),), clock=lambda: x.clock[0])
    x.clock[0] += timedelta(seconds=1)
    claim = store.claim_next(worker_id="worker:hh-correction", release=x.profile.release, now=x.clock[0])
    call = store.start_call(claim, now=x.clock[0])
    outcome = asyncio.run(runner.run(first, call_id=call.call_id))
    assert outcome.result is None and dispatched == [("POST", 20)]
    store.record_stopped(call, usage=outcome.usage, now=x.clock[0])
    if close_original:
        original.service.current("close")
        with pytest.raises(DelegatedWorkConflict):
            store.load(first_id, **original.service._owner, household_origin=first.origin)
    with pytest.raises(DelegatedWorkConflict, match="differs from its admitted execution"):
        store.load(first_id, **original.service._owner, household_origin=first.origin,
            household_continuation_execution_id="execution:unrelated")
    with pytest.raises(DelegatedWorkConflict, match="requires its original digital attempt"):
        store.admit(spec=first, title=first_work.title, summary=first_work.summary,
            worker_role=first_work.worker_role, retention_policy_id=first_work.retention_policy_id,
            retention_until=first_work.retention_until, occurred_at=x.clock[0],
            household_continuation_execution_id=first.execution_id)
    x.clock[0] += timedelta(seconds=20)

    class CoordinatorInterrupted(BaseException):
        pass
    transition = schedule_module.transition_timeline_thread
    def interrupted(*args, **kwargs):
        if kwargs.get("event_type") == "SCHEDULED_WORK_ADMITTED":
            raise CoordinatorInterrupted()
        return transition(*args, **kwargs)
    with monkeypatch.context() as crash:
        crash.setattr(schedule_module, "transition_timeline_thread", interrupted)
        with pytest.raises(CoordinatorInterrupted):
            HouseholdScheduledWork(x.server).run_once()
    assert HouseholdScheduledWork(x.server).run_once() == "dispatched"
    assert HouseholdScheduledWork(x.server).run_once() == "idle"
    schedule = HouseholdScheduledWork(x.server).list()["schedules"][0]
    assert schedule["schedule_id"] == corrected[0]
    second = store.load(schedule["work_id"], **original.service._owner,
        household_origin=first.origin, household_continuation_execution_id=first.execution_id).spec
    assert second.origin == first.origin and second.source == first.source
    assert second.execution_id == first.execution_id and second.grant == first.grant
    assert second.generation == first.generation + 1
    import sqlalchemy as sa
    from eva.adapters.postgres.current_catalog import delegated_work_runs
    with database.connect() as connection:
        store._scope(connection)
        assert connection.scalar(sa.select(sa.func.count()).select_from(delegated_work_runs).where(
            delegated_work_runs.c.tenant_id == store.scope.tenant_id,
            delegated_work_runs.c.installation_id == store.scope.installation_id,
            delegated_work_runs.c.parent_task_id == first_id)) == 1
    assert [item["instruction"] for item in work_corrections(database, second)] == [instruction]
    claim = store.claim_next(worker_id="worker:hh-correction", release=x.profile.release, now=x.clock[0])
    call = store.start_call(claim, now=x.clock[0])
    outcome = asyncio.run(runner.run(second, call_id=call.call_id))
    assert outcome.status == "succeeded", outcome
    assert dispatched == [("POST", 20), ("POST", 19), ("GET", 19)]
    store.record_success(call, outcome.result, now=x.clock[0])
    saved = store.load(second.work_id, **original.service._owner,
        household_origin=first.origin, household_continuation_execution_id=first.execution_id)
    assert saved.result == outcome.result and saved.spec.origin.destination() == first.origin.destination()
    if close_original:
        with pytest.raises(ValueError):
            original.service.poll(turn)
        current = native(x, "after-completion")
        assert current.service.poll(current.turn) is None
        assert current.service.execute("get", current.turn, request_key="get",
            work_id=second.work_id)["work"]["result"] == outcome.result.output
    else:
        result = original.service.poll(turn)
        assert result["work_id"] == second.work_id and result["origin_message_id"] == turn.message_id
        original.service.result_delivery(turn, "ack", work_id=second.work_id,
            result_sha256=result["result_sha256"], disposition="completed")
        assert original.service.poll(turn) is None
    with pytest.raises(DelegatedWorkNotFound):
        store.load(second.work_id, **original.service._owner)


@pytest.mark.parametrize("operation", ["stop", "stop_related"])
def test_later_household_stop_cancels_waiting_correction(database, operation):
    from eva.bootstrap.household_work_corrections import HouseholdScheduledWork
    from tests.postgres.test_native_task_continuity import native

    x = digital_setup(database, "hh-correct-stop-" + operation)
    original, current = native(x, "original"), native(x, "current")
    first_id = original.service.execute("digital", original.turn, request_key="start",
        task="Controleer de temperatuur")["work"]["work_id"]
    result = current.service.execute("revise_digital", current.turn, request_key="revision",
        revision={"work_id": first_id, "instruction": "Controleer ook de ingestelde doeltemperatuur."})
    assert result["status"] == "correction_saved", result
    current.service.execute(operation, current.turn, request_key="stop", work_id=first_id)
    scheduler = HouseholdScheduledWork(x.server)
    assert scheduler.list()["schedules"][0]["state"] == "cancelled"
    assert scheduler.run_once() == "idle"
    assert len(original.service.execute("list", original.turn, request_key="list")["jobs"]) == 1


def test_household_correction_keeps_device_boundary_and_current_policy(database):
    from eva.adapters.postgres.work_corrections import work_corrections
    from eva.bootstrap.household_work_corrections import HouseholdScheduledWork
    from tests.postgres.test_native_task_continuity import native

    x = digital_setup(database, "hh-correct-boundary")
    original = native(x, "original")
    first_id = original.service.execute("digital", original.turn, request_key="start",
        task="Controleer de temperatuur")["work"]["work_id"]
    first = original.service._load(first_id, original.service.origin(original.turn))
    other = native(x, "other", satellite="assist_satellite.office", device="b" * 32)
    revision = {"work_id": first_id, "instruction": "Controleer de doeltemperatuur."}
    with pytest.raises(DelegatedWorkNotFound):
        other.service.execute("revise_digital", other.turn, request_key="wrong-device", revision=revision)
    assert work_corrections(database, first.spec) == []
    assert original.service._load(first_id, first.spec.origin).stop_requested_at is None
    result = original.service.execute("revise_digital", original.turn, request_key="revision", revision=revision)
    assert result["status"] == "correction_saved", result
    x.policies[0] = x.policies[0].model_copy(update={"state": "revoked"})
    with pytest.raises(ValueError):
        HouseholdScheduledWork(x.server).run_once()



def test_invalid_household_origin_blocks_only_its_queued_correction(database):
    import sqlalchemy as sa
    from eva.adapters.postgres.current_catalog import delegated_work_runs
    from eva.bootstrap.household_work_corrections import HouseholdScheduledWork
    from tests.postgres.test_native_task_continuity import native

    x = digital_setup(database, "hh-correct-invalid-origin")
    invalid = native(x, "invalid-original")
    first_id = invalid.service.execute("digital", invalid.turn, request_key="start-invalid",
        task="Controleer de temperatuur")["work"]["work_id"]
    first_revision = invalid.service.execute("revise_digital", invalid.turn, request_key="revise-invalid",
        revision={"work_id": first_id, "instruction": "Controleer ook de doeltemperatuur."})
    assert first_revision["status"] == "correction_saved"
    with database.begin() as connection:
        connection.execute(sa.update(delegated_work_runs).where(
            delegated_work_runs.c.tenant_id == x.server.store.scope.tenant_id,
            delegated_work_runs.c.installation_id == x.server.store.scope.installation_id,
            delegated_work_runs.c.work_id == first_id,
        ).values(spec_sha256=bytes.fromhex("ff" * 32)))
    x.clock[0] += timedelta(seconds=1)

    valid = native(x, "valid-original")
    second_id = valid.service.execute("digital", valid.turn, request_key="start-valid",
        task="Controleer de verlichting")["work"]["work_id"]
    second_revision = valid.service.execute("revise_digital", valid.turn, request_key="revise-valid",
        revision={"work_id": second_id, "instruction": "Controleer alleen de woonkamer."})
    assert second_revision["status"] == "correction_saved"

    scheduler = HouseholdScheduledWork(x.server)
    assert scheduler.run_once() == "blocked"
    assert scheduler.run_once() == "dispatched"
    assert scheduler.run_once() == "idle"
    rows = {row["schedule_id"]: row for row in scheduler.list()["schedules"]}
    assert rows[first_revision["schedule"]["schedule_id"]]["state"] == "blocked"
    second = rows[second_revision["schedule"]["schedule_id"]]
    assert second["state"] == "dispatched"
    resumed = valid.service._load(second["work_id"], valid.service.origin(valid.turn))
    assert resumed.state == "admitted" and resumed.call_id is None
    assert resumed.spec.parent_task_id == second_id
    valid.service.current("close")
    stopped = scheduler.cancel_chain(second["schedule_id"])
    assert stopped["status"] == "chain_stop_recorded" and stopped["pending_task_stops"] == 0
    saved = x.server.store.load(resumed.spec.work_id, **valid.service._owner,
        household_origin=resumed.spec.origin,
        household_continuation_execution_id=resumed.spec.execution_id)
    assert saved.state == "stopped" and saved.call_id is None
    assert scheduler.run_once() == "idle"
    with x.server.store._engine.connect() as connection:
        x.server.store._scope(connection)
        count = connection.scalar(sa.select(sa.func.count()).select_from(delegated_work_runs).where(
            delegated_work_runs.c.tenant_id == x.server.store.scope.tenant_id,
            delegated_work_runs.c.installation_id == x.server.store.scope.installation_id,
            delegated_work_runs.c.parent_task_id == first_id))
    assert count == 0


@pytest.mark.parametrize("correct_input", [False, True])
def test_legacy_household_budget_is_readable_but_requires_new_release_to_run(database, correct_input):
    x = digital_setup(database, "hh-digital-budget-" + str(correct_input))
    legacy_profile = replace(x.profile, maximum_output_tokens=1200)
    x.server.authority.digital_profile = legacy_profile
    x.service.digital_profile = legacy_profile
    turn = x.service.turn(str(uuid4()), "Controleer de verwarming")
    receipt = HouseholdWorkActions(x.server.store._engine, x.service).execute(
        turn, ConversationActionCall("start-old", "start_digital_work", {"task": turn.text}))
    work_id = receipt.output["operation_result"]["work"]["work_id"]
    old = x.server.store.load(work_id, **x.service._owner, household_origin=x.service.origin(turn)).spec
    current = replace(legacy_profile, maximum_output_tokens=9600, maximum_response_output_tokens=1200,
        **({"maximum_response_input_tokens": legacy_profile.maximum_input_tokens,
            "maximum_input_tokens": legacy_profile.maximum_input_tokens * 8} if correct_input else {}))
    authority = x.server.authority
    authority.digital_profile = current
    x.service.digital_profile = current
    assert x.server.store.load(work_id, **x.service._owner, household_origin=old.origin).spec == old
    authority.validate_read_spec(old)
    with pytest.raises(ValueError):
        authority.validate_spec(old)
    with pytest.raises(ValueError):
        authority.validate_read_spec(old.model_copy(update={"grant": current.grant()}))
    x.clock[0] += timedelta(seconds=1)
    control = x.service.turn(str(uuid4()), "Stop deze taak")
    x.service.execute("stop", control, request_key="stop-old-budget", work_id=work_id)
    x.clock[0] += timedelta(seconds=1)
    followup = x.service.turn(str(uuid4()), "Ga verder")
    result = x.service.execute("resume", followup, request_key="resume-old-budget", work_id=work_id)
    resumed = x.server.store.load(result["work"]["work_id"], **x.service._owner,
        household_origin=old.origin).spec
    authority.validate_spec(resumed)
    assert resumed.grant == old.grant
    assert resumed.execution_id == old.execution_id and resumed.source == old.source
    claim = x.server.store.claim_next(worker_id="worker:budget-resume", release=current.release, now=x.clock[0])
    assert claim is not None
