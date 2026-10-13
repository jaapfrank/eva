import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import Database from 'better-sqlite3';

const mode = process.argv[2] || 'empty';
const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `eva-turn-predecessor-${mode}-`));
process.env.EVA_DATA_DIR = dataDir;

const { db } = await import('../src/db.js');
const {
  TURN_UNDERSTANDING_AUTHORITY_PRIOR_TABLE_SQL,
  TURN_UNDERSTANDING_AUTHORITY_PRIOR_TRIGGER_SQL,
  TURN_UNDERSTANDING_AUTHORITY_HTTP_SOURCE_V1_TABLE_SQL,
  TURN_UNDERSTANDING_AUTHORITY_TABLE_SQL,
  TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL,
  TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_SQL,
  turnUnderstandingAuthorityBody,
} = await import('../src/turnauthority.js');
const { canonicalJson } = await import('../src/util.js');
const sha256 = (value) => createHash('sha256').update(value).digest('hex');

const table = 'eva_turn_understanding_authorities_v1';
const predecessorTableSql = TURN_UNDERSTANDING_AUTHORITY_PRIOR_TABLE_SQL
  .replace('  source_body_json TEXT,\n', '')
  .replace(`  CHECK((channel='web' AND source_body_json IS NULL)
    OR (channel IN ('telegram','voice') AND typeof(source_body_json)='text'
      AND length(CAST(source_body_json AS BLOB))>=2
      AND length(CAST(source_body_json AS BLOB))<=65536
      AND json_valid(source_body_json) AND json_type(source_body_json)='object')),\n`, '');
const predecessorTriggers = {
  ...TURN_UNDERSTANDING_AUTHORITY_PRIOR_TRIGGER_SQL,
  eva_turn_understanding_authorities_v1_valid_successor:
    TURN_UNDERSTANDING_AUTHORITY_PRIOR_TRIGGER_SQL
      .eva_turn_understanding_authorities_v1_valid_successor
      .replace(`
            OR existing.source_sha256<>NEW.source_sha256
            OR existing.source_body_json IS NOT NEW.source_body_json))`, `
            OR existing.source_sha256<>NEW.source_sha256))`),
};
const httpSourceV1TriggerSql = TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_SQL
  .replace(
    "      WHERE NEW.contract='TurnUnderstandingExecutionAuthority/v2'\n        AND request.request_ref=NEW.source_ref",
    '      WHERE request.request_ref=NEW.source_ref',
  )
  .replace('        AND claim.claim_event_ref=NEW.source_claim_event_ref\n', '')
  .replace('        AND claim.created_ts=NEW.issued_ts',
    '        AND claim.created_ts<=NEW.issued_ts');

