"""Shared durable mechanics for terminal read and effect results."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.action_chain_ledger import (
    PostgresActionChainLedger,
    StoredActionChain,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.adapters.postgres.scope import PostgresScopedRole
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.evidence import EvidenceEnvelopeV2
from eva.contracts.timestamps import parse_utc_timestamp


evidence_envelopes = metadata.tables["eva_evidence.evidence_envelopes"]


class PostgresActionChainResultStore(PostgresScopedRole):
    """Own the storage invariants shared by provider read/effect results."""

    _result_kind: Literal["read", "effect"]
    _conflict_type: type[Exception]

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        role: Literal["eva_runtime", "eva_dbos"] = "eva_runtime",
    ) -> None:
        if engine.url.get_backend_name() != "postgresql":
            raise ValueError(
                f"action-chain {self._result_kind} results require PostgreSQL"
            )
        self._engine = engine
        self._scope_value = scope
        self._role = role
        self._ledger = PostgresActionChainLedger(engine, scope, role=role)

    def _insert_evidence(
        self,
        connection: sa.Connection,
        chain: StoredActionChain,
        evidence: EvidenceEnvelopeV2,
    ) -> None:
        connection.execute(
            sa.insert(evidence_envelopes).values(
                tenant_id=self._scope_value.tenant_id,
                installation_id=self._scope_value.installation_id,
                evidence_id=UUID(evidence.evidence_id),
                aggregate_type=evidence.aggregate_type,
                aggregate_id=evidence.aggregate_id,
                sequence=evidence.sequence,
                idempotency_key=evidence.idempotency_key,
                producer=evidence.producer,
                subject_contract=evidence.subject_contract,
                subject_id=evidence.subject_id,
                subject_payload_sha256=bytes.fromhex(evidence.subject_payload_sha256),
                object_ref_id=None,
                object_sha256=None,
                predecessor_sha256=None,
                provenance_refs=[],
                captured_at=parse_utc_timestamp(evidence.captured_at),
                privacy_class=evidence.privacy_class,
                retention_policy_id=chain.retention_policy_id,
                retention_until=chain.retention_until,
                payload=evidence.model_dump(mode="json"),
                payload_sha256=bytes.fromhex(canonical_sha256(evidence)),
            )
        )

    def _event_data(self, payload: object) -> dict[str, object]:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise self._conflict_type(
                f"{self._result_kind} result timeline payload differs"
            )
        return dict(payload["data"])

    def _load_evidence(
        self, connection: sa.Connection, event_data: dict[str, object]
    ) -> EvidenceEnvelopeV2:
        payload = connection.scalar(
            sa.select(evidence_envelopes.c.payload).where(
                evidence_envelopes.c.tenant_id == self._scope_value.tenant_id,
                evidence_envelopes.c.installation_id
                == self._scope_value.installation_id,
                evidence_envelopes.c.evidence_id
                == UUID(str(event_data["provider_evidence_id"])),
            )
        )
        return EvidenceEnvelopeV2.model_validate(payload)
