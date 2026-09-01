from __future__ import annotations

import ast
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
PRODUCER = (
    HERE.parent / "contextforge-gateway-v1" / "prove_filesystem_provider_sigkill_v2.py"
)
RUNNER = HERE / "run_physical_acceptance.py"
SPEC = importlib.util.spec_from_file_location(
    "tool01_v2_acceptance", HERE / "check_acceptance.py"
)
assert SPEC is not None and SPEC.loader is not None
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)
RUNNER_SPEC = importlib.util.spec_from_file_location("tool01_v2_runner", RUNNER)
assert RUNNER_SPEC is not None and RUNNER_SPEC.loader is not None
physical_runner = importlib.util.module_from_spec(RUNNER_SPEC)
sys.modules[RUNNER_SPEC.name] = physical_runner
RUNNER_SPEC.loader.exec_module(physical_runner)

RUN_ID = "tool01-v2-fixture-001"
NONCE = "a1b2c3d4e5f6"
IMAGE_ID = "sha256:" + "a" * 64
PROOF_HARNESS_SHA256 = "f" * 64
PROOF_SOURCE_COMMIT = "c" * 40
SOURCE_COMMIT = "b" * 40
AUXILIARY_IMAGE_IDS = {
    "contextforge": "sha256:" + "c" * 64,
    "filesystem": "sha256:" + "d" * 64,
    "postgres": "sha256:" + "e" * 64,
    "time": "sha256:" + "f" * 64,
}
CONTEXTFORGE_START = b"#!/bin/sh\nset -eu\n"
CONTEXTFORGE_START_SHA256 = hashlib.sha256(CONTEXTFORGE_START).hexdigest()
P1 = "1" * 64
P2 = "2" * 64
P3 = "3" * 64
CALL_ID = "effect-call-001"
MESSAGE_ID = "message-001"
OPERATION_ID = "operation-001"
HISTORY_SHA = "4" * 64
RECEIPT_SHA = "5" * 64
PROVIDER_INSTANCE = "provider-instance-001"
ARGUMENTS = copy.deepcopy(checker.EXPECTED_FILESYSTEM_ARGUMENTS)
EXECUTE_ARGUMENTS = copy.deepcopy(ARGUMENTS)
OBSERVE_ARGUMENTS = {"path": "tool01-proof.txt"}
EXPECTED = {"content": ARGUMENTS["content"]}
TOOL_CONFIGURATION_SHA = "7" * 64


def test_proof_producer_is_one_self_contained_hashed_file() -> None:
    tree = ast.parse(PRODUCER.read_text(encoding="utf-8"))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not {
        module
        for module in imported_modules
        if module.startswith("prove_") or module.startswith("experiments.")
    }
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert {"_json_request", "_login", "_migrate", "_write_private"} <= functions


