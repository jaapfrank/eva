"""Create the minimal episode head and immutable event log for tranche 4C."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, UUID


revision = "4b_0002_episode_atomicity"
down_revision = "4b_0001_tenant_scope"
branch_labels = None
depends_on = None


STATES = (
    "'accepted', 'planning', 'executing', 'reporting', 'completed', "
    "'partially_completed', 'waiting_user', 'waiting_external', "
    "'blocked', 'cancelled'"
)
SCOPE_PREDICATE = (
    "tenant_id::text = current_setting('eva.tenant_id', true) "
    "AND installation_id::text = current_setting('eva.installation_id', true)"
)

EPISODE_STATE_KEYS = (
    "schema_version",
    "contract",
    "episode_id",
    "tenant_id",
    "principal_id",
    "conversation_id",
    "ingress_request_id",
    "state",
    "current_generation",
    "supersedes_generation",
    "last_event_id",
    "updated_at",
)
EPISODE_STATE_STRING_KEYS = (
    "contract",
    "episode_id",
    "tenant_id",
    "principal_id",
    "conversation_id",
    "ingress_request_id",
    "state",
    "last_event_id",
    "updated_at",
)
EPISODE_STATE_UUID_KEYS = (
    "episode_id",
    "tenant_id",
    "principal_id",
    "conversation_id",
    "ingress_request_id",
    "last_event_id",
)


def episode_state_snapshot_predicate(
    payload: str,
    *,
    state_column: str,
    generation_column: str,
    event_column: str | None,
    occurred_at_column: str | None,
) -> str:
    """Build the frozen, two-valued EpisodeState/v1 JSON CHECK."""

    keys_sql = ", ".join(f"'{key}'" for key in EPISODE_STATE_KEYS)
    parts = [
        f"jsonb_typeof({payload}) = 'object'",
        f"{payload} ?& ARRAY[{keys_sql}]::text[]",
        f"({payload} - ARRAY[{keys_sql}]::text[]) = '{{}}'::jsonb",
        f"jsonb_typeof({payload} -> 'schema_version') = 'number'",
        f"{payload} ->> 'schema_version' = '1'",
        *(
            f"jsonb_typeof({payload} -> '{key}') = 'string'"
            for key in EPISODE_STATE_STRING_KEYS
        ),
        f"jsonb_typeof({payload} -> 'current_generation') = 'number'",
        f"({payload} ->> 'current_generation') ~ '^[1-9][0-9]*$'",
        (f"jsonb_typeof({payload} -> 'supersedes_generation') IN ('number', 'null')"),
        f"{payload} ->> 'contract' = 'EpisodeState/v1'",
        f"{payload} ->> 'state' = {state_column}",
        (f"({payload} ->> 'current_generation')::integer = {generation_column}"),
        (
            f"(({generation_column} = 1 AND "
            f"{payload} -> 'supersedes_generation' = 'null'::jsonb) OR "
            f"({generation_column} > 1 AND "
            f"jsonb_typeof({payload} -> 'supersedes_generation') = 'number' "
            f"AND ({payload} ->> 'supersedes_generation') ~ '^[1-9][0-9]*$' "
            f"AND ({payload} ->> 'supersedes_generation')::integer = "
            f"{generation_column} - 1))"
        ),
        *(
            f"{payload} ->> '{key}' = (({payload} ->> '{key}')::uuid)::text"
            for key in EPISODE_STATE_UUID_KEYS
        ),
        (
            f"{payload} ->> 'updated_at' ~ "
            r"'^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'"
        ),
        f"{payload} ->> 'episode_id' = episode_id::text",
        f"{payload} ->> 'tenant_id' = tenant_id::text",
    ]
    if event_column is not None:
        parts.append(f"{payload} ->> 'last_event_id' = {event_column}::text")
    if occurred_at_column is not None:
        parts.append(
            f"{payload} ->> 'updated_at' = "
            f"to_char({occurred_at_column} AT TIME ZONE 'UTC', "
            '\'YYYY-MM-DD"T"HH24:MI:SS"Z"\')'
        )
    return "(" + " AND ".join(parts) + ") IS TRUE"


def upgrade() -> None:
    op.create_table(
        "episodes",
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("episode_id", UUID(as_uuid=True), nullable=False),
        sa.Column("principal_id", UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("ingress_request_id", UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("current_generation", sa.Integer(), nullable=False),
        sa.Column("supersedes_generation", sa.Integer(), nullable=True),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("last_event_id", UUID(as_uuid=True), nullable=False),
        sa.Column("state_contract", sa.String(64), nullable=False),
        sa.Column("state_schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("state_payload_sha256", BYTEA(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id", "installation_id", "episode_id", name="pk_episodes"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "installation_id"],
            [
                "eva_core.installations.tenant_id",
                "eva_core.installations.installation_id",
            ],
            name="fk_episodes_installation",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(f"state IN ({STATES})", name="ck_episodes_state"),
        sa.CheckConstraint(
            "current_generation >= 1 AND ((current_generation = 1 AND supersedes_generation IS NULL) OR (current_generation > 1 AND supersedes_generation = current_generation - 1))",
            name="ck_episodes_generation_chain",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_episodes_revision"),
        sa.CheckConstraint(
            "state_contract = 'EpisodeState/v1' AND state_schema_version = 1",
            name="ck_episodes_contract",
        ),
        sa.CheckConstraint(
            "octet_length(state_payload_sha256) = 32",
            name="ck_episodes_state_hash",
        ),
        sa.CheckConstraint(
            "date_trunc('second', created_at) = created_at AND date_trunc('second', updated_at) = updated_at AND updated_at >= created_at",
            name="ck_episodes_timestamps",
        ),
        schema="eva_core",
    )
    op.create_table(
        "episode_events",
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("episode_id", UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", UUID(as_uuid=True), nullable=False),
        sa.Column("command_id", UUID(as_uuid=True), nullable=False),
        sa.Column("command_sha256", BYTEA(), nullable=False),
        sa.Column("event_kind", sa.Text(), nullable=False),
        sa.Column("before_revision", sa.BigInteger(), nullable=False),
        sa.Column("after_revision", sa.BigInteger(), nullable=False),
        sa.Column("before_state", sa.Text(), nullable=True),
        sa.Column("after_state", sa.Text(), nullable=False),
        sa.Column("before_generation", sa.Integer(), nullable=True),
        sa.Column("after_generation", sa.Integer(), nullable=False),
        sa.Column("before_episode_state", JSONB(none_as_null=True), nullable=True),
        sa.Column("after_episode_state", JSONB(none_as_null=True), nullable=False),
        sa.Column("before_state_sha256", BYTEA(), nullable=True),
        sa.Column("after_state_sha256", BYTEA(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "installation_id",
            "episode_id",
            "event_id",
            name="pk_episode_events",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "installation_id", "episode_id"],
            [
                "eva_core.episodes.tenant_id",
                "eva_core.episodes.installation_id",
                "eva_core.episodes.episode_id",
            ],
            name="fk_episode_events_episode",
            # This edge and fk_episodes_last_event form one explicit cycle.
            # PostgreSQL's default NO ACTION must be deferred for atomic erase.
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "installation_id",
            "command_id",
            name="uq_episode_events_scoped_command",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "installation_id",
            "episode_id",
            "after_revision",
            name="uq_episode_events_episode_revision",
        ),
        sa.CheckConstraint(
            "event_kind IN ('initialized', 'state_transition')",
            name="ck_episode_events_kind",
        ),
        sa.CheckConstraint(
            f"after_state IN ({STATES}) AND (before_state IS NULL OR before_state IN ({STATES}))",
            name="ck_episode_events_states",
        ),
        sa.CheckConstraint(
            "after_revision = before_revision + 1 AND after_revision >= 1",
            name="ck_episode_events_revision_step",
        ),
        sa.CheckConstraint(
            "after_generation >= 1 AND (before_generation IS NULL OR before_generation >= 1)",
            name="ck_episode_events_generation",
        ),
        sa.CheckConstraint(
            "((event_kind = 'initialized' AND before_revision = 0 AND before_state IS NULL AND before_generation IS NULL AND before_episode_state IS NULL AND before_state_sha256 IS NULL) OR (event_kind = 'state_transition' AND before_revision >= 1 AND before_state IS NOT NULL AND before_generation IS NOT NULL AND before_episode_state IS NOT NULL AND before_state_sha256 IS NOT NULL))",
            name="ck_episode_events_shape",
        ),
        sa.CheckConstraint(
            "octet_length(command_sha256) = 32 AND octet_length(after_state_sha256) = 32 AND (before_state_sha256 IS NULL OR octet_length(before_state_sha256) = 32)",
            name="ck_episode_events_hashes",
        ),
        sa.CheckConstraint(
            episode_state_snapshot_predicate(
                "after_episode_state",
                state_column="after_state",
                generation_column="after_generation",
                event_column="event_id",
                occurred_at_column="occurred_at",
            ),
            name="ck_episode_events_after_snapshot",
        ),
        sa.CheckConstraint(
            "before_episode_state IS NULL OR ("
            + episode_state_snapshot_predicate(
                "before_episode_state",
                state_column="before_state",
                generation_column="before_generation",
                event_column=None,
                occurred_at_column=None,
            )
            + ")",
            name="ck_episode_events_before_snapshot",
        ),
        sa.CheckConstraint(
            "date_trunc('second', occurred_at) = occurred_at",
            name="ck_episode_events_occurred_at_seconds",
        ),
        schema="eva_core",
    )
    op.create_foreign_key(
        "fk_episodes_last_event",
        "episodes",
        "episode_events",
        ["tenant_id", "installation_id", "episode_id", "last_event_id"],
        ["tenant_id", "installation_id", "episode_id", "event_id"],
        source_schema="eva_core",
        referent_schema="eva_core",
        # Default NO ACTION is deferrable; RESTRICT is not.
        deferrable=True,
        initially="DEFERRED",
    )
    op.execute("ALTER TABLE eva_core.episodes OWNER TO eva_migrator")
    op.execute("ALTER TABLE eva_core.episode_events OWNER TO eva_migrator")
    op.execute("REVOKE ALL ON eva_core.episodes, eva_core.episode_events FROM PUBLIC")
    op.execute(
        "GRANT SELECT ON eva_core.episodes, eva_core.episode_events "
        "TO eva_runtime, eva_dbos, eva_backup"
    )
    op.execute(
        "GRANT UPDATE (state, current_generation, supersedes_generation, revision, "
        "last_event_id, state_payload_sha256, updated_at) ON eva_core.episodes "
        "TO eva_runtime, eva_dbos"
    )
    op.execute("GRANT INSERT ON eva_core.episode_events TO eva_runtime, eva_dbos")
    op.execute("ALTER TABLE eva_core.episodes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE eva_core.episodes FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE eva_core.episode_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE eva_core.episode_events FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY episodes_scope ON eva_core.episodes "
        f"USING ({SCOPE_PREDICATE}) WITH CHECK ({SCOPE_PREDICATE})"
    )
    op.execute(
        "CREATE POLICY episode_events_scope ON eva_core.episode_events "
        f"USING ({SCOPE_PREDICATE}) WITH CHECK ({SCOPE_PREDICATE})"
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_episodes_last_event", "episodes", schema="eva_core", type_="foreignkey"
    )
    op.drop_table("episode_events", schema="eva_core")
    op.drop_table("episodes", schema="eva_core")
