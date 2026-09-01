from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("tool01_v2_acceptance", HERE / "check_acceptance.py")
assert SPEC is not None and SPEC.loader is not None
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)

RUN_ID = "tool01-v2-fixture-001"
NONCE = "a1b2c3d4e5f6"
IMAGE_ID = "sha256:" + "a" * 64
SOURCE_COMMIT = "b" * 40
P1 = "1" * 64
P2 = "2" * 64
P3 = "3" * 64
CALL_ID = "effect-call-001"
MESSAGE_ID = "message-001"
OPERATION_ID = "operation-001"
HISTORY_SHA = "4" * 64
RECEIPT_SHA = "5" * 64
PROVIDER_INSTANCE = "provider-instance-001"
ARGUMENTS = {"space": "overkapping", "targetTemperatureC": 20}
EXPECTED = {"comfortActive": True, "space": "overkapping", "temperatureC": 20}


def _file_reference(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(raw)
    return {"bytes": len(raw), "path": path.name, "sha256": hashlib.sha256(raw).hexdigest()}


def _payload(data: dict[str, Any]) -> dict[str, Any]:
    return {"checkpoint": {"projection": "fixture"}, "data": data}


def _timeline_event(
    sequence: int,
    event_type: str,
    container_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    payload = _payload(data)
    return {
        "eventType": event_type,
        "firstObservedByContainerId": container_id,
        "operationId": OPERATION_ID,
        "payload": payload,
        "payloadSha256": checker._sha256(payload),
        "sequence": sequence,
    }


def _framework_event(
    sequence: int, kind: str, container_id: str, **data: Any
) -> dict[str, Any]:
    return {"containerId": container_id, "data": data, "kind": kind, "sequence": sequence}


def _artifacts(
    *, image_id: str = IMAGE_ID, source_commit: str = SOURCE_COMMIT
) -> dict[str, dict[str, Any]]:
    effect = f"adjust_comfort_{NONCE}"
    readback = f"read_comfort_{NONCE}"
    distractor = f"find_music_{NONCE}"
    candidate = {
        "contract": checker.CANDIDATE_CONTRACT,
        "imageId": image_id,
        "proofHarnessSha256": "f" * 64,
        "runId": RUN_ID,
        "sourceCommitLabel": source_commit,
    }
    crash = {
        "containers": [
            {
                "containerId": P1,
                "imageId": image_id,
                "processId": "p1",
                "sourceCommitLabel": source_commit,
                "state": {"exitCode": 137, "oomKilled": False, "status": "exited"},
            },
            {
                "containerId": P2,
                "imageId": image_id,
                "processId": "p2",
                "sourceCommitLabel": source_commit,
                "state": {"exitCode": 0, "oomKilled": False, "status": "exited"},
            },
            {
                "containerId": P3,
                "imageId": image_id,
                "processId": "p3",
                "sourceCommitLabel": source_commit,
                "state": {"exitCode": 0, "oomKilled": False, "status": "exited"},
            },
        ],
        "contract": checker.CRASH_CONTRACT,
        "kill": {
            "commandExitCode": 0,
            "preKillRunning": True,
            "signal": "KILL",
            "targetContainerId": P1,
        },
        "runId": RUN_ID,
    }
    catalog = {
        "contract": checker.CATALOG_CONTRACT,
        "discoveredByContainerId": P1,
        "discoveryReceiptSha256": "6" * 64,
        "nonce": NONCE,
        "runId": RUN_ID,
        "tools": [
            {
                "description": "Adjust comfort in a space.",
                "inputSchema": {"type": "object"},
                "name": effect,
                "providerId": "space-provider",
                "role": "effect",
            },
            {
                "description": "Read comfort independently.",
                "inputSchema": {"type": "object"},
                "name": readback,
                "providerId": "space-provider",
                "role": "readback",
            },
            {
                "description": "Find music without changing comfort.",
                "inputSchema": {"type": "object"},
                "name": distractor,
                "providerId": "music-provider",
                "role": "distractor",
            },
        ],
    }
    trajectory = {
        "budget": checker.BUDGET,
        "contract": checker.TRAJECTORY_CONTRACT,
        "events": [
            _framework_event(1, "catalog_discovered", P1, catalogSha256=checker._sha256(catalog)),
            _framework_event(
                2,
                "deferred_requests",
                P1,
                callId=CALL_ID,
                historySha256=HISTORY_SHA,
                messageId=MESSAGE_ID,
                operationId=OPERATION_ID,
                toolName=effect,
            ),
            _framework_event(
                3,
                "action_port_execute",
                P1,
                callId=CALL_ID,
                messageId=MESSAGE_ID,
                operationId=OPERATION_ID,
                owner="ConversationActionPort",
                toolName=effect,
            ),
            _framework_event(
                4,
                "deferred_results",
                P2,
                historySha256=HISTORY_SHA,
                inputKind="DeferredToolResults",
                receiptSha256=RECEIPT_SHA,
                receiptSource="durable_timeline",
            ),
            _framework_event(5, "final_answer", P2, receiptSha256=RECEIPT_SHA),
            _framework_event(
                6,
                "replay_received",
                P3,
                messageId=MESSAGE_ID,
                operationId=OPERATION_ID,
            ),
            _framework_event(7, "replay_result", P3, receiptSha256=RECEIPT_SHA),
        ],
        "model": checker.MODEL,
        "prompt": checker.PROMPT,
        "reasoning": checker.REASONING,
        "runId": RUN_ID,
    }
    timeline_events = [
        _timeline_event(
            1,
            "TOOL_REQUEST_ADMITTED",
            P1,
            {
                "action_arguments": ARGUMENTS,
                "action_call_id": CALL_ID,
                "action_name": effect,
                "source_message_id": MESSAGE_ID,
            },
        ),
        _timeline_event(
            2,
            "TOOL_PREPARED",
            P1,
            {
                "arguments": copy.deepcopy(ARGUMENTS),
                "expected_proof": copy.deepcopy(EXPECTED),
                "tool_name": effect,
            },
        ),
        _timeline_event(
            3,
            "STATE_VERIFIED",
            P2,
            {
                "proof": {
                    "evidence": {"source": "provider"},
                    "expected": copy.deepcopy(EXPECTED),
                    "observed": copy.deepcopy(EXPECTED),
                }
            },
        ),
    ]

    def snapshot(
        container_id: str,
        phase: str,
        status: str,
        revision: int,
        count: int,
        receipt_sha: str | None,
    ) -> dict[str, Any]:
        projection = {"phase": phase, "revision": revision, "status": status}
        return {
            "checkpointProjectionSha256": checker._sha256(projection),
            "containerId": container_id,
            "eventCount": count,
            "eventsSha256": checker._sha256(timeline_events[:count]),
            "phase": phase,
            "receiptSha256": receipt_sha,
            "revision": revision,
            "status": status,
        }

    timeline = {
        "contract": checker.TIMELINE_CONTRACT,
        "database": {
            "alembicHead": "4f_0004_pydantic_conversation",
            "name": "eva2_living_disposable_tool01",
            "postgresVersionNum": 160013,
        },
        "events": timeline_events,
        "runId": RUN_ID,
        "snapshots": {
            "afterExecuteBeforeKill": snapshot(P1, "tool_prepared", "waiting_for_proof", 2, 2, None),
            "afterResume": snapshot(P2, "state_verified", "completed", 3, 3, RECEIPT_SHA),
            "afterThirdProcessReplay": snapshot(P3, "state_verified", "completed", 3, 3, RECEIPT_SHA),
        },
    }
    arguments_sha = checker._sha256(ARGUMENTS)
    provider = {
        "calls": [
            {
                "argumentsSha256": arguments_sha,
                "callId": "provider-execute-001",
                "containerId": P1,
                "kind": "execute",
                "operationId": OPERATION_ID,
                "toolName": effect,
            },
            {
                "argumentsSha256": arguments_sha,
                "callId": "provider-observe-001",
                "containerId": P2,
                "kind": "observe",
                "operationId": OPERATION_ID,
                "toolName": readback,
            },
        ],
        "contract": checker.PROVIDER_CONTRACT,
        "providerInstanceId": PROVIDER_INSTANCE,
        "runId": RUN_ID,
        "snapshots": [
            {
                "containerId": P1,
                "executeCalls": 40,
                "observeCalls": 12,
                "phase": "beforeExecute",
                "providerInstanceId": PROVIDER_INSTANCE,
            },
            {
                "containerId": P1,
                "executeCalls": 41,
                "observeCalls": 12,
                "phase": "afterExecuteBeforeKill",
                "providerInstanceId": PROVIDER_INSTANCE,
            },
            {
                "containerId": P2,
                "executeCalls": 41,
                "observeCalls": 13,
                "phase": "afterResume",
                "providerInstanceId": PROVIDER_INSTANCE,
            },
            {
                "containerId": P3,
                "executeCalls": 41,
                "observeCalls": 13,
                "phase": "afterThirdProcessReplay",
                "providerInstanceId": PROVIDER_INSTANCE,
            },
        ],
    }
    return {
        "candidateAuthority": candidate,
        "catalog": catalog,
        "containerCrashAudit": crash,
        "durableTimeline": timeline,
        "frameworkTrajectory": trajectory,
        "providerCounters": provider,
    }


def _source_tree(root: Path) -> tuple[Path, str]:
    model = root / "src/eva/adapters/model/pydantic_conversation.py"
    model.parent.mkdir(parents=True)
    model.write_text(
        "class PydanticConversationModel:\n    def generate(self):\n        return 'ok'\n",
        encoding="utf-8",
    )
    runtime = root / "src/eva/bootstrap/runtime.py"
    runtime.parent.mkdir(parents=True)
    runtime.write_text(
        "from eva.adapters.model.pydantic_conversation import PydanticConversationModel\n"
        "model = PydanticConversationModel()\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "src"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=TOOL01 Fixture",
            "-c",
            "user.email=tool01@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    return root, checker.source_authority.current_commit(root)


def _bundle(
    tmp_path: Path,
    *,
    mutate=None,
    source_root: Path | None = None,
    source_commit: str = SOURCE_COMMIT,
) -> Path:
    artifacts = copy.deepcopy(_artifacts(source_commit=source_commit))
    if mutate is not None:
        mutate(artifacts)
    references = {
        name: _file_reference(tmp_path / f"{name}.json", value)
        for name, value in artifacts.items()
    }
    source_reference = {"artifact": None, "status": "pending"}
    if source_root is not None:
        source = checker.source_authority.generate_source_authority(
            source_root,
            run_id=RUN_ID,
            git_commit=checker.source_authority.current_commit(source_root),
        )
        source_reference = {
            "artifact": _file_reference(tmp_path / "sourceAuthority.json", source),
            "status": source["status"],
        }
    bundle = {
        "artifacts": references,
        "contract": checker.BUNDLE_CONTRACT,
        "runId": RUN_ID,
        "schemaVersion": 2,
        "sourceAuthority": source_reference,
    }
    path = tmp_path / "bundle.json"
    _file_reference(path, bundle)
    return path


def _check(
    bundle: Path,
    *,
    source_commit: str = SOURCE_COMMIT,
    source_root: Path | None = None,
) -> dict[str, Any]:
    return checker.check(
        bundle,
        expected_image_id=IMAGE_ID,
        expected_source_commit=source_commit,
        source_root=source_root,
    )


def test_runtime_passes_without_promoting_pending_source(tmp_path: Path) -> None:
    result = _check(_bundle(tmp_path))
    assert result["runtimeStatus"] == "runtime_passed"
    assert result["replacementStatus"] == "source_authority_pending"


def test_complete_source_for_same_image_commit_promotes(tmp_path: Path) -> None:
    source_root, commit = _source_tree(tmp_path / "source")
    result = _check(
        _bundle(tmp_path, source_root=source_root, source_commit=commit),
        source_commit=commit,
        source_root=source_root,
    )
    assert result["replacementStatus"] == "replacement_passed"


def test_source_commit_must_match_candidate_image_label(tmp_path: Path) -> None:
    source_root, _ = _source_tree(tmp_path / "source")
    result = _check(_bundle(tmp_path, source_root=source_root), source_root=source_root)
    assert result["sourceAuthorityViolations"] == ["source_commit_differs_from_candidate"]
    assert result["replacementStatus"] == "failed"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("imageId", "sha256:" + "9" * 64, "candidate image differs"),
        ("sourceCommitLabel", "9" * 40, "candidate source commit differs"),
    ],
)
def test_candidate_requires_external_authority(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    def mutate(artifacts):
        artifacts["candidateAuthority"][field] = value

    with pytest.raises(checker.Tool01V2Error, match=message):
        _check(_bundle(tmp_path, mutate=mutate))


def test_all_three_containers_must_use_exact_candidate_image(tmp_path: Path) -> None:
    def mutate(artifacts):
        artifacts["containerCrashAudit"]["containers"][2]["imageId"] = "sha256:" + "8" * 64

    with pytest.raises(checker.Tool01V2Error, match="container image differs"):
        _check(_bundle(tmp_path, mutate=mutate))


def test_third_process_requires_a_unique_container(tmp_path: Path) -> None:
    def mutate(artifacts):
        artifacts["containerCrashAudit"]["containers"][2]["containerId"] = P2

    with pytest.raises(checker.Tool01V2Error, match="three unique"):
        _check(_bundle(tmp_path, mutate=mutate))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda audit: audit["containers"][0]["state"].update(exitCode=1), "exit-137"),
        (lambda audit: audit["containers"][0]["state"].update(oomKilled=True), "exit-137"),
        (lambda audit: audit["kill"].update(signal="TERM"), "SIGKILL"),
        (lambda audit: audit["kill"].update(targetContainerId=P2), "SIGKILL"),
        (lambda audit: audit["kill"].update(commandExitCode=1), "SIGKILL"),
    ],
)
def test_crash_audit_fails_closed(tmp_path: Path, mutation, message: str) -> None:
    def mutate(artifacts):
        mutation(artifacts["containerCrashAudit"])

    with pytest.raises(checker.Tool01V2Error, match=message):
        _check(_bundle(tmp_path, mutate=mutate))


