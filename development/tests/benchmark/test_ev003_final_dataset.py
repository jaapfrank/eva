"""Public synthetic contracts only; no final dataset or native/model execution."""
import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "development/benchmark/inspect_ai_v1"))
from ev003_final_dataset import (Decision, DatasetBindingError, RunEvidence,
                                 load_final_dataset)


def digest(b):
    return hashlib.sha256(b).hexdigest()


def canonical(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


@pytest.fixture
def sealed(tmp_path):
    """A single explicitly public toy case, not an EV-003 task or budget."""
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    candidate, protocol = tmp_path / "candidate.json", tmp_path / "protocol.md"
    candidate.write_text("public candidate")
    protocol.write_text("public protocol")
    sample = {"id": "PUBLIC-TOY", "input": "Put the supplied toy label in a file.",
              "metadata": {"family": "public_test", "oracle_in_input": False}}
    fixture = {"id": sample["id"], "files": {"toy.txt": "PRIVATE_FIXTURE_SENTINEL"},
               "events": ["PRIVATE_EVENT_SENTINEL"]}
    oracle = {"id": sample["id"], "semantic_conditions": ["PRIVATE_ORACLE_SENTINEL"],
              "independent_readbacks": [{"evaluator": "independent_file_readback",
                  "target": "toy.txt", "predicate": "PRIVATE_EXPECTED_SENTINEL",
                  "source": "native file", "required": True}],
              "forbidden_effects": ["PRIVATE_EFFECT_SENTINEL"],
              "human_evaluation": {"required": True},
              "completion_rule": {"all_required_independent_readbacks": True,
                                  "all_semantic_conditions": True}}
    records = {"samples": sample, "fixtures": fixture, "oracles": oracle}
    payloads, hashes = [], {}
    for name, row in records.items():
        data = canonical(row) + b"\n"
        p = private / (name + ".jsonl")
        p.write_bytes(data); p.chmod(0o600)
        payloads.append({"name": p.name, "path": str(p), "sha256": digest(data),
                         "bytes": len(data), "records": 1})
        hashes[name] = digest(canonical(row))
    case = {"id": sample["id"], "family": "public_test", "payload_sha256": hashes,
            "case_sha256": digest(canonical({"id": sample["id"], "payload_sha256": hashes})),
            "required_resource_types": ["private_filesystem"],
            "required_evaluator_types": ["independent_file_readback", "physical_listening_review"]}
    c = {"contract": "EvaComparisonDatasetCommitment/v1", "cases": [case],
         "candidate": {"path": candidate.name, "sha256": digest(candidate.read_bytes())},
         "protocol": {"path": protocol.name, "sha256": digest(protocol.read_bytes())},
         "private_payloads": payloads,
         "dataset": {"different_scenarios": 1, "families": {"public_test": 1},
                     "epochs": 3, "inference_admitted": False,
                     "dataset_total_sha256": digest(canonical({
                         "payload_files": [{"name": x["name"], "sha256": x["sha256"]} for x in payloads],
                         "case_hashes": [{"id": case["id"], "sha256": case["case_sha256"]}]}))}}
    cp = tmp_path / "commitment.json"; cp.write_bytes(canonical(c))
    loader = lambda: load_final_dataset(cp, commitment_sha256=digest(cp.read_bytes()), repo_root=tmp_path)
    return SimpleNamespace(root=tmp_path, private=private, commitment=cp, case=case,
                           loader=loader, dataset=loader())


def evidence(sealed):
    d = sealed.dataset
    sample = d.samples(1)[0]
    art = sealed.root / "actual-recorded-evidence.json"
    art.write_text("Public synthetic readbacks and two synthetic recorded human decisions; not real human acceptance.")
    def decision(scope, passed=True, *, rater=None):
        return Decision(passed=passed, scope=scope, run_id="public-run-1",
                        case_sha256=sample.metadata["case_sha256"], epoch=1,
                        evidence_path=art, evidence_sha256=digest(art.read_bytes()),
                        source="recorded_human" if rater else "independent_readback",
                        rater_id=rater, independent=bool(rater), blinded=bool(rater))
    pair = lambda scope: tuple(decision(scope, rater=r) for r in ("toy-rater-a", "toy-rater-b"))
    run = RunEvidence(run_id="public-run-1", case_sha256=sample.metadata["case_sha256"], epoch=1,
                      resources={"private_filesystem": decision("resource:private_filesystem")},
                      admission=decision("admission"),
                      native_completion=decision("native_completion"), accounting=decision("accounting"),
                      stop=decision("stop"), fresh_state=decision("fresh_state"), effects=decision("forbidden_effects"),
                      semantic=pair("semantic"), human=pair("human"), physical=pair("physical"))
    hooks = {"independent_file_readback": lambda req: decision(req.scope)}
    return run, hooks, decision


def test_inspect_samples_and_scores_never_serialize_fixture_or_oracle(sealed):
    from inspect_ai import Task
    from inspect_ai.solver import solver
    d = sealed.dataset
    sample = d.samples(1)[0]
    assert sample.target == ""
    serialized = sample.model_dump_json()
    assert "PRIVATE_" not in serialized and "PRIVATE_" not in repr(d)
    assert set(sample.metadata) == {"case_id", "family", "epoch", "case_sha256", "dataset_sha256"}
    assert d.samples(2)[0].id == sample.id
    assert d.samples(2)[0].metadata["case_sha256"] == sample.metadata["case_sha256"]
    copied = d.with_fixture(sample.id, 1, lambda epoch, fixture: fixture)
    copied["files"]["toy.txt"] = "mutated"
    assert d.with_fixture(sample.id, 2, lambda epoch, fixture: fixture)["files"]["toy.txt"] != "mutated"
    run, hooks, _ = evidence(sealed)
    scorer = d.make_scorer(lambda cid, epoch, sha: run, hooks)
    @solver
    def no_execution():
        async def solve(state, generate):
            raise AssertionError("this test only constructs a Task")
        return solve
    task = Task(dataset=d.samples(1), solver=no_execution(), scorer=scorer)
    assert len(task.dataset) == 1
    state = SimpleNamespace(sample_id=sample.id, metadata=sample.metadata)
    score = asyncio.run(scorer(state, None))
    assert score.value == "C"
    assert "PRIVATE_" not in score.model_dump_json()
    assert "evidence_path" not in score.model_dump_json()
    bad_state = SimpleNamespace(sample_id=sample.id, metadata={**sample.metadata, "oracle": "injected"})
    assert asyncio.run(scorer(bad_state, None)).value == "unknown"


@pytest.mark.parametrize("mutate", ["commitment", "candidate", "protocol", "payload", "payload_mode", "directory_mode", "record_hash", "case_hash", "total_hash"])
def test_checksum_or_access_mismatch_refuses_without_secret_error(sealed, mutate):
    cp = sealed.commitment; c = json.loads(cp.read_text())
    expected = digest(cp.read_bytes())
    if mutate == "commitment": cp.write_text(cp.read_text() + " ")
    elif mutate in ("candidate", "protocol"):
        (sealed.root / c[mutate]["path"]).write_text("changed")
    elif mutate == "payload": (sealed.private / "oracles.jsonl").write_text("PRIVATE_EXPECTED_SENTINEL")
    elif mutate == "payload_mode": (sealed.private / "oracles.jsonl").chmod(0o644)
    elif mutate == "directory_mode": sealed.private.chmod(0o755)
    else:
        if mutate == "record_hash": c["cases"][0]["payload_sha256"]["samples"] = "0" * 64
        elif mutate == "case_hash": c["cases"][0]["case_sha256"] = "0" * 64
        else: c["dataset"]["dataset_total_sha256"] = "0" * 64
        cp.write_bytes(canonical(c)); expected = digest(cp.read_bytes())
    with pytest.raises(DatasetBindingError) as exc:
        load_final_dataset(cp, commitment_sha256=expected, repo_root=sealed.root)
    assert str(exc.value) == "sealed dataset binding validation failed"
    assert "PRIVATE_" not in str(exc.value)


@pytest.mark.parametrize("field", ["resources", "admission", "native_completion", "stop", "fresh_state", "effects", "semantic", "physical"])
def test_absent_resources_or_actual_decisions_are_unknown(sealed, field):
    run, hooks, _ = evidence(sealed)
    missing = {} if field == "resources" else () if field in ("semantic", "human", "physical") else None
    assert sealed.dataset.evaluate("PUBLIC-TOY", 1, replace(run, **{field: missing}), hooks).value == "unknown"


def test_independent_fail_and_disagreement_remain_distinct(sealed):
    run, hooks, decide = evidence(sealed); d = sealed.dataset
    assert d.evaluate("PUBLIC-TOY", 1, run, {}).value == "unknown"
    def broken(req): raise ValueError("PRIVATE_EXPECTED_SENTINEL")
    score = d.evaluate("PUBLIC-TOY", 1, run, {"independent_file_readback": broken})
    assert score.value == "unknown" and "PRIVATE_" not in score.model_dump_json()
    hooks["independent_file_readback"] = lambda req: decide(req.scope, False)
    assert d.evaluate("PUBLIC-TOY", 1, run, hooks).value == "I"
    disputed = replace(run, semantic=(run.semantic[0], replace(run.semantic[1], passed=False)))
    hooks["independent_file_readback"] = lambda req: decide(req.scope)
    assert d.evaluate("PUBLIC-TOY", 1, disputed, hooks).value == "unknown"
    same_rater = replace(run, human=(run.human[0], run.human[0]))
    score = d.evaluate("PUBLIC-TOY", 1, same_rater, hooks)
    assert score.value == "C" and score.metadata["human_judgments_status"] == "unknown"


def test_evidence_requires_matching_epoch_run_and_retained_bytes(sealed):
    run, hooks, _ = evidence(sealed); d = sealed.dataset
    for change in ({"epoch": 2}, {"run_id": "other-run"}, {"case_sha256": "0" * 64}):
        assert d.evaluate("PUBLIC-TOY", 1, replace(run, **change), hooks).value == "unknown"
    assert d.evaluate("PUBLIC-TOY", 2, replace(run, epoch=2), hooks).value == "unknown"
    run.accounting.evidence_path.write_text("changed receipt")
    assert d.evaluate("PUBLIC-TOY", 1, run, hooks).value == "unknown"
    with pytest.raises(DatasetBindingError): d.samples(4)


def test_private_callback_errors_cannot_leak_into_inspect_logs(sealed):
    def broken(*args): raise RuntimeError("PRIVATE_EXPECTED_SENTINEL")
    for invoke in (lambda: sealed.dataset.with_fixture("PUBLIC-TOY", 1, broken),
                   lambda: sealed.dataset.with_oracle("PUBLIC-TOY", broken)):
        with pytest.raises(DatasetBindingError) as exc:
            invoke()
        assert "PRIVATE_" not in str(exc.value)
        assert exc.value.__suppress_context__ is True


def test_human_preferences_are_individual_and_do_not_fail_task_success(sealed):
    run, hooks, _ = evidence(sealed); d = sealed.dataset
    d._cases["PUBLIC-TOY"]["family"] = "conversation_memory"
    run = replace(run, human=(replace(run.human[0], preference=1),
                              replace(run.human[1], passed=False, preference=0)))
    score = d.evaluate("PUBLIC-TOY", 1, run, hooks)
    assert score.value == "C"
    assert [v["value"] for v in score.metadata["human_preference"]] == [1, 0]
    assert score.metadata["human_preference_status"] == "recorded"
    assert score.metadata["human_judgments_status"] == "recorded"
    assert score.metadata["initiative_useful"] is None
    score = d.evaluate("PUBLIC-TOY", 1, replace(run, human=()), hooks)
    assert score.value == "C" and score.metadata["human_judgments_status"] == "unknown"


@pytest.mark.parametrize("field", ["native_completion", "stop"])
def test_verified_admitted_product_failure_is_not_adapter_unknown(sealed, field):
    run, hooks, decide = evidence(sealed); d = sealed.dataset
    failed = replace(run, **{field: decide(field, False)}, failure_origin="admitted_product")
    score = d.evaluate("PUBLIC-TOY", 1, failed, {})
    assert score.value == "I" and score.metadata["task_success"] is False
    if field == "stop": assert score.metadata["safety_failure"] is True
    for origin in (None, "adapter", "unknown"):
        assert d.evaluate("PUBLIC-TOY", 1, replace(failed, failure_origin=origin), hooks).value == "unknown"
    unmetered_failure = d.evaluate("PUBLIC-TOY", 1, replace(failed, accounting=None), hooks)
    assert unmetered_failure.value == "I"
    assert unmetered_failure.metadata["accounting_status"] == "unknown"
    assert unmetered_failure.metadata["task_success"] is False
    assert d.evaluate("PUBLIC-TOY", 1, replace(failed, admission=None), hooks).value == "unknown"


def test_initiative_disagreement_is_not_useful_without_changing_task_success(sealed):
    run, hooks, _ = evidence(sealed); d = sealed.dataset
    d._cases["PUBLIC-TOY"]["family"] = "initiative"
    disagree = replace(run, human=(run.human[0], replace(run.human[1], passed=False)))
    score = d.evaluate("PUBLIC-TOY", 1, disagree, hooks)
    assert score.value == "C" and score.metadata["initiative_useful"] is False
    assert [v["decision"] for v in score.metadata["individual_human_judgments"]] == [True, False]
    assert d.evaluate("PUBLIC-TOY", 1, run, hooks).metadata["initiative_useful"] is True
    assert d.evaluate("PUBLIC-TOY", 1, replace(run, human=()), hooks).metadata["initiative_useful"] is None


def test_explicit_human_preference_tie_remains_half_not_unknown(sealed):
    run, hooks, _ = evidence(sealed); d = sealed.dataset
    d._cases["PUBLIC-TOY"]["family"] = "conversation_memory"
    run = replace(run, human=(replace(run.human[0], passed=None, preference=0.5),
                              replace(run.human[1], passed=None, preference=1)))
    score = d.evaluate("PUBLIC-TOY", 1, run, hooks)
    assert score.value == "C"
    assert score.metadata["human_preference_status"] == "recorded"
    assert [v["value"] for v in score.metadata["human_preference"]] == [0.5, 1]
    assert score.metadata["initiative_useful"] is None
    invalid = replace(run, human=(replace(run.human[0], preference=True), run.human[1]))
    assert d.evaluate("PUBLIC-TOY", 1, invalid, hooks).metadata["human_preference_status"] == "unknown"


@pytest.mark.parametrize("outcome, expected", [("success", "C"), ("readback_failure", "I"), ("forbidden_effect", "I")])
def test_unknown_accounting_does_not_erase_observed_task_or_safety_outcome(sealed, outcome, expected):
    run, hooks, decide = evidence(sealed)
    run = replace(run, accounting=None)
    if outcome == "readback_failure":
        hooks["independent_file_readback"] = lambda req: decide(req.scope, False)
    elif outcome == "forbidden_effect":
        run = replace(run, effects=decide("forbidden_effects", False))
    score = sealed.dataset.evaluate("PUBLIC-TOY", 1, run, hooks)
    assert score.value == expected
    assert score.metadata["task_success"] is (expected == "C")
    assert score.metadata["accounting_status"] == "unknown"
    if outcome == "forbidden_effect":
        assert score.metadata["safety_failure"] is True