const rows = [];
if (['web-row', 'prior-web-row', 'prior-web-noncanonical',
  'current-http-v1-row', 'current-http-v1-ambiguous',
  'current-http-v1-successor'].includes(mode)) {
  const { seedAuthorizedTurnUnderstanding } = await import(
    './turn-understanding-authority-fixture.js'
  );
  const seeded = seedAuthorizedTurnUnderstanding({
    database: db,
    channel: 'web',
    conversationId: 'web:predecessor-preserved',
    messageId: 'predecessor-web-1',
    requester: 'Jaap',
    originalText: 'Bewaar exact dit predecessorbewijs.',
    sessionRef: 'web:predecessor-preserved',
    syntheticTest: true,
  });
  const legacyAuthority = {
    ...seeded.authority,
    contract: 'TurnUnderstandingExecutionAuthority/v1',
  };
  delete legacyAuthority.source_claim_event_ref;
  legacyAuthority.authority_sha256 = createHash('sha256').update(canonicalJson(
    turnUnderstandingAuthorityBody(legacyAuthority, seeded.row),
  )).digest('hex');
  if (mode === 'prior-web-noncanonical') {
    const noncanonical = {
      ...legacyAuthority,
      issued_ts: String(legacyAuthority.issued_ts).replace(/Z$/u, '+00:00'),
    };
    noncanonical.authority_sha256 = createHash('sha256').update(canonicalJson(
      turnUnderstandingAuthorityBody(noncanonical, seeded.row),
    )).digest('hex');
    rows.push(noncanonical);
  } else rows.push(legacyAuthority);
  if (mode === 'current-http-v1-ambiguous') {
    const acquired = db.prepare(`SELECT * FROM main.eva_http_ingress_claim_events
      WHERE request_ref=? AND phase='execution' AND generation=?
      ORDER BY sequence DESC LIMIT 1`).get(
      legacyAuthority.source_ref, legacyAuthority.source_generation,
    );
    const renewed = {
      action: 'renewed', contract: 'HttpIngressClaimEvent/v1',
      generation: Number(acquired.generation),
      leaseUntil: new Date(Date.parse(acquired.lease_until_ts) + 30_000).toISOString(),
      phase: 'execution', requestRef: acquired.request_ref,
      sequence: Number(acquired.sequence) + 1, tokenSha256: acquired.token_sha256,
    };
    const eventJson = canonicalJson(renewed);
    const eventSha256 = createHash('sha256').update(eventJson).digest('hex');
    db.prepare(`INSERT INTO main.eva_http_ingress_claim_events (
      contract,claim_event_ref,claim_event_sha256,request_row_id,request_ref,phase,
      generation,sequence,action,token_sha256,lease_until_ts,event_json,created_ts
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      renewed.contract, `http-claim-event:${eventSha256}`, eventSha256,
      acquired.request_row_id, acquired.request_ref, acquired.phase,
      renewed.generation, renewed.sequence, renewed.action,
      renewed.tokenSha256, renewed.leaseUntil, eventJson, legacyAuthority.issued_ts,
    );
  }
}
if (mode === 'prior-native-rows') {
  const seedNativeAuthority = ({
    channel, conversationId, messageId, sourceKind, sourceRef, sourceBody,
    requester, originalText,
  }) => {
    const identity = { channel, conversationId, messageId };
    const args = {
      ...identity, requester, originalText, sessionRef: '',
      supersessionScope: null, supersessionToken: null, syntheticTest: true,
    };
    const storedArgs = canonicalJson(args);
    const executionKey = `turn-understanding:${sha256(canonicalJson(identity))}`;
    const turnId = `ingress:${channel}:${conversationId}:${messageId}`;
    const executionSeq = Number(db.prepare(
      'SELECT coalesce(max(execution_seq),0)+1 AS value FROM main.turn_tool_executions',
    ).get().value);
    const semanticHash = sha256(canonicalJson({
      turnId, tool: '@turn/understanding', args, binding: null,
    }));
    const intentRecordSha256 = sha256(canonicalJson({
      executionContract: 'ExecutionRecord/v2', executionSeq, turnId,
      tool: '@turn/understanding', args, binding: null, semanticHash,
    }));
    const issuedTs = new Date().toISOString();
    db.prepare(`INSERT INTO main.turn_tool_executions (
      execution_key,turn_id,tool,args,status,started_ts,semantic_hash,
      semantic_binding,intent_record_sha256,execution_seq,execution_contract,
      execution_verification
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      executionKey, turnId, '@turn/understanding', storedArgs, 'started', issuedTs,
      semanticHash, null, intentRecordSha256, executionSeq,
      'ExecutionRecord/v2', 'verified',
    );
    const execution = db.prepare(`SELECT * FROM main.turn_tool_executions
      WHERE execution_key=?`).get(executionKey);
    const sourceBodyJson = canonicalJson(sourceBody);
    const authority = {
      contract: 'TurnUnderstandingExecutionAuthority/v1', execution_key: executionKey,
      execution_seq: executionSeq, intent_record_sha256: intentRecordSha256,
      channel, conversation_id: conversationId, message_id: messageId,
      source_kind: sourceKind, source_ref: sourceRef,
      source_sha256: sha256(sourceBodyJson), source_body_json: sourceBodyJson,
      source_generation: 1, authority_sha256: '', issued_ts: issuedTs,
    };
    authority.authority_sha256 = sha256(canonicalJson(
      turnUnderstandingAuthorityBody(authority, execution),
    ));
    rows.push(authority);
  };
  const telegramMessageId = 'telegram-native-migration-1';
  seedNativeAuthority({
    channel: 'telegram', conversationId: 'telegram:12345',
    messageId: telegramMessageId, sourceKind: 'telegram_ingress',
    sourceRef: `telegram-ingress:12345:${telegramMessageId}`,
    requester: 'Jaap', originalText: 'Bewaar dit Telegram-bewijs.',
    sourceBody: {
      contract: 'TelegramArchivedUserEnvelope/v1', chatId: '12345',
      messageId: telegramMessageId, role: 'user', deleted: false,
      meta: { inbound: true, fromTelegramId: '12345' },
    },
  });
  const voiceMessageId = 'ha-voice-native-migration-1';
  const voiceEvent = {
    id: 71, type: 'event', event: {
      event_type: 'eva_voice_query',
      data: { id: voiceMessageId, text: 'Bewaar dit native voicebewijs.' },
    },
  };
  const frame = Buffer.from(canonicalJson(voiceEvent));
  seedNativeAuthority({
    channel: 'voice', conversationId: 'voice:ha-native-migration',
    messageId: voiceMessageId, sourceKind: 'voice_ingress',
    sourceRef: `home-assistant-websocket:${voiceMessageId}`,
    requester: 'Jaap', originalText: 'Bewaar dit native voicebewijs.',
    sourceBody: {
      contract: 'HomeAssistantVoiceEventEvidence/v1',
      adapterInstanceSha256: 'a'.repeat(64), socketGeneration: 7,
      eventType: 'eva_voice_query', frameSha256: sha256(frame),
      frameBase64: frame.toString('base64'), frameByteLength: frame.length,
      event: voiceEvent,
    },
  });
}
if (mode === 'native-row') {
  rows.push({
    contract: 'TurnUnderstandingExecutionAuthority/v1',
    execution_key: `turn-understanding:${'1'.repeat(64)}`,
    execution_seq: 1,
    intent_record_sha256: '2'.repeat(64),
    channel: 'voice',
    conversation_id: 'voice:predecessor-unreconstructable',
    message_id: 'predecessor-native-1',
    source_kind: 'voice_ingress',
    source_ref: 'home-assistant-websocket:predecessor-native-1',
    source_sha256: '3'.repeat(64),
    source_generation: 1,
    authority_sha256: '4'.repeat(64),
    issued_ts: '2026-08-13T12:00:00.000Z',
  });
}

db.transaction(() => {
  for (const name of Object.keys(TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL)) {
    db.exec(`DROP TRIGGER main.${name}`);
  }
  db.exec(`DROP TABLE main.${table}`);
  const priorSchema = mode.startsWith('prior-');
  const currentHttpV1Schema = ['current-http-v1-row',
    'current-http-v1-ambiguous', 'current-http-v1-successor'].includes(mode);
  db.exec(currentHttpV1Schema
    ? TURN_UNDERSTANDING_AUTHORITY_HTTP_SOURCE_V1_TABLE_SQL
    : priorSchema ? TURN_UNDERSTANDING_AUTHORITY_PRIOR_TABLE_SQL : predecessorTableSql);
  const insert = db.prepare(`INSERT INTO main.${table} (
    contract,execution_key,execution_seq,intent_record_sha256,channel,
    conversation_id,message_id,source_kind,source_ref,source_sha256,
    ${priorSchema ? 'source_body_json,' : ''}source_generation,authority_sha256,issued_ts
  ) VALUES (@contract,@execution_key,@execution_seq,@intent_record_sha256,@channel,
    @conversation_id,@message_id,@source_kind,@source_ref,@source_sha256,
    ${priorSchema ? '@source_body_json,' : ''}@source_generation,@authority_sha256,@issued_ts)`);
  for (const row of rows) insert.run(row);
  for (const sql of Object.values(currentHttpV1Schema
    ? TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL
    : priorSchema ? TURN_UNDERSTANDING_AUTHORITY_PRIOR_TRIGGER_SQL
      : predecessorTriggers)) db.exec(sql);
  if (currentHttpV1Schema) db.exec(httpSourceV1TriggerSql);
}).immediate();
const proofFields = `contract,execution_key,execution_seq,intent_record_sha256,channel,
  conversation_id,message_id,source_kind,source_ref,source_sha256,source_body_json,
  source_generation,authority_sha256,issued_ts`;
const beforeRowsBase64 = (mode.startsWith('prior-')
  || ['current-http-v1-row', 'current-http-v1-ambiguous',
    'current-http-v1-successor'].includes(mode))
  ? Buffer.from(canonicalJson(db.prepare(`SELECT ${proofFields} FROM main.${table}
    ORDER BY execution_key,source_generation`).all())).toString('base64') : null;
const beforeExecutionOutputsBase64 = Buffer.from(canonicalJson(db.prepare(`SELECT
  execution_key,status,output,output_sha256,completion_record_sha256
  FROM main.turn_tool_executions WHERE tool='@turn/understanding'
  ORDER BY execution_key`).all())).toString('base64');
function migrationRollbackSnapshot(database) {
  return {
    schemaVersion: Number(database.pragma('schema_version', { simple: true })),
    columns: database.prepare(`PRAGMA main.table_info('${table}')`)
      .all().map((column) => column.name),
    namespace: database.prepare(`SELECT type,name,tbl_name,sql FROM main.sqlite_master
      WHERE tbl_name=? ORDER BY type,name`).all(table),
    authorities: database.prepare(`SELECT * FROM main.${table}
      ORDER BY execution_key,source_generation`).all(),
    executionOutputs: database.prepare(`SELECT execution_key,status,output,
      output_sha256,completion_record_sha256 FROM main.turn_tool_executions
      WHERE tool='@turn/understanding' ORDER BY execution_key`).all(),
  };
}
const beforeMigrationRollbackBase64 = mode === 'current-http-v1-ambiguous'
  ? Buffer.from(canonicalJson(migrationRollbackSnapshot(db))).toString('base64') : null;
db.close();

const probe = spawnSync(process.execPath, ['--input-type=module', '-e', `
  try {
    const module = await import('./src/db.js');
    const {canonicalJson}=await import('./src/util.js');
    const {createHash}=await import('node:crypto');
    const authorityModule=await import('./src/turnauthority.js');
    const sha256=(value)=>createHash('sha256').update(value).digest('hex');
    const columns = module.db.prepare(
      "PRAGMA main.table_info('eva_turn_understanding_authorities_v1')",
    ).all().map((column) => column.name);
    const status = module.turnUnderstandingAuthorityStoreStatus();
    const httpTables = module.db.prepare("SELECT count(*) AS value "
      + "FROM main.sqlite_master WHERE type='table' "
      + "AND name IN ('eva_http_ingress_requests','eva_http_client_ack_bindings_v3',"
      + "'eva_http_ingress_claim_events')").get().value;
    const httpBoundary = module.db.prepare("SELECT count(*) AS value "
      + "FROM main.sqlite_master WHERE type='trigger' "
      + "AND name='eva_turn_understanding_authorities_v1_http_live_claim'").get().value;
    const row = module.db.prepare(
      "SELECT channel,message_id,source_body_json FROM main.eva_turn_understanding_authorities_v1 LIMIT 1",
    ).get() || null;
    const authority = module.db.prepare(
      "SELECT contract,source_claim_event_ref,authority_sha256 FROM main.eva_turn_understanding_authorities_v1 LIMIT 1",
    ).get() || null;
    let successor=null;
    if (${JSON.stringify(mode === 'current-http-v1-successor')}) {
      const legacy=module.db.prepare(
        "SELECT * FROM main.eva_turn_understanding_authorities_v1 WHERE source_generation=1"
      ).get();
      const execution=module.db.prepare(
        "SELECT * FROM main.turn_tool_executions WHERE execution_key=?"
      ).get(legacy.execution_key);
      const request=module.db.prepare(
        "SELECT * FROM main.eva_http_ingress_requests WHERE request_ref=?"
      ).get(legacy.source_ref);
      const current=module.db.prepare(
        "SELECT * FROM main.eva_http_ingress_claim_events WHERE request_row_id=? "
          + "AND phase='execution' ORDER BY generation DESC,sequence DESC LIMIT 1"
      ).get(request.id);
      if (current.action!=='acquired' || Number(current.generation)!==1) {
        throw new Error('HTTP-v1 successorfixture mist live gen1 acquired claim');
      }
      const insertClaim=(body,createdTs)=>{
        const eventJson=canonicalJson(body);
        const eventSha256=sha256(eventJson);
        const claimEventRef='http-claim-event:'+eventSha256;
        module.db.prepare("INSERT INTO main.eva_http_ingress_claim_events ("
          + "contract,claim_event_ref,claim_event_sha256,request_row_id,request_ref,phase,"
          + "generation,sequence,action,token_sha256,lease_until_ts,event_json,created_ts"
          + ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)").run(
          body.contract,claimEventRef,eventSha256,request.id,request.request_ref,
          body.phase,body.generation,body.sequence,body.action,body.tokenSha256,
          body.leaseUntil,eventJson,createdTs,
        );
        return claimEventRef;
      };
      const releasedAt=new Date(Math.max(
        Date.now(),Date.parse(current.created_ts),
      )).toISOString();
      insertClaim({
        action:'released',contract:'HttpIngressClaimEvent/v1',generation:1,
        leaseUntil:current.lease_until_ts,phase:'execution',requestRef:request.request_ref,
        sequence:1,tokenSha256:current.token_sha256,
      },releasedAt);
      const acquiredAt=new Date(Math.max(
        Date.now(),Date.parse(releasedAt)+1,
      )).toISOString();
      const leaseUntil=new Date(Date.parse(acquiredAt)+120000).toISOString();
      const tokenSha256=sha256('test-only-migrated-http-v2:'+request.request_ref);
      const claimEventRef=insertClaim({
        action:'acquired',contract:'HttpIngressClaimEvent/v1',generation:2,
        leaseUntil,phase:'execution',requestRef:request.request_ref,
        sequence:0,tokenSha256,
      },acquiredAt);
      const draft={
        contract:'TurnUnderstandingExecutionAuthority/v2',
        execution_key:legacy.execution_key,execution_seq:Number(legacy.execution_seq),
        intent_record_sha256:legacy.intent_record_sha256,channel:legacy.channel,
        conversation_id:legacy.conversation_id,message_id:legacy.message_id,
        source_kind:legacy.source_kind,source_ref:legacy.source_ref,
        source_sha256:legacy.source_sha256,source_body_json:legacy.source_body_json,
        source_generation:2,source_claim_event_ref:claimEventRef,
        authority_sha256:'',issued_ts:acquiredAt,
      };
      draft.authority_sha256=sha256(canonicalJson(
        authorityModule.turnUnderstandingAuthorityBody(draft,execution)
      ));
      module.db.prepare("INSERT INTO main.eva_turn_understanding_authorities_v1 ("
        + "contract,execution_key,execution_seq,intent_record_sha256,channel,"
        + "conversation_id,message_id,source_kind,source_ref,source_sha256,source_body_json,"
        + "source_generation,source_claim_event_ref,authority_sha256,issued_ts"
        + ") VALUES (@contract,@execution_key,@execution_seq,@intent_record_sha256,@channel,"
        + "@conversation_id,@message_id,@source_kind,@source_ref,@source_sha256,"
        + "@source_body_json,@source_generation,@source_claim_event_ref,"
        + "@authority_sha256,@issued_ts)").run(draft);
      const v1RowsBase64=Buffer.from(canonicalJson(module.db.prepare(
        ${JSON.stringify(`SELECT ${proofFields} FROM main.${table}
          WHERE source_generation=1 ORDER BY execution_key`)}
      ).all())).toString('base64');
      const history=module.db.prepare(
        "SELECT contract,source_generation,source_claim_event_ref,authority_sha256 "
          + "FROM main.eva_turn_understanding_authorities_v1 "
          + "WHERE execution_key=? ORDER BY source_generation"
      ).all(legacy.execution_key);
      const latest=authorityModule.turnUnderstandingAuthorityRecord(module.db,execution);
      successor={v1RowsBase64,history,latest:{
        contract:latest?.contract,sourceGeneration:latest?.sourceGeneration,
        sourceClaimEventRef:latest?.sourceClaimEventRef,
        authoritySha256:latest?.authoritySha256,
      }};
    }
    const authorityRows=module.db.prepare(
      "SELECT contract,execution_key,channel,source_kind,source_ref,source_sha256,"
        + "source_body_json,source_generation,source_claim_event_ref,authority_sha256 "
        + "FROM main.eva_turn_understanding_authorities_v1 "
        + "ORDER BY execution_key,source_generation"
    ).all();
    const authorityRecords=authorityRows.map((candidate)=>{
      const execution=module.db.prepare(
        "SELECT * FROM main.turn_tool_executions WHERE execution_key=?"
      ).get(candidate.execution_key);
      const record=authorityModule.turnUnderstandingAuthorityRecord(module.db,execution);
      return record?{
        contract:record.contract,channel:record.channel,sourceKind:record.sourceKind,
        sourceRef:record.sourceRef,sourceSha256:record.sourceSha256,
        sourceBodyJson:record.sourceBodyJson??null,
        sourceGeneration:record.sourceGeneration,
        sourceClaimEventRef:record.sourceClaimEventRef??null,
        authoritySha256:record.authoritySha256,
      }:null;
    });
    const afterRowsBase64=Buffer.from(canonicalJson(
      module.db.prepare(${JSON.stringify(`SELECT ${proofFields} FROM main.${table}
        ORDER BY execution_key,source_generation`)}).all()
    )).toString('base64');
    const afterExecutionOutputsBase64=Buffer.from(canonicalJson(
      module.db.prepare("SELECT execution_key,status,output,output_sha256,completion_record_sha256 FROM main.turn_tool_executions WHERE tool='@turn/understanding' ORDER BY execution_key").all()
    )).toString('base64');
    module.db.close();
    process.stdout.write('PREDECESSOR_PROBE='+JSON.stringify({
      ok:true,columns,status,row,authority,httpTables,httpBoundary,
      successor,authorityRows,authorityRecords,
      beforeRowsBase64:${JSON.stringify(beforeRowsBase64)},afterRowsBase64,
      beforeExecutionOutputsBase64:${JSON.stringify(beforeExecutionOutputsBase64)},
      afterExecutionOutputsBase64,
    })+'\\n');
  } catch (error) {
    process.stdout.write('PREDECESSOR_PROBE='+JSON.stringify({
      ok:false,error:String(error?.message||error),cause:String(error?.cause?.message||''),
    })+'\\n');
  }
`], {
  cwd: path.resolve(import.meta.dirname, '..'),
  env: { ...process.env, EVA_DATA_DIR: dataDir },
  encoding: 'utf8',
  timeout: 15_000,
});
const line = probe.stdout.split(/\r?\n/u)
  .find((entry) => entry.startsWith('PREDECESSOR_PROBE='));
if (!line) throw new Error(probe.stderr || probe.stdout || 'predecessor probe ontbreekt');
const result = JSON.parse(line.slice('PREDECESSOR_PROBE='.length));
if (mode === 'current-http-v1-ambiguous') {
  const readonly = new Database(path.join(dataDir, 'eva.db'), {
    readonly: true, fileMustExist: true,
  });
  try {
    const after = migrationRollbackSnapshot(readonly);
    result.rollback = {
      beforeBase64: beforeMigrationRollbackBase64,
      afterBase64: Buffer.from(canonicalJson(after)).toString('base64'),
      afterColumns: after.columns,
    };
  } finally { readonly.close(); }
}
process.stdout.write(`PREDECESSOR_PROBE=${JSON.stringify(result)}\n`);
fs.rmSync(dataDir, { recursive: true, force: true });
