"""Atomic PostgreSQL admission and reload for bounded action chains."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import ValidationError
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    insert_initial_timeline_thread,
    operational_checkpoints,
    operational_events,
)
from eva.adapters.postgres.scope import set_local_scope
from eva.application.action_chain import (
    ActionChainValidationError,
    ActionToolCatalog,
    action_chain_step_operation_id,
    action_chain_thread_id,
    validate_admitted_action_chain,
    validate_action_chain_requests,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.execution import CapabilityManifest, ConnectorBinding, ReadIntent
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


class PostgresActionChainLedger:
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
            step.step_id: uuid5(plan_id, f"step:{step.step_id}")
            for step in plan.steps
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
                    model_decision_sha256=bytes.fromhex(
                        plan.model_decision_sha256
                    ),
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

    def _scope(self, connection: sa.Connection) -> None:
        connection.exec_driver_sql(f"SET LOCAL ROLE {self._role}")
        set_local_scope(
            connection,
            tenant_id=str(self._scope_value.tenant_id),
            installation_id=str(self._scope_value.installation_id),
        )

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
                model_attempts.c.installation_id
                == self._scope_value.installation_id,
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
        except ValidationError as error:
            raise ActionChainConflict("model attempt payload is invalid") from error
        if (
            attempt.episode_id != decision.episode_id
            or attempt.generation != decision.generation
            or attempt.purpose != "plan"
            or attempt.state != "responded"
            or attempt.response_sha256 != response_sha256
        ):
            raise ActionChainConflict("model attempt payload binding differs")

    def _load(
        self, connection: sa.Connection, plan_id: UUID
    ) -> StoredActionChain:
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
        except ValidationError as error:
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
            or bytes(row.model_decision_sha256).hex()
            != plan.model_decision_sha256
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
        except (KeyError, TypeError, ValidationError) as error:
            raise ActionChainCorrupt("action-chain admission decision is invalid") from error
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
        resolved_ids = {
            key: by_key[key].plan_step_id for key in by_key
        }
        for step in plan.steps:
            stored = by_key[step.step_id]
            step_payload = step.model_dump(mode="json")
            expected_dependencies = tuple(
                resolved_ids[item] for item in step.depends_on
            )
            initial_state = "ready" if not step.depends_on else "pending"
            prepared_read = (
                step.kind == "read"
                and stored.state == "running"
                and stored.read_intent_id is not None
                and stored.effect_intent_id is None
                and stored.outcome_ref is None
                and stored.revision == 2
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
                or not (initial_step or prepared_read)
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
            if prepared_read:
                self._validate_prepared_read(
                    connection,
                    plan_id=plan_id,
                    plan=plan,
                    decision=decision,
                    authority_snapshot=authority_snapshot,
                    plan_row=row,
                    step=step,
                    stored=stored,
                    current_event_data=step_current,
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
        if admission.get("step_ids") != expected_admission_ids:
            raise ActionChainCorrupt("admission step IDs differ")

        attention_id = uuid5(plan_id, "continuation:1")
        attention_row = connection.execute(
            sa.select(attention_items).where(
                attention_items.c.tenant_id == self._scope_value.tenant_id,
                attention_items.c.installation_id
                == self._scope_value.installation_id,
                attention_items.c.attention_id == attention_id,
            )
        ).one_or_none()
        if attention_row is None:
            raise ActionChainCorrupt("action-chain continuation attention is absent")
        try:
            attention = AttentionWake.model_validate(attention_row.payload)
        except ValidationError as error:
            raise ActionChainCorrupt("continuation attention payload is invalid") from error
        if (
            canonical_sha256(attention)
            != bytes(attention_row.payload_sha256).hex()
            or attention.attention_id != str(attention_id)
            or attention.episode_id != plan.episode_id
            or attention.generation != plan.generation
            or attention.kind != "continuation"
            or attention.state not in {"pending", "claimed", "waiting"}
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

    def _validate_prepared_read(
        self,
        connection: sa.Connection,
        *,
        plan_id: UUID,
        plan: ValidatedPlan,
        decision: ModelDecision,
        authority_snapshot: dict[str, Any],
        plan_row: sa.Row,
        step: Any,
        stored: sa.Row,
        current_event_data: dict[str, Any],
    ) -> None:
        intent_row = connection.execute(
            sa.select(read_intents).where(
                read_intents.c.tenant_id == self._scope_value.tenant_id,
                read_intents.c.installation_id == self._scope_value.installation_id,
                read_intents.c.read_intent_id == stored.read_intent_id,
            )
        ).one_or_none()
        if intent_row is None:
            raise ActionChainCorrupt("prepared read intent is absent")
        try:
            intent = ReadIntent.model_validate(intent_row.payload)
        except ValidationError as error:
            raise ActionChainCorrupt("prepared read intent is invalid") from error
        action = next(
            (item for item in decision.actions if item.action_id == step.step_id),
            None,
        )
        descriptor = next(
            (
                item
                for item in authority_snapshot.get("descriptors", [])
                if isinstance(item, dict) and item.get("operation") == step.operation
            ),
            None,
        )
        if action is None or descriptor is None:
            raise ActionChainCorrupt("prepared read authority is absent")
        expected_intent_id = uuid5(stored.plan_step_id, "read-intent:v1")
        expected_query_sha256 = canonical_sha256(action.arguments)
        expected_scope_payload = {
            "resource_type": intent.resource_scope.resource_type,
            "resource_id": intent.resource_scope.resource_id,
        }
        if (
            stored.read_intent_id != expected_intent_id
            or intent.read_intent_id != str(expected_intent_id)
            or intent.episode_id != plan.episode_id
            or intent.generation != plan.generation
            or intent.purpose != "business_read"
            or intent.effect_intent_id is not None
            or intent.operation != step.operation
            or intent.resource_scope.resource_id != action.resource
            or intent.resource_scope.scope_sha256
            != canonical_sha256(expected_scope_payload)
            or intent.query_sha256 != expected_query_sha256
            or intent.maximum_attempts != 2
            or intent.deadline != plan.deadline
            or canonical_sha256(intent) != bytes(intent_row.payload_sha256).hex()
            or intent_row.episode_id != plan_row.episode_id
            or int(intent_row.generation) != plan.generation
            or intent_row.logical_read_key
            != f"action-chain:{stored.plan_step_id}:read"
            or intent_row.plan_step_id != stored.plan_step_id
            or intent_row.purpose != intent.purpose
            or intent_row.effect_intent_id is not None
            or intent_row.circuit_breaker_id is not None
            or intent_row.operation != intent.operation
            or intent_row.resource_scope != intent.resource_scope.model_dump(mode="json")
            or bytes(intent_row.resource_scope_sha256).hex()
            != intent.resource_scope.scope_sha256
            or bytes(intent_row.query_sha256).hex() != expected_query_sha256
            or int(intent_row.maximum_freshness_ms) != intent.maximum_freshness_ms
            or int(intent_row.maximum_attempts) != intent.maximum_attempts
            or intent_row.capability_id != intent.capability_id
            or intent_row.deadline != _parse_timestamp(intent.deadline)
            or intent_row.retention_policy_id != plan_row.retention_policy_id
            or intent_row.retention_until != plan_row.retention_until
            or intent_row.created_at != stored.updated_at
        ):
            raise ActionChainCorrupt("prepared read intent binding differs")

        activation = connection.execute(
            sa.select(capability_activations).where(
                capability_activations.c.tenant_id == self._scope_value.tenant_id,
                capability_activations.c.installation_id
                == self._scope_value.installation_id,
                capability_activations.c.activation_id
                == intent_row.capability_activation_id,
            )
        ).one_or_none()
        manifest = (
            None
            if activation is None
            else connection.execute(
                sa.select(capability_manifests).where(
                    capability_manifests.c.tenant_id == self._scope_value.tenant_id,
                    capability_manifests.c.installation_id
                    == self._scope_value.installation_id,
                    capability_manifests.c.capability_manifest_id
                    == activation.capability_manifest_id,
                )
            ).one_or_none()
        )
        binding = (
            None
            if activation is None
            else connection.execute(
                sa.select(connector_bindings).where(
                    connector_bindings.c.tenant_id == self._scope_value.tenant_id,
                    connector_bindings.c.installation_id
                    == self._scope_value.installation_id,
                    connector_bindings.c.binding_id == activation.connector_binding_id,
                )
            ).one_or_none()
        )
        policy = (
            None
            if activation is None
            else connection.execute(
                sa.select(policy_pack_versions).where(
                    policy_pack_versions.c.tenant_id == self._scope_value.tenant_id,
                    policy_pack_versions.c.installation_id
                    == self._scope_value.installation_id,
                    policy_pack_versions.c.policy_id == activation.policy_id,
                    policy_pack_versions.c.policy_version
                    == activation.policy_pack_version,
                )
            ).one_or_none()
        )
        try:
            manifest_payload = CapabilityManifest.model_validate(
                None if manifest is None else manifest.payload
            )
            binding_payload = ConnectorBinding.model_validate(
                None if binding is None else binding.payload
            )
        except ValidationError as error:
            raise ActionChainCorrupt("prepared read capability payload is invalid") from error
        if (
            activation is None
            or manifest is None
            or binding is None
            or policy is None
            or activation.state != "active"
            or binding.status != "verified"
            or manifest.state != "active"
            or activation.capability_id != manifest.capability_id
            or activation.operation != manifest.operation
            or activation.operation != step.operation
            or intent.capability_id != activation.capability_id
            or activation.connector_binding_id != binding.binding_id
            or step.operation not in binding.confirmed_operations
            or intent.resource_scope.resource_type not in binding.confirmed_scopes
            or not set(manifest_payload.required_provider_scopes).issubset(
                binding.confirmed_scopes
            )
            or bytes(activation.policy_pack_sha256).hex() != plan.policy_sha256
            or bytes(policy.canonical_sha256).hex() != plan.policy_sha256
            or bytes(activation.executable_sha256)
            != bytes(manifest.executable_sha256)
            or bytes(activation.sandbox_profile_sha256)
            != bytes(manifest.sandbox_profile_sha256)
            or manifest_payload.operation != step.operation
            or manifest_payload.effect_class != "read"
            or manifest_payload.input_schema_sha256 != step.input_schema_sha256
            or manifest_payload.output_schema_sha256 != step.output_schema_sha256
            or manifest_payload.proof_floor != step.proof_floor
            or manifest_payload.maximum_freshness_ms
            != intent.maximum_freshness_ms
            or canonical_sha256(manifest_payload)
            != bytes(manifest.payload_sha256).hex()
            or binding_payload.status != "verified"
            or canonical_sha256(binding_payload) != bytes(binding.payload_sha256).hex()
        ):
            raise ActionChainCorrupt("prepared read capability binding differs")

        claim_sha256 = _prepared_claim_sha256(
            connection,
            self._scope_value,
            plan=plan,
            plan_id=plan_id,
            plan_step_id=stored.plan_step_id,
            logical_step_key=step.step_id,
        )
        expected_event_data = {
            "capability_activation_id": str(intent_row.capability_activation_id),
            "capability_manifest_id": str(activation.capability_manifest_id),
            "catalog_sha256": authority_snapshot["catalog_sha256"],
            "claim_token_sha256": claim_sha256,
            "connector_binding_id": str(activation.connector_binding_id),
            "logical_step_key": step.step_id,
            "plan_id": str(plan_id),
            "plan_step_id": str(stored.plan_step_id),
            "query_sha256": expected_query_sha256,
            "read_intent_id": str(expected_intent_id),
            "read_intent_payload_sha256": canonical_sha256(intent),
        }
        if (
            current_event_data != expected_event_data
        ):
            raise ActionChainCorrupt("prepared read timeline binding differs")


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


def _prepared_claim_sha256(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    plan: ValidatedPlan,
    plan_id: UUID,
    plan_step_id: UUID,
    logical_step_key: str,
) -> str:
    root_thread_id = action_chain_thread_id(plan)
    rows = connection.execute(
        sa.select(operational_events)
        .where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == root_thread_id,
        )
        .order_by(operational_events.c.sequence.desc())
        .limit(2)
    ).all()
    if (
        len(rows) != 2
        or rows[0].event_type != "ACTION_CHAIN_READ_INTENT_PREPARED"
        or rows[1].event_type != "ACTION_CHAIN_CONTINUATION_CLAIMED"
        or int(rows[0].sequence) != int(rows[1].sequence) + 1
    ):
        raise ActionChainCorrupt("prepared read lease timeline differs")
    for event in rows:
        if (
            event.operation_id != root_thread_id
            or canonical_sha256(event.payload) != bytes(event.payload_sha256).hex()
            or set(event.payload) != {"checkpoint", "data"}
        ):
            raise ActionChainCorrupt("prepared read lease event differs")
    claimed_data = rows[1].payload.get("data")
    if not isinstance(claimed_data, dict):
        raise ActionChainCorrupt("prepared read claim data is absent")
    attention_payload = claimed_data.get("attention")
    if not isinstance(attention_payload, dict):
        raise ActionChainCorrupt("prepared read claim attention is absent")
    try:
        attention = AttentionWake.model_validate(attention_payload)
    except ValidationError as error:
        raise ActionChainCorrupt("prepared read claim attention is invalid") from error
    expected_attention_id = uuid5(plan_id, "continuation:1")
    if (
        set(claimed_data)
        != {
            "attention",
            "attention_payload_sha256",
            "attention_revision",
            "plan_id",
        }
        or attention.attention_id != str(expected_attention_id)
        or attention.state != "claimed"
        or attention.lease_owner is None
        or attention.lease_expires_at is None
        or claimed_data.get("attention_payload_sha256")
        != canonical_sha256(attention)
        or claimed_data.get("attention_revision") != rows[1].sequence
        or claimed_data.get("plan_id") != str(plan_id)
    ):
        raise ActionChainCorrupt("prepared read claim binding differs")
    return canonical_sha256(
        {
            "attention_id": str(expected_attention_id),
            "attention_revision": claimed_data["attention_revision"],
            "lease_expires_at": attention.lease_expires_at,
            "logical_step_key": logical_step_key,
            "plan_id": str(plan_id),
            "plan_step_id": str(plan_step_id),
            "worker_id": attention.lease_owner,
        }
    )


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
        }
        if root
        else {
            "ACTION_CHAIN_STEP_ADMITTED",
            "ACTION_CHAIN_STEP_RETRY_FAILED",
            "ACTION_CHAIN_STEP_NEEDS_HELP",
            "ACTION_CHAIN_READ_INTENT_PREPARED",
        }
    )
    allowed_statuses = {"active", "needs_help"} if root else {
        "active",
        "failed",
        "needs_help",
        "waiting_for_proof",
    }
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


def _require_utc_second(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be timezone-aware UTC")
    if value.microsecond:
        raise ValueError(f"{field} must use whole-second precision")


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


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
