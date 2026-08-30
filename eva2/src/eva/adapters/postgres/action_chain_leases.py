"""Crash-safe worker leases for admitted action chains; no capability execution."""

from __future__ import annotations
from eva.contracts.timestamps import format_required_utc_timestamp as _format_timestamp
from eva.contracts.timestamps import parse_utc_timestamp as _parse_timestamp
from eva.contracts.timestamps import require_utc_second as _require_utc_second

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
from typing import Literal
from uuid import UUID

import sqlalchemy as sa

from eva.adapters.postgres.action_chain_ledger import (
    PostgresActionChainStore,
    StoredActionChain,
    StoredActionChainStep,
    attention_items,
    plans,
)
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_checkpoints,
    transition_timeline_thread,
)
from eva.application.action_chain import (
    action_chain_step_operation_id,
    action_chain_thread_id,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.lifecycle import AttentionWake


class ActionChainLeaseError(RuntimeError):
    """Base failure for continuation ownership."""


class ActionChainLeaseConflict(ActionChainLeaseError):
    """The exact lease token is absent, stale, expired, or belongs elsewhere."""


@dataclass(frozen=True, slots=True)
class ActionChainClaim:
    attention_id: UUID
    plan_id: UUID
    plan_step_id: UUID
    logical_step_key: str
    attention_revision: int
    worker_id: str
    lease_expires_at: datetime

    @property
    def token_sha256(self) -> str:
        """Bind every authority-bearing field of this exact worker lease."""

        return canonical_sha256(
            {
                "attention_id": str(self.attention_id),
                "attention_revision": self.attention_revision,
                "lease_expires_at": _format_timestamp(self.lease_expires_at),
                "logical_step_key": self.logical_step_key,
                "plan_id": str(self.plan_id),
                "plan_step_id": str(self.plan_step_id),
                "worker_id": self.worker_id,
            }
        )


class PostgresActionChainLeases(PostgresActionChainStore):
    _postgres_error = "action-chain leases require PostgreSQL"
    """Lease one ready step atomically without creating an intent or effect."""

    def claim_next(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int = 30,
    ) -> ActionChainClaim | None:
        _require_worker(worker_id)
        _require_utc_second(now, "now")
        if not 1 <= lease_seconds <= 600:
            raise ValueError("lease_seconds must be inside 1..600")

        with self._engine.begin() as connection:
            self._scope(connection)
            candidate = connection.execute(
                sa.select(attention_items.c.attention_id, plans.c.plan_id)
                .join(
                    plans,
                    sa.and_(
                        plans.c.tenant_id == attention_items.c.tenant_id,
                        plans.c.installation_id == attention_items.c.installation_id,
                        plans.c.episode_id == attention_items.c.episode_id,
                        plans.c.generation == attention_items.c.generation,
                    ),
                )
                .where(
                    attention_items.c.tenant_id == self._scope_value.tenant_id,
                    attention_items.c.installation_id
                    == self._scope_value.installation_id,
                    attention_items.c.kind == "continuation",
                    attention_items.c.deadline >= now,
                    plans.c.state == "current",
                    sa.or_(
                        sa.and_(
                            attention_items.c.state == "pending",
                            attention_items.c.not_before <= now,
                        ),
                        sa.and_(
                            attention_items.c.state == "claimed",
                            attention_items.c.lease_expires_at < now,
                        ),
                    ),
                )
                .order_by(attention_items.c.not_before, attention_items.c.attention_id)
                .with_for_update(of=attention_items, skip_locked=True)
                .limit(1)
            ).one_or_none()
            if candidate is None:
                return None

            chain = self._ledger.load_in_transaction(connection, candidate.plan_id)
            step = _runnable_step(chain)
            if step is None:
                return None
            attention_row = _locked_attention(
                connection,
                self._scope_value,
                candidate.attention_id,
            )
            if attention_row is None:
                return None

            if attention_row.state == "claimed":
                needs_help = self._record_failure(
                    connection,
                    chain,
                    step.logical_step_key,
                    error_text="action-chain lease expired",
                    now=now,
                )
                if needs_help:
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
                        root_event_type="ACTION_CHAIN_CONTINUATION_NEEDS_HELP",
                        root_status="needs_help",
                    )
                    return None

            lease_expires_at = min(
                now + timedelta(seconds=lease_seconds),
                _parse_timestamp(chain.attention.deadline),
            )
            if lease_expires_at <= now:
                return None
            revision = write_action_chain_attention(
                connection,
                self._scope_value,
                chain,
                attention_row,
                state="claimed",
                worker_id=worker_id,
                lease_expires_at=lease_expires_at,
                not_before=attention_row.not_before,
                now=now,
                root_event_type="ACTION_CHAIN_CONTINUATION_CLAIMED",
                root_status="active",
            )
            return ActionChainClaim(
                attention_id=candidate.attention_id,
                plan_id=candidate.plan_id,
                plan_step_id=step.plan_step_id,
                logical_step_key=step.logical_step_key,
                attention_revision=revision,
                worker_id=worker_id,
                lease_expires_at=lease_expires_at,
            )

    def release(
        self,
        claim: ActionChainClaim,
        *,
        disposition: Literal["pending", "waiting"],
        now: datetime,
        not_before: datetime | None = None,
        reason: str | None = None,
        error_text: str | None = None,
    ) -> None:
        _require_utc_second(now, "now")
        if not_before is None:
            not_before = now
        _require_utc_second(not_before, "not_before")
        if reason is not None and not 1 <= len(reason) <= 512:
            raise ValueError("attention reason must contain 1..512 characters")
        if error_text is not None and not 1 <= len(error_text) <= 4096:
            raise ValueError("failure text must contain 1..4096 characters")

        with self._engine.begin() as connection:
            self._scope(connection)
            attention_row = _locked_attention(
                connection,
                self._scope_value,
                claim.attention_id,
            )
            if attention_row is None:
                raise ActionChainLeaseConflict("lease token does not exist in scope")
            if (
                attention_row.state != "claimed"
                or attention_row.lease_owner != claim.worker_id
                or int(attention_row.revision) != claim.attention_revision
                or attention_row.lease_expires_at != claim.lease_expires_at
                or claim.lease_expires_at <= now
            ):
                raise ActionChainLeaseConflict("lease token is stale or expired")

            chain = self._ledger.load_in_transaction(connection, claim.plan_id)
            step = next(
                (
                    item
                    for item in chain.steps
                    if item.plan_step_id == claim.plan_step_id
                    and item.logical_step_key == claim.logical_step_key
                ),
                None,
            )
            if step is None or step != _runnable_step(chain):
                raise ActionChainLeaseConflict("claimed step is no longer runnable")
            if not_before > _parse_timestamp(chain.attention.deadline):
                raise ValueError("not_before exceeds the action-chain deadline")

            needs_help = False
            if error_text is not None:
                needs_help = self._record_failure(
                    connection,
                    chain,
                    step.logical_step_key,
                    error_text=error_text,
                    now=now,
                )
            final_state = "waiting" if needs_help else disposition
            write_action_chain_attention(
                connection,
                self._scope_value,
                chain,
                attention_row,
                state=final_state,
                worker_id=None,
                lease_expires_at=None,
                not_before=not_before,
                now=now,
                reason=reason,
                root_event_type=(
                    "ACTION_CHAIN_CONTINUATION_NEEDS_HELP"
                    if needs_help
                    else "ACTION_CHAIN_CONTINUATION_RELEASED"
                ),
                root_status="needs_help" if needs_help else "active",
            )

    def _record_failure(
        self,
        connection: sa.Connection,
        chain: StoredActionChain,
        logical_step_key: str,
        *,
        error_text: str,
        now: datetime,
    ) -> bool:
        operation_id = action_chain_step_operation_id(chain.plan, logical_step_key)
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
        fingerprint = hashlib.sha256(error_text.encode("utf-8")).hexdigest()
        retry_count = (
            min(int(checkpoint.retry_count) + 1, 3)
            if checkpoint.last_error_fingerprint is not None
            and bytes(checkpoint.last_error_fingerprint).hex() == fingerprint
            else 1
        )
        needs_help = retry_count >= 3
        transition_timeline_thread(
            connection,
            self._scope_value,
            thread_id=operation_id,
            expected_revision=int(checkpoint.revision),
            checkpoint_id=f"checkpoint:{operation_id}:retry:{retry_count}",
            phase="action_chain_step_admitted",
            status="needs_help" if needs_help else "failed",
            event_type=(
                "ACTION_CHAIN_STEP_NEEDS_HELP"
                if needs_help
                else "ACTION_CHAIN_STEP_RETRY_FAILED"
            ),
            occurred_at=now,
            data={
                "error_fingerprint": fingerprint,
                "logical_step_key": logical_step_key,
                "plan_id": str(chain.plan_id),
                "retry_count": retry_count,
            },
            operation_id=operation_id,
            deadline=_parse_timestamp(chain.plan.deadline),
            retry_count=retry_count,
            last_error_fingerprint=fingerprint,
        )
        return needs_help


