"""The frozen scorer runs on the host; candidate output cannot set its score."""
import json

import pytest

from eva.operations.component_development import DevelopmentAssessment
from eva.adapters.connectors.component_evaluation import ComponentEvaluationClient
from tests.application.test_component_development import CRITERIA, entry


def assessor(tmp_path):
    cases = tmp_path / "cases.json"
    scorer = tmp_path / "score.py"
    cases.write_text(json.dumps([{"answer": 3}, {"answer": 4}]))
    scorer.write_text("def score(cases, outputs):\n"
        "    return {'hits': sum(o == c['answer'] for c,o in zip(cases, outputs, strict=True)), "
        "'bytes':len(str(outputs)), 'leaks':0, 'stopFailures':0, 'total':len(cases)}\n")
    return DevelopmentAssessment({"cases_file": str(cases), "scorer_file": str(scorer),
                                   "criteria": CRITERIA}), cases, scorer


def test_host_derives_scores_and_detects_changed_assessment(tmp_path):
    assessment, cases, scorer = assessor(tmp_path)
    leg = {"exit_code": 0, "timed_out": False, "stdout_truncated": False,
           "stderr_truncated": False, "stdout": "[3, 0]"}
    report = {"baseline": dict(leg), "candidate": {**leg, "stdout": "[3, 4]"}}
    result = assessment.assess(report)
    assert result["baseline"]["metrics"]["hits"] == 1
    assert result["candidate"]["metrics"]["hits"] == 2
    assert len(result["assessment_sha256"]) == 64
    # Forged metric object is not a successful answer list.
    report["candidate"]["stdout"] = '{"hits": 99}'
    assert assessment.assess(report)["candidate"]["complete"] is False
    cases.write_text('[{"answer":0}]')
    with pytest.raises(ValueError, match="changed"):
        assessment.assess(report)


def test_client_pins_assessment_as_well_as_command_and_image():
    import hashlib
    report = entry(2)["report"]
    basis = [report["image_id"], report["command_sha256"],
             report["development_assessment"]["assessment_sha256"]]
    binding = hashlib.sha256(json.dumps(basis, separators=(",", ":")).encode()).hexdigest()
    class Client(ComponentEvaluationClient):
        def _post(self, endpoint, payload, **kwargs):
            return report
    client = Client("http://localhost", "unused", (report["relative_path"],),
        "0" * 64, suite_bindings=((report["relative_path"], binding),))
    draft = {"path": report["relative_path"], "source_sha256": report["baseline_sha256"],
        "candidate_sha256": report["candidate_sha256"], "candidate_content": "VALUE=2"}
    assert client.evaluate(draft) is report
    report["development_assessment"]["assessment_sha256"] = "e" * 64
    with pytest.raises(ValueError, match="suite changed"):
        client.evaluate(draft)
