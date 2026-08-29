"""Experience, current-memory, and Presence-context application boundary.

The application layer decides what a memory write means.  PostgreSQL owns the
transactional representation; model output and search ranking never acquire
write authority through this module.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
import re
from typing import Literal, Protocol

from eva.contracts.base import JsonValue, validate_json_value
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.memory import (
    ExperienceSourceEvent,
    MemoryFact,
    MemoryRecall,
    RecalledFact,
)
from eva.contracts.planning import ConfirmedFact
from eva.application.memory_audience import MemoryAudience


class MemoryAuthorityError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class InvalidMemoryAssertion(MemoryAuthorityError):
    def __init__(self, message: str) -> None:
        super().__init__("invalid_memory_assertion", message)


class CurrentMemoryConflict(MemoryAuthorityError):
    def __init__(self, message: str = "current memory changed concurrently") -> None:
        super().__init__("current_memory_conflict", message)


@dataclass(frozen=True, slots=True)
class ExpectedCurrentFact:
    fact_id: str
    revision: int

    def __post_init__(self) -> None:
        if not self.fact_id:
            raise ValueError("expected current fact ID is required")
        if self.revision < 1:
            raise ValueError("expected current revision must be positive")


@dataclass(frozen=True, slots=True)
class RememberFact:
    installation_id: str
    current_fact_id: str
    source_event: ExperienceSourceEvent
    fact: MemoryFact
    scope: dict[str, JsonValue]
    retention_policy_id: str
    committed_at: str
    expected_current: ExpectedCurrentFact | None = None


@dataclass(frozen=True, slots=True)
class MemoryWriteReceipt:
    disposition: str
    source_event_id: str
    fact_id: str
    current_fact_id: str
    current_revision: int


@dataclass(frozen=True, slots=True)
class RecallCurrentFacts:
    recall_id: str
    installation_id: str
    tenant_id: str
    principal_id: str
    audience: MemoryAudience
    conversation_id: str | None
    project_id: str | None
    query_purpose: str
    requested_valid_at: str
    scope: dict[str, JsonValue]
    subject_ids: tuple[str, ...]
    query_text: str
    projection_version: str = "postgres-simple-v1"
    candidate_fact_ids: tuple[str, ...] | None = None
    maximum_facts: int = 32
    allowed_privacy_classes: tuple[str, ...] = (
        "public",
        "internal",
        "confidential",
    )

    def __post_init__(self) -> None:
        if self.audience.principal_id != self.principal_id:
            raise ValueError("memory recall audience principal differs")


@dataclass(frozen=True, slots=True)
class RecalledMemoryFact:
    fact: MemoryFact
    source_events: tuple[ExperienceSourceEvent, ...]
    ranking_reason: str


@dataclass(frozen=True, slots=True)
class PresenceMemory:
    recall: MemoryRecall
    confirmed_facts: tuple[ConfirmedFact, ...]
    style_mirror: str | None = None

    def __post_init__(self) -> None:
        if self.style_mirror is not None and (
            not isinstance(self.style_mirror, str)
            or not self.style_mirror.strip()
            or len(self.style_mirror) > 4096
        ):
            raise ValueError("memory style mirror is invalid")


@dataclass(frozen=True, slots=True)
class ConversationMemoryDocument:
    """Stable conversation source retained by the selected memory engine."""

    tenant_id: str
    installation_id: str
    principal_id: str
    conversation_id: str
    document_id: str
    content: str
    occurred_at: str
    source_ref: str
    evidence_ref: str
    privacy_class: Literal["public", "internal", "confidential", "restricted"]
    trust: Literal[
        "direct_user",
        "verified_external",
        "host_policy",
        "model_inference_unconfirmed",
    ]
    audience: MemoryAudience
    project_id: str | None = None
    retention_until: str | None = None

    def __post_init__(self) -> None:
        required = (
            self.tenant_id,
            self.installation_id,
            self.principal_id,
            self.conversation_id,
            self.document_id,
            self.source_ref,
            self.evidence_ref,
        )
        if not all(required):
            raise ValueError("memory document identifiers are required")
        if self.audience.principal_id != self.principal_id:
            raise ValueError("memory document audience author differs")
        if not self.content.strip():
            raise ValueError("memory document content is required")
        if self.project_id is not None and (
            not self.project_id.strip() or len(self.project_id) > 200
        ):
            raise ValueError("memory document project ID is invalid")
        if len(self.content.encode("utf-8")) > 1_048_576:
            raise ValueError("memory document exceeds one MiB")
        if self.privacy_class not in {
            "public",
            "internal",
            "confidential",
            "restricted",
        }:
            raise ValueError("memory document privacy class is invalid")
        if self.trust not in {
            "direct_user",
            "verified_external",
            "host_policy",
            "model_inference_unconfirmed",
        }:
            raise ValueError("memory document trust is invalid")
        try:
            occurred_at = datetime.strptime(self.occurred_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as error:
            raise ValueError(
                "memory document timestamp must be canonical UTC"
            ) from error
        if self.retention_until is not None:
            try:
                retention_until = datetime.strptime(
                    self.retention_until, "%Y-%m-%dT%H:%M:%SZ"
                )
            except ValueError as error:
                raise ValueError(
                    "memory document retention deadline must be canonical UTC"
                ) from error
            if retention_until < occurred_at:
                raise ValueError(
                    "memory document retention deadline precedes its source"
                )


@dataclass(frozen=True, slots=True)
class MemoryRetentionReceipt:
    bank_id: str
    document_id: str
    content_sha256: str
    disposition: Literal["retained"] = "retained"

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip() or len(value) > 512
            for value in (self.bank_id, self.document_id)
        ):
            raise ValueError("memory retention receipt identifiers are invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_sha256):
            raise ValueError("memory retention receipt content hash is invalid")
        if self.disposition != "retained":
            raise ValueError("memory retention receipt disposition is invalid")


class SemanticMemoryProviderError(RuntimeError):
    """Provider-neutral failure at Eva's semantic-memory boundary."""


