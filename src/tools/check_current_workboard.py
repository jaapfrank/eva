"""Validate and render the single current workboard; history stays immutable."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path


def regular(root: Path, relative: str) -> Path:
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"not an in-repository regular file: {relative}")
    return path


def small_tasks(board):
    return [task for step in board["audit"]["deliveryPlan"]["implementationSlices"]
            for task in step.get("remainingTasks", [])]


def validate(root: Path, board):
    if board.get("contract") != "EvaExecutionWorkboard/v2" or board.get("schemaVersion") != 2:
        raise ValueError("unsupported current workboard contract")
    if board.get("commandWorkingDirectory") != "." or board.get("humanBoardPath") != "development/docs/WERKBORD.md":
        raise ValueError("workboard must use the active repository root")
    binding = board["historicalWorkboard"]
    raw = regular(root, binding["path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding["sha256"]:
        raise ValueError("historical workboard bytes changed")
    previous = {t["id"]: t for t in small_tasks(json.loads(raw))}
    # New owner-requested scope is explicit and frozen separately. This extends
    # coverage without weakening any historical end condition or completion.
    extension_ids = set()
    for binding in board.get("taskScopeExtensions", []):
        raw_extension = regular(root, binding["path"]).read_bytes()
        if hashlib.sha256(raw_extension).hexdigest() != binding["sha256"]:
            raise ValueError("task scope extension bytes changed")
        extension = json.loads(raw_extension)
        if (extension.get("contract") != "EvaWorkboardScopeExtension/v1"
                or extension.get("id") != binding["id"] or binding["id"] in extension_ids
                or extension.get("ownerAuthorized") is not True or not extension.get("authorization")):
            raise ValueError("invalid task scope extension")
        extension_ids.add(binding["id"])
        regular(root, extension["sourceDocument"])
        for task in extension["tasks"]:
            if task["id"] in previous or task.get("status") != "planned" or not task.get("doneWhen"):
                raise ValueError("duplicate or invalid added task scope")
            previous[task["id"]] = task
    tasks = small_tasks(board)
    ids = [t["id"] for t in tasks]
    consolidations = board.get("taskConsolidations", [])
    merged = {}
    for group in consolidations:
        primary = group["primaryTaskId"]
        sources = group["sourceTaskIds"]
        if (primary in merged or primary not in sources or len(sources) < 2
                or len(sources) != len(set(sources))
                or group.get("ownerAuthorized") is not True
                or not group.get("ownerInstruction")):
            raise ValueError("invalid owner task consolidation")
        merged[primary] = sources
    covered = []
    for task in tasks:
        if task["id"] not in merged:
            if task.get("deviceChecks"):
                raise ValueError("undeclared device checks")
            covered.append(task)
            continue
        checks = task.get("deviceChecks", [])
        if [check["sourceTaskId"] for check in checks] != merged[task["id"]]:
            raise ValueError("consolidated device scope changed")
        covered.extend({**check, "id": check["sourceTaskId"]} for check in checks)
        if task["status"] == "completed" and any(check["status"] != "completed" for check in checks):
            raise ValueError("consolidated function has unfinished device checks")
    source_ids = [task["id"] for task in covered]
    if (len(ids) != len(set(ids)) or set(merged) - set(ids)
            or len(source_ids) != len(set(source_ids)) or set(source_ids) != set(previous)):
        raise ValueError("original task scope changed")
    for task in covered:
        old = previous[task["id"]]
        if task["doneWhen"] != old["doneWhen"]:
            raise ValueError(f"original end condition changed: {task['id']}")
        if old["status"] == "completed" and task["status"] != "completed":
            raise ValueError(f"accepted technical completion revoked: {task['id']}")
    for task in tasks:
        old = previous[task["id"]]
        if task["doneWhen"] != old["doneWhen"]:
            raise ValueError(f"original end condition changed: {task['id']}")
        if old["status"] != "completed" and task["status"] == "completed":
            for key in ("completionEvidence", "independentReview"):
                if not isinstance(task.get(key), str):
                    raise ValueError(f"completion requires {key}: {task['id']}")
                regular(root, task[key])
    for phase in board["audit"]["deliveryPlan"].get("finalizationPlan", {}).get("phases", []):
        if set(phase.get("taskIds", []) + phase.get("existingTaskIds", [])) - set(ids):
            raise ValueError("finalization phase references missing task")
    if any(set(task.get("dependsOn", [])) - set(ids) for task in tasks):
        raise ValueError("task dependency is missing")
    work = board["currentWork"]
    now = board["audit"]["projectDashboard"]["deliveryOverview"]["now"]
    if work["id"] != board["activeTaskId"] or work["id"] != now["taskId"]:
        raise ValueError("more than one authoritative current task")
    if board["maxActiveImplementationTasks"] != 1:
        raise ValueError("one primary implementation task is required")
    if board["audit"]["deliveryPlan"] != board["audit"]["projectDashboard"]["deliveryPlan"]:
        raise ValueError("dashboard and workboard task projections disagree")
    regular(root, board["audit"]["goalRevision"]["document"])
    return {"status": "valid", "taskCount": len(tasks),
            "completed": sum(t["status"] == "completed" for t in tasks), "activeTaskId": work["id"]}


def render_human(board):
    project = board["audit"]["projectDashboard"]
    overview = project["deliveryOverview"]
    lines = ["# Eva2 — huidig werkbord", "", "Bron: development/design/product/execution-workboard-v1.json. Geen aparte taakregistratie.", "",
             "## Nu", "", overview["now"]["title"], "", overview["now"]["summary"], "",
             "Klaar wanneer: " + overview["now"]["doneWhen"], "", "## Afmaakvolgorde", ""]
    lines.extend(f"{i}. {text}" for i, text in enumerate(board["audit"]["deliveryPlan"]["finishOrder"], 1))
    lines += ["", "## Vier productuitkomsten", "", "| Uitkomst | Wat staat | Wat ontbreekt |", "| --- | --- | --- |"]
    for row in project["housemateSummary"]["rows"]:
        lines.append(f"| {row['name']} | {row['label']} | {row['remaining']} |")
    lines += ["", project["housemateSummary"]["note"], "", "## Alle geregistreerde kleine taken", "", "| Taak | Status | Eindvoorwaarde |", "| --- | --- | --- |"]
    for task in small_tasks(board):
        state = "✓ Afgerond" if task["status"] == "completed" else ("Geparkeerd" if task.get("parked") else "● Open")
        lines.append(f"| {task['id']} · {task['title']} | {state} | {task['doneWhen'].replace('|', '/')} |")
        for check in task.get("deviceChecks", []):
            lines.append(f"| ↳ Apparaatcontrole: {check['device']} | {check['status']} | {check['doneWhen'].replace('|', '/')} |")
    lines += ["", "## Van jou", "", overview["ownerAction"]["detail"], "", "## Historie", "",
              f"Het oorspronkelijke bord staat in [{board['historicalWorkboard']['path']}]({os.path.relpath(board['historicalWorkboard']['path'], 'development/docs')}).",
              "Oude criteria, mislukte proeven en onbekend verbruik blijven behouden. De reorganisatie is geen productfunctie.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    board = json.loads(regular(root, "development/design/product/execution-workboard-v1.json").read_text())
    result = validate(root, board)
    text = render_human(board)
    target = root / "development/docs/WERKBORD.md"
    if args.write:
        target.write_text(text)
    elif not target.is_file() or target.read_text() != text:
        raise ValueError("rendered development/docs/WERKBORD.md is stale")
    print(json.dumps(result))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
