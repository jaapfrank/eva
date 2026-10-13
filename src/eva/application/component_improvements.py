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
    return read_component_content(path, raw, start_line=start_line)


def read_component_content(path, raw, *, start_line=1):
    """Read a host-resolved source; the caller owns artifact authority."""
    if type(start_line) is not int or start_line < 1:
        raise ValueError("source line must be positive")
    if not isinstance(raw, bytes) or len(raw) > 1_048_576:
        raise ValueError("component source must be bounded bytes")
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


def draft_component(root, arguments, *, parent=None):
    target = component_source(root, arguments["path"])
    if target.suffix != ".py" or not target.is_file() or target.stat().st_size > 65536:
        raise ValueError("drafts support Python components up to 64 KiB")
    return draft_component_content(arguments, target.read_bytes(), parent=parent)


def draft_component_content(arguments, raw, *, parent=None, source_target=None):
    """Draft against exact host-resolved bytes using the same lineage rules."""
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 65536:
        raise ValueError("drafts support Python components up to 64 KiB")
    before = hashlib.sha256(raw).hexdigest()
    installed_source = raw.decode("utf-8")
    source = installed_source
    lineage = None
    if parent is None:
        if before != arguments["source_sha256"]:
            raise ValueError("installed component changed since it was read")
    else:
        parent_source = parent.get("candidate_content")
        ancestors = parent.get("candidate_lineage", {}).get("ancestor_candidate_sha256s", [])
        if (parent.get("contract") != "EvaComponentImprovementDraft/v1"
                or parent.get("path") != arguments["path"]
                or parent.get("source_sha256") != before
                or not isinstance(parent_source, str)
                or len(parent_source.encode("utf-8")) > 65536
                or hashlib.sha256(parent_source.encode("utf-8")).hexdigest() != parent.get("candidate_sha256")
                or not isinstance(ancestors, list)
                or any(not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item) for item in ancestors)):
            raise ValueError("parent candidate source binding differs")
        if arguments["source_sha256"] != parent["candidate_sha256"]:
            raise ValueError("parent candidate changed since it was selected")
        source = parent_source
        lineage = {"parent_candidate_sha256": parent["candidate_sha256"],
                   "ancestor_candidate_sha256s": [*ancestors, parent["candidate_sha256"]]}
    if parent is not None and parent.get("source_target") != source_target:
        raise ValueError("parent artifact target binding differs")
    old, new = arguments["old_text"], arguments["new_text"]
    if not old or old == new or source.count(old) != 1:
        raise ValueError("component patch requires one exact distinct replacement")
    candidate = source.replace(old, new, 1)
    if len(candidate.encode()) > 65536:
        raise ValueError("candidate exceeds 64 KiB")
    if candidate == installed_source:
        raise ValueError("candidate does not change the installed component")
    # The release diff always includes all changes from the installed reference.
    diff = difflib.unified_diff(installed_source.splitlines(keepends=True),
        candidate.splitlines(keepends=True), fromfile="a/" + arguments["path"],
        tofile="b/" + arguments["path"])
    patch = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in diff)
    result = {"contract": "EvaComponentImprovementDraft/v1", "path": arguments["path"],
            "source_sha256": before, "candidate_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
            "patch": patch, "candidate_content": candidate,
            "problem": arguments["problem"], "expected_gain": arguments["expected_gain"],
            "verification_plan": arguments["verification_plan"],
            "installed": False, "checks_executed": False, "measured_gain": None,
            "guidance": "Bewaar patch, kandidaat en meetplan met de bestaande documenttool. "
                        "Dit voorstel is niet uitgevoerd of geïnstalleerd; verwachte winst is geen meting."}
    if lineage is not None:
        result["candidate_lineage"] = lineage
    if source_target is not None:
        from eva.operations.component_evaluation import validate_saved_artifact_target
        result["source_target"] = validate_saved_artifact_target(source_target,
            relative_path=arguments["path"], baseline_sha256=before)
        result["baseline_content"] = installed_source
    return result


def prepare_component_release(draft, report):
    """Bind a reviewable installation input; process success is not measured gain."""
    from eva.contracts.canonical_json import canonical_sha256
    if (draft.get("contract") != "EvaComponentImprovementDraft/v1"
            or report.get("contract") != "EvaIsolatedComponentEvaluation/v1"
            or report.get("relative_path") != draft.get("path")
            or report.get("baseline_sha256") != draft.get("source_sha256")
            or report.get("candidate_sha256") != draft.get("candidate_sha256")
            or report.get("source_target") != draft.get("source_target")
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
    if "source_target" in draft:
        from eva.operations.component_evaluation import validate_saved_artifact_target
        payload["source_target"] = validate_saved_artifact_target(draft["source_target"],
            relative_path=draft["path"], baseline_sha256=draft["source_sha256"])
        content = draft.get("baseline_content")
        if not isinstance(content, str) or hashlib.sha256(content.encode()).hexdigest() != draft["source_sha256"]:
            raise ValueError("saved artifact baseline content differs")
        payload["baseline_content"] = content
        payload["required_before_installation"][-1] = "verified artifact save and reusable designation under current owner authority"
    if "draft_step" in draft:
        payload["draft_step"] = draft["draft_step"]
    if "candidate_lineage" in draft:
        payload["candidate_lineage"] = draft["candidate_lineage"]
    return {**payload, "candidate_package_sha256": canonical_sha256(payload)}


def bind_selected_release(package, selection):
    """Freeze the exact measured winner; selection is not final approval."""
    from eva.contracts.canonical_json import canonical_sha256
    if (selection.get("contract") != "EvaComponentDevelopmentSelection/v1"
            or selection.get("state") != "candidate_selected"
            or selection.get("installed") is not False
            or selection.get("semantic_improvement_verified") is not False
            or selection.get("path") != package.get("path")
            or selection.get("baseline_sha256") != package.get("baseline_sha256")
            or selection.get("selected_candidate_sha256") != package.get("candidate_sha256")
            or selection.get("selected_draft_step") != package.get("draft_step")
            or selection.get("selected_evaluation_step") != package["evaluation"].get("evaluation_step")
            or selection.get("assessment_sha256") !=
                package["evaluation"].get("development_assessment", {}).get("assessment_sha256")):
        raise ValueError("release differs from selected development winner")
    payload = {key: value for key, value in package.items() if key != "candidate_package_sha256"}
    payload["development_selection"] = selection
    payload["development_selection_sha256"] = canonical_sha256(selection)
    return {**payload, "candidate_package_sha256": canonical_sha256(payload)}
