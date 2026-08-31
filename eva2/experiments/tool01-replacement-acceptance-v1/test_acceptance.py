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
SPEC = importlib.util.spec_from_file_location(
    "tool01_acceptance", HERE / "check_acceptance.py"
)
assert SPEC is not None and SPEC.loader is not None
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)


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


def _source() -> dict[str, Any]:
    owner = {"path": checker.ENTRYPOINT["path"], "symbol": checker.ENTRYPOINT["symbol"]}
    return {
        "contract": checker.SOURCE_CONTRACT,
        "gitCommit": "a" * 40,
        "retiredPaths": sorted(checker.REQUIRED_RETIRED_PATHS),
        "retiredSymbols": sorted(checker.REQUIRED_RETIRED_SYMBOLS),
        "runId": RUN_ID,
        "scan": {
            "forbiddenPathMatches": [],
            "forbiddenSymbolMatches": [],
            "nonEmptyToolLoops": [owner],
            "runtimeOwners": [owner],
        },
        "toolLoopEntrypoint": {**owner, "sha256": "b" * 64},
    }


def _write_json(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(raw)
    return {
        "bytes": len(raw),
        "path": path.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _bundle(tmp_path: Path, *, complete_source: bool = False, mutate=None) -> Path:
    artifacts = copy.deepcopy(_artifacts())
    if mutate is not None:
        mutate(artifacts)
    references = {
        name: _write_json(tmp_path / f"{name}.json", value)
        for name, value in artifacts.items()
    }
    source_reference = (
        _write_json(tmp_path / "source.json", _source()) if complete_source else None
    )
    bundle = {
        "artifacts": references,
        "contract": checker.BUNDLE_CONTRACT,
        "runId": RUN_ID,
        "schemaVersion": 1,
        "sourceAuthority": {
            "artifact": source_reference,
            "status": "complete" if complete_source else "pending",
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
    result = checker.check(_bundle(tmp_path, complete_source=True))
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

    result = checker.check(_bundle(tmp_path, complete_source=True, mutate=mutate))
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


def test_duplicate_source_runtime_owner_blocks_replacement(tmp_path: Path) -> None:
    bundle_path = _bundle(tmp_path, complete_source=True)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    source_path = tmp_path / bundle["sourceAuthority"]["artifact"]["path"]
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source["scan"]["runtimeOwners"].append(
        {"path": "src/eva/other.py", "symbol": "OtherLoop"}
    )
    bundle["sourceAuthority"]["artifact"] = _write_json(source_path, source)
    _write_json(bundle_path, bundle)
    result = checker.check(bundle_path)
    assert result["runtimeStatus"] == "runtime_passed"
    assert "runtime_owner_count_differs" in result["sourceAuthorityViolations"]
    assert result["replacementStatus"] == "failed"
