from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from eva.adapters.connectors.process import (
    ConnectorFixtureSelection,
    ConnectorProcessSpec,
    run_connector_process,
)
from eva.adapters.model import FixtureModelAdapter
from eva.adapters.objects.p256 import P256AckVerifier
from eva.application.noor_4d import (
    Noor4DArtifactError,
    Noor4DCallLifecycle,
    Noor4DConnectorCall,
    Noor4DConnectorObservation,
    Noor4DConnectorPort,
    Noor4DDocumentCandidateHook,
    Noor4DExternalCall,
    Noor4DFixtureBundle,
    Noor4DFlowError,
    Noor4DModelInvocationResult,
    Noor4DModelPort,
    run_noor_4d,
)
from eva.contracts import canonical_json_bytes, canonical_sha256
from eva.contracts.execution import (
    ClientAckChallengeClaim,
    DeliveryReceipt,
    DeliveryReceiptV2,
    EffectReadback,
    EffectReceipt,
)
from eva.contracts.ingress import DeliveryOffer, DeliveryOfferV2
from eva.contracts.planning import DocumentDraft


EVA2_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = EVA2_ROOT.parent
PROFILE_PATH = EVA2_ROOT / "design/vertical/noor-4d-execution-profile-v1.json"
MODEL_PATH = EVA2_ROOT / "design/vertical/noor-4d-model-vectors-v1.json"
CONNECTOR_PATH = (
    EVA2_ROOT / "design/vertical/noor-4d-connector-provider-fixtures-v1.json"
)
ACK_KEY_PATH = REPOSITORY_ROOT / "design/fixtures/noor-client-ack-key-v1.json"
CONNECTOR_SHA256 = "a17451b03978a7018f7fe8b7143981ce7d9a1c596abc7ada261cc77c211534e5"


@pytest.fixture(scope="module")
def bundle() -> Noor4DFixtureBundle:
    return Noor4DFixtureBundle.from_artifact_bytes(
        profile_artifact_bytes=PROFILE_PATH.read_bytes(),
        model_artifact_bytes=MODEL_PATH.read_bytes(),
        connector_artifact_bytes=CONNECTOR_PATH.read_bytes(),
    )


@pytest.fixture(scope="module")
def model_adapter() -> FixtureModelAdapter:
    return FixtureModelAdapter.from_artifact_bytes(MODEL_PATH.read_bytes())


class RecordingLifecycle(Noor4DCallLifecycle):
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def reserve(self, call: Noor4DExternalCall) -> None:
        self.events.append(("reserve", call.call_id))

    def authorize(self, call: Noor4DExternalCall) -> None:
        self.events.append(("authorize", call.call_id))

    def record_result(
        self,
        call: Noor4DExternalCall,
        *,
        result_kind: str,
        result_sha256: str | None,
    ) -> None:
        del result_kind, result_sha256
        self.events.append(("result", call.call_id))


class RecordingDocumentHook(Noor4DDocumentCandidateHook):
    def __init__(self) -> None:
        self.drafts: list[DocumentDraft] = []

    def prepare(self, draft: DocumentDraft) -> None:
        self.drafts.append(draft)


class FixtureModelPort(Noor4DModelPort):
    def __init__(self, adapter: FixtureModelAdapter) -> None:
        self.adapter = adapter

    def invoke(self, **kwargs: Any) -> Noor4DModelInvocationResult:
        result = self.adapter.invoke(**kwargs)
        return Noor4DModelInvocationResult(
            kind=result.kind,
            provider_state=result.provider_state,
            provider_reference=result.provider_reference,
            response_bytes=result.response_bytes,
            response_sha256=result.response_sha256,
            row_ordinal=result.row_ordinal,
            logical_provider_call_delta=result.logical_provider_call_delta,
            actual_network_call_delta=result.actual_network_call_delta,
            hidden_retry_delta=result.hidden_retry_delta,
        )


class NetworkClaimingModelPort(FixtureModelPort):
    def invoke(self, **kwargs: Any) -> Noor4DModelInvocationResult:
        return replace(super().invoke(**kwargs), actual_network_call_delta=1)


