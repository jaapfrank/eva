"""Small framework-neutral application flow for the sealed Noor 4D fixture.

This module composes the verified in-process model interpreter with a connector
port.  It proves call ordering and proof adoption in one process; the lifecycle
port deliberately does not claim database durability, transactionality, or
revision-CAS persistence.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeAlias, cast

from eva.contracts import canonical_json_bytes, canonical_sha256
from eva.contracts.connector import (
    ConnectorManifest,
    DefinitiveReject,
    EffectResponse,
    ProbeResult,
    RateLimited,
    ReadOk,
    ReadbackResult,
)
from eva.contracts.connector_rpc import (
    ConnectorErrorResponse,
    ConnectorInitializeRequest,
    ConnectorInvokeRequest,
    ConnectorInvokeSuccess,
    ConnectorProtocolFailure,
)
from eva.contracts.execution import (
    ClientAckChallengeClaimer,
    DeliveryReceipt,
    DeliveryReceiptV2,
    EffectReadback,
    EffectReceipt,
    P256SignatureVerifier,
)
from eva.contracts.ingress import DeliveryOffer, DeliveryOfferV2, IngressRequest
from eva.contracts.planning import (
    DocumentDraft,
    ModelDecision,
    ModelPurpose,
    OutcomeView,
    PresenceReply,
    ReplyCandidate,
)
from eva.contracts.canonical_json import require_canonical_bytes


PROFILE_ARTIFACT_SHA256 = (
    "7de5645bbcf61706f7babe16088a97825401512baec3eba949232cf76d06453a"
)
MODEL_ARTIFACT_SHA256 = (
    "3ac74e540ea49f43731113efd874af35acc0a31cd5340495a475c34d73081b3c"
)
CONNECTOR_ARTIFACT_SHA256 = (
    "a17451b03978a7018f7fe8b7143981ce7d9a1c596abc7ada261cc77c211534e5"
)

Noor4DScenario: TypeAlias = Literal[
    "happy_eva",
    "happy_mira",
    "model_rate_limit",
    "connector_rate_limit",
    "document_reject",
    "confirmation_reject",
    "model_provider_reject",
]
Noor4DFlowStatus: TypeAlias = Literal["completed", "partial_delivered", "stopped"]
Noor4DCommitmentState: TypeAlias = Literal["open", "fulfilled"]


class Noor4DArtifactError(ValueError):
    """A content-addressed fixture artifact is absent or inconsistent."""


class Noor4DFlowError(RuntimeError):
    """The application stopped before adopting unearned output or proof."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Noor4DModelCall:
    purpose: ModelPurpose
    attempt_ordinal: int
    dispatch_at: str
    fixture_case_id: str
    planner_request_bytes: bytes
    credential_grant_bytes: bytes
    expected_provider_state: str
    expected_provider_reference: str | None
    expected_response_sha256: str | None
    expected_result_kind: str
    expected_row_ordinal: int
    expected_logical_provider_call_delta: int
    expected_actual_network_call_delta: int
    expected_hidden_retry_delta: int

    @property
    def call_id(self) -> str:
        return f"model:{self.purpose}:{self.attempt_ordinal}"


@dataclass(frozen=True, slots=True)
class Noor4DConnectorCall:
    ordinal: int
    case_id: str
    worker_key: str
    current_state_sha256: str
    program_bytes: bytes
    manifest: ConnectorManifest
    initialize: ConnectorInitializeRequest
    invoke: ConnectorInvokeRequest
    observed_at: str
    expected_result_sha256: str
    expected_outcome_kind: str
    expected_effect_receipt_sha256: str | None = None
    expected_effect_readback_sha256: str | None = None
    expected_delivery_offer_sha256: str | None = None
    expected_delivery_receipt_sha256: str | None = None

    @property
    def call_id(self) -> str:
        return f"connector:{self.ordinal}:{self.case_id}"


Noor4DExternalCall: TypeAlias = Noor4DModelCall | Noor4DConnectorCall
Noor4DConnectorResult: TypeAlias = (
    ConnectorInvokeSuccess | ConnectorErrorResponse | ConnectorProtocolFailure
)


@dataclass(frozen=True, slots=True)
class Noor4DModelInvocationResult:
    """Application-owned projection of one model adapter invocation."""

    kind: Literal["return-contract-bytes", "return-http-result", "drop-after-dispatch"]
    provider_state: Literal[
        "responded", "rate_limited", "provider_rejected", "ambiguous"
    ]
    provider_reference: str | None
    response_bytes: bytes | None
    response_sha256: str | None
    row_ordinal: int
    logical_provider_call_delta: int
    actual_network_call_delta: int
    hidden_retry_delta: int


class Noor4DModelPort(Protocol):
    def invoke(
        self,
        *,
        planner_request_bytes: bytes,
        credential_grant_bytes: bytes,
        fixture_case_id: str,
        purpose: ModelPurpose,
        attempt_ordinal: int,
        dispatch_at: str,
    ) -> Noor4DModelInvocationResult: ...


@dataclass(frozen=True, slots=True)
class Noor4DConnectorObservation:
    """Connector result and independently obtained host-evidence candidates."""

    result: Noor4DConnectorResult
    effect_receipt: EffectReceipt | None = None
    effect_readback: EffectReadback | None = None
    delivery_offer: DeliveryOffer | DeliveryOfferV2 | None = None
    delivery_receipt: DeliveryReceipt | DeliveryReceiptV2 | None = None


class Noor4DConnectorPort(Protocol):
    def invoke(self, call: Noor4DConnectorCall) -> Noor4DConnectorObservation: ...


class Noor4DCallLifecycle(Protocol):
    """Ordering boundary only; implementations decide whether storage is durable."""

    def reserve(self, call: Noor4DExternalCall) -> None: ...

    def authorize(self, call: Noor4DExternalCall) -> None: ...

    def record_result(
        self,
        call: Noor4DExternalCall,
        *,
        result_kind: str,
        result_sha256: str | None,
    ) -> None: ...


class Noor4DDocumentCandidateHook(Protocol):
    """Fail-able handoff; it creates no object or proof authority by itself."""

    def prepare(self, draft: DocumentDraft) -> None: ...


@dataclass(frozen=True, slots=True)
class Noor4DRunSpec:
    scenario: Noor4DScenario
    identity: Literal["Eva", "Mira"]
    ingress: IngressRequest
    plan_calls: tuple[Noor4DModelCall, ...]
    document_call: Noor4DModelCall | None
    reply_call: Noor4DModelCall | None
    connector_calls: tuple[Noor4DConnectorCall, ...]
    expected_outcome_view: OutcomeView | None
    expected_reply_candidate: ReplyCandidate | None
    expected_presence_reply: PresenceReply | None
    expected_status: Noor4DFlowStatus


