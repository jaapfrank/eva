"""Host-configured client for isolated, verified reusable Python artifacts."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler
from urllib.parse import urlsplit
from eva.adapters.connectors.component_evaluation import _NoRedirect


def encoded_input(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


@dataclass(frozen=True)
class ProcedureExecutionClient:
    url: str
    token_file: str
    image_id: str
    timeout_seconds: int = 60

    def execute(self, source, input_data):
        source_bytes = source.encode("utf-8")
        input_bytes = encoded_input(input_data)
        if not 1 <= len(source_bytes) <= 16384 or len(input_bytes) > 32768:
            raise ValueError("procedure source or input exceeds bound")
        token = Path(self.token_file).read_text().strip()
        if not 32 <= len(token) <= 256 or any(c.isspace() for c in token):
            raise ValueError("procedure execution credential unavailable")
        request = Request(self.url + "/run-procedure",
            json.dumps({"source": source, "input_data": input_data}, ensure_ascii=False,
                       allow_nan=False).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            method="POST")
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request,
                timeout=self.timeout_seconds) as response:
            raw = response.read(100001)
        if len(raw) > 100000:
            raise ValueError("procedure execution report exceeds bound")
        report = json.loads(raw)
        if (report.get("contract") != "EvaIsolatedProcedureExecution/v1"
                or report.get("image_id") != self.image_id
                or report.get("source_sha256") != hashlib.sha256(source_bytes).hexdigest()
                or report.get("input_sha256") != hashlib.sha256(input_bytes).hexdigest()):
            raise ValueError("procedure execution report binding differs")
        return report


def configured_procedure_executor():
    path = os.environ.get("EVA_PROCEDURE_EXECUTION_CLIENT_FILE")
    if not path:
        return None
    data = json.loads(Path(path).read_text())
    if set(data) != {"url", "token_file", "image_id", "timeout_seconds"}:
        raise ValueError("procedure executor configuration fields differ")
    url = urlsplit(data["url"])
    import re
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
            or url.path or url.query or url.fragment
            or not isinstance(data["token_file"], str) or not Path(data["token_file"]).is_absolute()
            or not isinstance(data["image_id"], str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", data["image_id"])
            or type(data["timeout_seconds"]) is not int or not 1 <= data["timeout_seconds"] <= 90):
        raise ValueError("invalid procedure executor configuration")
    return ProcedureExecutionClient(**data)
