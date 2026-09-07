#!/usr/bin/env python3
"""Validate and render Eva's small, strictly ordered execution workboard."""

from __future__ import annotations

import argparse
from datetime import date
from fnmatch import fnmatchcase
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import subprocess
from typing import Any


CONTRACT = "EvaExecutionWorkboard/v1"
CATALOG_CONTRACT = "EvaComponentCatalog/v1"
INVENTORY_CONTRACT = "EvaRepositoryInventory/v1"
MAX_BOARD_BYTES = 1024 * 1024
MAX_INVENTORY_BYTES = 8 * 1024 * 1024
TASK_ID = re.compile(r"[A-Z]{2,3}-[0-9]{3}")
GIT_COMMIT = re.compile(r"[0-9a-f]{40}")
STATUSES = {"queued", "in_progress", "review", "done", "blocked"}
PRIMARY_STATUSES = {"in_progress", "review", "blocked"}
ARTIFACT_RETENTION_RULE = (
    "/home/administrator/project/.eva2-artifacts is alleen een beheerd "
    "artifactarchief en vereist een expliciete kaart met eigenaar, doel, exact "
    "retentiepad en opruimmoment."
)
RULES = [
    "Afgeronde uitvoerkaarten staan eerst, daarna exact één primaire kaart, daarna alleen wachtrijkaarten.",
    "Alleen de primaire uitvoerkaart is nu uitvoerbaar; na afronding wordt precies één volgende productmijlpaal eerst tot een kleine kaart uitgewerkt.",
    "Een kaart noemt exacte bestanden, eigenaren en echte testcommando's.",
    "Een bouwer keurt zijn eigen taak niet goed; klaar vereist bewijs en onafhankelijke GO.",
    "Paden blijven in eva2 (of de twee gegenereerde inventory-uitgangen); tijdelijke data gaat via mktemp naar /tmp.",
    ARTIFACT_RETENTION_RULE,
    "Releaseherstel wordt compact met zijn evidence- en reviewreferenties afgesloten; "
    "daarna volgt productwerk volgens de vastgelegde meetcadans.",
    "Productcadans: kleine nulmeting → maintained upstream kiezen → bouwen/hergebruiken → gerichte functietest → passende praktijkproef → dezelfde nameting; pas een complete installatiekandidaat doorloopt de zware releasepoort.",
    "Een functionele kaart verwijdert alleen de code die zij binnen haar vooraf genoemde paden werkelijk vervangt; breder opruimwerk mag de gebruikersfunctie niet opslokken.",
    "De actief/reserveslot-installatie (voorheen A/B genoemd) is geen functietest of experiment: het werkende slot blijft actief terwijl pas één complete releasekandidaat eenmaal in het reserveslot wordt gecontroleerd en daarna eventueel actief gemaakt; kleine ontwikkelwijzigingen krijgen geen A/B-cyclus.",
]
RETIRED_BENCHMARK_MARKERS = (
    "comparable-agent-run-v1.schema.json",
    "test_comparable_agent_run_validator.py",
    "validate_comparable_agent_run.py",
)
TOP_FIELDS = {
    "activeTaskId",
    "commandWorkingDirectory",
    "completions",
    "contract",
    "executionTasks",
    "humanBoardPath",
    "inventoryPath",
    "lastUpdated",
    "audit",
    "maxActiveImplementationTasks",
    "milestones",
    "rules",
    "schemaVersion",
    "sequencePolicy",
    "statusValues",
    "team",
}
EXECUTION_FIELDS = {
    "area",
    "changePaths",
    "componentIds",
    "dependsOn",
    "doneWhen",
    "evidenceRequired",
    "id",
    "inventoryOwners",
    "order",
    "outcome",
    "ownerRole",
    "reviewerRole",
    "stateDetails",
    "status",
    "testCommands",
    "title",
}
MILESTONE_FIELDS = {"area", "id", "order", "outcome", "title"}
COMPLETION_FIELDS = {
    "baseCommit",
    "commit",
    "decision",
    "evidenceCommit",
    "evidencePaths",
    "implementedBy",
    "reviewCommit",
    "reviewedBy",
    "testReceiptPaths",
}
ALLOWED_COMMANDS = {"node", "pytest", "python3", "ruff"}
VERIFICATION_SCRIPTS = {
    "experiments/react-admin-dashboard-v1/verify-project.cjs",
    "../test/repository-inventory-disposition.test.js",
    "../test/repository-inventory.test.js",
    "tools/check_component_catalog.py",
    "tools/check_current_workboard.py",
}
STATE_DETAIL_FIELDS = {
    "baseCommit",
    "blockerReason",
    "evidenceCommit",
    "implementationCommit",
    "reviewEvidencePaths",
}
TEAM = {
    "architecturelead": "/root",
    "releaselead": "/root/release_authority",
    "dependencyreviewer": "/root/pydantic_dedup",
    "proofreviewer": "/root/proof_review",
    "workboardreviewer": "/root/workboard_review",
    "dashboardreviewer": "/root/identity_pair_review",
}