def test_host_helpers_keep_stdin_attached_for_private_payloads() -> None:
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    helpers = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name
        in {"_assert_volume_metadata", "_init_volume", "_populate_secret_volume"}
    }
    assert set(helpers) == {
        "_assert_volume_metadata",
        "_init_volume",
        "_populate_secret_volume",
    }
    for helper in helpers.values():
        literals = {
            node.value
            for node in ast.walk(helper)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert "--interactive" in literals

    init_program = next(
        node.value
        for node in ast.walk(helpers["_init_volume"])
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "directory_owner=payload.get" in node.value
    )
    assert init_program.index("os.chmod(root,mode)") < init_program.index(
        "os.chown(root,uid,gid)"
    )
    assert init_program.index("os.chmod(target,int(row['mode']))") < init_program.index(
        "os.chown(target,int(row['uid']),int(row['gid']))"
    )
    assert init_program.index(
        "os.chown(target,int(row['uid']),int(row['gid']))"
    ) < init_program.index("os.chmod(root,mode)")
    populate_program = next(
        node.value
        for node in ast.walk(helpers["_populate_secret_volume"])
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "target='/state/'+name" in node.value
    )
    assert populate_program.index("os.chmod(target,0o400)") < populate_program.index(
        "os.chown(target,uid,uid)"
    )
    metadata_helper = helpers["_assert_volume_metadata"]
    metadata_literals = {
        node.value
        for node in ast.walk(metadata_helper)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "0:0" not in metadata_literals
    assert any(isinstance(node, ast.JoinedStr) for node in ast.walk(metadata_helper))


def test_filesystem_provider_has_a_distinct_private_start_secret_volume() -> None:
    source = RUNNER.read_text(encoding="utf-8")

    assert 'filesystem_secret_volume = f"{prefix}-filesystem-secrets"' in source
    assert (
        'f"type=volume,src={filesystem_secret_volume},dst=/run/secrets,readonly"'
        in source
    )
    assert "EVA_CONTEXTFORGE_JWT_SECRET_FILE=/run/secrets/filesystem-jwt" in source
    assert (
        "EVA_CONTEXTFORGE_ENCRYPTION_SECRET_FILE=/run/secrets/filesystem-encryption"
        in source
    )
    assert (
        "EVA_CONTEXTFORGE_ADMIN_PASSWORD_FILE=/run/secrets/filesystem-admin-password"
        in source
    )
    assert (
        'f"type=volume,src={contextforge_secret_volume},dst=/run/secrets,readonly"'
        in source
    )


def test_physical_runner_keeps_contextforge_generated_tool_names() -> None:
    runner_source = RUNNER.read_text(encoding="utf-8")
    producer_source = PRODUCER.read_text(encoding="utf-8")

    assert "def _make_tool_public(" in runner_source
    assert "def _rename_tool(" not in runner_source
    assert '"custom_name": final_name' not in runner_source
    assert 'not provider_id.endswith(f"_{nonce}")' in producer_source
    assert 'not name.startswith(f"{provider_id}_")' in producer_source


def test_virtual_server_uses_contextforge_server_body_envelope(monkeypatch) -> None:
    tool_ids = ["1" * 32, "2" * 32, "3" * 32]
    server_id = "4" * 32
    calls = []

    def request(container, method, path, *, payload=None, token=None, expected=(200,)):
        calls.append((container, method, path, payload, token, expected))
        if method == "POST":
            return {"id": server_id, "visibility": "public"}
        return [{"id": tool_id} for tool_id in tool_ids]

    monkeypatch.setattr(physical_runner, "_gateway_request", request)
    assert (
        physical_runner._create_virtual_server(
            "gateway", "token", name="tool01_fixture", tool_ids=tool_ids
        )
        == server_id
    )
    assert calls == [
        (
            "gateway",
            "POST",
            "/servers",
            {
                "server": {
                    "associated_tools": tool_ids,
                    "description": (
                        "Disposable TOOL01 v2 filesystem acceptance server."
                    ),
                    "name": "tool01_fixture",
                },
                "visibility": "public",
            },
            "token",
            (200, 201),
        ),
        (
            "gateway",
            "GET",
            f"/servers/{server_id}/tools?include_inactive=false&include_metrics=false",
            None,
            "token",
            (200,),
        ),
    ]


def test_virtual_server_rejects_a_nonpublic_create_response(monkeypatch) -> None:
    def request(container, method, path, *, payload=None, token=None, expected=(200,)):
        del container, method, path, payload, token, expected
        return {"id": "4" * 32, "visibility": "private"}

    monkeypatch.setattr(physical_runner, "_gateway_request", request)
    with pytest.raises(
        physical_runner.PhysicalAcceptanceError,
        match="virtual server is malformed or not public",
    ):
        physical_runner._create_virtual_server(
            "gateway", "token", name="tool01_fixture", tool_ids=["1" * 32]
        )


def _file_reference(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(raw)
    return {
        "bytes": len(raw),
        "path": path.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


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
    return {
        "containerId": container_id,
        "data": data,
        "kind": kind,
        "sequence": sequence,
    }


def _artifacts(
    *, image_id: str = IMAGE_ID, source_commit: str = SOURCE_COMMIT
) -> dict[str, dict[str, Any]]:
    filesystem_provider = f"filesystem_{NONCE}"
    time_provider = f"time_{NONCE}"
    effect = f"{filesystem_provider}_write_file"
    readback = f"{filesystem_provider}_read_text_file"
    distractor = f"{time_provider}_echo"
    candidate = {
        "contract": checker.CANDIDATE_CONTRACT,
        "imageId": image_id,
        "proofHarnessSha256": PROOF_HARNESS_SHA256,
        "runId": RUN_ID,
        "sourceCommitLabel": source_commit,
    }
    host = {
        "contextforgeStartSha256": CONTEXTFORGE_START_SHA256,
        "contract": checker.HOST_CONTRACT,
        "imageIds": {"candidate": image_id, **AUXILIARY_IMAGE_IDS},
        "modelLease": {
            "accessOnly": True,
            "leaseSha256": "9" * 64,
            "sourceSha256": "0" * 64,
        },
        "proofHarnessSha256": PROOF_HARNESS_SHA256,
        "proofSourceCommit": PROOF_SOURCE_COMMIT,
        "runId": RUN_ID,
        "sourceCommit": source_commit,
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
                "description": "Write the requested comfort state to the proof file.",
                "inputSchema": {
                    "properties": {
                        "content": {"type": "string"},
                        "path": {"type": "string"},
                    },
                    "required": ["path", "content"],
                    "type": "object",
                },
                "name": effect,
                "providerId": filesystem_provider,
                "role": "effect",
            },
            {
                "description": "Read the proof file independently.",
                "inputSchema": {
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "type": "object",
                },
                "name": readback,
                "providerId": filesystem_provider,
                "role": "readback",
            },
            {
                "description": "Read the current time without changing the proof file.",
                "inputSchema": {"type": "object"},
                "name": distractor,
                "providerId": time_provider,
                "role": "distractor",
            },
        ],
    }
    trajectory = {
        "budget": checker.BUDGET,
        "contract": checker.TRAJECTORY_CONTRACT,
        "events": [
            _framework_event(
                1, "catalog_discovered", P1, catalogSha256=checker._sha256(catalog)
            ),
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
                "request_arguments": copy.deepcopy(EXECUTE_ARGUMENTS),
                "request_observation_arguments": copy.deepcopy(OBSERVE_ARGUMENTS),
                "request_observation_tool_name": readback,
                "request_tool_configuration_sha256": TOOL_CONFIGURATION_SHA,
                "request_tool_name": effect,
                "source_message_id": MESSAGE_ID,
            },
        ),
        _timeline_event(
            2,
            "TOOL_PREPARED",
            P1,
            {
                "arguments": copy.deepcopy(EXECUTE_ARGUMENTS),
                "expected_proof": copy.deepcopy(EXPECTED),
                "observation_arguments": copy.deepcopy(OBSERVE_ARGUMENTS),
                "observation_tool_name": readback,
                "tool_configuration_sha256": TOOL_CONFIGURATION_SHA,
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
            "afterExecuteBeforeKill": snapshot(
                P1, "tool_prepared", "waiting_for_proof", 2, 2, None
            ),
            "afterResume": snapshot(
                P2, "state_verified", "completed", 3, 3, RECEIPT_SHA
            ),
            "afterThirdProcessReplay": snapshot(
                P3, "state_verified", "completed", 3, 3, RECEIPT_SHA
            ),
        },
    }
    provider = {
        "calls": [
            {
                "argumentsSha256": checker._sha256(EXECUTE_ARGUMENTS),
                "callId": "provider-execute-001",
                "containerId": P1,
                "kind": "execute",
                "operationId": OPERATION_ID,
                "toolConfigurationSha256": TOOL_CONFIGURATION_SHA,
                "toolName": effect,
            },
            {
                "argumentsSha256": checker._sha256(OBSERVE_ARGUMENTS),
                "callId": "provider-observe-001",
                "containerId": P2,
                "kind": "observe",
                "operationId": OPERATION_ID,
                "toolConfigurationSha256": TOOL_CONFIGURATION_SHA,
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
        "hostAuthority": host,
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
    contextforge_start = (
        root
        / "eva2"
        / "experiments"
        / "contextforge-gateway-v1"
        / "start-contextforge.sh"
    )
    contextforge_start.parent.mkdir(parents=True)
    contextforge_start.write_bytes(CONTEXTFORGE_START)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "src", "eva2"], check=True)
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
        expected_auxiliary_image_ids=AUXILIARY_IMAGE_IDS,
        expected_image_id=IMAGE_ID,
        expected_proof_harness_sha256=PROOF_HARNESS_SHA256,
        expected_proof_source_commit=PROOF_SOURCE_COMMIT,
        expected_source_commit=source_commit,
        source_root=source_root,
    )


def test_runtime_passes_without_promoting_pending_source(tmp_path: Path) -> None:
    source_root, commit = _source_tree(tmp_path / "source")
    result = _check(
        _bundle(tmp_path, source_commit=commit),
        source_commit=commit,
        source_root=source_root,
    )
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


def test_catalog_rejects_nonce_as_an_unbound_name_substring(tmp_path: Path) -> None:
    def mutate(artifacts):
        artifacts["catalog"]["tools"][0]["name"] = (
            f"rogue_{NONCE}_filesystem_{NONCE}_write_file"
        )

    with pytest.raises(
        checker.Tool01V2Error,
        match="catalog tool/provider namespace differs",
    ):
        _check(_bundle(tmp_path, mutate=mutate))


def test_source_commit_must_match_candidate_image_label(tmp_path: Path) -> None:
    source_root, _ = _source_tree(tmp_path / "source")
    with pytest.raises(
        checker.Tool01V2Error,
        match="candidate source checkout is not clean and commit-bound",
    ):
        _check(_bundle(tmp_path, source_root=source_root), source_root=source_root)


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


def test_candidate_requires_external_proof_harness_authority(
    tmp_path: Path,
) -> None:
    def mutate(artifacts):
        artifacts["candidateAuthority"]["proofHarnessSha256"] = "e" * 64

    with pytest.raises(
        checker.Tool01V2Error,
        match="candidate proof harness differs from external authority",
    ):
        _check(_bundle(tmp_path, mutate=mutate))


def test_host_authority_artifact_is_required(tmp_path: Path) -> None:
    def mutate(artifacts):
        artifacts.pop("hostAuthority")

    with pytest.raises(checker.Tool01V2Error, match="artifact set fields differ"):
        _check(_bundle(tmp_path, mutate=mutate))


@pytest.mark.parametrize(
    "field",
    [
        "contextforgeStartSha256",
        "contract",
        "imageIds",
        "modelLease",
        "proofHarnessSha256",
        "proofSourceCommit",
        "runId",
        "sourceCommit",
    ],
)
def test_host_authority_trust_fields_are_required(tmp_path: Path, field: str) -> None:
    def mutate(artifacts):
        artifacts["hostAuthority"].pop(field)

    with pytest.raises(checker.Tool01V2Error, match="host authority fields differ"):
        _check(_bundle(tmp_path, mutate=mutate))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("contract", "EvaTool01PhysicalHostAuthority/v9"),
        ("runId", "different-valid-run-id"),
    ],
)
def test_host_authority_header_is_exact(tmp_path: Path, field: str, value: str) -> None:
    def mutate(artifacts):
        artifacts["hostAuthority"][field] = value

    with pytest.raises(checker.Tool01V2Error, match="host authority differs"):
        _check(_bundle(tmp_path, mutate=mutate))


