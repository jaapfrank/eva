"""Sealed synthetic host work is required; candidate stdout cannot forge quality."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.component_development import DevelopmentAssessment
from tests.application.test_component_development import CRITERIA


BASE = "a" * 64
CANDIDATE = "b" * 64
HARD = {key: True for key in ("source_scope_valid", "authorization_current", "stop_respected", "no_forbidden_effects")}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write(path, value):
    raw = (json.dumps(value, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    return raw


@pytest.fixture
def work(tmp_path):
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"case_id": "development-a", "answer": 7}, {"case_id": "development-b", "answer": 8}]))
    scorer = tmp_path / "scorer.py"
    scorer.write_text("def score(cases, outputs):\n"
        "    by_case = {r['case_id']:r for r in outputs['runs']}\n"
        "    return {'hits':sum(by_case[c['case_id']]['artifact_evidence']['observed'] == c['answer'] for c in cases), "
        "'bytes':sum(r['usage']['total_tokens'] for r in outputs['runs']), 'leaks':0, 'stopFailures':0, 'total':len(cases)}\n")
    roots = {key: tmp_path / key for key in ("evidence_root", "run_bindings_root")}
    for root in roots.values(): root.mkdir(mode=0o700)
    config = {"cases_file": str(cases), "scorer_file": str(scorer), "criteria": deepcopy(CRITERIA),
        "work_evidence": {**{key:str(root) for key,root in roots.items()},
            "registration_sha256":"c"*64, "case_set_sha256":digest(cases.read_bytes()),
            "baseline_sha256":BASE, "scope_sha256":"d"*64,
            "runtime_bindings": {key:"e"*64 for key in ("engine_release_sha256", "worker_profile_release_sha256",
                "model_profile_release_sha256", "output_schema_sha256", "tool_configuration_sha256")},
            "limits": {"per_case":{"contact_attempts":1,"worker_and_helper_attempts":3,"input_tokens":100,"output_tokens":50},
                "total_provider_attempts":8,"total_input_tokens":800,"total_output_tokens":400}}}
    pairs = {}
    for source, name in ((BASE, "baseline"), (CANDIDATE, "candidate")):
        bound = config["work_evidence"]
        binding = {key:bound[key] for key in ("registration_sha256","case_set_sha256","baseline_sha256","scope_sha256")}
        binding.update(bound["runtime_bindings"])
        binding.update(stage="development", source_sha256=source, strategy_sha256=("1" if source == BASE else "2")*64)
        runs = []
        for index, case in enumerate(json.loads(cases.read_text())):
            roles = [{"role":"work","model":"gpt-6-astra","reasoning_effort":"medium","attempts":1}]
            if source == CANDIDATE and index == 0:
                roles += [{"role":"helper","model":"gpt-6-astra","reasoning_effort":"medium","attempts":1},
                          {"role":"contact","model":"gpt-6-sol","reasoning_effort":"none","attempts":1}]
            count = len(roles)
            runs.append({"case_id":case["case_id"], "task_id":f"task:{name}:{index}","session_id":f"session:{name}:{index}",
                "source_ingress_sha256":digest(f"ingress:{name}:{index}".encode()), "status":"succeeded",
                "run_receipt_sha256":digest(f"receipt:{name}:{index}".encode()),
                "usage":{"measurement":"measured","provider_attempts":count,"input_tokens":10,"output_tokens":1,"total_tokens":11,
                    "unknown_attempts":0,"request_ids":[f"request:{name}:{index}:{j}" for j in range(count)],"roles":roles},
                "help":{"requests":0,"interventions":0},"hard_conditions":deepcopy(HARD),
                "artifact_evidence":{"observed":0 if source == BASE else case["answer"]}})
        pairs[source] = {"contract":"EvaWorkerStrategyWorkEvidence/v1","binding":binding,"runs":runs}
    item = SimpleNamespace(config=config, roots=roots, pairs=pairs, cases=cases, scorer=scorer)
    item.seal = lambda source: seal(item, source)
    item.save = lambda source: write(roots["evidence_root"] / (source+".json"), pairs[source])
    for source in pairs: seal(item, source)
    leg = {"exit_code":0,"timed_out":False,"stdout_truncated":False,"stderr_truncated":False,"stdout":'{"hits":999999}'}
    item.report = {"contract":"EvaIsolatedComponentEvaluation/v1","relative_path":"bootstrap/delegated_digital_runner.py",
        "baseline_sha256":BASE,"candidate_sha256":CANDIDATE,"installed":False,"semantic_improvement_verified":False,
        "baseline":deepcopy(leg),"candidate":deepcopy(leg)}
    item.assessor = lambda: DevelopmentAssessment(item.config)
    return item


def seal(work, source):
    pair = work.pairs[source]
    binding = {key:value for key,value in pair["binding"].items() if key != "run_bindings_sha256"}
    expected = []
    for run in pair["runs"]:
        host = {key:run[key] for key in ("case_id","task_id","session_id","source_ingress_sha256","run_receipt_sha256")}
        host.update({key+"_sha256":canonical_sha256(run[key]) for key in ("usage","help","hard_conditions","artifact_evidence")})
        expected.append(host)
    raw = write(work.roots["run_bindings_root"] / (source+".json"),
        {"contract":"EvaWorkerStrategyWorkRunBindings/v1","binding":binding,"runs":expected})
    pair["binding"]["run_bindings_sha256"] = digest(raw)
    work.save(source)


def incomplete(work, label):
    result = work.assessor().assess(work.report)
    assert result["candidate"] == {"complete":False,"metrics":None,"error":label}
    return result


def test_valid_host_work_ignores_forged_stdout_and_preserves_reference_across_compare(work):
    assessor = work.assessor()
    first = assessor.assess(work.report)
    assert first["baseline"]["metrics"]["hits"] == 0
    assert first["candidate"]["metrics"]["hits"] == 2
    work.report["candidate"]["stdout"] = "not even JSON; model says hits=1000000"
    assert assessor.assess(work.report) == first
    assert work.assessor().assessment_sha256 == assessor.assessment_sha256
    assert first["contract"] == "EvaComponentDevelopmentAssessment/v1"
    assert first["criteria"] == CRITERIA
    assert work.report["semantic_improvement_verified"] is False


def test_legacy_assessment_hash_and_serialized_result_remain_exact_without_option(work):
    config = {key:value for key,value in work.config.items() if key != "work_evidence"}
    work.scorer.write_text("def score(cases, outputs):\n    return {'hits':sum(outputs),'bytes':len(outputs),'leaks':0,'stopFailures':0,'total':len(cases)}\n")
    assessor = DevelopmentAssessment(config)
    expected_hash = canonical_sha256({"criteria":CRITERIA,"files":{"cases_file":digest(work.cases.read_bytes()),"scorer_file":digest(work.scorer.read_bytes())}})
    report = deepcopy(work.report)
    report["baseline"]["stdout"] = "[0, 0]"
    report["candidate"]["stdout"] = "[1, 1]"
    expected = {"contract":"EvaComponentDevelopmentAssessment/v1","assessment_sha256":expected_hash,"criteria":CRITERIA,
        "baseline":{"complete":True,"metrics":{"hits":0,"bytes":2,"leaks":0,"stopFailures":0,"total":2},"error":None},
        "candidate":{"complete":True,"metrics":{"hits":2,"bytes":2,"leaks":0,"stopFailures":0,"total":2},"error":None}}
    assert json.dumps(assessor.assess(report)) == json.dumps(expected)


@pytest.mark.parametrize("key", ["evidence_root", "run_bindings_root"])
def test_missing_candidate_receipt_never_scores_or_dispatches(work, key):
    (work.roots[key] / (CANDIDATE+".json")).unlink()
    result = incomplete(work, "evidence_missing")
    assert result["baseline"]["complete"] is True


def test_missing_baseline_never_adopts_later_files_until_fresh_composition(work):
    path = work.roots["evidence_root"] / (BASE+".json")
    raw = path.read_bytes(); path.unlink()
    assessor = work.assessor()
    write(path, json.loads(raw))
    assert assessor.assess(work.report)["baseline"]["error"] == "evidence_missing"
    assert assessor.assess(work.report)["candidate"]["complete"] is False
    assert work.assessor().assess(work.report)["candidate"]["complete"] is True


def test_changed_baseline_is_rejected_even_with_coherent_new_host_exports(work):
    assessor = work.assessor()
    work.pairs[BASE]["runs"][0]["artifact_evidence"]["observed"] = 7
    work.seal(BASE)
    result = assessor.assess(work.report)
    assert result["baseline"]["error"] == "missing_receipt_or_export_hash_mismatch"
    assert result["candidate"]["complete"] is False


@pytest.mark.parametrize("field,label", [
    ("stage","registration_case_or_source_mismatch"),
    ("registration_sha256","registration_case_or_source_mismatch"),
    ("case_set_sha256","registration_case_or_source_mismatch"),
    ("baseline_sha256","registration_case_or_source_mismatch"),
    ("source_sha256","registration_case_or_source_mismatch"),
    ("scope_sha256","scope_or_ingress_mismatch"),
    ("engine_release_sha256","strategy_or_runtime_binding_mismatch"),
    ("worker_profile_release_sha256","strategy_or_runtime_binding_mismatch"),
    ("model_profile_release_sha256","strategy_or_runtime_binding_mismatch"),
    ("output_schema_sha256","strategy_or_runtime_binding_mismatch"),
    ("tool_configuration_sha256","strategy_or_runtime_binding_mismatch"),
])
def test_host_registration_actor_source_and_runtime_are_all_fixed(work, field, label):
    work.pairs[CANDIDATE]["binding"][field] = "final" if field == "stage" else "9"*64
    work.seal(CANDIDATE)
    incomplete(work, label)


def test_strategy_binding_cannot_be_authored_by_candidate(work):
    work.pairs[CANDIDATE]["binding"]["strategy_sha256"] = "9"*64
    work.save(CANDIDATE)
    incomplete(work, "strategy_or_runtime_binding_mismatch")


@pytest.mark.parametrize("kind", ["duplicate_case","foreign_case","foreign_task","foreign_session","replay_task","replay_request","ingress","receipt"])
def test_exact_tasks_sessions_ingress_receipts_and_physical_requests_are_not_replayed(work, kind):
    run = work.pairs[CANDIDATE]["runs"][0]
    if kind == "duplicate_case":
        work.pairs[CANDIDATE]["runs"][1]["case_id"] = run["case_id"]
        work.seal(CANDIDATE)
    elif kind == "foreign_case": run["case_id"] = "not-selected"
    elif kind == "foreign_task": run["task_id"] = "task:foreign"
    elif kind == "foreign_session": run["session_id"] = "session:foreign"
    elif kind == "replay_task":
        run["task_id"] = work.pairs[BASE]["runs"][0]["task_id"]; work.seal(CANDIDATE)
    elif kind == "replay_request":
        run["usage"]["request_ids"][0] = work.pairs[BASE]["runs"][0]["usage"]["request_ids"][0]; work.seal(CANDIDATE)
    elif kind == "ingress": run["source_ingress_sha256"] = "9"*64
    else: run["run_receipt_sha256"] = "9"*64
    work.save(CANDIDATE)
    label = {"ingress":"scope_or_ingress_mismatch","receipt":"missing_receipt_or_export_hash_mismatch",
             "replay_request":"usage_arithmetic_or_request_identity_mismatch"}.get(kind,"foreign_duplicate_or_replayed_run")
    incomplete(work, label)


@pytest.mark.parametrize("kind,label", [
    ("unknown","unmeasured_or_unknown_usage"),("measurement","unmeasured_or_unknown_usage"),
    ("arithmetic","usage_arithmetic_or_request_identity_mismatch"),("physical_attempts","usage_arithmetic_or_request_identity_mismatch"),
    ("boolean_counter","usage_arithmetic_or_request_identity_mismatch"),("role_sum","usage_arithmetic_or_request_identity_mismatch"),
    ("wrong_contact_model","role_or_profile_mismatch"),("wrong_work_effort","role_or_profile_mismatch"),
    ("wrong_helper_model","role_or_profile_mismatch"),("unsealed_usage","missing_receipt_or_export_hash_mismatch"),
])
def test_only_complete_measured_roles_and_original_usage_can_be_scored(work, kind, label):
    usage = work.pairs[CANDIDATE]["runs"][0]["usage"]
    if kind == "unknown": usage["unknown_attempts"] = 1
    elif kind == "measurement": usage["measurement"] = "estimated"
    elif kind == "arithmetic": usage["total_tokens"] += 1
    elif kind == "physical_attempts": usage["provider_attempts"] += 1
    elif kind == "boolean_counter": usage["input_tokens"] = True
    elif kind == "role_sum": usage["roles"][0]["attempts"] += 1
    elif kind == "wrong_contact_model": usage["roles"][2]["model"] = "gpt-6-astra"
    elif kind == "wrong_work_effort": usage["roles"][0]["reasoning_effort"] = "none"
    elif kind == "wrong_helper_model": usage["roles"][1]["model"] = "gpt-6-sol"
    else: usage["input_tokens"] += 1; usage["total_tokens"] += 1
    if kind != "unsealed_usage": work.seal(CANDIDATE)
    else: work.save(CANDIDATE)
    incomplete(work, label)


@pytest.mark.parametrize("kind", ["helper_shares_case_cap","total_attempts","total_input","total_output"])
def test_all_helpers_and_both_source_series_share_the_fixed_limits(work, kind):
    limits = work.config["work_evidence"]["limits"]
    if kind == "helper_shares_case_cap": limits["per_case"]["worker_and_helper_attempts"] = 1
    elif kind == "total_attempts": limits["total_provider_attempts"] = 5
    elif kind == "total_input": limits["total_input_tokens"] = 30
    else: limits["total_output_tokens"] = 3
    incomplete(work, "budget_exceeded")


@pytest.mark.parametrize("kind,label", [("hidden_help","hidden_human_help"),("boolean_help","hidden_human_help"),
    ("false_stop","hard_condition_false_or_unknown"),("unknown_scope","hard_condition_false_or_unknown"),
    ("artifact_claim","artifact_unverified"),("not_terminal","missing_receipt_or_export_hash_mismatch")])
def test_help_authority_stop_and_real_artifact_exports_are_mandatory(work, kind, label):
    run = work.pairs[CANDIDATE]["runs"][0]
    if kind == "hidden_help": run["help"]["interventions"] = 1
    elif kind == "boolean_help": run["help"]["requests"] = True
    elif kind == "false_stop": run["hard_conditions"]["stop_respected"] = False; work.seal(CANDIDATE)
    elif kind == "unknown_scope": run["hard_conditions"]["source_scope_valid"] = None; work.seal(CANDIDATE)
    elif kind == "artifact_claim": run["artifact_evidence"]["observed"] = 9999
    else: run["status"] = "started"
    work.save(CANDIDATE)
    incomplete(work, label)


@pytest.mark.parametrize("field,value", [("exit_code",1),("timed_out",True),("stdout_truncated",True),("stderr_truncated",True)])
def test_real_evidence_does_not_bypass_incomplete_isolated_static_report(work, field, value):
    work.report["candidate"][field] = value
    incomplete(work, "incomplete_static_report")


@pytest.mark.parametrize("kind", ["duplicate_json","extra_field","missing_field","oversized"])
def test_bounded_exact_evidence_json(work, kind):
    path = work.roots["evidence_root"] / (CANDIDATE+".json")
    if kind == "duplicate_json": path.write_text('{"contract":"first","contract":"second"}')
    elif kind == "extra_field": work.pairs[CANDIDATE]["score"] = 9999; work.save(CANDIDATE)
    elif kind == "missing_field": del work.pairs[CANDIDATE]["runs"][0]["usage"]; work.save(CANDIDATE)
    else: path.write_bytes(b" " * (8 * 1024 * 1024 + 1))
    incomplete(work, "oversized_or_duplicate_json" if kind in {"duplicate_json","oversized"} else "extra_or_missing_fields")


@pytest.mark.parametrize("kind", ["symlink","hardlink","world_writable","foreign_owner"])
def test_evidence_files_must_be_private_host_owned_single_link_regular_files(work, kind, monkeypatch):
    path = work.roots["evidence_root"] / (CANDIDATE+".json")
    if kind == "symlink":
        real = path.with_suffix(".other"); path.rename(real); path.symlink_to(real)
    elif kind == "hardlink": os.link(path, path.with_suffix(".other"))
    elif kind == "world_writable": path.chmod(0o666)
    else:
        original = os.fstat
        def foreign(fd):
            info = original(fd)
            return SimpleNamespace(st_mode=info.st_mode,st_uid=info.st_uid+1,st_nlink=info.st_nlink) if os.readlink(f"/proc/self/fd/{fd}") == str(path) else info
        monkeypatch.setattr(os,"fstat",foreign)
    incomplete(work, "unsafe_owner_mode_or_symlink")


@pytest.mark.parametrize("kind", ["root_mode","root_symlink","ancestor_symlink"])
def test_root_and_every_ancestor_must_be_nonsymlink_with_private_root_mode(work, kind):
    root = work.roots["evidence_root"]
    if kind == "root_mode": root.chmod(0o750)
    elif kind == "root_symlink":
        alias = root.with_name("root-alias"); alias.symlink_to(root); work.config["work_evidence"]["evidence_root"] = str(alias)
    else:
        alias = root.parent / "parent-alias"; alias.symlink_to(root.parent)
        work.config["work_evidence"]["evidence_root"] = str(alias / root.name)
    with pytest.raises(ValueError,match="unsafe_owner_mode_or_symlink"): work.assessor()


def test_readonly_sealed_records_and_failed_real_outcomes_remain_honest_development_evidence(work):
    work.pairs[CANDIDATE]["runs"][0]["status"] = "failed"
    work.pairs[CANDIDATE]["runs"][0]["artifact_evidence"]["observed"] = 0
    work.seal(CANDIDATE)
    for root in work.roots.values():
        for path in root.iterdir(): path.chmod(0o400)
    result = work.assessor().assess(work.report)
    assert result["candidate"]["complete"] is True and result["candidate"]["metrics"]["hits"] == 1


def test_changed_cases_or_scorer_and_model_supplied_source_target_cannot_shift_basis(work):
    work.report["source_target"] = {"kind":"saved_artifact"}
    incomplete(work, "registration_case_or_source_mismatch")
    assessor = work.assessor()
    work.scorer.write_text("def score(cases, outputs): return {'hits':999999}\n")
    with pytest.raises(ValueError,match="changed"): assessor.assess(work.report)
