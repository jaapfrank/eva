"""Closed EvaConnectorProtocol worker for the BROWSER-A02 proof.

The release-sealed profile is the only configuration input.  The worker emits
only the two framed protocol responses; exceptions produce no provider claim.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

candidate_root = Path.cwd()
candidate_source = candidate_root / "src"
if candidate_source.is_dir() and (candidate_root / "connector_sdk").is_dir():
    sys.path[:0] = [str(candidate_source), str(candidate_root)]

from tests.fixtures.browser.playwright_mcp_action import (  # noqa: E402
    PlaywrightFixtureStateAdapter,
    PlaywrightMcpActionProfile,
    PlaywrightMcpClickAdapter,
)
from eva.contracts.canonical_json import canonical_sha256  # noqa: E402
from eva.contracts.connector import ConnectorManifest  # noqa: E402
from eva.contracts.connector_rpc import (  # noqa: E402
    MAX_CONNECTOR_EXCHANGE_BYTES,
    ConnectorInitializeRequest,
    ConnectorInitializeSuccess,
    ConnectorInvokeRequest,
    ConnectorInvokeSuccess,
    decode_connector_rpc_frame_prefix,
    encode_connector_rpc_frame,
)


def main() -> int:
    try:
        if len(sys.argv) != 2:
            return 64
        profile_payload = json.loads(Path(sys.argv[1]).read_bytes())
        _require_keys(
            profile_payload,
            {
                "actionProfile",
                "connectorManifests",
                "contract",
                "logicalTimes",
                "testFailurePhase",
            },
            "worker profile",
        )
        if profile_payload["contract"] != "EvaPlaywrightMcpActionWorkerProfile/v1":
            return 65
        action_profile = PlaywrightMcpActionProfile(
            **profile_payload["actionProfile"]
        )
        incoming = sys.stdin.buffer.read(MAX_CONNECTOR_EXCHANGE_BYTES + 1)
        if len(incoming) > MAX_CONNECTOR_EXCHANGE_BYTES:
            return 65
        initialize, remainder = decode_connector_rpc_frame_prefix(incoming)
        invoke, trailing = decode_connector_rpc_frame_prefix(remainder)
        if (
            not isinstance(initialize, ConnectorInitializeRequest)
            or not isinstance(invoke, ConnectorInvokeRequest)
            or trailing
        ):
            return 65
        operation = initialize.params.capability_manifest.operation
        manifest_payload = profile_payload["connectorManifests"].get(operation)
        if manifest_payload is None:
            return 65
        manifest = ConnectorManifest.model_validate(manifest_payload)
        initialize.validate_against_manifest(manifest)
        invoke.validate_against_initialize(initialize, manifest)
        times = profile_payload["logicalTimes"]
        initialized_at = times["initializedAt"]
        completed_at = times[
            "effectCompletedAt" if invoke.method == "effect.execute" else "readbackCompletedAt"
        ]
        initialize_success = ConnectorInitializeSuccess.model_validate(
            {
                "schema_version": 1,
                "contract": "ConnectorInitializeSuccess/v1",
                "jsonrpc": "2.0",
                "id": initialize.id,
                "result": {
                    "protocol_version": "EvaConnectorProtocol/v1",
                    "request_id": initialize.id,
                    "correlation_id": initialize.params.correlation_id,
                    "connector_id": initialize.params.connector_id,
                    "connector_manifest_sha256": canonical_sha256(manifest),
                    "worker_id": initialize.params.worker_id,
                    "worker_executable_sha256": (
                        initialize.params.worker_executable_sha256
                    ),
                    "connector_binding_sha256": canonical_sha256(
                        initialize.params.connector_binding
                    ),
                    "capability_manifest_sha256": canonical_sha256(
                        initialize.params.capability_manifest
                    ),
                    "capability_grant_sha256": canonical_sha256(
                        initialize.params.capability_grant
                    ),
                    "credential_grant_sha256": canonical_sha256(
                        initialize.params.credential_grant
                    ),
                    "initialized_at": initialized_at,
                    "external_io_performed": False,
                    "secret_bytes_received": False,
                },
            }
        )
        initialize_success.validate_against_request(initialize)
        sys.stdout.buffer.write(encode_connector_rpc_frame(initialize_success))
        sys.stdout.buffer.flush()
        failure_phase = profile_payload["testFailurePhase"]
        if failure_phase not in {"none", "before_effect_io", "after_effect_io"}:
            return 65
        if failure_phase == "before_effect_io" and invoke.method == "effect.execute":
            return 70
        outcome = _invoke(
            invoke=invoke,
            manifest=manifest,
            action_profile=action_profile,
            completed_at=completed_at,
        )
        if failure_phase == "after_effect_io" and invoke.method == "effect.execute":
            return 70
        invoke_success = ConnectorInvokeSuccess.model_validate(
            {
                "schema_version": 1,
                "contract": "ConnectorInvokeSuccess/v1",
                "jsonrpc": "2.0",
                "id": invoke.id,
                "result": {
                    "schema_version": 1,
                    "contract": "ConnectorResult/v1",
                    "connector_id": invoke.params.connector_id,
                    "request_id": invoke.id,
                    "correlation_id": invoke.params.correlation_id,
                    "operation": invoke.params.operation,
                    "request_sha256": canonical_sha256(invoke.params),
                    "connector_manifest_sha256": canonical_sha256(manifest),
                    "outcome": outcome,
                    "completed_at": completed_at,
                },
            }
        )
        invoke_success.validate_against_request(invoke, manifest)
        sys.stdout.buffer.write(encode_connector_rpc_frame(invoke_success))
        return 0
    except Exception:
        return 70


def _invoke(
    *,
    invoke: ConnectorInvokeRequest,
    manifest: ConnectorManifest,
    action_profile: PlaywrightMcpActionProfile,
    completed_at: str,
) -> dict[str, object]:
    if invoke.method == "effect.execute":
        if invoke.params.operation != "browser.fixture.click" or invoke.params.request_payload != {
            "state": "committed",
            "target": action_profile.provider_resource_id,
        }:
            raise ValueError("browser click payload differs")
        clicked = PlaywrightMcpClickAdapter(action_profile).click_once(
            effect_key=invoke.id,
            request_sha256=invoke.params.request_payload_sha256,
        )
        return {
            "kind": "effect_response",
            "provider_resource_id": clicked.provider_resource_id,
            "provider_receipt_sha256": canonical_sha256(
                {
                    "clickResultSha256": clicked.click_result_sha256,
                    "preClickSnapshotSha256": clicked.pre_click_snapshot_sha256,
                    "toolSequence": list(clicked.tool_sequence),
                }
            ),
            "provider_time": completed_at,
        }
    if invoke.method != "effect.readback" or invoke.params.operation != "effect.readback":
        raise ValueError("browser worker operation differs")
    payload = invoke.params.request_payload
    _require_keys(
        payload,
        {"idempotencyKey", "providerResourceId", "requestSha256"},
        "readback payload",
    )
    if payload["providerResourceId"] != action_profile.provider_resource_id:
        raise ValueError("browser readback resource differs")
    state = PlaywrightFixtureStateAdapter(action_profile).read_state(
        effect_key=payload["idempotencyKey"],
        request_sha256=payload["requestSha256"],
    )
    operation = next(item for item in manifest.operations if item.operation == "effect.readback")
    return {
        "kind": "readback",
        "provider_resource_id": state.provider_resource_id,
        "idempotency_key": state.effect_key,
        "request_sha256": state.request_sha256,
        "match_status": state.match_status,
        "payload": (
            None
            if state.state is None
            else {
                "representation": "inline",
                "output_schema_sha256": operation.output_schema_sha256,
                "normalized_payload": state.state,
            }
        ),
        "observed_at": completed_at,
    }


def _require_keys(value: object, expected: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{label} fields differ")


if __name__ == "__main__":
    raise SystemExit(main())
