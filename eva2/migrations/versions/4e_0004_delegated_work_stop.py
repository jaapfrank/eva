"""Persist an owner stop request across an active delegated model call.

Revision ID: 4e_0004_delegated_work_stop
Revises: 4e_0003_delegated_work_runs
"""

from __future__ import annotations

from alembic import op

revision = "4e_0004_delegated_work_stop"
down_revision = "4e_0003_delegated_work_runs"
branch_labels = None
depends_on = None

_TABLE = "eva_core.delegated_work_runs"


def upgrade() -> None:
    op.execute("SET LOCAL ROLE eva_migrator")
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN stop_requested_at timestamptz(0)")
    op.execute(
        f"ALTER TABLE {_TABLE} DROP CONSTRAINT ck_delegated_work_runs_state_payload"
    )
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT ck_delegated_work_runs_time")
    op.execute(
        f"""
        ALTER TABLE {_TABLE} ADD CONSTRAINT ck_delegated_work_runs_state_payload CHECK (
            (state='admitted' AND call_id IS NULL AND call_started_at IS NULL
                AND stop_requested_at IS NULL AND completed_at IS NULL
                AND result_payload IS NULL AND result_sha256 IS NULL
                AND usage_payload IS NULL AND usage_sha256 IS NULL
                AND error_code IS NULL AND error_fingerprint IS NULL)
            OR (state='call_started' AND call_id IS NOT NULL
                AND call_started_at IS NOT NULL AND completed_at IS NULL
                AND result_payload IS NULL AND result_sha256 IS NULL
                AND usage_payload IS NULL AND usage_sha256 IS NULL
                AND error_code IS NULL AND error_fingerprint IS NULL)
            OR (state IN ('succeeded','verified') AND call_id IS NOT NULL
                AND call_started_at IS NOT NULL AND stop_requested_at IS NULL
                AND completed_at IS NOT NULL AND result_payload IS NOT NULL
                AND result_sha256 IS NOT NULL AND usage_payload IS NOT NULL
                AND usage_sha256 IS NOT NULL AND error_code IS NULL
                AND error_fingerprint IS NULL)
            OR (state IN ('failed','ambiguous') AND call_id IS NOT NULL
                AND call_started_at IS NOT NULL AND stop_requested_at IS NULL
                AND completed_at IS NOT NULL
                AND result_payload IS NULL AND result_sha256 IS NULL
                AND usage_payload IS NOT NULL AND usage_sha256 IS NOT NULL
                AND error_code IS NOT NULL AND error_fingerprint IS NOT NULL)
            OR (state='needs_help' AND call_id IS NULL
                AND call_started_at IS NULL AND stop_requested_at IS NULL
                AND completed_at IS NOT NULL AND result_payload IS NULL
                AND result_sha256 IS NULL AND usage_payload IS NULL
                AND usage_sha256 IS NULL AND error_code IS NOT NULL
                AND error_fingerprint IS NOT NULL)
            OR (state='stopped' AND completed_at IS NOT NULL
                AND result_payload IS NULL AND result_sha256 IS NULL
                AND error_code IS NULL AND error_fingerprint IS NULL
                AND ((call_id IS NULL AND call_started_at IS NULL
                    AND stop_requested_at IS NULL
                    AND usage_payload IS NULL AND usage_sha256 IS NULL)
                OR (call_id IS NOT NULL AND call_started_at IS NOT NULL
                    AND stop_requested_at IS NOT NULL
                    AND usage_payload IS NOT NULL AND usage_sha256 IS NOT NULL)))
        )
        """
    )
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
    op.execute(
        f"GRANT UPDATE (stop_requested_at) ON TABLE {_TABLE} TO eva_runtime, eva_dbos"
    )


def downgrade() -> None:
    op.execute("SET LOCAL ROLE eva_migrator")
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM {_TABLE} WHERE stop_requested_at IS NOT NULL) THEN
                RAISE EXCEPTION 'delegated stop requests must be empty before downgrade';
            END IF;
        END
        $$
        """
    )
    op.execute(
        f"ALTER TABLE {_TABLE} DROP CONSTRAINT ck_delegated_work_runs_state_payload"
    )
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT ck_delegated_work_runs_time")
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN stop_requested_at")
    op.execute(
        f"""
        ALTER TABLE {_TABLE} ADD CONSTRAINT ck_delegated_work_runs_state_payload CHECK (
            (state='admitted' AND call_id IS NULL AND call_started_at IS NULL
                AND completed_at IS NULL AND result_payload IS NULL
                AND result_sha256 IS NULL AND usage_payload IS NULL
                AND usage_sha256 IS NULL AND error_code IS NULL
                AND error_fingerprint IS NULL)
            OR (state='call_started' AND call_id IS NOT NULL
                AND call_started_at IS NOT NULL AND completed_at IS NULL
                AND result_payload IS NULL AND result_sha256 IS NULL
                AND usage_payload IS NULL AND usage_sha256 IS NULL
                AND error_code IS NULL AND error_fingerprint IS NULL)
            OR (state IN ('succeeded','verified') AND call_id IS NOT NULL
                AND call_started_at IS NOT NULL AND completed_at IS NOT NULL
                AND result_payload IS NOT NULL AND result_sha256 IS NOT NULL
                AND usage_payload IS NOT NULL AND usage_sha256 IS NOT NULL
                AND error_code IS NULL AND error_fingerprint IS NULL)
            OR (state IN ('failed','ambiguous') AND call_id IS NOT NULL
                AND call_started_at IS NOT NULL AND completed_at IS NOT NULL
                AND result_payload IS NULL AND result_sha256 IS NULL
                AND usage_payload IS NOT NULL AND usage_sha256 IS NOT NULL
                AND error_code IS NOT NULL AND error_fingerprint IS NOT NULL)
            OR (state='needs_help' AND call_id IS NULL
                AND call_started_at IS NULL AND completed_at IS NOT NULL
                AND result_payload IS NULL AND result_sha256 IS NULL
                AND usage_payload IS NULL AND usage_sha256 IS NULL
                AND error_code IS NOT NULL AND error_fingerprint IS NOT NULL)
            OR (state='stopped' AND completed_at IS NOT NULL
                AND call_id IS NULL AND call_started_at IS NULL
                AND result_payload IS NULL AND result_sha256 IS NULL
                AND usage_payload IS NULL AND usage_sha256 IS NULL
                AND error_code IS NULL AND error_fingerprint IS NULL)
        )
        """
    )
    op.execute(
        f"""
        ALTER TABLE {_TABLE} ADD CONSTRAINT ck_delegated_work_runs_time CHECK (
            deadline >= created_at AND retention_until >= created_at
            AND updated_at >= created_at
            AND (call_started_at IS NULL OR call_started_at >= created_at)
            AND (completed_at IS NULL OR completed_at >= created_at)
            AND (call_started_at IS NULL OR completed_at IS NULL
                OR completed_at >= call_started_at)
        )
        """
    )
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