def _root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip())


def _object(path: Path, label: str, *, max_bytes: int = MAX_BOARD_BYTES) -> dict[str, Any]:
    metadata = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"{label} must be a regular non-symlink file")
    if not 0 < metadata.st_size <= max_bytes:
        raise RuntimeError(f"{label} size is invalid")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be an object")
    return value


def _strings(value: object, label: str, *, minimum: int = 1) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) < minimum
        or not all(isinstance(item, str) and item.strip() and "\x00" not in item for item in value)
        or len(value) != len(set(value))
    ):
        raise RuntimeError(f"{label} must contain unique non-empty strings")
    return value


def _repo_file(root: Path, raw: str, label: str) -> Path:
    if "\\" in raw:
        raise RuntimeError(f"{label} is not a repository path")
    relative = PurePosixPath(raw)
    if (
        relative.is_absolute()
        or relative.as_posix() != raw
        or any(part in {"", ".", ".."} for part in relative.parts)
        or not (raw.startswith("eva2/") or raw.startswith("docs/") or raw in {"renovate.json", "README.md"})
    ):
        raise RuntimeError(f"{label} is not a safe repository path")
    path = root.joinpath(*relative.parts)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise RuntimeError(f"{label} does not exist") from error
    if root not in resolved.parents or path.is_symlink() or not path.is_file():
        raise RuntimeError(f"{label} must be an exact regular repository file")
    return path


