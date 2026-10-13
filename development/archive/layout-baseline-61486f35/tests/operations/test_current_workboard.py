"""Current board must retain scope, evidence and one authoritative task."""
import json
from pathlib import Path
import pytest
from tools.check_current_workboard import validate
ROOT = Path(__file__).resolve().parents[2]

def board():
    return json.loads((ROOT / "design/product/execution-workboard-v1.json").read_text())

def test_current_scope_retained():
    result = validate(ROOT, board())
    assert result["taskCount"] == 54
    assert result["completed"] == 43

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