def test_only_real_generic_timeline_events_are_accepted(tmp_path: Path) -> None:
    def mutate(artifacts):
        events = artifacts["durableTimeline"]["events"]
        forged = copy.deepcopy(events[1])
        forged["eventType"] = "PROVIDER_ACCEPTED"
        forged["sequence"] = 3
        events.insert(2, forged)

    result = _check(_bundle(tmp_path, mutate=mutate))
    assert "timeline_event_sequence_differs" in result["runtimeViolations"]


def test_tool_executed_is_not_valid_in_precommit_crash_window(tmp_path: Path) -> None:
    def mutate(artifacts):
        events = artifacts["durableTimeline"]["events"]
        forged = copy.deepcopy(events[1])
        forged["eventType"] = "TOOL_EXECUTED"
        forged["sequence"] = 3
        events.insert(2, forged)

    result = _check(_bundle(tmp_path, mutate=mutate))
    assert "timeline_event_sequence_differs" in result["runtimeViolations"]


def test_timeline_payload_hash_is_recomputed(tmp_path: Path) -> None:
    def mutate(artifacts):
        artifacts["durableTimeline"]["events"][1]["payload"]["data"]["arguments"]["space"] = "garage"

    result = _check(_bundle(tmp_path, mutate=mutate))
    assert "timeline_payload_hash_differs" in result["runtimeViolations"]


