from copy import deepcopy
import json

import pytest

from eva.adapters.model.programming_history import archive_recovered_programming
from eva.application.conversation import ConversationActionCall, ConversationActionReceipt, ConversationConflict
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256


def receipt(call_id, name, arguments, output):
    return ConversationActionReceipt(ConversationActionCall(call_id, name, arguments), output)


def progress(revision=1, ids=("build", "activate")):
    arguments = {"steps": [{"id": i, "state": "pending"} for i in ids]}
    return receipt("progress-" + str(revision), "save_work_progress", arguments,
                   {"status": "verified", "contract": "EvaWorkProgressSaved/v1",
                    "revision": revision, "task_completion_verified": False, "progress": arguments})


def programming(call_id="write", **changes):
    return receipt(call_id, "write_programming_file", {"path": "/workspace/tool.py", "content": "x" * 30000},
                   {"status": "verified", "workspace": "/workspace", "installed": False,
                    "semantic_success_verified": False,
                    "programming_result": {"isError": False, "content": [{"type": "text", "text": "File written"}]},
                    **changes})


def archive_capture(captured):
    def archive(turn, index):
        captured.append((turn, deepcopy(index)))
        sha = canonical_sha256(index)
        return {"contract": "EvaStoredConnectorResult/v1", "stored_complete": True,
                "external_state_verified": False, "sha256": sha,
                "byte_length": len(canonical_json_bytes(index)),
                "reader": {"action": "use_connector_tool", "tool_id": "eva-stored-result:" + "a"*64 + ":" + sha}}
    return archive


def test_resumed_write_remains_exactly_readable_without_repeating_effect():
    old, plan, newest = programming(), progress(), programming("latest")
    external = receipt("external", "use_connector_tool", {}, {"status": "unknown", "proof": {"uncertain": True}})
    original = deepcopy((old, plan, external, newest))
    captured = []
    selected, context = archive_recovered_programming("same-source", (old, plan, external), newest,
        recovered_call_ids={"write", "latest"}, archive=archive_capture(captured))
    assert selected == (plan, external)
    assert original == (old, plan, external, newest)
    assert captured[0][0] == "same-source"
    stored = captured[0][1]["historical_programming"][0]
    assert stored == {"call": {"call_id": old.call.call_id, "name": old.call.name,
                             "arguments": old.call.arguments}, "output": old.output}
    assert "latest" not in [row["call"]["call_id"] for row in captured[0][1]["historical_programming"]]
    assert len(context[0]) < len(canonical_json_bytes(stored)) / 5
    navigation = json.loads(context[0].split("\n", 1)[1])
    assert navigation["historical_programming"][0]["path"] == "/workspace/tool.py"
    assert navigation["archive"]["reader"]["action"] == "use_connector_tool"
    assert navigation["historical_programming"][0]["semantic_success_verified"] is False


@pytest.mark.parametrize("case", ["no_archive", "no_progress", "new_call", "error", "unknown", "stale_progress"])
def test_unsafe_or_unavailable_projection_keeps_original_receipts(case):
    old, plan, newest = programming(), progress(), programming("latest")
    captured = []
    archive = archive_capture(captured)
    recovered = {"write"}
    previous = (old, plan)
    if case == "no_archive": archive = None
    if case == "no_progress": previous = (old,)
    if case == "new_call": recovered = set()
    if case == "error":
        old = programming(programming_result={"isError": True, "content": [{"text": "failed"}]})
        previous = (old, plan)
    if case == "unknown":
        old = programming(status="unknown"); previous = (old, plan)
    if case == "stale_progress": previous = (old, progress(2), plan)
    selected, context = archive_recovered_programming("source", previous, newest,
        recovered_call_ids=recovered, archive=archive)
    assert selected == previous and context == () and captured == []


def test_archive_must_acknowledge_exact_bytes_and_authorized_reader():
    def invalid(turn, index):
        value = archive_capture([])(turn, index)
        value["sha256"] = "0" * 64
        return value
    with pytest.raises(ConversationConflict, match="archive binding"):
        archive_recovered_programming("source", (programming(), progress()), programming("latest"),
            recovered_call_ids={"write"}, archive=invalid)


def test_projection_decorator_preserves_identity_catalog_and_current_receipt():
    from eva.adapters.model.programming_history import ProgrammingHistoryModel
    from eva.bootstrap.delegated_digital_runner import _TaskModel
    class Model:
        supports_context_archive = True
        journals_provider_requests = True
        def generate(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs
            return "same-result"
    model = Model()
    captured = []
    wrapper = ProgrammingHistoryModel(model, archive_capture(captured), lambda: {"write"})
    task = _TaskModel(wrapper, "original task", attempt_work_id="same-work")
    current = programming("latest")
    identity, memory, recent = object(), object(), ()
    actions = ("write_programming_file", "use_connector_tool")
    assert task.generate("same-source", identity, memory, recent,
        available_actions=actions, prior_actions=(programming(), progress()),
        completed_action=current, self_context=("existing authority",)) == "same-result"
    assert model.args == ("same-source", identity, memory, recent)
    assert model.kwargs["available_actions"] is actions
    assert model.kwargs["completed_action"] is current
    assert model.kwargs["prior_actions"] == (progress(),)
    assert model.kwargs["self_context"][0] == "existing authority"
    assert any("original task" in item for item in model.kwargs["self_context"])
    assert task.journals_provider_requests is True
