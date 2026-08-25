"""Prepare one durable action-chain read intent without dispatching a provider."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid5

from pydantic import ValidationError
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.action_chain_leases import (
    ActionChainClaim,
    _locked_attention,
    write_action_chain_attention,
)
from eva.adapters.postgres.action_chain_ledger import (
    PostgresActionChainLedger,
    StoredActionChain,
    capability_activations,
    capability_manifests,
    connector_bindings,
    plan_steps,
    policy_pack_versions,
    read_intents,
)
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_checkpoints,
    operational_events,
    transition_timeline_thread,
)
from eva.adapters.postgres.scope import set_local_scope
from eva.application.action_chain import (
    ActionChainValidationError,
    ActionToolCatalog,
    action_chain_step_operation_id,
    validate_action_chain_requests,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.execution import (
    CapabilityManifest,
    ConnectorBinding,
    ReadIntent,
    ResourceScope,
)


class ActionChainReadError(RuntimeError):
    """Base failure for durable action-chain read preparation."""


class ActionChainReadConflict(ActionChainReadError):
    """The lease, catalog, step, or concrete read authority no longer matches."""


@dataclass(frozen=True, slots=True)
class ReadStepAuthority:
    """Host-resolved concrete activation and resource; never model-owned."""

    capability_activation_id: UUID
    connector_binding_id: UUID
    resource_scope: ResourceScope


@dataclass(frozen=True, slots=True)
class PreparedActionChainRead:
    disposition: Literal["prepared", "already_prepared"]
    intent: ReadIntent


class PostgresActionChainReadPreparer:
    """Consume one exact chain lease into one immutable, undispatched read intent."""

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        role: Literal["eva_runtime", "eva_dbos"] = "eva_runtime",
    ) -> None:
        if engine.url.get_backend_name() != "postgresql":
            raise ValueError("action-chain read preparation requires PostgreSQL")
        self._engine = engine
        self._scope_value = scope
        self._role = role
        self._ledger = PostgresActionChainLedger(engine, scope, role=role)

    def prepare_read(
        self,
        claim: ActionChainClaim,
        *,
        live_catalog: ActionToolCatalog,
        authority: ReadStepAuthority,
        now: datetime,
    ) -> PreparedActionChainRead:
        _require_utc_second(now, "now")
        with self._engine.begin() as connection:
            self._scope(connection)
            attention_row = _locked_attention(
                connection, self._scope_value, claim.attention_id
            )
            step_row = connection.execute(
                sa.select(plan_steps)
                .where(
                    plan_steps.c.tenant_id == self._scope_value.tenant_id,
                    plan_steps.c.installation_id
                    == self._scope_value.installation_id,
                    plan_steps.c.plan_step_id == claim.plan_step_id,
                    plan_steps.c.plan_id == claim.plan_id,
                )
                .with_for_update()
            ).one_or_none()
            if attention_row is None or step_row is None:
                raise ActionChainReadConflict("claimed read is absent in scope")

            chain = self._ledger.load_in_transaction(connection, claim.plan_id)
            stored_step = next(
                (
                    item
                    for item in chain.steps
                    if item.plan_step_id == claim.plan_step_id
                    and item.logical_step_key == claim.logical_step_key
                ),
                None,
            )
            if stored_step is None:
                raise ActionChainReadConflict("claimed read step differs")
            try:
                validate_action_chain_requests(chain.decision, live_catalog)
            except ActionChainValidationError as error:
                raise ActionChainReadConflict(str(error)) from error
            if live_catalog.authority_snapshot() != chain.authority_snapshot:
                raise ActionChainReadConflict("live action catalog differs from admission")

            action = next(
                (
                    item
                    for item in chain.decision.actions
                    if item.action_id == stored_step.logical_step_key
                ),
                None,
            )
            plan_step = next(
                (
                    item
                    for item in chain.plan.steps
                    if item.step_id == stored_step.logical_step_key
                ),
                None,
            )
            if (
                action is None
                or plan_step is None
                or action.kind != "request_read"
                or plan_step.kind != "read"
            ):
                raise ActionChainReadConflict("claimed step is not a read")
            descriptor = live_catalog.require(action.operation)
            activation, manifest = self._validate_authority(
                connection,
                chain,
                action=action,
                plan_step=plan_step,
                descriptor=descriptor,
                authority=authority,
            )

            expected_intent_id = uuid5(claim.plan_step_id, "read-intent:v1")
            if stored_step.state == "running":
                if stored_step.read_intent_id != expected_intent_id:
                    raise ActionChainReadConflict("prepared read identity differs")
                event_data = self._latest_child_data(
                    connection, chain, stored_step.logical_step_key
                )
                if event_data.get("claim_token_sha256") != claim.token_sha256:
                    raise ActionChainReadConflict("prepared read belongs to another lease")
                intent_row = connection.execute(
                    sa.select(read_intents).where(
                        read_intents.c.tenant_id == self._scope_value.tenant_id,
                        read_intents.c.installation_id
                        == self._scope_value.installation_id,
                        read_intents.c.read_intent_id == expected_intent_id,
                    )
                ).one()
                try:
                    intent = ReadIntent.model_validate(intent_row.payload)
                except ValidationError as error:
                    raise ActionChainReadConflict("prepared read payload is invalid") from error
                return PreparedActionChainRead("already_prepared", intent)

            states = {item.plan_step_id: item.state for item in chain.steps}
            if (
                stored_step.state != "ready"
                or any(
                    states.get(dependency) != "succeeded"
                    for dependency in stored_step.dependency_step_ids
                )
            ):
                raise ActionChainReadConflict("claimed read is no longer runnable")
            if (
                attention_row.state != "claimed"
                or attention_row.lease_owner != claim.worker_id
                or int(attention_row.revision) != claim.attention_revision
                or attention_row.lease_expires_at != claim.lease_expires_at
                or claim.lease_expires_at <= now
            ):
                raise ActionChainReadConflict("read lease is stale or expired")

            query_sha256 = canonical_sha256(action.arguments)
            intent = ReadIntent(
                schema_version=1,
                contract="ReadIntent/v1",
                read_intent_id=str(expected_intent_id),
                episode_id=chain.plan.episode_id,
                generation=chain.plan.generation,
                purpose="business_read",
                effect_intent_id=None,
                operation=action.operation,
                resource_scope=authority.resource_scope.model_dump(mode="json"),
                query_sha256=query_sha256,
                maximum_freshness_ms=manifest.maximum_freshness_ms,
                maximum_attempts=2,
                capability_id=activation.capability_id,
                deadline=chain.plan.deadline,
            )
            payload = intent.model_dump(mode="json")
            connection.execute(
                sa.insert(read_intents).values(
                    tenant_id=self._scope_value.tenant_id,
                    installation_id=self._scope_value.installation_id,
                    read_intent_id=expected_intent_id,
                    episode_id=chain.storage_episode_id,
                    generation=chain.plan.generation,
                    logical_read_key=f"action-chain:{claim.plan_step_id}:read",
                    plan_step_id=claim.plan_step_id,
                    purpose=intent.purpose,
                    effect_intent_id=None,
                    circuit_breaker_id=None,
                    operation=intent.operation,
                    resource_scope=intent.resource_scope.model_dump(mode="json"),
                    resource_scope_sha256=bytes.fromhex(
                        intent.resource_scope.scope_sha256
                    ),
                    query_sha256=bytes.fromhex(query_sha256),
                    maximum_freshness_ms=intent.maximum_freshness_ms,
                    maximum_attempts=intent.maximum_attempts,
                    capability_id=intent.capability_id,
                    capability_activation_id=authority.capability_activation_id,
                    deadline=_parse_timestamp(intent.deadline),
                    payload=payload,
                    payload_sha256=bytes.fromhex(canonical_sha256(intent)),
                    retention_policy_id=chain.retention_policy_id,
                    retention_until=chain.retention_until,
                    created_at=now,
                )
            )
            step_update = connection.execute(
                sa.update(plan_steps)
                .where(
                    plan_steps.c.tenant_id == self._scope_value.tenant_id,
                    plan_steps.c.installation_id
                    == self._scope_value.installation_id,
                    plan_steps.c.plan_step_id == claim.plan_step_id,
                    plan_steps.c.plan_id == claim.plan_id,
                    plan_steps.c.state == "ready",
                    plan_steps.c.read_intent_id.is_(None),
                    plan_steps.c.effect_intent_id.is_(None),
                    plan_steps.c.outcome_ref.is_(None),
                    plan_steps.c.revision == 1,
                )
                .values(
                    state="running",
                    read_intent_id=expected_intent_id,
                    revision=2,
                    updated_at=now,
                )
            )
            if step_update.rowcount != 1:
                raise ActionChainReadConflict("read step changed during preparation")

            event_data = {
                "capability_activation_id": str(authority.capability_activation_id),
                "capability_manifest_id": str(activation.capability_manifest_id),
                "catalog_sha256": chain.authority_snapshot["catalog_sha256"],
                "claim_token_sha256": claim.token_sha256,
                "connector_binding_id": str(authority.connector_binding_id),
                "logical_step_key": claim.logical_step_key,
                "plan_id": str(claim.plan_id),
                "plan_step_id": str(claim.plan_step_id),
                "query_sha256": query_sha256,
                "read_intent_id": str(expected_intent_id),
                "read_intent_payload_sha256": canonical_sha256(intent),
            }
            child_operation_id = action_chain_step_operation_id(
                chain.plan, claim.logical_step_key
            )
            child_checkpoint = connection.execute(
                sa.select(operational_checkpoints)
                .where(
                    operational_checkpoints.c.tenant_id
                    == self._scope_value.tenant_id,
                    operational_checkpoints.c.installation_id
                    == self._scope_value.installation_id,
                    operational_checkpoints.c.thread_id == child_operation_id,
                )
                .with_for_update()
            ).one()
            transition_timeline_thread(
                connection,
                self._scope_value,
                thread_id=child_operation_id,
                expected_revision=int(child_checkpoint.revision),
                checkpoint_id=f"checkpoint:{child_operation_id}:read-prepared",
                phase="action_chain_read_prepared",
                status="waiting_for_proof",
                event_type="ACTION_CHAIN_READ_INTENT_PREPARED",
                occurred_at=now,
                data=event_data,
                operation_id=child_operation_id,
                deadline=_parse_timestamp(chain.plan.deadline),
                retry_count=int(child_checkpoint.retry_count),
                last_error_fingerprint=(
                    None
                    if child_checkpoint.last_error_fingerprint is None
                    else bytes(child_checkpoint.last_error_fingerprint).hex()
                ),
            )
            write_action_chain_attention(
                connection,
                self._scope_value,
                chain,
                attention_row,
                state="waiting",
                worker_id=None,
                lease_expires_at=None,
                not_before=now,
                now=now,
                root_event_type="ACTION_CHAIN_READ_INTENT_PREPARED",
                root_status="active",
            )
            self._ledger.load_in_transaction(connection, claim.plan_id)
            return PreparedActionChainRead("prepared", intent)

    def _validate_authority(
        self,
        connection: sa.Connection,
        chain: StoredActionChain,
        *,
        action: object,
        plan_step: object,
        descriptor: object,
        authority: ReadStepAuthority,
    ) -> tuple[sa.Row, CapabilityManifest]:
        scope_payload = authority.resource_scope.model_dump(mode="json")
        scope_base = {
            "resource_type": authority.resource_scope.resource_type,
            "resource_id": authority.resource_scope.resource_id,
        }
        if (
            authority.resource_scope.scope_sha256 != canonical_sha256(scope_base)
            or authority.resource_scope.resource_id != action.resource
        ):
            raise ActionChainReadConflict("host resource scope differs from the plan")
        activation = connection.execute(
            sa.select(capability_activations).where(
                capability_activations.c.tenant_id == self._scope_value.tenant_id,
                capability_activations.c.installation_id
                == self._scope_value.installation_id,
                capability_activations.c.activation_id
                == authority.capability_activation_id,
            ).with_for_update(read=True)
        ).one_or_none()
        if activation is None:
            raise ActionChainReadConflict("read capability activation is absent")
        manifest_row = connection.execute(
            sa.select(capability_manifests).where(
                capability_manifests.c.tenant_id == self._scope_value.tenant_id,
                capability_manifests.c.installation_id
                == self._scope_value.installation_id,
                capability_manifests.c.capability_manifest_id
                == activation.capability_manifest_id,
            )
        ).one_or_none()
        binding_row = connection.execute(
            sa.select(connector_bindings).where(
                connector_bindings.c.tenant_id == self._scope_value.tenant_id,
                connector_bindings.c.installation_id
                == self._scope_value.installation_id,
                connector_bindings.c.binding_id == authority.connector_binding_id,
            ).with_for_update(read=True)
        ).one_or_none()
        policy_row = connection.execute(
            sa.select(policy_pack_versions).where(
                policy_pack_versions.c.tenant_id == self._scope_value.tenant_id,
                policy_pack_versions.c.installation_id
                == self._scope_value.installation_id,
                policy_pack_versions.c.policy_id == activation.policy_id,
                policy_pack_versions.c.policy_version
                == activation.policy_pack_version,
            ).with_for_update(read=True)
        ).one_or_none()
        try:
            manifest = CapabilityManifest.model_validate(
                None if manifest_row is None else manifest_row.payload
            )
            binding = ConnectorBinding.model_validate(
                None if binding_row is None else binding_row.payload
            )
        except ValidationError as error:
            raise ActionChainReadConflict("read capability payload is invalid") from error
        if (
            manifest_row is None
            or binding_row is None
            or policy_row is None
            or activation.state != "active"
            or manifest_row.state != "active"
            or binding_row.status != "verified"
            or policy_row.state != "current"
            or activation.connector_binding_id != authority.connector_binding_id
            or activation.scope != scope_payload
            or bytes(activation.scope_sha256).hex()
            != authority.resource_scope.scope_sha256
            or activation.capability_id != manifest.capability_id
            or activation.operation != manifest.operation
            or activation.operation != action.operation
            or manifest_row.capability_id != manifest.capability_id
            or manifest_row.connector_id != manifest.connector_id
            or manifest_row.operation != manifest.operation
            or bytes(manifest_row.input_schema_sha256).hex()
            != manifest.input_schema_sha256
            or bytes(manifest_row.output_schema_sha256).hex()
            != manifest.output_schema_sha256
            or manifest_row.effect_class != manifest.effect_class
            or list(manifest_row.required_provider_scopes)
            != manifest.required_provider_scopes
            or manifest_row.proof_floor != manifest.proof_floor
            or manifest_row.maximum_freshness_ms != manifest.maximum_freshness_ms
            or manifest.connector_id != binding.provider_id
            or manifest.effect_class != "read"
            or manifest.maximum_freshness_ms is None
            or manifest.input_schema_sha256 != plan_step.input_schema_sha256
            or manifest.output_schema_sha256 != plan_step.output_schema_sha256
            or manifest.proof_floor != plan_step.proof_floor
            or manifest.input_schema_sha256 != descriptor.input_schema_sha256
            or manifest.output_schema_sha256 != descriptor.output_schema_sha256
            or manifest.proof_floor != descriptor.proof_floor
            or action.operation not in binding.confirmed_operations
            or authority.resource_scope.resource_type not in binding.confirmed_scopes
            or not set(manifest.required_provider_scopes).issubset(
                binding.confirmed_scopes
            )
            or bytes(activation.policy_pack_sha256).hex() != chain.plan.policy_sha256
            or bytes(policy_row.canonical_sha256).hex() != chain.plan.policy_sha256
            or bytes(activation.executable_sha256)
            != bytes(manifest_row.executable_sha256)
            or bytes(activation.sandbox_profile_sha256)
            != bytes(manifest_row.sandbox_profile_sha256)
            or canonical_sha256(manifest) != bytes(manifest_row.payload_sha256).hex()
            or canonical_sha256(binding) != bytes(binding_row.payload_sha256).hex()
            or binding.status != "verified"
            or binding.binding_id != str(binding_row.binding_id)
            or binding.installation_id != str(binding_row.installation_id)
            or binding.tenant_id != str(binding_row.tenant_id)
            or binding.owner_principal_id != str(binding_row.owner_principal_id)
            or binding.provider_id != binding_row.provider_id
            or binding.provider_account_id != binding_row.provider_account_id
            or binding.provider_resource_id != binding_row.provider_resource_id
            or binding.confirmed_operations != list(binding_row.confirmed_operations)
            or binding.confirmed_scopes != list(binding_row.confirmed_scopes)
            or binding.credential_reference != binding_row.credential_reference
            or canonical_sha256(policy_row.payload)
            != bytes(policy_row.payload_sha256).hex()
            or bytes(policy_row.canonical_sha256)
            != bytes(policy_row.payload_sha256)
        ):
            raise ActionChainReadConflict("concrete read authority differs")
        return activation, manifest

    def _latest_child_data(
        self,
        connection: sa.Connection,
        chain: StoredActionChain,
        logical_step_key: str,
    ) -> dict[str, object]:
        operation_id = action_chain_step_operation_id(chain.plan, logical_step_key)
        row = connection.execute(
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
        data = row.payload.get("data")
        if row.event_type != "ACTION_CHAIN_READ_INTENT_PREPARED" or not isinstance(
            data, dict
        ):
            raise ActionChainReadConflict("prepared read timeline differs")
        return data

    def _scope(self, connection: sa.Connection) -> None:
        connection.exec_driver_sql(f"SET LOCAL ROLE {self._role}")
        set_local_scope(
            connection,
            tenant_id=str(self._scope_value.tenant_id),
            installation_id=str(self._scope_value.installation_id),
        )


def _require_utc_second(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be timezone-aware UTC")
    if value.microsecond:
        raise ValueError(f"{field} must use whole-second precision")


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


__all__ = [
    "ActionChainReadConflict",
    "ActionChainReadError",
    "PostgresActionChainReadPreparer",
    "PreparedActionChainRead",
    "ReadStepAuthority",
]
