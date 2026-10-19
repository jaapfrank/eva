"""Typed provenance after ordinary private day text becomes durable knowledge."""
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import re
from typing import Literal

from eva.application.memory_audience import MemoryAudience, memory_audience_from_wire
from eva.contracts.canonical_json import canonical_sha256

CONTRACT = "EvaPrivateMemorySourceCompaction/v1"
_SHA = re.compile(r"[0-9a-f]{64}")


def _utc(value):
    if not isinstance(value, str):
        raise ValueError("compaction timestamp must be canonical UTC")
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ValueError("compaction timestamp must be canonical UTC")
    return parsed


def source_identity_sha256(data: dict) -> str:
    return canonical_sha256({key: value for key, value in data.items()
        if key not in {"content", "content_compaction"}})


@dataclass(frozen=True, slots=True)
class MemorySourceCompactionProof:
    contract: Literal["EvaPrivateMemorySourceCompaction/v1"]
    compacted_at: str
    queued_payload_sha256: str
    content_sha256: str
    source_payload_sha256: str
    ingress_payload_sha256: str
    ingress_text_sha256: str
    reply_payload_sha256: str
    reply_text_sha256: str
    cohort_manifest_sha256: str
    bank_id: str
    operation_id: str
    selected_fact_count: int
    disposition: Literal["retained", "not_selected"]
    receipt_sha256: str
    source_identity_sha256: str

    def __post_init__(self):
        if self.contract != CONTRACT:
            raise ValueError("compaction proof contract differs")
        _utc(self.compacted_at)
        for key, value in asdict(self).items():
            if key.endswith("sha256") and (not isinstance(value, str) or not _SHA.fullmatch(value)):
                raise ValueError("compaction proof hash is invalid")
        for value in (self.bank_id, self.operation_id):
            if not isinstance(value, str) or not value.strip() or len(value) > 512:
                raise ValueError("compaction proof identifier is invalid")
        if type(self.selected_fact_count) is not int or self.selected_fact_count < 0:
            raise ValueError("compaction proof requires a known selection count")
        if self.disposition != ("not_selected" if self.selected_fact_count == 0 else "retained"):
            raise ValueError("compaction proof disposition differs from selection")

    def to_wire(self):
        return asdict(self)


def compaction_proof_from_data(data) -> MemorySourceCompactionProof:
    if not isinstance(data, dict):
        raise ValueError("compaction proof is malformed")
    try:
        return MemorySourceCompactionProof(**data)
    except TypeError as error:
        raise ValueError("compaction proof fields differ") from error


@dataclass(frozen=True, slots=True)
class CompactedPrivateMemorySource:
    tenant_id: str
    installation_id: str
    principal_id: str
    conversation_id: str
    document_id: str
    occurred_at: str
    source_ref: str
    evidence_ref: str
    privacy_class: Literal["public", "internal", "confidential", "restricted"]
    audience: MemoryAudience
    content_compaction: MemorySourceCompactionProof
    retention_until: str | None = None
    trust: Literal["direct_user"] = "direct_user"
    project_id: None = None
    retention_mode: Literal["dream"] = "dream"

    def __post_init__(self):
        for value in (self.tenant_id, self.installation_id, self.principal_id,
                self.conversation_id, self.document_id, self.source_ref, self.evidence_ref):
            if not isinstance(value, str) or not value or len(value) > 1024:
                raise ValueError("compacted source identity is invalid")
        if (not isinstance(self.audience, MemoryAudience) or self.trust != "direct_user" or self.project_id is not None
                or self.retention_mode != "dream" or self.audience.kind != "private_person"
                or self.audience.principal_id != self.principal_id
                or self.document_id != f"{self.conversation_id}:exchange:{self.source_ref}"
                or self.conversation_id.startswith(("initiative-task:", "telegram:-", "household:"))
                or self.privacy_class not in {"public", "internal", "confidential", "restricted"}
                or not isinstance(self.content_compaction, MemorySourceCompactionProof)):
            raise ValueError("compacted source is not an ordinary private dream exchange")
        occurred = _utc(self.occurred_at)
        if self.retention_until is not None and _utc(self.retention_until) < occurred:
            raise ValueError("compacted source retention precedes its source")
        if occurred.date() >= _utc(self.content_compaction.compacted_at).date():
            raise ValueError("compacted source is not from a previous UTC day")


def compacted_private_memory_source_from_data(data) -> CompactedPrivateMemorySource:
    required = {"tenant_id", "installation_id", "principal_id", "conversation_id", "document_id",
        "occurred_at", "source_ref", "evidence_ref", "privacy_class", "audience", "trust",
        "kind", "retention_mode", "content_compaction"}
    if (not isinstance(data, dict) or not required <= set(data)
            or set(data) - required - {"retention_until", "project_id"}
            or data.get("kind") != "hindsight_memory_document"):
        raise ValueError("compacted source fields differ")
    proof = compaction_proof_from_data(data["content_compaction"])
    if proof.source_identity_sha256 != source_identity_sha256(data):
        raise ValueError("compacted source identity hash differs")
    source = CompactedPrivateMemorySource(**{key: data[key] for key in required
        - {"kind", "content_compaction", "audience"}},
        audience=memory_audience_from_wire(data["audience"]), content_compaction=proof,
        retention_until=data.get("retention_until"), project_id=data.get("project_id"))
    receipt = {"bank_id": proof.bank_id, "document_id": source.document_id,
        "content_sha256": proof.content_sha256, "operation_id": proof.operation_id,
        "selected_fact_count": proof.selected_fact_count, "disposition": proof.disposition}
    if canonical_sha256(receipt) != proof.receipt_sha256:
        raise ValueError("compacted source receipt hash differs")
    return source