class SemanticMemoryProviderPort(Protocol):
    """Small replaceable boundary for recall, retain, and cold maintenance."""

    def health(self) -> None: ...

    def recall_for_presence(self, query: RecallCurrentFacts) -> PresenceMemory: ...

    def retain_conversation(
        self, document: ConversationMemoryDocument
    ) -> MemoryRetentionReceipt: ...

    def remove_conversation_document(
        self, *, bank_id: str, document_id: str
    ) -> None: ...

    def maintain_person_scope(
        self, tenant_id: str, installation_id: str, principal_id: str
    ) -> object: ...


class MemoryStorageAdmissionPort(Protocol):
    """Minimum result needed from a durable memory-storage admission."""

    admitted: bool
    retention_until: str | None
    reason: str


class ConversationMemoryStorageBudgetPort(Protocol):
    """Provider-neutral budget boundary used before a semantic-memory write."""

    def admit_memory(
        self, document: ConversationMemoryDocument, *, now: datetime
    ) -> MemoryStorageAdmissionPort: ...

    def mark_retained(
        self,
        document: ConversationMemoryDocument,
        receipt: MemoryRetentionReceipt,
        *,
        now: datetime,
    ) -> object: ...


class BudgetedConversationMemoryWriter:
    """The only direct semantic-memory writer exposed to runtime producers.

    The raw provider deliberately has ``retain_conversation`` while this writer
    exposes ``retain_bounded``.  Producers cannot accidentally satisfy this
    boundary by receiving Hindsight directly.
    """

    def __init__(
        self,
        memory: SemanticMemoryProviderPort,
        budget: ConversationMemoryStorageBudgetPort | None,
    ) -> None:
        self._memory = memory
        self._budget = budget

    @staticmethod
    def _require_now(now: datetime) -> datetime:
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise ValueError("memory writer time must be timezone-aware UTC")
        if now.microsecond:
            raise ValueError("memory writer time must use whole seconds")
        return now

    def retain_bounded(
        self, document: ConversationMemoryDocument, *, now: datetime
    ) -> MemoryRetentionReceipt:
        now = self._require_now(now)
        if self._budget is None:
            return self._memory.retain_conversation(document)
        admission = self._budget.admit_memory(document, now=now)
        if not admission.admitted:
            raise ValueError(f"memory storage blocked: {admission.reason}")
        if admission.retention_until is None:
            raise RuntimeError("memory storage admission lacks a retention deadline")
        admitted = replace(document, retention_until=admission.retention_until)
        receipt = self._memory.retain_conversation(admitted)
        self._budget.mark_retained(admitted, receipt, now=now)
        return receipt


