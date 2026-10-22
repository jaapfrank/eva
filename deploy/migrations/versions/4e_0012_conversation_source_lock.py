"""Fence existing private ingress against erasure without granting UPDATE rights."""
from alembic import op

revision = '4e_0012_conversation_source_lock'
down_revision = '4e_0011_timeline_type_time'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('SET LOCAL ROLE eva_migrator')
    op.execute("""
    CREATE FUNCTION eva_core.lock_conversation_source(
        p_event_id uuid, p_expected_payload_sha256 bytea
    ) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = pg_catalog, eva_core
    AS $function$
    DECLARE stored eva_core.operational_events%ROWTYPE;
    BEGIN
        IF NULLIF(current_setting('eva.tenant_id', true), '') IS NULL
           OR NULLIF(current_setting('eva.installation_id', true), '') IS NULL
           OR p_event_id IS NULL OR p_expected_payload_sha256 IS NULL
           OR octet_length(p_expected_payload_sha256) <> 32
        THEN
            RAISE EXCEPTION 'eva_conversation_source_lock_input_invalid'
                USING ERRCODE = '42501';
        END IF;
        SELECT * INTO stored FROM eva_core.operational_events
        WHERE tenant_id::text = current_setting('eva.tenant_id', true)
          AND installation_id::text = current_setting('eva.installation_id', true)
          AND event_id = p_event_id AND event_type = 'INGRESS_COMMITTED'
        FOR SHARE;
        RETURN FOUND AND stored.payload_sha256 = p_expected_payload_sha256;
    END
    $function$;
    REVOKE ALL ON FUNCTION eva_core.lock_conversation_source(uuid, bytea) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION eva_core.lock_conversation_source(uuid, bytea)
        TO eva_runtime, eva_dbos;
    """)


def downgrade():
    op.execute('SET LOCAL ROLE eva_migrator')
    op.execute('DROP FUNCTION eva_core.lock_conversation_source(uuid, bytea)')
