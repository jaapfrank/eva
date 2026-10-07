"""Score fixed public development outputs outside the candidate container."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

from eva.application.component_development import validate_development_criteria
from eva.contracts.canonical_json import canonical_sha256


class DevelopmentAssessment:
    def __init__(self, config):
        if not isinstance(config, dict) or set(config) != {"cases_file", "scorer_file", "criteria"}:
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