def test_state_verified_requires_exact_observation(tmp_path: Path) -> None:
    def mutate(artifacts):
        artifacts["durableTimeline"]["events"][2]["payload"]["data"]["proof"]["observed"]["temperatureC"] = 19
        payload = artifacts["durableTimeline"]["events"][2]["payload"]
        artifacts["durableTimeline"]["events"][2]["payloadSha256"] = checker._sha256(payload)

    result = _check(_bundle(tmp_path, mutate=mutate))
    assert "state_verification_differs" in result["runtimeViolations"]


def test_execute_and_observe_must_be_separate_calls(tmp_path: Path) -> None:
    def mutate(artifacts):
        artifacts["providerCounters"]["calls"][1]["callId"] = "provider-execute-001"

    result = _check(_bundle(tmp_path, mutate=mutate))
    assert "separate_execute_observe_binding_differs" in result["runtimeViolations"]


def test_resume_must_not_execute_again(tmp_path: Path) -> None:
    def mutate(artifacts):
        artifacts["providerCounters"]["snapshots"][2]["executeCalls"] += 1
        artifacts["providerCounters"]["snapshots"][3]["executeCalls"] += 1

    result = _check(_bundle(tmp_path, mutate=mutate))
    assert "provider_counter_deltas_differ" in result["runtimeViolations"]


