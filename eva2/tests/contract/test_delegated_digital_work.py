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
    root = Path(__file__).resolve().parents[2] / "src/eva"
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