class AlteredHttpModelPort(FixtureModelPort):
    def invoke(self, **kwargs: Any) -> Noor4DModelInvocationResult:
        result = super().invoke(**kwargs)
        if result.provider_state != "rate_limited" or result.response_bytes is None:
            return result
        value = json.loads(result.response_bytes)
        value["headers"][1]["value"] = "6"
        return replace(result, response_bytes=canonical_json_bytes(value))


_EFFECT_RECEIPT_KEYS = {
    "calendar.create.happy": "calendarAccepted",
    "document.create.happy": "documentAccepted",
    "document.create.definitive-reject": "documentRejected",
    "confirmation.deliver.happy": "confirmationAccepted",
    "confirmation.deliver.definitive-reject": "confirmationRejected",
    "outcome.deliver.evaSuccess": "outcomeEvaSuccess",
    "outcome.deliver.miraSuccess": "outcomeMiraSuccess",
    "outcome.deliver.documentRejected": "outcomeDocumentRejected",
    "outcome.deliver.confirmationRejected": "outcomeConfirmationRejected",
}
_READBACK_KEYS = {
    "calendar.readback.matches": "calendar",
    "document.readback.matches": "document",
    "confirmation.readback.matches": "confirmation",
}
_OUTCOME_KEYS = {
    "outcome.deliver.evaSuccess": "evaSuccess",
    "outcome.deliver.miraSuccess": "miraSuccess",
    "outcome.deliver.documentRejected": "documentRejected",
    "outcome.deliver.confirmationRejected": "confirmationRejected",
}


class RealFixtureConnectorPort(Noor4DConnectorPort):
    """Real process adapter plus the separately sealed host-evidence candidates."""

    def __init__(self) -> None:
        self.artifact: dict[str, Any] = json.loads(CONNECTOR_PATH.read_bytes())
        self.calls: list[str] = []

    def invoke(self, call: Noor4DConnectorCall) -> Noor4DConnectorObservation:
        self.calls.append(call.case_id)
        command = (
            sys.executable,
            "-I",
            str(EVA2_ROOT / f"connectors/{call.worker_key}/worker.py"),
            "--artifact",
            str(CONNECTOR_PATH),
            "--artifact-sha256",
            CONNECTOR_SHA256,
            "--current-state-sha256",
            call.current_state_sha256,
            "--case-id",
            call.case_id,
        )
        result = run_connector_process(
            spec=ConnectorProcessSpec(
                command=command,
                program_bytes=call.program_bytes,
                fixture_selection=ConnectorFixtureSelection(
                    artifact_path=CONNECTOR_PATH,
                    artifact_sha256=CONNECTOR_SHA256,
                    worker_key=call.worker_key,
                    case_id=call.case_id,
                    current_state_sha256=call.current_state_sha256,
                ),
            ),
            manifest=call.manifest,
            initialize=call.initialize,
            invoke=call.invoke,
            observed_at=call.observed_at,
            timeout_seconds=10,
        )
        host = self.artifact["hostEvidence"]
        effect_receipt = None
        readback = None
        delivery_offer = None
        delivery_receipt = None
        effect_key = _EFFECT_RECEIPT_KEYS.get(call.case_id)
        if effect_key is not None:
            effect_receipt = EffectReceipt.model_validate(
                host["acceptedEffectReceipts"][effect_key]["payload"]
            )
        readback_key = _READBACK_KEYS.get(call.case_id)
        if readback_key is not None:
            readback = EffectReadback.model_validate(
                host["providerReadbacks"][readback_key]["payload"]
            )
        if call.case_id == "confirmation.readback.matches":
            delivery_offer = DeliveryOffer.model_validate(
                host["confirmationDeliveryOffer"]["payload"]
            )
            delivery_receipt = DeliveryReceipt.model_validate(
                host["confirmationDeliveryReceipt"]["payload"]
            )
        outcome_key = _OUTCOME_KEYS.get(call.case_id)
        if outcome_key is not None:
            vector = host["outcomeAckVectors"][outcome_key]
            delivery_offer = DeliveryOfferV2.model_validate(
                vector["deliveryOffer"]["payload"]
            )
            delivery_receipt = DeliveryReceiptV2.model_validate(
                vector["deliveryReceipt"]["payload"]
            )
        return Noor4DConnectorObservation(
            result=result,
            effect_receipt=effect_receipt,
            effect_readback=readback,
            delivery_offer=delivery_offer,
            delivery_receipt=delivery_receipt,
        )


