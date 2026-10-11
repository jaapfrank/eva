"""Sealed selected-candidate transfer to independent assessment and managed application."""
from __future__ import annotations
import fcntl
from decimal import Decimal
import importlib.util
import json
import math
import os
from pathlib import Path
import threading
from eva.application.component_improvements import bind_selected_release
from eva.operations.component_evaluation import (
    CandidateRequest, ComponentEvaluator, EvaluationConfig, validate_saved_artifact_target,
)
from eva.operations.reviewed_component_application import _artifact, _identifier, _path, _save
from tools.import_component_release import canonical, digest, load_object

_TERMINAL = {"rejected", "awaiting_independent_answer_assessment", "cancelled", "unconfirmed", "application_requested"}
_METRICS = {"hard_failures", "included_references", "context_bytes", "input_tokens",
            "output_tokens", "median_microseconds"}
_COUNTERS = {"case_count", "static_failure_count", "relevance_regressions", "required_references",
             "newly_answerable_questions", "answer_calls", "correctness_regressions",
             "new_direct_answer_wins", "model_calls_performed_by_scorer"}
_FLAGS = {"answer_stage_eligible", "answer_quality_observed", "latency_gate_passed",
          "token_reduction_branch_passed"}

def public_decision(result, package):
    """Only named numeric aggregates; arbitrary/nested scorer text stays private."""
    decision = result["decision"]
    value = {"contract": "EvaIndependentComponentFinalDecision/v1", "decision": decision,
        "reason": {"accept": "independent_criteria_passed",
                   "reject": "independent_criteria_not_met",
                   "needs_independent_answer_assessment": "independent_answer_evidence_required"}[decision],
        "candidate_sha256": package["candidate_sha256"],
        "baseline_sha256": package["baseline_sha256"],
        "semantic_improvement_verified": decision == "accept",
        "answer_assessment_required": decision == "needs_independent_answer_assessment"}
    # Tool receipts use canonical JSON: floats are forbidden. Keep time units
    # explicit and leave original scorer/decision artifacts untouched.
    for key in _METRICS:
        if isinstance(result.get(key), dict):
            value[key] = {leg: result[key][leg] for leg in ("baseline", "candidate")
                          if type(result[key].get(leg)) is int}
    seconds = result.get("median_seconds")
    if isinstance(seconds, dict):
        value["median_microseconds"] = {
            leg: int((Decimal(str(seconds[leg])) * 1_000_000).to_integral_value())
            for leg in ("baseline", "candidate")
            if (type(seconds.get(leg)) is int or
                (type(seconds.get(leg)) is float and math.isfinite(seconds[leg])))}
    for key in _COUNTERS:
        if type(result.get(key)) is int:
            value[key] = result[key]
    for key in _FLAGS:
        if type(result.get(key)) is bool or result.get(key) is None:
            value[key] = result.get(key)
    binding = result.get("binding", {})
    value["binding"] = {key: item for key, item in binding.items()
        if key in {"registration_sha256", "criteria_sha256", "dataset_sha256", "runner_sha256",
                   "scorer_sha256", "baseline_sha256", "candidate_sha256", "isolated_report_sha256"}
        and isinstance(item, str) and len(item) == 64 and all(c in "0123456789abcdef" for c in item)}
    return value


def validate_selected_package(package):
    if (not isinstance(package, dict) or package.get("contract") != "EvaComponentReleaseCandidate/v1"
            or package.get("state") != "awaiting_measured_gain_review" or package.get("installed") is not False
            or package.get("candidate_package_sha256") != digest(canonical(
                {k: v for k, v in package.items() if k != "candidate_package_sha256"}))
            or not isinstance(package.get("candidate_content"), str)
            or digest(package["candidate_content"].encode()) != package.get("candidate_sha256")
            or digest(canonical(package.get("evaluation"))) != package.get("evaluation_sha256")):
        raise ValueError("selected release package binding differs")
    choice = package.get("development_selection", {})
    base = {k: v for k, v in package.items() if k not in {
        "development_selection", "development_selection_sha256", "candidate_package_sha256"}}
    if (package.get("development_selection_sha256") != digest(canonical(choice))
            or bind_selected_release(base, choice) != package):
        raise ValueError("selected release differs from development choice")
    CandidateRequest(package["path"], package["baseline_sha256"], package["candidate_content"],
        **({"baseline_source": package.get("baseline_content"),
            "source_target": package["source_target"]} if "source_target" in package else {}))
    return package


