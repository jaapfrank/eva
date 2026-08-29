"""Atomic PostgreSQL admission and reload for bounded action chains."""

from __future__ import annotations
from eva.contracts.timestamps import parse_utc_timestamp as _parse_timestamp
from eva.contracts.timestamps import require_utc_second as _require_utc_second

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.action_chain_effect_validation import (
    is_cancelled_action_chain_effect,
    is_completed_action_chain_effect,
    is_prepared_action_chain_effect,
    validate_action_chain_effect,
)
from eva.adapters.postgres.action_chain_read_validation import (
    validate_action_chain_read,
)
from eva.adapters.postgres.action_chain_read_advance_validation import (
    validate_action_chain_read_advances,
)
from eva.adapters.postgres.action_chain_effect_progress_validation import (
    validate_action_chain_effect_advances,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    insert_initial_timeline_thread,
    operational_checkpoints,
    operational_events,
)
from eva.adapters.postgres.scope import PostgresScopedRole
from eva.application.action_chain import (
    ActionChainValidationError,
    ActionToolCatalog,
    action_chain_step_operation_id,
    action_chain_thread_id,
    validate_admitted_action_chain,
    validate_action_chain_requests,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.execution import EffectAttempt, EffectIntent
from eva.contracts.lifecycle import AttentionWake
from eva.contracts.planning import ModelAttempt, ModelDecision, ValidatedPlan

plans = metadata.tables["eva_core.plans"]
plan_steps = metadata.tables["eva_core.plan_steps"]
attention_items = metadata.tables["eva_core.attention_items"]
model_attempts = metadata.tables["eva_core.model_attempts"]
connector_bindings = metadata.tables["eva_core.connector_bindings"]
capability_manifests = metadata.tables["eva_core.capability_manifests"]
capability_activations = metadata.tables["eva_core.capability_activations"]
policy_pack_versions = metadata.tables["eva_core.policy_pack_versions"]
read_intents = metadata.tables["eva_effects.read_intents"]
effect_intents = metadata.tables["eva_effects.effect_intents"]
effect_attempts = metadata.tables["eva_effects.effect_attempts"]
effect_receipts = metadata.tables["eva_effects.effect_receipts"]
effect_readbacks = metadata.tables["eva_effects.effect_readbacks"]


class ActionChainLedgerError(RuntimeError):
    """Base failure for durable action-chain admission or reload."""


class ActionChainConflict(ActionChainLedgerError):
    """The episode generation already owns different durable plan bytes."""


class ActionChainNotFound(ActionChainLedgerError):
    """The requested scoped plan is absent."""


class ActionChainCorrupt(ActionChainLedgerError):
    """Stored rows no longer match their immutable admission authority."""


@dataclass(frozen=True, slots=True)
class StoredActionChainStep:
    logical_step_key: str
    plan_step_id: UUID
    dependency_step_ids: tuple[UUID, ...]
    state: str
    read_intent_id: UUID | None


@dataclass(frozen=True, slots=True)
class StoredActionChain:
    plan_id: UUID
    storage_episode_id: UUID
    model_attempt_id: UUID
    decision: ModelDecision
    plan: ValidatedPlan
    authority_snapshot: dict[str, Any]
    steps: tuple[StoredActionChainStep, ...]
    attention: AttentionWake
    retention_policy_id: str
    retention_until: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ActionChainAdmission:
    disposition: Literal["inserted", "already_present"]
    chain: StoredActionChain


class PostgresActionChainLedger(PostgresScopedRole):
    """Own one atomic admission transaction; execute no external capability."""

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        role: Literal["eva_runtime", "eva_dbos"] = "eva_runtime",
    ) -> None:
        if engine.url.get_backend_name() != "postgresql":
            raise ValueError("action-chain ledger requires PostgreSQL")
        self._engine = engine
        self._scope_value = scope
        self._role = role

    def admit(
        self,
        *,
        storage_episode_id: UUID,
        model_attempt_id: UUID,
        decision: ModelDecision,
        plan: ValidatedPlan,
        catalog: ActionToolCatalog,
        retention_policy_id: str,
        retention_until: datetime,
        occurred_at: datetime,
    ) -> ActionChainAdmission:
        authority_snapshot = catalog.authority_snapshot()
        try:
            validate_action_chain_requests(decision, catalog)
            catalog_sha256 = validate_admitted_action_chain(
                decision, plan, authority_snapshot
            )
        except ActionChainValidationError as error:
            raise ActionChainConflict(str(error)) from error
        _require_retention(
            retention_policy_id=retention_policy_id,
            retention_until=retention_until,
            occurred_at=occurred_at,
            deadline=_parse_timestamp(plan.deadline),
        )
        plan_sha256 = canonical_sha256(plan)
        plan_id = _plan_id(
            self._scope_value.installation_id,
            storage_episode_id,
            plan.generation,
            plan_sha256,
        )
        step_ids = {
            step.step_id: uuid5(plan_id, f"step:{step.step_id}") for step in plan.steps
        }

        with self._engine.begin() as connection:
            self._scope(connection)
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {
                    "key": (
                        f"action-chain:{self._scope_value.tenant_id}:"
                        f"{self._scope_value.installation_id}:{storage_episode_id}:"
                        f"{plan.generation}"
                    )
                },
            )
            existing_id = connection.scalar(
                sa.select(plans.c.plan_id).where(
                    plans.c.tenant_id == self._scope_value.tenant_id,
                    plans.c.installation_id == self._scope_value.installation_id,
                    plans.c.episode_id == storage_episode_id,
                    plans.c.generation == plan.generation,
                )
            )
            if existing_id is not None:
                chain = self._load(connection, existing_id)
                if (
                    chain.plan_id != plan_id
                    or chain.storage_episode_id != storage_episode_id
                    or chain.model_attempt_id != model_attempt_id
                    or chain.decision != decision
                    or chain.plan != plan
                    or chain.authority_snapshot != authority_snapshot
                    or chain.retention_policy_id != retention_policy_id
                    or chain.retention_until != retention_until
                ):
                    raise ActionChainConflict(
                        "episode generation already owns different action-chain bytes"
                    )
                return ActionChainAdmission("already_present", chain)

            self._validate_model_attempt(
                connection,
                storage_episode_id=storage_episode_id,
                generation=plan.generation,
                model_attempt_id=model_attempt_id,
                decision=decision,
            )
            connection.execute(
                sa.insert(plans).values(
                    tenant_id=self._scope_value.tenant_id,
                    installation_id=self._scope_value.installation_id,
                    plan_id=plan_id,
                    episode_id=storage_episode_id,
                    generation=plan.generation,
                    model_attempt_id=model_attempt_id,
                    state="current",
                    model_decision_sha256=bytes.fromhex(plan.model_decision_sha256),
                    policy_pack_sha256=bytes.fromhex(plan.policy_sha256),
                    capability_set_sha256=bytes.fromhex(catalog_sha256),
                    validated_plan_payload=plan.model_dump(mode="json"),
                    validated_plan_sha256=bytes.fromhex(plan_sha256),
                    revision=1,
                    retention_policy_id=retention_policy_id,
                    retention_until=retention_until,
                    deadline=_parse_timestamp(plan.deadline),
                    created_at=occurred_at,
                    updated_at=occurred_at,
                )
            )
            for step in plan.steps:
                step_payload = step.model_dump(mode="json")
                connection.execute(
                    sa.insert(plan_steps).values(
                        tenant_id=self._scope_value.tenant_id,
                        installation_id=self._scope_value.installation_id,
                        plan_step_id=step_ids[step.step_id],
                        plan_id=plan_id,
                        episode_id=storage_episode_id,
                        generation=plan.generation,
                        logical_step_key=step.step_id,
                        dependency_step_ids=[
                            step_ids[item] for item in step.depends_on
                        ],
                        operation=step.operation,
                        effect_class=step.kind,
                        state="ready" if not step.depends_on else "pending",
                        read_intent_id=None,
                        effect_intent_id=None,
                        outcome_ref=None,
                        step_definition=step_payload,
                        step_definition_sha256=bytes.fromhex(
                            canonical_sha256(step_payload)
                        ),
                        revision=1,
                        retention_policy_id=retention_policy_id,
                        retention_until=retention_until,
                        created_at=occurred_at,
                        updated_at=occurred_at,
                    )
                )

            root_thread_id = action_chain_thread_id(plan)
            admission_data = {
                "authority_snapshot": authority_snapshot,
                "catalog_sha256": catalog_sha256,
                "decision": decision.model_dump(mode="json"),
                "decision_sha256": canonical_sha256(decision),
                "model_attempt_id": str(model_attempt_id),
                "plan": plan.model_dump(mode="json"),
                "plan_id": str(plan_id),
                "plan_sha256": plan_sha256,
                "step_ids": [
                    {
                        "logical_step_key": step.step_id,
                        "plan_step_id": str(step_ids[step.step_id]),
                    }
                    for step in plan.steps
                ],
                "storage_episode_id": str(storage_episode_id),
            }
            insert_initial_timeline_thread(
                connection,
                self._scope_value,
                thread_id=root_thread_id,
                checkpoint_id=f"checkpoint:{root_thread_id}:admitted",
                occurred_at=occurred_at,
                data=admission_data,
                event_type="ACTION_CHAIN_ADMITTED",
                phase="action_chain_admitted",
                status="active",
                operation_id=root_thread_id,
                deadline=_parse_timestamp(plan.deadline),
            )
            for step in plan.steps:
                operation_id = action_chain_step_operation_id(plan, step.step_id)
                insert_initial_timeline_thread(
                    connection,
                    self._scope_value,
                    thread_id=operation_id,
                    checkpoint_id=f"checkpoint:{operation_id}:admitted",
                    occurred_at=occurred_at,
                    data={
                        "logical_step_key": step.step_id,
                        "plan_id": str(plan_id),
                        "plan_sha256": plan_sha256,
                        "plan_step_id": str(step_ids[step.step_id]),
                        "step_definition_sha256": canonical_sha256(step),
                    },
                    event_type="ACTION_CHAIN_STEP_ADMITTED",
                    phase="action_chain_step_admitted",
                    status="active",
                    operation_id=operation_id,
                    deadline=_parse_timestamp(plan.deadline),
                )

            attention_id = uuid5(plan_id, "continuation:1")
            attention = AttentionWake(
                schema_version=1,
                contract="AttentionWake/v1",
                attention_id=str(attention_id),
                episode_id=plan.episode_id,
                generation=plan.generation,
                kind="continuation",
                state="pending",
                reason="hervat het gevalideerde meerstapsplan",
                not_before=_format_timestamp(occurred_at),
                deadline=plan.deadline,
                lease_owner=None,
                lease_expires_at=None,
                ordinal=1,
            )
            attention_payload = attention.model_dump(mode="json")
            connection.execute(
                sa.insert(attention_items).values(
                    tenant_id=self._scope_value.tenant_id,
                    installation_id=self._scope_value.installation_id,
                    attention_id=attention_id,
                    episode_id=storage_episode_id,
                    generation=plan.generation,
                    commitment_id=None,
                    kind=attention.kind,
                    state=attention.state,
                    reason=attention.reason,
                    not_before=occurred_at,
                    deadline=_parse_timestamp(plan.deadline),
                    lease_owner=None,
                    lease_expires_at=None,
                    ordinal=attention.ordinal,
                    payload=attention_payload,
                    payload_sha256=bytes.fromhex(canonical_sha256(attention)),
                    revision=1,
                    retention_policy_id=retention_policy_id,
                    retention_until=retention_until,
                    created_at=occurred_at,
                    updated_at=occurred_at,
                )
            )
            chain = self._load(connection, plan_id)
        return ActionChainAdmission("inserted", chain)

    def load(self, plan_id: UUID) -> StoredActionChain:
        with self._engine.connect() as connection:
            transaction = connection.begin()
            try:
                self._scope(connection)
                return self._load(connection, plan_id)
            finally:
                transaction.rollback()

    def load_in_transaction(
        self, connection: sa.Connection, plan_id: UUID
    ) -> StoredActionChain:
        """Reload authority while a caller owns the root transaction and locks."""

        if not connection.in_transaction() or connection.in_nested_transaction():
            raise ActionChainConflict("action-chain reload requires a root transaction")
        return self._load(connection, plan_id)

    def _validate_model_attempt(
        self,
        connection: sa.Connection,
        *,
        storage_episode_id: UUID,
        generation: int,
        model_attempt_id: UUID,
        decision: ModelDecision,
    ) -> None:
        row = connection.execute(
            sa.select(model_attempts).where(
                model_attempts.c.tenant_id == self._scope_value.tenant_id,
                model_attempts.c.installation_id == self._scope_value.installation_id,
                model_attempts.c.attempt_id == model_attempt_id,
            )
        ).one_or_none()
        if row is None:
            raise ActionChainConflict("responded plan model attempt does not exist")
        response_sha256 = canonical_sha256(decision)
        if (
            row.episode_id != storage_episode_id
            or int(row.generation) != generation
            or row.purpose != "plan"
            or row.state != "responded"
            or row.response_sha256 is None
            or bytes(row.response_sha256).hex() != response_sha256
            or canonical_sha256(row.payload) != bytes(row.payload_sha256).hex()
        ):
            raise ActionChainConflict("model attempt authority differs")
        try:
            attempt = ModelAttempt.model_validate(row.payload)
        except ValueError as error:
            raise ActionChainConflict("model attempt payload is invalid") from error
        if (
            attempt.episode_id != decision.episode_id
            or attempt.generation != decision.generation
            or attempt.purpose != "plan"
            or attempt.state != "responded"
            or attempt.response_sha256 != response_sha256
        ):
            raise ActionChainConflict("model attempt payload binding differs")

    def _load(self, connection: sa.Connection, plan_id: UUID) -> StoredActionChain:
        row = connection.execute(
            sa.select(plans).where(
                plans.c.tenant_id == self._scope_value.tenant_id,
                plans.c.installation_id == self._scope_value.installation_id,
                plans.c.plan_id == plan_id,
            )
        ).one_or_none()
        if row is None:
            raise ActionChainNotFound("action chain does not exist")
        try:
            plan = ValidatedPlan.model_validate(row.validated_plan_payload)
        except ValueError as error:
            raise ActionChainCorrupt("stored validated plan is invalid") from error
        plan_sha256 = canonical_sha256(plan)
        if (
            plan_id
            != _plan_id(
                self._scope_value.installation_id,
                row.episode_id,
                plan.generation,
                plan_sha256,
            )
            or bytes(row.validated_plan_sha256).hex() != plan_sha256
            or bytes(row.model_decision_sha256).hex() != plan.model_decision_sha256
            or bytes(row.policy_pack_sha256).hex() != plan.policy_sha256
            or row.generation != plan.generation
            or row.deadline != _parse_timestamp(plan.deadline)
            or row.state != "current"
            or row.revision != 1
            or row.updated_at != row.created_at
        ):
            raise ActionChainCorrupt("stored plan columns differ from its payload")

        root_thread_id = action_chain_thread_id(plan)
        admission, root_current = _origin_data(
            connection,
            self._scope_value,
            thread_id=root_thread_id,
            event_type="ACTION_CHAIN_ADMITTED",
            operation_id=root_thread_id,
            phase="action_chain_admitted",
            deadline=plan.deadline,
            occurred_at=row.created_at,
        )
        if set(admission) != {
            "authority_snapshot",
            "catalog_sha256",
            "decision",
            "decision_sha256",
            "model_attempt_id",
            "plan",
            "plan_id",
            "plan_sha256",
            "step_ids",
            "storage_episode_id",
        }:
            raise ActionChainCorrupt("action-chain admission fields differ")
        try:
            decision = ModelDecision.model_validate(admission["decision"])
        except (KeyError, TypeError, ValueError) as error:
            raise ActionChainCorrupt(
                "action-chain admission decision is invalid"
            ) from error
        authority_snapshot = admission.get("authority_snapshot")
        if not isinstance(authority_snapshot, dict):
            raise ActionChainCorrupt("action-chain authority snapshot is absent")
        try:
            self._validate_model_attempt(
                connection,
                storage_episode_id=row.episode_id,
                generation=plan.generation,
                model_attempt_id=row.model_attempt_id,
                decision=decision,
            )
        except ActionChainConflict as error:
            raise ActionChainCorrupt(str(error)) from error
        try:
            catalog_sha256 = validate_admitted_action_chain(
                decision, plan, authority_snapshot
            )
        except ActionChainValidationError as error:
            raise ActionChainCorrupt(str(error)) from error
        if (
            admission.get("catalog_sha256") != catalog_sha256
            or admission.get("decision_sha256") != canonical_sha256(decision)
            or admission.get("model_attempt_id") != str(row.model_attempt_id)
            or admission.get("plan") != plan.model_dump(mode="json")
            or admission.get("plan_id") != str(plan_id)
            or admission.get("plan_sha256") != plan_sha256
            or admission.get("storage_episode_id") != str(row.episode_id)
            or bytes(row.capability_set_sha256).hex() != catalog_sha256
        ):
            raise ActionChainCorrupt("action-chain admission bindings differ")

        step_rows = connection.execute(
            sa.select(plan_steps)
            .where(
                plan_steps.c.tenant_id == self._scope_value.tenant_id,
                plan_steps.c.installation_id == self._scope_value.installation_id,
                plan_steps.c.plan_id == plan_id,
            )
            .order_by(plan_steps.c.created_at, plan_steps.c.logical_step_key)
        ).all()
        by_key = {stored.logical_step_key: stored for stored in step_rows}
        if len(by_key) != len(step_rows) or set(by_key) != {
            step.step_id for step in plan.steps
        }:
            raise ActionChainCorrupt("stored plan-step membership differs")
        stored_steps: list[StoredActionChainStep] = []
        expected_admission_ids: list[dict[str, str]] = []
        resolved_ids = {key: by_key[key].plan_step_id for key in by_key}
        for step in plan.steps:
            stored = by_key[step.step_id]
            step_payload = step.model_dump(mode="json")
            expected_dependencies = tuple(
                resolved_ids[item] for item in step.depends_on
            )
            initial_state = "ready" if not step.depends_on else "pending"
            ready_revision = 1 if not expected_dependencies else 2
            running_revision = ready_revision + 1
            succeeded_revision = running_revision + 1
            released_ready = (
                bool(expected_dependencies)
                and stored.state == "ready"
                and stored.read_intent_id is None
                and stored.effect_intent_id is None
                and stored.outcome_ref is None
                and stored.revision == ready_revision
                and stored.updated_at >= stored.created_at
            )
            prepared_read = (
                step.kind == "read"
                and stored.state == "running"
                and stored.read_intent_id is not None
                and stored.effect_intent_id is None
                and stored.outcome_ref is None
                and stored.revision == running_revision
                and stored.updated_at >= stored.created_at
            )
            prepared_effect = is_prepared_action_chain_effect(
                step, stored, running_revision
            )
            completed_effect = is_completed_action_chain_effect(
                step, stored, succeeded_revision
            )
            cancelled_effect = is_cancelled_action_chain_effect(
                step, stored, succeeded_revision
            )
            completed_read = (
                step.kind == "read"
                and stored.state == "succeeded"
                and stored.read_intent_id is not None
                and stored.effect_intent_id is None
                and isinstance(stored.outcome_ref, str)
                and stored.outcome_ref.startswith("read-observation:")
                and stored.revision == succeeded_revision
                and stored.updated_at >= stored.created_at
            )
            initial_step = (
                stored.state == initial_state
                and stored.read_intent_id is None
                and stored.effect_intent_id is None
                and stored.outcome_ref is None
                and stored.updated_at == stored.created_at
                and stored.revision == 1
            )
            if (
                stored.episode_id != row.episode_id
                or stored.generation != plan.generation
                or stored.operation != step.operation
                or stored.effect_class != step.kind
                or stored.plan_step_id != uuid5(plan_id, f"step:{step.step_id}")
                or not (
                    initial_step
                    or released_ready
                    or prepared_read
                    or completed_read
                    or prepared_effect
                    or completed_effect
                    or cancelled_effect
                )
                or stored.step_definition != step_payload
                or bytes(stored.step_definition_sha256).hex()
                != canonical_sha256(step_payload)
                or tuple(stored.dependency_step_ids) != expected_dependencies
                or stored.retention_policy_id != row.retention_policy_id
                or stored.retention_until != row.retention_until
                or stored.created_at != row.created_at
            ):
                raise ActionChainCorrupt("stored plan-step binding differs")
            operation_id = action_chain_step_operation_id(plan, step.step_id)
            step_admission, step_current = _origin_data(
                connection,
                self._scope_value,
                thread_id=operation_id,
                event_type="ACTION_CHAIN_STEP_ADMITTED",
                operation_id=operation_id,
                phase="action_chain_step_admitted",
                deadline=plan.deadline,
                occurred_at=row.created_at,
            )
            expected_step_admission = {
                "logical_step_key": step.step_id,
                "plan_id": str(plan_id),
                "plan_sha256": plan_sha256,
                "plan_step_id": str(stored.plan_step_id),
                "step_definition_sha256": canonical_sha256(step),
            }
            if step_admission != expected_step_admission:
                raise ActionChainCorrupt("step admission event differs")
            if prepared_read or completed_read:
                validate_action_chain_read(
                    connection,
                    self._scope_value,
                    plan_id=plan_id,
                    plan=plan,
                    decision=decision,
                    authority_snapshot=authority_snapshot,
                    plan_row=row,
                    step=step,
                    stored=stored,
                    current_event_data=step_current,
                    corrupt=ActionChainCorrupt,
                )
            elif prepared_effect or completed_effect or cancelled_effect:
                validate_action_chain_effect(
                    connection,
                    self._scope_value,
                    plan_id,
                    plan,
                    decision,
                    authority_snapshot,
                    row,
                    step,
                    stored,
                    step_current,
                    ActionChainCorrupt,
                )
            expected_admission_ids.append(
                {
                    "logical_step_key": step.step_id,
                    "plan_step_id": str(stored.plan_step_id),
                }
            )
            stored_steps.append(
                StoredActionChainStep(
                    logical_step_key=step.step_id,
                    plan_step_id=stored.plan_step_id,
                    dependency_step_ids=expected_dependencies,
                    state=str(stored.state),
                    read_intent_id=stored.read_intent_id,
                )
            )
        effect_transition_context, effect_released_step_ids = (
            validate_action_chain_effect_advances(
                connection,
                self._scope_value,
                plan_id=plan_id,
                plan=plan,
                step_rows=step_rows,
                root_current=root_current,
                corrupt=ActionChainCorrupt,
            )
        )
        read_transition_context = validate_action_chain_read_advances(
            connection,
            self._scope_value,
            plan_id=plan_id,
            plan=plan,
            step_rows=step_rows,
            root_current=root_current,
            effect_released_step_ids=effect_released_step_ids,
            corrupt=ActionChainCorrupt,
        )
        normal_transition_context = effect_transition_context or read_transition_context
        current_states = {row.plan_step_id: row.state for row in step_rows}
        if any(
            row.state == "pending"
            and row.dependency_step_ids
            and all(
                current_states.get(dependency) == "succeeded"
                for dependency in row.dependency_step_ids
            )
            for row in step_rows
        ):
            raise ActionChainCorrupt("eligible dependent step was not released")
        if admission.get("step_ids") != expected_admission_ids:
            raise ActionChainCorrupt("admission step IDs differ")

        attention_id = uuid5(plan_id, "continuation:1")
        attention_row = connection.execute(
            sa.select(attention_items).where(
                attention_items.c.tenant_id == self._scope_value.tenant_id,
                attention_items.c.installation_id == self._scope_value.installation_id,
                attention_items.c.attention_id == attention_id,
            )
        ).one_or_none()
        if attention_row is None:
            raise ActionChainCorrupt("action-chain continuation attention is absent")
        try:
            attention = AttentionWake.model_validate(attention_row.payload)
        except ValueError as error:
            raise ActionChainCorrupt(
                "continuation attention payload is invalid"
            ) from error
        if (
            canonical_sha256(attention) != bytes(attention_row.payload_sha256).hex()
            or attention.attention_id != str(attention_id)
            or attention.episode_id != plan.episode_id
            or attention.generation != plan.generation
            or attention.kind != "continuation"
            or attention.state not in {"pending", "claimed", "waiting", "completed"}
            or attention_row.episode_id != row.episode_id
            or attention_row.generation != plan.generation
            or attention_row.kind != attention.kind
            or attention_row.state != attention.state
            or attention_row.reason != attention.reason
            or attention_row.not_before != _parse_timestamp(attention.not_before)
            or attention_row.deadline != _parse_timestamp(attention.deadline)
            or attention_row.lease_owner != attention.lease_owner
            or attention_row.lease_expires_at
            != (
                None
                if attention.lease_expires_at is None
                else _parse_timestamp(attention.lease_expires_at)
            )
            or attention_row.ordinal != attention.ordinal
            or attention_row.commitment_id is not None
            or attention_row.retention_policy_id != row.retention_policy_id
            or attention_row.retention_until != row.retention_until
            or attention_row.created_at != row.created_at
            or attention_row.updated_at < attention_row.created_at
            or attention_row.revision < 1
        ):
            raise ActionChainCorrupt("continuation attention binding differs")
        cancellation_context = _validate_action_chain_effect_cancellation(
            connection,
            self._scope_value,
            plan_id=plan_id,
            plan=plan,
            step_rows=step_rows,
            attention=attention,
            attention_revision=int(attention_row.revision),
            root_current=root_current,
            corrupt=ActionChainCorrupt,
        )
        if normal_transition_context is not None and cancellation_context is not None:
            raise ActionChainCorrupt("cancellation overlaps a completion transition")
        current_transition_context = normal_transition_context or cancellation_context
        if attention_row.revision == 1:
            if (
                attention.state != "pending"
                or attention.lease_owner is not None
                or attention.lease_expires_at is not None
                or attention_row.updated_at != attention_row.created_at
            ):
                raise ActionChainCorrupt("initial continuation attention differs")
        else:
            expected_attention_projection = {
                "attention": attention.model_dump(mode="json"),
                "attention_payload_sha256": canonical_sha256(attention),
                "attention_revision": int(attention_row.revision),
                "plan_id": str(plan_id),
            }
            if current_transition_context is not None:
                expected_attention_projection["transition_context"] = (
                    current_transition_context
                )
            if root_current != expected_attention_projection:
                raise ActionChainCorrupt(
                    "continuation attention differs from its timeline event"
                )

        return StoredActionChain(
            plan_id=plan_id,
            storage_episode_id=row.episode_id,
            model_attempt_id=row.model_attempt_id,
            decision=decision,
            plan=plan,
            authority_snapshot=authority_snapshot,
            steps=tuple(stored_steps),
            attention=attention,
            retention_policy_id=str(row.retention_policy_id),
            retention_until=row.retention_until,
            created_at=row.created_at,
        )