ModelTrace: TypeAlias = tuple[ModelPurpose, int, str, str]
ConnectorTrace: TypeAlias = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class Noor4DFlowState:
    """Earned in-memory state; this is not a database or snapshot claim."""

    ingress: IngressRequest
    identity: Literal["Eva", "Mira"]
    generation: int
    plan: ModelDecision | None
    document_draft: DocumentDraft | None
    reply_candidate: ReplyCandidate | None
    outcome_view: OutcomeView | None
    presence_reply: PresenceReply | None
    effect_receipts: tuple[EffectReceipt, ...]
    effect_readbacks: tuple[EffectReadback, ...]
    confirmation_delivery_receipt: DeliveryReceipt | None
    outcome_ack: DeliveryReceiptV2 | None
    model_trace: tuple[ModelTrace, ...]
    connector_trace: tuple[ConnectorTrace, ...]
    commitment_state: Noor4DCommitmentState


@dataclass(frozen=True, slots=True)
class Noor4DFlowResult:
    status: Noor4DFlowStatus
    reason: str | None
    state: Noor4DFlowState


@dataclass(slots=True)
class _Accumulator:
    plan: ModelDecision | None = None
    document_draft: DocumentDraft | None = None
    reply_candidate: ReplyCandidate | None = None
    outcome_view: OutcomeView | None = None
    presence_reply: PresenceReply | None = None
    effect_receipts: list[EffectReceipt] = field(default_factory=list)
    effect_readbacks: list[EffectReadback] = field(default_factory=list)
    confirmation_delivery_receipt: DeliveryReceipt | None = None
    outcome_ack: DeliveryReceiptV2 | None = None
    model_trace: list[ModelTrace] = field(default_factory=list)
    connector_trace: list[ConnectorTrace] = field(default_factory=list)
    business_reads: dict[str, ReadOk] = field(default_factory=dict)


_SCENARIO_BINDINGS: dict[
    Noor4DScenario,
    tuple[
        str | None,
        Literal["Eva", "Mira"],
        tuple[int, ...],
        int | None,
        int | None,
        str | None,
    ],
] = {
    "happy_eva": ("happyEva", "Eva", (0,), 1, 2, "evaSuccess"),
    "happy_mira": ("happyMira", "Mira", (0,), 1, 3, "miraSuccess"),
    "model_rate_limit": (
        "happyEva",
        "Eva",
        (4, 5),
        1,
        2,
        "evaSuccess",
    ),
    "connector_rate_limit": (
        "connectorRateLimit",
        "Eva",
        (0,),
        1,
        2,
        "evaSuccess",
    ),
    "document_reject": (
        "documentReject",
        "Eva",
        (0,),
        1,
        None,
        "documentRejected",
    ),
    "confirmation_reject": (
        "confirmationReject",
        "Eva",
        (0,),
        1,
        None,
        "confirmationRejected",
    ),
    "model_provider_reject": (None, "Eva", (12,), None, None, None),
}

_OUTCOME_VIEW_KEYS = {
    "evaSuccess": "success",
    "miraSuccess": "success",
    "documentRejected": "documentRejected",
    "confirmationRejected": "confirmationRejected",
}

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

