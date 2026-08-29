"""Atomically retain one claimed action-chain effect-provider result."""

from __future__ import annotations
from eva.contracts.timestamps import format_utc_timestamp as _format_timestamp
from eva.contracts.timestamps import parse_utc_timestamp as _parse_timestamp
from eva.contracts.timestamps import require_utc_second as _require_utc_second

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import sqlalchemy as sa

from eva.adapters.postgres.action_chain_effects import ActionChainEffectConflict
from eva.adapters.postgres.action_chain_ledger import (
    StoredActionChain,
    plan_steps,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    operational_checkpoints,
    operational_events,
    transition_timeline_thread,
)
from eva.adapters.postgres.action_chain_result_common import (
    PostgresActionChainResultStore,
)
from eva.application.action_chain import action_chain_step_operation_id
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector import ConnectorManifest
from eva.contracts.evidence import EvidenceEnvelopeV2
from eva.contracts.execution import EffectAttempt, EffectIntent, EffectReceipt
from eva.operations.action_chain_effect_dispatcher import (
    ClaimedActionChainEffectCall,
)
from eva.operations.action_chain_effect_results import (
    ProjectedActionChainEffectResult,
    ProviderEffectResult,
    project_action_chain_effect_result,
)


effect_intents = metadata.tables["eva_effects.effect_intents"]
effect_attempts = metadata.tables["eva_effects.effect_attempts"]
effect_receipts = metadata.tables["eva_effects.effect_receipts"]


@dataclass(frozen=True, slots=True)
class StoredActionChainEffectResult:
    disposition: Literal["recorded", "already_recorded"]
    intent: EffectIntent
    attempt: EffectAttempt
    evidence: EvidenceEnvelopeV2
    receipt: EffectReceipt | None


