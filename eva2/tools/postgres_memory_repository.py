"""Operator-only PostgreSQL repository for the legacy memory trial."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as postgres_insert

from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.scope import set_local_scope
from eva.application.episode_repository import (
    TransactionRequired,
    TransactionScopeMismatch,
)
from eva.application.memory import (
    CurrentMemoryConflict,
    MemoryRepository,
    MemoryWriteReceipt,
    RecallCurrentFacts,
    RecalledMemoryFact,
    RememberFact,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.memory import ExperienceSourceEvent, MemoryFact


source_events = metadata.tables["eva_memory.source_events"]
fact_assertions = metadata.tables["eva_memory.fact_assertions"]
current_facts = metadata.tables["eva_memory.current_facts"]
evidence_envelopes = metadata.tables["eva_evidence.evidence_envelopes"]


def _uuid(value: str) -> UUID:
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError) as error:
        raise CurrentMemoryConflict("memory database IDs must be UUIDs") from error
    if str(parsed) != value:
        raise CurrentMemoryConflict("memory database IDs must be canonical UUIDs")
    return parsed


def _timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _hash(value: str) -> bytes:
    return bytes.fromhex(value)


def _payload_hash(value: ExperienceSourceEvent | MemoryFact) -> bytes:
    return _hash(canonical_sha256(value))


class PostgresMemoryRepository(MemoryRepository):
    """Memory writes and reads inside the caller's original root transaction."""

    def __init__(self, connection: Any, tenant_id: str, installation_id: str) -> None:
        transaction = connection.get_transaction()
        if (
            transaction is None
            or not connection.in_transaction()
            or connection.in_nested_transaction()
        ):
            raise TransactionRequired()
        self._connection = connection
        self._transaction = transaction
        self._tenant_id = str(_uuid(tenant_id))
        self._installation_id = str(_uuid(installation_id))

    def _require_transaction(self) -> None:
        if (
            not self._connection.in_transaction()
            or self._connection.in_nested_transaction()
            or self._connection.get_transaction() is not self._transaction
        ):
            raise TransactionRequired()
        try:
            set_local_scope(
                self._connection,
                tenant_id=self._tenant_id,
                installation_id=self._installation_id,
            )
        except TransactionScopeMismatch as error:
            raise CurrentMemoryConflict("memory transaction scope changed") from error

    def _validate_command_scope(self, command: RememberFact) -> None:
        if (
            command.source_event.tenant_id != self._tenant_id
            or command.fact.tenant_id != self._tenant_id
            or command.installation_id != self._installation_id
        ):
            raise CurrentMemoryConflict("memory command belongs to another scope")

    def _require_evidence(self, event: ExperienceSourceEvent) -> None:
        expected = {_uuid(value) for value in event.provenance_refs}
        found = set(
            self._connection.execute(
                sa.select(evidence_envelopes.c.evidence_id).where(
                    evidence_envelopes.c.tenant_id == _uuid(self._tenant_id),
                    evidence_envelopes.c.installation_id
                    == _uuid(self._installation_id),
                    evidence_envelopes.c.evidence_id.in_(expected),
                )
            ).scalars()
        )
        if found != expected:
            raise CurrentMemoryConflict("memory source provenance is incomplete")

    def _require_fact_sources(self, fact: MemoryFact) -> None:
        expected = {_uuid(value) for value in fact.source_event_ids}
        found = set(
            self._connection.execute(
                sa.select(source_events.c.source_event_id).where(
                    source_events.c.tenant_id == _uuid(self._tenant_id),
                    source_events.c.installation_id == _uuid(self._installation_id),
                    source_events.c.source_event_id.in_(expected),
                )
            ).scalars()
        )
        if found != expected:
            raise CurrentMemoryConflict("memory fact sources are incomplete")

    def _insert_source_event(self, command: RememberFact) -> bool:
        event = command.source_event
        values = {
            "tenant_id": _uuid(event.tenant_id),
            "installation_id": _uuid(command.installation_id),
            "source_event_id": _uuid(event.source_event_id),
            "principal_id": _uuid(event.principal_id) if event.principal_id else None,
            "subject_id": event.subject_id,
            "event_type": event.event_type,
            "source_kind": event.source_kind,
            "source_ref": event.source_ref,
            "content_sha256": _hash(event.content_sha256),
            "provenance_refs": [_uuid(value) for value in event.provenance_refs],
            "observed_at": _timestamp(event.observed_at),
            "valid_from": _timestamp(event.valid_from),
            "valid_until": _timestamp(event.valid_until) if event.valid_until else None,
            "ingested_at": _timestamp(event.ingested_at),
            "trust": event.trust,
            "privacy_class": event.privacy_class,
            "purpose": event.purpose,
            "retention_policy_id": command.retention_policy_id,
            "retention_until": _timestamp(event.retention_until),
            "delete_sla_at": _timestamp(event.delete_sla_at),
            "corrects_source_event_id": (
                _uuid(event.corrects_source_event_id)
                if event.corrects_source_event_id
                else None
            ),
            "supersedes_source_event_id": (
                _uuid(event.supersedes_source_event_id)
                if event.supersedes_source_event_id
                else None
            ),
            "retracts_source_event_id": (
                _uuid(event.retracts_source_event_id)
                if event.retracts_source_event_id
                else None
            ),
            "erasure_request_id": (
                _uuid(event.erasure_request_id) if event.erasure_request_id else None
            ),
            "payload": event.model_dump(mode="json"),
            "payload_sha256": _payload_hash(event),
        }
        inserted = self._connection.execute(
            postgres_insert(source_events)
            .values(**values)
            .on_conflict_do_nothing()
            .returning(source_events.c.source_event_id)
        ).scalar_one_or_none()
        if inserted is not None:
            return True
        existing = self._connection.execute(
            sa.select(source_events.c.payload_sha256).where(
                source_events.c.tenant_id == _uuid(self._tenant_id),
                source_events.c.installation_id == _uuid(self._installation_id),
                source_events.c.source_event_id == _uuid(event.source_event_id),
            )
        ).scalar_one_or_none()
        if existing is None or bytes(existing) != values["payload_sha256"]:
            raise CurrentMemoryConflict("source-event ID was reused with other bytes")
        return False

    def _insert_fact(self, command: RememberFact) -> bool:
        fact = command.fact
        values = {
            "tenant_id": _uuid(fact.tenant_id),
            "installation_id": _uuid(command.installation_id),
            "fact_id": _uuid(fact.fact_id),
            "subject_id": fact.subject_id,
            "predicate": fact.predicate,
            "scope": command.scope,
            "scope_sha256": _hash(canonical_sha256(command.scope)),
            "value": fact.value,
            "value_sha256": _hash(canonical_sha256(fact.value)),
            "source_event_ids": [_uuid(value) for value in fact.source_event_ids],
            "observed_at": _timestamp(fact.observed_at),
            "valid_from": _timestamp(fact.valid_from),
            "valid_until": _timestamp(fact.valid_until) if fact.valid_until else None,
            "trust": fact.trust,
            "content_sha256": _hash(fact.content_sha256),
            "assertion_status": fact.assertion_status,
            "superseded_by_fact_id": None,
            "retraction_source_event_id": None,
            "payload": fact.model_dump(mode="json"),
            "payload_sha256": _payload_hash(fact),
            "revision": 1,
            "retention_policy_id": command.retention_policy_id,
            "retention_until": _timestamp(command.source_event.retention_until),
            "created_at": _timestamp(command.committed_at),
            "updated_at": _timestamp(command.committed_at),
        }
        inserted = self._connection.execute(
            postgres_insert(fact_assertions)
            .values(**values)
            .on_conflict_do_nothing()
            .returning(fact_assertions.c.fact_id)
        ).scalar_one_or_none()
        if inserted is not None:
            return True
        existing = (
            self._connection.execute(
                sa.select(
                    fact_assertions.c.payload_sha256,
                    fact_assertions.c.scope_sha256,
                ).where(
                    fact_assertions.c.tenant_id == _uuid(self._tenant_id),
                    fact_assertions.c.installation_id == _uuid(self._installation_id),
                    fact_assertions.c.fact_id == _uuid(fact.fact_id),
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            existing is None
            or bytes(existing["payload_sha256"]) != values["payload_sha256"]
            or bytes(existing["scope_sha256"]) != values["scope_sha256"]
        ):
            raise CurrentMemoryConflict("fact ID was reused with other bytes")
        return False

    def _current_for_update(self, command: RememberFact) -> Any:
        return (
            self._connection.execute(
                sa.select(current_facts)
                .where(
                    current_facts.c.tenant_id == _uuid(self._tenant_id),
                    current_facts.c.installation_id == _uuid(self._installation_id),
                    current_facts.c.subject_id == command.fact.subject_id,
                    current_facts.c.predicate == command.fact.predicate,
                    current_facts.c.scope_sha256
                    == _hash(canonical_sha256(command.scope)),
                )
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )

    def _supersede_prior(self, command: RememberFact, current: Any) -> None:
        expected = command.expected_current
        if expected is None:
            raise CurrentMemoryConflict("existing current fact requires an expectation")
        if (
            current["fact_id"] != _uuid(expected.fact_id)
            or current["revision"] != expected.revision
            or current["current_fact_id"] != _uuid(command.current_fact_id)
        ):
            raise CurrentMemoryConflict()
        prior = (
            self._connection.execute(
                sa.select(fact_assertions)
                .where(
                    fact_assertions.c.tenant_id == _uuid(self._tenant_id),
                    fact_assertions.c.installation_id == _uuid(self._installation_id),
                    fact_assertions.c.fact_id == current["fact_id"],
                )
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if prior is None or prior["assertion_status"] != "active":
            raise CurrentMemoryConflict("prior current fact is not active")
        prior_payload = MemoryFact.model_validate(prior["payload"]).model_copy(
            update={
                "assertion_status": "superseded",
                "superseded_by_fact_id": command.fact.fact_id,
            }
        )
        result = self._connection.execute(
            sa.update(fact_assertions)
            .where(
                fact_assertions.c.tenant_id == _uuid(self._tenant_id),
                fact_assertions.c.installation_id == _uuid(self._installation_id),
                fact_assertions.c.fact_id == current["fact_id"],
                fact_assertions.c.revision == prior["revision"],
                fact_assertions.c.assertion_status == "active",
            )
            .values(
                assertion_status="superseded",
                superseded_by_fact_id=_uuid(command.fact.fact_id),
                payload=prior_payload.model_dump(mode="json"),
                payload_sha256=_payload_hash(prior_payload),
                revision=prior["revision"] + 1,
                updated_at=_timestamp(command.committed_at),
            )
        )
        if result.rowcount != 1:
            raise CurrentMemoryConflict()

    def remember(self, command: RememberFact) -> MemoryWriteReceipt:
        self._require_transaction()
        self._validate_command_scope(command)
        self._require_evidence(command.source_event)
        self._insert_source_event(command)
        self._require_fact_sources(command.fact)
        fact_inserted = self._insert_fact(command)
        current = self._current_for_update(command)

        if current is not None and current["fact_id"] == _uuid(command.fact.fact_id):
            if current["current_fact_id"] != _uuid(command.current_fact_id):
                raise CurrentMemoryConflict("current pointer ID changed")
            return MemoryWriteReceipt(
                disposition="already_present",
                source_event_id=command.source_event.source_event_id,
                fact_id=command.fact.fact_id,
                current_fact_id=command.current_fact_id,
                current_revision=current["revision"],
            )

        scope_sha256 = _hash(canonical_sha256(command.scope))
        if current is None:
            if command.expected_current is not None:
                raise CurrentMemoryConflict("expected current fact is missing")
            self._connection.execute(
                postgres_insert(current_facts).values(
                    tenant_id=_uuid(self._tenant_id),
                    installation_id=_uuid(self._installation_id),
                    current_fact_id=_uuid(command.current_fact_id),
                    subject_id=command.fact.subject_id,
                    predicate=command.fact.predicate,
                    scope=command.scope,
                    scope_sha256=scope_sha256,
                    fact_id=_uuid(command.fact.fact_id),
                    revision=1,
                    retention_policy_id=command.retention_policy_id,
                    retention_until=_timestamp(command.source_event.retention_until),
                    created_at=_timestamp(command.committed_at),
                    updated_at=_timestamp(command.committed_at),
                )
            )
            revision = 1
        else:
            self._supersede_prior(command, current)
            result = self._connection.execute(
                sa.update(current_facts)
                .where(
                    current_facts.c.tenant_id == _uuid(self._tenant_id),
                    current_facts.c.installation_id == _uuid(self._installation_id),
                    current_facts.c.current_fact_id == _uuid(command.current_fact_id),
                    current_facts.c.revision == current["revision"],
                )
                .values(
                    fact_id=_uuid(command.fact.fact_id),
                    revision=current["revision"] + 1,
                    retention_policy_id=command.retention_policy_id,
                    retention_until=_timestamp(command.source_event.retention_until),
                    updated_at=_timestamp(command.committed_at),
                )
            )
            if result.rowcount != 1:
                raise CurrentMemoryConflict()
            revision = current["revision"] + 1

        return MemoryWriteReceipt(
            disposition="inserted" if fact_inserted else "already_present",
            source_event_id=command.source_event.source_event_id,
            fact_id=command.fact.fact_id,
            current_fact_id=command.current_fact_id,
            current_revision=revision,
        )

    def recall_current(
        self, query: RecallCurrentFacts
    ) -> tuple[RecalledMemoryFact, ...]:
        self._require_transaction()
        if (
            query.tenant_id != self._tenant_id
            or query.installation_id != self._installation_id
        ):
            raise CurrentMemoryConflict("recall belongs to another scope")
        valid_at = _timestamp(query.requested_valid_at)
        scope_sha256 = _hash(canonical_sha256(query.scope))
        document = (
            fact_assertions.c.predicate
            + sa.literal(" ")
            + sa.cast(fact_assertions.c.value, sa.Text)
        )
        rank = sa.literal(0)
        predicates: list[Any] = [
            current_facts.c.tenant_id == _uuid(self._tenant_id),
            current_facts.c.installation_id == _uuid(self._installation_id),
            current_facts.c.scope_sha256 == scope_sha256,
            current_facts.c.subject_id.in_(query.subject_ids),
            fact_assertions.c.assertion_status == "active",
            fact_assertions.c.valid_from <= valid_at,
            sa.or_(
                fact_assertions.c.valid_until.is_(None),
                fact_assertions.c.valid_until > valid_at,
            ),
            fact_assertions.c.retention_until >= valid_at,
        ]
        candidate_order: dict[UUID, int] | None = None
        if query.candidate_fact_ids is not None:
            if not query.candidate_fact_ids:
                return ()
            candidate_order = {
                _uuid(fact_id): index
                for index, fact_id in enumerate(query.candidate_fact_ids)
            }
            predicates.append(fact_assertions.c.fact_id.in_(candidate_order))
        elif query.query_text.strip():
            vector = sa.func.to_tsvector(
                sa.literal_column("'simple'::regconfig"), document
            )
            terms = sa.func.plainto_tsquery(
                sa.literal_column("'simple'::regconfig"), query.query_text.strip()
            )
            predicates.append(vector.op("@@")(terms))
            rank = sa.func.ts_rank_cd(vector, terms)
        rows = (
            self._connection.execute(
                sa.select(fact_assertions, rank.label("memory_rank"))
                .select_from(
                    current_facts.join(
                        fact_assertions,
                        sa.and_(
                            current_facts.c.tenant_id == fact_assertions.c.tenant_id,
                            current_facts.c.installation_id
                            == fact_assertions.c.installation_id,
                            current_facts.c.fact_id == fact_assertions.c.fact_id,
                        ),
                    )
                )
                .where(*predicates)
                .order_by(
                    sa.desc("memory_rank"),
                    fact_assertions.c.observed_at.desc(),
                    fact_assertions.c.fact_id.asc(),
                )
                .limit(query.maximum_facts * 4)
            )
            .mappings()
            .all()
        )
        if candidate_order is not None:
            rows.sort(key=lambda row: candidate_order[row["fact_id"]])

        recalled: list[RecalledMemoryFact] = []
        for row in rows:
            source_ids = tuple(row["source_event_ids"])
            sources = (
                self._connection.execute(
                    sa.select(source_events).where(
                        source_events.c.tenant_id == _uuid(self._tenant_id),
                        source_events.c.installation_id == _uuid(self._installation_id),
                        source_events.c.source_event_id.in_(source_ids),
                        source_events.c.privacy_class.in_(
                            query.allowed_privacy_classes
                        ),
                        source_events.c.valid_from <= valid_at,
                        sa.or_(
                            source_events.c.valid_until.is_(None),
                            source_events.c.valid_until > valid_at,
                        ),
                        source_events.c.retention_until >= valid_at,
                    )
                )
                .mappings()
                .all()
            )
            if {item["source_event_id"] for item in sources} != set(source_ids):
                continue
            source_contracts = tuple(
                ExperienceSourceEvent.model_validate(item["payload"])
                for item in sorted(
                    sources,
                    key=lambda value: source_ids.index(value["source_event_id"]),
                )
            )
            recalled.append(
                RecalledMemoryFact(
                    fact=MemoryFact.model_validate(row["payload"]),
                    source_events=source_contracts,
                    ranking_reason=(
                        "hindsight_temporal_semantic_match"
                        if candidate_order is not None
                        else (
                            "postgres_full_text_match"
                            if query.query_text.strip()
                            else "current_fact_recency"
                        )
                    ),
                )
            )
            if len(recalled) >= query.maximum_facts:
                break
        return tuple(recalled)


__all__ = ["PostgresMemoryRepository"]