_OUTCOME_VECTOR_KEYS = {
    "outcome.deliver.evaSuccess": "evaSuccess",
    "outcome.deliver.miraSuccess": "miraSuccess",
    "outcome.deliver.documentRejected": "documentRejected",
    "outcome.deliver.confirmationRejected": "confirmationRejected",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _artifact_object(data: bytes, expected_sha256: str, label: str) -> dict[str, Any]:
    if not isinstance(data, bytes):
        raise TypeError(f"{label} artifact must be bytes")
    if _sha256(data) != expected_sha256:
        raise Noor4DArtifactError(f"{label} artifact SHA-256 differs from authority")
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Noor4DArtifactError(f"{label} artifact is not valid JSON") from exc
    if type(value) is not dict:
        raise Noor4DArtifactError(f"{label} artifact must be an object")
    return cast(dict[str, Any], value)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise Noor4DArtifactError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _array(value: Any, label: str) -> list[Any]:
    if type(value) is not list:
        raise Noor4DArtifactError(f"{label} must be an array")
    return cast(list[Any], value)


def _metadata_bytes(value: Any, label: str) -> bytes:
    metadata = _mapping(value, label)
    try:
        encoded = metadata["base64"]
        if not isinstance(encoded, str):
            raise TypeError
        decoded = base64.b64decode(encoded, validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise Noor4DArtifactError(f"{label} has invalid base64 bytes") from exc
    if (
        metadata.get("encoding") != "base64"
        or metadata.get("profile") != "EvaCanonicalJSON/v1"
        or metadata.get("bom") is not False
        or metadata.get("trailingLf") is not False
        or metadata.get("byteLength") != len(decoded)
        or metadata.get("sha256") != _sha256(decoded)
    ):
        raise Noor4DArtifactError(f"{label} byte metadata is inconsistent")
    return decoded


def _canonical_wrapper_sha(value: Any, label: str) -> str:
    wrapper = _mapping(value, label)
    payload = wrapper.get("payload")
    expected = wrapper.get("canonicalSha256")
    length = wrapper.get("canonicalByteLength")
    if not isinstance(expected, str) or type(length) is not int:
        raise Noor4DArtifactError(f"{label} canonical metadata is invalid")
    encoded = canonical_json_bytes(payload)
    if len(encoded) != length or _sha256(encoded) != expected:
        raise Noor4DArtifactError(f"{label} canonical metadata is inconsistent")
    return expected


@dataclass(frozen=True, slots=True)
class Noor4DFixtureBundle:
    """Verified artifact bundle that derives calls, never terminal snapshots."""

    profile_sha256: str
    model_sha256: str
    connector_sha256: str
    _profile: dict[str, Any] = field(repr=False)
    _model: dict[str, Any] = field(repr=False)
    _connector: dict[str, Any] = field(repr=False)

    @classmethod
    def from_artifact_bytes(
        cls,
        *,
        profile_artifact_bytes: bytes,
        model_artifact_bytes: bytes,
        connector_artifact_bytes: bytes,
    ) -> Noor4DFixtureBundle:
        profile = _artifact_object(
            profile_artifact_bytes, PROFILE_ARTIFACT_SHA256, "profile"
        )
        model = _artifact_object(model_artifact_bytes, MODEL_ARTIFACT_SHA256, "model")
        connector = _artifact_object(
            connector_artifact_bytes, CONNECTOR_ARTIFACT_SHA256, "connector"
        )
        expected_contracts = (
            (profile, "Eva2Noor4DExecutionProfile/v1", "profile"),
            (model, "Eva24DModelVectors/v1", "model"),
            (connector, "Eva24DConnectorProviderFixtures/v1", "connector"),
        )
        for artifact, contract, label in expected_contracts:
            if artifact.get("contract") != contract:
                raise Noor4DArtifactError(f"{label} artifact contract is invalid")
        bundle = cls(
            profile_sha256=PROFILE_ARTIFACT_SHA256,
            model_sha256=MODEL_ARTIFACT_SHA256,
            connector_sha256=CONNECTOR_ARTIFACT_SHA256,
            _profile=profile,
            _model=model,
            _connector=connector,
        )
        bundle._validate_cross_artifact_schedule()
        return bundle

    def _validate_cross_artifact_schedule(self) -> None:
        happy = _mapping(self._profile.get("happyPath"), "profile happyPath")
        profile_calls = _array(happy.get("connectorCalls"), "profile connectorCalls")
        expected_operations = [
            _mapping(item, "profile connector call").get("operation")
            for item in profile_calls
        ]
        scenarios = _mapping(self._connector.get("scenarios"), "connector scenarios")
        happy_eva = _mapping(scenarios.get("happyEva"), "connector happyEva")
        calls = _array(happy_eva.get("calls"), "connector happyEva calls")
        actual_operations = [
            _mapping(
                _mapping(
                    _mapping(
                        _mapping(
                            self._connector["languageNeutralRpcConformance"],
                            "connector RPC conformance",
                        )["conformanceCases"],
                        "connector conformance cases",
                    )[case_id],
                    f"connector case {case_id}",
                )["invokeSuccess"],
                f"connector success {case_id}",
            )["result"]["operation"]
            for case_id in calls
        ]
        if len(calls) != 11 or expected_operations != actual_operations:
            raise Noor4DArtifactError(
                "profile and connector happy-path schedules are inconsistent"
            )
        vectors = _array(self._model.get("happyPathVectors"), "model happy vectors")
        if [
            _mapping(vector, "model happy vector").get("purpose") for vector in vectors
        ] != ["plan", "document_draft", "reply", "reply"]:
            raise Noor4DArtifactError("model happy-vector purpose order is invalid")

    def scenario(self, scenario: str) -> Noor4DRunSpec:
        if scenario not in _SCENARIO_BINDINGS:
            raise Noor4DArtifactError(f"unknown Noor 4D scenario: {scenario}")
        name = cast(Noor4DScenario, scenario)
        (
            connector_scenario,
            identity,
            plan_indexes,
            document_index,
            reply_index,
            outcome_key,
        ) = _SCENARIO_BINDINGS[name]
        plan_calls = tuple(self._model_call(index) for index in plan_indexes)
        document_call = (
            self._model_call(document_index) if document_index is not None else None
        )
        reply_call = self._model_call(reply_index) if reply_index is not None else None
        if connector_scenario is None:
            return Noor4DRunSpec(
                scenario=name,
                identity=identity,
                ingress=self._ingress("evaSuccess"),
                plan_calls=plan_calls,
                document_call=None,
                reply_call=None,
                connector_calls=(),
                expected_outcome_view=None,
                expected_reply_candidate=None,
                expected_presence_reply=None,
                expected_status="stopped",
            )
        scenarios = _mapping(self._connector["scenarios"], "connector scenarios")
        source = _mapping(
            scenarios.get(connector_scenario),
            f"connector scenario {connector_scenario}",
        )
        case_ids = _array(source.get("calls"), f"{connector_scenario} calls")
        connector_calls = tuple(
            self._connector_call(name, ordinal, cast(str, case_id))
            for ordinal, case_id in enumerate(case_ids, start=1)
        )
        if outcome_key is None:
            raise Noor4DArtifactError(
                "terminal connector scenario lacks an outcome key"
            )
        content = _mapping(
            _mapping(self._connector["hostEvidence"], "hostEvidence").get(
                "outcomeDeliveryContent"
            ),
            "outcomeDeliveryContent",
        )
        view_key = _OUTCOME_VIEW_KEYS[outcome_key]
        outcome_view = OutcomeView.model_validate(
            _mapping(content["outcomeViews"], "outcomeViews")[view_key]["payload"]
        )
        reply_candidate = ReplyCandidate.model_validate(
            _mapping(content["replyCandidates"], "replyCandidates")[outcome_key][
                "payload"
            ]
        )
        presence_reply = PresenceReply.model_validate(
            _mapping(content["presenceReplies"], "presenceReplies")[outcome_key][
                "payload"
            ]
        )
        expected_status: Noor4DFlowStatus = (
            "completed"
            if name
            in {
                "happy_eva",
                "happy_mira",
                "model_rate_limit",
                "connector_rate_limit",
            }
            else "partial_delivered"
        )
        return Noor4DRunSpec(
            scenario=name,
            identity=identity,
            ingress=self._ingress(outcome_key),
            plan_calls=plan_calls,
            document_call=document_call,
            reply_call=reply_call,
            connector_calls=connector_calls,
            expected_outcome_view=outcome_view,
            expected_reply_candidate=reply_candidate,
            expected_presence_reply=presence_reply,
            expected_status=expected_status,
        )

    def _model_call(self, index: int) -> Noor4DModelCall:
        dispatches = _array(self._model["modelDispatchVectors"], "model dispatches")
        try:
            dispatch = _mapping(dispatches[index], f"model dispatch {index}")
        except IndexError as exc:
            raise Noor4DArtifactError("model dispatch index is absent") from exc
        attempt = _mapping(
            _mapping(dispatch["modelAttempt"], "modelAttempt").get("payload"),
            "modelAttempt payload",
        )
        request_sha256 = attempt.get("request_sha256")
        if not isinstance(request_sha256, str):
            raise Noor4DArtifactError("model attempt request hash is invalid")
        request_bytes = self._request_bytes(request_sha256)
        grant_bytes = _metadata_bytes(
            _mapping(dispatch["credentialGrant"], "credentialGrant").get(
                "canonicalBytes"
            ),
            "credentialGrant canonicalBytes",
        )
        purpose = dispatch.get("purpose")
        if purpose not in {"plan", "document_draft", "reply"}:
            raise Noor4DArtifactError("model dispatch purpose is invalid")
        ordinal = dispatch.get("attemptOrdinal")
        if type(ordinal) is not int:
            raise Noor4DArtifactError("model dispatch ordinal is invalid")
        fixture_case_id = dispatch.get("fixtureCaseId")
        dispatch_at = dispatch.get("dispatchAt")
        state = attempt.get("state")
        response_sha256 = attempt.get("response_sha256")
        provider_reference = _mapping(
            dispatch["eva_core.model_attempts"], "model attempt row"
        ).get("provider_reference")
        rows = _array(
            _mapping(
                _mapping(self._model["modelFixtureProgram"], "model fixture program")[
                    "program"
                ],
                "model fixture program payload",
            )["decisionTable"],
            "model decision table",
        )
        row = _mapping(rows[index], f"model program row {index}")
        action = _mapping(row["action"], f"model program action {index}")
        if (
            not isinstance(fixture_case_id, str)
            or not isinstance(dispatch_at, str)
            or not isinstance(state, str)
            or (
                provider_reference is not None
                and not isinstance(provider_reference, str)
            )
            or (response_sha256 is not None and not isinstance(response_sha256, str))
            or not isinstance(action.get("kind"), str)
            or type(row.get("rowOrdinal")) is not int
            or type(action.get("logicalProviderCallDelta")) is not int
            or type(action.get("actualNetworkCallDelta")) is not int
            or type(action.get("hiddenRetryDelta")) is not int
        ):
            raise Noor4DArtifactError("model dispatch binding is invalid")
        return Noor4DModelCall(
            purpose=cast(ModelPurpose, purpose),
            attempt_ordinal=ordinal,
            dispatch_at=dispatch_at,
            fixture_case_id=fixture_case_id,
            planner_request_bytes=request_bytes,
            credential_grant_bytes=grant_bytes,
            expected_provider_state=state,
            expected_provider_reference=cast(str | None, provider_reference),
            expected_response_sha256=cast(str | None, response_sha256),
            expected_result_kind=cast(str, action["kind"]),
            expected_row_ordinal=cast(int, row["rowOrdinal"]),
            expected_logical_provider_call_delta=cast(
                int, action["logicalProviderCallDelta"]
            ),
            expected_actual_network_call_delta=cast(
                int, action["actualNetworkCallDelta"]
            ),
            expected_hidden_retry_delta=cast(int, action["hiddenRetryDelta"]),
        )

    def _request_bytes(self, expected_sha256: str) -> bytes:
        vectors = _array(self._model["happyPathVectors"], "model happy vectors")
        for vector_value in vectors:
            vector = _mapping(vector_value, "model happy vector")
            metadata = _mapping(
                vector.get("requestCanonicalBytes"), "requestCanonicalBytes"
            )
            if metadata.get("sha256") == expected_sha256:
                return _metadata_bytes(metadata, "requestCanonicalBytes")
        raise Noor4DArtifactError(
            "model dispatch request is absent from sealed happy vectors"
        )

    def _connector_call(
        self, scenario: Noor4DScenario, ordinal: int, case_id: str
    ) -> Noor4DConnectorCall:
        rpc = _mapping(
            self._connector["languageNeutralRpcConformance"],
            "connector RPC conformance",
        )
        cases = _mapping(rpc["conformanceCases"], "connector conformance cases")
        conformance = _mapping(cases.get(case_id), f"connector case {case_id}")
        try:
            initialize = ConnectorInitializeRequest.model_validate(
                conformance["initializeRequest"]
            )
            invoke = ConnectorInvokeRequest.model_validate(conformance["invokeRequest"])
            expected_success = ConnectorInvokeSuccess.model_validate(
                conformance["invokeSuccess"]
            )
        except (KeyError, ValueError) as exc:
            raise Noor4DArtifactError(
                f"connector case {case_id} has invalid RPC contracts"
            ) from exc
        connector_id = initialize.params.connector_id
        if not connector_id.endswith("_fixture"):
            raise Noor4DArtifactError("fixture connector ID has no worker suffix")
        worker_key = connector_id.removesuffix("_fixture")
        manifests = _mapping(self._connector["connectorManifests"], "manifests")
        programs = _mapping(self._connector["fixtureWorkerPrograms"], "programs")
        try:
            manifest = ConnectorManifest.model_validate(
                _mapping(manifests[worker_key], "manifest entry")["payload"]
            )
            program_bytes = canonical_json_bytes(
                _mapping(programs[worker_key], "program entry")["payload"]
            )
        except (KeyError, ValueError) as exc:
            raise Noor4DArtifactError(
                f"connector worker {worker_key} is invalid"
            ) from exc
        operation_cases = _mapping(
            self._connector["operationCallCases"], "operation cases"
        )
        operation_case = _mapping(operation_cases.get(case_id), case_id)
        states = _mapping(
            _mapping(self._connector["providerStateMachine"], "provider state")[
                "states"
            ],
            "provider states",
        )
        state_name = operation_case.get("providerStateBefore")
        state = _mapping(states.get(state_name), f"provider state {state_name}")
        current_state_sha256 = state.get("canonicalSha256")
        observed_at = operation_case.get("completedAt")
        outcome_kind = operation_case.get("outcomeKind")
        if (
            not isinstance(current_state_sha256, str)
            or not isinstance(observed_at, str)
            or not isinstance(outcome_kind, str)
        ):
            raise Noor4DArtifactError(f"connector case {case_id} metadata is invalid")
        if scenario == "connector_rate_limit" and case_id in {
            "calendar.probe.ready",
            "calendar.create.happy",
        }:
            observed_at = "2026-08-20T12:00:04Z"
        (
            effect_receipt_sha256,
            effect_readback_sha256,
            delivery_offer_sha256,
            delivery_receipt_sha256,
        ) = self._expected_evidence(case_id)
        return Noor4DConnectorCall(
            ordinal=ordinal,
            case_id=case_id,
            worker_key=worker_key,
            current_state_sha256=current_state_sha256,
            program_bytes=program_bytes,
            manifest=manifest,
            initialize=initialize,
            invoke=invoke,
            observed_at=observed_at,
            expected_result_sha256=canonical_sha256(expected_success),
            expected_outcome_kind=outcome_kind,
            expected_effect_receipt_sha256=effect_receipt_sha256,
            expected_effect_readback_sha256=effect_readback_sha256,
            expected_delivery_offer_sha256=delivery_offer_sha256,
            expected_delivery_receipt_sha256=delivery_receipt_sha256,
        )

    def _expected_evidence(
        self, case_id: str
    ) -> tuple[str | None, str | None, str | None, str | None]:
        host = _mapping(self._connector["hostEvidence"], "hostEvidence")
        effect_sha256 = None
        readback_sha256 = None
        offer_sha256 = None
        receipt_sha256 = None
        effect_key = _EFFECT_RECEIPT_KEYS.get(case_id)
        if effect_key is not None:
            receipts = _mapping(host["acceptedEffectReceipts"], "effect receipts")
            effect_sha256 = _canonical_wrapper_sha(
                receipts[effect_key], f"effect receipt {effect_key}"
            )
        readback_key = _READBACK_KEYS.get(case_id)
        if readback_key is not None:
            readbacks = _mapping(host["providerReadbacks"], "provider readbacks")
            readback_sha256 = _canonical_wrapper_sha(
                readbacks[readback_key], f"provider readback {readback_key}"
            )
        if case_id == "confirmation.readback.matches":
            offer_sha256 = _canonical_wrapper_sha(
                host["confirmationDeliveryOffer"], "confirmation delivery offer"
            )
            receipt_sha256 = _canonical_wrapper_sha(
                host["confirmationDeliveryReceipt"], "confirmation delivery receipt"
            )
        outcome_key = _OUTCOME_VECTOR_KEYS.get(case_id)
        if outcome_key is not None:
            vectors = _mapping(host["outcomeAckVectors"], "outcome ACK vectors")
            vector = _mapping(vectors[outcome_key], f"outcome vector {outcome_key}")
            offer_sha256 = _canonical_wrapper_sha(
                vector["deliveryOffer"], f"outcome offer {outcome_key}"
            )
            receipt_sha256 = _canonical_wrapper_sha(
                vector["deliveryReceipt"], f"outcome receipt {outcome_key}"
            )
        return effect_sha256, readback_sha256, offer_sha256, receipt_sha256

    def _ingress(self, outcome_key: str) -> IngressRequest:
        host = _mapping(self._connector["hostEvidence"], "hostEvidence")
        vectors = _mapping(host["outcomeAckVectors"], "outcome ACK vectors")
        vector = _mapping(vectors.get(outcome_key), f"outcome vector {outcome_key}")
        try:
            return IngressRequest.model_validate(
                _mapping(vector["ingress"], "ingress wrapper")["payload"]
            )
        except (KeyError, ValueError) as exc:
            raise Noor4DArtifactError("sealed ingress is invalid") from exc


def _record_model_call(
    *,
    call: Noor4DModelCall,
    model: Noor4DModelPort,
    lifecycle: Noor4DCallLifecycle,
) -> Noor4DModelInvocationResult:
    lifecycle.reserve(call)
    lifecycle.authorize(call)
    result = model.invoke(
        planner_request_bytes=call.planner_request_bytes,
        credential_grant_bytes=call.credential_grant_bytes,
        fixture_case_id=call.fixture_case_id,
        purpose=call.purpose,
        attempt_ordinal=call.attempt_ordinal,
        dispatch_at=call.dispatch_at,
    )
    if result.provider_state != call.expected_provider_state:
        raise Noor4DFlowError(
            "model_result_mismatch", "model provider state differs from sealed call"
        )
    if result.response_sha256 != call.expected_response_sha256:
        raise Noor4DFlowError(
            "model_result_mismatch", "model response hash differs from sealed call"
        )
    if (
        result.kind != call.expected_result_kind
        or result.provider_reference != call.expected_provider_reference
        or result.row_ordinal != call.expected_row_ordinal
        or result.logical_provider_call_delta
        != call.expected_logical_provider_call_delta
        or result.actual_network_call_delta != call.expected_actual_network_call_delta
        or result.hidden_retry_delta != call.expected_hidden_retry_delta
    ):
        raise Noor4DFlowError(
            "model_call_delta_mismatch", "model call delta differs from sealed program"
        )
    if result.response_bytes is None:
        if result.response_sha256 is not None:
            raise Noor4DFlowError(
                "model_result_mismatch",
                "model response hash is present without response bytes",
            )
    elif (
        result.response_sha256 is None
        or _sha256(result.response_bytes) != result.response_sha256
    ):
        raise Noor4DFlowError(
            "model_result_mismatch",
            "model response bytes differ from their bound SHA-256",
        )
    if result.provider_state == "responded":
        model_by_purpose = {
            "plan": ModelDecision,
            "document_draft": DocumentDraft,
            "reply": ReplyCandidate,
        }
        _model_contract(
            result,
            model_by_purpose[call.purpose],
            f"{call.purpose} model response",
        )
    elif result.provider_state == "rate_limited":
        _validate_rate_limit(result)
    elif result.provider_state == "provider_rejected":
        _validate_provider_reject(result)
    elif (
        result.provider_state != "ambiguous"
        or result.response_bytes is not None
        or result.response_sha256 is not None
    ):
        raise Noor4DFlowError(
            "model_result_mismatch", "model result shape differs from provider state"
        )
    lifecycle.record_result(
        call,
        result_kind=result.provider_state,
        result_sha256=result.response_sha256,
    )
    return result


def _model_contract(
    result: Noor4DModelInvocationResult,
    model: type[ModelDecision] | type[DocumentDraft] | type[ReplyCandidate],
    label: str,
) -> ModelDecision | DocumentDraft | ReplyCandidate:
    if (
        result.kind != "return-contract-bytes"
        or result.provider_state != "responded"
        or result.response_bytes is None
    ):
        raise Noor4DFlowError(
            "model_contract_absent", f"{label} did not return contract bytes"
        )
    try:
        payload = require_canonical_bytes(result.response_bytes)
        parsed = model.model_validate(payload)
    except ValueError as exc:
        raise Noor4DFlowError(
            "invalid_model_contract", f"{label} returned an invalid contract"
        ) from exc
    if canonical_sha256(parsed) != result.response_sha256:
        raise Noor4DFlowError(
            "model_contract_hash_mismatch", f"{label} contract hash is inconsistent"
        )
    return parsed


def _validate_rate_limit(result: Noor4DModelInvocationResult) -> None:
    if (
        result.kind != "return-http-result"
        or result.provider_state != "rate_limited"
        or result.response_bytes is None
    ):
        raise Noor4DFlowError(
            "invalid_model_rate_limit", "model 429 result is not the sealed HTTP result"
        )
    try:
        value = require_canonical_bytes(result.response_bytes)
        headers = {
            item["name"]: item["value"]
            for item in _array(value.get("headers"), "model HTTP headers")
            if type(item) is dict
        }
    except (KeyError, ValueError, Noor4DArtifactError) as exc:
        raise Noor4DFlowError(
            "invalid_model_rate_limit", "model 429 result is malformed"
        ) from exc
    if value.get("status") != 429 or headers.get("retry-after") != "5":
        raise Noor4DFlowError(
            "invalid_model_rate_limit", "model 429 Retry-After differs from authority"
        )


def _validate_provider_reject(result: Noor4DModelInvocationResult) -> None:
    if result.kind != "return-http-result" or result.response_bytes is None:
        raise Noor4DFlowError(
            "invalid_model_provider_reject",
            "model provider reject is not a sealed HTTP result",
        )
    try:
        value = require_canonical_bytes(result.response_bytes)
    except ValueError as exc:
        raise Noor4DFlowError(
            "invalid_model_provider_reject", "model provider reject is malformed"
        ) from exc
    if value.get("status") != 422:
        raise Noor4DFlowError(
            "invalid_model_provider_reject", "model provider reject status is not 422"
        )


def _record_connector_call(
    *,
    call: Noor4DConnectorCall,
    connector: Noor4DConnectorPort,
    lifecycle: Noor4DCallLifecycle,
) -> Noor4DConnectorObservation:
    lifecycle.reserve(call)
    lifecycle.authorize(call)
    observation = connector.invoke(call)
    result = observation.result
    if not isinstance(result, ConnectorInvokeSuccess):
        raise Noor4DFlowError(
            "connector_transport_failure",
            f"connector {call.case_id} returned no successful typed result",
        )
    result_sha256 = canonical_sha256(result)
    if result_sha256 != call.expected_result_sha256:
        raise Noor4DFlowError(
            "connector_result_mismatch",
            f"connector {call.case_id} differs from sealed result bytes",
        )
    if result.result.outcome.kind != call.expected_outcome_kind:
        raise Noor4DFlowError(
            "connector_outcome_mismatch",
            f"connector {call.case_id} returned another outcome kind",
        )
    lifecycle.record_result(
        call,
        result_kind=result.result.outcome.kind,
        result_sha256=result_sha256,
    )
    return observation


def _evidence_sha(value: Any) -> str | None:
    return canonical_sha256(value) if value is not None else None


def _validate_evidence(
    call: Noor4DConnectorCall,
    observation: Noor4DConnectorObservation,
    accumulator: _Accumulator,
) -> None:
    expected = (
        call.expected_effect_receipt_sha256,
        call.expected_effect_readback_sha256,
        call.expected_delivery_offer_sha256,
        call.expected_delivery_receipt_sha256,
    )
    actual = (
        _evidence_sha(observation.effect_receipt),
        _evidence_sha(observation.effect_readback),
        _evidence_sha(observation.delivery_offer),
        _evidence_sha(observation.delivery_receipt),
    )
    if expected != actual:
        if (
            call.case_id == "calendar.readback.matches"
            and observation.effect_readback is None
        ):
            raise Noor4DFlowError(
                "missing_calendar_readback", "calendar readback evidence is missing"
            )
        raise Noor4DFlowError(
            "connector_evidence_mismatch",
            f"connector {call.case_id} evidence differs from sealed candidates",
        )
    result = cast(ConnectorInvokeSuccess, observation.result).result
    outcome = result.outcome
    receipt = observation.effect_receipt
    if receipt is not None:
        if receipt.request_payload_sha256 != call.invoke.params.request_payload_sha256:
            raise Noor4DFlowError(
                "effect_receipt_mismatch", "effect receipt binds another payload"
            )
        if isinstance(outcome, EffectResponse):
            if (
                receipt.decision != "accepted"
                or receipt.provider_resource_id != outcome.provider_resource_id
                or receipt.provider_receipt_sha256 != outcome.provider_receipt_sha256
            ):
                raise Noor4DFlowError(
                    "effect_receipt_mismatch",
                    "accepted effect receipt differs from connector response",
                )
        elif isinstance(outcome, DefinitiveReject):
            if receipt.decision != "rejected":
                raise Noor4DFlowError(
                    "effect_receipt_mismatch",
                    "definitive reject lacks a rejected effect receipt",
                )
        accumulator.effect_receipts.append(receipt)
    readback = observation.effect_readback
    if readback is not None:
        if not isinstance(outcome, ReadbackResult):
            raise Noor4DFlowError(
                "readback_mismatch", "readback evidence accompanies another outcome"
            )
        if (
            readback.match_status != outcome.match_status
            or readback.provider_resource_id != outcome.provider_resource_id
            or readback.match_status != "matches"
        ):
            raise Noor4DFlowError(
                "readback_mismatch", "readback evidence does not prove an exact match"
            )
        matching_receipt = next(
            (
                item
                for item in accumulator.effect_receipts
                if item.effect_intent_id == readback.effect_intent_id
            ),
            None,
        )
        if (
            matching_receipt is None
            or matching_receipt.provider_resource_id != readback.provider_resource_id
        ):
            raise Noor4DFlowError(
                "readback_mismatch", "readback has no matching accepted effect receipt"
            )
        accumulator.effect_readbacks.append(readback)
    if isinstance(observation.delivery_receipt, DeliveryReceipt):
        if not isinstance(observation.delivery_offer, DeliveryOffer):
            raise Noor4DFlowError(
                "confirmation_proof_mismatch",
                "confirmation receipt lacks its v1 delivery offer",
            )
        observation.delivery_receipt.validate_against_offer(observation.delivery_offer)
        if observation.delivery_receipt.proof.mode != "server":
            raise Noor4DFlowError(
                "confirmation_proof_mismatch",
                "confirmation delivery proof is not the sealed server proof",
            )
        proof_readback_id = observation.delivery_receipt.proof.independent_readback_id
        if not any(
            item.readback_id == proof_readback_id
            for item in accumulator.effect_readbacks
        ):
            raise Noor4DFlowError(
                "confirmation_proof_mismatch",
                "confirmation receipt precedes its independent readback proof",
            )
        accumulator.confirmation_delivery_receipt = observation.delivery_receipt


def _validate_business_chain(
    accumulator: _Accumulator, calendar_call: Noor4DConnectorCall
) -> None:
    try:
        search = accumulator.business_reads["mail.search.happy"].payload
        mail = accumulator.business_reads["mail.read.happy"]
        erp = accumulator.business_reads["erp.lookup.unique"].payload
        availability = accumulator.business_reads["calendar.availability.happy"].payload
        if (
            search.representation != "inline"
            or mail.payload.representation != "inline"
            or erp.representation != "inline"
            or availability.representation != "inline"
        ):
            raise KeyError
        search_value = cast(dict[str, Any], search.normalized_payload)
        mail_value = cast(dict[str, Any], mail.payload.normalized_payload)
        erp_value = cast(dict[str, Any], erp.normalized_payload)
        availability_value = cast(dict[str, Any], availability.normalized_payload)
        message = cast(list[dict[str, Any]], search_value["messages"])[0]
        match = cast(list[dict[str, Any]], erp_value["matches"])[0]
        slot = cast(list[dict[str, Any]], availability_value["availableSlots"])[0]
        payload = cast(dict[str, Any], calendar_call.invoke.params.request_payload)
    except (KeyError, IndexError, TypeError) as exc:
        raise Noor4DFlowError(
            "business_read_chain_mismatch", "four business reads are not usable"
        ) from exc
    if (
        message.get("mailId") != mail.provider_resource_id
        or mail_value.get("contentClass") != "untrusted_external_data"
        or erp_value.get("matchCardinality") != 1
        or match.get("confirmedEmail") not in payload.get("attendees", [])
        or slot.get("start") != payload.get("start")
        or slot.get("end") != payload.get("end")
        or availability_value.get("timezone") != payload.get("timezone")
    ):
        raise Noor4DFlowError(
            "business_read_chain_mismatch",
            "calendar effect is not derived from the four sealed business reads",
        )


def _validate_document_call(draft: DocumentDraft, call: Noor4DConnectorCall) -> None:
    payload = call.invoke.params.request_payload
    if type(payload) is not dict:
        raise Noor4DFlowError(
            "document_payload_mismatch", "document connector payload is not an object"
        )
    value = cast(dict[str, Any], payload)
    content_bytes = draft.content.encode("utf-8")
    if (
        value.get("title") != draft.title
        or value.get("mimeType") != draft.mime_type
        or value.get("content") != draft.content
        or value.get("contentByteLength") != len(content_bytes)
        or value.get("contentSha256") != _sha256(content_bytes)
        or value.get("retentionUntil") != draft.retention_until
    ):
        raise Noor4DFlowError(
            "document_payload_mismatch",
            "document connector payload differs from accepted DocumentDraft",
        )


def _validate_confirmation_call(
    draft: DocumentDraft, call: Noor4DConnectorCall, accumulator: _Accumulator
) -> None:
    payload = call.invoke.params.request_payload
    if type(payload) is not dict:
        raise Noor4DFlowError(
            "confirmation_payload_mismatch",
            "confirmation connector payload is not an object",
        )
    erp = accumulator.business_reads.get("erp.lookup.unique")
    if erp is None or erp.payload.representation != "inline":
        raise Noor4DFlowError(
            "confirmation_payload_mismatch", "confirmation has no unique ERP result"
        )
    erp_value = cast(dict[str, Any], erp.payload.normalized_payload)
    matches = cast(list[dict[str, Any]], erp_value.get("matches", []))
    value = cast(dict[str, Any], payload)
    content_sha256 = _sha256(draft.content.encode("utf-8"))
    if (
        len(matches) != 1
        or value.get("recipient") != matches[0].get("confirmedEmail")
        or value.get("sourceCustomerId") != matches[0].get("customerId")
        or value.get("content") != draft.content
        or value.get("contentSha256") != content_sha256
    ):
        raise Noor4DFlowError(
            "confirmation_payload_mismatch",
            "confirmation is not bound to the draft and unique ERP recipient",
        )


def _validate_presence(
    *,
    spec: Noor4DRunSpec,
    candidate: ReplyCandidate,
    outcome_view: OutcomeView,
    presence: PresenceReply,
    accumulator: _Accumulator,
) -> None:
    if (
        presence.outcome_view_sha256 != canonical_sha256(outcome_view)
        or presence.reply_candidate_sha256 != canonical_sha256(candidate)
        or presence.text != candidate.text
        or presence.episode_id != candidate.episode_id
        or presence.generation != candidate.generation
        or candidate.identity_id != presence.identity_id
    ):
        raise Noor4DFlowError(
            "presence_binding_mismatch",
            "PresenceReply does not bind the accepted outcome and reply candidate",
        )
    expected_identity = (
        "identity:eva:fixture" if spec.identity == "Eva" else "identity:mira:fixture"
    )
    if presence.identity_id != expected_identity:
        raise Noor4DFlowError(
            "identity_binding_mismatch", "PresenceReply uses another active identity"
        )
    earned_refs = {item.readback_id for item in accumulator.effect_readbacks}
    if accumulator.confirmation_delivery_receipt is not None:
        earned_refs.add(accumulator.confirmation_delivery_receipt.delivery_receipt_id)
    claimed_refs = {
        evidence_ref
        for binding in presence.claim_bindings
        for evidence_ref in binding.evidence_refs
    }
    if not claimed_refs.issubset(earned_refs):
        raise Noor4DFlowError(
            "unearned_presence_claim", "PresenceReply contains an unearned claim"
        )


def _validate_outcome_payload(
    call: Noor4DConnectorCall, presence: PresenceReply, ingress: IngressRequest
) -> None:
    payload = call.invoke.params.request_payload
    if type(payload) is not dict:
        raise Noor4DFlowError(
            "outcome_payload_mismatch", "outcome connector payload is not an object"
        )
    value = cast(dict[str, Any], payload)
    text_bytes = presence.text.encode("utf-8")
    if (
        value.get("presenceReplySha256") != canonical_sha256(presence)
        or value.get("wireText") != presence.text
        or value.get("wireSha256") != _sha256(text_bytes)
        or value.get("requestRef") != ingress.request_id
    ):
        raise Noor4DFlowError(
            "outcome_payload_mismatch",
            "outcome connector payload differs from PresenceReply bytes",
        )


def _flow_state(
    spec: Noor4DRunSpec,
    accumulator: _Accumulator,
    *,
    commitment_state: Noor4DCommitmentState,
) -> Noor4DFlowState:
    return Noor4DFlowState(
        ingress=spec.ingress,
        identity=spec.identity,
        generation=1,
        plan=accumulator.plan,
        document_draft=accumulator.document_draft,
        reply_candidate=accumulator.reply_candidate,
        outcome_view=accumulator.outcome_view,
        presence_reply=accumulator.presence_reply,
        effect_receipts=tuple(accumulator.effect_receipts),
        effect_readbacks=tuple(accumulator.effect_readbacks),
        confirmation_delivery_receipt=accumulator.confirmation_delivery_receipt,
        outcome_ack=accumulator.outcome_ack,
        model_trace=tuple(accumulator.model_trace),
        connector_trace=tuple(accumulator.connector_trace),
        commitment_state=commitment_state,
    )


def run_noor_4d(
    *,
    spec: Noor4DRunSpec,
    model: Noor4DModelPort,
    connector: Noor4DConnectorPort,
    lifecycle: Noor4DCallLifecycle,
    document_candidate_hook: Noor4DDocumentCandidateHook,
    ack_signature_verifier: P256SignatureVerifier,
    ack_challenge_claimer: ClientAckChallengeClaimer,
) -> Noor4DFlowResult:
    """Execute one sealed Noor flow without DB, DBOS, API, or snapshot input."""

    accumulator = _Accumulator()
    for plan_call in spec.plan_calls:
        result = _record_model_call(call=plan_call, model=model, lifecycle=lifecycle)
        accumulator.model_trace.append(
            (
                plan_call.purpose,
                plan_call.attempt_ordinal,
                result.provider_state,
                plan_call.dispatch_at,
            )
        )
        if result.provider_state == "rate_limited":
            _validate_rate_limit(result)
            continue
        if result.provider_state == "provider_rejected":
            if spec.scenario != "model_provider_reject":
                raise Noor4DFlowError(
                    "unexpected_model_reject",
                    "model provider rejected another scenario",
                )
            return Noor4DFlowResult(
                status="stopped",
                reason="model_provider_rejected",
                state=_flow_state(spec, accumulator, commitment_state="open"),
            )
        parsed = _model_contract(result, ModelDecision, "plan")
        if not isinstance(parsed, ModelDecision):
            raise AssertionError("model contract reader returned another type")
        accumulator.plan = parsed
    if accumulator.plan is None:
        raise Noor4DFlowError("plan_absent", "no accepted plan was produced")
    if not spec.connector_calls:
        raise Noor4DFlowError(
            "connector_schedule_absent", "accepted plan has no connector schedule"
        )

    outcome_call = spec.connector_calls[-1]
    for call in spec.connector_calls[:-1]:
        if call.case_id.startswith("document.create"):
            if spec.document_call is None:
                raise Noor4DFlowError(
                    "document_model_call_absent", "document effect has no model draft"
                )
            if accumulator.document_draft is None:
                model_result = _record_model_call(
                    call=spec.document_call,
                    model=model,
                    lifecycle=lifecycle,
                )
                accumulator.model_trace.append(
                    (
                        spec.document_call.purpose,
                        spec.document_call.attempt_ordinal,
                        model_result.provider_state,
                        spec.document_call.dispatch_at,
                    )
                )
                parsed = _model_contract(model_result, DocumentDraft, "document draft")
                if not isinstance(parsed, DocumentDraft):
                    raise AssertionError("model contract reader returned another type")
                accumulator.document_draft = parsed
                _validate_document_call(parsed, call)
                document_candidate_hook.prepare(parsed)
        if call.case_id.startswith("calendar.create") and call.case_id != (
            "calendar.create.rate-limited"
        ):
            _validate_business_chain(accumulator, call)
        if call.case_id.startswith("confirmation.deliver"):
            if accumulator.document_draft is None:
                raise Noor4DFlowError(
                    "document_proof_absent", "confirmation precedes a document draft"
                )
            _validate_confirmation_call(accumulator.document_draft, call, accumulator)
        observation = _record_connector_call(
            call=call, connector=connector, lifecycle=lifecycle
        )
        result = cast(ConnectorInvokeSuccess, observation.result).result
        accumulator.connector_trace.append(
            (call.case_id, result.outcome.kind, call.observed_at)
        )
        _validate_evidence(call, observation, accumulator)
        if isinstance(result.outcome, ReadOk):
            accumulator.business_reads[call.case_id] = result.outcome
        if isinstance(result.outcome, RateLimited):
            if (
                spec.scenario != "connector_rate_limit"
                or call.case_id != "calendar.create.rate-limited"
                or result.outcome.retry_after_ms != 4_000
            ):
                raise Noor4DFlowError(
                    "unexpected_connector_rate_limit",
                    "connector rate limit differs from sealed recovery",
                )
        if isinstance(result.outcome, ProbeResult):
            if (
                spec.scenario != "connector_rate_limit"
                or result.outcome.result != "ready"
                or call.observed_at != "2026-08-20T12:00:04Z"
            ):
                raise Noor4DFlowError(
                    "connector_probe_mismatch", "connector recovery probe is invalid"
                )
        if isinstance(result.outcome, DefinitiveReject):
            allowed = {
                "document_reject": "document.create.definitive-reject",
                "confirmation_reject": "confirmation.deliver.definitive-reject",
            }
            if allowed.get(spec.scenario) != call.case_id:
                raise Noor4DFlowError(
                    "unexpected_definitive_reject",
                    "connector definitive reject is not a sealed branch",
                )

    if spec.expected_outcome_view is None or spec.expected_reply_candidate is None:
        raise Noor4DFlowError(
            "outcome_authority_absent", "terminal flow lacks sealed outcome authority"
        )
    accumulator.outcome_view = spec.expected_outcome_view
    candidate: ReplyCandidate
    if spec.reply_call is not None:
        model_result = _record_model_call(
            call=spec.reply_call, model=model, lifecycle=lifecycle
        )
        accumulator.model_trace.append(
            (
                spec.reply_call.purpose,
                spec.reply_call.attempt_ordinal,
                model_result.provider_state,
                spec.reply_call.dispatch_at,
            )
        )
        parsed = _model_contract(model_result, ReplyCandidate, "reply")
        if not isinstance(parsed, ReplyCandidate):
            raise AssertionError("model contract reader returned another type")
        if canonical_sha256(parsed) != canonical_sha256(spec.expected_reply_candidate):
            raise Noor4DFlowError(
                "reply_candidate_mismatch",
                "model reply differs from sealed host-validated candidate",
            )
        candidate = parsed
    else:
        candidate = spec.expected_reply_candidate
    accumulator.reply_candidate = candidate
    presence = spec.expected_presence_reply
    if presence is None:
        raise Noor4DFlowError(
            "presence_reply_absent", "terminal connector call lacks PresenceReply"
        )
    _validate_presence(
        spec=spec,
        candidate=candidate,
        outcome_view=spec.expected_outcome_view,
        presence=presence,
        accumulator=accumulator,
    )
    _validate_outcome_payload(outcome_call, presence, spec.ingress)
    outcome_observation = _record_connector_call(
        call=outcome_call, connector=connector, lifecycle=lifecycle
    )
    outcome_result = cast(ConnectorInvokeSuccess, outcome_observation.result).result
    accumulator.connector_trace.append(
        (outcome_call.case_id, outcome_result.outcome.kind, outcome_call.observed_at)
    )
    _validate_evidence(outcome_call, outcome_observation, accumulator)
    if not isinstance(
        outcome_observation.delivery_offer, DeliveryOfferV2
    ) or not isinstance(outcome_observation.delivery_receipt, DeliveryReceiptV2):
        raise Noor4DFlowError(
            "outcome_ack_absent", "outcome delivery did not return a v2 client ACK"
        )
    if not isinstance(outcome_result.outcome, EffectResponse):
        raise Noor4DFlowError(
            "outcome_delivery_failed", "outcome connector did not accept delivery"
        )
    offer = outcome_observation.delivery_offer
    receipt = outcome_observation.delivery_receipt
    if offer.content_sha256 != canonical_sha256(presence):
        raise Noor4DFlowError(
            "outcome_ack_mismatch", "outcome offer binds another PresenceReply"
        )
    try:
        persisted_receipt = receipt.validate_against_offer(
            offer,
            spec.ingress,
            signature_verifier=ack_signature_verifier,
            challenge_claimer=ack_challenge_claimer,
        )
    except (TypeError, ValueError) as exc:
        raise Noor4DFlowError(
            "outcome_ack_mismatch", "outcome client ACK failed host validation"
        ) from exc
    accumulator.outcome_ack = persisted_receipt
    accumulator.presence_reply = presence
    commitment: Noor4DCommitmentState = (
        "fulfilled" if spec.expected_status == "completed" else "open"
    )
    return Noor4DFlowResult(
        status=spec.expected_status,
        reason=None,
        state=_flow_state(spec, accumulator, commitment_state=commitment),
    )


__all__ = [
    "CONNECTOR_ARTIFACT_SHA256",
    "MODEL_ARTIFACT_SHA256",
    "PROFILE_ARTIFACT_SHA256",
    "Noor4DArtifactError",
    "Noor4DCallLifecycle",
    "Noor4DConnectorCall",
    "Noor4DConnectorObservation",
    "Noor4DConnectorPort",
    "Noor4DDocumentCandidateHook",
    "Noor4DExternalCall",
    "Noor4DFixtureBundle",
    "Noor4DFlowError",
    "Noor4DFlowResult",
    "Noor4DFlowState",
    "Noor4DModelCall",
    "Noor4DModelInvocationResult",
    "Noor4DModelPort",
    "Noor4DRunSpec",
    "run_noor_4d",
]
