import { createHash, generateKeyPairSync } from 'node:crypto';

const [mode, dataDir] = process.argv.slice(2);
if (!['seed', 'seed-valid', 'inspect', 'release'].includes(mode) || !dataDir) {
  throw new Error('usage: http-turn-authority-retroactive-fixture.mjs MODE DATA_DIR');
}
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_RUNTIME_DISABLE_INFERENCE = 'true';
process.env.EVA_RUNTIME_DISABLE_WORLD_LEARNING = 'true';

const { db } = await import('../src/db.js');
const { canonicalJson } = await import('../src/util.js');

function persistentSnapshot() {
  const authorityRows = db.prepare(`SELECT * FROM main.eva_turn_understanding_authorities_v1
    ORDER BY execution_key,source_generation`).all();
  const authoritySchema = db.prepare(`SELECT type,name,tbl_name,sql FROM main.sqlite_master
    WHERE tbl_name='eva_turn_understanding_authorities_v1'
       OR name='eva_turn_understanding_authorities_v1_http_live_claim'
    ORDER BY type,name`).all();
  return {
    schemaVersion: Number(db.pragma('schema_version', { simple: true })),
    httpBoundary: Number(db.prepare(`SELECT count(*) AS value FROM main.sqlite_master
      WHERE type='trigger'
        AND name='eva_turn_understanding_authorities_v1_http_live_claim'`).get().value),
    authorityRowsBase64: Buffer.from(canonicalJson(authorityRows)).toString('base64'),
    authoritySchemaBase64: Buffer.from(canonicalJson(authoritySchema)).toString('base64'),
  };
}

if (mode === 'inspect') {
  const before = persistentSnapshot();
  const { httpIngressStoreStatus } = await import('../src/web/httpingress.js');
  console.log(JSON.stringify({
    ...httpIngressStoreStatus(), before, after: persistentSnapshot(),
  }));
  db.close();
  process.exit(0);
}

const {
  TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_NAME,
  turnUnderstandingAuthorityBody,
} = await import('../src/turnauthority.js');
const {
  beginHttpIngress,
  verifiedHttpIngressExecutionAuthority,
} = await import('../src/web/httpingress.js');

const sha256 = (value) => createHash('sha256').update(value).digest('hex');

