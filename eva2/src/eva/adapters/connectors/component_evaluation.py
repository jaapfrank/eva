"""Call the installed isolated evaluator; models never choose its command or image."""
from dataclasses import dataclass
import hashlib
import json
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

    def _post(self, endpoint, payload, *, limit=160000):
        token = Path(self.token_file).read_text().strip()
        if not token or len(token) > 1024 or any(c.isspace() for c in token):
            raise ValueError("component evaluator credential unavailable")
        request = Request(self.url + endpoint, json.dumps(payload, ensure_ascii=False).encode(),
                          headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                          method="POST")
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=self.timeout_seconds) as response:
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

    def evaluate(self, draft):
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
        report = self._post("/evaluate", payload)
        if (report.get("contract") != "EvaIsolatedComponentEvaluation/v1"
                or report.get("relative_path") != path
                or report.get("baseline_sha256") != draft["source_sha256"]
                or report.get("candidate_sha256") != draft["candidate_sha256"]
                or report.get("source_target") != target
                or report.get("installed") is not False
                or report.get("semantic_improvement_verified") is not False):
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
    if not required <= set(data) or set(data) - required - {"reviewed_releases", "suite_bindings", "final_assessment_scopes", "saved_artifact_suite_sha256"}:
        raise ValueError("component evaluator configuration fields differ")
    url = urlsplit(data["url"])
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
            or url.path or url.query or url.fragment
            or not isinstance(data["allowed_paths"], list) or not data["allowed_paths"]
            or any(not isinstance(p, str) or not p.endswith(".py") for p in data["allowed_paths"])
            or type(data["timeout_seconds"]) is not int or not 1 <= data["timeout_seconds"] <= 300
            or not isinstance(data["suite_sha256"], str) or len(data["suite_sha256"]) != 64):
        raise ValueError("invalid component evaluator configuration")
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
                                        "reviewed_releases": tuple(sorted(reviews.items())),
                                        "suite_bindings": tuple(sorted(suites.items())),
                                        "final_assessment_scopes": tuple(sorted(final.items()))})
