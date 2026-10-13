#!/usr/bin/env python3
"""Run the bounded Codex/Pydantic source-candidate compatibility probe.

The probe intentionally owns no credential parser and no provider transport.  It
uses the public conversation adapter and its existing access-lease boundary.
Output is a single canonical, secret-free receipt; prompts and answers are only
represented by hashes.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import hashlib
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.application.conversation import (
    ConversationActionReceipt,
    ConversationModelResult,
    ConversationToolDescriptor,
    ConversationTurn,
    IdentityProfile,
    stream_conversation_text,
)
from eva.application.memory import PresenceMemory
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.contracts.memory import MemoryRecall
from eva.contracts.runtime_budget import RuntimeResourceBudget


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "EvaCodexSourceCompatibilityProbe/v1"
NOW = "2026-08-31T12:00:00Z"
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE_PATHS = (
    "src/eva/adapters/model/openai_codex_provider.py",
    "src/eva/adapters/model/pydantic_conversation.py",
    "src/eva/application/conversation.py",
    "tools/probe_codex_source_compatibility.py",
)
_STREAM_PROMPT = "Antwoord exact met compatibility-stream-ok en niets anders."
_STREAM_ANSWER = "compatibility-stream-ok"
_TOOL_PROMPT = (
    "Selecteer nu zonder uitvoering de beschikbare compatibiliteitsproeftool."
)
_FINAL_ANSWER = "compatibility-receipt-ok"
_TOOL_ARGUMENTS = {"capability": "streaming-deferred-tools"}
_TOOL = ConversationToolDescriptor(
    name="probe_candidate_capability",
    description=(
        "Select one source-candidate capability for compatibility inspection; "
        "this deferred tool performs no provider or external effect."
    ),
    input_schema={
        "additionalProperties": False,
        "properties": {
            "capability": {
                "const": _TOOL_ARGUMENTS["capability"],
                "type": "string",
            }
        },
        "required": ["capability"],
        "type": "object",
    },
    completion_guidance=(
        f"After the synthetic receipt, answer exactly {_FINAL_ANSWER}."
    ),
)


class CompatibilityProbeError(RuntimeError):
    """A sanitized, machine-categorizable probe failure."""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(status)


GitRunner = Callable[[tuple[str, ...]], str]


def _probe_budget() -> RuntimeResourceBudget:
    values = {
        "background_reflections_per_installation_day": 0,
        "background_reflections_per_person_day": 0,
        "contract": "EvaInstallerBudget/v2",
        "external_monthly_spend_cap_eur_cents": 0,
        "max_input_tokens": 9_000,
        "max_output_tokens": 500,
        "memory_bytes_per_group": 0,
        "memory_bytes_per_installation": 1_048_576,
        "memory_bytes_per_person": 1_048_576,
        "memory_bytes_per_project": 0,
        "memory_records_per_group": 0,
        "memory_records_per_installation": 100,
        "memory_records_per_person": 100,
        "memory_records_per_project": 0,
        "profile_context_tokens": 100,
        "recall_context_tokens": 2_000,
        "retention_days": 7,
        "retrieval_nodes": 20,
        "unknown_cost_policy": "block_autonomous_spend",
    }
    return RuntimeResourceBudget(
        tenant_id="tenant:codex-source-compatibility",
        installation_id="installation:codex-source-compatibility",
        source_budget_sha256=hashlib.sha256(canonical_json_bytes(values)).hexdigest(),
        recall_context_tokens=2_000,
        retrieval_nodes=20,
        max_input_tokens=9_000,
        max_output_tokens=500,
        profile_context_tokens=100,
        memory_records_per_person=100,
        memory_bytes_per_person=1_048_576,
        memory_records_per_group=0,
        memory_bytes_per_group=0,
        memory_records_per_project=0,
        memory_bytes_per_project=0,
        memory_records_per_installation=100,
        memory_bytes_per_installation=1_048_576,
        retention_days=7,
        background_reflections_per_person_day=0,
        background_reflections_per_installation_day=0,
        external_monthly_spend_cap_eur_cents=0,
    )


def _run_git(command: tuple[str, ...]) -> str:
    try:
        completed = subprocess.run(
            command,
            check=True,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise CompatibilityProbeError("source_unavailable") from error
    return completed.stdout


def source_candidate_sha256() -> str:
    rows: list[dict[str, str]] = []
    for relative in _SOURCE_PATHS:
        path = ROOT / relative
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise CompatibilityProbeError("source_unavailable") from error
        rows.append({"path": relative, "sha256": hashlib.sha256(raw).hexdigest()})
    return hashlib.sha256(canonical_json_bytes(rows)).hexdigest()


def _verify_source_candidate(
    *,
    expected_commit: str,
    expected_source_sha256: str,
    git_runner: GitRunner,
) -> None:
    if _COMMIT.fullmatch(expected_commit) is None:
        raise CompatibilityProbeError("expected_commit_invalid")
    if _SHA256.fullmatch(expected_source_sha256) is None:
        raise CompatibilityProbeError("expected_source_hash_invalid")
    head = git_runner(("git", "rev-parse", "HEAD")).strip()
    if head != expected_commit:
        raise CompatibilityProbeError("source_commit_differs")
    dirty = git_runner(
        ("git", "status", "--porcelain=v1", "--untracked-files=all")
    )
    if dirty.strip():
        raise CompatibilityProbeError("source_not_clean")
    if source_candidate_sha256() != expected_source_sha256:
        raise CompatibilityProbeError("source_hash_differs")


def _memory() -> PresenceMemory:
    return PresenceMemory(
        MemoryRecall(
            schema_version=1,
            contract="MemoryRecall/v1",
            recall_id="recall:codex-source-compatibility",
            query_purpose="conversation_reply",
            tenant_id="tenant:codex-source-compatibility",
            principal_id="principal:codex-source-compatibility",
            conversation_id="conversation:codex-source-compatibility",
            project_id=None,
            requested_valid_at=NOW,
            admitted_fact_ids_sha256=canonical_sha256([]),
            selected=[],
            excluded_reasons={},
            projection_version="codex-source-compatibility-v1",
            scope_sha256=canonical_sha256(
                {
                    "kind": "person",
                    "principal_id": "principal:codex-source-compatibility",
                }
            ),
            created_at=NOW,
        ),
        (),
    )


def _turn(message_id: str, text: str) -> ConversationTurn:
    return ConversationTurn.private(
        tenant_id="tenant:codex-source-compatibility",
        installation_id="installation:codex-source-compatibility",
        principal_id="principal:codex-source-compatibility",
        conversation_id="conversation:codex-source-compatibility",
        message_id=message_id,
        text=text,
        occurred_at=NOW,
    )


def _identity() -> IdentityProfile:
    return IdentityProfile(
        "Eva",
        "Bounded source-candidate compatibility identity.",
        ("Return only the requested compatibility result.",),
        80,
    )


def _usage(result: ConversationModelResult) -> dict[str, str | int | None]:
    return result.usage.to_wire()


def run_probe(
    *,
    auth_file: Path,
    expected_commit: str,
    expected_source_sha256: str,
    model_backend: Any | None = None,
    git_runner: GitRunner = _run_git,
) -> dict[str, Any]:
    """Run exactly three public-adapter model phases and return a safe receipt."""

    _verify_source_candidate(
        expected_commit=expected_commit,
        expected_source_sha256=expected_source_sha256,
        git_runner=git_runner,
    )
    resource_budget = _probe_budget()
    first_model = PydanticConversationModel(
        auth_file=auth_file,
        conversation_tools=(_TOOL,),
        model_backend=model_backend,
        resource_budget=resource_budget,
    )
    if not first_model.credential_status().ready:
        raise CompatibilityProbeError("lease_invalid")

    deltas: list[str] = []
    with stream_conversation_text(deltas.append):
        streamed = first_model.generate_tool_free_text(
            prompt=_STREAM_PROMPT,
            instructions="Return the exact requested compatibility marker.",
        )
    if streamed.text != _STREAM_ANSWER or "".join(deltas) != _STREAM_ANSWER:
        raise CompatibilityProbeError("stream_result_differs")

    selected = first_model.generate(
        _turn("message:codex-source-tool", _TOOL_PROMPT),
        _identity(),
        _memory(),
        (),
        available_actions=(_TOOL.name,),
    )
    call = selected.action_call
    if (
        selected.text is not None
        or call is None
        or call.name != _TOOL.name
        or call.arguments != _TOOL_ARGUMENTS
    ):
        raise CompatibilityProbeError("deferred_tool_differs")

    synthetic_receipt = ConversationActionReceipt(
        call,
        {
            "contract": "EvaCodexCompatibilitySyntheticReceipt/v1",
            "effect_executed": False,
            "status": "synthetic_only",
        },
    )
    fresh_model = PydanticConversationModel(
        auth_file=auth_file,
        # Keep the immutable descriptor so the fresh process can recover the
        # receipt-specific completion guidance.  No action is admitted below,
        # so Pydantic still offers exactly zero executable tools.
        conversation_tools=(_TOOL,),
        model_backend=model_backend,
        resource_budget=resource_budget,
    )
    completed = fresh_model.generate(
        _turn("message:codex-source-receipt", _TOOL_PROMPT),
        _identity(),
        _memory(),
        (),
        completed_action=synthetic_receipt,
    )
    if completed.action_call is not None or completed.text != _FINAL_ANSWER:
        raise CompatibilityProbeError("receipt_continuation_differs")
    model_attempts = (
        streamed.usage.model_attempts,
        selected.usage.model_attempts,
        completed.usage.model_attempts,
    )
    if any(value is None for value in model_attempts):
        raise CompatibilityProbeError("usage_unmeasured")

    return {
        "contract": CONTRACT,
        "model_attempt_count": sum(value or 0 for value in model_attempts),
        "phase_count": 3,
        "phases": {
            "deferred_tool": {
                "call_id_sha256": hashlib.sha256(call.call_id.encode()).hexdigest(),
                "status": "selected_not_executed",
                "tool_name": call.name,
                "usage": _usage(selected),
                "validated_arguments": call.arguments,
            },
            "receipt_continuation": {
                "answer_sha256": hashlib.sha256(
                    completed.text.encode("utf-8")
                ).hexdigest(),
                "offered_tool_count": 0,
                "status": "synthetic_receipt_continued",
                "usage": _usage(completed),
            },
            "streaming_text": {
                "answer_sha256": hashlib.sha256(
                    streamed.text.encode("utf-8")
                ).hexdigest(),
                "delta_count": len(deltas),
                "status": "exact",
                "usage": _usage(streamed),
            },
        },
        "resource_budget": {
            "max_input_tokens": resource_budget.max_input_tokens,
            "max_output_tokens": resource_budget.max_output_tokens,
            "source_sha256": resource_budget.source_budget_sha256,
            "unknown_cost_status": "blocked",
        },
        "source_candidate_sha256": expected_source_sha256,
        "source_commit": expected_commit,
        "status": "passed",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auth-file", required=True, type=Path)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        receipt = run_probe(
            auth_file=args.auth_file,
            expected_commit=args.expected_commit,
            expected_source_sha256=args.expected_source_sha256,
        )
    except CompatibilityProbeError as error:
        receipt = {"contract": CONTRACT, "status": error.status}
        print(canonical_json_bytes(receipt).decode("utf-8"), end="")
        return 2
    except Exception:
        receipt = {"contract": CONTRACT, "status": "probe_failed"}
        print(canonical_json_bytes(receipt).decode("utf-8"), end="")
        return 2
    print(canonical_json_bytes(receipt).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
