"""Test-only framework-neutral flow for the sealed historical Noor 4D fixture.

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
    ClaimBinding,
    DocumentDraft,
    ModelDecision,
    ModelPurpose,
    OutcomeView,
    PlannerRequest,
    PresenceReply,
    ReplyCandidate,
    ReplyModelInput,
    StepOutcome,
)
from eva.contracts.canonical_json import require_canonical_bytes
from eva.contracts.identity import IdentityPack
from eva.application.objects import (
    AuthenticatedCandidateReadback,
    ObjectBinding,
    ObjectBindingError,
)


PROFILE_ARTIFACT_SHA256 = (
    "17279a5064790398693573af1e2baf7988a496c716f5e37a7c8880763817b7fe"
)
MODEL_ARTIFACT_SHA256 = (
    "e0a9fca6aed7f5146dd0255ba9ae4cdee4fa15dc274398e535cab68c8e28f593"
)
CONNECTOR_ARTIFACT_SHA256 = (
    "7655b181889d95f1140aea114be4934724931814898b4cee134151f57c2009c6"
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
Noor4DProofScope: TypeAlias = Literal[
    "eva_installation_exact", "identity_surface_only", "none"
]
Noor4DReplySource: TypeAlias = Literal[
    "model_receipt", "host_fallback_template", "identity_surface_vector", "none"
]


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
    """Create an authenticated pre-selection candidate, never an ObjectRef."""

    def prepare(
        self, request: Noor4DDocumentCandidateRequest
    ) -> AuthenticatedCandidateReadback | None: ...


@dataclass(frozen=True, slots=True)
class Noor4DDocumentCandidateRequest:
    draft: DocumentDraft
    binding: ObjectBinding | None
    plaintext: bytes


@dataclass(frozen=True, slots=True)
class Noor4DHostValidation:
    """Host-built fixture validation preimage for one visible reply."""

    vector_id: str
    identity_pack_sha256: str
    outcome_view_sha256: str
    text_sha256: str
    unproven_claims: Literal[0]
    forbidden_internal_jargon: Literal[0]
    result: Literal["accepted"]

    @property
    def payload(self) -> dict[str, object]:
        return {
            "contract": "FixturePresenceHostValidation/v1",
            "schema_version": 1,
            "vector_id": self.vector_id,
            "identityPackSha256": self.identity_pack_sha256,
            "outcomeViewSha256": self.outcome_view_sha256,
            "textSha256": self.text_sha256,
            "unprovenClaims": self.unproven_claims,
            "forbiddenInternalJargon": self.forbidden_internal_jargon,
            "result": self.result,
        }

    @property
    def canonical_sha256(self) -> str:
        return _sha256(canonical_json_bytes(self.payload))


@dataclass(frozen=True, slots=True)
class _OutcomeStepTemplate:
    kind: Literal["mail", "erp", "calendar", "document", "confirmation"]
    step_id: str
    human_summary: str
    business_evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ClaimTemplate:
    claim_ref: str
    step_id: str


@dataclass(frozen=True, slots=True)
class _OutputTemplate:
    steps: tuple[_OutcomeStepTemplate, ...]
    message_id: str
    claim_bindings: tuple[_ClaimTemplate, ...]
    host_validation_vector_id: str
    expected_host_validation_sha256: str
    fallback_reply_template: ReplyCandidate


@dataclass(frozen=True, slots=True)
class Noor4DRunSpec:
    scenario: Noor4DScenario
    identity: Literal["Eva", "Mira"]
    ingress: IngressRequest
    plan_calls: tuple[Noor4DModelCall, ...]
    document_call: Noor4DModelCall | None
    reply_call: Noor4DModelCall | None
    connector_calls: tuple[Noor4DConnectorCall, ...]
    identity_pack: IdentityPack
    identity_pack_sha256: str
    proof_scope: Noor4DProofScope
    document_binding: ObjectBinding | None
    output_template: _OutputTemplate | None
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
    document_candidate_readback: AuthenticatedCandidateReadback | None
    reply_candidate: ReplyCandidate | None
    reply_source: Noor4DReplySource
    outcome_view: OutcomeView | None
    presence_reply: PresenceReply | None
    host_validation: Noor4DHostValidation | None
    identity_pack_sha256: str
    proof_scope: Noor4DProofScope
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
    document_candidate_readback: AuthenticatedCandidateReadback | None = None
    reply_candidate: ReplyCandidate | None = None
    reply_source: Noor4DReplySource = "none"
    outcome_view: OutcomeView | None = None
    presence_reply: PresenceReply | None = None
    host_validation: Noor4DHostValidation | None = None
    effect_receipts: list[EffectReceipt] = field(default_factory=list)
    effect_readbacks: list[EffectReadback] = field(default_factory=list)
    confirmation_delivery_receipt: DeliveryReceipt | None = None
    outcome_ack: DeliveryReceiptV2 | None = None
    model_trace: list[ModelTrace] = field(default_factory=list)
    connector_trace: list[ConnectorTrace] = field(default_factory=list)
    business_reads: dict[str, ReadOk] = field(default_factory=dict)
    effect_receipts_by_case: dict[str, EffectReceipt] = field(default_factory=dict)
    effect_readbacks_by_case: dict[str, EffectReadback] = field(default_factory=dict)
    business_chain_validated: bool = False


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
        identity_pack, identity_pack_sha256 = self._identity_pack(identity)
        if connector_scenario is None:
            return Noor4DRunSpec(
                scenario=name,
                identity=identity,
                ingress=self._ingress("evaSuccess"),
                plan_calls=plan_calls,
                document_call=None,
                reply_call=None,
                connector_calls=(),
                identity_pack=identity_pack,
                identity_pack_sha256=identity_pack_sha256,
                proof_scope="none",
                document_binding=None,
                output_template=None,
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
        outcome_wrapper = _mapping(content["outcomeViews"], "outcomeViews")[view_key]
        reply_wrapper = _mapping(content["replyCandidates"], "replyCandidates")[
            outcome_key
        ]
        presence_wrapper = _mapping(content["presenceReplies"], "presenceReplies")[
            outcome_key
        ]
        outcome_view = OutcomeView.model_validate(
            _mapping(outcome_wrapper, f"outcome view {view_key}")["payload"]
        )
        reply_candidate = ReplyCandidate.model_validate(
            _mapping(reply_wrapper, f"reply candidate {outcome_key}")["payload"]
        )
        presence_reply = PresenceReply.model_validate(
            _mapping(presence_wrapper, f"presence reply {outcome_key}")["payload"]
        )
        for wrapper, label in (
            (outcome_wrapper, f"outcome view {view_key}"),
            (reply_wrapper, f"reply candidate {outcome_key}"),
            (presence_wrapper, f"presence reply {outcome_key}"),
        ):
            _canonical_wrapper_sha(wrapper, label)
        output_template = self._output_template(
            outcome_key=outcome_key,
            outcome_view=outcome_view,
            reply_candidate=reply_candidate,
            presence_reply=presence_reply,
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
            identity_pack=identity_pack,
            identity_pack_sha256=identity_pack_sha256,
            proof_scope=(
                "identity_surface_only"
                if name == "happy_mira"
                else "eva_installation_exact"
            ),
            document_binding=(
                None if name == "happy_mira" else self._document_binding("Eva")
            ),
            output_template=output_template,
            expected_outcome_view=outcome_view,
            expected_reply_candidate=reply_candidate,
            expected_presence_reply=presence_reply,
            expected_status=expected_status,
        )

    def _identity_pack(
        self, identity: Literal["Eva", "Mira"]
    ) -> tuple[IdentityPack, str]:
        packs = _array(
            _mapping(self._profile["sealedPackFixtures"], "sealed packs")[
                "identityPacks"
            ],
            "identity packs",
        )
        matches = [
            _mapping(item, "identity pack entry")
            for item in packs
            if _mapping(item, "identity pack entry").get("fixtureId")
            == f"fixture:identity-pack:{identity.lower()}:001"
        ]
        if len(matches) != 1:
            raise Noor4DArtifactError("active fixture IdentityPack is not unique")
        entry = matches[0]
        expected_sha256 = entry.get("canonicalSha256")
        try:
            pack = IdentityPack.model_validate(entry["payload"])
        except (KeyError, ValueError) as exc:
            raise Noor4DArtifactError("active fixture IdentityPack is invalid") from exc
        if (
            not isinstance(expected_sha256, str)
            or pack.state != "current"
            or pack.display_name != identity
            or canonical_sha256(pack) != expected_sha256
        ):
            raise Noor4DArtifactError(
                "active fixture IdentityPack hash or selection is invalid"
            )
        return pack, expected_sha256

    def _document_binding(self, identity: Literal["Eva", "Mira"]) -> ObjectBinding:
        vectors = _array(
            _mapping(self._profile["encryptedObjectFormat"], "encrypted object format")[
                "knownAnswerVectors"
            ]["vectors"],
            "object known-answer vectors",
        )
        matches = [
            _mapping(item, "object known-answer vector")
            for item in vectors
            if _mapping(item, "object known-answer vector").get("identity") == identity
        ]
        if len(matches) != 1:
            raise Noor4DArtifactError("fixture object binding is not unique")
        vector = matches[0]
        expected_ref = _mapping(vector.get("expectedObjectRef"), "expected ObjectRef")
        try:
            return ObjectBinding(
                tenant_id=cast(str, vector["tenantId"]),
                installation_id=cast(str, vector["installationId"]),
                object_id=cast(str, vector["objectId"]),
                key_ref=cast(str, vector["keyRef"]),
                mime_type=cast(str, expected_ref["mime_type"]),
            )
        except (KeyError, TypeError, ValueError, ObjectBindingError) as exc:
            raise Noor4DArtifactError("fixture object binding is invalid") from exc

    def _output_template(
        self,
        *,
        outcome_key: str,
        outcome_view: OutcomeView,
        reply_candidate: ReplyCandidate,
        presence_reply: PresenceReply,
    ) -> _OutputTemplate:
        kinds: tuple[
            Literal["mail", "erp", "calendar", "document", "confirmation"], ...
        ] = ("mail", "erp", "calendar", "document", "confirmation")
        if len(outcome_view.steps) != len(kinds):
            raise Noor4DArtifactError("fixture OutcomeView step template is invalid")
        steps = tuple(
            _OutcomeStepTemplate(
                kind=kind,
                step_id=step.step_id,
                human_summary=step.human_summary,
                business_evidence_refs=(
                    tuple(step.evidence_refs) if kind in {"mail", "erp"} else ()
                ),
            )
            for kind, step in zip(kinds, outcome_view.steps, strict=True)
        )
        claims: list[_ClaimTemplate] = []
        for binding in presence_reply.claim_bindings:
            matches = [
                step.step_id
                for step in outcome_view.steps
                if binding.evidence_refs == step.evidence_refs
            ]
            if len(matches) != 1:
                raise Noor4DArtifactError(
                    "PresenceReply claim template does not select one outcome step"
                )
            claims.append(
                _ClaimTemplate(claim_ref=binding.claim_ref, step_id=matches[0])
            )
        host_validations = _mapping(
            _mapping(
                _mapping(self._connector["hostEvidence"], "hostEvidence")[
                    "outcomeDeliveryContent"
                ],
                "outcome delivery content",
            )["hostValidations"],
            "host validations",
        )
        validation_wrapper = _mapping(
            host_validations[outcome_key], f"host validation {outcome_key}"
        )
        expected_sha256 = _canonical_wrapper_sha(
            validation_wrapper, f"host validation {outcome_key}"
        )
        validation = _mapping(
            validation_wrapper.get("payload"), f"host validation {outcome_key} payload"
        )
        vector_id = validation.get("vector_id")
        if not isinstance(vector_id, str):
            raise Noor4DArtifactError("host validation vector ID is invalid")
        return _OutputTemplate(
            steps=steps,
            message_id=presence_reply.message_id,
            claim_bindings=tuple(claims),
            host_validation_vector_id=vector_id,
            expected_host_validation_sha256=expected_sha256,
            fallback_reply_template=reply_candidate,
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
        accumulator.effect_receipts_by_case[call.case_id] = receipt
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
        accumulator.effect_readbacks_by_case[call.case_id] = readback
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
    accumulator.business_chain_validated = True


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


def _planner_request(call: Noor4DModelCall) -> PlannerRequest:
    try:
        payload = require_canonical_bytes(call.planner_request_bytes)
        request = PlannerRequest.model_validate(payload)
    except ValueError as exc:
        raise Noor4DFlowError(
            "planner_request_invalid", "model call does not contain a valid request"
        ) from exc
    if request.model_input.purpose != call.purpose or canonical_sha256(
        request
    ) != _sha256(call.planner_request_bytes):
        raise Noor4DFlowError(
            "planner_request_mismatch", "model call request differs from its purpose"
        )
    return request


def _validate_model_request(
    *,
    spec: Noor4DRunSpec,
    call: Noor4DModelCall,
    accumulator: _Accumulator,
    outcome_view: OutcomeView | None = None,
) -> PlannerRequest:
    request = _planner_request(call)
    context = request.model_input.presence_context
    identity_surface_prefix = (
        spec.proof_scope == "identity_surface_only" and call.purpose != "reply"
    )
    selected_pack_matches = (
        context.identity_pack.model_dump(mode="json")
        == spec.identity_pack.model_dump(mode="json")
        and context.identity_pack_sha256 == spec.identity_pack_sha256
    )
    relationship_scope_matches = bool(context.relationships) and all(
        relationship.installation_id == spec.ingress.installation_id
        for relationship in context.relationships
    )
    if (
        canonical_sha256(context.identity_pack) != context.identity_pack_sha256
        or context.tenant_id != spec.ingress.tenant_id
        or (
            not identity_surface_prefix
            and (not selected_pack_matches or not relationship_scope_matches)
        )
    ):
        raise Noor4DFlowError(
            "identity_context_mismatch",
            "PlannerRequest does not bind the selected current IdentityPack and scope",
        )
    if (
        context.effect_receipts != accumulator.effect_receipts
        or context.effect_readbacks != accumulator.effect_readbacks
        or context.delivery_receipts
        != (
            []
            if accumulator.confirmation_delivery_receipt is None
            else [accumulator.confirmation_delivery_receipt]
        )
    ):
        raise Noor4DFlowError(
            "model_evidence_context_mismatch",
            "PlannerRequest evidence differs from the proof earned before dispatch",
        )
    if call.purpose == "reply":
        if not isinstance(request.model_input, ReplyModelInput) or outcome_view is None:
            raise Noor4DFlowError(
                "reply_context_mismatch", "reply call lacks a built OutcomeView"
            )
        if request.model_input.outcome_view != outcome_view:
            raise Noor4DFlowError(
                "reply_context_mismatch",
                "reply PlannerRequest embeds another OutcomeView",
            )
        template = spec.output_template
        if template is None or request.model_input.allowed_claim_refs != [
            item.claim_ref for item in template.claim_bindings
        ]:
            raise Noor4DFlowError(
                "reply_context_mismatch",
                "reply PlannerRequest claim authority differs from the output template",
            )
    return request


def _validate_connector_scope(spec: Noor4DRunSpec, call: Noor4DConnectorCall) -> None:
    if spec.proof_scope != "eva_installation_exact":
        return
    envelope = call.invoke.params
    if (
        envelope.installation_id != spec.ingress.installation_id
        or envelope.tenant_id != spec.ingress.tenant_id
    ):
        raise Noor4DFlowError(
            "connector_scope_mismatch",
            "connector call differs from the admitted Eva installation scope",
        )


def _validate_document_candidate(
    *,
    spec: Noor4DRunSpec,
    draft: DocumentDraft,
    readback: AuthenticatedCandidateReadback | None,
) -> None:
    if spec.proof_scope == "identity_surface_only":
        if spec.document_binding is not None or readback is not None:
            raise Noor4DFlowError(
                "identity_surface_scope_exceeded",
                "Mira identity-surface fixture cannot adopt an object candidate",
            )
        return
    binding = spec.document_binding
    plaintext = draft.content.encode("utf-8")
    if (
        spec.proof_scope != "eva_installation_exact"
        or binding is None
        or not isinstance(readback, AuthenticatedCandidateReadback)
        or readback.authenticated is not True
        or readback.binding != binding
        or readback.binding.mime_type != draft.mime_type
        or readback.plaintext_sha256 != _sha256(plaintext)
        or readback.plaintext_byte_length != len(plaintext)
    ):
        raise Noor4DFlowError(
            "document_candidate_mismatch",
            "authenticated document candidate readback is absent or unbound",
        )


def _request_created_at(spec: Noor4DRunSpec) -> str:
    if not spec.plan_calls:
        raise Noor4DFlowError("plan_absent", "flow has no plan request timestamp")
    return _planner_request(
        spec.plan_calls[-1]
    ).model_input.presence_context.current_utterance.received_at


def _build_outcome_view(spec: Noor4DRunSpec, accumulator: _Accumulator) -> OutcomeView:
    template = spec.output_template
    plan = accumulator.plan
    if template is None or plan is None or not accumulator.business_chain_validated:
        raise Noor4DFlowError(
            "outcome_construction_absent",
            "earned business results cannot construct an OutcomeView",
        )
    calendar = accumulator.effect_readbacks_by_case.get("calendar.readback.matches")
    document = accumulator.effect_readbacks_by_case.get("document.readback.matches")
    document_reject = accumulator.effect_receipts_by_case.get(
        "document.create.definitive-reject"
    )
    confirmation_reject = accumulator.effect_receipts_by_case.get(
        "confirmation.deliver.definitive-reject"
    )
    confirmation = accumulator.confirmation_delivery_receipt
    built_steps: list[StepOutcome] = []
    for item in template.steps:
        status: Literal["completed", "blocked", "failed"]
        evidence_refs: list[str] = []
        unresolved_refs: list[str] = []
        if item.kind in {"mail", "erp"}:
            if not item.business_evidence_refs:
                raise Noor4DFlowError(
                    "outcome_template_mismatch", "business evidence alias is absent"
                )
            status = "completed"
            evidence_refs = list(item.business_evidence_refs)
        elif item.kind == "calendar" and calendar is not None:
            status = "completed"
            evidence_refs = [calendar.readback_id]
        elif item.kind == "document" and document is not None:
            status = "completed"
            evidence_refs = [document.readback_id]
        elif item.kind == "document" and document_reject is not None:
            status = "failed"
            unresolved_refs = [document_reject.evidence_ref]
        elif item.kind == "confirmation" and confirmation is not None:
            status = "completed"
            evidence_refs = [confirmation.delivery_receipt_id]
        elif item.kind == "confirmation" and confirmation_reject is not None:
            status = "failed"
            unresolved_refs = [confirmation_reject.evidence_ref]
        elif item.kind == "confirmation" and document_reject is not None:
            status = "blocked"
            unresolved_refs = [document_reject.evidence_ref]
        else:
            raise Noor4DFlowError(
                "outcome_proof_absent",
                f"earned proof for {item.kind} outcome step is absent",
            )
        built_steps.append(
            StepOutcome(
                step_id=item.step_id,
                status=status,
                human_summary=item.human_summary,
                evidence_refs=evidence_refs,
                unresolved_refs=unresolved_refs,
            )
        )
    overall_status: Literal["completed", "partially_completed"] = (
        "completed"
        if all(step.status == "completed" for step in built_steps)
        else "partially_completed"
    )
    return OutcomeView.model_validate(
        {
            "schema_version": 1,
            "contract": "OutcomeView/v1",
            "episode_id": plan.episode_id,
            "generation": plan.generation,
            "steps": [step.model_dump(mode="json") for step in built_steps],
            "overall_status": overall_status,
            "created_at": _request_created_at(spec),
        }
    )


def _build_presence(
    *,
    spec: Noor4DRunSpec,
    accumulator: _Accumulator,
    outcome_view: OutcomeView,
    candidate: ReplyCandidate,
) -> PresenceReply:
    template = spec.output_template
    if template is None:
        raise Noor4DFlowError(
            "presence_template_absent", "fixture output template is absent"
        )
    step_by_id = {step.step_id: step for step in outcome_view.steps}
    claim_bindings: list[ClaimBinding] = []
    for item in template.claim_bindings:
        step = step_by_id.get(item.step_id)
        if step is None or not step.evidence_refs:
            raise Noor4DFlowError(
                "unearned_presence_claim", "reply claim has no earned outcome proof"
            )
        claim_bindings.append(
            ClaimBinding(claim_ref=item.claim_ref, evidence_refs=step.evidence_refs)
        )
    if (
        candidate.claim_refs != [item.claim_ref for item in template.claim_bindings]
        or candidate.episode_id != outcome_view.episode_id
        or candidate.generation != outcome_view.generation
        or candidate.identity_id != spec.identity_pack.identity_id
        or candidate.identity_pack_version != spec.identity_pack.pack_version
        or candidate.style_check != "passed"
    ):
        raise Noor4DFlowError(
            "reply_candidate_mismatch",
            "reply candidate does not match earned claims and active identity",
        )
    lowered_text = candidate.text.casefold()
    forbidden = [
        value
        for value in spec.identity_pack.voice_style.forbidden_wording
        if value.casefold() in lowered_text
    ]
    if forbidden:
        raise Noor4DFlowError(
            "host_validation_rejected", "reply contains forbidden internal jargon"
        )
    host_validation = Noor4DHostValidation(
        vector_id=template.host_validation_vector_id,
        identity_pack_sha256=spec.identity_pack_sha256,
        outcome_view_sha256=canonical_sha256(outcome_view),
        text_sha256=_sha256(candidate.text.encode("utf-8")),
        unproven_claims=0,
        forbidden_internal_jargon=0,
        result="accepted",
    )
    if host_validation.canonical_sha256 != template.expected_host_validation_sha256:
        raise Noor4DFlowError(
            "host_validation_mismatch",
            "host-built validation differs from the sealed fixture oracle",
        )
    presence = PresenceReply.model_validate(
        {
            "schema_version": 1,
            "contract": "PresenceReply/v1",
            "message_id": template.message_id,
            "episode_id": outcome_view.episode_id,
            "generation": outcome_view.generation,
            "outcome_view_sha256": canonical_sha256(outcome_view),
            "reply_candidate_sha256": canonical_sha256(candidate),
            "fallback_reason": None,
            "host_validation_sha256": host_validation.canonical_sha256,
            "text": candidate.text,
            "claim_bindings": [
                binding.model_dump(mode="json") for binding in claim_bindings
            ],
            "identity_id": spec.identity_pack.identity_id,
            "identity_pack_version": spec.identity_pack.pack_version,
            "language": spec.identity_pack.language,
            "created_at": _request_created_at(spec),
        }
    )
    accumulator.host_validation = host_validation
    return presence


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
        document_candidate_readback=accumulator.document_candidate_readback,
        reply_candidate=accumulator.reply_candidate,
        reply_source=accumulator.reply_source,
        outcome_view=accumulator.outcome_view,
        presence_reply=accumulator.presence_reply,
        host_validation=accumulator.host_validation,
        identity_pack_sha256=spec.identity_pack_sha256,
        proof_scope=spec.proof_scope,
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
        _validate_model_request(
            spec=spec,
            call=plan_call,
            accumulator=accumulator,
        )
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
        _validate_connector_scope(spec, call)
        if call.case_id.startswith("document.create"):
            if spec.document_call is None:
                raise Noor4DFlowError(
                    "document_model_call_absent", "document effect has no model draft"
                )
            if accumulator.document_draft is None:
                _validate_model_request(
                    spec=spec,
                    call=spec.document_call,
                    accumulator=accumulator,
                )
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
                candidate_readback = document_candidate_hook.prepare(
                    Noor4DDocumentCandidateRequest(
                        draft=parsed,
                        binding=spec.document_binding,
                        plaintext=parsed.content.encode("utf-8"),
                    )
                )
                _validate_document_candidate(
                    spec=spec,
                    draft=parsed,
                    readback=candidate_readback,
                )
                accumulator.document_candidate_readback = candidate_readback
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
    if spec.proof_scope == "eva_installation_exact":
        outcome_view = _build_outcome_view(spec, accumulator)
        if outcome_view != spec.expected_outcome_view:
            raise Noor4DFlowError(
                "outcome_assertion_mismatch",
                "built OutcomeView differs from the sealed assertion oracle",
            )
    elif spec.proof_scope == "identity_surface_only":
        # The sealed Mira connector rows reuse Eva installation fixtures.  Keep
        # this as the pre-existing identity surface vector, not scoped proof.
        outcome_view = spec.expected_outcome_view
    else:
        raise Noor4DFlowError(
            "outcome_scope_absent", "terminal flow has no authorized proof scope"
        )
    accumulator.outcome_view = outcome_view
    candidate: ReplyCandidate
    if spec.reply_call is not None:
        _validate_model_request(
            spec=spec,
            call=spec.reply_call,
            accumulator=accumulator,
            outcome_view=outcome_view,
        )
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
        accumulator.reply_source = (
            "identity_surface_vector"
            if spec.proof_scope == "identity_surface_only"
            else "model_receipt"
        )
    else:
        if spec.output_template is None:
            raise Noor4DFlowError(
                "reply_template_absent", "partial flow has no host fallback template"
            )
        candidate = spec.output_template.fallback_reply_template
        if candidate != spec.expected_reply_candidate:
            raise Noor4DFlowError(
                "reply_assertion_mismatch",
                "host fallback template differs from the sealed assertion oracle",
            )
        accumulator.reply_source = "host_fallback_template"
    accumulator.reply_candidate = candidate
    expected_presence = spec.expected_presence_reply
    if expected_presence is None:
        raise Noor4DFlowError(
            "presence_reply_absent", "terminal connector call lacks PresenceReply"
        )
    if spec.proof_scope == "eva_installation_exact":
        presence = _build_presence(
            spec=spec,
            accumulator=accumulator,
            outcome_view=outcome_view,
            candidate=candidate,
        )
        if presence != expected_presence:
            raise Noor4DFlowError(
                "presence_assertion_mismatch",
                "built PresenceReply differs from the sealed assertion oracle",
            )
    else:
        presence = expected_presence
    _validate_presence(
        spec=spec,
        candidate=candidate,
        outcome_view=outcome_view,
        presence=presence,
        accumulator=accumulator,
    )
    _validate_outcome_payload(outcome_call, presence, spec.ingress)
    _validate_connector_scope(spec, outcome_call)
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
    status: Noor4DFlowStatus = (
        "completed"
        if outcome_view.overall_status == "completed"
        else "partial_delivered"
    )
    if status != spec.expected_status:
        raise Noor4DFlowError(
            "status_assertion_mismatch",
            "ACK-closed terminal status differs from the sealed assertion oracle",
        )
    commitment: Noor4DCommitmentState = "fulfilled" if status == "completed" else "open"
    return Noor4DFlowResult(
        status=status,
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
    "Noor4DDocumentCandidateRequest",
    "Noor4DDocumentCandidateHook",
    "Noor4DExternalCall",
    "Noor4DFixtureBundle",
    "Noor4DFlowError",
    "Noor4DFlowResult",
    "Noor4DFlowState",
    "Noor4DHostValidation",
    "Noor4DModelCall",
    "Noor4DModelInvocationResult",
    "Noor4DModelPort",
    "Noor4DRunSpec",
    "run_noor_4d",
]
