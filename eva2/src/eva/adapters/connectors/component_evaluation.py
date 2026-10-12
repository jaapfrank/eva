"""Call the installed isolated evaluator; models never choose its command or image."""
from dataclasses import dataclass
import hashlib
import json
import math
import os
import re
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.parse import urlsplit


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class ComponentEvaluationClient:
    url: str
    token_file: str
    allowed_paths: tuple[str, ...]
    suite_sha256: str
    timeout_seconds: int = 120
    reviewed_releases: tuple[tuple[str, str], ...] = ()
    suite_bindings: tuple[tuple[str, str], ...] = ()
    final_assessment_scopes: tuple[tuple[str, str], ...] = ()
    saved_artifact_suite_sha256: str | None = None
    work_execution_paths: tuple[str, ...] = ()

    def __post_init__(self):
        if (not isinstance(self.work_execution_paths, tuple)
                or any(not isinstance(p, str) for p in self.work_execution_paths)
                or len(set(self.work_execution_paths)) != len(self.work_execution_paths)
                or set(self.work_execution_paths) - set(self.allowed_paths)):
            raise ValueError("invalid component work execution paths")

    def _post(self, endpoint, payload, *, limit=160000, timeout_seconds=None):
        token = Path(self.token_file).read_text().strip()
        if not token or len(token) > 1024 or any(c.isspace() for c in token):
            raise ValueError("component evaluator credential unavailable")
        request = Request(self.url + endpoint, json.dumps(payload, ensure_ascii=False).encode(),
                          headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                          method="POST")
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=self.timeout_seconds if timeout_seconds is None else timeout_seconds) as response:
            raw = response.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("component host response exceeds bound")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("component host response must be an object")
        return value

    def assess(self, package, *, request_id, task_id, scope_sha256):
        if dict(self.final_assessment_scopes).get(package["path"]) != scope_sha256:
            raise ValueError("no independent final assessment for this source")
        result = self._post("/component-assessment", {"action": "request", "package": package,
            "request_id": request_id, "task_id": task_id, "scope_sha256": scope_sha256})
        if (result.get("review_id") != package["candidate_package_sha256"]
                or result.get("request_id") != request_id or result.get("task_id") != task_id
                or result.get("independent_final") is not True or result.get("installed") is not False):
            raise ValueError("independent handoff binding differs")
        return result

    def application(self, action, *, review_id, request_id, task_id, scope_sha256=None):
        if review_id not in dict(self.reviewed_releases):
            if action != "status" or scope_sha256 not in dict(self.final_assessment_scopes).values():
                raise ValueError("component review is not configured")
            return self._post("/component-assessment", {"action": "status",
                "review_id": review_id, "scope_sha256": scope_sha256}, limit=16384)
        return self._post("/reviewed-component", {"action": action, "review_id": review_id,
            "request_id": request_id, "task_id": task_id}, limit=16384)

    def round_status(self, path, *, scope_sha256):
        if dict(self.final_assessment_scopes).get(path) != scope_sha256:
            raise ValueError("no independent final round for this source")
        result = self._post("/component-assessment", {"action": "round_status",
            "path": path, "scope_sha256": scope_sha256}, limit=16384)
        fields = {"contract", "path", "scope_sha256", "registration_sha256", "baseline_sha256",
                  "source_target_sha256", "state", "independent_final", "installed"}
        if (set(result) != fields or result.get("contract") != "EvaComponentImprovementRound/v1"
                or result.get("path") != path or result.get("scope_sha256") != scope_sha256
                or result.get("state") not in {"available", "consumed"}
                or result.get("independent_final") is not True or result.get("installed") is not False
                or any(not isinstance(result.get(k), str) or not re.fullmatch(r"[0-9a-f]{64}", result[k])
                       for k in ("registration_sha256", "baseline_sha256"))
                or (result.get("source_target_sha256") is not None and (
                    not isinstance(result["source_target_sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", result["source_target_sha256"])))):
            raise ValueError("independent round metadata binding differs")
        return result

    def evaluate(self, draft, *, execution_binding=None, remaining_seconds=None):
        path = draft["path"]
        target = draft.get("source_target")
        if target is None:
            if draft.get("baseline_content") is not None:
                raise ValueError("saved artifact baseline has no source target")
            if path not in self.allowed_paths:
                raise ValueError("no installed functional evaluation suite for this component")
            payload = {"relative_path": path, "baseline_sha256": draft["source_sha256"],
                       "source": draft["candidate_content"]}
            suite = dict(self.suite_bindings).get(path, self.suite_sha256)
        else:
            from eva.operations.component_evaluation import CandidateRequest
            if (not isinstance(self.saved_artifact_suite_sha256, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", self.saved_artifact_suite_sha256)):
                raise ValueError("no installed saved artifact evaluation suite")
            request = CandidateRequest(path, draft["source_sha256"], draft["candidate_content"],
                draft.get("baseline_content"), target)
            if hashlib.sha256(request.source.encode("utf-8")).hexdigest() != draft["candidate_sha256"]:
                raise ValueError("saved artifact candidate source hash differs")
            target = request.source_target
            payload = {"relative_path": path, "baseline_sha256": request.baseline_sha256,
                "source": request.source, "baseline_source": request.baseline_source,
                "source_target": target}
            suite = self.saved_artifact_suite_sha256
        work_execution = target is None and path in self.work_execution_paths
        if work_execution:
            fields = {"contract", "task_id", "draft_step", "evaluation_step",
                      "evaluation_call_id", "execution_binding_ref"}
            if (not isinstance(execution_binding, dict) or set(execution_binding) != fields
                    or execution_binding.get("contract") != "EvaScopedComponentEvaluationBinding/v1"
                    or type(execution_binding.get("draft_step")) is not int
                    or type(execution_binding.get("evaluation_step")) is not int
                    or not 0 <= execution_binding["draft_step"] < execution_binding["evaluation_step"]
                    or any(not isinstance(execution_binding.get(k), str)
                           or not 1 <= len(execution_binding[k]) <= 256
                           or any(c.isspace() or ord(c) < 32 for c in execution_binding[k])
                           for k in ("task_id", "evaluation_call_id"))
                    or not isinstance(execution_binding.get("execution_binding_ref"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", execution_binding["execution_binding_ref"])):
                raise ValueError("scoped component execution binding required")
            if hashlib.sha256(draft["candidate_content"].encode("utf-8")).hexdigest() != draft["candidate_sha256"]:
                raise ValueError("component work candidate source hash differs")
            payload["execution_binding"] = dict(execution_binding)
        elif execution_binding is not None:
            raise ValueError("component work execution path is not configured")
        if remaining_seconds is not None:
            if (not work_execution or type(remaining_seconds) not in (int, float)
                    or not math.isfinite(remaining_seconds) or remaining_seconds <= 0):
                raise ValueError("original component work time is unavailable")
            report = self._post("/evaluate", payload, timeout_seconds=remaining_seconds)
        else:
            report = self._post("/evaluate", payload)
        if (report.get("contract") != "EvaIsolatedComponentEvaluation/v1"
                or report.get("relative_path") != path
                or report.get("baseline_sha256") != draft["source_sha256"]
                or report.get("candidate_sha256") != draft["candidate_sha256"]
                or report.get("source_target") != target
                or report.get("installed") is not False
                or report.get("semantic_improvement_verified") is not False
                or (work_execution and report.get("execution_binding_ref") != execution_binding["execution_binding_ref"])):
            raise ValueError("component evaluation report binding differs")
        basis = [report.get("image_id"), report.get("command_sha256")]
        if "development_assessment" in report:
            basis.append(report["development_assessment"].get("assessment_sha256"))
        if target is not None:
            from eva.contracts.canonical_json import canonical_sha256
            basis.append(canonical_sha256(target))
        binding = hashlib.sha256(json.dumps(basis, separators=(",", ":")).encode()).hexdigest()
        if binding != suite:
            raise ValueError("installed evaluation suite changed")
        return report


def configured_component_evaluator():
    config_path = os.environ.get("EVA_COMPONENT_EVALUATION_CLIENT_FILE")
    if not config_path:
        return None
    data = json.loads(Path(config_path).read_text())
    required = {"url", "token_file", "allowed_paths", "suite_sha256", "timeout_seconds"}
    if not required <= set(data) or set(data) - required - {"reviewed_releases", "suite_bindings", "final_assessment_scopes", "saved_artifact_suite_sha256", "work_execution_paths"}:
        raise ValueError("component evaluator configuration fields differ")
    url = urlsplit(data["url"])
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
            or url.path or url.query or url.fragment
            or not isinstance(data["allowed_paths"], list) or not data["allowed_paths"]
            or any(not isinstance(p, str) or not p.endswith(".py") for p in data["allowed_paths"])
            or type(data["timeout_seconds"]) is not int or not 1 <= data["timeout_seconds"] <= 300
            or not isinstance(data["suite_sha256"], str) or len(data["suite_sha256"]) != 64):
        raise ValueError("invalid component evaluator configuration")
    work_paths = data.get("work_execution_paths", [])
    if (not isinstance(work_paths, list) or any(not isinstance(p, str) for p in work_paths)
            or len(set(work_paths)) != len(work_paths)
            or set(work_paths) - set(data["allowed_paths"])):
        raise ValueError("invalid component work execution paths")
    saved_suite = data.get("saved_artifact_suite_sha256")
    if saved_suite is not None and (not isinstance(saved_suite, str)
                                    or not re.fullmatch(r"[0-9a-f]{64}", saved_suite)):
        raise ValueError("invalid saved artifact evaluation suite binding")
    reviews = data.get("reviewed_releases", {})
    if (not isinstance(reviews, dict) or len(reviews) > 32
            or any(not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key)
                   or not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                   for key, value in reviews.items())):
        raise ValueError("invalid reviewed component scopes")
    suites = data.get("suite_bindings", {})
    if (not isinstance(suites, dict) or set(suites) - set(data["allowed_paths"])
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                   for value in suites.values())):
        raise ValueError("invalid component development suite bindings")
    final = data.get("final_assessment_scopes", {})
    if (not isinstance(final, dict) or set(final) - set(data["allowed_paths"])
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                   for value in final.values())):
        raise ValueError("invalid independent final assessment scopes")
    return ComponentEvaluationClient(**{**data, "allowed_paths": tuple(data["allowed_paths"]),
                                        "work_execution_paths": tuple(work_paths),
                                        "reviewed_releases": tuple(sorted(reviews.items())),
                                        "suite_bindings": tuple(sorted(suites.items())),
                                        "final_assessment_scopes": tuple(sorted(final.items()))})
