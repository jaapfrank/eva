from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

import pytest


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location(
    "tool01_acceptance", HERE / "check_acceptance.py"
)
assert SPEC is not None and SPEC.loader is not None
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)
source_generator = importlib.import_module("generate_source_authority")


RUN_ID = "tool01-fixture-001"
NONCE = "a1b2c3d4e5f6"
HISTORY = "1" * 64
MESSAGE = "message-001"
OPERATION = "operation-001"
EFFECT_CALL = "effect-call-001"
READBACK_CALL = "readback-call-001"


def _admission_sha() -> str:
    value = {
        "callId": EFFECT_CALL,
        "messageId": MESSAGE,
        "operationId": OPERATION,
        "toolName": f"adjust_comfort_{NONCE}",
    }
    return hashlib.sha256(
        (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()


def _event(sequence: int, kind: str, process: str, **data: Any) -> dict[str, Any]:
    return {"data": data, "kind": kind, "processId": process, "sequence": sequence}


def _artifacts() -> dict[str, dict[str, Any]]:
    effect = f"adjust_comfort_{NONCE}"
    readback = f"read_comfort_{NONCE}"
    observe = f"observe_space_{NONCE}"
    distractor = f"find_music_{NONCE}"
    catalog = {
        "contract": checker.CATALOG_CONTRACT,
        "discoveredByProcess": "p1",
        "discoverySequence": 2,
        "nonce": NONCE,
        "providers": [
            {
                "providerId": "space-provider",
                "tools": [
                    {
                        "description": "Observe a named space.",
                        "inputSchema": {"type": "object"},
                        "name": observe,
                        "role": "observe",
                    },
                    {
                        "description": "Adjust comfort in a named space.",
                        "inputSchema": {"type": "object"},
                        "name": effect,
                        "role": "effect",
                    },
                    {
                        "description": "Read comfort independently.",
                        "inputSchema": {"type": "object"},
                        "name": readback,
                        "role": "readback",
                    },
                ],
            },
            {
                "providerId": "music-provider",
                "tools": [
                    {
                        "description": "Find music without changing state.",
                        "inputSchema": {"type": "object"},
                        "name": distractor,
                        "role": "distractor",
                    },
                ],
            },
        ],
        "runId": RUN_ID,
        "staticSourceScan": {
            "matches": [],
            "searchedToolNames": sorted([effect, readback, observe, distractor]),
        },
    }
    trajectory = {
        "budget": checker.BUDGET,
        "contract": checker.TRAJECTORY_CONTRACT,
        "events": [
            _event(1, "process_started", "p1"),
            _event(2, "catalog_discovered", "p1"),
            _event(
                3,
                "deferred_requests",
                "p1",
                callId=EFFECT_CALL,
                historySha256=HISTORY,
                messageId=MESSAGE,
                operationId=OPERATION,
                toolName=effect,
            ),
            _event(
                4,
                "action_port_execute",
                "p1",
                callId=EFFECT_CALL,
                messageId=MESSAGE,
                operationId=OPERATION,
                owner="ConversationActionPort",
                toolName=effect,
            ),
            _event(9, "process_killed", "p1", phase="after_effect_before_readback"),
            _event(10, "process_started", "p2"),
            _event(11, "catalog_discovered", "p2"),
            _event(
                15,
                "deferred_results",
                "p2",
                historySha256=HISTORY,
                inputKind="DeferredToolResults",
                receiptSource="durable_ledger",
            ),
            _event(16, "final_answer", "p2"),
            _event(17, "process_started", "p3"),
            _event(
                18, "replay_received", "p3", messageId=MESSAGE, operationId=OPERATION
            ),
            _event(19, "replay_result", "p3"),
        ],
        "model": checker.MODEL,
        "prompt": checker.PROMPT,
        "reasoning": checker.REASONING,
        "runId": RUN_ID,
    }
    ledger = {
        "contract": checker.LEDGER_CONTRACT,
        "events": [
            _event(
                5,
                "TOOL_REQUEST_ADMITTED",
                "p1",
                callId=EFFECT_CALL,
                eventSha256=_admission_sha(),
                messageId=MESSAGE,
                operationId=OPERATION,
                toolName=effect,
            ),
            _event(6, "PROVIDER_CALL_STARTED", "p1"),
            _event(8, "PROVIDER_ACCEPTED", "p1"),
            _event(12, "READBACK_CALL_STARTED", "p2"),
            _event(14, "STATE_VERIFIED", "p2"),
        ],
        "runId": RUN_ID,
    }
    provider = {
        "calls": [
            _event(
                7,
                "effect",
                "p1",
                admissionEventSha256=_admission_sha(),
                callId=EFFECT_CALL,
                messageId=MESSAGE,
                operationId=OPERATION,
                toolName=effect,
            ),
            _event(
                13,
                "readback",
                "p2",
                callId=READBACK_CALL,
                messageId=MESSAGE,
                operationId=OPERATION,
                toolName=readback,
            ),
        ],
        "contract": checker.PROVIDER_CONTRACT,
        "finalState": {
            "comfortActive": True,
            "space": "overkapping",
            "temperatureC": 20.0,
        },
        "runId": RUN_ID,
    }
    restart = {
        "contract": checker.RESTART_CONTRACT,
        "processes": [
            {
                "processId": "p1",
                "startSequence": 1,
                "stopKind": "killed",
                "stopSequence": 9,
            },
            {
                "processId": "p2",
                "startSequence": 10,
                "stopKind": "clean",
                "stopSequence": 16,
            },
            {
                "processId": "p3",
                "startSequence": 17,
                "stopKind": "clean",
                "stopSequence": 19,
            },
        ],
        "runId": RUN_ID,
    }
    return {
        "catalog": catalog,
        "ledger": ledger,
        "providerAudit": provider,
        "restartAudit": restart,
        "trajectory": trajectory,
    }


def _source_tree(
    root: Path,
    *,
    entrypoint_path: str = "src/eva/adapters/model/pydantic_conversation.py",
    entrypoint_symbol: str = "PydanticConversationModel",
    legacy: bool = False,
    second_owner: bool = False,
) -> Path:
    model_path = root / entrypoint_path
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_text(
        f"class {entrypoint_symbol}:\n    def generate(self):\n        return 'ok'\n",
        encoding="utf-8",
    )
    runtime_path = root / "src/eva/bootstrap/runtime.py"
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_lines = [
        f"from eva.adapters.model.{model_path.stem} import {entrypoint_symbol}",
        f"model = {entrypoint_symbol}()",
    ]
    if second_owner:
        other = root / "src/eva/adapters/model/other_conversation.py"
        other.write_text(
            "class OtherConversationModel:\n    def generate(self):\n        return 'other'\n",
            encoding="utf-8",
        )
        runtime_lines.extend(
            [
                "from eva.adapters.model.other_conversation import OtherConversationModel",
                "other = OtherConversationModel()",
            ]
        )
    if legacy:
        conversation = root / "src/eva/adapters/model/conversation.py"
        conversation.write_text(
            "class CodexConversationModel:\n    def generate(self):\n        return 'legacy'\n",
            encoding="utf-8",
        )
        transport = root / "src/eva/adapters/model/openai_responses_transport.py"
        transport.write_text(
            "class OpenAIResponsesTransport:\n    pass\n\n"
            "def parse_conversation_sse(events):\n    return events\n",
            encoding="utf-8",
        )
        runtime_lines.extend(
            [
                "from eva.adapters.model.conversation import CodexConversationModel",
                "legacy = CodexConversationModel()",
            ]
        )
    runtime_path.write_text("\n".join(runtime_lines) + "\n", encoding="utf-8")
    return root


def _source(root: Path) -> dict[str, Any]:
    return source_generator.generate_source_authority(
        root,
        run_id=RUN_ID,
        git_commit="a" * 40,
    )


def _write_json(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(raw)
    return {
        "bytes": len(raw),
        "path": path.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _bundle(
    tmp_path: Path,
    *,
    complete_source: bool = False,
    mutate=None,
    source_root: Path | None = None,
) -> Path:
    artifacts = copy.deepcopy(_artifacts())
    if mutate is not None:
        mutate(artifacts)
    references = {
        name: _write_json(tmp_path / f"{name}.json", value)
        for name, value in artifacts.items()
    }
    source_reference = None
    source_status = "pending"
    if complete_source:
        if source_root is None:
            source_root = _source_tree(tmp_path / "source-worktree")
        source_value = _source(source_root)
        source_reference = _write_json(tmp_path / "source.json", source_value)
        source_status = source_value["status"]
    bundle = {
        "artifacts": references,
        "contract": checker.BUNDLE_CONTRACT,
        "runId": RUN_ID,
        "schemaVersion": 1,
        "sourceAuthority": {
            "artifact": source_reference,
            "status": source_status,
        },
    }
    path = tmp_path / "bundle.json"
    _write_json(path, bundle)
    return path


def test_runtime_passes_while_source_authority_remains_pending(tmp_path: Path) -> None:
    result = checker.check(_bundle(tmp_path))
    assert result["runtimeStatus"] == "runtime_passed"
    assert result["sourceAuthorityStatus"] == "source_authority_pending"
    assert result["replacementStatus"] == "source_authority_pending"


def test_complete_source_authority_promotes_replacement(tmp_path: Path) -> None:
    source_root = _source_tree(tmp_path / "source-worktree")
    result = checker.check(
        _bundle(tmp_path, complete_source=True, source_root=source_root),
        source_root=source_root,
    )
    assert result["runtimeStatus"] == "runtime_passed"
    assert result["sourceAuthorityStatus"] == "source_authority_passed"
    assert result["replacementStatus"] == "replacement_passed"


def test_second_mutation_fails_runtime_even_if_source_is_complete(
    tmp_path: Path,
) -> None:
    def mutate(artifacts):
        call = copy.deepcopy(artifacts["providerAudit"]["calls"][0])
        call["sequence"] = 12
        call["processId"] = "p2"
        artifacts["providerAudit"]["calls"].insert(1, call)

    source_root = _source_tree(tmp_path / "source-worktree")
    result = checker.check(
        _bundle(
            tmp_path,
            complete_source=True,
            mutate=mutate,
            source_root=source_root,
        ),
        source_root=source_root,
    )
    assert result["runtimeStatus"] == "runtime_failed"
    assert result["replacementStatus"] == "failed"


def test_readback_must_be_a_separate_call(tmp_path: Path) -> None:
    def mutate(artifacts):
        artifacts["providerAudit"]["calls"][1]["data"]["callId"] = EFFECT_CALL

    result = checker.check(_bundle(tmp_path, mutate=mutate))
    assert "readback_is_not_separate" in result["runtimeViolations"]


def test_provider_requires_the_durable_admission_hash(tmp_path: Path) -> None:
    def mutate(artifacts):
        artifacts["providerAudit"]["calls"][0]["data"]["admissionEventSha256"] = (
            "0" * 64
        )

    result = checker.check(_bundle(tmp_path, mutate=mutate))
    assert "provider_admission_attestation_differs" in result["runtimeViolations"]


def test_runtime_tool_name_must_be_absent_before_discovery(tmp_path: Path) -> None:
    def mutate(artifacts):
        artifacts["catalog"]["staticSourceScan"]["matches"] = [
            f"adjust_comfort_{NONCE}"
        ]

    with pytest.raises(checker.Tool01Error, match="absent before discovery"):
        checker.check(_bundle(tmp_path, mutate=mutate))


def test_artifact_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    bundle_path = _bundle(tmp_path)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["artifacts"]["catalog"]["sha256"] = "0" * 64
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(checker.Tool01Error, match="binding differs"):
        checker.check(bundle_path)


def test_artifact_symlink_cannot_escape_bundle(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    reference = _write_json(outside, _artifacts()["catalog"])
    bundle_path = _bundle(tmp_path)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    link = tmp_path / "escaped-catalog.json"
    link.symlink_to(outside)
    bundle["artifacts"]["catalog"] = {**reference, "path": link.name}
    _write_json(bundle_path, bundle)
    with pytest.raises(checker.Tool01Error, match="escapes the bundle"):
        checker.check(bundle_path)


def test_self_asserted_pass_is_rejected(tmp_path: Path) -> None:
    bundle_path = _bundle(tmp_path)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["passed"] = True
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(checker.Tool01Error, match="self-asserted pass"):
        checker.check(bundle_path)


def test_second_source_runtime_owner_keeps_authority_pending(tmp_path: Path) -> None:
    source_root = _source_tree(tmp_path / "source-worktree", second_owner=True)
    source = _source(source_root)
    assert source["status"] == "pending"
    assert "runtime_owner_count_or_path_differs" in source["blockers"]
    assert "tool_loop_count_or_owner_differs" in source["blockers"]

    bundle_path = _bundle(
        tmp_path,
        complete_source=True,
        source_root=source_root,
    )
    result = checker.check(bundle_path, source_root=source_root)
    assert result["runtimeStatus"] == "runtime_passed"
    assert result["sourceAuthorityStatus"] == "source_authority_pending"
    assert result["replacementStatus"] == "source_authority_pending"


def test_entrypoint_path_drift_keeps_authority_pending(tmp_path: Path) -> None:
    source_root = _source_tree(
        tmp_path / "source-worktree",
        entrypoint_path="src/eva/adapters/model/moved_conversation.py",
    )
    source = _source(source_root)
    assert source["status"] == "pending"
    assert "tool_loop_entrypoint_missing_or_drifted" in source["blockers"]
    assert "runtime_owner_count_or_path_differs" in source["blockers"]


def test_entrypoint_symbol_drift_keeps_authority_pending(tmp_path: Path) -> None:
    source_root = _source_tree(
        tmp_path / "source-worktree",
        entrypoint_symbol="RenamedConversationModel",
    )
    source = _source(source_root)
    assert source["status"] == "pending"
    assert "tool_loop_entrypoint_missing_or_drifted" in source["blockers"]
    assert "runtime_owner_count_or_path_differs" in source["blockers"]


def test_forged_legacy_removal_is_rejected_against_worktree(tmp_path: Path) -> None:
    source_root = _source_tree(tmp_path / "source-worktree", legacy=True)
    source = _source(source_root)
    assert source["status"] == "pending"
    assert source["scan"]["forbiddenPathMatches"]
    assert source["scan"]["forbiddenSymbolMatches"]

    forged = copy.deepcopy(source)
    forged["scan"]["forbiddenPathMatches"] = []
    forged["scan"]["forbiddenSymbolMatches"] = []
    forged["blockers"] = [
        blocker for blocker in forged["blockers"] if not blocker.startswith("retired_")
    ]
    with pytest.raises(
        source_generator.SourceAuthorityError,
        match="differs from inspected worktree",
    ):
        source_generator.verify_source_authority(forged, source_root)


def test_acceptance_rejects_forged_source_artifact(tmp_path: Path) -> None:
    source_root = _source_tree(tmp_path / "source-worktree", legacy=True)
    bundle_path = _bundle(
        tmp_path,
        complete_source=True,
        source_root=source_root,
    )
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    source_path = tmp_path / bundle["sourceAuthority"]["artifact"]["path"]
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source["scan"]["forbiddenPathMatches"] = []
    source["scan"]["forbiddenSymbolMatches"] = []
    bundle["sourceAuthority"]["artifact"] = _write_json(source_path, source)
    _write_json(bundle_path, bundle)
    with pytest.raises(checker.Tool01Error, match="differs from inspected worktree"):
        checker.check(bundle_path, source_root=source_root)
