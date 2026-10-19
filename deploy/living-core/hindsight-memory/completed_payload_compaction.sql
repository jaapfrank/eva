-- EvaCompletedMemoryPayloadCompaction/v1; native Hindsight 0.9.1 provider addendum.
-- __SCHEMA__ is an installer-validated SQL identifier, never user input.
-- Keep results/replay/errors; only compact completed Eva conversation duplicates.
CREATE OR REPLACE FUNCTION __SCHEMA__.eva_compact_memory_payload(payload jsonb)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE item jsonb; items jsonb := '[]'::jsonb; changed boolean := false;
BEGIN
    IF jsonb_typeof(payload) <> 'object' OR COALESCE(payload->>'type', '') NOT IN ('retain', 'batch_retain')
       OR jsonb_typeof(payload->'contents') IS DISTINCT FROM 'array' THEN
        RETURN payload;
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(payload->'contents') LOOP
        IF jsonb_typeof(item) = 'object'
           AND jsonb_typeof(item->'content') = 'string'
           AND octet_length(item->>'content') > 96
           AND NOT item ? 'content_sha256'
           AND item->>'context' = 'Eva conversation and experience memory'
           AND item->'tags' @> '["eva-memory"]'::jsonb
           AND item->'metadata'->>'selection_policy' = 'later-useful-knowledge-v2'
           AND item->'metadata'->>'trust' = 'direct_user'
           AND jsonb_typeof(item->'document_id') = 'string' THEN
            item := (item - 'content') || jsonb_build_object(
                'content_sha256', encode(sha256(convert_to(item->>'content', 'UTF8')), 'hex'));
            changed := true;
        END IF;
        items := items || jsonb_build_array(item);
    END LOOP;
    IF NOT changed THEN RETURN payload; END IF;
    RETURN jsonb_set(payload, '{contents}', items, false);
END;
$$;

CREATE OR REPLACE FUNCTION __SCHEMA__.eva_compact_closed_memory_family()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_id text;
BEGIN
    IF NEW.status <> 'completed' THEN RETURN NULL; END IF;
    IF NEW.operation_type = 'batch_retain' THEN
        parent_id := NEW.operation_id::text;
    ELSIF NEW.operation_type = 'retain' THEN
        parent_id := NEW.result_metadata->>'parent_operation_id';
    ELSE
        RETURN NULL;
    END IF;
    IF parent_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM __SCHEMA__.async_operations parent
        WHERE parent.operation_id::text = parent_id AND parent.bank_id = NEW.bank_id
          AND parent.operation_type = 'batch_retain' AND parent.status = 'completed'
    ) OR EXISTS (
        SELECT 1 FROM __SCHEMA__.async_operations child
        WHERE child.bank_id = NEW.bank_id
          AND child.result_metadata @> jsonb_build_object('parent_operation_id', parent_id)
          AND (child.status <> 'completed' OR child.operation_type <> 'retain'
               OR child.result_metadata->'extraction_errors_count' IS DISTINCT FROM '0'::jsonb
               OR child.result_metadata->>'extraction_errors_count' IS DISTINCT FROM '0')
    ) THEN RETURN NULL; END IF;
    UPDATE __SCHEMA__.async_operations child
    SET task_payload = __SCHEMA__.eva_compact_memory_payload(child.task_payload)
    WHERE child.bank_id = NEW.bank_id AND child.status = 'completed'
      AND child.operation_type = 'retain'
      AND child.result_metadata @> jsonb_build_object('parent_operation_id', parent_id)
      -- Native 'completed' may contain extraction failures. Unknown is not zero.
      AND child.result_metadata->'extraction_errors_count' = '0'::jsonb
      AND child.result_metadata->>'extraction_errors_count' = '0'
      AND jsonb_typeof(child.result_metadata->'unit_ids_count') = 'number'
      AND (child.result_metadata->>'unit_ids_count') ~ '^[0-9]+$'
      AND child.task_payload->>'operation_id' = child.operation_id::text
      AND child.task_payload->>'bank_id' = child.bank_id
      AND child.task_payload IS DISTINCT FROM
          __SCHEMA__.eva_compact_memory_payload(child.task_payload);
    RETURN NULL;
END;
$$;
DROP TRIGGER IF EXISTS eva_compact_closed_memory_family ON __SCHEMA__.async_operations;
CREATE TRIGGER eva_compact_closed_memory_family
AFTER INSERT OR UPDATE OF status ON __SCHEMA__.async_operations
FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.eva_compact_closed_memory_family();