class PostgresActionChainStore(PostgresScopedRole):
    """Share PostgreSQL, scope, role, and ledger setup across chain stores."""

    _postgres_error: str

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        role: Literal["eva_runtime", "eva_dbos"] = "eva_runtime",
    ) -> None:
        if engine.url.get_backend_name() != "postgresql":
            raise ValueError(
                self._postgres_error.format(
                    result_kind=getattr(self, "_result_kind", "")
                )
            )
        self._engine = engine
        self._scope_value = scope
        self._role = role
        self._ledger = PostgresActionChainLedger(engine, scope, role=role)


def _origin_data(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    thread_id: str,
    event_type: str,
    operation_id: str,
    phase: str,
    deadline: str,
    occurred_at: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    event = connection.execute(
        sa.select(operational_events).where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == thread_id,
            operational_events.c.sequence == 1,
        )
    ).one_or_none()
    if (
        event is None
        or event.event_type != event_type
        or event.operation_id != operation_id
        or event.occurred_at != occurred_at
        or canonical_sha256(event.payload) != bytes(event.payload_sha256).hex()
    ):
        raise ActionChainCorrupt("action-chain timeline origin differs")
    expected_checkpoint = {
        "checkpoint_id": f"checkpoint:{thread_id}:admitted",
        "deadline": deadline,
        "last_error_fingerprint": None,
        "operation_id": operation_id,
        "phase": phase,
        "retry_count": 0,
        "revision": 1,
        "status": "active",
        "updated_at": _format_timestamp(occurred_at),
    }
    if (
        event.checkpoint_id != expected_checkpoint["checkpoint_id"]
        or set(event.payload) != {"checkpoint", "data"}
        or event.payload.get("checkpoint") != expected_checkpoint
    ):
        raise ActionChainCorrupt("action-chain timeline checkpoint differs")
    data = event.payload.get("data")
    if not isinstance(data, dict):
        raise ActionChainCorrupt("action-chain timeline origin lacks data")
    current = _current_timeline_data(
        connection,
        scope,
        thread_id=thread_id,
        operation_id=operation_id,
        deadline=deadline,
        root=event_type == "ACTION_CHAIN_ADMITTED",
    )
    return data, current


