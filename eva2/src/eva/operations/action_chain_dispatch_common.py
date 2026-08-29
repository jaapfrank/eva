"""Shared persistence mechanics for read and effect provider dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Literal, NamedTuple
from uuid import UUID, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.action_chain_ledger import (
    StoredActionChain,
    capability_activations,
    capability_manifests,
    connector_bindings,
    plan_steps,
    policy_pack_versions,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector import ConnectorEnvelope
from eva.contracts.timestamps import (
    format_utc_timestamp,
    parse_utc_timestamp,
    require_utc_second,
)
from eva.contracts.execution import (
    CapabilityGrant,
    CapabilityManifest,
    ConnectorBinding,
    CredentialGrant,
    EffectAttempt,
    EffectIntent,
    ReadAttempt,
    ReadIntent,
)
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_checkpoints,
    operational_events,
    transition_timeline_thread,
)
from eva.application.action_chain import (
    ActionChainValidationError,
    ActionToolCatalog,
    validate_action_chain_requests,
)


@dataclass(frozen=True, slots=True)
class DispatchAuthorityRows:
    intent: Any
    step: Any
    attempt: Any
    activation: Any
    manifest: Any
    binding: Any


class ResolvedDispatchAuthorization(NamedTuple):
    intent_row: Any
    chain: StoredActionChain
    stored_step: Any
    intent: ReadIntent | EffectIntent
    action: Any
    profile: Any
    capability_manifest: CapabilityManifest
    binding: ConnectorBinding
    existing: Any
    created_at: datetime


def utc_now_second() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def require_connector_execution_inputs(now: datetime, timeout_seconds: float) -> None:
    require_utc_second(now, "now")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(
        timeout_seconds, bool
    ):
        raise TypeError("connector timeout must be numeric")
    if timeout_seconds <= 0 or timeout_seconds > 30:
        raise ValueError("connector timeout must be between 0 and 30 seconds")


def execute_claimed_connector(
    claim: Any,
    *,
    results: Any,
    now: datetime,
    timeout_seconds: float,
    clock: Callable[[], datetime],
    claim_label: str,
    recording_subject: str,
    runner: Callable[..., Any],
) -> Any:
    """Execute one first claim and atomically retain its provider result."""

    if (
        claim.process_spec is None
        or claim.connector_manifest is None
        or claim.initialize is None
        or claim.invoke is None
    ):
        raise RuntimeError(f"first {claim_label} claim lacks call material")
    deadline = parse_utc_timestamp(claim.attempt.deadline)
    remaining_seconds = (deadline - now).total_seconds()
    if remaining_seconds <= 0:
        raise RuntimeError(f"claimed {claim_label} has no remaining deadline")
    result = runner(
        spec=claim.process_spec,
        manifest=claim.connector_manifest,
        initialize=claim.initialize,
        invoke=claim.invoke,
        observed_at=format_utc_timestamp(now),
        timeout_seconds=min(timeout_seconds, remaining_seconds),
    )
    recorded_at = clock()
    require_utc_second(recorded_at, "clock result")
    if recorded_at < now:
        raise RuntimeError(f"{recording_subject} recording precedes the call")
    return results.record(
        claim,
        result=result,
        observed_at=now,
        recorded_at=recorded_at,
    )


def execute_connector_once(
    operation_id: UUID,
    *,
    results: Any,
    claim_provider: Callable[[], Any],
    conflict: type[Exception],
    now: datetime,
    timeout_seconds: float,
    clock: Callable[[], datetime],
    claim_label: str,
    recording_subject: str,
    runner: Callable[..., Any],
) -> tuple[Literal["executed", "already_started"], Any, Any | None]:
    """Resolve replay/races and execute only the first durable provider claim."""

    require_connector_execution_inputs(now, timeout_seconds)
    existing = results.load_terminal(operation_id)
    if existing is not None:
        return "already_started", existing.attempt, existing
    try:
        claim = claim_provider()
    except conflict:
        raced_result = results.load_terminal(operation_id)
        if raced_result is None:
            raise
        return "already_started", raced_result.attempt, raced_result
    if claim.disposition == "already_started":
        return "already_started", claim.attempt, None
    stored = execute_claimed_connector(
        claim,
        results=results,
        now=now,
        timeout_seconds=timeout_seconds,
        clock=clock,
        claim_label=claim_label,
        recording_subject=recording_subject,
        runner=runner,
    )
    return "executed", stored.attempt, stored


def resolve_dispatch_authorization(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    kind: Literal["read", "effect"],
    intent_id: UUID,
    intent_table: sa.Table,
    attempt_table: sa.Table,
    ledger: Any,
    preparer: Any,
    provider_registry: Any,
    live_catalog: ActionToolCatalog,
    now: datetime,
    conflict_type: type[Exception],
    authority_factory: Callable[[Any, Any, ReadIntent | EffectIntent], Any],
    validate_profile: Callable[..., None],
) -> ResolvedDispatchAuthorization:
    """Resolve and revalidate the shared durable authority graph once."""

    intent_id_name = f"{kind}_intent_id"
    intent_statement = sa.select(intent_table).where(
        intent_table.c.tenant_id == scope.tenant_id,
        intent_table.c.installation_id == scope.installation_id,
        intent_table.c[intent_id_name] == intent_id,
    )
    if kind == "effect":
        intent_statement = intent_statement.with_for_update()
    intent_row = connection.execute(intent_statement).one_or_none()
    if intent_row is None:
        raise conflict_type(f"prepared {kind} intent is absent in scope")
    step_row = connection.execute(
        sa.select(plan_steps)
        .where(
            plan_steps.c.tenant_id == scope.tenant_id,
            plan_steps.c.installation_id == scope.installation_id,
            plan_steps.c.plan_step_id == intent_row.plan_step_id,
            plan_steps.c[intent_id_name] == intent_id,
        )
        .with_for_update()
    ).one_or_none()
    if step_row is None or step_row.state != "running":
        raise conflict_type(f"prepared {kind} step is no longer running")

    chain = ledger.load_in_transaction(connection, step_row.plan_id)
    stored_step = next(
        (
            item
            for item in chain.steps
            if item.plan_step_id == step_row.plan_step_id
            and (kind == "effect" or getattr(item, intent_id_name) == intent_id)
        ),
        None,
    )
    if stored_step is None:
        raise conflict_type(f"prepared {kind} differs from its plan")
    intent_type = ReadIntent if kind == "read" else EffectIntent
    try:
        intent = intent_type.model_validate(intent_row.payload)
        validate_action_chain_requests(chain.decision, live_catalog)
    except (ValueError, ActionChainValidationError) as error:
        raise conflict_type(str(error)) from error
    if live_catalog.authority_snapshot() != chain.authority_snapshot:
        raise conflict_type("live action catalog differs from admission")

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
    expected_action_kind = "request_read" if kind == "read" else "propose_effect"
    if (
        action is None
        or plan_step is None
        or action.kind != expected_action_kind
        or (kind == "effect" and plan_step.kind != "effect")
    ):
        raise conflict_type(f"prepared action is not a {kind}")
    activation_hint = connection.execute(
        sa.select(capability_activations)
        .where(
            capability_activations.c.tenant_id == scope.tenant_id,
            capability_activations.c.installation_id == scope.installation_id,
            capability_activations.c.activation_id
            == intent_row.capability_activation_id,
        )
        .with_for_update(read=True)
    ).one_or_none()
    if activation_hint is None:
        raise conflict_type(f"{kind} capability activation is absent")
    descriptor = live_catalog.require(action.operation)
    activation, capability_manifest = preparer._validate_authority(
        connection,
        chain,
        action=action,
        plan_step=plan_step,
        descriptor=descriptor,
        authority=authority_factory(intent_row, activation_hint, intent),
    )
    binding = ConnectorBinding.model_validate(
        connection.execute(
            sa.select(connector_bindings.c.payload).where(
                connector_bindings.c.tenant_id == scope.tenant_id,
                connector_bindings.c.installation_id == scope.installation_id,
                connector_bindings.c.binding_id == activation.connector_binding_id,
            )
        ).scalar_one()
    )
    try:
        profile = provider_registry.resolve(activation.capability_manifest_id)
    except LookupError as error:
        raise conflict_type(str(error)) from error
    validate_profile(
        profile,
        capability_manifest=capability_manifest,
        binding=binding,
        intent=intent,
    )
    existing = connection.execute(
        sa.select(attempt_table)
        .where(
            attempt_table.c.tenant_id == scope.tenant_id,
            attempt_table.c.installation_id == scope.installation_id,
            attempt_table.c[intent_id_name] == intent_id,
            attempt_table.c.ordinal == 1,
        )
        .with_for_update()
    ).one_or_none()
    if existing is None and now >= parse_utc_timestamp(intent.deadline):
        raise conflict_type(f"{kind} dispatch deadline has passed")
    return ResolvedDispatchAuthorization(
        intent_row,
        chain,
        stored_step,
        intent,
        action,
        profile,
        capability_manifest,
        binding,
        existing,
        now if existing is None else existing.created_at,
    )


def build_dispatch_attempt_values(
    *,
    scope: TimelineScope,
    chain: StoredActionChain,
    attempt: ReadAttempt | EffectAttempt,
    capability_grant: CapabilityGrant,
    credential_grant: CredentialGrant,
    created_at: datetime,
) -> dict[str, object]:
    """Return the provider-neutral durable columns for one dispatch fence."""

    return {
        "tenant_id": scope.tenant_id,
        "installation_id": scope.installation_id,
        "ordinal": attempt.ordinal,
        "state": attempt.state,
        "capability_grant_id": UUID(capability_grant.grant_id),
        "capability_grant_payload": capability_grant.model_dump(mode="json"),
        "capability_grant_sha256": bytes.fromhex(canonical_sha256(capability_grant)),
        "capability_grant_nonce": capability_grant.nonce,
        "capability_grant_deadline": parse_utc_timestamp(capability_grant.deadline),
        "credential_grant_id": UUID(credential_grant.credential_grant_id),
        "credential_grant_payload": credential_grant.model_dump(mode="json"),
        "credential_grant_sha256": bytes.fromhex(canonical_sha256(credential_grant)),
        "credential_grant_nonce": credential_grant.nonce,
        "credential_grant_deadline": parse_utc_timestamp(credential_grant.deadline),
        "connector_manifest_sha256": bytes.fromhex(attempt.connector_manifest_sha256),
        "request_sha256": bytes.fromhex(attempt.request_sha256),
        "response_sha256": None,
        "deadline": parse_utc_timestamp(attempt.deadline),
        "payload": attempt.model_dump(mode="json"),
        "payload_sha256": bytes.fromhex(canonical_sha256(attempt)),
        "revision": 1,
        "retention_policy_id": chain.retention_policy_id,
        "retention_until": chain.retention_until,
        "created_at": created_at,
        "updated_at": created_at,
    }


def commit_dispatch_authorized(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    kind: Literal["read", "effect"],
    operation_id: str,
    occurred_at: datetime,
    event_data: dict[str, object],
    deadline: datetime,
) -> None:
    """Commit the shared timeline boundary after a dispatch fence is stored."""

    checkpoint = _load_scoped_row(
        connection,
        scope,
        operational_checkpoints,
        operational_checkpoints.c.thread_id == operation_id,
        lock="write",
    )
    transition_timeline_thread(
        connection,
        scope,
        thread_id=operation_id,
        expected_revision=int(checkpoint.revision),
        checkpoint_id=f"checkpoint:{operation_id}:{kind}-dispatch-authorized",
        phase=f"action_chain_{kind}_dispatch_authorized",
        status="waiting_for_proof",
        event_type=f"ACTION_CHAIN_{kind.upper()}_DISPATCH_AUTHORIZED",
        occurred_at=occurred_at,
        data=event_data,
        operation_id=operation_id,
        deadline=deadline,
        retry_count=int(checkpoint.retry_count),
        last_error_fingerprint=(
            None
            if checkpoint.last_error_fingerprint is None
            else bytes(checkpoint.last_error_fingerprint).hex()
        ),
    )


def build_dispatch_authority_material(
    *,
    kind: Literal["read", "effect"],
    scope: TimelineScope,
    chain: StoredActionChain,
    action_arguments: dict[str, object],
    intent: ReadIntent | EffectIntent,
    profile: Any,
    capability_manifest: CapabilityManifest,
    binding: ConnectorBinding,
    created_at: datetime,
    conflict_type: type[Exception],
) -> tuple[UUID, CapabilityGrant, CredentialGrant, str, ConnectorEnvelope]:
    """Build the provider-neutral grants and exact connector envelope once."""

    if (
        datetime.strptime(
            profile.connector_manifest.issued_at, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=UTC)
        > created_at
    ):
        raise conflict_type("connector manifest was issued in the future")
    if kind == "read" and isinstance(intent, ReadIntent):
        attempt_id = uuid5(UUID(intent.read_intent_id), "read-attempt:1")
        payload_sha256 = intent.query_sha256
        request_id = str(uuid5(attempt_id, "connector-invoke:v1"))
    elif kind == "effect" and isinstance(intent, EffectIntent):
        attempt_id = uuid5(UUID(intent.effect_intent_id), "effect-attempt:1")
        payload_sha256 = intent.frozen_payload_sha256
        request_id = intent.idempotency_key
    else:
        raise TypeError("dispatch kind and intent differ")
    issued_at = created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    capability_grant = CapabilityGrant.model_validate(
        {
            "schema_version": 1,
            "contract": "CapabilityGrant/v1",
            "grant_id": str(uuid5(attempt_id, "capability-grant:v1")),
            "tenant_id": str(scope.tenant_id),
            "episode_id": chain.plan.episode_id,
            "generation": chain.plan.generation,
            "capability_id": intent.capability_id,
            "connector_binding_id": binding.binding_id,
            "operation": intent.operation,
            "resource_scope": intent.resource_scope.model_dump(mode="json"),
            "authorized_payload_sha256": payload_sha256,
            "policy_sha256": chain.plan.policy_sha256,
            "nonce": str(uuid5(attempt_id, "capability-nonce:v1")),
            "issued_at": issued_at,
            "deadline": intent.deadline,
        }
    )
    credential_grant = CredentialGrant.model_validate(
        {
            "schema_version": 1,
            "contract": "CredentialGrant/v1",
            "credential_grant_id": str(uuid5(attempt_id, "credential-grant:v1")),
            "connector_binding_id": binding.binding_id,
            "model_provider_profile_id": None,
            "worker_id": profile.worker_id,
            "executable_sha256": capability_manifest.executable_sha256,
            "provider_scopes": capability_manifest.required_provider_scopes,
            "secret_descriptor_id": binding.credential_reference,
            "nonce": str(uuid5(attempt_id, "credential-nonce:v1")),
            "issued_at": issued_at,
            "deadline": intent.deadline,
        }
    )
    connector_manifest_sha256 = canonical_sha256(profile.connector_manifest)
    envelope = ConnectorEnvelope.model_validate(
        {
            "schema_version": 1,
            "contract": "ConnectorEnvelope/v1",
            "installation_id": str(scope.installation_id),
            "tenant_id": str(scope.tenant_id),
            "request_id": request_id,
            "correlation_id": str(uuid5(attempt_id, "correlation:v1")),
            "episode_id": chain.plan.episode_id,
            "generation": chain.plan.generation,
            "connector_id": binding.provider_id,
            "connector_binding_id": binding.binding_id,
            "capability_id": intent.capability_id,
            "operation": intent.operation,
            "resource_scope": intent.resource_scope.model_dump(mode="json"),
            "deadline": intent.deadline,
            "request_payload_sha256": payload_sha256,
            "connector_manifest_sha256": connector_manifest_sha256,
            "capability_grant_id": capability_grant.grant_id,
            "credential_grant_id": credential_grant.credential_grant_id,
            "request_payload": action_arguments,
        }
    )
    return (
        attempt_id,
        capability_grant,
        credential_grant,
        connector_manifest_sha256,
        envelope,
    )


def load_dispatch_authority_rows(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    intent_table: sa.Table,
    attempt_table: sa.Table,
    intent_id_name: str,
    intent_id: UUID,
    lock_intent: bool,
) -> DispatchAuthorityRows:
    """Load the same sealed authority graph for either a read or an effect."""

    intent_id_column = intent_table.c[intent_id_name]
    intent = _load_scoped_row(
        connection,
        scope,
        intent_table,
        intent_id_column == intent_id,
        lock="write" if lock_intent else None,
    )
    step = _load_scoped_row(
        connection,
        scope,
        plan_steps,
        plan_steps.c.plan_step_id == intent.plan_step_id,
        plan_steps.c[intent_id_name] == intent_id,
        lock="read",
    )
    attempt = _load_scoped_row(
        connection,
        scope,
        attempt_table,
        attempt_table.c[intent_id_name] == intent_id,
        attempt_table.c.ordinal == 1,
        lock="write",
    )
    activation = _load_scoped_row(
        connection,
        scope,
        capability_activations,
        capability_activations.c.activation_id == intent.capability_activation_id,
        lock="read",
    )
    manifest = _load_scoped_row(
        connection,
        scope,
        capability_manifests,
        capability_manifests.c.capability_manifest_id
        == activation.capability_manifest_id,
    )
    binding = _load_scoped_row(
        connection,
        scope,
        connector_bindings,
        connector_bindings.c.binding_id == activation.connector_binding_id,
        lock="read",
    )
    _load_scoped_row(
        connection,
        scope,
        policy_pack_versions,
        policy_pack_versions.c.policy_id == activation.policy_id,
        policy_pack_versions.c.policy_version == activation.policy_pack_version,
        lock="read",
    )
    return DispatchAuthorityRows(intent, step, attempt, activation, manifest, binding)


def _load_scoped_row(
    connection: sa.Connection,
    scope: TimelineScope,
    table: sa.Table,
    *criteria: Any,
    lock: Literal["read", "write"] | None = None,
) -> Any:
    statement = sa.select(table).where(
        table.c.tenant_id == scope.tenant_id,
        table.c.installation_id == scope.installation_id,
        *criteria,
    )
    if lock is not None:
        statement = statement.with_for_update(read=lock == "read")
    return connection.execute(statement).one()


def load_latest_provider_event(
    connection: sa.Connection, scope: TimelineScope, operation_id: str
) -> Any:
    return connection.execute(
        sa.select(operational_events)
        .where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == operation_id,
        )
        .order_by(operational_events.c.sequence.desc())
        .limit(1)
    ).one()


def commit_provider_call_started(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    kind: Literal["read", "effect"],
    operation_id: str,
    checkpoint_data: dict[str, object],
    now: datetime,
    deadline: datetime,
) -> None:
    checkpoint = _load_scoped_row(
        connection,
        scope,
        operational_checkpoints,
        operational_checkpoints.c.thread_id == operation_id,
        lock="write",
    )
    data = dict(checkpoint_data)
    data["provider_call_started_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    transition_timeline_thread(
        connection,
        scope,
        thread_id=operation_id,
        expected_revision=int(checkpoint.revision),
        checkpoint_id=f"checkpoint:{operation_id}:{kind}-provider-call-started",
        phase=f"action_chain_{kind}_provider_call_started",
        status="waiting_for_proof",
        event_type=f"ACTION_CHAIN_{kind.upper()}_PROVIDER_CALL_STARTED",
        occurred_at=now,
        data=data,
        operation_id=operation_id,
        deadline=deadline,
        retry_count=int(checkpoint.retry_count),
        last_error_fingerprint=(
            None
            if checkpoint.last_error_fingerprint is None
            else bytes(checkpoint.last_error_fingerprint).hex()
        ),
    )
