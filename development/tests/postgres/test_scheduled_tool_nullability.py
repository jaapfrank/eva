"""Strict tool input reaches the existing scheduler without null dependencies."""
from copy import deepcopy
from datetime import timedelta
import hashlib
from pathlib import Path
import pytest
from jsonschema import validate
from pydantic_ai.profiles.openai import OpenAIJsonSchemaTransformer
from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
from eva.adapters.postgres.scheduled_work import ScheduledOwnerWork
from eva.application.conversation import ConversationActionCall, ConversationConflict
from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile
from tests.postgres.test_scheduled_failure_return import database
from tests.postgres.test_private_work_return import setup


def configured(database, label):
    service, turn, clock = setup(database, label, False)
    root = Path(__file__).resolve().parents[3] / "src/eva"
    source = "bootstrap/delegated_digital_runner.py"
    service.digital_profile = DelegatedDigitalProfile("model:controlled", "a" * 64, "b" * 64,
        ("discover_connector_tools", "use_connector_tool"),
        ((source, hashlib.sha256((root / source).read_bytes()).hexdigest()),))
    return service, turn, clock


def strict_arguments(schedule):
    descriptor = next(t for t in PostgresDelegatedWorkActions.conversation_tools(include_digital=True)
                      if t.name == "schedule_digital_work")
    schema = OpenAIJsonSchemaTransformer(deepcopy(descriptor.input_schema), strict=True).walk()
    assert set(schema["properties"]["schedule"]["required"]) == {
        "task", "run_at", "after_work_id", "after_schedule_id"}
    arguments = {"schedule": schedule}
    validate(arguments, schema)
    return arguments


@pytest.mark.parametrize("dependent", [False, True])
def test_strict_schedule_nulls_reach_scheduler_and_replay_original_call(database, dependent):
    service, turn, clock = configured(database, "schedule-null-" + str(dependent))
    due = (clock[0] + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    predecessor = None
    if dependent:
        predecessor = ScheduledOwnerWork(service).schedule(turn, request_key="parent",
            request={"task": "Write a report", "run_at": due})["schedule_id"]
    request = {"task": "Summarize report", "run_at": None if dependent else due,
               "after_work_id": None, "after_schedule_id": predecessor}
    call = ConversationActionCall("schedule", "schedule_digital_work", strict_arguments(request))
    original = deepcopy(call.arguments)
    actions = PostgresDelegatedWorkActions(database, service)
    receipt = actions.execute(turn, call)
    assert actions.pending(turn) == receipt
    assert actions.execute(turn, call) == receipt
    assert call.arguments == original and receipt.call.arguments == original
    rows = ScheduledOwnerWork(service).list()["schedules"]
    assert len(rows) == (2 if dependent else 1)
    saved = next(r for r in rows if r["schedule_id"] == receipt.output["operation_result"]["schedule_id"])
    assert saved["state"] == "scheduled"
    changed = deepcopy(call.arguments); changed["schedule"].pop("after_work_id")
    with pytest.raises(ConversationConflict, match="changed after admission"):
        actions.execute(turn, ConversationActionCall(call.call_id, call.name, changed))


@pytest.mark.parametrize("both", [False, True])
def test_real_scheduler_still_rejects_missing_time_or_two_dependencies(database, both):
    service, turn, clock = configured(database, "schedule-invalid-" + str(both))
    request = {"task": "Write report", "run_at": None,
               "after_work_id": "work:previous" if both else None,
               "after_schedule_id": "schedule:previous" if both else None}
    call = ConversationActionCall("invalid", "schedule_digital_work", strict_arguments(request))
    with pytest.raises(ValueError, match="at most one predecessor" if both else "requires its UTC time"):
        PostgresDelegatedWorkActions(database, service).execute(turn, call)
    assert ScheduledOwnerWork(service).list()["schedules"] == []
