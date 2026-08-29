"""Extend exact conversation erasure to the durable memory outbox."""

from __future__ import annotations

from alembic import op


revision = "4e_0006_memory_outbox_erasure"
down_revision = "4e_0005_conversation_erasure"
branch_labels = None
depends_on = None


_V1_FUNCTION = r"""
CREATE OR REPLACE FUNCTION eva_core.erase_conversation_event_content(
    p_event_id uuid,
    p_expected_payload_sha256 bytea,
    p_redacted_payload_json text,
    p_redacted_payload_sha256 bytea
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, eva_core
AS $function$
DECLARE
    stored eva_core.operational_events%ROWTYPE;
    old_data jsonb;
    new_data jsonb;
    erasure jsonb;
    expected_data jsonb;
    redacted_payload jsonb;
    expected_payload jsonb;
BEGIN
    IF current_setting('eva.tenant_id', true) IS NULL
       OR current_setting('eva.installation_id', true) IS NULL
    THEN
        RAISE EXCEPTION 'eva_conversation_erasure_scope_missing'
            USING ERRCODE = '42501';
    END IF;
    IF octet_length(p_expected_payload_sha256) <> 32
       OR octet_length(p_redacted_payload_sha256) <> 32
    THEN
        RAISE EXCEPTION 'eva_conversation_erasure_hash_invalid'
            USING ERRCODE = '22023';
    END IF;
    IF sha256(convert_to(p_redacted_payload_json, 'UTF8'))
        <> p_redacted_payload_sha256
    THEN
        RAISE EXCEPTION 'eva_conversation_erasure_hash_invalid'
            USING ERRCODE = '22023';
    END IF;
    redacted_payload := p_redacted_payload_json::jsonb;

    SELECT * INTO stored
    FROM eva_core.operational_events
    WHERE tenant_id::text = current_setting('eva.tenant_id', true)
      AND installation_id::text = current_setting('eva.installation_id', true)
      AND event_id = p_event_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    IF stored.event_type NOT IN ('INGRESS_COMMITTED', 'REPLY_COMMITTED')
       OR stored.payload_sha256 <> p_expected_payload_sha256
    THEN
        RAISE EXCEPTION 'eva_conversation_erasure_target_differs'
            USING ERRCODE = '40001';
    END IF;

    old_data := stored.payload -> 'data';
    new_data := redacted_payload -> 'data';
    erasure := new_data -> 'content_erasure';
    expected_data := (old_data - 'text' - 'text_sha256')
        || jsonb_build_object('content_erasure', erasure);
    expected_payload := jsonb_set(stored.payload, '{data}', expected_data, false);
    IF jsonb_typeof(old_data -> 'text') <> 'string'
       OR jsonb_typeof(old_data -> 'principal_id') <> 'string'
       OR jsonb_typeof(old_data -> 'message_id') <> 'string'
       OR redacted_payload IS DISTINCT FROM expected_payload
       OR jsonb_typeof(erasure) <> 'object'
       OR erasure ->> 'contract' <> 'EvaConversationContentErasure/v1'
       OR erasure ->> 'status' <> 'erased'
       OR erasure ->> 'erased_at'
            !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
       OR erasure ->> 'operation_sha256' !~ '^[0-9a-f]{64}$'
       OR erasure ->> 'principal_sha256' <> encode(
            sha256(convert_to(old_data ->> 'principal_id', 'UTF8')), 'hex'
       )
       OR erasure ->> 'message_sha256' <> encode(
            sha256(convert_to(old_data ->> 'message_id', 'UTF8')), 'hex'
       )
       OR (SELECT count(*) FROM jsonb_object_keys(erasure)) <> 6
    THEN
        RAISE EXCEPTION 'eva_conversation_erasure_payload_invalid'
            USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('eva.conversation_erasure_v1', 'enabled', true);
    UPDATE eva_core.operational_events
    SET payload = redacted_payload,
        payload_sha256 = p_redacted_payload_sha256
    WHERE tenant_id = stored.tenant_id
      AND installation_id = stored.installation_id
      AND thread_id = stored.thread_id
      AND sequence = stored.sequence;
    RETURN true;
END
$function$
"""


