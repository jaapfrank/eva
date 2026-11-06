"""Current board must retain scope, evidence and one authoritative task."""
import json
from pathlib import Path
import pytest
from tools.check_current_workboard import validate
ROOT = Path(__file__).resolve().parents[3]

def board():
    return json.loads((ROOT / "development/design/product/execution-workboard-v1.json").read_text())

def test_current_scope_retained():
    result = validate(ROOT, board())
    assert result["taskCount"] == 103
    tasks = [task for step in board()["audit"]["deliveryPlan"]["implementationSlices"]
             for task in step["remainingTasks"]]
    assert len([task for task in tasks if task.get("scopeOrigin") == "finalization-20261015"]) == 18
    assert len([task for task in tasks if not task.get("scopeOrigin")]) == 52
    assert len([task for task in tasks if task.get("scopeOrigin") and task.get("scopeOrigin") != "finalization-20261015"]) == 33
    assert result["completed"] >= 45

def test_missing_device_check_refused():
    value = board()
    merged = next(task for step in value["audit"]["deliveryPlan"]["implementationSlices"]
                 for task in step["remainingTasks"] if task.get("deviceChecks"))
    merged["deviceChecks"].pop()
    with pytest.raises(ValueError, match="consolidated device scope changed"):
        validate(ROOT, value)

def test_unfinished_device_cannot_complete_function():
    value = board()
    merged = next(task for step in value["audit"]["deliveryPlan"]["implementationSlices"]
                 for task in step["remainingTasks"] if task.get("deviceChecks"))
    merged["status"] = "completed"
    merged["deviceChecks"][0]["status"] = "planned"
    with pytest.raises(ValueError, match="unfinished device checks"):
        validate(ROOT, value)

def test_changed_original_condition_refused():
    value = board()
    next(step["remainingTasks"] for step in value["audit"]["deliveryPlan"]["implementationSlices"] if step["remainingTasks"])[0]["doneWhen"] = "easier"
    with pytest.raises(ValueError, match="original end condition changed"):
        validate(ROOT, value)

def test_missing_original_task_refused():
    value = board()
    next(step["remainingTasks"] for step in value["audit"]["deliveryPlan"]["implementationSlices"] if step["remainingTasks"]).pop()
    with pytest.raises(ValueError, match="original task scope changed"):
        validate(ROOT, value)

def test_contradictory_current_task_refused():
    value = board()
    value["activeTaskId"] = "another-task"
    with pytest.raises(ValueError, match="authoritative current task"):
        validate(ROOT, value)


def test_new_finalization_task_cannot_disappear():
    value = board()
    next(step for step in value["audit"]["deliveryPlan"]["implementationSlices"] if step["id"] == "FE1")["remainingTasks"].pop()
    with pytest.raises(ValueError, match="original task scope changed"):
        validate(ROOT, value)


def test_added_end_condition_is_also_preserved():
    value = board()
    next(step for step in value["audit"]["deliveryPlan"]["implementationSlices"] if step["id"] == "FE1")["remainingTasks"][0]["doneWhen"] = "easier"
    with pytest.raises(ValueError, match="original end condition changed"):
        validate(ROOT, value)


def test_added_task_needs_completion_and_independent_evidence():
    value = board()
    task = next(task for step in value["audit"]["deliveryPlan"]["implementationSlices"]
                for task in step.get("remainingTasks", [])
                if task.get("scopeOrigin") and task["status"] != "completed"
                and not task.get("deviceChecks"))
    task.pop("completionEvidence", None)
    task.pop("independentReview", None)
    task["status"] = "completed"
    with pytest.raises(ValueError, match="completion requires completionEvidence"):
        validate(ROOT, value)


def test_withdrawn_task_is_retained_without_completion_or_open_action():
    from tools.check_current_workboard import render_human, small_tasks
    value = board()
    result = validate(ROOT, value)
    tasks = small_tasks(value)
    withdrawn = next(t for t in tasks if t["id"] == "E2.6")
    assert withdrawn["status"] == "cancelled"
    assert result["taskCount"] == 103
    assert result["cancelled"] == 1
    assert result["completed"] == sum(
        t["status"] == "completed" for t in tasks if t["id"] != "E2.6"
    )
    open_count = sum(t["status"] not in {"completed", "cancelled"} for t in tasks)
    assert open_count + result["completed"] + result["cancelled"] == result["taskCount"]
    text = render_human(value)
    open_section = text.split("## Open taken")[1].split("## Door eigenaar ingetrokken")[0]
    assert "E2.6" not in open_section
    assert "E2.6" in text.split("## Door eigenaar ingetrokken")[1]


@pytest.mark.parametrize("tamper", ["authority", "action", "worker", "evidence"])
def test_withdrawal_requires_owner_evidence_and_no_pending_work(tamper):
    from tools.check_current_workboard import small_tasks
    value = board()
    task = next(t for t in small_tasks(value) if t["id"] == "E2.6")
    if tamper == "authority": task["ownerDisposition"]["ownerAuthorized"] = False
    if tamper == "action": task["ownerActionRequired"] = True
    if tamper == "worker": task["assignment"]["activeWorkers"] = ["worker"]
    if tamper == "evidence": task["ownerDisposition"]["evidence"] = "missing-owner-instruction.json"
    with pytest.raises(ValueError):
        validate(ROOT, value)


def test_withdrawn_task_cannot_use_historical_transport_proof_to_claim_completion():
    from tools.check_current_workboard import small_tasks
    value = board()
    task = next(t for t in small_tasks(value) if t["id"] == "E2.6")
    task["status"] = "completed"
    with pytest.raises(ValueError, match="withdrawn task must remain cancelled"):
        validate(ROOT, value)