@pytest.mark.parametrize("role", sorted(AUXILIARY_IMAGE_IDS))
def test_every_auxiliary_image_role_is_required(tmp_path: Path, role: str) -> None:
    def mutate(artifacts):
        artifacts["hostAuthority"]["imageIds"].pop(role)

    with pytest.raises(
        checker.Tool01V2Error, match="host image authority fields differ"
    ):
        _check(_bundle(tmp_path, mutate=mutate))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("sourceCommit", "1" * 40, "host source commit differs"),
        ("proofHarnessSha256", "1" * 64, "host proof harness differs"),
        ("proofSourceCommit", "1" * 40, "host proof source commit differs"),
    ],
)
def test_host_authority_valid_alternative_trust_values_are_rejected(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    def mutate(artifacts):
        artifacts["hostAuthority"][field] = value

    with pytest.raises(checker.Tool01V2Error, match=message):
        _check(_bundle(tmp_path, mutate=mutate))


def test_host_candidate_valid_alternative_image_is_rejected(tmp_path: Path) -> None:
    def mutate(artifacts):
        artifacts["hostAuthority"]["imageIds"]["candidate"] = "sha256:" + "1" * 64

    with pytest.raises(checker.Tool01V2Error, match="host candidate image differs"):
        _check(_bundle(tmp_path, mutate=mutate))


@pytest.mark.parametrize("role", sorted(AUXILIARY_IMAGE_IDS))
def test_auxiliary_valid_alternative_image_is_rejected(
    tmp_path: Path, role: str
) -> None:
    def mutate(artifacts):
        artifacts["hostAuthority"]["imageIds"][role] = "sha256:" + "1" * 64

    with pytest.raises(checker.Tool01V2Error, match="host auxiliary image differs"):
        _check(_bundle(tmp_path, mutate=mutate))


def test_contextforge_start_valid_alternative_hash_is_rejected(tmp_path: Path) -> None:
    source_root, commit = _source_tree(tmp_path / "source")

    def mutate(artifacts):
        artifacts["hostAuthority"]["contextforgeStartSha256"] = "1" * 64

    with pytest.raises(checker.Tool01V2Error, match="ContextForge start differs"):
        _check(
            _bundle(
                tmp_path,
                mutate=mutate,
                source_root=source_root,
                source_commit=commit,
            ),
            source_commit=commit,
            source_root=source_root,
        )


def test_all_three_containers_must_use_exact_candidate_image(tmp_path: Path) -> None:
    def mutate(artifacts):
        artifacts["containerCrashAudit"]["containers"][2]["imageId"] = (
            "sha256:" + "8" * 64
        )

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
        (
            lambda audit: audit["containers"][0]["state"].update(oomKilled=True),
            "exit-137",
        ),
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
        artifacts["durableTimeline"]["events"][1]["payload"]["data"]["arguments"][
            "path"
        ] = "other-proof.txt"

    result = _check(_bundle(tmp_path, mutate=mutate))
    assert "timeline_payload_hash_differs" in result["runtimeViolations"]


def test_state_verified_requires_exact_observation(tmp_path: Path) -> None:
    def mutate(artifacts):
        artifacts["durableTimeline"]["events"][2]["payload"]["data"]["proof"][
            "observed"
        ]["content"] = "different-marker"
        payload = artifacts["durableTimeline"]["events"][2]["payload"]
        artifacts["durableTimeline"]["events"][2]["payloadSha256"] = checker._sha256(
            payload
        )

    result = _check(_bundle(tmp_path, mutate=mutate))
    assert "state_verification_differs" in result["runtimeViolations"]


def test_model_arguments_must_project_to_execute_and_readback(tmp_path: Path) -> None:
    def mutate(artifacts):
        admission = artifacts["durableTimeline"]["events"][0]
        admission["payload"]["data"]["action_arguments"]["content"] = (
            "different-model-marker"
        )
        admission["payloadSha256"] = checker._sha256(admission["payload"])

    result = _check(_bundle(tmp_path, mutate=mutate))
    assert "model_filesystem_projection_differs" in result["runtimeViolations"]


def test_coordinated_provider_rewrite_cannot_detach_from_model(tmp_path: Path) -> None:
    changed_execute = {"content": "forged-marker", "path": "forged-proof.txt"}
    changed_observe = {"path": "forged-proof.txt"}
    changed_expected = {"content": "forged-marker"}

    def mutate(artifacts):
        events = artifacts["durableTimeline"]["events"]
        admission = events[0]["payload"]["data"]
        admission["request_arguments"] = copy.deepcopy(changed_execute)
        admission["request_observation_arguments"] = copy.deepcopy(changed_observe)
        prepared = events[1]["payload"]["data"]
        prepared["arguments"] = copy.deepcopy(changed_execute)
        prepared["observation_arguments"] = copy.deepcopy(changed_observe)
        prepared["expected_proof"] = copy.deepcopy(changed_expected)
        proof = events[2]["payload"]["data"]["proof"]
        proof["expected"] = copy.deepcopy(changed_expected)
        proof["observed"] = copy.deepcopy(changed_expected)
        for event in events:
            event["payloadSha256"] = checker._sha256(event["payload"])
        for snapshot in artifacts["durableTimeline"]["snapshots"].values():
            snapshot["eventsSha256"] = checker._sha256(events[: snapshot["eventCount"]])
        calls = artifacts["providerCounters"]["calls"]
        calls[0]["argumentsSha256"] = checker._sha256(changed_execute)
        calls[1]["argumentsSha256"] = checker._sha256(changed_observe)

    result = _check(_bundle(tmp_path, mutate=mutate))
    assert "model_filesystem_projection_differs" in result["runtimeViolations"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("content", "verkeerde kopermaan"),
        ("path", "verkeerd-bestand.txt"),
    ],
)
def test_coordinated_rewrite_cannot_change_the_prompt_result(
    tmp_path: Path, field: str, value: str
) -> None:
    changed = copy.deepcopy(ARGUMENTS)
    changed[field] = value
    changed_observe = {"path": changed["path"]}
    changed_expected = {"content": changed["content"]}

    def mutate(artifacts):
        events = artifacts["durableTimeline"]["events"]
        admission = events[0]["payload"]["data"]
        admission["action_arguments"] = copy.deepcopy(changed)
        admission["request_arguments"] = copy.deepcopy(changed)
        admission["request_observation_arguments"] = copy.deepcopy(changed_observe)
        prepared = events[1]["payload"]["data"]
        prepared["arguments"] = copy.deepcopy(changed)
        prepared["observation_arguments"] = copy.deepcopy(changed_observe)
        prepared["expected_proof"] = copy.deepcopy(changed_expected)
        proof = events[2]["payload"]["data"]["proof"]
        proof["expected"] = copy.deepcopy(changed_expected)
        proof["observed"] = copy.deepcopy(changed_expected)
        for event in events:
            event["payloadSha256"] = checker._sha256(event["payload"])
        for snapshot in artifacts["durableTimeline"]["snapshots"].values():
            snapshot["eventsSha256"] = checker._sha256(events[: snapshot["eventCount"]])
        calls = artifacts["providerCounters"]["calls"]
        calls[0]["argumentsSha256"] = checker._sha256(changed)
        calls[1]["argumentsSha256"] = checker._sha256(changed_observe)

    result = _check(_bundle(tmp_path, mutate=mutate))
    assert "model_filesystem_projection_differs" in result["runtimeViolations"]