class MemoryRepository(Protocol):
    def remember(self, command: RememberFact) -> MemoryWriteReceipt: ...

    def recall_current(
        self, query: RecallCurrentFacts
    ) -> tuple[RecalledMemoryFact, ...]: ...


_FACT_TRUST_FOR_SOURCE_TRUST = {
    "confirmed_direct_user": "confirmed_direct_user",
    "verified_external": "verified_external",
    "model_inference_unconfirmed": "model_inference_unconfirmed",
    "legacy_unverified": "legacy_unverified",
}

_PRESENCE_TRUST = {
    "confirmed_direct_user": "direct_user",
    "verified_external": "verified_external",
    "model_inference_unconfirmed": "host_policy",
    "legacy_unverified": "host_policy",
}


def _validate_remember(command: RememberFact) -> None:
    validate_json_value(command.scope)
    event = command.source_event
    fact = command.fact
    if event.tenant_id != fact.tenant_id:
        raise InvalidMemoryAssertion("source event and fact must share a tenant")
    if event.subject_id != fact.subject_id:
        raise InvalidMemoryAssertion("source event and fact must share a subject")
    if event.source_event_id not in fact.source_event_ids:
        raise InvalidMemoryAssertion("the asserted fact must cite its source event")
    expected_trust = _FACT_TRUST_FOR_SOURCE_TRUST.get(event.trust)
    if expected_trust is None:
        raise InvalidMemoryAssertion(
            "untrusted external content cannot become a canonical memory fact"
        )
    if fact.trust != expected_trust:
        raise InvalidMemoryAssertion("fact trust does not match source trust")
    if fact.assertion_status != "active":
        raise InvalidMemoryAssertion("a newly remembered fact must be active")
    if fact.valid_from < event.valid_from:
        raise InvalidMemoryAssertion("fact validity cannot precede its source")
    if fact.observed_at < event.observed_at:
        raise InvalidMemoryAssertion("fact observation cannot precede its source")
    if command.committed_at < event.ingested_at:
        raise InvalidMemoryAssertion("memory commit cannot precede source ingestion")
    if not command.installation_id or not command.current_fact_id:
        raise InvalidMemoryAssertion("installation and current-fact IDs are required")
    if not command.retention_policy_id:
        raise InvalidMemoryAssertion("retention policy is required")


def _validate_recall(query: RecallCurrentFacts) -> None:
    validate_json_value(query.scope)
    if not query.conversation_id and not query.project_id:
        raise InvalidMemoryAssertion("recall requires conversation or project scope")
    if not query.subject_ids:
        raise InvalidMemoryAssertion("recall requires an admitted subject set")
    if len(set(query.subject_ids)) != len(query.subject_ids):
        raise InvalidMemoryAssertion("recall subject IDs must be unique")
    if not 1 <= query.maximum_facts <= 64:
        raise InvalidMemoryAssertion("Presence recall is limited to 64 facts")
    if not query.allowed_privacy_classes:
        raise InvalidMemoryAssertion("recall requires an admitted privacy set")
    if query.candidate_fact_ids is not None:
        if len(query.candidate_fact_ids) > 256:
            raise InvalidMemoryAssertion("projection returned too many candidate facts")
        if len(set(query.candidate_fact_ids)) != len(query.candidate_fact_ids):
            raise InvalidMemoryAssertion("projection candidate fact IDs must be unique")


