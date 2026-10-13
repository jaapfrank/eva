"""Score fixed public development outputs outside the candidate container."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat

from eva.application.component_development import validate_development_criteria
from eva.contracts.canonical_json import canonical_sha256


class DevelopmentAssessment:
    def __init__(self, config):
        if (not isinstance(config, dict) or set(config) not in (
                {"cases_file", "scorer_file", "criteria"},
                {"cases_file", "scorer_file", "criteria", "work_evidence"})):
            raise ValueError("invalid development assessment configuration")
        self.paths = {}
        for key in ("cases_file", "scorer_file"):
            path = Path(config[key])
            if not path.is_absolute() or path.resolve() != path or not path.is_file():
                raise ValueError("fixed development assessment file required")
            raw = path.read_bytes()
            if len(raw) > 1_048_576:
                raise ValueError("development assessment file exceeds bound")
            self.paths[key] = (path, hashlib.sha256(raw).hexdigest())
        self.criteria = deepcopy(validate_development_criteria(config["criteria"]))
        self.cases = json.loads(self.paths["cases_file"][0].read_bytes())
        if not isinstance(self.cases, list) or not self.cases:
            raise ValueError("fixed public development cases required")
        self.assessment_sha256 = canonical_sha256({
            "criteria": self.criteria,
            "files": {key: digest for key, (_, digest) in self.paths.items()}})
        self.work_evidence = None
        if "work_evidence" in config:
            self.work_evidence = _WorkEvidence(config["work_evidence"], self.cases,
                                              self.paths["cases_file"][1])
            self.assessment_sha256 = canonical_sha256({
                "static_assessment_sha256": self.assessment_sha256,
                # Admission/runtime identities are checked against every sealed run.
                # They cannot identify this suite: the model profile itself includes
                # the client suite binding. Reference bytes are pinned separately.
                "work_evidence": {key: value for key, value in
                    self.work_evidence.config.items() if key != "runtime_bindings"}})
        spec = importlib.util.spec_from_file_location("eva_fixed_development_scorer",
                                                     self.paths["scorer_file"][0])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.scorer = module.score

    def assess(self, report):
        for path, digest in self.paths.values():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("fixed development assessment changed")
        result = {"contract": "EvaComponentDevelopmentAssessment/v1",
            "assessment_sha256": self.assessment_sha256,
            "criteria": deepcopy(self.criteria)}
        if self.work_evidence is not None:
            return self.work_evidence.assess(report, result, self.scorer, self.cases)
        for name in ("baseline", "candidate"):
            outcome = report[name]
            try:
                if (outcome["exit_code"] != 0 or outcome["timed_out"]
                        or outcome["stdout_truncated"] or outcome["stderr_truncated"]):
                    raise ValueError("incomplete or failed candidate execution")
                outputs = json.loads(outcome["stdout"])
                metrics = self.scorer(deepcopy(self.cases), outputs)
                if not isinstance(metrics, dict):
                    raise ValueError("development scorer must return an object")
                # All metrics are computed by this fixed host scorer, never accepted from the model.
                result[name] = {"complete": True, "metrics": metrics, "error": None}
            except (ValueError, KeyError, TypeError) as error:
                result[name] = {"complete": False, "metrics": None, "error": type(error).__name__}
        return result


class _WorkEvidenceError(ValueError):
    """Safe failure label, never private work contents or candidate error text."""


def _require(condition, label):
    if not condition:
        raise _WorkEvidenceError(label)


def _fields(value, fields):
    _require(isinstance(value, dict) and set(value) == set(fields), "extra_or_missing_fields")


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _count(value):
    return type(value) is int and value >= 0


def _identity(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 256


def _export_matches(value, expected, label):
    try:
        matches = _sha(expected) and canonical_sha256(value) == expected
    except (ValueError, TypeError, RecursionError):
        matches = False
    _require(matches, label)


_RUNTIME = {"engine_release_sha256", "worker_profile_release_sha256",
    "model_profile_release_sha256", "output_schema_sha256", "tool_configuration_sha256"}
_BINDING = {"stage", "registration_sha256", "case_set_sha256", "source_sha256",
    "baseline_sha256", "strategy_sha256", "scope_sha256"} | _RUNTIME
_RUN = {"case_id", "task_id", "session_id", "source_ingress_sha256", "status",
    "run_receipt_sha256", "usage", "help", "hard_conditions", "artifact_evidence"}
_EXPECTED_RUN = {"case_id", "task_id", "session_id", "source_ingress_sha256",
    "run_receipt_sha256", "usage_sha256", "help_sha256", "hard_conditions_sha256",
    "artifact_evidence_sha256"}
_USAGE = {"measurement", "provider_attempts", "input_tokens", "output_tokens",
    "total_tokens", "unknown_attempts", "request_ids", "roles"}
_HARD = {"source_scope_valid", "authorization_current", "stop_respected", "no_forbidden_effects"}
_MAX_WORK_BYTES = 8 * 1024 * 1024


class _WorkEvidence:
    """Validate sealed host observations; never dispatch work or adopt a late baseline."""

    def __init__(self, config, cases, case_set_sha256):
        _fields(config, {"evidence_root", "run_bindings_root", "registration_sha256",
            "case_set_sha256", "baseline_sha256", "scope_sha256", "runtime_bindings", "limits"})
        self.config = deepcopy(config)
        for key in ("registration_sha256", "case_set_sha256", "baseline_sha256", "scope_sha256"):
            _require(_sha(config[key]), "registration_case_or_source_mismatch")
        _require(config["case_set_sha256"] == case_set_sha256, "registration_case_or_source_mismatch")
        _fields(config["runtime_bindings"], _RUNTIME)
        _require(all(_sha(v) for v in config["runtime_bindings"].values()), "strategy_or_runtime_binding_mismatch")
        _fields(config["limits"], {"per_case", "total_provider_attempts", "total_input_tokens", "total_output_tokens"})
        _fields(config["limits"]["per_case"], {"contact_attempts", "worker_and_helper_attempts", "input_tokens", "output_tokens"})
        values = list(config["limits"]["per_case"].values()) + [v for k, v in config["limits"].items() if k != "per_case"]
        _require(all(_count(v) for v in values), "budget_exceeded")
        self.case_ids = []
        for case in cases:
            _require(isinstance(case, dict) and _identity(case.get("case_id")), "registration_case_or_source_mismatch")
            self.case_ids.append(case["case_id"])
        _require(len(set(self.case_ids)) == len(self.case_ids), "registration_case_or_source_mismatch")
        self.roots = {}
        for key in ("evidence_root", "run_bindings_root"):
            _require(isinstance(config[key], str), "unsafe_owner_mode_or_symlink")
            root = Path(config[key])
            self._root(root)
            self.roots[key] = root
        # Store only exact hashes, not cached work. Missing baselines require fresh
        # composition after the producer has sealed both reference files.
        self.baseline_reference = {}
        self.baseline_error = None
        for key in self.roots:
            try:
                raw = self._read(key, config["baseline_sha256"])
                self.baseline_reference[key] = hashlib.sha256(raw).hexdigest()
            except _WorkEvidenceError as error:
                self.baseline_reference[key] = None
                self.baseline_error = str(error)

    @staticmethod
    def _root(root):
        try:
            _require(root.is_absolute() and root.resolve() == root, "unsafe_owner_mode_or_symlink")
            for part in (root, *root.parents):
                _require(not stat.S_ISLNK(part.lstat().st_mode), "unsafe_owner_mode_or_symlink")
            info = root.stat()
            _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                and stat.S_IMODE(info.st_mode) == 0o700, "unsafe_owner_mode_or_symlink")
        except OSError:
            raise _WorkEvidenceError("unsafe_owner_mode_or_symlink") from None

    def _read(self, key, source_sha256):
        root = self.roots[key]
        self._root(root)
        try:
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                info = os.fstat(directory)
                _require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
                         "unsafe_owner_mode_or_symlink")
                descriptor = os.open(source_sha256 + ".json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            finally:
                os.close(directory)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                    and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) in {0o400, 0o600},
                    "unsafe_owner_mode_or_symlink")
                _require(info.st_size <= _MAX_WORK_BYTES, "oversized_or_duplicate_json")
                raw = stream.read(_MAX_WORK_BYTES + 1)
                after = os.fstat(stream.fileno())
                _require(len(raw) <= _MAX_WORK_BYTES and info.st_size == len(raw)
                    and info.st_mtime_ns == after.st_mtime_ns, "oversized_or_duplicate_json")
                return raw
        except FileNotFoundError:
            raise _WorkEvidenceError("evidence_missing") from None
        except OSError:
            raise _WorkEvidenceError("unsafe_owner_mode_or_symlink") from None

    @staticmethod
    def _decode(raw):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                _require(key not in result, "oversized_or_duplicate_json")
                result[key] = value
            return result
        def invalid_constant(value):
            raise _WorkEvidenceError("oversized_or_duplicate_json")
        try:
            return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise _WorkEvidenceError("oversized_or_duplicate_json") from None

    def _binding(self, binding, source):
        _fields(binding, _BINDING)
        _require(binding["stage"] == "development"
            and all(_sha(v) for k, v in binding.items() if k != "stage"), "registration_case_or_source_mismatch")
        expected = {k: self.config[k] for k in ("registration_sha256", "case_set_sha256", "baseline_sha256", "scope_sha256")}
        expected.update(self.config["runtime_bindings"])
        expected["source_sha256"] = source
        for key, value in expected.items():
            label = ("scope_or_ingress_mismatch" if key == "scope_sha256" else
                "strategy_or_runtime_binding_mismatch" if key in _RUNTIME else "registration_case_or_source_mismatch")
            _require(binding[key] == value, label)

    def _usage(self, usage, seen):
        _fields(usage, _USAGE)
        _require(usage["measurement"] == "measured" and usage["unknown_attempts"] == 0
            and type(usage["unknown_attempts"]) is int, "unmeasured_or_unknown_usage")
        _require(all(_count(usage[k]) for k in ("provider_attempts", "input_tokens", "output_tokens", "total_tokens")),
                 "usage_arithmetic_or_request_identity_mismatch")
        ids = usage["request_ids"]
        _require(isinstance(ids, list) and all(_identity(v) for v in ids)
            and usage["provider_attempts"] == len(ids) and len(set(ids)) == len(ids)
            and not (set(ids) & seen["requests"])
            and usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"],
            "usage_arithmetic_or_request_identity_mismatch")
        seen["requests"].update(ids)
        roles = usage["roles"]
        _require(isinstance(roles, list), "role_or_profile_mismatch")
        admitted = {("contact", "gpt-6-sol", "none"), ("work", "gpt-6-astra", "medium"), ("helper", "gpt-6-astra", "medium")}
        counts = {"contact": 0, "work": 0, "helper": 0}
        role_seen = set()
        for role in roles:
            _fields(role, {"role", "model", "reasoning_effort", "attempts"})
            key = (role["role"], role["model"], role["reasoning_effort"])
            _require(all(isinstance(v, str) for v in key) and key in admitted
                and key not in role_seen and _count(role["attempts"]), "role_or_profile_mismatch")
            role_seen.add(key)
            counts[role["role"]] += role["attempts"]
        _require(sum(counts.values()) == usage["provider_attempts"], "usage_arithmetic_or_request_identity_mismatch")
        limits = self.config["limits"]["per_case"]
        _require(counts["contact"] <= limits["contact_attempts"]
            and counts["work"] + counts["helper"] <= limits["worker_and_helper_attempts"]
            and usage["input_tokens"] <= limits["input_tokens"]
            and usage["output_tokens"] <= limits["output_tokens"], "budget_exceeded")

    def _evidence(self, source, *, baseline, seen):
        _require(_sha(source), "registration_case_or_source_mismatch")
        if baseline:
            _require(self.baseline_error is None, self.baseline_error or "evidence_missing")
        raw = {key: self._read(key, source) for key in self.roots}
        if baseline:
            _require(all(hashlib.sha256(v).hexdigest() == self.baseline_reference[k] for k, v in raw.items()),
                     "missing_receipt_or_export_hash_mismatch")
        evidence = self._decode(raw["evidence_root"])
        trusted = self._decode(raw["run_bindings_root"])
        for value, contract in ((evidence, "EvaWorkerStrategyWorkEvidence/v1"), (trusted, "EvaWorkerStrategyWorkRunBindings/v1")):
            _fields(value, {"contract", "binding", "runs"})
            _require(value["contract"] == contract, "extra_or_missing_fields")
        _fields(evidence["binding"], _BINDING | {"run_bindings_sha256"})
        binding = {k: v for k, v in evidence["binding"].items() if k != "run_bindings_sha256"}
        self._binding(binding, source)
        self._binding(trusted["binding"], source)
        _require(binding == trusted["binding"], "strategy_or_runtime_binding_mismatch")
        _require(evidence["binding"]["run_bindings_sha256"] == hashlib.sha256(raw["run_bindings_root"]).hexdigest(),
                 "missing_receipt_or_export_hash_mismatch")
        expected = {}
        _require(isinstance(trusted["runs"], list), "foreign_duplicate_or_replayed_run")
        for run in trusted["runs"]:
            _fields(run, _EXPECTED_RUN)
            _require(_identity(run["case_id"]) and run["case_id"] not in expected, "foreign_duplicate_or_replayed_run")
            expected[run["case_id"]] = run
        _require(set(expected) == set(self.case_ids), "foreign_duplicate_or_replayed_run")
        _require(isinstance(evidence["runs"], list) and len(evidence["runs"]) == len(expected), "foreign_duplicate_or_replayed_run")
        cases_seen = set()
        totals = {"provider_attempts": 0, "input_tokens": 0, "output_tokens": 0}
        for run in evidence["runs"]:
            _fields(run, _RUN)
            case = run["case_id"]
            _require(_identity(case) and case in expected and case not in cases_seen, "foreign_duplicate_or_replayed_run")
            cases_seen.add(case)
            host = expected[case]
            for key, group in (("task_id", "tasks"), ("session_id", "sessions")):
                _require(_identity(run[key]) and run[key] == host[key] and run[key] not in seen[group],
                         "foreign_duplicate_or_replayed_run")
                seen[group].add(run[key])
            _require(_sha(run["source_ingress_sha256"]) and run["source_ingress_sha256"] == host["source_ingress_sha256"],
                     "scope_or_ingress_mismatch")
            _require(_sha(run["run_receipt_sha256"]) and run["run_receipt_sha256"] == host["run_receipt_sha256"],
                     "missing_receipt_or_export_hash_mismatch")
            _require(run["run_receipt_sha256"] not in seen["receipts"], "foreign_duplicate_or_replayed_run")
            seen["receipts"].add(run["run_receipt_sha256"])
            _require(run["status"] in {"succeeded", "failed", "stopped", "ambiguous", "timed_out"},
                     "missing_receipt_or_export_hash_mismatch")
            self._usage(run["usage"], seen)
            _export_matches(run["usage"], host["usage_sha256"], "missing_receipt_or_export_hash_mismatch")
            _fields(run["help"], {"requests", "interventions"})
            _require(all(_count(v) for v in run["help"].values()), "hidden_human_help")
            _export_matches(run["help"], host["help_sha256"], "hidden_human_help")
            _fields(run["hard_conditions"], _HARD)
            _require(all(v is True for v in run["hard_conditions"].values()), "hard_condition_false_or_unknown")
            _export_matches(run["hard_conditions"], host["hard_conditions_sha256"], "hard_condition_false_or_unknown")
            _require(isinstance(run["artifact_evidence"], dict), "artifact_unverified")
            _export_matches(run["artifact_evidence"], host["artifact_evidence_sha256"], "artifact_unverified")
            for key in totals:
                totals[key] += run["usage"][key]
        limits = self.config["limits"]
        for key, value in totals.items():
            seen["totals"][key] += value
        _require(all(v <= limits["total_" + k] for k, v in seen["totals"].items()), "budget_exceeded")
        return evidence

    def assess(self, report, result, scorer, cases):
        seen = {key: set() for key in ("requests", "tasks", "sessions", "receipts")}
        seen["totals"] = {"provider_attempts": 0, "input_tokens": 0, "output_tokens": 0}
        baseline_valid = False
        for name in ("baseline", "candidate"):
            try:
                outcome = report[name]
                _require(isinstance(outcome, dict) and type(outcome.get("exit_code")) is int
                    and outcome["exit_code"] == 0 and all(outcome.get(k) is False
                    for k in ("timed_out", "stdout_truncated", "stderr_truncated")), "incomplete_static_report")
                _require(report.get("contract") == "EvaIsolatedComponentEvaluation/v1"
                    and report.get("relative_path") == "bootstrap/delegated_digital_runner.py"
                    and report.get("installed") is False and report.get("semantic_improvement_verified") is False
                    and "source_target" not in report and report.get("baseline_sha256") == self.config["baseline_sha256"],
                    "registration_case_or_source_mismatch")
                if name == "candidate":
                    _require(baseline_valid, "evidence_missing")
                source = report["baseline_sha256"] if name == "baseline" else report["candidate_sha256"]
                outputs = self._evidence(source, baseline=name == "baseline", seen=seen)
                metrics = scorer(deepcopy(cases), deepcopy(outputs))
                _require(isinstance(metrics, dict), "extra_or_missing_fields")
                result[name] = {"complete": True, "metrics": metrics, "error": None}
                if name == "baseline":
                    baseline_valid = True
            except _WorkEvidenceError as error:
                result[name] = {"complete": False, "metrics": None, "error": str(error)}
            except (ValueError, KeyError, TypeError):
                result[name] = {"complete": False, "metrics": None, "error": "extra_or_missing_fields"}
        return result
