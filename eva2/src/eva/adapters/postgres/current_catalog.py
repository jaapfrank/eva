"""Current Alembic metadata projected over the sealed historical 4D catalog."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from eva.adapters.postgres.catalog import metadata as sealed_metadata

OLD_ATTENTION_KIND_CHECK = (
    "kind IN ('ingress','continuation','deadline','external_signal',"
    "'delivery_recovery')"
)
CURRENT_ATTENTION_KIND_CHECK = (
    "kind IN ('ingress','continuation','deadline','external_signal',"
    "'delivery_recovery','delegated_work')"
)

metadata = sa.MetaData()
for _table in sealed_metadata.tables.values():
    _table.to_metadata(metadata)

_attention = metadata.tables["eva_core.attention_items"]
_kind_constraints = [
    constraint
    for constraint in _attention.constraints
    if isinstance(constraint, sa.CheckConstraint)
    and constraint.name == "ck_attention_items_kind"
]
if len(_kind_constraints) != 1 or str(_kind_constraints[0].sqltext) != (
    OLD_ATTENTION_KIND_CHECK
):
    raise RuntimeError("sealed attention kind constraint differs")
_attention.constraints.remove(_kind_constraints[0])
_attention.append_constraint(
    sa.CheckConstraint(
        CURRENT_ATTENTION_KIND_CHECK,
        name="ck_attention_items_kind",
    )
)

# This is a post-freeze extension.  The sealed 4D catalog remains byte-for-byte
# unchanged; current metadata adds one private authority table for durable child
# work instead of overloading the generic AttentionWake payload with prompts and
# results.
delegated_work_runs = sa.Table(
    "delegated_work_runs",
    metadata,
    sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("work_id", sa.Text(collation="C"), nullable=False),
    sa.Column("attention_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("storage_episode_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("generation", sa.Integer(), nullable=False),
    sa.Column("owner_principal_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("principal_binding_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("parent_task_id", sa.Text(collation="C"), nullable=False),
    sa.Column("title", sa.Text(collation="C"), nullable=False),
    sa.Column("summary", sa.Text(collation="C"), nullable=False),
    sa.Column("worker_name", sa.Text(collation="C"), nullable=False),
    sa.Column("worker_role", sa.Text(collation="C"), nullable=False),
    sa.Column("state", sa.Text(collation="C"), nullable=False),
    sa.Column("claim_count", sa.SmallInteger(), nullable=False),
    sa.Column("lease_owner", sa.Text(collation="C"), nullable=True),
    sa.Column(
        "lease_expires_at",
        postgresql.TIMESTAMP(timezone=True, precision=0),
        nullable=True,
    ),
    sa.Column("call_id", sa.Text(collation="C"), nullable=True),
    sa.Column("spec_payload", postgresql.JSONB(none_as_null=True), nullable=False),
    sa.Column("spec_sha256", postgresql.BYTEA(), nullable=False),
    sa.Column("result_payload", postgresql.JSONB(none_as_null=True), nullable=True),
    sa.Column("result_sha256", postgresql.BYTEA(), nullable=True),
    sa.Column("usage_payload", postgresql.JSONB(none_as_null=True), nullable=True),
    sa.Column("usage_sha256", postgresql.BYTEA(), nullable=True),
    sa.Column("error_code", sa.Text(collation="C"), nullable=True),
    sa.Column("error_fingerprint", postgresql.BYTEA(), nullable=True),
    sa.Column("revision", sa.BigInteger(), nullable=False),
    sa.Column(
        "deadline", postgresql.TIMESTAMP(timezone=True, precision=0), nullable=False
    ),
    sa.Column(
        "call_started_at",
        postgresql.TIMESTAMP(timezone=True, precision=0),
        nullable=True,
    ),
    sa.Column(
        "stop_requested_at",
        postgresql.TIMESTAMP(timezone=True, precision=0),
        nullable=True,
    ),
    sa.Column(
        "completed_at",
        postgresql.TIMESTAMP(timezone=True, precision=0),
        nullable=True,
    ),
    sa.Column("retention_policy_id", sa.Text(collation="C"), nullable=False),
    sa.Column(
        "retention_until",
        postgresql.TIMESTAMP(timezone=True, precision=0),
        nullable=False,
    ),
    sa.Column(
        "created_at", postgresql.TIMESTAMP(timezone=True, precision=0), nullable=False
    ),
    sa.Column(
        "updated_at", postgresql.TIMESTAMP(timezone=True, precision=0), nullable=False
    ),
    sa.PrimaryKeyConstraint(
        "tenant_id",
        "installation_id",
        "work_id",
        name="pk_delegated_work_runs",
    ),
    sa.UniqueConstraint(
        "tenant_id",
        "installation_id",
        "attention_id",
        name="uq_delegated_work_runs_attention",
    ),
    sa.ForeignKeyConstraint(
        ["tenant_id", "owner_principal_id"],
        ["eva_core.principals.tenant_id", "eva_core.principals.principal_id"],
        name="fk_delegated_work_runs_owner",
        ondelete="RESTRICT",
    ),
    sa.ForeignKeyConstraint(
        ["tenant_id", "principal_binding_id"],
        [
            "eva_core.principal_bindings.tenant_id",
            "eva_core.principal_bindings.principal_binding_id",
        ],
        name="fk_delegated_work_runs_binding",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint(
        "state IN ('admitted','call_started','succeeded','verified','failed',"
        "'ambiguous','needs_help','stopped')",
        name="ck_delegated_work_runs_state",
    ),
    sa.CheckConstraint(
        "claim_count BETWEEN 0 AND 3 AND generation >= 1 AND revision >= 1",
        name="ck_delegated_work_runs_counters",
    ),
    sa.CheckConstraint(
        "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
        "(state IN ('admitted','call_started') "
        "AND length(lease_owner) BETWEEN 1 AND 192 "
        "AND lease_expires_at IS NOT NULL)",
        name="ck_delegated_work_runs_lease",
    ),
    sa.CheckConstraint(
        "length(work_id) BETWEEN 1 AND 192 "
        "AND length(parent_task_id) BETWEEN 1 AND 192 "
        "AND length(title) BETWEEN 1 AND 120 "
        "AND length(summary) BETWEEN 1 AND 240 "
        "AND length(worker_name) BETWEEN 1 AND 32 "
        "AND length(worker_role) BETWEEN 1 AND 80",
        name="ck_delegated_work_runs_text_bounds",
    ),
    sa.CheckConstraint(
        "octet_length(spec_sha256)=32 "
        "AND (result_sha256 IS NULL OR octet_length(result_sha256)=32) "
        "AND (usage_sha256 IS NULL OR octet_length(usage_sha256)=32) "
        "AND (error_fingerprint IS NULL OR octet_length(error_fingerprint)=32)",
        name="ck_delegated_work_runs_hashes",
    ),
    sa.CheckConstraint(
        "(state='admitted' AND call_id IS NULL AND call_started_at IS NULL "
        "AND stop_requested_at IS NULL "
        "AND completed_at IS NULL AND result_payload IS NULL "
        "AND result_sha256 IS NULL AND usage_payload IS NULL "
        "AND usage_sha256 IS NULL AND error_code IS NULL "
        "AND error_fingerprint IS NULL) OR "
        "(state='call_started' AND call_id IS NOT NULL "
        "AND call_started_at IS NOT NULL AND completed_at IS NULL "
        "AND result_payload IS NULL AND result_sha256 IS NULL "
        "AND usage_payload IS NULL AND usage_sha256 IS NULL "
        "AND error_code IS NULL AND error_fingerprint IS NULL) OR "
        "(state IN ('succeeded','verified') AND call_id IS NOT NULL "
        "AND call_started_at IS NOT NULL AND stop_requested_at IS NULL "
        "AND completed_at IS NOT NULL "
        "AND result_payload IS NOT NULL AND result_sha256 IS NOT NULL "
        "AND usage_payload IS NOT NULL AND usage_sha256 IS NOT NULL "
        "AND error_code IS NULL AND error_fingerprint IS NULL) OR "
        "(state IN ('failed','ambiguous') AND call_id IS NOT NULL "
        "AND call_started_at IS NOT NULL AND stop_requested_at IS NULL "
        "AND completed_at IS NOT NULL "
        "AND result_payload IS NULL AND result_sha256 IS NULL "
        "AND usage_payload IS NOT NULL AND usage_sha256 IS NOT NULL "
        "AND error_code IS NOT NULL AND error_fingerprint IS NOT NULL) OR "
        "(state='needs_help' AND call_id IS NULL AND call_started_at IS NULL "
        "AND stop_requested_at IS NULL "
        "AND completed_at IS NOT NULL AND result_payload IS NULL "
        "AND result_sha256 IS NULL AND usage_payload IS NULL "
        "AND usage_sha256 IS NULL AND error_code IS NOT NULL "
        "AND error_fingerprint IS NOT NULL) OR "
        "(state='stopped' AND completed_at IS NOT NULL "
        "AND result_payload IS NULL AND result_sha256 IS NULL "
        "AND error_code IS NULL AND error_fingerprint IS NULL "
        "AND ((call_id IS NULL AND call_started_at IS NULL "
        "AND stop_requested_at IS NULL "
        "AND usage_payload IS NULL AND usage_sha256 IS NULL) "
        "OR (call_id IS NOT NULL AND call_started_at IS NOT NULL "
        "AND stop_requested_at IS NOT NULL "
        "AND usage_payload IS NOT NULL AND usage_sha256 IS NOT NULL)))",
        name="ck_delegated_work_runs_state_payload",
    ),
    sa.CheckConstraint(
        "deadline >= created_at AND retention_until >= created_at "
        "AND updated_at >= created_at "
        "AND (call_started_at IS NULL OR call_started_at >= created_at) "
        "AND (stop_requested_at IS NULL OR (call_started_at IS NOT NULL "
        "AND stop_requested_at >= call_started_at)) "
        "AND (completed_at IS NULL OR completed_at >= created_at) "
        "AND (stop_requested_at IS NULL OR completed_at IS NULL "
        "OR completed_at >= stop_requested_at) "
        "AND (call_started_at IS NULL OR completed_at IS NULL "
        "OR completed_at >= call_started_at) "
        "AND (lease_expires_at IS NULL OR lease_expires_at >= updated_at)",
        name="ck_delegated_work_runs_time",
    ),
    schema="eva_core",
)
sa.Index(
    "ix_delegated_work_runs__board",
    delegated_work_runs.c.tenant_id,
    delegated_work_runs.c.installation_id,
    delegated_work_runs.c.owner_principal_id,
    delegated_work_runs.c.state,
    delegated_work_runs.c.updated_at,
)

del _attention, _kind_constraints, _table


__all__ = [
    "CURRENT_ATTENTION_KIND_CHECK",
    "OLD_ATTENTION_KIND_CHECK",
    "delegated_work_runs",
    "metadata",
]
