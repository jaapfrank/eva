"""Bind delegated work directly to a verified private owner context.

Revision ID: 4e_0007_delegated_private
Revises: 4e_0006_memory_outbox_erasure
"""

from __future__ import annotations

from alembic import op


revision = "4e_0007_delegated_private"
down_revision = "4e_0006_memory_outbox_erasure"
branch_labels = None
depends_on = None

_TABLE = "eva_core.delegated_work_runs"


def upgrade() -> None:
    op.execute("SET LOCAL ROLE eva_migrator")
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN lease_owner text COLLATE \"C\"")
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN lease_expires_at timestamptz(0)")
    # Preserve an in-flight pre-call lease when upgrading a non-empty install.
    op.execute(
        f"""
        UPDATE {_TABLE} AS work
        SET lease_owner = attention.lease_owner,
            lease_expires_at = attention.lease_expires_at
        FROM eva_core.attention_items AS attention
        WHERE attention.tenant_id = work.tenant_id
          AND attention.installation_id = work.installation_id
          AND attention.attention_id = work.attention_id
          AND attention.state = 'claimed'
          AND work.state IN ('admitted', 'call_started')
        """
    )
    op.execute(
        f"ALTER TABLE {_TABLE} DROP CONSTRAINT fk_delegated_work_runs_attention"
    )
    op.execute(
        f"ALTER TABLE {_TABLE} DROP CONSTRAINT fk_delegated_work_runs_generation"
    )
    op.execute(
        f"""
        ALTER TABLE {_TABLE} ADD CONSTRAINT ck_delegated_work_runs_lease CHECK (
            (lease_owner IS NULL AND lease_expires_at IS NULL)
            OR (state IN ('admitted','call_started')
                AND length(lease_owner) BETWEEN 1 AND 192
                AND lease_expires_at IS NOT NULL)
        )
        """
    )
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT ck_delegated_work_runs_time")
    op.execute(
        f"""
        ALTER TABLE {_TABLE} ADD CONSTRAINT ck_delegated_work_runs_time CHECK (
            deadline >= created_at AND retention_until >= created_at
            AND updated_at >= created_at
            AND (call_started_at IS NULL OR call_started_at >= created_at)
            AND (stop_requested_at IS NULL OR (call_started_at IS NOT NULL
                AND stop_requested_at >= call_started_at))
            AND (completed_at IS NULL OR completed_at >= created_at)
            AND (stop_requested_at IS NULL OR completed_at IS NULL
                OR completed_at >= stop_requested_at)
            AND (call_started_at IS NULL OR completed_at IS NULL
                OR completed_at >= call_started_at)
            AND (lease_expires_at IS NULL OR lease_expires_at >= updated_at)
        )
        """
    )
    op.execute(
        f"GRANT UPDATE (lease_owner, lease_expires_at) ON TABLE {_TABLE} "
        "TO eva_runtime, eva_dbos"
    )


def downgrade() -> None:
    op.execute("SET LOCAL ROLE eva_migrator")
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM {_TABLE} work
                LEFT JOIN eva_core.attention_items attention
                  ON attention.tenant_id = work.tenant_id
                 AND attention.installation_id = work.installation_id
                 AND attention.attention_id = work.attention_id
                LEFT JOIN eva_core.episode_generations generation
                  ON generation.tenant_id = work.tenant_id
                 AND generation.installation_id = work.installation_id
                 AND generation.episode_id = work.storage_episode_id
                 AND generation.generation = work.generation
                WHERE attention.attention_id IS NULL
                   OR generation.episode_id IS NULL
                   OR work.lease_owner IS DISTINCT FROM attention.lease_owner
                   OR work.lease_expires_at IS DISTINCT FROM attention.lease_expires_at
            ) THEN
                RAISE EXCEPTION
                    'delegated private contexts cannot be represented by the old schema';
            END IF;
        END
        $$
        """
    )
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT ck_delegated_work_runs_time")
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT ck_delegated_work_runs_lease")
    op.execute(
        f"""
        ALTER TABLE {_TABLE} ADD CONSTRAINT ck_delegated_work_runs_time CHECK (
            deadline >= created_at AND retention_until >= created_at
            AND updated_at >= created_at
            AND (call_started_at IS NULL OR call_started_at >= created_at)
            AND (stop_requested_at IS NULL OR (call_started_at IS NOT NULL
                AND stop_requested_at >= call_started_at))
            AND (completed_at IS NULL OR completed_at >= created_at)
            AND (stop_requested_at IS NULL OR completed_at IS NULL
                OR completed_at >= stop_requested_at)
            AND (call_started_at IS NULL OR completed_at IS NULL
                OR completed_at >= call_started_at)
        )
        """
    )
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN lease_expires_at")
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN lease_owner")
    op.execute(
        f"""
        ALTER TABLE {_TABLE} ADD CONSTRAINT fk_delegated_work_runs_attention
        FOREIGN KEY (tenant_id, installation_id, attention_id)
        REFERENCES eva_core.attention_items
            (tenant_id, installation_id, attention_id) ON DELETE CASCADE
        """
    )
    op.execute(
        f"""
        ALTER TABLE {_TABLE} ADD CONSTRAINT fk_delegated_work_runs_generation
        FOREIGN KEY (tenant_id, installation_id, storage_episode_id, generation)
        REFERENCES eva_core.episode_generations
            (tenant_id, installation_id, episode_id, generation) ON DELETE CASCADE
        """
    )
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