class MissingCalendarReadbackPort(RealFixtureConnectorPort):
    def invoke(self, call: Noor4DConnectorCall) -> Noor4DConnectorObservation:
        observation = super().invoke(call)
        if call.case_id == "calendar.readback.matches":
            return replace(observation, effect_readback=None)
        return observation


class WrongFirstResultPort(RealFixtureConnectorPort):
    def invoke(self, call: Noor4DConnectorCall) -> Noor4DConnectorObservation:
        observation = super().invoke(call)
        if call.case_id == "mail.search.happy":
            return replace(
                observation,
                result=observation.result.model_copy(
                    update={"id": "fixture:rpc:invoke:wrong-result:001"}
                ),
            )
        return observation


def _ack_ports():
    fixture = json.loads(ACK_KEY_PATH.read_bytes())
    verifier = P256AckVerifier.from_public_jwk(
        fixture["publicJwk"],
        expected_sha256=fixture["publicJwkCanonicalSha256"],
    )
    claims: list[str] = []

    def claim(
        request_ref: str,
        delivery_id: str,
        challenge: str,
        ack_proof_sha256: str,
        receipt: DeliveryReceiptV2,
    ) -> ClientAckChallengeClaim:
        del request_ref, delivery_id, ack_proof_sha256
        claims.append(challenge)
        return ClientAckChallengeClaim.model_validate(
            {"status": "claimed", "receipt": receipt.model_dump(mode="json")}
        )

    return verifier, claim, claims


def _run(
    bundle: Noor4DFixtureBundle,
    model_adapter: FixtureModelAdapter,
    scenario: str,
    *,
    connector: Noor4DConnectorPort | None = None,
):
    return _run_spec(
        bundle.scenario(scenario),
        FixtureModelPort(model_adapter),
        connector=connector,
    )


def _run_spec(
    spec,
    model: Noor4DModelPort,
    *,
    connector: Noor4DConnectorPort | None = None,
):
    lifecycle = RecordingLifecycle()
    hook = RecordingDocumentHook()
    verifier, claimer, claims = _ack_ports()
    actual_connector = connector or RealFixtureConnectorPort()
    result = run_noor_4d(
        spec=spec,
        model=model,
        connector=actual_connector,
        lifecycle=lifecycle,
        document_candidate_hook=hook,
        ack_signature_verifier=verifier,
        ack_challenge_claimer=claimer,
    )
    return result, actual_connector, lifecycle, hook, claims


def _call_ids(lifecycle: RecordingLifecycle) -> list[str]:
    assert all(
        lifecycle.events[index : index + 3]
        == [
            ("reserve", lifecycle.events[index][1]),
            ("authorize", lifecycle.events[index][1]),
            ("result", lifecycle.events[index][1]),
        ]
        for index in range(0, len(lifecycle.events), 3)
    )
    return [call_id for action, call_id in lifecycle.events if action == "result"]


def test_bundle_is_content_addressed_and_rejects_one_changed_byte() -> None:
    profile = PROFILE_PATH.read_bytes()
    model = MODEL_PATH.read_bytes()
    connector = CONNECTOR_PATH.read_bytes()
    loaded = Noor4DFixtureBundle.from_artifact_bytes(
        profile_artifact_bytes=profile,
        model_artifact_bytes=model,
        connector_artifact_bytes=connector,
    )
    assert loaded.profile_sha256 == (
        "7de5645bbcf61706f7babe16088a97825401512baec3eba949232cf76d06453a"
    )
    assert loaded.model_sha256 == (
        "3ac74e540ea49f43731113efd874af35acc0a31cd5340495a475c34d73081b3c"
    )
    assert loaded.connector_sha256 == CONNECTOR_SHA256

    with pytest.raises(Noor4DArtifactError, match="profile artifact SHA-256"):
        Noor4DFixtureBundle.from_artifact_bytes(
            profile_artifact_bytes=profile + b" ",
            model_artifact_bytes=model,
            connector_artifact_bytes=connector,
        )


