"""Compact acknowledged private day text while keeping exact durable provenance."""
from alembic import op

revision = "4e_0010_memory_compaction"
down_revision = "4e_0009_restore_hold"
branch_labels = None
depends_on = None

_COMPACT_FUNCTION = r"""
CREATE OR REPLACE FUNCTION eva_core.compact_private_memory_source(
 p_queued uuid, p_ingress uuid, p_reply uuid, p_proof_json text,
 p_queued_payload_json text, p_ingress_payload_json text, p_reply_payload_json text
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, eva_core AS $function$
DECLARE
 q eva_core.operational_events%ROWTYPE;
 i eva_core.operational_events%ROWTYPE;
 r eva_core.operational_events%ROWTYPE;
 proof jsonb := p_proof_json::jsonb;
 nq jsonb := p_queued_payload_json::jsonb;
 ni jsonb := p_ingress_payload_json::jsonb;
 nr jsonb := p_reply_payload_json::jsonb;
 d jsonb; manifest jsonb; ack jsonb; dispatch jsonb; receipt jsonb;
 cohort text; n integer; k text; member jsonb; canonical_source text; canonical_identity text; canonical_receipt text;
BEGIN
 IF nullif(current_setting('eva.tenant_id',true),'') IS NULL OR
    nullif(current_setting('eva.installation_id',true),'') IS NULL THEN
  RAISE EXCEPTION 'eva_memory_compaction_scope_missing' USING ERRCODE='42501';
 END IF;
 IF jsonb_typeof(proof) IS DISTINCT FROM 'object' OR
    (SELECT count(*) FROM jsonb_object_keys(proof)) <> 16 OR
    proof->>'contract' IS DISTINCT FROM 'EvaPrivateMemorySourceCompaction/v1' OR
    proof->>'compacted_at' IS NULL OR
    proof->>'compacted_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' OR
    (proof->>'compacted_at')::timestamptz > statement_timestamp() OR
    jsonb_typeof(proof->'selected_fact_count') IS DISTINCT FROM 'number' OR
    proof->>'selected_fact_count' !~ '^(0|[1-9][0-9]*)$' OR
    proof->>'disposition' IS DISTINCT FROM (CASE WHEN proof->>'selected_fact_count'='0' THEN 'not_selected' ELSE 'retained' END) OR
    nullif(proof->>'bank_id','') IS NULL OR nullif(proof->>'operation_id','') IS NULL THEN
  RAISE EXCEPTION 'eva_memory_compaction_proof_invalid' USING ERRCODE='22023';
 END IF;
 FOREACH k IN ARRAY ARRAY['queued_payload_sha256','content_sha256','source_payload_sha256',
  'ingress_payload_sha256','ingress_text_sha256','reply_payload_sha256','reply_text_sha256',
  'cohort_manifest_sha256','receipt_sha256','source_identity_sha256'] LOOP
  IF proof->>k IS NULL OR proof->>k !~ '^[0-9a-f]{64}$' THEN
   RAISE EXCEPTION 'eva_memory_compaction_hash_invalid' USING ERRCODE='22023';
  END IF;
 END LOOP;
 -- Checkpoints precede event locks, matching existing erasure transitions.
 PERFORM 1 FROM eva_core.operational_checkpoints c WHERE
  c.tenant_id::text=current_setting('eva.tenant_id',true) AND c.installation_id::text=current_setting('eva.installation_id',true)
  AND (c.thread_id='memory-dream:'||(proof->>'cohort_manifest_sha256') OR c.thread_id IN (
   SELECT e.thread_id FROM eva_core.operational_events e WHERE e.event_id IN(p_queued,p_ingress,p_reply)
    AND e.tenant_id=c.tenant_id AND e.installation_id=c.installation_id)) ORDER BY c.thread_id FOR UPDATE;
 PERFORM 1 FROM eva_core.operational_events WHERE event_id IN (p_queued,p_ingress,p_reply)
  AND tenant_id::text=current_setting('eva.tenant_id',true)
  AND installation_id::text=current_setting('eva.installation_id',true)
  ORDER BY thread_id,sequence FOR UPDATE;
 SELECT * INTO q FROM eva_core.operational_events WHERE event_id=p_queued
  AND tenant_id::text=current_setting('eva.tenant_id',true) AND installation_id::text=current_setting('eva.installation_id',true);
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO i FROM eva_core.operational_events WHERE event_id=p_ingress
  AND tenant_id=q.tenant_id AND installation_id=q.installation_id;
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO r FROM eva_core.operational_events WHERE event_id=p_reply
  AND tenant_id=q.tenant_id AND installation_id=q.installation_id;
 IF NOT FOUND THEN RETURN false; END IF;
 d:=q.payload->'data';
 IF q.event_type <> 'MEMORY_RETAIN_QUEUED' OR q.sequence<>1 OR
    i.event_type <> 'INGRESS_COMMITTED' OR r.event_type <> 'REPLY_COMMITTED' OR
    i.thread_id <> 'conversation:'||(d->>'source_ref') OR r.thread_id<>i.thread_id OR i.sequence>=r.sequence OR
    d->>'kind' IS DISTINCT FROM 'hindsight_memory_document' OR
    d->>'tenant_id' IS NULL OR d->>'installation_id' IS NULL OR
    d->>'trust' IS DISTINCT FROM 'direct_user' OR d->>'retention_mode' IS DISTINCT FROM 'dream' OR
    d->'audience' IS DISTINCT FROM jsonb_build_object('kind','private_person','principal_id',d->>'principal_id','audience_id','person:'||(d->>'principal_id')) OR
    d->>'document_id' IS DISTINCT FROM (d->>'conversation_id')||':exchange:'||(d->>'source_ref') OR
    d->>'conversation_id' ~ '^(initiative-task:|telegram:-|household:)' OR
    d->>'privacy_class' NOT IN ('public','internal','confidential','restricted') OR
    d->'project_id' IS NOT NULL AND d->'project_id'<>'null'::jsonb OR
    d ? 'content_erasure' OR i.payload->'data' ? 'content_erasure' OR r.payload->'data' ? 'content_erasure' OR
    i.payload->'data'->>'principal_id' IS DISTINCT FROM d->>'principal_id' OR
    r.payload->'data'->>'principal_id' IS DISTINCT FROM d->>'principal_id' OR
    i.payload->'data'->>'conversation_id' IS DISTINCT FROM d->>'conversation_id' OR
    r.payload->'data'->>'conversation_id' IS DISTINCT FROM d->>'conversation_id' OR
    i.payload->'data'->>'message_id' IS DISTINCT FROM d->>'source_ref' OR
    r.payload->'data'->>'message_id' IS DISTINCT FROM d->>'source_ref' OR
    i.payload->'data' ? 'project_id' OR r.payload->'data' ? 'project_id' OR
    i.payload->'data' ? 'origin' OR
    (d->>'occurred_at')::timestamptz >= date_trunc('day',(proof->>'compacted_at')::timestamptz AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
 THEN RAISE EXCEPTION 'eva_memory_compaction_source_invalid' USING ERRCODE='22023'; END IF;
 cohort:='memory-dream:'||(proof->>'cohort_manifest_sha256');
 -- Checkpoint row locks stop a concurrent state transition from reopening any selected source.
 PERFORM 1 FROM eva_core.operational_checkpoints WHERE tenant_id=q.tenant_id AND installation_id=q.installation_id
  AND thread_id IN (q.thread_id,i.thread_id,cohort) ORDER BY thread_id FOR UPDATE;
 IF NOT EXISTS(SELECT 1 FROM eva_core.operational_checkpoints WHERE tenant_id=q.tenant_id AND installation_id=q.installation_id
  AND thread_id=q.thread_id AND status='completed' AND phase IN ('memory_retained','memory_not_selected')) OR
    NOT EXISTS(SELECT 1 FROM eva_core.operational_checkpoints WHERE tenant_id=q.tenant_id AND installation_id=q.installation_id
  AND thread_id=i.thread_id AND status='completed' AND (phase='reply_committed' OR (phase='memory_queued' AND
   (SELECT count(*) FROM eva_core.operational_events je WHERE je.tenant_id=q.tenant_id AND je.installation_id=q.installation_id
     AND je.thread_id=i.thread_id AND je.event_type='MEMORY_RETAIN_QUEUED')=1 AND
   EXISTS(SELECT 1 FROM eva_core.operational_events je WHERE je.tenant_id=q.tenant_id AND je.installation_id=q.installation_id
     AND je.thread_id=i.thread_id AND je.event_type='MEMORY_RETAIN_QUEUED' AND je.payload->'data'=jsonb_build_object(
      'kind','memory_retain_queued','conversation_id',d->'conversation_id','document_id',d->'document_id',
      'message_id',d->'source_ref','principal_id',d->'principal_id'))))) OR
    NOT EXISTS(SELECT 1 FROM eva_core.operational_checkpoints WHERE tenant_id=q.tenant_id AND installation_id=q.installation_id
  AND thread_id=cohort AND status='completed' AND phase='memory_dream_completed') THEN
  RAISE EXCEPTION 'eva_memory_compaction_pending' USING ERRCODE='40001';
 END IF;
 SELECT payload->'data' INTO manifest FROM eva_core.operational_events WHERE tenant_id=q.tenant_id AND installation_id=q.installation_id
  AND thread_id=cohort AND sequence=1 AND event_type='MEMORY_DREAM_BATCH_ASSIGNED';
 IF manifest->>'contract' IS DISTINCT FROM 'EvaDreamMemoryBatch/v1' OR jsonb_typeof(manifest->'members') IS DISTINCT FROM 'array'
  OR jsonb_array_length(manifest->'members') NOT BETWEEN 1 AND 32 THEN
  RAISE EXCEPTION 'eva_memory_compaction_manifest_invalid' USING ERRCODE='22023';
 END IF;
 SELECT count(*) INTO n FROM eva_core.operational_events WHERE tenant_id=q.tenant_id AND installation_id=q.installation_id
  AND thread_id=cohort AND event_type='MEMORY_DREAM_RECEIPTS_BOUND';
 IF n<>1 THEN RAISE EXCEPTION 'eva_memory_compaction_receipts_missing' USING ERRCODE='22023'; END IF;
 SELECT payload->'data' INTO ack FROM eva_core.operational_events WHERE tenant_id=q.tenant_id AND installation_id=q.installation_id
  AND thread_id=cohort AND event_type='MEMORY_DREAM_RECEIPTS_BOUND';
 SELECT count(*) INTO n FROM eva_core.operational_events WHERE tenant_id=q.tenant_id AND installation_id=q.installation_id
  AND thread_id=cohort AND event_type='MEMORY_DREAM_DISPATCH_BOUND';
 IF n<>1 THEN RAISE EXCEPTION 'eva_memory_compaction_dispatch_invalid' USING ERRCODE='22023'; END IF;
 SELECT payload->'data' INTO dispatch FROM eva_core.operational_events WHERE tenant_id=q.tenant_id AND installation_id=q.installation_id
  AND thread_id=cohort AND event_type='MEMORY_DREAM_DISPATCH_BOUND';
 IF ack->>'contract' IS DISTINCT FROM 'EvaDreamReceipts/v1' OR ack->>'source_manifest_sha256' IS DISTINCT FROM proof->>'cohort_manifest_sha256'
  OR jsonb_typeof(ack->'receipts') IS DISTINCT FROM 'array' OR jsonb_array_length(ack->'receipts')<>jsonb_array_length(manifest->'members') THEN
  RAISE EXCEPTION 'eva_memory_compaction_receipts_invalid' USING ERRCODE='22023'; END IF;
 FOR member IN SELECT value FROM jsonb_array_elements(manifest->'members') LOOP
  SELECT count(*) INTO n FROM jsonb_array_elements(ack->'receipts') x WHERE x->>'document_id'=member->>'document_id';
  IF n<>1 THEN RAISE EXCEPTION 'eva_memory_compaction_membership_invalid' USING ERRCODE='22023'; END IF;
  SELECT value INTO receipt FROM jsonb_array_elements(ack->'receipts') WHERE value->>'document_id'=member->>'document_id';
  IF (SELECT count(*) FROM jsonb_object_keys(receipt))<>6 OR receipt->>'bank_id' IS DISTINCT FROM dispatch->>'bank_id'
   OR receipt->>'operation_id' IS DISTINCT FROM dispatch->>'operation_id'
   OR receipt->>'content_sha256' IS NULL OR receipt->>'content_sha256' !~ '^[0-9a-f]{64}$'
   OR jsonb_typeof(receipt->'selected_fact_count') IS DISTINCT FROM 'number'
   OR receipt->>'selected_fact_count' !~ '^(0|[1-9][0-9]*)$'
   OR receipt->>'disposition' IS DISTINCT FROM (CASE WHEN receipt->>'selected_fact_count'='0' THEN 'not_selected' ELSE 'retained' END)
   OR NOT EXISTS(SELECT 1 FROM eva_core.operational_events e JOIN eva_core.operational_checkpoints c USING(tenant_id,installation_id,thread_id)
    WHERE e.tenant_id=q.tenant_id AND e.installation_id=q.installation_id AND e.event_type='MEMORY_RETAIN_QUEUED' AND e.sequence=1
     AND e.payload->'data'->>'document_id'=member->>'document_id' AND c.status='completed' AND c.phase IN('memory_retained','memory_not_selected')) THEN
   RAISE EXCEPTION 'eva_memory_compaction_member_unacknowledged' USING ERRCODE='22023'; END IF;
 END LOOP;
 SELECT value INTO receipt FROM jsonb_array_elements(ack->'receipts') WHERE value->>'document_id'=d->>'document_id';
 IF receipt IS NULL OR receipt IS DISTINCT FROM jsonb_build_object('bank_id',proof->'bank_id','document_id',d->'document_id',
  'content_sha256',proof->'content_sha256','operation_id',proof->'operation_id','selected_fact_count',proof->'selected_fact_count','disposition',proof->'disposition')
  OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(manifest->'members') x WHERE x->>'document_id'=d->>'document_id'
    AND x->>'source_payload_sha256'=proof->>'source_payload_sha256') THEN
  RAISE EXCEPTION 'eva_memory_compaction_receipt_differs' USING ERRCODE='22023'; END IF;
 -- These ordinary source fields are scalars except the exact three-scalar audience.
 -- Rebuild their canonical form rather than hashing jsonb::text whitespace.
 SELECT '{'||string_agg(to_jsonb(key)::text||':'||value::text,',' ORDER BY key COLLATE "C")||'}'
 INTO canonical_receipt FROM jsonb_each(receipt);
 IF encode(sha256(convert_to(canonical_receipt,'UTF8')),'hex')<>proof->>'receipt_sha256' THEN
  RAISE EXCEPTION 'eva_memory_compaction_receipt_hash_invalid' USING ERRCODE='22023'; END IF;
 SELECT '{'||string_agg(to_jsonb(x.key)::text||':'||CASE WHEN x.key='audience' THEN
  (SELECT '{'||string_agg(to_jsonb(a.key)::text||':'||a.value::text,',' ORDER BY a.key COLLATE "C")||'}' FROM jsonb_each(x.value) a)
  ELSE x.value::text END,',' ORDER BY x.key COLLATE "C")||'}'
 INTO canonical_identity FROM jsonb_each(d-'content'-'content_compaction') x;
 IF encode(sha256(convert_to(canonical_identity,'UTF8')),'hex')<>proof->>'source_identity_sha256' THEN
  RAISE EXCEPTION 'eva_memory_compaction_identity_hash_invalid' USING ERRCODE='22023'; END IF;
 IF NOT d ? 'content_compaction' THEN
  SELECT '{'||string_agg(to_jsonb(x.key)::text||':'||CASE WHEN x.key='audience' THEN
   (SELECT '{'||string_agg(to_jsonb(a.key)::text||':'||a.value::text,',' ORDER BY a.key COLLATE "C")||'}' FROM jsonb_each(x.value) a)
   ELSE x.value::text END,',' ORDER BY x.key COLLATE "C")||'}'
  INTO canonical_source FROM jsonb_each(d) x;
  IF encode(sha256(convert_to(canonical_source,'UTF8')),'hex')<>proof->>'source_payload_sha256' THEN
   RAISE EXCEPTION 'eva_memory_compaction_source_hash_invalid' USING ERRCODE='22023'; END IF;
 END IF;
 IF nq IS DISTINCT FROM jsonb_set(q.payload,'{data}',(d-'content')||jsonb_build_object('content_compaction',proof),false)
  OR ni IS DISTINCT FROM jsonb_set(i.payload,'{data}',((i.payload->'data')-'text')||jsonb_build_object('content_compaction',proof),false)
  OR nr IS DISTINCT FROM jsonb_set(r.payload,'{data}',((r.payload->'data')-'text')||jsonb_build_object('content_compaction',proof),false) THEN
  RAISE EXCEPTION 'eva_memory_compaction_replacement_differs' USING ERRCODE='22023'; END IF;
 IF d ? 'content_compaction' THEN
  IF d->'content_compaction'=proof AND i.payload->'data'->'content_compaction'=proof AND r.payload->'data'->'content_compaction'=proof
   AND NOT d ? 'content' AND NOT i.payload->'data' ? 'text' AND NOT r.payload->'data' ? 'text' THEN RETURN true; END IF;
  RAISE EXCEPTION 'eva_memory_compaction_replay_differs' USING ERRCODE='40001';
 END IF;
 IF jsonb_typeof(d->'content') IS DISTINCT FROM 'string' OR jsonb_typeof(i.payload->'data'->'text') IS DISTINCT FROM 'string'
  OR jsonb_typeof(r.payload->'data'->'text') IS DISTINCT FROM 'string'
  OR encode(q.payload_sha256,'hex')<>proof->>'queued_payload_sha256'
  OR encode(i.payload_sha256,'hex')<>proof->>'ingress_payload_sha256'
  OR encode(r.payload_sha256,'hex')<>proof->>'reply_payload_sha256'
  OR encode(sha256(convert_to(d->>'content','UTF8')),'hex')<>proof->>'content_sha256'
  OR encode(sha256(convert_to(i.payload->'data'->>'text','UTF8')),'hex')<>proof->>'ingress_text_sha256'
  OR encode(sha256(convert_to(r.payload->'data'->>'text','UTF8')),'hex')<>proof->>'reply_text_sha256'
  OR r.payload->'data'->>'text_sha256' IS DISTINCT FROM proof->>'reply_text_sha256'
  OR i.payload->'data' ? 'content_compaction' OR r.payload->'data' ? 'content_compaction' THEN
  RAISE EXCEPTION 'eva_memory_compaction_original_differs' USING ERRCODE='40001'; END IF;
 PERFORM set_config('eva.conversation_erasure_v1','enabled',true);
 UPDATE eva_core.operational_events SET payload=nq,payload_sha256=sha256(convert_to(p_queued_payload_json,'UTF8'))
  WHERE event_id=q.event_id AND tenant_id=q.tenant_id AND installation_id=q.installation_id;
 UPDATE eva_core.operational_events SET payload=ni,payload_sha256=sha256(convert_to(p_ingress_payload_json,'UTF8'))
  WHERE event_id=i.event_id AND tenant_id=q.tenant_id AND installation_id=q.installation_id;
 UPDATE eva_core.operational_events SET payload=nr,payload_sha256=sha256(convert_to(p_reply_payload_json,'UTF8'))
  WHERE event_id=r.event_id AND tenant_id=q.tenant_id AND installation_id=q.installation_id;
 RETURN true;
END $function$;
"""