def test_third_process_replay_must_not_call_provider(tmp_path: Path) -> None:
    def mutate(artifacts):
        artifacts["providerCounters"]["snapshots"][3]["observeCalls"] += 1

    result = _check(_bundle(tmp_path, mutate=mutate))
    assert "provider_counter_deltas_differ" in result["runtimeViolations"]


def test_provider_instance_cannot_change_during_resume(tmp_path: Path) -> None:
    def mutate(artifacts):
        artifacts["providerCounters"]["snapshots"][2]["providerInstanceId"] = "replacement-provider"

    result = _check(_bundle(tmp_path, mutate=mutate))
    assert "provider_counter_authority_differs" in result["runtimeViolations"]


def test_replay_must_preserve_timeline_snapshot(tmp_path: Path) -> None:
    def mutate(artifacts):
        artifacts["durableTimeline"]["snapshots"]["afterThirdProcessReplay"]["revision"] = 4

    result = _check(_bundle(tmp_path, mutate=mutate))
    assert "afterThirdProcessReplay_snapshot_differs" in result["runtimeViolations"]


def test_artifact_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    bundle_path = _bundle(tmp_path)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["artifacts"]["providerCounters"]["sha256"] = "0" * 64
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(checker.Tool01V2Error, match="binding differs"):
        _check(bundle_path)


def test_self_asserted_pass_is_forbidden(tmp_path: Path) -> None:
    bundle_path = _bundle(tmp_path)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["passed"] = True
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(checker.Tool01V2Error, match="self-asserted pass"):
        _check(bundle_path)
