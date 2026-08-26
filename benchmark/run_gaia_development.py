#!/usr/bin/env python3
"""Run a bounded GAIA development slice without reading or scoring gold answers.

This adapter deliberately accepts the official GAIA JSONL metadata only after
the owner has downloaded it under the dataset's access conditions.  It sends an
agent just the task id, question and an optional local attachment path.  The
official ``Final answer`` field is never sent to the agent, copied to an
artifact or used for scoring here.

The resulting ``answers.jsonl`` has the exact field pair accepted by GAIA's
official leaderboard: ``task_id`` and ``model_answer``.  It is a development
artifact, not a leaderboard submission and not an official score.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import time
from typing import NoReturn, Sequence


MAX_METADATA_BYTES = 25 * 1024 * 1024
MAX_QUESTION_CHARS = 100_000
MAX_ANSWER_CHARS = 100_000
MAX_AGENT_STDOUT_BYTES = 256 * 1024
MAX_AGENT_STDERR_BYTES = 64 * 1024
MAX_DEVELOPMENT_TASKS = 165
SAFE_RELATIVE_PATH = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-")


class RunError(ValueError):
    """The development run cannot produce a trustworthy artifact."""


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _safe_regular_file(path: Path, *, name: str, maximum_bytes: int | None = None) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RunError(f"{name} is unavailable") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RunError(f"{name} must be a regular non-symlink file")
    if maximum_bytes is not None and metadata.st_size > maximum_bytes:
        raise RunError(f"{name} exceeds its byte limit")


def _safe_attachment(root: Path, value: object, *, line: int) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not value or len(value) > 512:
        raise RunError(f"metadata line {line} file_path is invalid")
    if value.startswith("/") or ".." in value.split("/") or any(
        character not in SAFE_RELATIVE_PATH for character in value
    ):
        raise RunError(f"metadata line {line} file_path is unsafe")
    candidate = root / value
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise RunError(f"metadata line {line} file_path escapes attachment root") from error
    _safe_regular_file(candidate, name=f"metadata line {line} attachment")
    return str(candidate)


def _tasks(metadata_path: Path, attachment_root: Path, maximum: int) -> list[dict[str, str | None]]:
    _safe_regular_file(metadata_path, name="metadata", maximum_bytes=MAX_METADATA_BYTES)
    try:
        raw_lines = metadata_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise RunError("metadata is unreadable UTF-8") from error
    tasks: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(raw_lines, start=1):
        if not raw_line.strip():
            raise RunError(f"metadata line {line_number} is blank")
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError as error:
            raise RunError(f"metadata line {line_number} is invalid JSON") from error
        if not isinstance(row, dict):
            raise RunError(f"metadata line {line_number} is not an object")
        task_id = row.get("task_id")
        question = row.get("Question")
        if not isinstance(task_id, str) or not task_id or len(task_id) > 200:
            raise RunError(f"metadata line {line_number} task_id is invalid")
        if task_id in seen:
            raise RunError(f"metadata line {line_number} task_id is duplicated")
        if not isinstance(question, str) or not question or len(question) > MAX_QUESTION_CHARS:
            raise RunError(f"metadata line {line_number} Question is invalid")
        seen.add(task_id)
        tasks.append(
            {
                "task_id": task_id,
                "question": question,
                "attachment_path": _safe_attachment(
                    attachment_root, row.get("file_path"), line=line_number
                ),
            }
        )
        if len(tasks) == maximum:
            break
    if not tasks:
        raise RunError("metadata contains no tasks")
    return tasks


def _new_staging_directory(target: Path) -> Path:
    if target.exists() or target.is_symlink():
        raise RunError("output directory already exists")
    if not target.parent.is_dir():
        raise RunError("output parent directory is unavailable")
    temporary = target.parent / f".{target.name}.staging-{secrets.token_hex(12)}"
    temporary.mkdir(mode=0o700)
    return temporary


def _write_private(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _agent_answer(
    command: Sequence[str], task: dict[str, str | None], timeout_seconds: int
) -> tuple[str, float]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(command),
            input=_canonical_bytes(task),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RunError("agent did not complete this task") from error
    duration_seconds = time.monotonic() - started
    if len(completed.stdout) > MAX_AGENT_STDOUT_BYTES or len(completed.stderr) > MAX_AGENT_STDERR_BYTES:
        raise RunError("agent output exceeds its byte limit")
    if completed.returncode != 0:
        raise RunError("agent rejected this task")
    try:
        response = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RunError("agent output is not JSON") from error
    if not isinstance(response, dict) or set(response) != {"model_answer"}:
        raise RunError("agent output must contain exactly model_answer")
    answer = response["model_answer"]
    if not isinstance(answer, str) or not answer or len(answer) > MAX_ANSWER_CHARS:
        raise RunError("agent model_answer is invalid")
    return answer, duration_seconds


def run(
    *,
    metadata_path: Path,
    attachment_root: Path,
    output_directory: Path,
    maximum_tasks: int,
    timeout_seconds: int,
    command: Sequence[str],
) -> dict[str, object]:
    """Create a new bounded GAIA development artifact or fail without a receipt."""

    if not 1 <= maximum_tasks <= MAX_DEVELOPMENT_TASKS:
        raise RunError(f"maximum tasks must be between 1 and {MAX_DEVELOPMENT_TASKS}")
    if not 1 <= timeout_seconds <= 3_600:
        raise RunError("timeout seconds must be between 1 and 3600")
    if not command or any(not part for part in command):
        raise RunError("agent command is required")
    _safe_regular_file(Path(__file__), name="adapter")
    _safe_regular_file(attachment_root, name="attachment root") if attachment_root.is_file() else None
    if not attachment_root.is_dir() or attachment_root.is_symlink():
        raise RunError("attachment root must be a directory")
    tasks = _tasks(metadata_path, attachment_root, maximum_tasks)
    staging = _new_staging_directory(output_directory)
    try:
        answers: list[bytes] = []
        trace: list[bytes] = []
        for task in tasks:
            answer, duration_seconds = _agent_answer(command, task, timeout_seconds)
            answers.append(
                _canonical_bytes({"model_answer": answer, "task_id": task["task_id"]}) + b"\n"
            )
            trace.append(
                _canonical_bytes(
                    {
                        "durationMilliseconds": round(duration_seconds * 1000),
                        "taskId": task["task_id"],
                        "status": "completed",
                    }
                )
                + b"\n"
            )
        answers_path = staging / "answers.jsonl"
        trace_path = staging / "adapter-trace.jsonl"
        _write_private(answers_path, b"".join(answers))
        _write_private(trace_path, b"".join(trace))
        receipt = {
            "adapterCommandSha256": hashlib.sha256(_canonical_bytes(list(command))).hexdigest(),
            "adapterSha256": _sha256_path(Path(__file__)),
            "answersSha256": _sha256_path(answers_path),
            "contract": "EvaGaiaDevelopmentRun/v1",
            "metadataSha256": _sha256_path(metadata_path),
            "nonClaims": [
                "No official GAIA scorer was run.",
                "No official score or leaderboard submission exists.",
                "No Final answer field was sent to the agent or written to these artifacts.",
                "The adapter trace is not a complete agent tool trace.",
            ],
            "officialScore": None,
            "status": "development_completed",
            "taskCount": len(tasks),
            "traceSha256": _sha256_path(trace_path),
        }
        receipt_path = staging / "receipt.json"
        _write_private(receipt_path, _canonical_bytes(receipt) + b"\n")
        os.replace(staging, output_directory)
        return receipt
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-jsonl", type=Path, required=True)
    parser.add_argument("--attachments-root", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--max-tasks", type=int, required=True)
    parser.add_argument("--timeout-seconds", type=int, required=True)
    parser.add_argument(
        "--agent-command",
        nargs=argparse.REMAINDER,
        required=True,
        help="command after --; receives one safe task JSON object on stdin and writes one JSON object with model_answer",
    )
    return parser.parse_args()


def _die(message: str) -> NoReturn:
    raise SystemExit(f"gaia-development: {message}")


def main() -> int:
    arguments = _arguments()
    command = arguments.agent_command
    if command[:1] == ["--"]:
        command = command[1:]
    try:
        receipt = run(
            metadata_path=arguments.metadata_jsonl,
            attachment_root=arguments.attachments_root,
            output_directory=arguments.output_directory,
            maximum_tasks=arguments.max_tasks,
            timeout_seconds=arguments.timeout_seconds,
            command=command,
        )
    except RunError as error:
        _die(str(error))
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
