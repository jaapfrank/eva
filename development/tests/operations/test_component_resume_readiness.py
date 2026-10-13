"""Retained component work waits through failure and finishes on its typed resume."""
from copy import deepcopy

import pytest

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.component_application_runtime import _execution_completion_state

SOURCE = {"tenant_id": "tenant", "installation_id": "installation", "principal_id": "owner"}
SCOPE = canonical_sha256(SOURCE)


def row(work_id, state, *, parent="chat:original", generation=1, stopped=None):
    return {"work_id": work_id, "state": state, "stop_requested_at": stopped,
            "spec_payload": {"source": SOURCE, "execution_id": "work:original",
                "source_ingress_sha256": "same-ingress", "grant": {"capability_ids": ["save"]},
                "owner_principal_id": "owner", "principal_binding_id": "binding",
                "audience": {"kind": "private"}, "episode_id": "episode",
                "generation": generation, "parent_task_id": parent}}


def state(rows):
    return _execution_completion_state(rows, "work:original", SCOPE, canonical_sha256)


@pytest.mark.parametrize("failed", ["ambiguous", "failed", "needs_help"])
def test_failure_waits_and_exact_authenticated_resume_can_finish(failed):
    original = row("work:original", failed)
    assert state([original]) == "waiting"
    resumed = row("work:resume", "call_started", parent="work:original", generation=2)
    assert state([original, resumed]) == "waiting"
    resumed["state"] = "succeeded"
    assert state([original, resumed]) == "ready"
    assert original["state"] == failed


@pytest.mark.parametrize("field", ["execution_id", "source", "source_ingress_sha256",
    "grant", "owner_principal_id", "principal_binding_id", "audience", "episode_id", "generation"])
def test_unrelated_or_changed_authority_child_never_finishes_original(field):
    original = row("work:original", "ambiguous")
    resumed = row("work:resume", "succeeded", parent="work:original", generation=2)
    resumed["spec_payload"][field] = "changed"
    assert state([original, resumed]) == "waiting"


def test_explicit_stop_wins_on_original_or_resume():
    original = row("work:original", "ambiguous")
    resumed = row("work:resume", "succeeded", parent="work:original", generation=2)
    for index in (0, 1):
        stopped = deepcopy([original, resumed])
        stopped[index]["stop_requested_at"] = "stop"
        assert state(stopped) == "cancelled"
    original["state"] = "stopped"
    assert state([original, resumed]) == "cancelled"


def test_two_successors_or_wrong_scope_do_not_authorize_application():
    original = row("work:original", "ambiguous")
    children = [row(name, "succeeded", parent="work:original", generation=2)
                for name in ("work:resume", "work:other")]
    with pytest.raises(ValueError, match="ambiguous"):
        state([original, *children])
    with pytest.raises(ValueError, match="source differs"):
        _execution_completion_state([original], "work:original", "foreign", canonical_sha256)


def test_actual_core_readiness_program_is_executable_python():
    from eva.operations.component_application_runtime import _READINESS
    compile(_READINESS, "<core-readiness>", "exec")


def test_reviewed_binding_keeps_stop_authority_without_child_idle_deadlock():
    import json
    from types import SimpleNamespace
    from eva.operations.component_application_runtime import ComponentApplicationRuntime
    observed=[]
    value={"state":"ready","contract":"EvaReviewedComponentWorkAuthority/v1",
        "task_id":"work:original","scope_sha256":SCOPE,
        "spec_sha256":"a"*64,"source_ingress_sha256":"b"*64}
    def run(args,**kwargs):
        observed.append(json.loads(args[-1]))
        return SimpleNamespace(returncode=0,stdout=json.dumps(value))
    runtime=ComponentApplicationRuntime({"core_container":"core","scope_sha256":SCOPE},run=run)
    assert runtime.reviewed_work_binding("work:original")==value
    assert observed==[{"task_id":"work:original","scope_sha256":SCOPE,
                      "require_idle":False,"include_binding":True}]
    value["state"]="cancelled"
    assert runtime.reviewed_work_binding("work:original")["state"]=="cancelled"