def _protocol_package(package, protocol):
    validate_selected_package(package)
    if not protocol or protocol.get("baseline_sha256") != package["baseline_sha256"]:
        raise ValueError("no frozen final protocol for this baseline")
    if protocol.get("source_target") != package.get("source_target"):
        raise ValueError("selected source differs from frozen final target")
    return package


def _assessor_protocol(assessor, protocol):
    if (isinstance(assessor, HostFinalAssessment)
            and assessor.config.get("source_target") != protocol.get("source_target")):
        raise ValueError("final protocol and fixed assessment target differ")


class HostFinalAssessment:
    """Fixed host scorer and private oracles; candidate executes only in isolation."""
    def __init__(self, config):
        if (not isinstance(config, dict)
                or set(config) - {"evaluation", "scorer", "registration", "bindings", "answer_evidence_file", "source_target"}
                or not {"evaluation", "scorer", "registration", "bindings"} <= set(config)):
            raise ValueError("invalid independent assessment configuration")
        self.config = json.loads(json.dumps(config))
        evaluation = dict(config["evaluation"])
        evaluation["command"] = tuple(evaluation["command"])
        if not evaluation["image"].startswith("sha256:"):
            raise ValueError("final evaluator image must be immutable")
        _identifier(evaluation["image"][7:])
        source_target = self.config.get("source_target")
        if source_target is not None:
            self.config["source_target"] = validate_saved_artifact_target(source_target)
        self.evaluator = ComponentEvaluator(EvaluationConfig(**evaluation),
                                           source_target=self.config.get("source_target"))
        self._validate()

    def _validate(self):
        if self.config.get("source_target") != self.evaluator.source_target:
            raise ValueError("fixed independent assessment target changed")
        _, registration = _artifact(self.config["registration"])
        scorer = _path(self.config["scorer"]["path"])
        if digest(scorer.read_bytes()) != self.config["scorer"]["sha256"]:
            raise ValueError("independent scorer changed")
        for binding in self.config["bindings"].values():
            source = _path(binding["path"])
            if digest(source.read_bytes()) != binding["sha256"]:
                raise ValueError("independent assessment inputs changed")
        self.registration = registration
        spec = importlib.util.spec_from_file_location("eva_final_" + self.config["registration"]["sha256"], scorer)
        self.scorer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.scorer)

    def _candidate(self, package):
        validate_selected_package(package)
        request = CandidateRequest(
            package["path"], package["baseline_sha256"], package["candidate_content"],
            **({"baseline_source": package["baseline_content"],
                "source_target": package["source_target"]} if "source_target" in package else {}))
        if request.source_target != self.evaluator.source_target:
            raise ValueError("selected source differs from fixed independent target")
        return request

    def _report_target(self, package, report):
        if report.get("source_target") != package.get("source_target"):
            raise ValueError("independent report source target differs")
        if package.get("source_target") is not None and (
                report.get("relative_path") != package["path"]
                or report.get("baseline_sha256") != package["baseline_sha256"]
                or report.get("candidate_sha256") != package["candidate_sha256"]
                or report.get("image_id") != self.evaluator.config.image
                or report.get("command_sha256") != digest(json.dumps(
                    self.evaluator.config.command, separators=(",", ":")).encode())):
            raise ValueError("independent report saved source binding differs")

    def _final_binding(self, report):
        binding = {
            "registration_sha256": self.config["registration"]["sha256"],
            "scorer_sha256": self.config["scorer"]["sha256"],
            "bindings": {k: v["sha256"] for k, v in self.config["bindings"].items()},
            "image_id": report["image_id"], "command_sha256": report["command_sha256"]}
        if self.config.get("source_target") is not None:
            binding["source_target_sha256"] = digest(canonical(self.config["source_target"]))
        return binding

    def __call__(self, package, output_root):
        self._validate()
        request = self._candidate(package)
        self.scorer.check_candidate(package["candidate_content"].encode(), package["candidate_sha256"])
        report = self.evaluator.evaluate(request)
        self._report_target(package, report)
        report["final_assessment_binding"] = self._final_binding(report)
        _save(output_root / "isolated-final.private.json", report, initial=True)
        return self._decide(package, report, output_root)

    def answer_evidence_ready(self):
        filename = self.config.get("answer_evidence_file")
        return bool(filename and Path(filename).exists())

    def complete(self, package, output_root):
        self._validate()
        self._candidate(package)
        report = load_object(_path(str(output_root / "isolated-final.private.json")))
        self._report_target(package, report)
        if (package.get("source_target") is not None
                and report.get("final_assessment_binding") != self._final_binding(report)):
            raise ValueError("retained independent saved assessment binding changed")
        filename = self.config.get("answer_evidence_file")
        if not filename:
            raise ValueError("no separately admitted answer evidence")
        answer = _path(filename)
        binding = {"path": str(answer), "sha256": digest(answer.read_bytes())}
        return self._decide(package, report, output_root, answer_evidence=binding)

    def _decide(self, package, report, output_root, *, answer_evidence=None):
        result = self.scorer.assess(report, candidate_source=package["candidate_content"].encode(),
                                   answer_evidence=answer_evidence)
        binding = result.get("binding", {}) if isinstance(result, dict) else {}
        if (not isinstance(result, dict) or result.get("decision") not in {
                "accept", "reject", "needs_independent_answer_assessment"}
                or binding.get("candidate_sha256") != package["candidate_sha256"]
                or binding.get("baseline_sha256") != package["baseline_sha256"]
                or binding.get("registration_sha256") != self.config["registration"]["sha256"]
                or binding.get("isolated_report_sha256") != digest(canonical(report))
                or (result["decision"] == "accept" and result.get("semantic_improvement_verified") is not True)):
            raise ValueError("independent semantic decision binding differs")
        suffix = "-answer" if answer_evidence is not None else ""
        _save(output_root / ("decision" + suffix + ".private.json"), result, initial=True)
        public = public_decision(result, package)
        public_file = output_root / ("decision" + suffix + ".json")
        _save(public_file, public, initial=True)
        return {"decision": result["decision"], "public": public,
                "evidence": {"path": str(public_file), "sha256": digest(canonical(public))},
                "registration": self.config["registration"]}