_ERASURE_FUNCTION = r"""
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

    IF (old_data ? 'content_compaction' AND (
        jsonb_typeof(old_data -> 'content_compaction') IS DISTINCT FROM 'object'
        OR (SELECT count(*) FROM jsonb_object_keys(old_data -> 'content_compaction')) <> 16
        OR old_data -> 'content_compaction' ->> 'contract' IS DISTINCT FROM 'EvaPrivateMemorySourceCompaction/v1'
        OR old_data ? 'content' OR old_data ? 'text'
        OR EXISTS (SELECT 1 FROM unnest(ARRAY['queued_payload_sha256','content_sha256','source_payload_sha256',
            'ingress_payload_sha256','ingress_text_sha256','reply_payload_sha256','reply_text_sha256',
            'cohort_manifest_sha256','receipt_sha256','source_identity_sha256']) k
            WHERE old_data->'content_compaction'->>k IS NULL OR old_data->'content_compaction'->>k !~ '^[0-9a-f]{64}$')
        OR old_data->'content_compaction'->>'compacted_at' IS NULL
        OR old_data->'content_compaction'->>'compacted_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
        OR nullif(old_data->'content_compaction'->>'bank_id','') IS NULL
        OR nullif(old_data->'content_compaction'->>'operation_id','') IS NULL
        OR jsonb_typeof(old_data->'content_compaction'->'selected_fact_count') IS DISTINCT FROM 'number'
        OR old_data->'content_compaction'->>'selected_fact_count' !~ '^(0|[1-9][0-9]*)$'
        OR old_data->'content_compaction'->>'disposition' IS DISTINCT FROM (CASE
            WHEN old_data->'content_compaction'->>'selected_fact_count'='0' THEN 'not_selected' ELSE 'retained' END)
    )) OR (
        stored.event_type = 'MEMORY_RETAIN_QUEUED'
        AND NOT COALESCE((jsonb_typeof(old_data -> 'content') = 'string' OR (NOT old_data ? 'content' AND old_data -> 'content_compaction' ->> 'contract' = 'EvaPrivateMemorySourceCompaction/v1')), false)
    ) OR (
        stored.event_type IN ('INGRESS_COMMITTED', 'REPLY_COMMITTED')
        AND NOT COALESCE((jsonb_typeof(old_data -> 'text') = 'string' OR (NOT old_data ? 'text' AND old_data -> 'content_compaction' ->> 'contract' = 'EvaPrivateMemorySourceCompaction/v1')), false)
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

def upgrade():
    op.execute("SET LOCAL ROLE eva_migrator")
    op.execute(_COMPACT_FUNCTION)
    op.execute(_ERASURE_FUNCTION)
    op.execute("REVOKE ALL ON FUNCTION eva_core.compact_private_memory_source(uuid,uuid,uuid,text,text,text,text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION eva_core.compact_private_memory_source(uuid,uuid,uuid,text,text,text,text) TO eva_runtime")

def downgrade():
    op.execute("SET LOCAL ROLE eva_migrator")
    op.execute("DROP FUNCTION eva_core.compact_private_memory_source(uuid,uuid,uuid,text,text,text,text)")
    op.execute("\nCREATE OR REPLACE FUNCTION eva_core.erase_conversation_event_content(\n    p_event_id uuid,\n    p_expected_payload_sha256 bytea,\n    p_redacted_payload_json text,\n    p_redacted_payload_sha256 bytea\n)\nRETURNS boolean\nLANGUAGE plpgsql\nSECURITY DEFINER\nSET search_path = pg_catalog, eva_core\nAS $function$\nDECLARE\n    stored eva_core.operational_events%ROWTYPE;\n    old_data jsonb;\n    new_data jsonb;\n    erasure jsonb;\n    expected_data jsonb;\n    redacted_payload jsonb;\n    expected_payload jsonb;\n    source_message_id text;\nBEGIN\n    IF current_setting('eva.tenant_id', true) IS NULL\n       OR current_setting('eva.installation_id', true) IS NULL\n    THEN\n        RAISE EXCEPTION 'eva_conversation_erasure_scope_missing'\n            USING ERRCODE = '42501';\n    END IF;\n    IF octet_length(p_expected_payload_sha256) <> 32\n       OR octet_length(p_redacted_payload_sha256) <> 32\n    THEN\n        RAISE EXCEPTION 'eva_conversation_erasure_hash_invalid'\n            USING ERRCODE = '22023';\n    END IF;\n    IF sha256(convert_to(p_redacted_payload_json, 'UTF8'))\n        <> p_redacted_payload_sha256\n    THEN\n        RAISE EXCEPTION 'eva_conversation_erasure_hash_invalid'\n            USING ERRCODE = '22023';\n    END IF;\n    redacted_payload := p_redacted_payload_json::jsonb;\n\n    SELECT * INTO stored\n    FROM eva_core.operational_events\n    WHERE tenant_id::text = current_setting('eva.tenant_id', true)\n      AND installation_id::text = current_setting('eva.installation_id', true)\n      AND event_id = p_event_id\n    FOR UPDATE;\n    IF NOT FOUND THEN\n        RETURN false;\n    END IF;\n    IF stored.event_type NOT IN (\n        'INGRESS_COMMITTED', 'REPLY_COMMITTED', 'MEMORY_RETAIN_QUEUED'\n    ) OR stored.payload_sha256 <> p_expected_payload_sha256\n    THEN\n        RAISE EXCEPTION 'eva_conversation_erasure_target_differs'\n            USING ERRCODE = '40001';\n    END IF;\n\n    old_data := stored.payload -> 'data';\n    new_data := redacted_payload -> 'data';\n    erasure := new_data -> 'content_erasure';\n    IF stored.event_type = 'MEMORY_RETAIN_QUEUED' THEN\n        expected_data := (old_data - 'content')\n            || jsonb_build_object('content_erasure', erasure);\n        source_message_id := old_data ->> 'source_ref';\n        IF old_data ->> 'kind' <> 'hindsight_memory_document'\n           OR jsonb_typeof(old_data -> 'document_id') <> 'string'\n        THEN\n            RAISE EXCEPTION 'eva_conversation_erasure_target_differs'\n                USING ERRCODE = '40001';\n        END IF;\n    ELSE\n        expected_data := (old_data - 'text' - 'text_sha256')\n            || jsonb_build_object('content_erasure', erasure);\n        source_message_id := old_data ->> 'message_id';\n    END IF;\n    expected_payload := jsonb_set(stored.payload, '{data}', expected_data, false);\n\n    IF (\n        stored.event_type = 'MEMORY_RETAIN_QUEUED'\n        AND jsonb_typeof(old_data -> 'content') <> 'string'\n    ) OR (\n        stored.event_type IN ('INGRESS_COMMITTED', 'REPLY_COMMITTED')\n        AND jsonb_typeof(old_data -> 'text') <> 'string'\n    ) OR jsonb_typeof(old_data -> 'principal_id') <> 'string'\n       OR source_message_id IS NULL\n       OR redacted_payload IS DISTINCT FROM expected_payload\n       OR jsonb_typeof(erasure) <> 'object'\n       OR erasure ->> 'contract' <> 'EvaConversationContentErasure/v1'\n       OR erasure ->> 'status' <> 'erased'\n       OR erasure ->> 'erased_at'\n            !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'\n       OR erasure ->> 'operation_sha256' !~ '^[0-9a-f]{64}$'\n       OR erasure ->> 'principal_sha256' <> encode(\n            sha256(convert_to(old_data ->> 'principal_id', 'UTF8')), 'hex'\n       )\n       OR erasure ->> 'message_sha256' <> encode(\n            sha256(convert_to(source_message_id, 'UTF8')), 'hex'\n       )\n       OR (SELECT count(*) FROM jsonb_object_keys(erasure)) <> 6\n    THEN\n        RAISE EXCEPTION 'eva_conversation_erasure_payload_invalid'\n            USING ERRCODE = '22023';\n    END IF;\n\n    PERFORM set_config('eva.conversation_erasure_v1', 'enabled', true);\n    UPDATE eva_core.operational_events\n    SET payload = redacted_payload,\n        payload_sha256 = p_redacted_payload_sha256\n    WHERE tenant_id = stored.tenant_id\n      AND installation_id = stored.installation_id\n      AND thread_id = stored.thread_id\n      AND sequence = stored.sequence;\n    RETURN true;\nEND\n$function$\n")
