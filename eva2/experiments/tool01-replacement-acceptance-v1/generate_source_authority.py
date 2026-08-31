#!/usr/bin/env python3
"""Generate TOOL01 source authority by inspecting an Eva2 source worktree."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any


SOURCE_CONTRACT = "EvaTool01SourceAuthority/v1"
ENTRYPOINT = {
    "path": "src/eva/adapters/model/pydantic_conversation.py",
    "symbol": "PydanticConversationModel",
}
REQUIRED_RETIRED_PATHS = {
    "src/eva/adapters/model/openai_responses_transport.py",
}
REQUIRED_RETIRED_SYMBOLS = {
    "eva.adapters.model.conversation:CodexConversationModel",
    "eva.adapters.model.openai_responses_transport:OpenAIResponsesTransport",
    "eva.adapters.model.openai_responses_transport:parse_conversation_sse",
}

_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_RUN_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,159}$")
_MAX_FILE_BYTES = 2_000_000
_MAX_TREE_BYTES = 30_000_000


class SourceAuthorityError(RuntimeError):
    """The requested source root or claimed authority is not trustworthy."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _module_name(relative: str) -> str:
    path = Path(relative)
    parts = path.with_suffix("").parts
    if not parts or parts[0] != "src":
        raise SourceAuthorityError("source path is outside src")
    module_parts = list(parts[1:])
    if module_parts[-1] == "__init__":
        module_parts.pop()
    return ".".join(module_parts)


def _definitions(tree: ast.AST, module: str) -> tuple[set[str], dict[str, list[str]]]:
    symbols: set[str] = set()
    classes: dict[str, list[str]] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.add(f"{module}:{node.name}")
        if isinstance(node, ast.ClassDef):
            classes.setdefault(node.name, []).append(node.name)
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.add(f"{module}:{node.name}.{member.name}")
    return symbols, classes