def test_original_usage_unknown_contact_is_not_free_construction():
    import json
    from types import SimpleNamespace
    from eva.operations.component_application_runtime import ComponentApplicationRuntime
    terminal={"contract":"DelegatedWorkUsage/v1","schema_version":1,"measurement":"partial",
        "completed_model_responses":2,"tool_calls":2,"input_tokens":20,"output_tokens":2,
        "total_tokens":22,"cost_usd_micros":None}
    value={"state":"ready","contract":"EvaReviewedComponentWorkUsage/v1",
        "task_id":"work:original","scope_sha256":SCOPE,
        "model_usage":{"measurement":"measured","model_attempts":2,"tool_calls":0,
                       "input_tokens":20,"output_tokens":2},
        "terminal_usage":terminal,"terminal_usage_sha256":canonical_sha256(terminal),
        "contact_disposition":"recorded_original_ingress",
        "contact_usage":{"measurement":"partial","model_attempts":1,"tool_calls":0,
                         "input_tokens":1,"output_tokens":1}}
    runtime=ComponentApplicationRuntime({"core_container":"core","scope_sha256":SCOPE},
        run=lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps(value)))
    with pytest.raises(ValueError,match="contact accounting incomplete"):
        runtime.reviewed_work_usage("work:original")
    value["contact_usage"]["measurement"]="measured"
    assert runtime.reviewed_work_usage("work:original")==value
    value["contact_usage"]=None
    with pytest.raises(ValueError,match="contact accounting absent"):
        runtime.reviewed_work_usage("work:original")
    value["contact_disposition"]="native_origin_without_foreground"
    assert runtime.reviewed_work_usage("work:original")==value


def test_requested_resume_includes_ancestors_and_preserves_new_profile_deadline():
    from eva.operations.component_application_runtime import _execution_chain
    original = row("work:original", "ambiguous")
    child = row("work:resume", "succeeded", parent="work:original", generation=2)
    original["spec_payload"].update(task="same task", deadline="2026-10-12T01:00:00Z",
                                    model_profile_release_sha256="old")
    child["spec_payload"].update(task="same task", deadline="2026-10-12T02:00:00Z",
                                 model_profile_release_sha256="new")
    before = deepcopy([original, child])
    assert _execution_chain([child, original], "work:resume", SCOPE,
                            canonical_sha256, strict=True) == [original, child]
    assert _execution_completion_state([child, original], "work:resume", SCOPE,
                                       canonical_sha256) == "ready"
    assert [original, child] == before
    with pytest.raises(ValueError, match="root is missing"):
        _execution_chain([child], "work:resume", SCOPE, canonical_sha256, strict=True)
    original["stop_requested_at"] = "stop"
    assert _execution_completion_state([original, child], "work:resume", SCOPE,
                                       canonical_sha256) == "cancelled"


@pytest.mark.parametrize("field", ["task", "source", "source_ingress_sha256", "grant",
                                  "owner_principal_id", "principal_binding_id",
                                  "episode_id", "execution_id", "audience"])
def test_requested_leaf_cannot_adopt_changed_ancestor_authority(field):
    from eva.operations.component_application_runtime import _execution_chain
    original = row("work:original", "ambiguous")
    child = row("work:resume", "succeeded", parent="work:original", generation=2)
    original["spec_payload"][field] = "changed"
    with pytest.raises(ValueError, match="authority differs"):
        _execution_chain([original, child], "work:resume", SCOPE, canonical_sha256, strict=True)


def test_strict_execution_chain_refuses_duplicate_or_unaccounted_branch():
    from eva.operations.component_application_runtime import _execution_chain
    original = row("work:original", "succeeded")
    with pytest.raises(ValueError, match="duplicate"):
        _execution_chain([original, original], "work:original", SCOPE,
                         canonical_sha256, strict=True)
    alien = row("work:alien", "succeeded", parent="absent", generation=2)
    with pytest.raises(ValueError, match="one validated chain"):
        _execution_chain([original, alien], "work:original", SCOPE,
                         canonical_sha256, strict=True)
