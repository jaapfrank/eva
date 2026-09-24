"""Call the installed isolated evaluator; models never choose its command or image."""
from dataclasses import dataclass
import hashlib
import json
import os
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

    def evaluate(self, draft):
        path = draft["path"]
        if path not in self.allowed_paths:
            raise ValueError("no installed functional evaluation suite for this component")
        payload = {"relative_path": path, "baseline_sha256": draft["source_sha256"],
                   "source": draft["candidate_content"]}
        token = Path(self.token_file).read_text().strip()
        if not token or len(token) > 1024 or any(c.isspace() for c in token):
            raise ValueError("component evaluator credential unavailable")
        request = Request(self.url + "/evaluate", json.dumps(payload, ensure_ascii=False).encode(),
                          headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                          method="POST")
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=self.timeout_seconds) as response:
            raw = response.read(160001)
        if len(raw) > 160000:
            raise ValueError("component evaluation report exceeds bound")
        report = json.loads(raw)
        if (report.get("contract") != "EvaIsolatedComponentEvaluation/v1"
                or report.get("relative_path") != path
                or report.get("baseline_sha256") != draft["source_sha256"]
                or report.get("candidate_sha256") != draft["candidate_sha256"]
                or report.get("installed") is not False
                or report.get("semantic_improvement_verified") is not False):
            raise ValueError("component evaluation report binding differs")
        binding = hashlib.sha256(json.dumps([report.get("image_id"), report.get("command_sha256")],
                                            separators=(",", ":")).encode()).hexdigest()
        if binding != self.suite_sha256:
            raise ValueError("installed evaluation suite changed")
        return report


def configured_component_evaluator():
    config_path = os.environ.get("EVA_COMPONENT_EVALUATION_CLIENT_FILE")
    if not config_path:
        return None
    data = json.loads(Path(config_path).read_text())
    if set(data) != {"url", "token_file", "allowed_paths", "suite_sha256", "timeout_seconds"}:
        raise ValueError("component evaluator configuration fields differ")
    url = urlsplit(data["url"])
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
            or url.path or url.query or url.fragment
            or not isinstance(data["allowed_paths"], list) or not data["allowed_paths"]
            or any(not isinstance(p, str) or not p.endswith(".py") for p in data["allowed_paths"])
            or type(data["timeout_seconds"]) is not int or not 1 <= data["timeout_seconds"] <= 300
            or not isinstance(data["suite_sha256"], str) or len(data["suite_sha256"]) != 64):
        raise ValueError("invalid component evaluator configuration")
    return ComponentEvaluationClient(**{**data, "allowed_paths": tuple(data["allowed_paths"])})