def _current_timeline_data(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    thread_id: str,
    operation_id: str,
    deadline: str,
    root: bool,
) -> dict[str, Any]:
    checkpoint = connection.execute(
        sa.select(operational_checkpoints).where(
            operational_checkpoints.c.tenant_id == scope.tenant_id,
            operational_checkpoints.c.installation_id == scope.installation_id,
            operational_checkpoints.c.thread_id == thread_id,
        )
    ).one_or_none()
    latest = connection.execute(
        sa.select(operational_events)
        .where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == thread_id,
        )
        .order_by(operational_events.c.sequence.desc())
        .limit(1)
    ).one_or_none()
    if checkpoint is None or latest is None:
        raise ActionChainCorrupt("action-chain current timeline is absent")
    allowed_event_types = (
        {
            "ACTION_CHAIN_ADMITTED",
            "ACTION_CHAIN_CONTINUATION_CLAIMED",
            "ACTION_CHAIN_CONTINUATION_RELEASED",
            "ACTION_CHAIN_CONTINUATION_NEEDS_HELP",
            "ACTION_CHAIN_READ_INTENT_PREPARED",
            "ACTION_CHAIN_READ_DEPENDENCIES_RELEASED",
            "ACTION_CHAIN_EFFECT_INTENT_PREPARED",
            "ACTION_CHAIN_EFFECT_DEPENDENCIES_RELEASED",
            "ACTION_CHAIN_EFFECT_CANCELLED",
        }
        if root
        else {
            "ACTION_CHAIN_STEP_ADMITTED",
            "ACTION_CHAIN_STEP_RETRY_FAILED",
            "ACTION_CHAIN_STEP_NEEDS_HELP",
            "ACTION_CHAIN_READ_INTENT_PREPARED",
            "ACTION_CHAIN_READ_DISPATCH_AUTHORIZED",
            "ACTION_CHAIN_READ_PROVIDER_CALL_STARTED",
            "ACTION_CHAIN_READ_OBSERVED",
            "ACTION_CHAIN_READ_AMBIGUOUS",
            "ACTION_CHAIN_READ_REJECTED",
            "ACTION_CHAIN_READ_RATE_LIMITED",
            "ACTION_CHAIN_EFFECT_INTENT_PREPARED",
            "ACTION_CHAIN_EFFECT_CANCELLED",
            "ACTION_CHAIN_EFFECT_DISPATCH_AUTHORIZED",
            "ACTION_CHAIN_EFFECT_PROVIDER_CALL_STARTED",
            "ACTION_CHAIN_EFFECT_PROVIDER_ACCEPTED",
            "ACTION_CHAIN_EFFECT_REJECTED",
            "ACTION_CHAIN_EFFECT_AMBIGUOUS",
            "ACTION_CHAIN_EFFECT_RATE_LIMITED",
            "ACTION_CHAIN_EFFECT_BLOCKED",
            "STATE_VERIFIED",
        }
    )
    allowed_statuses = (
        {"active", "completed", "needs_help"}
        if root
        else {"active", "completed", "failed", "needs_help", "waiting_for_proof"}
    )
    expected_checkpoint = {
        "checkpoint_id": str(checkpoint.checkpoint_id),
        "deadline": deadline,
        "last_error_fingerprint": (
            None
            if checkpoint.last_error_fingerprint is None
            else bytes(checkpoint.last_error_fingerprint).hex()
        ),
        "operation_id": operation_id,
        "phase": str(checkpoint.phase),
        "retry_count": int(checkpoint.retry_count),
        "revision": int(checkpoint.revision),
        "status": str(checkpoint.status),
        "updated_at": _format_timestamp(checkpoint.updated_at),
    }
    if (
        latest.event_type not in allowed_event_types
        or latest.operation_id != operation_id
        or latest.sequence != checkpoint.revision
        or latest.checkpoint_id != checkpoint.checkpoint_id
        or latest.occurred_at != checkpoint.updated_at
        or checkpoint.operation_id != operation_id
        or checkpoint.deadline != _parse_timestamp(deadline)
        or checkpoint.status not in allowed_statuses
        or (root and int(checkpoint.retry_count) != 0)
        or canonical_sha256(latest.payload) != bytes(latest.payload_sha256).hex()
        or set(latest.payload) != {"checkpoint", "data"}
        or latest.payload.get("checkpoint") != expected_checkpoint
    ):
        raise ActionChainCorrupt("action-chain current timeline differs")
    data = latest.payload.get("data")
    if not isinstance(data, dict):
        raise ActionChainCorrupt("action-chain current timeline lacks data")
    return data


