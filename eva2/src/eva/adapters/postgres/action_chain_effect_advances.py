"""Atomically verify one physical effect and release its dependencies."""

from __future__ import annotations
from eva.contracts.timestamps import parse_utc_timestamp as _parse_timestamp
from eva.contracts.timestamps import require_utc_second as _require_utc_second

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.action_chain_effect_advance_validation import (
    effect_completion_context,
    project_stored_action_chain_effect_proof,
)
from eva.adapters.postgres.action_chain_effect_stop_requests import (
    ActionChainEffectStopRequestConflict,
    has_valid_post_call_stop_request,
)
from eva.adapters.postgres.action_chain_leases import (
    _locked_attention,
    write_action_chain_attention,
)
from eva.adapters.postgres.action_chain_ledger import (
    PostgresActionChainLedger,
    plan_steps,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_checkpoints,
    operational_events,
    transition_timeline_thread,
)
from eva.adapters.postgres.scope import PostgresScopedRole
from eva.application.action_chain import (
    action_chain_step_operation_id,
    action_chain_thread_id,
)
from eva.application.effect_verification import (
    EffectVerificationError,
    EffectVerificationProjection,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.execution import EffectAttempt, EffectIntent


effect_attempts = metadata.tables["eva_effects.effect_attempts"]
effect_intents = metadata.tables["eva_effects.effect_intents"]


class ActionChainEffectAdvanceConflict(RuntimeError):
    """The effect or its independent proof is not eligible for success."""


@dataclass(frozen=True, slots=True)
class AdvancedActionChainEffect:
    disposition: Literal["advanced", "already_advanced"]
    effect_step_id: UUID
    released_step_ids: tuple[UUID, ...]
    outcome_ref: str


class PostgresActionChainEffectAdvancer(PostgresScopedRole):
    """Commit STATE_VERIFIED, step success and dependency release together."""

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        role: Literal["eva_runtime", "eva_dbos"] = "eva_runtime",
    ) -> None:
        if engine.url.get_backend_name() != "postgresql":
            raise ValueError("action-chain effect advances require PostgreSQL")
        self._engine = engine
        self._scope_value = scope
        self._role = role
        self._ledger = PostgresActionChainLedger(engine, scope, role=role)

    def advance(
        self, effect_intent_id: UUID, *, now: datetime
    ) -> AdvancedActionChainEffect:
        _require_utc_second(now, "now")
        with self._engine.begin() as connection:
            self._scope(connection)
            seed = connection.execute(
                sa.select(plan_steps.c.plan_id, plan_steps.c.plan_step_id).where(
                    plan_steps.c.tenant_id == self._scope_value.tenant_id,
                    plan_steps.c.installation_id == self._scope_value.installation_id,
                    plan_steps.c.effect_intent_id == effect_intent_id,
                )
            ).one_or_none()
            if seed is None:
                raise ActionChainEffectAdvanceConflict(
                    "effect advance step is absent in scope"
                )
            attention = _locked_attention(
                connection,
                self._scope_value,
                uuid5(seed.plan_id, "continuation:1"),
            )
            rows = connection.execute(
                sa.select(plan_steps)
                .where(
                    plan_steps.c.tenant_id == self._scope_value.tenant_id,
                    plan_steps.c.installation_id == self._scope_value.installation_id,
                    plan_steps.c.plan_id == seed.plan_id,
                )
                .order_by(plan_steps.c.plan_step_id)
                .with_for_update()
            ).all()
            effect_row = connection.execute(
                sa.select(effect_intents)
                .where(
                    effect_intents.c.tenant_id == self._scope_value.tenant_id,
                    effect_intents.c.installation_id
                    == self._scope_value.installation_id,
                    effect_intents.c.effect_intent_id == effect_intent_id,
                )
                .with_for_update()
            ).one_or_none()
            by_id = {row.plan_step_id: row for row in rows}
            step_row = by_id.get(seed.plan_step_id)
            if attention is None or effect_row is None or step_row is None:
                raise ActionChainEffectAdvanceConflict(
                    "effect advance durable binding is incomplete"
                )
            chain = self._ledger.load_in_transaction(connection, seed.plan_id)
            if step_row.state == "succeeded":
                if now < step_row.updated_at:
                    raise ActionChainEffectAdvanceConflict(
                        "effect advance replay predates storage"
                    )
                return self._replay(
                    connection,
                    chain=chain,
                    effect_intent_id=effect_intent_id,
                    effect_step_id=step_row.plan_step_id,
                    outcome_ref=step_row.outcome_ref,
                )
            if (
                step_row.effect_class != "effect"
                or step_row.state != "running"
                or step_row.read_intent_id is not None
                or step_row.effect_intent_id != effect_intent_id
                or step_row.outcome_ref is not None
                or effect_row.state != "provider_accepted"
                or int(effect_row.revision) != 3
                or attention.state != "waiting"
                or attention.lease_owner is not None
                or attention.lease_expires_at is not None
            ):
                raise ActionChainEffectAdvanceConflict(
                    "effect is not ready for independent verification"
                )
            attempt_rows = connection.execute(
                sa.select(effect_attempts).where(
                    effect_attempts.c.tenant_id == self._scope_value.tenant_id,
                    effect_attempts.c.installation_id
                    == self._scope_value.installation_id,
                    effect_attempts.c.effect_intent_id == effect_intent_id,
                )
            ).all()
            if len(attempt_rows) != 1:
                raise ActionChainEffectAdvanceConflict(
                    "effect provider attempt cardinality differs"
                )
            try:
                attempt = EffectAttempt.model_validate(attempt_rows[0].payload)
                if has_valid_post_call_stop_request(
                    connection,
                    scope=self._scope_value,
                    chain=chain,
                    step_row=step_row,
                    effect_intent_id=effect_intent_id,
                    attempt=attempt,
                ):
                    raise ActionChainEffectAdvanceConflict(
                        "owner stopped the chain after provider call start"
                    )
            except ActionChainEffectStopRequestConflict as error:
                raise ActionChainEffectAdvanceConflict(
                    "effect stop request is invalid"
                ) from error
            action = next(
                (
                    item
                    for item in chain.decision.actions
                    if item.action_id == step_row.logical_step_key
                ),
                None,
            )
            descriptor = next(
                (
                    item
                    for item in chain.authority_snapshot.get("descriptors", [])
                    if isinstance(item, dict)
                    and action is not None
                    and item.get("operation") == action.operation
                ),
                None,
            )
            try:
                effect = EffectIntent.model_validate(effect_row.payload)
                projection = EffectVerificationProjection.from_authority_payload(
                    None
                    if descriptor is None
                    else descriptor.get("effect_verification")
                )
                if action is None:
                    raise ValueError("effect action is absent")
                proof, readback, observation = project_stored_action_chain_effect_proof(
                    connection,
                    self._scope_value,
                    effect_intent_id=effect_intent_id,
                    projection=projection,
                    resource=action.resource,
                    arguments=action.arguments,
                    provider_accepted_at=attempt_rows[0].updated_at,
                    verified_at=now,
                    deadline=_parse_timestamp(chain.plan.deadline),
                    corrupt=ActionChainEffectAdvanceConflict,
                )
            except (EffectVerificationError, ValueError) as error:
                raise ActionChainEffectAdvanceConflict(
                    "effect verification authority is invalid"
                ) from error

            succeeded = {row.plan_step_id for row in rows if row.state == "succeeded"}
            succeeded.add(step_row.plan_step_id)
            released = tuple(
                sorted(
                    (
                        row.plan_step_id
                        for row in rows
                        if row.state == "pending"
                        and row.dependency_step_ids
                        and set(row.dependency_step_ids).issubset(succeeded)
                    ),
                    key=str,
                )
            )
            context = effect_completion_context(
                effect_step_id=step_row.plan_step_id,
                projection=projection,
                proof=proof,
                readback=readback,
                observation=observation,
                released_step_ids=released,
            )
            verified_effect = EffectIntent.model_validate(
                {**effect.model_dump(mode="json"), "state": "verified"}
            )
            self._commit_rows(
                connection,
                effect_row=effect_row,
                step_row=step_row,
                by_id=by_id,
                released=released,
                verified_effect=verified_effect,
                outcome_ref=str(context["effect_outcome_ref"]),
                now=now,
            )
            operation_id = action_chain_step_operation_id(
                chain.plan, step_row.logical_step_key
            )
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
            transition_timeline_thread(
                connection,
                self._scope_value,
                thread_id=operation_id,
                expected_revision=int(checkpoint.revision),
                checkpoint_id=f"checkpoint:{operation_id}:state-verified",
                phase="action_chain_effect_state_verified",
                status="completed",
                event_type="STATE_VERIFIED",
                occurred_at=now,
                data=context,
                operation_id=operation_id,
                deadline=_parse_timestamp(chain.plan.deadline),
                retry_count=0,
                last_error_fingerprint=None,
            )
            existing_ready = any(
                row.state == "ready" and row.plan_step_id != step_row.plan_step_id
                for row in rows
            )
            next_state: Literal["pending", "waiting"] = (
                "pending" if released or existing_ready else "waiting"
            )
            write_action_chain_attention(
                connection,
                self._scope_value,
                chain,
                attention,
                state=next_state,
                worker_id=None,
                lease_expires_at=None,
                not_before=now,
                now=now,
                root_event_type="ACTION_CHAIN_EFFECT_DEPENDENCIES_RELEASED",
                root_status="active",
                reason="verified physical effect proof advanced the action chain",
                transition_context=context,
            )
            self._ledger.load_in_transaction(connection, chain.plan_id)
            return AdvancedActionChainEffect(
                "advanced",
                step_row.plan_step_id,
                released,
                str(context["effect_outcome_ref"]),
            )

    def _commit_rows(
        self,
        connection: sa.Connection,
        *,
        effect_row: sa.Row,
        step_row: sa.Row,
        by_id: dict[UUID, sa.Row],
        released: tuple[UUID, ...],
        verified_effect: EffectIntent,
        outcome_ref: str,
        now: datetime,
    ) -> None:
        updated = connection.execute(
            sa.update(effect_intents)
            .where(
                effect_intents.c.tenant_id == self._scope_value.tenant_id,
                effect_intents.c.installation_id == self._scope_value.installation_id,
                effect_intents.c.effect_intent_id == effect_row.effect_intent_id,
                effect_intents.c.state == "provider_accepted",
                effect_intents.c.revision == 3,
            )
            .values(
                state="verified",
                payload=verified_effect.model_dump(mode="json"),
                payload_sha256=bytes.fromhex(canonical_sha256(verified_effect)),
                revision=4,
                updated_at=now,
            )
        )
        step_updated = connection.execute(
            sa.update(plan_steps)
            .where(
                plan_steps.c.tenant_id == self._scope_value.tenant_id,
                plan_steps.c.installation_id == self._scope_value.installation_id,
                plan_steps.c.plan_step_id == step_row.plan_step_id,
                plan_steps.c.state == "running",
                plan_steps.c.effect_intent_id == effect_row.effect_intent_id,
                plan_steps.c.revision == step_row.revision,
            )
            .values(
                state="succeeded",
                outcome_ref=outcome_ref,
                revision=int(step_row.revision) + 1,
                updated_at=now,
            )
        )
        if updated.rowcount != 1 or step_updated.rowcount != 1:
            raise ActionChainEffectAdvanceConflict("effect changed during advance")
        for released_id in released:
            row = by_id[released_id]
            result = connection.execute(
                sa.update(plan_steps)
                .where(
                    plan_steps.c.tenant_id == self._scope_value.tenant_id,
                    plan_steps.c.installation_id == self._scope_value.installation_id,
                    plan_steps.c.plan_step_id == released_id,
                    plan_steps.c.state == "pending",
                    plan_steps.c.read_intent_id.is_(None),
                    plan_steps.c.effect_intent_id.is_(None),
                    plan_steps.c.outcome_ref.is_(None),
                    plan_steps.c.revision == row.revision,
                )
                .values(state="ready", revision=2, updated_at=now)
            )
            if result.rowcount != 1:
                raise ActionChainEffectAdvanceConflict(
                    "dependent step changed during effect advance"
                )

    def _replay(
        self,
        connection: sa.Connection,
        *,
        chain,
        effect_intent_id: UUID,
        effect_step_id: UUID,
        outcome_ref: str | None,
    ) -> AdvancedActionChainEffect:
        events = connection.execute(
            sa.select(operational_events).where(
                operational_events.c.tenant_id == self._scope_value.tenant_id,
                operational_events.c.installation_id
                == self._scope_value.installation_id,
                operational_events.c.thread_id == action_chain_thread_id(chain.plan),
                operational_events.c.event_type
                == "ACTION_CHAIN_EFFECT_DEPENDENCIES_RELEASED",
            )
        ).all()
        matches = []
        for event in events:
            data = event.payload.get("data")
            context = data.get("transition_context") if isinstance(data, dict) else None
            if isinstance(context, dict) and context.get("effect_step_id") == str(
                effect_step_id
            ):
                matches.append(context)
        if len(matches) != 1:
            raise ActionChainEffectAdvanceConflict(
                "effect advance replay event differs"
            )
        context = matches[0]
        raw_released = context.get("released_step_ids")
        if (
            context.get("effect_intent_id") != str(effect_intent_id)
            or context.get("effect_outcome_ref") != outcome_ref
            or not isinstance(outcome_ref, str)
            or not isinstance(raw_released, list)
        ):
            raise ActionChainEffectAdvanceConflict(
                "effect advance replay binding differs"
            )
        try:
            released = tuple(UUID(str(item)) for item in raw_released)
        except (TypeError, ValueError) as error:
            raise ActionChainEffectAdvanceConflict(
                "effect advance replay dependency is invalid"
            ) from error
        return AdvancedActionChainEffect(
            "already_advanced", effect_step_id, released, outcome_ref
        )


__all__ = [
    "ActionChainEffectAdvanceConflict",
    "AdvancedActionChainEffect",
    "PostgresActionChainEffectAdvancer",
]