def write_action_chain_attention(
    connection: sa.Connection,
    scope: TimelineScope,
    chain: StoredActionChain,
    row: sa.Row,
    *,
    state: Literal["pending", "claimed", "waiting", "completed"],
    worker_id: str | None,
    lease_expires_at: datetime | None,
    not_before: datetime,
    now: datetime,
    root_event_type: str,
    root_status: Literal["active", "completed", "needs_help"],
    reason: str | None = None,
    transition_context: dict[str, object] | None = None,
) -> int:
    """CAS continuation state and append its matching root timeline event."""

    revision = int(row.revision) + 1
    payload_data = dict(row.payload)
    payload_data.update(
        {
            "state": state,
            "reason": row.reason if reason is None else reason,
            "not_before": _format_timestamp(not_before),
            "lease_owner": worker_id,
            "lease_expires_at": (
                None
                if lease_expires_at is None
                else _format_timestamp(lease_expires_at)
            ),
        }
    )
    attention = AttentionWake.model_validate(payload_data)
    payload = attention.model_dump(mode="json")
    result = connection.execute(
        sa.update(attention_items)
        .where(
            attention_items.c.tenant_id == scope.tenant_id,
            attention_items.c.installation_id == scope.installation_id,
            attention_items.c.attention_id == row.attention_id,
            attention_items.c.revision == row.revision,
            attention_items.c.state == row.state,
            attention_items.c.lease_owner.is_not_distinct_from(row.lease_owner),
            attention_items.c.lease_expires_at.is_not_distinct_from(
                row.lease_expires_at
            ),
        )
        .values(
            state=state,
            reason=attention.reason,
            not_before=not_before,
            lease_owner=worker_id,
            lease_expires_at=lease_expires_at,
            payload=payload,
            payload_sha256=bytes.fromhex(canonical_sha256(attention)),
            revision=revision,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        raise ActionChainLeaseConflict("attention lease changed")

    root_thread_id = action_chain_thread_id(chain.plan)
    checkpoint = connection.execute(
        sa.select(operational_checkpoints)
        .where(
            operational_checkpoints.c.tenant_id == scope.tenant_id,
            operational_checkpoints.c.installation_id == scope.installation_id,
            operational_checkpoints.c.thread_id == root_thread_id,
        )
        .with_for_update()
    ).one()
    event_data: dict[str, object] = {
        "attention": payload,
        "attention_payload_sha256": canonical_sha256(attention),
        "attention_revision": revision,
        "plan_id": str(chain.plan_id),
    }
    if transition_context is not None:
        event_data["transition_context"] = transition_context
    transition_timeline_thread(
        connection,
        scope,
        thread_id=root_thread_id,
        expected_revision=int(checkpoint.revision),
        checkpoint_id=f"checkpoint:{root_thread_id}:continuation:{revision}",
        phase=(
            "action_chain_needs_help"
            if root_status == "needs_help"
            else "action_chain_continuation"
        ),
        status=root_status,
        event_type=root_event_type,
        occurred_at=now,
        data=event_data,
        operation_id=root_thread_id,
        deadline=_parse_timestamp(chain.plan.deadline),
        retry_count=0,
        last_error_fingerprint=None,
    )
    return revision


def _locked_attention(
    connection: sa.Connection, scope: TimelineScope, attention_id: UUID
) -> sa.Row | None:
    return connection.execute(
        sa.select(attention_items)
        .where(
            attention_items.c.tenant_id == scope.tenant_id,
            attention_items.c.installation_id == scope.installation_id,
            attention_items.c.attention_id == attention_id,
        )
        .with_for_update()
    ).one_or_none()


def _runnable_step(chain: StoredActionChain) -> StoredActionChainStep | None:
    states = {step.plan_step_id: step.state for step in chain.steps}
    return next(
        (
            step
            for step in chain.steps
            if step.state == "ready"
            and all(
                states.get(dependency) == "succeeded"
                for dependency in step.dependency_step_ids
            )
        ),
        None,
    )


def _require_worker(worker_id: str) -> None:
    if (
        not 1 <= len(worker_id) <= 192
        or not worker_id[0].isalnum()
        or any(
            not (character.isalnum() or character in ":._/@+-=")
            for character in worker_id
        )
    ):
        raise ValueError("worker_id is not a valid identifier")


__all__ = [
    "ActionChainClaim",
    "ActionChainLeaseConflict",
    "ActionChainLeaseError",
    "PostgresActionChainLeases",
    "write_action_chain_attention",
]