def _validate_action_chain_effect_cancellation(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    plan_id: UUID,
    plan: ValidatedPlan,
    step_rows: list[sa.Row],
    attention: AttentionWake,
    attention_revision: int,
    root_current: dict[str, Any],
    corrupt: type[RuntimeError],
) -> dict[str, Any] | None:
    """Accept exactly one owner-bound cancellation before provider I/O."""

    cancelled_rows = [row for row in step_rows if row.state == "cancelled"]
    if not cancelled_rows:
        return None
    if (
        len(cancelled_rows) != 1
        or any(
            row.plan_step_id != cancelled_rows[0].plan_step_id
            and row.state != "succeeded"
            for row in step_rows
        )
        or attention.state != "completed"
        or attention.lease_owner is not None
        or attention.lease_expires_at is not None
    ):
        raise corrupt("cancelled action-chain projection differs")
    step_row = cancelled_rows[0]
    step = next(
        (item for item in plan.steps if item.step_id == step_row.logical_step_key),
        None,
    )
    if (
        step is None
        or step.kind != "effect"
        or step_row.effect_intent_id is None
        or step_row.read_intent_id is not None
        or step_row.outcome_ref is not None
    ):
        raise corrupt("cancelled action-chain effect binding differs")
    intent_row = connection.execute(
        sa.select(effect_intents).where(
            effect_intents.c.tenant_id == scope.tenant_id,
            effect_intents.c.installation_id == scope.installation_id,
            effect_intents.c.effect_intent_id == step_row.effect_intent_id,
        )
    ).one_or_none()
    if intent_row is None:
        raise corrupt("cancelled action-chain effect intent is absent")
    try:
        intent = EffectIntent.model_validate(intent_row.payload)
    except ValueError as error:
        raise corrupt("cancelled action-chain effect payload is invalid") from error
    operation_id = action_chain_step_operation_id(plan, step.step_id)
    child = _latest_timeline_event(connection, scope, operation_id, corrupt)
    root_operation_id = action_chain_thread_id(plan)
    root = _latest_timeline_event(connection, scope, root_operation_id, corrupt)
    child_context = child.payload.get("data")
    if not isinstance(child_context, dict):
        raise corrupt("cancelled action-chain effect lacks event data")
    expected_keys = {
        "binding_verification_evidence_id",
        "binding_subject_sha256",
        "binding_revision",
        "binding_verified_at",
        "cancellation_stage",
        "contract",
        "effect_attempt_id",
        "effect_intent_id",
        "effect_intent_payload_sha256",
        "logical_step_key",
        "owner_principal_id",
        "plan_id",
        "plan_step_id",
        "principal_binding_id",
    }
    if (
        set(child_context) != expected_keys
        or child_context.get("contract") != "ActionChainEffectCancellation/v1"
        or not _is_sha256(child_context.get("binding_subject_sha256"))
        or not isinstance(child_context.get("binding_revision"), int)
        or child_context["binding_revision"] < 1
        or child_context.get("effect_intent_id") != str(step_row.effect_intent_id)
        or child_context.get("effect_intent_payload_sha256") != canonical_sha256(intent)
        or child_context.get("logical_step_key") != step.step_id
        or child_context.get("plan_id") != str(plan_id)
        or child_context.get("plan_step_id") != str(step_row.plan_step_id)
        or child.event_type != "ACTION_CHAIN_EFFECT_CANCELLED"
        or child.payload.get("checkpoint", {}).get("phase")
        != "action_chain_effect_cancelled"
        or child.payload.get("checkpoint", {}).get("status") != "completed"
        or root.event_type != "ACTION_CHAIN_EFFECT_CANCELLED"
        or root.payload.get("checkpoint", {}).get("phase")
        != "action_chain_continuation"
        or root.payload.get("checkpoint", {}).get("status") != "completed"
    ):
        raise corrupt("cancelled action-chain event context differs")
    try:
        UUID(str(child_context["owner_principal_id"]))
        UUID(str(child_context["principal_binding_id"]))
        UUID(str(child_context["binding_verification_evidence_id"]))
        _parse_timestamp(str(child_context["binding_verified_at"]))
    except (TypeError, ValueError) as error:
        raise corrupt("cancelled action-chain owner context is invalid") from error
    provider_call_count = connection.scalar(
        sa.select(sa.func.count())
        .select_from(operational_events)
        .where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == operation_id,
            operational_events.c.event_type
            == "ACTION_CHAIN_EFFECT_PROVIDER_CALL_STARTED",
        )
    )
    attempts = connection.execute(
        sa.select(effect_attempts)
        .where(
            effect_attempts.c.tenant_id == scope.tenant_id,
            effect_attempts.c.installation_id == scope.installation_id,
            effect_attempts.c.effect_intent_id == step_row.effect_intent_id,
        )
        .order_by(effect_attempts.c.ordinal)
    ).all()
    receipt_and_readback_count = sum(
        int(
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(table)
                .where(
                    table.c.tenant_id == scope.tenant_id,
                    table.c.installation_id == scope.installation_id,
                    table.c.effect_intent_id == step_row.effect_intent_id,
                )
            )
        )
        for table in (effect_receipts, effect_readbacks)
    )
    prepared_cancellation = (
        child_context.get("cancellation_stage") == "prepared"
        and child_context.get("effect_attempt_id") is None
        and not attempts
        and int(intent_row.revision) == 2
    )
    authorized_cancellation = False
    if (
        child_context.get("cancellation_stage") == "dispatch_authorized"
        and len(attempts) == 1
        and child_context.get("effect_attempt_id") == str(attempts[0].effect_attempt_id)
        and int(intent_row.revision) == 3
    ):
        try:
            authorized_attempt = EffectAttempt.model_validate(attempts[0].payload)
        except ValueError as error:
            raise corrupt("cancelled action-chain attempt is invalid") from error
        authorized_cancellation = (
            authorized_attempt.state == "dispatch_authorized"
            and attempts[0].state == "dispatch_authorized"
            and int(attempts[0].revision) == 1
            and attempts[0].response_sha256 is None
            and attempts[0].transport_evidence_id is None
        )
    expected_root = {
        "attention": attention.model_dump(mode="json"),
        "attention_payload_sha256": canonical_sha256(attention),
        "attention_revision": attention_revision,
        "plan_id": str(plan_id),
        "transition_context": child_context,
    }
    if (
        int(provider_call_count)
        or receipt_and_readback_count
        or not (prepared_cancellation or authorized_cancellation)
        or root_current != expected_root
    ):
        raise corrupt("cancelled action-chain authority or history differs")
    return child_context


