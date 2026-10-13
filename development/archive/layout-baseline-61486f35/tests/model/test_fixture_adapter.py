from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.support.model_fixture import (
    FixtureDispatchRejected,
    FixtureModelAdapter,
    SealedFixtureProgram,
)
from eva.contracts.canonical_json import canonical_json_bytes


EVA2_ROOT = Path(__file__).resolve().parents[2]
MODEL_ARTIFACT_PATH = EVA2_ROOT / "design/vertical/noor-4d-model-vectors-v1.json"


@pytest.fixture(scope="module")
def artifact() -> dict[str, Any]:
    value = json.loads(MODEL_ARTIFACT_PATH.read_bytes())
    assert isinstance(value, dict)
    return value


@pytest.fixture(scope="module")
def adapter() -> FixtureModelAdapter:
    program = SealedFixtureProgram.from_artifact_bytes(MODEL_ARTIFACT_PATH.read_bytes())
    return FixtureModelAdapter(program)


def _request_vectors(artifact: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield from artifact["happyPathVectors"]
    correction = artifact["correctionBeforeFirstEffect"]
    yield correction["generation1"]["modelVector"]
    yield from correction["generation2"]["modelVectors"]


def _requests_by_sha256(artifact: dict[str, Any]) -> dict[str, bytes]:
    return {
        vector["requestCanonicalBytes"]["sha256"]: base64.b64decode(
            vector["requestCanonicalBytes"]["base64"], validate=True
        )
        for vector in _request_vectors(artifact)
    }


def _grant_bytes(dispatch: dict[str, Any]) -> bytes:
    return base64.b64decode(
        dispatch["credentialGrant"]["canonicalBytes"]["base64"], validate=True
    )


def _invoke(
    adapter: FixtureModelAdapter,
    artifact: dict[str, Any],
    dispatch: dict[str, Any],
):
    request_sha256 = dispatch["modelAttempt"]["payload"]["request_sha256"]
    return adapter.invoke(
        planner_request_bytes=_requests_by_sha256(artifact)[request_sha256],
        credential_grant_bytes=_grant_bytes(dispatch),
        fixture_case_id=dispatch["fixtureCaseId"],
        purpose=dispatch["purpose"],
        attempt_ordinal=dispatch["attemptOrdinal"],
        dispatch_at=dispatch["dispatchAt"],
    )


def test_executes_all_twenty_sealed_dispatch_rows_exactly(
    adapter: FixtureModelAdapter, artifact: dict[str, Any]
) -> None:
    rows = artifact["modelFixtureProgram"]["program"]["decisionTable"]
    blobs = {
        blob["blobId"]: base64.b64decode(blob["base64"], validate=True)
        for blob in artifact["modelFixtureProgram"]["program"]["blobCatalog"]
    }

    for dispatch, row in zip(artifact["modelDispatchVectors"], rows, strict=True):
        result = _invoke(adapter, artifact, dispatch)
        action = row["action"]
        expected_bytes = blobs.get(action.get("blobId"))
        attempt = dispatch["modelAttempt"]["payload"]

        assert result.kind == action["kind"]
        assert result.row_ordinal == row["rowOrdinal"]
        assert result.provider_state == attempt["state"]
        assert (
            result.provider_reference
            == dispatch["eva_core.model_attempts"]["provider_reference"]
        )
        assert result.response_bytes == expected_bytes
        assert result.response_sha256 == attempt["response_sha256"]
        assert result.logical_provider_call_delta == 1
        assert result.actual_network_call_delta == 0
        assert result.hidden_retry_delta == 0


def test_429_is_an_exact_provider_result_then_one_explicit_success(
    adapter: FixtureModelAdapter, artifact: dict[str, Any]
) -> None:
    first, second = artifact["modelDispatchVectors"][4:6]

    limited = _invoke(adapter, artifact, first)
    recovered = _invoke(adapter, artifact, second)
    limited_http = json.loads(limited.response_bytes)

    assert limited.kind == "return-http-result"
    assert limited.provider_state == "rate_limited"
    assert limited_http["status"] == 429
    assert {row["name"]: row["value"] for row in limited_http["headers"]}[
        "retry-after"
    ] == "5"
    assert recovered.kind == "return-contract-bytes"
    assert recovered.provider_state == "responded"
    assert recovered.response_sha256 == (
        "165584c8dc04d736014477ff21d04cd48f60aa97b9cbf8607958fd39eb27eeb5"
    )
    assert (
        limited.logical_provider_call_delta + recovered.logical_provider_call_delta == 2
    )
    assert limited.hidden_retry_delta + recovered.hidden_retry_delta == 0


@pytest.mark.parametrize(
    "dispatch_index",
    [6, 7, 8, 9, 10, 11],
    ids=[
        "malformed-schema",
        "wrong-episode",
        "wrong-generation",
        "wrong-purpose",
        "wrong-identity",
        "wrong-claim-refs",
    ],
)
def test_wrong_outputs_are_returned_as_exact_untrusted_bytes_for_host_validation(
    adapter: FixtureModelAdapter,
    artifact: dict[str, Any],
    dispatch_index: int,
) -> None:
    dispatch = artifact["modelDispatchVectors"][dispatch_index]
    row = artifact["modelFixtureProgram"]["program"]["decisionTable"][dispatch_index]
    blob = next(
        item
        for item in artifact["modelFixtureProgram"]["program"]["blobCatalog"]
        if item["blobId"] == row["action"]["blobId"]
    )

    result = _invoke(adapter, artifact, dispatch)

    assert result.provider_state == "responded"
    assert result.response_bytes == base64.b64decode(blob["base64"], validate=True)
    assert result.response_sha256 == blob["sha256"]


@pytest.mark.parametrize("dispatch_index", [13, 14, 15])
def test_ambiguous_dispatches_have_one_call_and_no_response(
    adapter: FixtureModelAdapter,
    artifact: dict[str, Any],
    dispatch_index: int,
) -> None:
    result = _invoke(
        adapter, artifact, artifact["modelDispatchVectors"][dispatch_index]
    )

    assert result.kind == "drop-after-dispatch"
    assert result.provider_state == "ambiguous"
    assert result.provider_reference is None
    assert result.response_bytes is None
    assert result.response_sha256 is None
    assert result.logical_provider_call_delta == 1
    assert result.actual_network_call_delta == 0
    assert result.hidden_retry_delta == 0


def test_early_429_second_attempt_is_blocked_before_dispatch(
    adapter: FixtureModelAdapter, artifact: dict[str, Any]
) -> None:
    dispatch = artifact["modelDispatchVectors"][5]
    request_sha256 = dispatch["modelAttempt"]["payload"]["request_sha256"]

    with pytest.raises(FixtureDispatchRejected) as raised:
        adapter.invoke(
            planner_request_bytes=_requests_by_sha256(artifact)[request_sha256],
            credential_grant_bytes=_grant_bytes(dispatch),
            fixture_case_id="provider-rate-limit",
            purpose="plan",
            attempt_ordinal=2,
            dispatch_at="2026-08-20T12:00:04Z",
        )

    assert raised.value.reason == "blocked_by_guard"
    assert raised.value.logical_provider_call_delta == 0
    assert raised.value.actual_network_call_delta == 0
    assert raised.value.hidden_retry_delta == 0


def test_third_plan_attempt_is_rejected_before_dispatch(
    adapter: FixtureModelAdapter, artifact: dict[str, Any]
) -> None:
    dispatch = artifact["modelDispatchVectors"][0]
    request_sha256 = dispatch["modelAttempt"]["payload"]["request_sha256"]

    with pytest.raises(FixtureDispatchRejected) as raised:
        adapter.invoke(
            planner_request_bytes=_requests_by_sha256(artifact)[request_sha256],
            credential_grant_bytes=_grant_bytes(dispatch),
            fixture_case_id="happy-path-eva",
            purpose="plan",
            attempt_ordinal=3,
            dispatch_at=dispatch["dispatchAt"],
        )

    assert raised.value.reason == "rejected_by_guard"
    assert raised.value.logical_provider_call_delta == 0


def test_unknown_selector_is_rejected_without_a_provider_call(
    adapter: FixtureModelAdapter, artifact: dict[str, Any]
) -> None:
    dispatch = artifact["modelDispatchVectors"][0]
    request_sha256 = dispatch["modelAttempt"]["payload"]["request_sha256"]

    with pytest.raises(FixtureDispatchRejected) as raised:
        adapter.invoke(
            planner_request_bytes=_requests_by_sha256(artifact)[request_sha256],
            credential_grant_bytes=_grant_bytes(dispatch),
            fixture_case_id="unknown-fixture-case",
            purpose="plan",
            attempt_ordinal=1,
            dispatch_at=dispatch["dispatchAt"],
        )

    assert raised.value.reason == "unknown_selector"
    assert raised.value.logical_provider_call_delta == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("executable_sha256", "0" * 64),
        ("model_provider_profile_id", "fixture:model-provider:other:001"),
        ("worker_id", "worker:model-fixture-adapter:other"),
        ("provider_scopes", ["model.invoke:reply"]),
        ("deadline", "2026-08-20T12:00:44Z"),
    ],
)
def test_credential_grant_program_profile_scope_and_deadline_are_bound(
    adapter: FixtureModelAdapter,
    artifact: dict[str, Any],
    field: str,
    value: object,
) -> None:
    dispatch = artifact["modelDispatchVectors"][0]
    request_sha256 = dispatch["modelAttempt"]["payload"]["request_sha256"]
    mutated = dict(dispatch["credentialGrant"]["payload"])
    mutated[field] = value

    with pytest.raises(FixtureDispatchRejected) as raised:
        adapter.invoke(
            planner_request_bytes=_requests_by_sha256(artifact)[request_sha256],
            credential_grant_bytes=canonical_json_bytes(mutated),
            fixture_case_id=dispatch["fixtureCaseId"],
            purpose=dispatch["purpose"],
            attempt_ordinal=dispatch["attemptOrdinal"],
            dispatch_at=dispatch["dispatchAt"],
        )

    assert raised.value.reason == "invalid_credential_grant"
    assert raised.value.logical_provider_call_delta == 0


