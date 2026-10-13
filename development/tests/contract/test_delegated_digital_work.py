"""Version four adds private connector work without expanding older grants."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile
from eva.contracts.delegated_digital_work import DelegatedDigitalWorkGrant, DelegatedDigitalWorkResult, PrivateWorkSource
from eva.contracts.delegated_work import DelegatedWorkResult, load_delegated_work_result, load_delegated_work_spec


def profile():
    root = Path(__file__).resolve().parents[3] / "src/eva"
    name = "application/background_execution.py"
    return DelegatedDigitalProfile("model:host-selected", "a" * 64, "b" * 64,
        ("discover_connector_tools", "use_connector_tool"),
        ((name, hashlib.sha256((root / name).read_bytes()).hexdigest()),))


def source():
    return dict(tenant_id="tenant", installation_id="installation", principal_id="speaker",
                conversation_id="telegram:123", message_id="telegram:123:456",
                audience={"kind": "private_person", "principal_id": "speaker"})


def test_profile_build_roundtrip_preserves_original_source_and_partial_cost():
    host = profile()
    now = datetime(2026, 9, 23, 12, tzinfo=UTC)
    spec = host.build_spec(source=source(), source_ingress_sha256="c" * 64,
        work_id="work:first", episode_id="episode:first", generation=1,
        parent_task_id="parent:first", owner_principal_id="owner",
        principal_binding_id="binding:owner", task="Compare my documents",
        created_at=now, deadline=now + timedelta(seconds=300))
    assert load_delegated_work_spec(spec.to_payload()) == spec
    assert spec.source.model_dump() == source()
    assert spec.grant.maximum_model_runs == 8
    assert spec.grant.maximum_tool_calls == 6
    assert spec.grant.maximum_cost_usd_micros is None
    assert spec.model_profile_id == "model:host-selected"
    assert spec.worker_program_sha256 == host.release.worker_program_sha256


@pytest.mark.parametrize("change", [
    {"capability_ids": ["start_delegated_work"]}, {"capability_ids": []},
    {"capability_ids": ["use_connector_tool", "use_connector_tool"]},
    {"context_refs": ["memory:other"]}, {"workspace_ref": "workspace:ambient"},
    {"child_network_access": True}, {"maximum_model_runs": 0},
    {"maximum_completed_model_responses": 9}, {"maximum_tool_calls": 33},
    {"delegation_depth": 2},
])
def test_general_work_stays_bounded_and_has_no_recursive_or_ambient_authority(change):
    raw = profile().grant().to_payload()
    raw.update(change)
    with pytest.raises(ValidationError):
        DelegatedDigitalWorkGrant.model_validate(raw)


@pytest.mark.parametrize("change", [
    {"audience": {"kind": "household", "principal_id": "speaker"}},
    {"audience": {"kind": "private_person", "principal_id": "other"}},
    {"conversation_id": "background-accounting:fake"},
    {"conversation_id": "initiative-task:fake"}, {"extra": "unexpected"},
])
def test_source_is_an_exact_private_reference(change):
    raw = source()
    raw.update(change)
    with pytest.raises(ValidationError):
        PrivateWorkSource.model_validate(raw)


def test_release_binds_model_budgets_and_actual_execution_sources():
    host = profile()
    assert replace(host, configuration_sha256="d" * 64).release != host.release
    assert replace(host, maximum_tool_calls=5).release != host.release
    changed = replace(host, execution_source_sha256=((host.execution_source_sha256[0][0], "0" * 64),))
    with pytest.raises(ValueError, match="source differs"):
        _ = changed.release


def result_payload():
    output = "Compared both documents and saved the differences."
    return dict(contract="DelegatedDigitalWorkResult/v4", schema_version=4,
        work_id="work:first", call_id="call:first", spec_sha256="c" * 64,
        output=output, output_sha256=hashlib.sha256(output.encode()).hexdigest(),
        completed_at="2026-09-23T12:00:00Z", usage=dict(
            contract="DelegatedWorkUsage/v1", schema_version=1, measurement="partial",
            completed_model_responses=3, tool_calls=2, input_tokens=100,
            output_tokens=20, total_tokens=120, cost_usd_micros=None))


def test_digital_result_roundtrip_permits_unknown_cost_but_not_unknown_work():
    raw = result_payload()
    result = DelegatedDigitalWorkResult.model_validate(raw)
    assert load_delegated_work_result(result.to_payload()) == result
    raw["usage"].update(measurement="measured", cost_usd_micros=5)
    assert load_delegated_work_result(raw).usage.cost_usd_micros == 5


@pytest.mark.parametrize("change", [
    {"completed_model_responses": None}, {"completed_model_responses": 0},
    {"tool_calls": None},
    {"input_tokens": None, "output_tokens": None, "total_tokens": None},
    {"measurement": "partial", "cost_usd_micros": 3},
])
def test_digital_success_rejects_missing_measurements_except_price(change):
    raw = result_payload()
    raw["usage"].update(change)
    with pytest.raises(ValidationError):
        load_delegated_work_result(raw)


def test_digital_result_still_binds_output_and_v1_rules_stay_strict():
    raw = result_payload()
    raw["output"] += " altered"
    with pytest.raises(ValidationError, match="hash differs"):
        load_delegated_work_result(raw)
    raw = result_payload()
    raw.update(contract="DelegatedWorkResult/v1", schema_version=1)
    with pytest.raises(ValidationError, match="must be measured"):
        DelegatedWorkResult.model_validate(raw)
    raw["usage"].update(measurement="measured", cost_usd_micros=2)
    with pytest.raises(ValidationError, match="one model response"):
        load_delegated_work_result(raw)


def test_resumed_result_can_reuse_verified_receipts_without_new_tool_dispatch():
    raw = result_payload()
    raw["usage"]["tool_calls"] = 0
    assert load_delegated_work_result(raw).usage.tool_calls == 0


def household_spec(**changes):
    from eva.contracts.delegated_work import HouseholdWorkOrigin
    actor = "household-device:" + "a" * 32
    conversation = "household:home:conversation:voice"
    origin = HouseholdWorkOrigin(
        contract="HouseholdWorkOrigin/v1", schema_version=1,
        tenant_id="tenant", installation_id="installation", household_id="home",
        actor_id=actor, satellite_id="assist_satellite.lounge", device_id="a" * 32,
        consent_operation_id="household-consent:owner",
        household_policy_id="policy:voice", household_policy_sha256="d" * 64,
        research_policy_id="policy:work", research_policy_sha256="e" * 64,
        session_id="00000000-0000-0000-0000-000000000001",
        conversation_id=conversation, message_id=conversation + ":request:one",
        received_at="2026-09-23T12:00:00Z",
    )
    host = replace(profile(), capability_ids=(
        "home_assistant_request", "read_home_assistant_inventory",
        "save_work_progress", "use_connector_tool", "get_delegated_work"))
    now = datetime(2026, 9, 23, 12, tzinfo=UTC)
    arguments = dict(
        source=dict(tenant_id=origin.tenant_id, installation_id=origin.installation_id,
            principal_id=actor, conversation_id=conversation, message_id=origin.message_id,
            audience=dict(kind="household", principal_id=actor, household_id="home",
                          consent_operation_id=origin.consent_operation_id)),
        source_ingress_sha256="c" * 64, origin=origin,
        work_id="work:household:first", generation=1, parent_task_id="parent:household",
        owner_principal_id="owner", principal_binding_id="binding:owner",
        task="Inspect and update the heating automation",
        created_at=now, deadline=now + timedelta(seconds=300),
    )
    arguments.update(changes)
    return host, host.build_spec(**arguments)


def test_household_digital_work_keeps_shared_source_and_existing_worker_release():
    from eva.adapters.postgres.delegated_work import DelegatedWorkReleaseSelector
    from eva.contracts.delegated_digital_work import (
        DelegatedDigitalWorkSpec, DelegatedHouseholdDigitalWorkSpec,
    )
    host, spec = household_spec()
    assert isinstance(spec, DelegatedHouseholdDigitalWorkSpec)
    assert isinstance(spec, DelegatedDigitalWorkSpec)
    assert load_delegated_work_spec(spec.to_payload()) == spec
    assert spec.audience == "household"
    assert spec.source.principal_id == spec.origin.actor_id != spec.owner_principal_id
    assert spec.source.audience.kind == "household"
    assert spec.source.audience.household_id == spec.origin.household_id
    assert spec.grant.capability_ids == [
        "home_assistant_request", "read_home_assistant_inventory", "save_work_progress"]
    assert host.accepts_grant(spec.grant)
    assert DelegatedWorkReleaseSelector.from_spec(spec) == host.release


@pytest.mark.parametrize("capability", [
    "get_delegated_work", "use_connector_tool", "save_workspace_document",
    "read_own_component_source",
])
def test_household_digital_work_rejects_private_worker_capabilities(capability):
    _, spec = household_spec()
    raw = spec.to_payload()
    raw["grant"]["capability_ids"].append(capability)
    with pytest.raises(ValidationError, match="capability grant"):
        load_delegated_work_spec(raw)


@pytest.mark.parametrize("section,field,value", [
    ("source", "tenant_id", "other-tenant"),
    ("source", "installation_id", "other-installation"),
    ("source", "message_id", "household:home:conversation:voice:request:other"),
    ("audience", "consent_operation_id", "household-consent:other"),
    ("audience", "household_id", "neighbours"),
    ("source", "principal_id", "owner"),
    ("audience", "kind", "private_person"),
    ("spec", "destination_sha256", "0" * 64),
    ("spec", "episode_id", "episode:other"),
    ("spec", "deadline", "2026-09-23T12:05:01Z"),
    ("spec", "created_at", "2026-09-23T12:00:01Z"),
])
def test_household_digital_work_rejects_changed_source_scope_and_destination(section, field, value):
    _, spec = household_spec()
    raw = spec.to_payload()
    target = raw if section == "spec" else raw["source"]
    if section == "audience":
        target = target["audience"]
    target[field] = value
    with pytest.raises(ValidationError):
        load_delegated_work_spec(raw)


def test_private_digital_contract_cannot_be_relabelled_as_household():
    _, spec = household_spec()
    raw = spec.to_payload()
    raw.update(contract="DelegatedDigitalWork/v4", schema_version=4, audience="private")
    raw.pop("origin")
    raw.pop("destination_sha256")
    with pytest.raises(ValidationError):
        load_delegated_work_spec(raw)


def test_household_digital_resume_retains_source_and_changes_destination_binding():
    _, previous = household_spec()
    now = datetime(2026, 9, 23, 12, 1, tzinfo=UTC)
    _, resumed = household_spec(
        work_id="work:household:second", generation=2, parent_task_id=previous.work_id,
        execution_id=previous.execution_id, grant=previous.grant,
        created_at=now, deadline=now + timedelta(seconds=300),
    )
    assert resumed.source == previous.source
    assert resumed.origin == previous.origin
    assert resumed.episode_id == previous.episode_id
    assert resumed.execution_id == previous.execution_id
    assert resumed.destination_sha256 != previous.destination_sha256


def test_household_store_rechecks_digital_authority_and_rejects_private_reads(monkeypatch):
    from types import SimpleNamespace
    from eva.adapters.postgres.delegated_work import (
        PostgresDelegatedWorkStore, DelegatedWorkConflict, DelegatedWorkNotFound,
    )
    import eva.adapters.postgres.household_work_sessions as sessions
    _, spec = household_spec()
    store = object.__new__(PostgresDelegatedWorkStore)
    store._scope_value = SimpleNamespace(tenant_id="tenant", installation_id="installation")
    checks = []
    store._household_authorizer = lambda value: checks.append(value)
    store._household_clock = lambda: datetime(2026, 9, 23, 12, tzinfo=UTC)
    verified = []
    monkeypatch.setattr(sessions, "verify_origin", lambda *args, **kwargs: verified.append((args, kwargs)))
    connection = object()
    with pytest.raises(DelegatedWorkNotFound):
        store._read_scope(spec, connection)
    store._read_scope(spec, connection, spec.origin)
    assert checks == [spec]
    assert verified[0][0] == (connection, store.scope, spec.origin)
    assert verified[0][1]["require_open"] is True
    store._household_authorizer = None
    with pytest.raises(DelegatedWorkConflict, match="authority unavailable"):
        store._authorize_household(spec, connection)



@pytest.mark.parametrize("input_tokens, output_tokens", [(100, 20), (3_200_000, 100_000)])
def test_usage_wire_bounds_cover_existing_digital_grant_bounds(input_tokens, output_tokens):
    from eva.contracts.delegated_work import DelegatedWorkUsage
    from eva.contracts.workboard import WorkBoardUsage
    counters = dict(measurement="partial", input_tokens=input_tokens, output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens, cost_usd_micros=None)
    delegated = DelegatedWorkUsage(contract="DelegatedWorkUsage/v1", schema_version=1,
        completed_model_responses=32, tool_calls=31, **counters)
    board = WorkBoardUsage(contract="EvaWorkBoardUsage/v1", schema_version=1, **counters)
    assert DelegatedWorkUsage.model_validate(delegated.to_payload()) == delegated
    assert WorkBoardUsage.model_validate(board.model_dump(mode="json")) == board
    for value in (delegated, board):
        with pytest.raises(ValidationError):
            type(value).model_validate({**value.model_dump(mode="json"), "input_tokens": 3_200_001,
                                       "total_tokens": 3_200_001 + output_tokens})
        with pytest.raises(ValidationError):
            type(value).model_validate({**value.model_dump(mode="json"), "total_tokens": 3_300_001})


def test_reviewed_application_descriptors_roundtrip_through_digital_grant(monkeypatch):
    from types import SimpleNamespace
    from eva.adapters.postgres import component_improvement_actions as actions
    monkeypatch.setattr(actions, "configured_component_evaluator", lambda: SimpleNamespace(
        allowed_paths=("adapters/postgres/delegated_work_actions.py",),
        reviewed_releases=(("a" * 64, "b" * 64),)))
    names = tuple(item.name for item in actions.component_descriptors())
    assert actions.APPLY in names and actions.APPLICATION_STATUS in names
    grant = replace(profile(), capability_ids=names).grant()
    assert DelegatedDigitalWorkGrant.model_validate(grant.to_payload()) == grant
    changed = grant.to_payload()
    changed["capability_ids"] = [*names, "approve_own_component_release"]
    with pytest.raises(ValidationError, match="explicit connector actions"):
        DelegatedDigitalWorkGrant.model_validate(changed)


def test_future_digital_task_can_run_longer_without_extending_an_old_spec():
    old = profile()
    now = datetime(2026, 9, 23, 12, tzinfo=UTC)
    args = dict(source=source(), source_ingress_sha256="c"*64, work_id="work:deadline",
        episode_id="episode:deadline", generation=1, parent_task_id="parent:deadline",
        owner_principal_id="owner", principal_binding_id="binding:owner",
        task="Complete several dependent changes", created_at=now)
    admitted = old.build_spec(**args, deadline=now+timedelta(seconds=300))
    newer = replace(old, timeout_seconds=1200, historical_grants=(old.grant(),))
    future = newer.build_spec(**{**args,"work_id":"work:future"},
                             deadline=now+timedelta(seconds=1200))
    assert future.grant.timeout_seconds == 1200
    assert load_delegated_work_spec(admitted.to_payload()).deadline == admitted.deadline
    assert newer.accepts_grant(admitted.grant)
    invalid = future.grant.to_payload()
    invalid["timeout_seconds"] = 3601
    with pytest.raises(ValidationError):
        DelegatedDigitalWorkGrant.model_validate(invalid)