def _latest_timeline_event(
    connection: sa.Connection,
    scope: TimelineScope,
    thread_id: str,
    corrupt: type[RuntimeError],
) -> sa.Row:
    event = connection.execute(
        sa.select(operational_events)
        .where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == thread_id,
        )
        .order_by(operational_events.c.sequence.desc())
        .limit(1)
    ).one_or_none()
    if (
        event is None
        or canonical_sha256(event.payload) != bytes(event.payload_sha256).hex()
        or set(event.payload) != {"checkpoint", "data"}
    ):
        raise corrupt("cancelled action-chain timeline event differs")
    return event


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True


def _plan_id(
    installation_id: UUID,
    storage_episode_id: UUID,
    generation: int,
    plan_sha256: str,
) -> UUID:
    return uuid5(
        installation_id,
        f"action-plan:{storage_episode_id}:{generation}:{plan_sha256}",
    )


def _require_retention(
    *,
    retention_policy_id: str,
    retention_until: datetime,
    occurred_at: datetime,
    deadline: datetime,
) -> None:
    if not retention_policy_id or len(retention_policy_id) > 192:
        raise ValueError("retention policy ID is invalid")
    _require_utc_second(occurred_at, "occurred_at")
    _require_utc_second(retention_until, "retention_until")
    if deadline < occurred_at:
        raise ValueError("action-chain deadline precedes admission")
    if retention_until < deadline:
        raise ValueError("retention ends before the action-chain deadline")


def _format_timestamp(value: datetime) -> str:
    _require_utc_second(value, "timestamp")
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "ActionChainAdmission",
    "ActionChainConflict",
    "ActionChainCorrupt",
    "ActionChainLedgerError",
    "ActionChainNotFound",
    "PostgresActionChainLedger",
    "StoredActionChain",
    "StoredActionChainStep",
    "capability_activations",
    "capability_manifests",
    "connector_bindings",
    "read_intents",
]