_V2_FUNCTION = r"""
CREATE OR REPLACE FUNCTION eva_core.erase_conversation_event_content(
    p_event_id uuid,
    p_expected_payload_sha256 bytea,
    p_redacted_payload_json text,
    p_redacted_payload_sha256 bytea
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, eva_core
AS $function$
DECLARE
    stored eva_core.operational_events%ROWTYPE;
    old_data jsonb;
    new_data jsonb;
    erasure jsonb;
    expected_data jsonb;
    redacted_payload jsonb;
    expected_payload jsonb;
    source_message_id text;
BEGIN
    IF current_setting('eva.tenant_id', true) IS NULL
       OR current_setting('eva.installation_id', true) IS NULL
    THEN
        RAISE EXCEPTION 'eva_conversation_erasure_scope_missing'
            USING ERRCODE = '42501';
    END IF;
    IF octet_length(p_expected_payload_sha256) <> 32
       OR octet_length(p_redacted_payload_sha256) <> 32
    THEN
        RAISE EXCEPTION 'eva_conversation_erasure_hash_invalid'
            USING ERRCODE = '22023';
    END IF;
    IF sha256(convert_to(p_redacted_payload_json, 'UTF8'))
        <> p_redacted_payload_sha256
    THEN
        RAISE EXCEPTION 'eva_conversation_erasure_hash_invalid'
            USING ERRCODE = '22023';
    END IF;
    redacted_payload := p_redacted_payload_json::jsonb;

    SELECT * INTO stored
    FROM eva_core.operational_events
    WHERE tenant_id::text = current_setting('eva.tenant_id', true)
      AND installation_id::text = current_setting('eva.installation_id', true)
      AND event_id = p_event_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    IF stored.event_type NOT IN (
        'INGRESS_COMMITTED', 'REPLY_COMMITTED', 'MEMORY_RETAIN_QUEUED'
    ) OR stored.payload_sha256 <> p_expected_payload_sha256
    THEN
        RAISE EXCEPTION 'eva_conversation_erasure_target_differs'
            USING ERRCODE = '40001';
    END IF;

    old_data := stored.payload -> 'data';
    new_data := redacted_payload -> 'data';
    erasure := new_data -> 'content_erasure';
    IF stored.event_type = 'MEMORY_RETAIN_QUEUED' THEN
        expected_data := (old_data - 'content')
            || jsonb_build_object('content_erasure', erasure);
        source_message_id := old_data ->> 'source_ref';
        IF old_data ->> 'kind' <> 'hindsight_memory_document'
           OR jsonb_typeof(old_data -> 'document_id') <> 'string'
        THEN
            RAISE EXCEPTION 'eva_conversation_erasure_target_differs'
                USING ERRCODE = '40001';
        END IF;
    ELSE
        expected_data := (old_data - 'text' - 'text_sha256')
            || jsonb_build_object('content_erasure', erasure);
        source_message_id := old_data ->> 'message_id';
    END IF;
    expected_payload := jsonb_set(stored.payload, '{data}', expected_data, false);

    IF (
        stored.event_type = 'MEMORY_RETAIN_QUEUED'
        AND jsonb_typeof(old_data -> 'content') <> 'string'
    ) OR (
        stored.event_type IN ('INGRESS_COMMITTED', 'REPLY_COMMITTED')
        AND jsonb_typeof(old_data -> 'text') <> 'string'
    ) OR jsonb_typeof(old_data -> 'principal_id') <> 'string'
       OR source_message_id IS NULL
       OR redacted_payload IS DISTINCT FROM expected_payload
       OR jsonb_typeof(erasure) <> 'object'
       OR erasure ->> 'contract' <> 'EvaConversationContentErasure/v1'
       OR erasure ->> 'status' <> 'erased'
       OR erasure ->> 'erased_at'
            !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
       OR erasure ->> 'operation_sha256' !~ '^[0-9a-f]{64}$'
       OR erasure ->> 'principal_sha256' <> encode(
            sha256(convert_to(old_data ->> 'principal_id', 'UTF8')), 'hex'
       )
       OR erasure ->> 'message_sha256' <> encode(
            sha256(convert_to(source_message_id, 'UTF8')), 'hex'
       )
       OR (SELECT count(*) FROM jsonb_object_keys(erasure)) <> 6
    THEN
        RAISE EXCEPTION 'eva_conversation_erasure_payload_invalid'
            USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('eva.conversation_erasure_v1', 'enabled', true);
    UPDATE eva_core.operational_events
    SET payload = redacted_payload,
        payload_sha256 = p_redacted_payload_sha256
    WHERE tenant_id = stored.tenant_id
      AND installation_id = stored.installation_id
      AND thread_id = stored.thread_id
      AND sequence = stored.sequence;
    RETURN true;
END
$function$
"""


def upgrade() -> None:
    op.execute("SET LOCAL ROLE eva_migrator")
    op.execute(_V2_FUNCTION)


def downgrade() -> None:
    op.execute("SET LOCAL ROLE eva_migrator")
    op.execute(_V1_FUNCTION)