def _runtime_constructor_names(tree: ast.AST) -> set[str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            name = aliases.get(node.func.id, node.func.id)
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        else:
            continue
        if name.endswith("ConversationModel"):
            names.add(name)
    return names


def _inspect(root: Path) -> dict[str, Any]:
    resolved_root = root.resolve(strict=True)
    source_root = resolved_root / "src" / "eva"
    if not source_root.is_dir():
        raise SourceAuthorityError("source root must contain src/eva")
    files = sorted(source_root.rglob("*.py"))
    if not files:
        raise SourceAuthorityError("source tree contains no Python files")

    manifest: list[dict[str, object]] = []
    definitions: set[str] = set()
    class_paths: dict[str, list[str]] = {}
    class_methods: dict[tuple[str, str], set[str]] = {}
    trees: dict[str, ast.AST] = {}
    total_bytes = 0
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise SourceAuthorityError("source tree contains an unsafe Python path")
        relative = path.relative_to(resolved_root).as_posix()
        raw = path.read_bytes()
        if not raw or len(raw) > _MAX_FILE_BYTES:
            raise SourceAuthorityError(f"source file size is invalid: {relative}")
        total_bytes += len(raw)
        if total_bytes > _MAX_TREE_BYTES:
            raise SourceAuthorityError("source tree is too large")
        try:
            tree = ast.parse(raw, filename=relative)
        except (SyntaxError, UnicodeDecodeError) as error:
            raise SourceAuthorityError(
                f"source file is not valid Python: {relative}"
            ) from error
        trees[relative] = tree
        module = _module_name(relative)
        file_symbols, file_classes = _definitions(tree, module)
        definitions.update(file_symbols)
        for class_name in file_classes:
            class_paths.setdefault(class_name, []).append(relative)
        for node in getattr(tree, "body", []):
            if isinstance(node, ast.ClassDef):
                class_methods[(relative, node.name)] = {
                    member.name
                    for member in node.body
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
        manifest.append({"bytes": len(raw), "path": relative, "sha256": _sha256(raw)})

    tool_loops = [
        {"path": path, "symbol": symbol}
        for (path, symbol), methods in sorted(class_methods.items())
        if path.startswith("src/eva/adapters/model/")
        and ("generate" in methods or symbol == ENTRYPOINT["symbol"])
    ]
    runtime_path = "src/eva/bootstrap/runtime.py"
    runtime_tree = trees.get(runtime_path)
    runtime_names = (
        set() if runtime_tree is None else _runtime_constructor_names(runtime_tree)
    )
    runtime_owners: list[dict[str, str]] = []
    for name in sorted(runtime_names):
        paths = sorted(class_paths.get(name, []))
        if len(paths) == 1:
            runtime_owners.append({"path": paths[0], "symbol": name})
        else:
            runtime_owners.append({"path": runtime_path, "symbol": name})

    path_set = {row["path"] for row in manifest}
    forbidden_paths = sorted(path_set & REQUIRED_RETIRED_PATHS)
    forbidden_symbols = sorted(definitions & REQUIRED_RETIRED_SYMBOLS)
    entrypoint_paths = class_paths.get(ENTRYPOINT["symbol"], [])
    entrypoint = None
    if entrypoint_paths == [ENTRYPOINT["path"]]:
        entry_manifest = next(
            row for row in manifest if row["path"] == ENTRYPOINT["path"]
        )
        entrypoint = {**ENTRYPOINT, "sha256": entry_manifest["sha256"]}

    expected_owner = [{"path": ENTRYPOINT["path"], "symbol": ENTRYPOINT["symbol"]}]
    blockers: list[str] = []
    if entrypoint is None:
        blockers.append("tool_loop_entrypoint_missing_or_drifted")
    if tool_loops != expected_owner:
        blockers.append("tool_loop_count_or_owner_differs")
    if runtime_owners != expected_owner:
        blockers.append("runtime_owner_count_or_path_differs")
    blockers.extend(f"retired_path_present:{path}" for path in forbidden_paths)
    blockers.extend(f"retired_symbol_present:{symbol}" for symbol in forbidden_symbols)
    return {
        "blockers": sorted(blockers),
        "manifest": manifest,
        "scan": {
            "forbiddenPathMatches": forbidden_paths,
            "forbiddenSymbolMatches": forbidden_symbols,
            "nonEmptyToolLoops": tool_loops,
            "runtimeOwners": runtime_owners,
        },
        "sourceTreeSha256": _sha256(_canonical(manifest)),
        "status": "complete" if not blockers else "pending",
        "toolLoopEntrypoint": entrypoint,
    }


def generate_source_authority(
    root: Path, *, run_id: str, git_commit: str
) -> dict[str, Any]:
    """Inspect source and return a complete or explicitly pending authority."""
    if _RUN_ID.fullmatch(run_id) is None:
        raise SourceAuthorityError("run id is invalid")
    if _COMMIT.fullmatch(git_commit) is None:
        raise SourceAuthorityError("git commit is invalid")
    if current_commit(root) != git_commit:
        raise SourceAuthorityError("git commit differs from source worktree HEAD")
    inspected = _inspect(root)
    return {
        "blockers": inspected["blockers"],
        "contract": SOURCE_CONTRACT,
        "gitCommit": git_commit,
        "manifest": inspected["manifest"],
        "retiredPaths": sorted(REQUIRED_RETIRED_PATHS),
        "retiredSymbols": sorted(REQUIRED_RETIRED_SYMBOLS),
        "runId": run_id,
        "scan": inspected["scan"],
        "sourceTreeSha256": inspected["sourceTreeSha256"],
        "status": inspected["status"],
        "toolLoopEntrypoint": inspected["toolLoopEntrypoint"],
    }


def verify_source_authority(value: object, root: Path) -> dict[str, Any]:
    """Reinspect root and reject any source claim that differs byte-for-byte."""
    if not isinstance(value, dict):
        raise SourceAuthorityError("source authority must be an object")
    if value.get("contract") != SOURCE_CONTRACT:
        raise SourceAuthorityError("source authority contract differs")
    run_id = value.get("runId")
    git_commit = value.get("gitCommit")
    if not isinstance(run_id, str) or not isinstance(git_commit, str):
        raise SourceAuthorityError("source authority identity is invalid")
    expected = generate_source_authority(root, run_id=run_id, git_commit=git_commit)
    if value != expected:
        raise SourceAuthorityError("source authority differs from inspected worktree")
    return expected


def current_commit(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    commit = result.stdout.strip()
    if result.returncode != 0 or _COMMIT.fullmatch(commit) is None:
        raise SourceAuthorityError("could not resolve current git commit")
    return commit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--git-commit")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    try:
        commit = arguments.git_commit or current_commit(arguments.source_root)
        result = generate_source_authority(
            arguments.source_root,
            run_id=arguments.run_id,
            git_commit=commit,
        )
    except (OSError, SourceAuthorityError) as error:
        print(json.dumps({"error": str(error), "status": "failed"}, sort_keys=True))
        return 2
    raw = _canonical(result)
    if arguments.output is None:
        print(raw.decode(), end="")
    else:
        arguments.output.write_bytes(raw)
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