@pytest.mark.parametrize(
    ("scenario", "outcome_case", "identity", "reply_sha256", "wire_sha256"),
    (
        (
            "happy_eva",
            "outcome.deliver.evaSuccess",
            "identity:eva:fixture",
            "773dbd6d32a8a94d6f1f246c50fc8df0d3d9786a83986e317eab89df9cecc11b",
            "8934f46980f62691fd136edfc7427ce1980954c9f5b6747678b5cc0617c412df",
        ),
        (
            "happy_mira",
            "outcome.deliver.miraSuccess",
            "identity:mira:fixture",
            "73fd43973da9abb7ea2fede2876c1dae165e3f891fed825603c2aa07b025d15a",
            "d1a6d6b1066de9a8ac9b3a09c9806a314604d5212c466eaf2c19ab51b6fff56d",
        ),
    ),
)
def test_happy_eva_and_mira_execute_real_interleaved_flow_and_close_only_on_ack(
    bundle: Noor4DFixtureBundle,
    model_adapter: FixtureModelAdapter,
    scenario: str,
    outcome_case: str,
    identity: str,
    reply_sha256: str,
    wire_sha256: str,
) -> None:
    result, connector, lifecycle, hook, claims = _run(bundle, model_adapter, scenario)

    assert result.status == "completed"
    assert result.state.presence_reply is not None
    assert result.state.presence_reply.identity_id == identity
    assert result.state.reply_candidate is not None
    assert canonical_sha256(result.state.reply_candidate) == reply_sha256
    assert result.state.outcome_ack is not None
    assert result.state.outcome_ack.wire_sha256 == wire_sha256
    assert result.state.commitment_state == "fulfilled"
    assert len(result.state.effect_receipts) == 4
    assert len(result.state.effect_readbacks) == 3
    assert result.state.confirmation_delivery_receipt is not None
    assert len(claims) == 1
    assert len(hook.drafts) == 1
    assert isinstance(connector, RealFixtureConnectorPort)
    assert connector.calls == [
        "mail.search.happy",
        "mail.read.happy",
        "erp.lookup.unique",
        "calendar.availability.happy",
        "calendar.create.happy",
        "calendar.readback.matches",
        "document.create.happy",
        "document.readback.matches",
        "confirmation.deliver.happy",
        "confirmation.readback.matches",
        outcome_case,
    ]
    assert _call_ids(lifecycle) == [
        "model:plan:1",
        "connector:1:mail.search.happy",
        "connector:2:mail.read.happy",
        "connector:3:erp.lookup.unique",
        "connector:4:calendar.availability.happy",
        "connector:5:calendar.create.happy",
        "connector:6:calendar.readback.matches",
        "model:document_draft:1",
        "connector:7:document.create.happy",
        "connector:8:document.readback.matches",
        "connector:9:confirmation.deliver.happy",
        "connector:10:confirmation.readback.matches",
        "model:reply:1",
        f"connector:11:{outcome_case}",
    ]


def test_identity_swap_changes_only_reply_and_outcome_surface(
    bundle: Noor4DFixtureBundle, model_adapter: FixtureModelAdapter
) -> None:
    eva, *_ = _run(bundle, model_adapter, "happy_eva")
    mira, *_ = _run(bundle, model_adapter, "happy_mira")

    assert eva.state.plan == mira.state.plan
    assert eva.state.document_draft == mira.state.document_draft
    assert eva.state.effect_readbacks == mira.state.effect_readbacks
    assert eva.state.effect_receipts[:3] == mira.state.effect_receipts[:3]
    assert eva.state.reply_candidate != mira.state.reply_candidate
    assert eva.state.presence_reply != mira.state.presence_reply


