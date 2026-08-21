"""Closed registry for Eva v1 durable and wire contracts."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from .base import ContractModel
from .connector import ConnectorEnvelope, ConnectorManifest, ConnectorResult
from .evidence import (
    ClaimEvidenceBinding,
    EvaAcceptanceEvidenceBundle,
    EvidenceEnvelope,
    ObjectRef,
    ObjectStore,
)
from .execution import (
    CapabilityGrant,
    CapabilityManifest,
    ConnectorBinding,
    ConsentGrant,
    CredentialGrant,
    DeliveryIntent,
    DeliveryReceipt,
    EffectAttempt,
    EffectIntent,
    EffectReadback,
    EffectReceipt,
    ReadAttempt,
    ReadIntent,
    ReadObservation,
)
from .identity import IdentityPack, PolicyPack
from .ingress import (
    DeliveryOffer,
    IngressClaim,
    IngressOutcome,
    IngressRequest,
    TurnEnvelope,
)
from .lifecycle import AttentionWake, Commitment, EpisodeState
from .memory import (
    ExperienceSourceEvent,
    ImprovementCandidate,
    MemoryFact,
    MemoryRecall,
    RelationshipBinding,
)
from .planning import (
    ContextSelection,
    DocumentDraft,
    ModelAttempt,
    ModelDecision,
    OutcomeView,
    PlannerRequest,
    PresenceContext,
    PresenceReply,
    ReplyCandidate,
    ValidatedPlan,
)
from .platform import (
    ConnectorCandidate,
    DocumentationSnapshot,
    EvaPortableArchive,
    InstallationRemap,
    InstallationManifest,
    PlatformLock,
    PromotionDecision,
    ReleaseManifest,
)

_CONTRACT_TYPES: dict[str, type[ContractModel]] = {
    "AttentionWake/v1": AttentionWake,
    "CapabilityGrant/v1": CapabilityGrant,
    "CapabilityManifest/v1": CapabilityManifest,
    "ClaimEvidenceBinding/v1": ClaimEvidenceBinding,
    "Commitment/v1": Commitment,
    "ConnectorBinding/v1": ConnectorBinding,
    "ConnectorCandidate/v1": ConnectorCandidate,
    "ConnectorEnvelope/v1": ConnectorEnvelope,
    "ConnectorManifest/v1": ConnectorManifest,
    "ConnectorResult/v1": ConnectorResult,
    "ConsentGrant/v1": ConsentGrant,
    "ContextSelection/v1": ContextSelection,
    "CredentialGrant/v1": CredentialGrant,
    "DeliveryIntent/v1": DeliveryIntent,
    "DeliveryOffer/v1": DeliveryOffer,
    "DeliveryReceipt/v1": DeliveryReceipt,
    "DocumentDraft/v1": DocumentDraft,
    "DocumentationSnapshot/v1": DocumentationSnapshot,
    "EffectAttempt/v1": EffectAttempt,
    "EffectIntent/v1": EffectIntent,
    "EffectReadback/v1": EffectReadback,
    "EffectReceipt/v1": EffectReceipt,
    "EpisodeState/v1": EpisodeState,
    "EvaAcceptanceEvidenceBundle/v1": EvaAcceptanceEvidenceBundle,
    "EvaPortableArchive/v1": EvaPortableArchive,
    "EvidenceEnvelope/v1": EvidenceEnvelope,
    "ExperienceSourceEvent/v1": ExperienceSourceEvent,
    "IdentityPack/v1": IdentityPack,
    "ImprovementCandidate/v1": ImprovementCandidate,
    "IngressClaim/v1": IngressClaim,
    "IngressOutcome/v1": IngressOutcome,
    "IngressRequest/v1": IngressRequest,
    "InstallationManifest/v1": InstallationManifest,
    "InstallationRemap/v1": InstallationRemap,
    "MemoryFact/v1": MemoryFact,
    "MemoryRecall/v1": MemoryRecall,
    "ModelAttempt/v1": ModelAttempt,
    "ModelDecision/v1": ModelDecision,
    "ObjectRef/v1": ObjectRef,
    "ObjectStore/v1": ObjectStore,
    "OutcomeView/v1": OutcomeView,
    "PlannerRequest/v1": PlannerRequest,
    "PlatformLock/v1": PlatformLock,
    "PolicyPack/v1": PolicyPack,
    "PresenceContext/v1": PresenceContext,
    "PresenceReply/v1": PresenceReply,
    "PromotionDecision/v1": PromotionDecision,
    "ReadAttempt/v1": ReadAttempt,
    "ReadIntent/v1": ReadIntent,
    "ReadObservation/v1": ReadObservation,
    "RelationshipBinding/v1": RelationshipBinding,
    "ReleaseManifest/v1": ReleaseManifest,
    "ReplyCandidate/v1": ReplyCandidate,
    "TurnEnvelope/v1": TurnEnvelope,
    "ValidatedPlan/v1": ValidatedPlan,
}

if list(_CONTRACT_TYPES) != sorted(_CONTRACT_TYPES):
    raise RuntimeError("CONTRACT_REGISTRY must stay lexicographically sorted")

CONTRACT_REGISTRY: Final[MappingProxyType[str, type[ContractModel]]] = MappingProxyType(
    _CONTRACT_TYPES
)
del _CONTRACT_TYPES


def schema_filename(contract_name: str) -> str:
    return contract_name.replace("/", "-") + ".schema.json"