class PostgresActionChainEffectResultStore(PostgresActionChainResultStore):
    """Move one call-started effect attempt to a durable provider outcome."""

    _result_kind = "effect"
    _conflict_type = ActionChainEffectConflict

    def load_terminal(
        self, effect_intent_id: UUID
    ) -> StoredActionChainEffectResult | None:
        """Load a terminal provider result without ever exposing call material."""

        with self._engine.begin() as connection:
            self._scope(connection)
            intent_row, step_row, attempt_row = self._rows(
                connection, effect_intent_id, lock=False
            )
            if attempt_row is None:
                return None
            attempt = _attempt(attempt_row)
            if attempt.state == "dispatch_authorized":
                return None
            chain = self._ledger.load_in_transaction(connection, step_row.plan_id)
            return self._load_stored(
                connection,
                intent=_intent(intent_row),
                attempt=attempt,
                operation_id=action_chain_step_operation_id(
                    chain.plan,
                    step_row.logical_step_key,
                ),
            )

    def record(
        self,
        claim: ClaimedActionChainEffectCall,
        *,
        result: ProviderEffectResult,
        observed_at: datetime,
        recorded_at: datetime,
    ) -> StoredActionChainEffectResult:
        """Store evidence and receipt atomically; physical readback stays absent."""

        _require_utc_second(observed_at, "observed_at")
        _require_utc_second(recorded_at, "recorded_at")
        if (
            claim.disposition != "claimed"
            or claim.process_spec is None
            or claim.connector_manifest is None
            or claim.initialize is None
            or claim.invoke is None
        ):
            raise ActionChainEffectConflict("effect result lacks its first-call claim")

        effect_intent_id = UUID(claim.attempt.effect_intent_id)
        with self._engine.begin() as connection:
            self._scope(connection)
            intent_row, step_row, attempt_row = self._rows(
                connection, effect_intent_id, lock=True
            )
            if attempt_row is None:
                raise ActionChainEffectConflict("call-started effect attempt is absent")
            chain = self._ledger.load_in_transaction(connection, step_row.plan_id)
            operation_id = action_chain_step_operation_id(
                chain.plan, step_row.logical_step_key
            )
            intent = _intent(intent_row)
            attempt = _attempt(attempt_row)
            if attempt.state != "dispatch_authorized":
                return self._replay(
                    connection,
                    intent=intent,
                    attempt=attempt,
                    operation_id=operation_id,
                    result=result,
                    observed_at=observed_at,
                    recorded_at=recorded_at,
                )
            if attempt != claim.attempt or intent.state != "dispatch_authorized":
                raise ActionChainEffectConflict(
                    "effect result claim differs from storage"
                )
            latest = self._latest_event(connection, operation_id)
            if latest.event_type != "ACTION_CHAIN_EFFECT_PROVIDER_CALL_STARTED":
                raise ActionChainEffectConflict(
                    "effect result has no call-start marker"
                )
            started_at = _parse_timestamp(
                str(self._event_data(latest.payload).get("provider_call_started_at"))
            )
            if not started_at <= observed_at <= recorded_at:
                raise ActionChainEffectConflict(
                    "effect result times are outside the provider-call window"
                )
            projected = project_action_chain_effect_result(
                tenant_id=self._scope_value.tenant_id,
                intent=intent,
                attempt=attempt,
                initialize=claim.initialize,
                invoke=claim.invoke,
                manifest=claim.connector_manifest,
                result=result,
                observed_at=observed_at,
                recorded_at=recorded_at,
                retention_until=chain.retention_until,
            )
            self._insert_evidence(connection, chain, projected.evidence)
            if projected.receipt is not None:
                self._insert_receipt(connection, chain, projected.receipt, recorded_at)
            self._update_attempt(connection, projected, recorded_at)
            self._update_intent(connection, projected, recorded_at)
            checkpoint = connection.execute(
                sa.select(operational_checkpoints)
                .where(
                    operational_checkpoints.c.tenant_id == self._scope_value.tenant_id,
                    operational_checkpoints.c.installation_id
                    == self._scope_value.installation_id,
                    operational_checkpoints.c.thread_id == operation_id,
                )
                .with_for_update()
            ).one()
            event_type, phase, status = _terminal_timeline(projected.attempt.state)
            data = self._event_data(latest.payload)
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
                checkpoint_id=f"checkpoint:{operation_id}:effect-result",
                phase=phase,
                status=status,
                event_type=event_type,
                occurred_at=recorded_at,
                data=data,
                operation_id=operation_id,
                deadline=_parse_timestamp(chain.plan.deadline),
                retry_count=int(checkpoint.retry_count),
                last_error_fingerprint=(
                    None
                    if checkpoint.last_error_fingerprint is None
                    else bytes(checkpoint.last_error_fingerprint).hex()
                ),
            )
            return StoredActionChainEffectResult(
                "recorded",
                projected.intent,
                projected.attempt,
                projected.evidence,
                projected.receipt,
            )

    def _rows(
        self, connection: sa.Connection, effect_intent_id: UUID, *, lock: bool
    ) -> tuple[sa.Row, sa.Row, sa.Row | None]:
        intent_query = sa.select(effect_intents).where(
            effect_intents.c.tenant_id == self._scope_value.tenant_id,
            effect_intents.c.installation_id == self._scope_value.installation_id,
            effect_intents.c.effect_intent_id == effect_intent_id,
        )
        if lock:
            intent_query = intent_query.with_for_update()
        intent_row = connection.execute(intent_query).one_or_none()
        if intent_row is None:
            raise ActionChainEffectConflict("effect result intent is absent in scope")
        step_query = sa.select(plan_steps).where(
            plan_steps.c.tenant_id == self._scope_value.tenant_id,
            plan_steps.c.installation_id == self._scope_value.installation_id,
            plan_steps.c.plan_step_id == intent_row.plan_step_id,
            plan_steps.c.effect_intent_id == effect_intent_id,
        )
        if lock:
            step_query = step_query.with_for_update(read=True)
        step_row = connection.execute(step_query).one()
        attempt_query = sa.select(effect_attempts).where(
            effect_attempts.c.tenant_id == self._scope_value.tenant_id,
            effect_attempts.c.installation_id == self._scope_value.installation_id,
            effect_attempts.c.effect_intent_id == effect_intent_id,
            effect_attempts.c.ordinal == 1,
        )
        if lock:
            attempt_query = attempt_query.with_for_update()
        return intent_row, step_row, connection.execute(attempt_query).one_or_none()

    def _update_attempt(
        self,
        connection: sa.Connection,
        projected: ProjectedActionChainEffectResult,
        recorded_at: datetime,
    ) -> None:
        attempt = projected.attempt
        updated = connection.execute(
            sa.update(effect_attempts)
            .where(
                effect_attempts.c.tenant_id == self._scope_value.tenant_id,
                effect_attempts.c.installation_id == self._scope_value.installation_id,
                effect_attempts.c.effect_attempt_id == UUID(attempt.effect_attempt_id),
                effect_attempts.c.state == "dispatch_authorized",
                effect_attempts.c.revision == 1,
            )
            .values(
                state=attempt.state,
                dispatch_permit_sha256=(
                    None
                    if attempt.dispatch_permit_sha256 is None
                    else bytes.fromhex(attempt.dispatch_permit_sha256)
                ),
                response_sha256=(
                    None
                    if attempt.response_sha256 is None
                    else bytes.fromhex(attempt.response_sha256)
                ),
                error_class=attempt.error_class,
                transport_evidence_id=UUID(projected.evidence.evidence_id),
                payload=attempt.model_dump(mode="json"),
                payload_sha256=bytes.fromhex(canonical_sha256(attempt)),
                revision=2,
                updated_at=recorded_at,
            )
        )
        if updated.rowcount != 1:
            raise ActionChainEffectConflict("effect result lost its attempt revision")

    def _update_intent(
        self,
        connection: sa.Connection,
        projected: ProjectedActionChainEffectResult,
        recorded_at: datetime,
    ) -> None:
        intent = projected.intent
        updated = connection.execute(
            sa.update(effect_intents)
            .where(
                effect_intents.c.tenant_id == self._scope_value.tenant_id,
                effect_intents.c.installation_id == self._scope_value.installation_id,
                effect_intents.c.effect_intent_id == UUID(intent.effect_intent_id),
                effect_intents.c.state == "dispatch_authorized",
                effect_intents.c.revision == 2,
            )
            .values(
                state=intent.state,
                payload=intent.model_dump(mode="json"),
                payload_sha256=bytes.fromhex(canonical_sha256(intent)),
                revision=3,
                updated_at=recorded_at,
            )
        )
        if updated.rowcount != 1:
            raise ActionChainEffectConflict("effect result lost its intent revision")

    def _insert_receipt(
        self,
        connection: sa.Connection,
        chain: StoredActionChain,
        receipt: EffectReceipt,
        recorded_at: datetime,
    ) -> None:
        connection.execute(
            sa.insert(effect_receipts).values(
                tenant_id=self._scope_value.tenant_id,
                installation_id=self._scope_value.installation_id,
                receipt_id=UUID(receipt.receipt_id),
                effect_intent_id=UUID(receipt.effect_intent_id),
                effect_attempt_id=UUID(receipt.effect_attempt_id),
                decision=receipt.decision,
                provider_resource_id=receipt.provider_resource_id,
                provider_receipt_sha256=bytes.fromhex(receipt.provider_receipt_sha256),
                request_payload_sha256=bytes.fromhex(receipt.request_payload_sha256),
                provider_time=_parse_timestamp(receipt.provider_time),
                evidence_id=UUID(receipt.evidence_ref),
                payload=receipt.model_dump(mode="json"),
                payload_sha256=bytes.fromhex(canonical_sha256(receipt)),
                retention_policy_id=chain.retention_policy_id,
                retention_until=chain.retention_until,
                created_at=recorded_at,
            )
        )

    def _replay(
        self,
        connection: sa.Connection,
        *,
        intent: EffectIntent,
        attempt: EffectAttempt,
        operation_id: str,
        result: ProviderEffectResult,
        observed_at: datetime,
        recorded_at: datetime,
    ) -> StoredActionChainEffectResult:
        event = self._latest_event(connection, operation_id)
        data = self._event_data(event.payload)
        if (
            data.get("provider_result_sha256") != canonical_sha256(result)
            or data.get("provider_result_observed_at") != _format_timestamp(observed_at)
            or data.get("provider_result_recorded_at") != _format_timestamp(recorded_at)
        ):
            raise ActionChainEffectConflict("effect result replay differs")
        return self._load_stored(
            connection,
            intent=intent,
            attempt=attempt,
            operation_id=operation_id,
            event=event,
        )

    def _load_stored(
        self,
        connection: sa.Connection,
        *,
        intent: EffectIntent,
        attempt: EffectAttempt,
        operation_id: str,
        event: sa.Row | None = None,
    ) -> StoredActionChainEffectResult:
        event = event or self._latest_event(connection, operation_id)
        data = self._event_data(event.payload)
        evidence = self._load_evidence(connection, data)
        receipt_payload = connection.scalar(
            sa.select(effect_receipts.c.payload).where(
                effect_receipts.c.tenant_id == self._scope_value.tenant_id,
                effect_receipts.c.installation_id == self._scope_value.installation_id,
                effect_receipts.c.effect_attempt_id == UUID(attempt.effect_attempt_id),
            )
        )
        receipt = (
            None
            if receipt_payload is None
            else EffectReceipt.model_validate(receipt_payload)
        )
        return StoredActionChainEffectResult(
            "already_recorded", intent, attempt, evidence, receipt
        )

    def _latest_event(self, connection: sa.Connection, operation_id: str) -> sa.Row:
        return connection.execute(
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


_TERMINAL_TIMELINE = {
    "provider_accepted": (
        "ACTION_CHAIN_EFFECT_PROVIDER_ACCEPTED",
        "action_chain_effect_provider_accepted",
        "waiting_for_proof",
    ),
    "definitive_reject": (
        "ACTION_CHAIN_EFFECT_REJECTED",
        "action_chain_effect_rejected",
        "failed",
    ),
    "ambiguous_transport": (
        "ACTION_CHAIN_EFFECT_AMBIGUOUS",
        "action_chain_effect_ambiguous",
        "needs_help",
    ),
    "rate_limited": (
        "ACTION_CHAIN_EFFECT_RATE_LIMITED",
        "action_chain_effect_rate_limited",
        "active",
    ),
    "policy_denied": (
        "ACTION_CHAIN_EFFECT_BLOCKED",
        "action_chain_effect_blocked",
        "failed",
    ),
    "internal_failure": (
        "ACTION_CHAIN_EFFECT_BLOCKED",
        "action_chain_effect_blocked",
        "failed",
    ),
}


def _terminal_timeline(state: str) -> tuple[str, str, str]:
    try:
        return _TERMINAL_TIMELINE[state]
    except KeyError as error:
        raise ActionChainEffectConflict(
            "effect result state is not terminal"
        ) from error


def _terminal_event_data(
    projected: ProjectedActionChainEffectResult,
    *,
    connector_manifest: ConnectorManifest,
    observed_at: datetime,
    recorded_at: datetime,
) -> dict[str, object]:
    receipt = projected.receipt
    return {
        "connector_manifest": connector_manifest.model_dump(mode="json"),
        "effect_attempt_terminal_payload_sha256": canonical_sha256(projected.attempt),
        "effect_intent_terminal_payload_sha256": canonical_sha256(projected.intent),
        "effect_receipt_id": None if receipt is None else receipt.receipt_id,
        "effect_receipt_payload_sha256": (
            None if receipt is None else canonical_sha256(receipt)
        ),
        "effect_terminal_state": projected.attempt.state,
        "provider_evidence_id": projected.evidence.evidence_id,
        "provider_evidence_payload_sha256": canonical_sha256(projected.evidence),
        "provider_result": projected.provider_result.model_dump(mode="json"),
        "provider_result_observed_at": _format_timestamp(observed_at),
        "provider_result_recorded_at": _format_timestamp(recorded_at),
        "provider_result_sha256": projected.provider_result_sha256,
    }


def _intent(row: sa.Row) -> EffectIntent:
    try:
        value = EffectIntent.model_validate(row.payload)
    except ValueError as error:
        raise ActionChainEffectConflict("stored effect intent is invalid") from error
    if canonical_sha256(value) != bytes(row.payload_sha256).hex():
        raise ActionChainEffectConflict("stored effect intent hash differs")
    return value


def _attempt(row: sa.Row) -> EffectAttempt:
    try:
        value = EffectAttempt.model_validate(row.payload)
    except ValueError as error:
        raise ActionChainEffectConflict("stored effect attempt is invalid") from error
    if canonical_sha256(value) != bytes(row.payload_sha256).hex():
        raise ActionChainEffectConflict("stored effect attempt hash differs")
    return value


__all__ = [
    "PostgresActionChainEffectResultStore",
    "StoredActionChainEffectResult",
]