def test_model_429_has_one_explicit_waited_plan_retry_and_no_hidden_call(
    bundle: Noor4DFixtureBundle, model_adapter: FixtureModelAdapter
) -> None:
    result, connector, lifecycle, _, _ = _run(bundle, model_adapter, "model_rate_limit")

    assert result.status == "completed"
    assert isinstance(connector, RealFixtureConnectorPort)
    assert len(connector.calls) == 11
    call_ids = _call_ids(lifecycle)
    assert call_ids[:2] == ["model:plan:1", "model:plan:2"]
    assert call_ids[2] == "connector:1:mail.search.happy"
    assert result.state.model_trace == (
        ("plan", 1, "rate_limited", "2026-08-20T12:00:00Z"),
        ("plan", 2, "responded", "2026-08-20T12:00:05Z"),
        ("document_draft", 1, "responded", "2026-08-20T12:01:00Z"),
        ("reply", 1, "responded", "2026-08-20T12:01:30Z"),
    )


def test_connector_429_waits_then_probes_before_ordinal_two_effect(
    bundle: Noor4DFixtureBundle, model_adapter: FixtureModelAdapter
) -> None:
    result, connector, _, _, _ = _run(bundle, model_adapter, "connector_rate_limit")

    assert result.status == "completed"
    assert isinstance(connector, RealFixtureConnectorPort)
    assert connector.calls[4:8] == [
        "calendar.create.rate-limited",
        "calendar.probe.ready",
        "calendar.create.happy",
        "calendar.readback.matches",
    ]
    assert len(connector.calls) == 13
    assert result.state.connector_trace[4:7] == (
        ("calendar.create.rate-limited", "rate_limited", "2026-08-20T12:00:00Z"),
        ("calendar.probe.ready", "probe_result", "2026-08-20T12:00:04Z"),
        ("calendar.create.happy", "effect_response", "2026-08-20T12:00:04Z"),
    )
    assert len(result.state.effect_receipts) == 4


@pytest.mark.parametrize(
    ("scenario", "status_text", "connector_cases", "readback_count"),
    (
        (
            "document_reject",
            "De afspraak met Noor BV staat op woensdag 26 augustus 2026 van 15.00 tot 15.30 uur in de agenda. De bevestiging kon ik niet maken, dus ik heb niets naar Noor verstuurd.",
            [
                "mail.search.happy",
                "mail.read.happy",
                "erp.lookup.unique",
                "calendar.availability.happy",
                "calendar.create.happy",
                "calendar.readback.matches",
                "document.create.definitive-reject",
                "outcome.deliver.documentRejected",
            ],
            1,
        ),
        (
            "confirmation_reject",
            "De afspraak met Noor BV staat op woensdag 26 augustus 2026 van 15.00 tot 15.30 uur in de agenda. De bevestiging is gemaakt, maar niet verstuurd naar planning@noorbv.example.",
            [
                "mail.search.happy",
                "mail.read.happy",
                "erp.lookup.unique",
                "calendar.availability.happy",
                "calendar.create.happy",
                "calendar.readback.matches",
                "document.create.happy",
                "document.readback.matches",
                "confirmation.deliver.definitive-reject",
                "outcome.deliver.confirmationRejected",
            ],
            2,
        ),
    ),
)
def test_definitive_rejects_keep_only_proven_partial_result_and_do_not_resend(
    bundle: Noor4DFixtureBundle,
    model_adapter: FixtureModelAdapter,
    scenario: str,
    status_text: str,
    connector_cases: list[str],
    readback_count: int,
) -> None:
    result, connector, _, _, claims = _run(bundle, model_adapter, scenario)

    assert result.status == "partial_delivered"
    assert result.state.presence_reply is not None
    assert result.state.presence_reply.text == status_text
    assert result.state.reply_candidate is not None
    assert result.state.presence_reply.reply_candidate_sha256 == canonical_sha256(
        result.state.reply_candidate
    )
    assert len(result.state.model_trace) == 2
    assert result.state.outcome_ack is not None
    assert len(result.state.effect_readbacks) == readback_count
    assert len(claims) == 1
    assert isinstance(connector, RealFixtureConnectorPort)
    assert connector.calls == connector_cases


def test_model_422_stops_after_one_attempt_without_invented_visible_result(
    bundle: Noor4DFixtureBundle, model_adapter: FixtureModelAdapter
) -> None:
    result, connector, lifecycle, hook, claims = _run(
        bundle, model_adapter, "model_provider_reject"
    )

    assert result.status == "stopped"
    assert result.reason == "model_provider_rejected"
    assert result.state.presence_reply is None
    assert result.state.outcome_ack is None
    assert result.state.commitment_state == "open"
    assert isinstance(connector, RealFixtureConnectorPort)
    assert connector.calls == []
    assert _call_ids(lifecycle) == ["model:plan:1"]
    assert hook.drafts == []
    assert claims == []


