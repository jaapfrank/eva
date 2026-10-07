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