def test_execute_and_observe_must_be_separate_calls(tmp_path: Path) -> None:
    def mutate(artifacts):
        artifacts["providerCounters"]["calls"][1]["callId"] = "provider-execute-001"

    result = _check(_bundle(tmp_path, mutate=mutate))
    assert "separate_execute_observe_binding_differs" in result["runtimeViolations"]


def test_observe_arguments_must_use_the_durable_separate_projection(
    tmp_path: Path,
) -> None:
    def mutate(artifacts):
        call = artifacts["providerCounters"]["calls"][1]
        call["argumentsSha256"] = checker._sha256(EXECUTE_ARGUMENTS)

    result = _check(_bundle(tmp_path, mutate=mutate))
    assert "separate_execute_observe_binding_differs" in result["runtimeViolations"]


@pytest.mark.parametrize(
    ("field", "value", "violation"),
    [
        ("observation_tool_name", "changed_readback", "timeline_preparation_differs"),
        (
            "observation_arguments",
            {"path": "changed-proof.txt"},
            "timeline_preparation_differs",
        ),
        (
            "tool_configuration_sha256",
            "8" * 64,
            "timeline_preparation_differs",
        ),
    ],
)
def test_prepared_readback_command_must_match_admission(
    tmp_path: Path, field: str, value: object, violation: str
) -> None:
    def mutate(artifacts):
        event = artifacts["durableTimeline"]["events"][1]
        event["payload"]["data"][field] = value
        event["payloadSha256"] = checker._sha256(event["payload"])

    result = _check(_bundle(tmp_path, mutate=mutate))
    assert violation in result["runtimeViolations"]


def test_provider_call_must_bind_the_durable_configuration(tmp_path: Path) -> None:
    def mutate(artifacts):
        artifacts["providerCounters"]["calls"][1]["toolConfigurationSha256"] = "8" * 64

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
        artifacts["providerCounters"]["snapshots"][2]["providerInstanceId"] = (
            "replacement-provider"
        )

    result = _check(_bundle(tmp_path, mutate=mutate))
    assert "provider_counter_authority_differs" in result["runtimeViolations"]


def test_replay_must_preserve_timeline_snapshot(tmp_path: Path) -> None:
    def mutate(artifacts):
        artifacts["durableTimeline"]["snapshots"]["afterThirdProcessReplay"][
            "revision"
        ] = 4

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