def _inventory(root: Path, path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    inventory = _object(path, "repository inventory", max_bytes=MAX_INVENTORY_BYTES)
    if inventory.get("schema") != INVENTORY_CONTRACT:
        raise RuntimeError("repository inventory contract differs")
    files = inventory.get("files")
    if not isinstance(files, list):
        raise RuntimeError("repository inventory files differ")
    entries: dict[str, dict[str, Any]] = {}
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise RuntimeError("repository inventory entry differs")
        entries[item["path"]] = item
    return entries, inventory


def _catalog_owners(catalog: dict[str, Any], raw: str) -> set[str]:
    prefixes = _strings(catalog.get("scopePrefixes"), "component scopePrefixes")
    if not any(raw.startswith(prefix) for prefix in prefixes):
        return set()
    owners: set[str] = set()
    for component in catalog["components"]:
        for pattern in component["ownerPaths"]:
            if fnmatchcase(raw, pattern):
                owners.add(component["id"])
    if len(owners) != 1:
        raise RuntimeError(f"{raw} must have exactly one component owner")
    return owners


def _command_file(root: Path, raw: str, task_id: str) -> Path:
    workspace = root / "eva2"
    candidate = Path(raw)
    if candidate.is_absolute():
        raise RuntimeError(f"{task_id} test command contains an absolute path")
    try:
        resolved = (workspace / candidate).resolve(strict=True)
    except OSError as error:
        raise RuntimeError(f"{task_id} test command names a missing file") from error
    if root not in resolved.parents or resolved.is_symlink() or not resolved.is_file():
        raise RuntimeError(f"{task_id} test command path is not a repository file")
    return resolved


def _check_command(root: Path, command: str, task_id: str) -> str:
    if any(character in command for character in "|&<>\n\r`$();") or any(
        marker in command
        for marker in (
            "--co",
            "--collect-only",
            "--help",
            "--setup-plan",
            "--version",
            "git diff --check",
        )
    ):
        raise RuntimeError(f"{task_id} contains a non-verifying test command")
    try:
        words = shlex.split(command)
    except ValueError as error:
        raise RuntimeError(f"{task_id} contains an invalid test command") from error
    environment: list[str] = []
    while words and "=" in words[0] and not words[0].startswith(("=", "-")):
        environment.append(words.pop(0))
    allowed_environment = {"TEMP=/tmp", "TMP=/tmp", "TMPDIR=/tmp"}
    if len(environment) != len(set(environment)) or any(
        item not in allowed_environment for item in environment
    ):
        raise RuntimeError(f"{task_id} contains an unsupported test command environment")
    if not words or words[0] not in ALLOWED_COMMANDS:
        raise RuntimeError(f"{task_id} contains an unsupported test command")
    if words == ["true"] or len(words) < 2:
        raise RuntimeError(f"{task_id} contains a non-verifying test command")
    executable = words[0]
    if executable == "pytest":
        options = [word for word in words[1:] if word.startswith("-")]
        if any(option != "-q" for option in options):
            raise RuntimeError(f"{task_id} pytest command contains a non-run option")
        paths = [word for word in words[1:] if not word.startswith("-") and ".py" in word]
        if not paths:
            raise RuntimeError(f"{task_id} pytest command contains no test file")
        for raw in paths:
            _command_file(root, raw.split("::", 1)[0], task_id)
    elif executable in {"node", "python3"}:
        script = next((word for word in words[1:] if not word.startswith("-")), None)
        if (
            script is None
            or script not in VERIFICATION_SCRIPTS
            or words[1:] != [script]
        ):
            raise RuntimeError(f"{task_id} test command contains no allowed verification script")
        _command_file(root, script, task_id)
    elif executable == "ruff":
        if words[1:2] != ["check"]:
            raise RuntimeError(f"{task_id} Ruff test command must use the check subcommand")
        paths = [word for word in words[2:] if word.endswith(".py")]
        if not paths or words[2:] != paths:
            raise RuntimeError(f"{task_id} Ruff command contains no Python file")
        for raw in paths:
            _command_file(root, raw, task_id)
    return executable


def _commit_exists(root: Path, commit: object, label: str) -> None:
    if not isinstance(commit, str) or GIT_COMMIT.fullmatch(commit) is None:
        raise RuntimeError(f"{label} is not a full commit hash")
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=root,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{label} is not present in this repository")


def _git_text(root: Path, commit: str, raw: str, label: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{commit}:{raw}"],
        cwd=root,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{label} is absent from its bound commit")
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError(f"{label} is not UTF-8 text") from error


def _validate_implementation_scope(
    root: Path,
    task_id: str,
    base_commit: object,
    implementation_commit: object,
    change_paths: list[str],
) -> tuple[str, str]:
    _commit_exists(root, base_commit, f"{task_id} baseCommit")
    _commit_exists(root, implementation_commit, f"{task_id} implementationCommit")
    assert isinstance(base_commit, str)
    assert isinstance(implementation_commit, str)
    parent = subprocess.run(
        ["git", "rev-parse", f"{implementation_commit}^"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if parent.returncode != 0 or parent.stdout.strip() != base_commit:
        raise RuntimeError(f"{task_id} implementationCommit is not directly based on baseCommit")
    changed = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", base_commit, implementation_commit],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if set(changed) != set(change_paths) or len(changed) != len(change_paths):
        raise RuntimeError(f"{task_id} implementation commit differs from exact changePaths")
    return base_commit, implementation_commit


def _validate_direct_proof_commit(
    root: Path,
    task_id: str,
    parent_commit: str,
    proof_commit: object,
    proof_paths: list[str],
    label: str,
) -> str:
    _commit_exists(root, proof_commit, f"{task_id} {label}")
    assert isinstance(proof_commit, str)
    parent = subprocess.run(
        ["git", "rev-parse", f"{proof_commit}^"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if parent.returncode != 0 or parent.stdout.strip() != parent_commit:
        raise RuntimeError(f"{task_id} {label} is not directly based on its predecessor")
    changed = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", parent_commit, proof_commit],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if set(changed) != set(proof_paths) or len(changed) != len(proof_paths):
        raise RuntimeError(f"{task_id} {label} differs from its exact proof paths")
    return proof_commit


def _validate_completion(
    root: Path,
    task: dict[str, Any],
    completion: object,
    team: dict[str, str],
) -> None:
    task_id = task["id"]
    if not isinstance(completion, dict) or set(completion) != COMPLETION_FIELDS:
        raise RuntimeError(f"{task_id} completion fields differ")
    if completion.get("decision") != "GO":
        raise RuntimeError(f"{task_id} completion must contain an independent GO")
    expected_builder = team[task["ownerRole"]]
    expected_reviewer = team[task["reviewerRole"]]
    if completion.get("implementedBy") != expected_builder:
        raise RuntimeError(f"{task_id} completion implementer differs")
    if completion.get("reviewedBy") != expected_reviewer or expected_builder == expected_reviewer:
        raise RuntimeError(f"{task_id} completion independent reviewer differs")
    _, implementation_commit = _validate_implementation_scope(
        root,
        task_id,
        completion.get("baseCommit"),
        completion.get("commit"),
        task["changePaths"],
    )
    test_paths = _strings(completion.get("testReceiptPaths"), f"{task_id} testReceiptPaths")
    evidence_commit = _validate_direct_proof_commit(
        root,
        task_id,
        implementation_commit,
        completion.get("evidenceCommit"),
        test_paths,
        "evidenceCommit",
    )
    review_paths = _strings(completion.get("evidencePaths"), f"{task_id} evidencePaths")
    review_commit = _validate_direct_proof_commit(
        root,
        task_id,
        evidence_commit,
        completion.get("reviewCommit"),
        review_paths,
        "reviewCommit",
    )
    for field, proof_commit, paths in (
        ("testReceiptPaths", evidence_commit, test_paths),
        ("evidencePaths", review_commit, review_paths),
    ):
        for raw in paths:
            if not raw.startswith(("eva2/evidence/", "eva2/reviews/")):
                raise RuntimeError(f"{task_id} {field} must point to evidence or review")
            proof_text = _git_text(root, proof_commit, raw, f"{task_id} {field}")
            if task_id not in proof_text or implementation_commit not in proof_text:
                raise RuntimeError(f"{task_id} {field} is not bound to the task and commit")
    head = subprocess.run(
        ["git", "merge-base", "--is-ancestor", review_commit, "HEAD"],
        cwd=root,
        capture_output=True,
    )
    if head.returncode != 0:
        raise RuntimeError(f"{task_id} reviewCommit is not in current history")


def _validate_state_details(
    root: Path,
    task: dict[str, Any],
    status: str,
    details: object,
) -> None:
    task_id = task["id"]
    if not isinstance(details, dict) or set(details) != STATE_DETAIL_FIELDS:
        raise RuntimeError(f"{task_id} stateDetails fields differ")
    base_commit = details.get("baseCommit")
    commit = details.get("implementationCommit")
    evidence_commit = details.get("evidenceCommit")
    evidence = details.get("reviewEvidencePaths")
    blocker = details.get("blockerReason")
    if not isinstance(evidence, list):
        raise RuntimeError(f"{task_id} reviewEvidencePaths differ")
    if status in {"queued", "in_progress"}:
        if (
            base_commit is not None
            or commit is not None
            or evidence_commit is not None
            or evidence
            or blocker is not None
        ):
            raise RuntimeError(f"{task_id} has premature state evidence")
        return
    if status == "blocked":
        if (
            base_commit is not None
            or commit is not None
            or evidence_commit is not None
            or evidence
            or not isinstance(blocker, str)
            or len(blocker.strip()) < 20
        ):
            raise RuntimeError(f"{task_id} blocked state requires a concrete blocker reason")
        return
    if blocker is not None:
        raise RuntimeError(f"{task_id} non-blocked state contains a blocker reason")
    _, implementation_commit = _validate_implementation_scope(
        root,
        task_id,
        base_commit,
        commit,
        task["changePaths"],
    )
    paths = _strings(evidence, f"{task_id} reviewEvidencePaths")
    bound_evidence_commit = _validate_direct_proof_commit(
        root,
        task_id,
        implementation_commit,
        evidence_commit,
        paths,
        "evidenceCommit",
    )
    current_history = subprocess.run(
        ["git", "merge-base", "--is-ancestor", bound_evidence_commit, "HEAD"],
        cwd=root,
        capture_output=True,
    )
    if current_history.returncode != 0:
        raise RuntimeError(f"{task_id} evidenceCommit is not in current history")
    for raw in paths:
        if not raw.startswith(("eva2/evidence/", "eva2/reviews/")):
            raise RuntimeError(f"{task_id} review evidence must be an evidence or review file")
        proof_text = _git_text(
            root,
            bound_evidence_commit,
            raw,
            f"{task_id} reviewEvidencePath",
        )
        if task_id not in proof_text or implementation_commit not in proof_text:
            raise RuntimeError(f"{task_id} review evidence is not bound to the task and commit")


def render_human(board: dict[str, Any]) -> str:
    tasks = board["executionTasks"]
    primary = next(task for task in tasks if task["status"] in PRIMARY_STATUSES)
    primary_index = tasks.index(primary)
    next_task = tasks[primary_index + 1] if primary_index + 1 < len(tasks) else None
    status_label = {
        "in_progress": "🟡 BEZIG",
        "review": "🔵 REVIEW",
        "blocked": "🔴 GEBLOKKEERD",
    }[primary["status"]]
    lines = [
        "# Eva 2 — uitvoerbaar werkbord",
        "",
        f"Laatst bijgewerkt: {board['lastUpdated']}.",
        "",
        "Doel: [Eva als zelfstandig lerende digitale huisgenoot](PROJECTDOEL.md).",
        "Gebouwd, getest, live en door jou geaccepteerd zijn verschillende stappen.",
        "",
        board["audit"]["goalRevision"]["currentWork"],
        f"[Actueel bewijs]({board['audit']['goalRevision']['currentEvidence'].removeprefix('eva2/')}).",
        "",
        board["audit"]["goalRevision"]["nextStep"],
        "",
        "## Nu",
        "",
        "| Volgorde | Kaart | Resultaat | Status |",
        "|---:|---|---|---|",
        f"| {primary['order']} | `{primary['id']}` — {primary['title']} | {primary['outcome']} | {status_label} |",
    ]
    if next_task is not None:
        lines.append(
            f"| {next_task['order']} | `{next_task['id']}` — {next_task['title']} | {next_task['outcome']} | ⚪ HIERNA |"
        )
    lines.extend(["", "Klaar betekent:", ""])
    lines.extend(f"- {condition}" for condition in primary["doneWhen"])
    lines.extend(["", "<details>", "<summary>Technische uitvoering: bestanden en controles</summary>",
                  "", f"### Exact werk voor `{primary['id']}`", ""])
    lines.extend(f"- `{path}`" for path in primary["changePaths"])
    lines.extend(["", "Echte controles:", ""])
    lines.extend(f"- `{command}`" for command in primary["testCommands"])
    if next_task is not None:
        lines.extend(["", f"### Daarna voorbereid: `{next_task['id']}`", ""])
        lines.extend(f"- `{path}`" for path in next_task["changePaths"])
        lines.extend(["", "Vooraf vastgelegde controles:", ""])
        lines.extend(f"- `{command}`" for command in next_task["testCommands"])
    lines.extend(["", "</details>"])
    lines.extend(
        [
            "",
            "## Daarna",
            "",
            "Deze onderwerpen zijn alleen mijlpalen. Een mijlpaal wordt pas uitvoerbaar",
            "nadat zij als eerstvolgende kleine kaart exacte bestanden en tests heeft.",
            "",
        ]
    )
    for item in board["milestones"]:
        lines.append(
            f"- [ ] `{item['id']}` — {item['title']}: {item['outcome']}"
        )
    lines.extend(["", "<details>", "<summary>Afgeronde en eerdere uitvoerkaarten met bewijs</summary>",
                  "", "## Uitvoerkaarten en bewijs", "",
                  "Afgevinkt betekent alleen: de vastgelegde kaart is afgerond, niet het hele product.", ""])
    for task in tasks:
        checked = "x" if task["status"] == "done" else " "
        lines.append(f"- [{checked}] `{task['id']}` — {task['title']} ({task['status']}).")
        completion = board["completions"].get(task["id"], {})
        for path in completion.get("testReceiptPaths", []):
            lines.append(f"  [Bewijs]({path.removeprefix('eva2/')}).")
    lines.extend(["", "</details>"])
    pending = board["audit"]["pendingHumanAcceptance"]["task"]
    technical = board["audit"].get("pendingTechnicalReview")
    if technical is not None:
        lines.extend(["", "## Technische review blijft open", "",
                      f"- [ ] `{technical['task']['id']}` — {technical['reason']}",
                      f"  [Bestaand bewijs]({technical['evidence'].removeprefix('eva2/')})."])
    lines.extend(["", "## Menselijke acceptatie blijft open", "",
                  f"- [ ] `{pending['id']}` — {pending['outcome']}"])
    lines.extend(
        [
            "",
            "<details>",
            "<summary>Werkafspraken en machineleesbare bron</summary>",
            "",
            "## Vaste regels",
            "",
            *[f"- {rule}" for rule in board["rules"]],
            "",
            "Machineleesbare bron:",
            "[`design/product/execution-workboard-v1.json`](design/product/execution-workboard-v1.json).",
            "",
            "</details>",
            "",
        ]
    )
    return "\n".join(lines)


def validate(
    root: Path,
    board_path: Path,
    catalog_path: Path,
    human_path: Path | None = None,
) -> dict[str, object]:
    board = _object(board_path, "workboard")
    catalog = _object(catalog_path, "component catalog")
    if set(board) != TOP_FIELDS:
        raise RuntimeError("workboard top-level fields differ")
    try:
        updated = date.fromisoformat(board.get("lastUpdated"))
    except (TypeError, ValueError) as error:
        raise RuntimeError("workboard lastUpdated is not an ISO date") from error
    if updated > date.today():
        raise RuntimeError("workboard lastUpdated is in the future")
    if (
        board.get("contract") != CONTRACT
        or board.get("schemaVersion") != 1
        or board.get("sequencePolicy") != "done_then_one_primary_then_queued"
        or board.get("commandWorkingDirectory") != "eva2"
        or board.get("maxActiveImplementationTasks") != 1
        or board.get("rules") != RULES
        or set(_strings(board.get("statusValues"), "statusValues")) != STATUSES
    ):
        raise RuntimeError("workboard contract or rules differ")
    team = board.get("team")
    if team != TEAM:
        raise RuntimeError("workboard team identities differ")
    if catalog.get("contract") != CATALOG_CONTRACT or not isinstance(catalog.get("components"), list):
        raise RuntimeError("component catalog contract differs")
    for component in catalog["components"]:
        if not isinstance(component, dict):
            raise RuntimeError("component catalog component differs")
        _strings(component.get("ownerPaths"), f"{component.get('id')} ownerPaths")
    component_ids = {component["id"] for component in catalog["components"]}
    inventory_raw = board.get("inventoryPath")
    human_raw = board.get("humanBoardPath")
    if inventory_raw != "docs/eva-repository-inventory.generated.json" or human_raw != "eva2/WERKBORD.md":
        raise RuntimeError("workboard managed paths differ")
    inventory_path = _repo_file(root, inventory_raw, "inventoryPath")
    inventory_entries, _ = _inventory(root, inventory_path)
    tasks = board.get("executionTasks")
    milestones = board.get("milestones")
    completions = board.get("completions")
    if not isinstance(tasks, list) or not tasks:
        raise RuntimeError("workboard execution cards differ")
    if not isinstance(milestones, list) or not milestones or not isinstance(completions, dict):
        raise RuntimeError("workboard milestones or completions differ")
    seen_ids: set[str] = set()
    seen_orders: set[int] = set()
    previous_order = -1
    phase = "done"
    primary: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    for index, task in enumerate(tasks):
        if not isinstance(task, dict) or set(task) != EXECUTION_FIELDS:
            raise RuntimeError(f"execution task {index} fields differ")
        task_id = task.get("id")
        order = task.get("order")
        status = task.get("status")
        if (
            not isinstance(task_id, str)
            or TASK_ID.fullmatch(task_id) is None
            or task_id in seen_ids
            or isinstance(order, bool)
            or not isinstance(order, int)
            or order <= previous_order
            or order in seen_orders
            or status not in STATUSES
        ):
            raise RuntimeError(f"execution task {index} identity, order or status differs")
        if phase == "done" and status in PRIMARY_STATUSES:
            phase = "primary"
        elif phase == "done" and status != "done":
            raise RuntimeError("execution cards must be done, then one primary, then queued")
        elif phase == "primary" and status == "queued":
            phase = "queued"
        elif phase == "primary":
            raise RuntimeError("execution cards must contain exactly one primary")
        elif phase == "queued" and status != "queued":
            raise RuntimeError("execution cards after the primary must remain queued")
        if status in PRIMARY_STATUSES:
            primary.append(task_id)
        seen_ids.add(task_id)
        seen_orders.add(order)
        previous_order = order
        by_id[task_id] = task
        for field in ("area", "title", "outcome"):
            if not isinstance(task.get(field), str) or not task[field].strip():
                raise RuntimeError(f"{task_id} {field} is empty")
        if task.get("ownerRole") not in team or task.get("reviewerRole") not in team:
            raise RuntimeError(f"{task_id} names an unknown team role")
        if task["ownerRole"] == task["reviewerRole"]:
            raise RuntimeError(f"{task_id} cannot review its own work")
        if status != "done":
            _validate_state_details(root, task, status, task.get("stateDetails"))
        dependencies = _strings(task.get("dependsOn"), f"{task_id} dependsOn", minimum=0)
        if any(dependency not in seen_ids for dependency in dependencies):
            raise RuntimeError(f"{task_id} has a missing or forward dependency")
        if status != "queued" and any(by_id[dependency]["status"] != "done" for dependency in dependencies):
            raise RuntimeError(f"{task_id} started before its dependencies were done")
        selected_components = _strings(task.get("componentIds"), f"{task_id} componentIds")
        if any(component not in component_ids for component in selected_components):
            raise RuntimeError(f"{task_id} names an unknown component")
        owners = _strings(task.get("inventoryOwners"), f"{task_id} inventoryOwners")
        change_paths = _strings(task.get("changePaths"), f"{task_id} changePaths")
        managed_generated_paths = {
            inventory_raw,
            "docs/eva-repository-inventory.generated.md",
            # Jaap requested a clear entry point in the relocated project root.
            "README.md",
        }
        if status != "done" and any(
            not raw.startswith("eva2/") and raw not in managed_generated_paths
            for raw in change_paths
        ):
            raise RuntimeError(
                f"{task_id} open changePaths must stay inside the canonical Eva2 authoring root"
            )
        actual_inventory_owners: set[str] = set()
        actual_component_owners: set[str] = set()
        for raw in change_paths:
            _repo_file(root, raw, f"{task_id} changePath")
            entry = inventory_entries.get(raw)
            if (
                entry is None
                or entry.get("present") is not True
                or entry.get("decision") != "keep"
                or entry.get("owner") not in owners
            ):
                raise RuntimeError(f"{task_id} changePath inventory ownership differs")
            actual_inventory_owners.add(entry["owner"])
            catalog_owners = _catalog_owners(catalog, raw)
            actual_component_owners.update(catalog_owners)
        if set(owners) != actual_inventory_owners:
            raise RuntimeError(f"{task_id} inventoryOwners must be exact")
        if set(selected_components) != actual_component_owners:
            raise RuntimeError(f"{task_id} componentIds must match exact path ownership")
        commands = _strings(task.get("testCommands"), f"{task_id} testCommands")
        if (
            any(marker in raw for marker in RETIRED_BENCHMARK_MARKERS for raw in change_paths)
            or any(marker in command for marker in RETIRED_BENCHMARK_MARKERS for command in commands)
        ):
            raise RuntimeError(f"{task_id} references a retired benchmark format")
        executables = [_check_command(root, command, task_id) for command in commands]
        if "pytest" not in executables:
            raise RuntimeError(f"{task_id} must contain a real test-suite command")
        _strings(task.get("evidenceRequired"), f"{task_id} evidenceRequired")
        _strings(task.get("doneWhen"), f"{task_id} doneWhen", minimum=2)
        if status == "done" and task_id in {"RR-002", "RR-003", "RR-004"}:
            # Recovery is recorded once in the compact audit block; its source
            # receipts are intentionally not rewritten as ordinary card commits.
            if task_id != "RR-002":
                raise RuntimeError(f"{task_id} recovery must remain in audit")
        elif status == "done":
            _validate_completion(root, task, completions.get(task_id), team)
            _validate_state_details(root, task, status, task.get("stateDetails"))
            if task["stateDetails"]["baseCommit"] != completions[task_id]["baseCommit"]:
                raise RuntimeError(f"{task_id} state and completion base commits differ")
            if task["stateDetails"]["implementationCommit"] != completions[task_id]["commit"]:
                raise RuntimeError(f"{task_id} state and completion commits differ")
            if task["stateDetails"]["evidenceCommit"] != completions[task_id]["evidenceCommit"]:
                raise RuntimeError(f"{task_id} state and completion evidence commits differ")
        elif task_id in completions:
            raise RuntimeError(f"{task_id} has completion evidence before it is done")
    if len(primary) != 1 or board.get("activeTaskId") != primary[0]:
        raise RuntimeError("workboard must name exactly one primary execution card")
    if len([task for task in tasks if task["status"] != "done"]) not in {1, 2}:
        raise RuntimeError("workboard may expose only the primary and immediate next open cards")
    audited_done = {"RR-002", "RR-003", "RR-004"}
    if set(completions) != {
        task["id"] for task in tasks if task["status"] == "done" and task["id"] not in audited_done
    }:
        raise RuntimeError("workboard completion set differs from done cards")
    for index, item in enumerate(milestones):
        if not isinstance(item, dict) or set(item) != MILESTONE_FIELDS:
            raise RuntimeError(f"milestone {index} fields differ")
        item_id = item.get("id")
        order = item.get("order")
        if (
            not isinstance(item_id, str)
            or TASK_ID.fullmatch(item_id) is None
            or item_id in seen_ids
            or isinstance(order, bool)
            or not isinstance(order, int)
            or order <= previous_order
            or order in seen_orders
        ):
            raise RuntimeError(f"milestone {index} identity or order differs")
        for field in ("area", "title", "outcome"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise RuntimeError(f"{item_id} {field} is empty")
        seen_ids.add(item_id)
        seen_orders.add(order)
        previous_order = order
        by_id[item_id] = item
    audit = board.get("audit")
    if not isinstance(audit, dict) or not audit.get("releaseRecovery"):
        raise RuntimeError("workboard audit must retain release recovery trace")
    revision = audit.get("goalRevision")
    roadmap = audit.get("projectDashboard", {}).get("roadmap")
    if not isinstance(roadmap, list) or not roadmap:
        raise RuntimeError("project roadmap missing")
    roadmap_ids = []
    for phase in roadmap:
        if not isinstance(phase, dict) or any(
            not isinstance(phase.get(field), str) or not phase[field].strip()
            for field in ("id", "title", "outcome", "acceptance")
        ):
            raise RuntimeError("project roadmap phase differs")
        roadmap_ids.extend(_strings(phase.get("taskIds"), "roadmap taskIds"))
    open_ids = [task["id"] for task in tasks if task["status"] != "done"] + [item["id"] for item in milestones]
    if (
        len(roadmap_ids) != len(set(roadmap_ids))
        or not set(roadmap_ids) <= set(by_id)
        or [item_id for item_id in roadmap_ids if item_id in open_ids] != open_ids
    ):
        raise RuntimeError("project roadmap differs from actual work order")
    if not isinstance(revision, dict) or revision.get("document") != "eva2/PROJECTDOEL.md":
        raise RuntimeError("workboard goal revision differs")
    _repo_file(root, revision["document"], "goal document")
    _repo_file(root, revision["currentEvidence"], "current evidence")
    selected_human = human_path or _repo_file(root, human_raw, "humanBoardPath")
    if selected_human.is_symlink() or not selected_human.is_file():
        raise RuntimeError("human workboard must be a regular non-symlink file")
    if selected_human.read_text(encoding="utf-8") != render_human(board):
        raise RuntimeError("human workboard differs from its machine-readable source")
    return {
        "activeTaskId": primary[0],
        "contract": CONTRACT,
        "executionTaskCount": len(tasks),
        "milestoneCount": len(milestones),
        "status": "valid",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", type=Path, default=Path("design/product/execution-workboard-v1.json"))
    parser.add_argument("--catalog", type=Path, default=Path("design/architecture/component-catalog-v1.json"))
    parser.add_argument("--human", type=Path, default=Path("WERKBORD.md"))
    parser.add_argument("--render-human", action="store_true")
    arguments = parser.parse_args()
    root = _root()
    board = _object(arguments.board.resolve(), "workboard")
    if arguments.render_human:
        print(render_human(board), end="")
        return 0
    result = validate(root, arguments.board.resolve(), arguments.catalog.resolve(), arguments.human.resolve())
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
