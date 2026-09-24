"""Read packaged implementation and draft a patch without changing running code."""
from pathlib import Path
import difflib
import hashlib
import re


def component_source(root, relative):
    if not isinstance(relative, str) or (relative and (
            not re.fullmatch(r"[A-Za-z0-9_.\-/]{1,240}", relative)
            or any(part in {"", ".", ".."} for part in relative.split("/")))):
        raise ValueError("component path must be relative to the installed Eva package")
    target = root / relative
    for part in [target, *target.parents]:
        if part == root:
            break
        if part.is_symlink():
            raise ValueError("component source cannot follow symbolic links")
    target = target.resolve(strict=True)
    if not target.is_relative_to(root.resolve(strict=True)):
        raise ValueError("component source escapes installed package")
    return target


def read_component(root, path, start_line=1):
    target = component_source(root, path)
    if type(start_line) is not int or start_line < 1:
        raise ValueError("source line must be positive")
    if target.is_dir():
        children = sorted(p for p in target.iterdir() if not p.is_symlink()
                          and p.name != "__pycache__" and (p.is_dir() or p.suffix == ".py"))
        return {"kind": "directory", "path": path,
                "entries": [{"path": p.relative_to(root).as_posix(),
                             "kind": "directory" if p.is_dir() else "python"} for p in children[:100]],
                "truncated": len(children) > 100}
    if target.suffix != ".py" or target.stat().st_size > 1_048_576:
        raise ValueError("component source must be a bounded Python file")
    raw = target.read_bytes()
    lines = raw.decode("utf-8").splitlines(keepends=True)
    selected = []
    length = 0
    for line in lines[start_line - 1:start_line + 159]:
        if length + len(line.encode()) > 16000:
            if not selected:
                raise ValueError("one source line exceeds the read bound")
            break
        selected.append(line)
        length += len(line.encode())
    return {"kind": "python", "path": path, "source_sha256": hashlib.sha256(raw).hexdigest(),
            "start_line": start_line, "total_lines": len(lines), "content": "".join(selected),
            "next_line": start_line + len(selected) if start_line + len(selected) <= len(lines) else None}


def draft_component(root, arguments):
    target = component_source(root, arguments["path"])
    if target.suffix != ".py" or not target.is_file() or target.stat().st_size > 32768:
        raise ValueError("drafts support Python components up to 32 KiB")
    raw = target.read_bytes()
    before = hashlib.sha256(raw).hexdigest()
    if before != arguments["source_sha256"]:
        raise ValueError("installed component changed since it was read")
    source = raw.decode("utf-8")
    old, new = arguments["old_text"], arguments["new_text"]
    if not old or old == new or source.count(old) != 1:
        raise ValueError("component patch requires one exact distinct replacement")
    candidate = source.replace(old, new, 1)
    if len(candidate.encode()) > 32768:
        raise ValueError("candidate exceeds 32 KiB")
    diff = difflib.unified_diff(source.splitlines(keepends=True),
        candidate.splitlines(keepends=True), fromfile="a/" + arguments["path"],
        tofile="b/" + arguments["path"])
    patch = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in diff)
    return {"contract": "EvaComponentImprovementDraft/v1", "path": arguments["path"],
            "source_sha256": before, "candidate_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
            "patch": patch, "candidate_content": candidate,
            "problem": arguments["problem"], "expected_gain": arguments["expected_gain"],
            "verification_plan": arguments["verification_plan"],
            "installed": False, "checks_executed": False, "measured_gain": None,
            "guidance": "Bewaar patch, kandidaat en meetplan met de bestaande documenttool. "
                        "Dit voorstel is niet uitgevoerd of geïnstalleerd; verwachte winst is geen meting."}


def prepare_component_release(draft, report):
    """Bind a reviewable installation input; process success is not measured gain."""
    from eva.contracts.canonical_json import canonical_sha256
    if (draft.get("contract") != "EvaComponentImprovementDraft/v1"
            or report.get("contract") != "EvaIsolatedComponentEvaluation/v1"
            or report.get("relative_path") != draft.get("path")
            or report.get("baseline_sha256") != draft.get("source_sha256")
            or report.get("candidate_sha256") != draft.get("candidate_sha256")
            or hashlib.sha256(draft["candidate_content"].encode()).hexdigest() != draft["candidate_sha256"]
            or report.get("installed") is not False
            or report.get("semantic_improvement_verified") is not False):
        raise ValueError("component release source and measurement bindings differ")
    for name in ("baseline", "candidate"):
        outcome = report.get(name, {})
        if (type(outcome.get("exit_code")) is not int or outcome["exit_code"] != 0
                or outcome.get("timed_out") is not False
                or outcome.get("stdout_truncated") is not False
                or outcome.get("stderr_truncated") is not False):
            raise ValueError("component release requires complete successful comparison output")
    payload = {"contract": "EvaComponentReleaseCandidate/v1",
        "path": draft["path"], "baseline_sha256": draft["source_sha256"],
        "candidate_sha256": draft["candidate_sha256"],
        "candidate_content": draft["candidate_content"], "patch": draft["patch"],
        "problem": draft["problem"], "expected_gain": draft["expected_gain"],
        "verification_plan": draft["verification_plan"],
        "evaluation": report, "evaluation_sha256": canonical_sha256(report),
        "state": "awaiting_measured_gain_review", "installed": False,
        "required_before_installation": ["independent evidence of the stated benefit",
            "review of this exact candidate and evaluation",
            "unchanged production baseline and a recorded rollback",
            "installation through the existing managed release process"],
        "guidance": "Bewaar dit exacte pakket met de documenttool. Verwachte winst is geen meting; "
            "een nul-exitcode is geen installatiebesluit. Noem de resterende beoordeling expliciet."}
    return {**payload, "candidate_package_sha256": canonical_sha256(payload)}
