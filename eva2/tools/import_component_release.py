#!/usr/bin/env python3
"""Import one reviewed component package into an existing clean release checkout.

This operator command bridges Eva's saved candidate to the ordinary Git/build/
installation flow. It never executes candidate Python, creates a branch, commits,
builds an image, or changes a running service. Review inputs are operator-provided
attestations, not proof that a model's claim of improvement is correct.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
from pathlib import Path
import subprocess

_ACTIVE_CONTEXT_PATH = "adapters/postgres/delegated_work_actions.py"
ALLOWED_PATHS = frozenset({
    "contracts/timestamps.py", "contracts/canonical_json.py", "application/cancellation.py",
    _ACTIVE_CONTEXT_PATH,
})


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_object(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result
    raw = path.read_bytes()
    if len(raw) > 1048576:
        raise ValueError("input exceeds one MiB")
    value = json.loads(raw, object_pairs_hook=unique,
                       parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    if not isinstance(value, dict):
        raise ValueError("input must be a JSON object")
    return value


def git(checkout, *args):
    return subprocess.check_output(["git", "-C", str(checkout), *args],
                                   text=True, timeout=30).strip()


def checked_evidence(review_file, entry):
    if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
        raise ValueError("review evidence needs an exact path and SHA-256")
    path = Path(entry["path"])
    if not path.is_absolute():
        path = review_file.parent / path
    raw = path.read_bytes()
    if not raw or len(raw) > 8388608 or digest(raw) != entry["sha256"]:
        raise ValueError("review evidence is absent, oversized, or changed")
    return {"path": str(path.resolve()), "sha256": digest(raw)}


def _active_context_surroundings(raw):
    """Locate only the method body; AST columns are UTF-8 byte offsets."""
    module = ast.parse(raw.decode("utf-8"))
    classes = [node for node in module.body if isinstance(node, ast.ClassDef)
               and node.name == "PostgresDelegatedWorkActions"]
    if len(classes) != 1:
        raise ValueError("active_context scope requires exactly one target class")
    methods = [node for node in classes[0].body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name == "active_context"]
    if len(methods) != 1 or not isinstance(methods[0], ast.FunctionDef):
        raise ValueError("active_context scope requires exactly one synchronous method")
    method = methods[0]
    first = method.body[0]
    lines = raw.splitlines(keepends=True)
    start = sum(map(len, lines[:first.lineno - 1])) + first.col_offset
    end = sum(map(len, lines[:method.end_lineno - 1])) + method.end_col_offset
    return raw[:start], raw[end:]


def prepare(package_file, review_file, checkout):
    package = load_object(package_file)
    package_hash = package.get("candidate_package_sha256")
    payload = {key: value for key, value in package.items() if key != "candidate_package_sha256"}
    if (package.get("contract") != "EvaComponentReleaseCandidate/v1"
            or package.get("state") != "awaiting_measured_gain_review"
            or package.get("installed") is not False
            or digest(canonical(payload)) != package_hash):
        raise ValueError("candidate package binding differs")
    path = package.get("path")
    content = package.get("candidate_content")
    if path not in ALLOWED_PATHS or not isinstance(content, str):
        raise ValueError("component is outside the configured evaluation scope")
    raw = content.encode("utf-8")
    limit = 65536 if path == _ACTIVE_CONTEXT_PATH else 32768
    if len(raw) > limit or digest(raw) != package.get("candidate_sha256"):
        raise ValueError("candidate bytes differ")
    ast.parse(content, filename=path)
    report = package["evaluation"]
    if (digest(canonical(report)) != package.get("evaluation_sha256")
            or report.get("contract") != "EvaIsolatedComponentEvaluation/v1"
            or report.get("relative_path") != path
            or report.get("baseline_sha256") != package.get("baseline_sha256")
            or report.get("candidate_sha256") != package.get("candidate_sha256")
            or report.get("installed") is not False):
        raise ValueError("evaluation binding differs")
    for name in ("baseline", "candidate"):
        result = report.get(name, {})
        if (type(result.get("exit_code")) is not int or result["exit_code"] != 0
                or any(result.get(key) is not False for key in
                       ("timed_out", "stdout_truncated", "stderr_truncated"))):
            raise ValueError("evaluation is incomplete or unsuccessful")
    review = load_object(review_file)
    if (review.get("contract") != "EvaComponentReleaseReview/v1"
            or review.get("candidate_package_sha256") != package_hash
            or review.get("decision") != "accept"
            or not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip()):
        raise ValueError("operator review does not accept this exact package")
    evidence = {name: checked_evidence(review_file, review.get(name))
                for name in ("measured_gain_evidence", "independent_review_evidence")}
    root = Path(git(checkout, "rev-parse", "--show-toplevel")).resolve()
    if root != checkout.resolve() or not git(root, "branch", "--show-current").startswith("codex/"):
        raise ValueError("select the root of a separate codex release branch checkout")
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("release checkout must be clean")
    target = root / "eva2/src/eva" / path
    if any(part.is_symlink() for part in (target, *target.parents) if part != root.parent):
        raise ValueError("release source cannot follow symbolic links")
    baseline = target.read_bytes()
    if digest(baseline) != package["baseline_sha256"] or baseline == raw:
        raise ValueError("release baseline changed or candidate has no change")
    if (path == _ACTIVE_CONTEXT_PATH
            and _active_context_surroundings(baseline) != _active_context_surroundings(raw)):
        raise ValueError("candidate changes bytes outside the active_context body")
    diff = difflib.unified_diff(baseline.decode().splitlines(keepends=True),
        content.splitlines(keepends=True), fromfile="a/" + path, tofile="b/" + path)
    patch = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in diff)
    if patch != package.get("patch"):
        raise ValueError("candidate patch differs from its exact bytes")
    receipt = {"contract": "EvaComponentSourceImport/v1", "state": "validated",
        "candidate_package_sha256": package_hash, "evaluation_sha256": package["evaluation_sha256"],
        "baseline_commit": git(root, "rev-parse", "HEAD"), "path": "eva2/src/eva/" + path,
        "baseline_sha256": digest(baseline), "candidate_sha256": digest(raw),
        "review_sha256": digest(canonical(review)), "reviewer": review["reviewer"],
        "evidence": evidence, "installed": False,
        "next": "Commit the exact source and review evidence, then use the existing managed build and installation with runtime baseline verification and rollback."}
    return target, baseline, raw, receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    target, baseline, raw, receipt = prepare(args.package, args.review, args.checkout)
    if args.apply:
        if args.receipt is None:
            parser.error("--apply requires a new --receipt path outside the release checkout")
        if args.receipt.resolve().is_relative_to(args.checkout.resolve()):
            parser.error("receipt must be outside the release checkout")
        # Reserve a new receipt before changing source; never overwrite evidence.
        with args.receipt.open("x", encoding="utf-8") as output:
            with target.open("r+b") as source:
                if source.read() != baseline:
                    raise ValueError("source changed immediately before import")
                source.seek(0)
                source.write(raw)
                source.truncate()
            receipt["state"] = "source_imported_not_installed"
            output.write(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