class FinalComponentAssessments:
    """One final candidate per registered experiment; no holdout feedback loop."""
    def __init__(self, config, *, assessors, publisher, applications, task_state):
        if (not isinstance(config, dict) or set(config) != {"receipt_root", "scope_sha256", "protocols"}
                or not config["protocols"] or set(config["protocols"]) != set(assessors)):
            raise ValueError("invalid final assessment queue")
        self.scope = _identifier(config["scope_sha256"])
        self.protocols = json.loads(json.dumps(config["protocols"]))
        for path, protocol in self.protocols.items():
            CandidateRequest(path, protocol["baseline_sha256"], "pass")
            _identifier(protocol["registration_sha256"])
            if protocol.get("source_target") is not None:
                validate_saved_artifact_target(protocol["source_target"], relative_path=path,
                                               baseline_sha256=protocol["baseline_sha256"])
            _assessor_protocol(assessors[path], protocol)
        self.root = _path(config["receipt_root"], exists=False)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("final receipts require a private host directory")
        self._file = (self.root / ".dispatcher.lock").open("a+b")
        try:
            fcntl.flock(self._file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            self._file.close()
            raise ValueError("another final dispatcher owns these receipts") from None
        self.assessors, self.publisher, self.applications, self.task_state = assessors, publisher, applications, task_state
        self.condition, self.closed, self.jobs = threading.Condition(), False, {}
        try:
            for path in self.root.glob("*.json"):
                job = load_object(_path(path))
                if (job.get("contract") != "EvaSelectedComponentFinalAssessment/v1"
                        or path.name != _identifier(job.get("review_id")) + ".json"
                        or job.get("scope_sha256") != self.scope
                        or job.get("state") not in _TERMINAL | {"queued", "waiting", "assessing", "publishing", "answer_assessing"}):
                    raise ValueError("final receipt differs")
                _identifier(job["request_id"])
                self.applications._task(job["task_id"])
                if (self.protocols.get(job.get("path"), {}).get("source_target") is not None
                        or job.get("path", "").startswith("saved_artifact_")):
                    self._queued_package(job)
                if job["state"] in {"assessing", "publishing", "answer_assessing"}:
                    job.update(state="unconfirmed", reason="assessment_interrupted_after_dispatch")
                    _save(path, job)
                self.jobs[job["review_id"]] = job
        except Exception:
            self._file.close()
            raise
        self.thread = threading.Thread(target=self._run, name="component-final-assessment", daemon=True)
        self.thread.start()

    def _result(self, job):
        result = {k: job[k] for k in ("review_id", "request_id", "task_id", "state")}
        result.update(installed=False, independent_final=True)
        if "public" in job:
            # Re-project retained legacy decisions as well as newly scored ones.
            public = job["public"]
            result["assessment"] = (
                public_decision(public, public)
                if {"decision", "candidate_sha256", "baseline_sha256"} <= public.keys()
                else public)
        if "reason" in job: result["reason"] = job["reason"]
        # A crash after accepted application request must not hide its real receipt.
        registered = job["review_id"] in getattr(self.applications, "releases", {})
        if job["state"] == "application_requested" or (job["state"] == "unconfirmed" and registered):
            result["application"] = self.applications.review_status(job["review_id"])
            result["installed"] = result["application"]["installed"]
            if job["state"] == "unconfirmed":
                result["final_assessment_state"] = "unconfirmed"
        return result

    def request(self, package, *, request_id, task_id, scope_sha256):
        validate_selected_package(package)
        _identifier(request_id)
        self.applications._task(task_id)
        if scope_sha256 != self.scope: raise ValueError("final source differs")
        protocol = self.protocols.get(package["path"])
        _protocol_package(package, protocol)
        _assessor_protocol(self.assessors[package["path"]], protocol)
        if self.task_state(task_id) == "cancelled": raise ValueError("final task was stopped")
        review_id = package["candidate_package_sha256"]
        with self.condition:
            if self.closed: raise ValueError("final dispatcher is closed")
            existing = self.jobs.get(review_id)
            if existing:
                if (existing["task_id"] != task_id or existing["request_id"] != request_id
                        or existing["protocol_sha256"] != protocol["registration_sha256"]):
                    raise ValueError("final replay differs")
                return self._result(existing)
            if any(j["protocol_sha256"] == protocol["registration_sha256"] for j in self.jobs.values()):
                raise ValueError("final experiment already consumed; no optimizer feedback")
            directory = self.root / review_id
            directory.mkdir(mode=0o700)
            _save(directory / "package.json", package, initial=True)
            job = {"contract": "EvaSelectedComponentFinalAssessment/v1", "review_id": review_id,
                   "request_id": request_id, "task_id": task_id, "scope_sha256": self.scope,
                   "protocol_sha256": protocol["registration_sha256"], "path": package["path"],
                   "package": {"path": str(directory / "package.json"), "sha256": digest(canonical(package))},
                   "state": "queued"}
            _save(self.root / (review_id + ".json"), job, initial=True)
            self.jobs[review_id] = job
            self.condition.notify_all()
            return self._result(job)

    def status(self, review_id, *, scope_sha256):
        _identifier(review_id)
        with self.condition:
            job = self.jobs.get(review_id)
            if scope_sha256 != self.scope or job is None: raise ValueError("final assessment is unavailable")
            return self._result(job)

    def _queued_package(self, job):
        _, package = _artifact(job["package"])
        protocol = self.protocols.get(job["path"], {})
        if (job["protocol_sha256"] != protocol.get("registration_sha256")
                or package["path"] != job["path"]
                or package["candidate_package_sha256"] != job["review_id"]):
            raise ValueError("queued independent protocol changed")
        _protocol_package(package, protocol)
        _assessor_protocol(self.assessors[job["path"]], protocol)
        return package

    def _run(self):
        while True:
            with self.condition:
                if self.closed: return
                pending = [j for j in self.jobs.values() if j["state"] in {"queued", "waiting"}
                    or (j["state"] == "awaiting_independent_answer_assessment"
                        and getattr(self.assessors[j["path"]], "answer_evidence_ready", lambda:False)())]
                if not pending:
                    self.condition.wait(timeout=0.5)
                    continue
            for job in pending:
                try:
                    try:
                        ready = self.task_state(job["task_id"])
                    except Exception:
                        # A readiness read has no effects and is safe to retry.
                        # Preserve answer-stage state so the isolated suite never repeats.
                        with self.condition:
                            if self.closed: return
                            if job["state"] == "queued":
                                job["state"] = "waiting"
                            job["readiness_unavailable"] = True
                            _save(self.root / (job["review_id"] + ".json"), job)
                        continue
                    with self.condition:
                        job.pop("readiness_unavailable", None)
                        if self.closed: return
                        if ready == "cancelled": job.update(state="cancelled", reason="originating_task_stopped")
                        elif ready != "ready":
                            if job["state"] != "awaiting_independent_answer_assessment":
                                job["state"] = "waiting"
                        else:
                            job["state"] = "answer_assessing" if job["state"] == "awaiting_independent_answer_assessment" else "assessing"
                        _save(self.root / (job["review_id"] + ".json"), job)
                    if ready != "ready": continue
                    package = self._queued_package(job)
                    assessor = self.assessors[job["path"]]
                    result = (assessor.complete(package, self.root / job["review_id"])
                              if job["state"] == "answer_assessing"
                              else assessor(package, self.root / job["review_id"]))
                    after_assessment = self.task_state(job["task_id"])
                    with self.condition:
                        job["public"] = result["public"]
                        if after_assessment == "cancelled":
                            job.update(state="cancelled", reason="originating_task_stopped")
                        elif result["decision"] == "reject": job["state"] = "rejected"
                        elif result["decision"] == "needs_independent_answer_assessment":
                            job["state"] = "awaiting_independent_answer_assessment"
                        else:
                            job["state"] = "publishing"
                        _save(self.root / (job["review_id"] + ".json"), job)
                    if job["state"] == "publishing":
                        # Keep status/stop available while trusted image building runs.
                        release = self.publisher(package, result, self.root / job["review_id"])
                        _, published_package = _artifact(release["package"])
                        if published_package != package:
                            raise ValueError("publisher did not prepare the exact selected package")
                        after_publication = self.task_state(job["task_id"])
                        if after_publication != "cancelled":
                            self.applications.register(job["review_id"], release)
                            self.applications.request(job["review_id"], job["request_id"], job["task_id"])
                        with self.condition:
                            job["state"] = "cancelled" if after_publication == "cancelled" else "application_requested"
                            _save(self.root / (job["review_id"] + ".json"), job)
                except Exception:
                    with self.condition:
                        job.update(state="unconfirmed", reason="final_assessment_or_publication_unconfirmed")
                        _save(self.root / (job["review_id"] + ".json"), job)
            with self.condition:
                if not self.closed: self.condition.wait(timeout=0.5)

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        self.thread.join(timeout=1)
        if not self.thread.is_alive(): self._file.close()


def configured_final_assessments(config, *, applications, runtime_config):
    from eva.operations.component_application_runtime import ComponentApplicationRuntime
    from eva.operations.component_release_publisher import ComponentReleasePublisher
    if not isinstance(config, dict) or set(config) != {
            "receipt_root", "scope_sha256", "protocols", "assessments", "publisher"}:
        raise ValueError("final assessment host configuration differs")
    runtime = ComponentApplicationRuntime(runtime_config)
    if config["scope_sha256"] != runtime.config["scope_sha256"]:
        raise ValueError("final and application source scopes differ")
    assessors = {path: HostFinalAssessment(value) for path, value in config["assessments"].items()}
    for path, assessor in assessors.items():
        if config["protocols"][path]["registration_sha256"] != assessor.config["registration"]["sha256"]:
            raise ValueError("final protocol and independent registration differ")
        _assessor_protocol(assessor, config["protocols"][path])
    return FinalComponentAssessments({key: config[key] for key in
        ("receipt_root", "scope_sha256", "protocols")}, assessors=assessors,
        publisher=ComponentReleasePublisher(config["publisher"]), applications=applications,
        task_state=runtime.ready_to_apply)
