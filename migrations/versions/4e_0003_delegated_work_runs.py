"""Add private durable authority for one bounded child-model run.

Revision ID: 4e_0003_delegated_work_runs
Revises: 4e_0002_delegated_work
"""

from __future__ import annotations

from alembic import op

revision = "4e_0003_delegated_work_runs"
down_revision = "4e_0002_delegated_work"
branch_labels = None
depends_on = None


_TABLE = "eva_core.delegated_work_runs"


def upgrade() -> None:
    # Keep historical migrations immutable: this revision owns its complete DDL
    # and never imports the mutable current-catalog projection.
    op.execute("SET LOCAL ROLE eva_migrator")
    op.execute(
        f"""
        CREATE TABLE {_TABLE} (
            tenant_id uuid NOT NULL,
            installation_id uuid NOT NULL,
            work_id text COLLATE "C" NOT NULL,
            attention_id uuid NOT NULL,
            storage_episode_id uuid NOT NULL,
            generation integer NOT NULL,
            owner_principal_id uuid NOT NULL,
            principal_binding_id uuid NOT NULL,
            parent_task_id text COLLATE "C" NOT NULL,
            title text COLLATE "C" NOT NULL,
            summary text COLLATE "C" NOT NULL,
            worker_name text COLLATE "C" NOT NULL,
            worker_role text COLLATE "C" NOT NULL,
            state text COLLATE "C" NOT NULL,
            claim_count smallint NOT NULL,
            call_id text COLLATE "C",
            spec_payload jsonb NOT NULL,
            spec_sha256 bytea NOT NULL,
            result_payload jsonb,
            result_sha256 bytea,
            usage_payload jsonb,
            usage_sha256 bytea,
            error_code text COLLATE "C",
            error_fingerprint bytea,
            revision bigint NOT NULL,
            deadline timestamptz(0) NOT NULL,
            call_started_at timestamptz(0),
            completed_at timestamptz(0),
            retention_policy_id text COLLATE "C" NOT NULL,
            retention_until timestamptz(0) NOT NULL,
            created_at timestamptz(0) NOT NULL,
            updated_at timestamptz(0) NOT NULL,
            CONSTRAINT pk_delegated_work_runs
                PRIMARY KEY (tenant_id, installation_id, work_id),
            CONSTRAINT uq_delegated_work_runs_attention
                UNIQUE (tenant_id, installation_id, attention_id),
            CONSTRAINT fk_delegated_work_runs_attention
                FOREIGN KEY (tenant_id, installation_id, attention_id)
                REFERENCES eva_core.attention_items
                    (tenant_id, installation_id, attention_id)
                ON DELETE CASCADE,
            CONSTRAINT fk_delegated_work_runs_generation
                FOREIGN KEY (tenant_id, installation_id, storage_episode_id, generation)
                REFERENCES eva_core.episode_generations
                    (tenant_id, installation_id, episode_id, generation)
                ON DELETE CASCADE,
            CONSTRAINT fk_delegated_work_runs_owner
                FOREIGN KEY (tenant_id, owner_principal_id)
                REFERENCES eva_core.principals (tenant_id, principal_id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_delegated_work_runs_binding
                FOREIGN KEY (tenant_id, principal_binding_id)
                REFERENCES eva_core.principal_bindings
                    (tenant_id, principal_binding_id)
                ON DELETE RESTRICT,
            CONSTRAINT ck_delegated_work_runs_state CHECK (
                state IN ('admitted','call_started','succeeded','verified',
                    'failed','ambiguous','needs_help','stopped')
            ),
            CONSTRAINT ck_delegated_work_runs_counters CHECK (
                claim_count BETWEEN 0 AND 3 AND generation >= 1 AND revision >= 1
            ),
            CONSTRAINT ck_delegated_work_runs_text_bounds CHECK (
                length(work_id) BETWEEN 1 AND 192
                AND length(parent_task_id) BETWEEN 1 AND 192
                AND length(title) BETWEEN 1 AND 120
                AND length(summary) BETWEEN 1 AND 240
                AND length(worker_name) BETWEEN 1 AND 32
                AND length(worker_role) BETWEEN 1 AND 80
            ),
            CONSTRAINT ck_delegated_work_runs_hashes CHECK (
                octet_length(spec_sha256)=32
                AND (result_sha256 IS NULL OR octet_length(result_sha256)=32)
                AND (usage_sha256 IS NULL OR octet_length(usage_sha256)=32)
                AND (error_fingerprint IS NULL OR octet_length(error_fingerprint)=32)
            ),
            CONSTRAINT ck_delegated_work_runs_state_payload CHECK (
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
            ),
            CONSTRAINT ck_delegated_work_runs_time CHECK (
                deadline >= created_at AND retention_until >= created_at
                AND updated_at >= created_at
                AND (call_started_at IS NULL OR call_started_at >= created_at)
                AND (completed_at IS NULL OR completed_at >= created_at)
                AND (call_started_at IS NULL OR completed_at IS NULL
                    OR completed_at >= call_started_at)
            )
        )
        """
    )
    op.execute(
        f"""
        CREATE INDEX ix_delegated_work_runs__board ON {_TABLE}
            (tenant_id, installation_id, owner_principal_id, state, updated_at)
        """
    )

    predicate = (
        "tenant_id::text = current_setting('eva.tenant_id', true) "
        "AND installation_id::text = current_setting('eva.installation_id', true)"
    )
    op.execute(f"ALTER TABLE {_TABLE} OWNER TO eva_migrator")
    op.execute(f"REVOKE ALL ON TABLE {_TABLE} FROM PUBLIC, eva_runtime, eva_dbos")
    op.execute(f"GRANT SELECT, INSERT ON TABLE {_TABLE} TO eva_runtime, eva_dbos")
    columns = (
        "state, claim_count, call_id, result_payload, result_sha256, "
        "usage_payload, usage_sha256, error_code, error_fingerprint, revision, "
        "call_started_at, completed_at, updated_at"
    )
    op.execute(f"GRANT UPDATE ({columns}) ON TABLE {_TABLE} TO eva_runtime, eva_dbos")
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY delegated_work_runs_scope ON {_TABLE} "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )


def downgrade() -> None:
    op.execute("SET LOCAL ROLE eva_migrator")
    # A schema rollback may never silently orphan its durable parent cards.
    # The table normally FORCEs RLS even for its owner. Temporarily remove that
    # owner restriction inside the migration transaction so the global
    # precondition sees every tenant; a refusal rolls this ALTER back as well.
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM {_TABLE}) THEN
                RAISE EXCEPTION
                    'delegated_work_runs must be empty before downgrade';
            END IF;
        END
        $$
        """
    )
    op.execute(f"DROP TABLE {_TABLE}")
