"""Independent durable artifact identity regression; synthetic receipts only."""
import json
from tests.operations.test_runtime_repair_development import flow


def test_oracle_and_review_receipts_keep_actual_successful_continuation(flow):
    f=flow;c=f.controller
    c.request({**f.request,"action":"submit"})
    f.meta.update(state="succeeded",work_succeeded=True,usage_measured=True)
    assert c.run_once()=="original_behavior_oracle"
    original="work:eva-initiative:"+"1"*64
    oracle_child="work:eva-resume:oracle-successful-child"
    f.metadata[original].update(task_id=oracle_child,state="succeeded",work_succeeded=True,usage_measured=True)
    assert c.run_once()=="comparing"
    job=c.jobs[f.identity]
    assert job["original_behavior_oracle_task_id"]==oracle_child
    assert json.loads((c.root/f.identity/"oracle-work.json").read_text())["work"]["task_id"]==oracle_child
    assert c.run_once()=="awaiting_independent_review"
    assert c.run_once()=="independent_review"
    original="work:eva-initiative:"+"2"*64
    review_child="work:eva-resume:review-successful-child"
    f.metadata[original].update(task_id=review_child,state="succeeded",work_succeeded=True,usage_measured=True)
    f.review["comparison_sha256"]=f.metadata[original]["comparison_sha256"]
    assert c.run_once()=="rejected"
    assert job["independent_review_task_id"]==review_child
    assert json.loads((c.root/f.identity/"review-work.json").read_text())["work"]["task_id"]==review_child
    assert not f.published