class MemoryAuthority:
    """Host-owned memory semantics over a caller-supplied durable repository."""

    def __init__(self, repository: MemoryRepository) -> None:
        self._repository = repository

    def remember(self, command: RememberFact) -> MemoryWriteReceipt:
        _validate_remember(command)
        return self._repository.remember(command)

    def recall_for_presence(self, query: RecallCurrentFacts) -> PresenceMemory:
        _validate_recall(query)
        recalled = self._repository.recall_current(query)
        if len(recalled) > query.maximum_facts:
            raise InvalidMemoryAssertion(
                "repository exceeded the admitted recall limit"
            )

        selected: list[RecalledFact] = []
        confirmed: list[ConfirmedFact] = []
        seen_fact_ids: set[str] = set()
        for rank, item in enumerate(recalled, start=1):
            fact = item.fact
            if fact.fact_id in seen_fact_ids:
                raise InvalidMemoryAssertion("repository returned a duplicate fact")
            seen_fact_ids.add(fact.fact_id)
            if (
                fact.tenant_id != query.tenant_id
                or fact.subject_id not in query.subject_ids
            ):
                raise InvalidMemoryAssertion(
                    "repository widened the admitted memory scope"
                )
            if fact.assertion_status != "active" or not item.source_events:
                raise InvalidMemoryAssertion(
                    "recall returned a non-current or sourceless fact"
                )
            source_by_id = {
                source.source_event_id: source for source in item.source_events
            }
            if set(fact.source_event_ids) != set(source_by_id):
                raise InvalidMemoryAssertion("recall source provenance is incomplete")
            source = source_by_id[fact.source_event_ids[0]]
            if source.tenant_id != query.tenant_id:
                raise InvalidMemoryAssertion("recall source belongs to another tenant")
            evidence_ref = source.provenance_refs[0]
            selected.append(
                RecalledFact(
                    rank=rank,
                    fact_id=fact.fact_id,
                    source_event_ids=fact.source_event_ids,
                    trust=fact.trust,
                    ranking_reason=item.ranking_reason,
                )
            )
            confirmed.append(
                ConfirmedFact(
                    fact_id=fact.fact_id,
                    subject=fact.subject_id,
                    predicate=fact.predicate,
                    value=fact.value,
                    source_ref=source.source_ref,
                    evidence_ref=evidence_ref,
                    observed_at=fact.observed_at,
                    trust=_PRESENCE_TRUST[fact.trust],
                )
            )

        admitted_fact_ids = sorted(seen_fact_ids)
        scope_sha256 = canonical_sha256(query.scope)
        recall = MemoryRecall(
            schema_version=1,
            contract="MemoryRecall/v1",
            recall_id=query.recall_id,
            query_purpose=query.query_purpose,
            tenant_id=query.tenant_id,
            principal_id=query.principal_id,
            conversation_id=query.conversation_id,
            project_id=query.project_id,
            requested_valid_at=query.requested_valid_at,
            admitted_fact_ids_sha256=canonical_sha256(admitted_fact_ids),
            selected=[item.model_dump(mode="json") for item in selected],
            excluded_reasons={},
            projection_version=query.projection_version,
            scope_sha256=scope_sha256,
            created_at=query.requested_valid_at,
        )
        return PresenceMemory(recall=recall, confirmed_facts=tuple(confirmed))


__all__ = [
    "ConversationMemoryDocument",
    "BudgetedConversationMemoryWriter",
    "ConversationMemoryStorageBudgetPort",
    "CurrentMemoryConflict",
    "ExpectedCurrentFact",
    "InvalidMemoryAssertion",
    "MemoryAuthority",
    "MemoryAuthorityError",
    "MemoryRepository",
    "MemoryRetentionReceipt",
    "MemoryStorageAdmissionPort",
    "MemoryWriteReceipt",
    "PresenceMemory",
    "RecallCurrentFacts",
    "RecalledMemoryFact",
    "RememberFact",
    "SemanticMemoryProviderError",
    "SemanticMemoryProviderPort",
]
