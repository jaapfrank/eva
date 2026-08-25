"""Atomically retain one claimed action-chain provider-read result."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.action_chain_ledger import (
    PostgresActionChainLedger,
    StoredActionChain,
    plan_steps,
    read_intents,
)
from eva.adapters.postgres.action_chain_effect_readback_validation import (
    effect_readback_thread_id,
)
from eva.adapters.postgres.action_chain_reads import ActionChainReadConflict
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_checkpoints,
    operational_events,
    transition_timeline_thread,
)
from eva.adapters.postgres.scope import set_local_scope
from eva.application.action_chain import action_chain_step_operation_id
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector import ConnectorManifest
from eva.contracts.evidence import EvidenceEnvelopeV2
from eva.contracts.execution import ReadAttempt, ReadObservation
from eva.operations.action_chain_read_dispatcher import ClaimedActionChainReadCall
from eva.operations.action_chain_effect_readback_dispatcher import (
    ClaimedActionChainEffectReadbackCall,
)
from eva.operations.action_chain_read_results import (
    ProjectedActionChainReadResult,
    ProviderReadResult,
    project_action_chain_read_result,
)


read_attempts = metadata.tables["eva_effects.read_attempts"]
read_observations = metadata.tables["eva_effects.read_observations"]
evidence_envelopes = metadata.tables["eva_evidence.evidence_envelopes"]


@dataclass(frozen=True, slots=True)
class StoredActionChainReadResult:
    disposition: Literal["recorded", "already_recorded"]
    attempt: ReadAttempt
    evidence: EvidenceEnvelopeV2
    observation: ReadObservation | None


class PostgresActionChainReadResultStore:
    """Move a call-started attempt to one immutable terminal projection."""

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        role: Literal["eva_runtime", "eva_dbos"] = "eva_runtime",
    ) -> None:
        if engine.url.get_backend_name() != "postgresql":
            raise ValueError("action-chain read results require PostgreSQL")
        self._engine = engine
        self._scope_value = scope
        self._role = role
        self._ledger = PostgresActionChainLedger(engine, scope, role=role)

    def load_terminal(
        self,
        read_intent_id: UUID,
    ) -> StoredActionChainReadResult | None:
        """Load an already validated terminal result without call material."""

        with self._engine.begin() as connection:
            self._scope(connection)
            intent_row = connection.execute(
                sa.select(read_intents).where(
                    read_intents.c.tenant_id == self._scope_value.tenant_id,
                    read_intents.c.installation_id == self._scope_value.installation_id,
                    read_intents.c.read_intent_id == read_intent_id,
                )
            ).one_or_none()
            if intent_row is None:
                raise ActionChainReadConflict("read result intent is absent in scope")
            step_row = connection.execute(
                _step_statement(self._scope_value, intent_row, read_intent_id)
            ).one()
            attempt_row = connection.execute(
                sa.select(read_attempts).where(
                    read_attempts.c.tenant_id == self._scope_value.tenant_id,
                    read_attempts.c.installation_id == self._scope_value.installation_id,
                    read_attempts.c.read_intent_id == read_intent_id,
                    read_attempts.c.ordinal == 1,
                )
            ).one_or_none()
            if attempt_row is None:
                return None
            stored_attempt = _attempt(attempt_row)
            if stored_attempt.state == "dispatch_authorized":
                return None
            chain = self._ledger.load_in_transaction(connection, step_row.plan_id)
            operation_id = _result_thread_id(
                chain,
                step_row,
                intent_row,
                read_intent_id,
            )
            return self._load_stored_result(
                connection,
                stored_attempt=stored_attempt,
                operation_id=operation_id,
            )

    def record(
        self,
        claim: ClaimedActionChainReadCall | ClaimedActionChainEffectReadbackCall,
        *,
        result: ProviderReadResult,
        observed_at: datetime,
        recorded_at: datetime,
    ) -> StoredActionChainReadResult:
        _require_utc_second(observed_at, "observed_at")
        _require_utc_second(recorded_at, "recorded_at")
        if (
            claim.disposition != "claimed"
            or claim.process_spec is None
            or claim.connector_manifest is None
            or claim.initialize is None
            or claim.invoke is None
        ):
            raise ActionChainReadConflict("read result lacks its first-call claim")

        read_intent_id = UUID(claim.attempt.read_intent_id)
        with self._engine.begin() as connection:
            self._scope(connection)
            intent_row = connection.execute(
                sa.select(read_intents).where(
                    read_intents.c.tenant_id == self._scope_value.tenant_id,
                    read_intents.c.installation_id == self._scope_value.installation_id,
                    read_intents.c.read_intent_id == read_intent_id,
                )
            ).one_or_none()
            if intent_row is None:
                raise ActionChainReadConflict("call-started read intent is absent")
            step_row = connection.execute(
                _step_statement(
                    self._scope_value,
                    intent_row,
                    read_intent_id,
                ).with_for_update(read=True)
            ).one()
            attempt_row = connection.execute(
                sa.select(read_attempts)
                .where(
                    read_attempts.c.tenant_id == self._scope_value.tenant_id,
                    read_attempts.c.installation_id == self._scope_value.installation_id,
                    read_attempts.c.read_intent_id == read_intent_id,
                    read_attempts.c.ordinal == 1,
                )
                .with_for_update()
            ).one()
            chain = self._ledger.load_in_transaction(connection, step_row.plan_id)
            operation_id = _result_thread_id(
                chain,
                step_row,
                intent_row,
                read_intent_id,
            )
            stored_attempt = _attempt(attempt_row)
            if stored_attempt.state != "dispatch_authorized":
                return self._replay(
                    connection,
                    stored_attempt=stored_attempt,
                    result=result,
                    operation_id=operation_id,
                    observed_at=observed_at,
                    recorded_at=recorded_at,
                )
            if stored_attempt != claim.attempt:
                raise ActionChainReadConflict("read result claim differs from storage")

            latest = connection.execute(
                sa.select(operational_events)
                .where(
                    operational_events.c.tenant_id == self._scope_value.tenant_id,
                    operational_events.c.installation_id
                    == self._scope_value.installation_id,
                    operational_events.c.thread_id == operation_id,
                )
                .order_by(operational_events.c.sequence.desc())
                .limit(1)
            ).one()
            if latest.event_type != "ACTION_CHAIN_READ_PROVIDER_CALL_STARTED":
                raise ActionChainReadConflict("read result has no call-start marker")
            call_started_at = _parse_timestamp(
                str(_event_data(latest.payload).get("provider_call_started_at"))
            )
            if not call_started_at <= observed_at <= recorded_at:
                raise ActionChainReadConflict(
                    "read result times are outside the provider-call window"
                )
            projected = project_action_chain_read_result(
                tenant_id=self._scope_value.tenant_id,
                attempt=stored_attempt,
                initialize=claim.initialize,
                invoke=claim.invoke,
                manifest=claim.connector_manifest,
                result=result,
                observed_at=observed_at,
                recorded_at=recorded_at,
                retention_until=chain.retention_until,
            )
            self._insert_evidence(connection, chain, projected)
            if projected.observation is not None:
                self._insert_observation(connection, chain, projected.observation)
            terminal_payload = projected.attempt.model_dump(mode="json")
            updated = connection.execute(
                sa.update(read_attempts)
                .where(
                    read_attempts.c.tenant_id == self._scope_value.tenant_id,
                    read_attempts.c.installation_id == self._scope_value.installation_id,
                    read_attempts.c.read_attempt_id
                    == UUID(projected.attempt.read_attempt_id),
                    read_attempts.c.state == "dispatch_authorized",
                    read_attempts.c.revision == 1,
                )
                .values(
                    state=projected.attempt.state,
                    response_sha256=(
                        None
                        if projected.attempt.response_sha256 is None
                        else bytes.fromhex(projected.attempt.response_sha256)
                    ),
                    payload=terminal_payload,
                    payload_sha256=bytes.fromhex(canonical_sha256(projected.attempt)),
                    revision=2,
                    updated_at=recorded_at,
                )
            )
            if updated.rowcount != 1:
                raise ActionChainReadConflict("read result lost its attempt revision")
            checkpoint = connection.execute(
                sa.select(operational_checkpoints)
                .where(
                    operational_checkpoints.c.tenant_id
                    == self._scope_value.tenant_id,
                    operational_checkpoints.c.installation_id
                    == self._scope_value.installation_id,
                    operational_checkpoints.c.thread_id == operation_id,
                )
                .with_for_update()
            ).one()
            event_type, phase, status = _terminal_timeline(projected.attempt.state)
            data = _event_data(latest.payload)
            data.update(
                _terminal_event_data(
                    projected,
                    connector_manifest=claim.connector_manifest,
                    observed_at=observed_at,
                    recorded_at=recorded_at,
                )
            )
            transition_timeline_thread(
                connection,
                self._scope_value,
                thread_id=operation_id,
                expected_revision=int(checkpoint.revision),
                checkpoint_id=f"checkpoint:{operation_id}:read-result",
                phase=phase,
                status=status,
                event_type=event_type,
                occurred_at=recorded_at,
                data=data,
                operation_id=operation_id,
                deadline=intent_row.deadline,
                retry_count=int(checkpoint.retry_count),
                last_error_fingerprint=(
                    None
                    if checkpoint.last_error_fingerprint is None
                    else bytes(checkpoint.last_error_fingerprint).hex()
                ),
            )
            self._ledger.load_in_transaction(connection, chain.plan_id)
            return StoredActionChainReadResult(
                "recorded",
                projected.attempt,
                projected.evidence,
                projected.observation,
            )

    def _insert_evidence(
        self,
        connection: sa.Connection,
        chain: StoredActionChain,
        projected: ProjectedActionChainReadResult,
    ) -> None:
        evidence = projected.evidence
        payload = evidence.model_dump(mode="json")
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
                subject_payload_sha256=bytes.fromhex(
                    evidence.subject_payload_sha256
                ),
                object_ref_id=None,
                object_sha256=None,
                predecessor_sha256=None,
                provenance_refs=[],
                captured_at=_parse_timestamp(evidence.captured_at),
                privacy_class=evidence.privacy_class,
                retention_policy_id=chain.retention_policy_id,
                retention_until=chain.retention_until,
                payload=payload,
                payload_sha256=bytes.fromhex(canonical_sha256(evidence)),
            )
        )

    def _insert_observation(
        self,
        connection: sa.Connection,
        chain: StoredActionChain,
        observation: ReadObservation,
    ) -> None:
        payload = observation.model_dump(mode="json")
        connection.execute(
            sa.insert(read_observations).values(
                tenant_id=self._scope_value.tenant_id,
                installation_id=self._scope_value.installation_id,
                observation_id=UUID(observation.observation_id),
                read_intent_id=UUID(observation.read_intent_id),
                read_attempt_id=UUID(observation.read_attempt_id),
                provider_resource_id=observation.provider_resource_id,
                retrieved_at=_parse_timestamp(observation.retrieved_at),
                observed_at=_parse_timestamp(observation.observed_at),
                normalized_value=observation.normalized_value,
                normalized_value_sha256=bytes.fromhex(
                    canonical_sha256(observation.normalized_value)
                ),
                response_sha256=bytes.fromhex(observation.response_sha256),
                trust=observation.trust,
                evidence_id=UUID(observation.evidence_ref),
                payload=payload,
                payload_sha256=bytes.fromhex(canonical_sha256(observation)),
                retention_policy_id=chain.retention_policy_id,
                retention_until=chain.retention_until,
            )
        )

    def _replay(
        self,
        connection: sa.Connection,
        *,
        stored_attempt: ReadAttempt,
        result: ProviderReadResult,
        operation_id: str,
        observed_at: datetime,
        recorded_at: datetime,
    ) -> StoredActionChainReadResult:
        event = connection.execute(
            sa.select(operational_events)
            .where(
                operational_events.c.tenant_id == self._scope_value.tenant_id,
                operational_events.c.installation_id == self._scope_value.installation_id,
                operational_events.c.thread_id == operation_id,
                operational_events.c.event_type.in_(
                    tuple(item[0] for item in _TERMINAL_TIMELINE.values())
                ),
            )
            .order_by(operational_events.c.occurred_at.desc())
            .limit(1)
        ).one()
        data = _event_data(event.payload)
        if (
            data.get("provider_result_sha256") != canonical_sha256(result)
            or data.get("provider_result_observed_at")
            != _format_timestamp(observed_at)
            or data.get("provider_result_recorded_at")
            != _format_timestamp(recorded_at)
        ):
            raise ActionChainReadConflict("read result replay differs")
        return self._load_stored_result(
            connection,
            stored_attempt=stored_attempt,
            operation_id=operation_id,
            event=event,
        )

    def _load_stored_result(
        self,
        connection: sa.Connection,
        *,
        stored_attempt: ReadAttempt,
        operation_id: str,
        event: sa.Row | None = None,
    ) -> StoredActionChainReadResult:
        if event is None:
            event = connection.execute(
                sa.select(operational_events)
                .where(
                    operational_events.c.tenant_id == self._scope_value.tenant_id,
                    operational_events.c.installation_id
                    == self._scope_value.installation_id,
                    operational_events.c.thread_id == operation_id,
                    operational_events.c.event_type.in_(
                        tuple(item[0] for item in _TERMINAL_TIMELINE.values())
                    ),
                )
                .order_by(operational_events.c.occurred_at.desc())
                .limit(1)
            ).one()
        data = _event_data(event.payload)
        evidence = EvidenceEnvelopeV2.model_validate(
            connection.scalar(
                sa.select(evidence_envelopes.c.payload).where(
                    evidence_envelopes.c.tenant_id == self._scope_value.tenant_id,
                    evidence_envelopes.c.installation_id
                    == self._scope_value.installation_id,
                    evidence_envelopes.c.evidence_id
                    == UUID(str(data["provider_evidence_id"])),
                )
            )
        )
        observation_payload = connection.scalar(
            sa.select(read_observations.c.payload).where(
                read_observations.c.tenant_id == self._scope_value.tenant_id,
                read_observations.c.installation_id
                == self._scope_value.installation_id,
                read_observations.c.read_attempt_id
                == UUID(stored_attempt.read_attempt_id),
            )
        )
        observation = (
            None
            if observation_payload is None
            else ReadObservation.model_validate(observation_payload)
        )
        return StoredActionChainReadResult(
            "already_recorded", stored_attempt, evidence, observation
        )

    def _scope(self, connection: sa.Connection) -> None:
        connection.exec_driver_sql(f"SET LOCAL ROLE {self._role}")
        set_local_scope(
            connection,
            tenant_id=str(self._scope_value.tenant_id),
            installation_id=str(self._scope_value.installation_id),
        )


_TERMINAL_TIMELINE = {
    "observed": ("ACTION_CHAIN_READ_OBSERVED", "action_chain_read_observed", "active"),
    "ambiguous": (
        "ACTION_CHAIN_READ_AMBIGUOUS",
        "action_chain_read_ambiguous",
        "needs_help",
    ),
    "rejected": ("ACTION_CHAIN_READ_REJECTED", "action_chain_read_rejected", "failed"),
    "rate_limited": (
        "ACTION_CHAIN_READ_RATE_LIMITED",
        "action_chain_read_rate_limited",
        "active",
    ),
}


def _step_statement(
    scope: TimelineScope,
    intent_row: sa.Row,
    read_intent_id: UUID,
) -> sa.Select:
    statement = sa.select(plan_steps).where(
        plan_steps.c.tenant_id == scope.tenant_id,
        plan_steps.c.installation_id == scope.installation_id,
        plan_steps.c.plan_step_id == intent_row.plan_step_id,
    )
    if intent_row.purpose == "effect_verify":
        if intent_row.effect_intent_id is None:
            raise ActionChainReadConflict("effect readback result has no effect")
        return statement.where(
            plan_steps.c.effect_intent_id == intent_row.effect_intent_id,
            plan_steps.c.read_intent_id.is_(None),
        )
    if intent_row.effect_intent_id is not None:
        raise ActionChainReadConflict("ordinary read result binds an effect")
    return statement.where(
        plan_steps.c.read_intent_id == read_intent_id,
        plan_steps.c.effect_intent_id.is_(None),
    )


def _result_thread_id(
    chain: StoredActionChain,
    step_row: sa.Row,
    intent_row: sa.Row,
    read_intent_id: UUID,
) -> str:
    if intent_row.purpose == "effect_verify":
        return effect_readback_thread_id(read_intent_id)
    return action_chain_step_operation_id(chain.plan, step_row.logical_step_key)


def _terminal_timeline(state: str) -> tuple[str, str, str]:
    try:
        return _TERMINAL_TIMELINE[state]
    except KeyError as error:
        raise ActionChainReadConflict("read result state is not terminal") from error


def _terminal_event_data(
    projected: ProjectedActionChainReadResult,
    *,
    connector_manifest: ConnectorManifest,
    observed_at: datetime,
    recorded_at: datetime,
) -> dict[str, object]:
    observation = projected.observation
    return {
        "connector_manifest": connector_manifest.model_dump(mode="json"),
        "provider_evidence_id": projected.evidence.evidence_id,
        "provider_evidence_payload_sha256": canonical_sha256(projected.evidence),
        "provider_result": projected.provider_result.model_dump(mode="json"),
        "provider_result_observed_at": _format_timestamp(observed_at),
        "provider_result_recorded_at": _format_timestamp(recorded_at),
        "provider_result_sha256": projected.provider_result_sha256,
        "read_attempt_terminal_payload_sha256": canonical_sha256(projected.attempt),
        "read_observation_id": None if observation is None else observation.observation_id,
        "read_observation_payload_sha256": (
            None if observation is None else canonical_sha256(observation)
        ),
        "read_terminal_state": projected.attempt.state,
    }


def _attempt(row: sa.Row) -> ReadAttempt:
    try:
        attempt = ReadAttempt.model_validate(row.payload)
    except ValueError as error:
        raise ActionChainReadConflict("stored read attempt is invalid") from error
    if canonical_sha256(attempt) != bytes(row.payload_sha256).hex():
        raise ActionChainReadConflict("stored read attempt hash differs")
    return attempt


def _event_data(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise ActionChainReadConflict("read result timeline payload differs")
    return dict(payload["data"])


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _format_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_utc_second(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{label} must be timezone-aware UTC")
    if value.microsecond:
        raise ValueError(f"{label} must use whole-second precision")


__all__ = ["PostgresActionChainReadResultStore", "StoredActionChainReadResult"]