if (mode === 'release') {
  const authority = db.prepare(`SELECT source_ref,source_generation,issued_ts
    FROM main.eva_turn_understanding_authorities_v1 LIMIT 1`).get();
  const latest = db.prepare(`SELECT * FROM main.eva_http_ingress_claim_events
    WHERE request_ref=? AND phase='execution' ORDER BY generation DESC,sequence DESC
    LIMIT 1`).get(authority.source_ref);
  const body = {
    action: 'released', contract: 'HttpIngressClaimEvent/v1',
    generation: Number(authority.source_generation), leaseUntil: latest.lease_until_ts,
    phase: 'execution', requestRef: authority.source_ref,
    sequence: Number(latest.sequence) + 1, tokenSha256: latest.token_sha256,
  };
  const eventJson = canonicalJson(body);
  const eventSha256 = sha256(eventJson);
  db.prepare(`INSERT INTO main.eva_http_ingress_claim_events (
    contract,claim_event_ref,claim_event_sha256,request_row_id,request_ref,phase,
    generation,sequence,action,token_sha256,lease_until_ts,event_json,created_ts
  ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    body.contract, `http-claim-event:${eventSha256}`, eventSha256,
    latest.request_row_id, body.requestRef, body.phase, body.generation,
    body.sequence, body.action, body.tokenSha256, body.leaseUntil,
    eventJson, authority.issued_ts,
  );
  console.log(JSON.stringify({ released: true }));
  db.close();
  process.exit(0);
}

const pair = generateKeyPairSync('ec', { namedCurve: 'P-256' });
const jwk = pair.publicKey.export({ format: 'jwk' });
const channel = 'web';
const conversationId = 'web:retroactive-http-sidecar';
const requestId = 'http-request:retroactive-http-sidecar:1';
const intent = {
  contract: 'PublicHttpIntent/v1', route: '/api/chat', sender: 'Jaap',
  rawText: 'Blokkeer deze vooraf ingevoegde late sidecar.', conversationId,
  syntheticTest: true,
  clientAckPublicKey: {
    contract: 'HttpClientAckPublicKey/v3', algorithm: 'ES256',
    kty: 'EC', crv: 'P-256', x: jwk.x, y: jwk.y,
  },
};
const begun = beginHttpIngress({ channel, conversationId, requestId, intent });
if (begun.kind !== 'started') throw new Error(`unexpected begin kind: ${begun.kind}`);
const source = verifiedHttpIngressExecutionAuthority(begun.preparationAuthority, {
  channel, conversationId, requestId, intent,
});
if (!source) throw new Error('fixture mist echte HTTP execution-authority');
const identity = { channel, conversationId, messageId: requestId };
const executionKey = `turn-understanding:${sha256(canonicalJson(identity))}`;
const turnId = `ingress:${channel}:${conversationId}:${requestId}`;
const argsValue = {
  ...identity, requester: intent.sender, originalText: intent.rawText.trim(),
  sessionRef: '', supersessionScope: null, supersessionToken: null,
  syntheticTest: true,
};
const args = canonicalJson(argsValue);
const executionSeq = Number(db.prepare(
  'SELECT coalesce(max(execution_seq),0)+1 AS value FROM main.turn_tool_executions',
).get().value);
const semanticHash = sha256(canonicalJson({
  turnId, tool: '@turn/understanding', args: argsValue, binding: null,
}));
const intentRecordSha256 = sha256(canonicalJson({
  executionContract: 'ExecutionRecord/v2', executionSeq, turnId,
  tool: '@turn/understanding', args: argsValue, binding: null, semanticHash,
}));
db.prepare(`INSERT INTO main.turn_tool_executions (
  execution_key,turn_id,tool,args,status,started_ts,semantic_hash,
  semantic_binding,intent_record_sha256,execution_seq,execution_contract,
  execution_verification
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
  executionKey, turnId, '@turn/understanding', args, 'started',
  new Date().toISOString(), semanticHash, null, intentRecordSha256,
  executionSeq, 'ExecutionRecord/v2', 'verified',
);
const execution = db.prepare(`SELECT * FROM main.turn_tool_executions
  WHERE execution_key=?`).get(executionKey);
const claim = db.prepare(`SELECT created_ts FROM main.eva_http_ingress_claim_events
  WHERE request_ref=? AND phase='execution' AND generation=?
  ORDER BY sequence DESC LIMIT 1`).get(source.requestRef, source.generation);
const draft = {
  contract: 'TurnUnderstandingExecutionAuthority/v1',
  execution_key: execution.execution_key,
  execution_seq: Number(execution.execution_seq),
  intent_record_sha256: execution.intent_record_sha256,
  channel, conversation_id: conversationId, message_id: requestId,
  source_kind: 'http_ingress', source_ref: source.requestRef,
  source_sha256: source.intentSha256, source_body_json: null,
  source_generation: Number(source.generation),
  source_claim_event_ref: source.claimEventRef, authority_sha256: '',
  issued_ts: mode === 'seed-valid' ? claim.created_ts
    : new Date(Date.parse(claim.created_ts) - 1).toISOString(),
};
draft.authority_sha256 = sha256(canonicalJson(
  turnUnderstandingAuthorityBody(draft, execution),
));
db.exec(`DROP TRIGGER main.${TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_NAME}`);
db.prepare(`INSERT INTO main.eva_turn_understanding_authorities_v1 (
  contract,execution_key,execution_seq,intent_record_sha256,channel,
  conversation_id,message_id,source_kind,source_ref,source_sha256,source_body_json,
  source_generation,source_claim_event_ref,authority_sha256,issued_ts
) VALUES (@contract,@execution_key,@execution_seq,@intent_record_sha256,@channel,
  @conversation_id,@message_id,@source_kind,@source_ref,@source_sha256,@source_body_json,
  @source_generation,@source_claim_event_ref,@authority_sha256,@issued_ts)`).run(draft);
console.log(JSON.stringify({ seeded: true, executionKey }));
db.close();