def test_request_purpose_and_deadline_are_bound_before_dispatch(
    adapter: FixtureModelAdapter, artifact: dict[str, Any]
) -> None:
    dispatch = artifact["modelDispatchVectors"][0]
    request_sha256 = dispatch["modelAttempt"]["payload"]["request_sha256"]
    original = json.loads(_requests_by_sha256(artifact)[request_sha256])

    for mutation in (
        {"deadline": "2026-08-20T12:00:44Z"},
        {"model_input": {**original["model_input"], "purpose": "reply"}},
    ):
        mutated = {**original, **mutation}
        with pytest.raises(FixtureDispatchRejected) as raised:
            adapter.invoke(
                planner_request_bytes=canonical_json_bytes(mutated),
                credential_grant_bytes=_grant_bytes(dispatch),
                fixture_case_id=dispatch["fixtureCaseId"],
                purpose=dispatch["purpose"],
                attempt_ordinal=dispatch["attemptOrdinal"],
                dispatch_at=dispatch["dispatchAt"],
            )
        assert raised.value.reason == "invalid_planner_request"
        assert raised.value.logical_provider_call_delta == 0


def test_noncanonical_request_is_rejected_before_dispatch(
    adapter: FixtureModelAdapter, artifact: dict[str, Any]
) -> None:
    dispatch = artifact["modelDispatchVectors"][0]
    request_sha256 = dispatch["modelAttempt"]["payload"]["request_sha256"]
    request = _requests_by_sha256(artifact)[request_sha256]

    with pytest.raises(FixtureDispatchRejected) as raised:
        adapter.invoke(
            planner_request_bytes=request + b" ",
            credential_grant_bytes=_grant_bytes(dispatch),
            fixture_case_id=dispatch["fixtureCaseId"],
            purpose=dispatch["purpose"],
            attempt_ordinal=dispatch["attemptOrdinal"],
            dispatch_at=dispatch["dispatchAt"],
        )

    assert raised.value.reason == "invalid_planner_request"
    assert raised.value.logical_provider_call_delta == 0