def test_missing_calendar_readback_proof_fails_before_document_or_success_claim(
    bundle: Noor4DFixtureBundle, model_adapter: FixtureModelAdapter
) -> None:
    connector = MissingCalendarReadbackPort()

    with pytest.raises(Noor4DFlowError, match="calendar readback evidence"):
        _run(
            bundle,
            model_adapter,
            "happy_eva",
            connector=connector,
        )

    assert connector.calls == [
        "mail.search.happy",
        "mail.read.happy",
        "erp.lookup.unique",
        "calendar.availability.happy",
        "calendar.create.happy",
        "calendar.readback.matches",
    ]


@pytest.mark.parametrize(
    "model_port_type",
    (NetworkClaimingModelPort, AlteredHttpModelPort),
)
def test_unbound_model_result_is_not_recorded_by_lifecycle(
    bundle: Noor4DFixtureBundle,
    model_adapter: FixtureModelAdapter,
    model_port_type: type[FixtureModelPort],
) -> None:
    scenario = (
        "model_rate_limit" if model_port_type is AlteredHttpModelPort else "happy_eva"
    )
    lifecycle = RecordingLifecycle()
    hook = RecordingDocumentHook()
    connector = RealFixtureConnectorPort()
    verifier, claimer, _ = _ack_ports()

    with pytest.raises(Noor4DFlowError):
        run_noor_4d(
            spec=bundle.scenario(scenario),
            model=model_port_type(model_adapter),
            connector=connector,
            lifecycle=lifecycle,
            document_candidate_hook=hook,
            ack_signature_verifier=verifier,
            ack_challenge_claimer=claimer,
        )

    assert lifecycle.events == [
        ("reserve", "model:plan:1"),
        ("authorize", "model:plan:1"),
    ]
    assert connector.calls == []


def test_unbound_connector_result_is_not_recorded_by_lifecycle(
    bundle: Noor4DFixtureBundle, model_adapter: FixtureModelAdapter
) -> None:
    lifecycle = RecordingLifecycle()
    hook = RecordingDocumentHook()
    connector = WrongFirstResultPort()
    verifier, claimer, _ = _ack_ports()

    with pytest.raises(Noor4DFlowError, match="differs from sealed result bytes"):
        run_noor_4d(
            spec=bundle.scenario("happy_eva"),
            model=FixtureModelPort(model_adapter),
            connector=connector,
            lifecycle=lifecycle,
            document_candidate_hook=hook,
            ack_signature_verifier=verifier,
            ack_challenge_claimer=claimer,
        )

    assert lifecycle.events[-2:] == [
        ("reserve", "connector:1:mail.search.happy"),
        ("authorize", "connector:1:mail.search.happy"),
    ]
    assert ("result", "connector:1:mail.search.happy") not in lifecycle.events


def test_wrong_non_null_outcome_request_ref_stops_before_outcome_dispatch(
    bundle: Noor4DFixtureBundle, model_adapter: FixtureModelAdapter
) -> None:
    spec = bundle.scenario("happy_eva")
    outcome_call = spec.connector_calls[-1]
    payload = dict(outcome_call.invoke.params.request_payload)
    payload["requestRef"] = "fixture:http-request:wrong:001"
    mutated_params = outcome_call.invoke.params.model_copy(
        update={
            "request_payload": payload,
            "request_payload_sha256": canonical_sha256(payload),
        }
    )
    mutated_call = replace(
        outcome_call,
        invoke=outcome_call.invoke.model_copy(update={"params": mutated_params}),
    )
    mutated_spec = replace(
        spec, connector_calls=(*spec.connector_calls[:-1], mutated_call)
    )
    connector = RealFixtureConnectorPort()

    with pytest.raises(Noor4DFlowError, match="outcome connector payload"):
        _run_spec(
            mutated_spec,
            FixtureModelPort(model_adapter),
            connector=connector,
        )

    assert connector.calls == [call.case_id for call in spec.connector_calls[:-1]]
