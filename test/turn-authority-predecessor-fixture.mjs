import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const mode = process.argv[2] || 'empty';
const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `eva-turn-predecessor-${mode}-`));
process.env.EVA_DATA_DIR = dataDir;

const { db } = await import('../src/db.js');
const {
  TURN_UNDERSTANDING_AUTHORITY_TABLE_SQL,
  TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL,
} = await import('../src/turnauthority.js');

const table = 'eva_turn_understanding_authorities_v1';
const predecessorTableSql = TURN_UNDERSTANDING_AUTHORITY_TABLE_SQL
  .replace('  source_body_json TEXT,\n', '')
  .replace(`  CHECK((channel='web' AND source_body_json IS NULL)
    OR (channel IN ('telegram','voice') AND typeof(source_body_json)='text'
      AND length(CAST(source_body_json AS BLOB))>=2
      AND length(CAST(source_body_json AS BLOB))<=65536
      AND json_valid(source_body_json) AND json_type(source_body_json)='object')),\n`, '');
const predecessorTriggers = {
  ...TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL,
  eva_turn_understanding_authorities_v1_valid_successor:
    TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL
      .eva_turn_understanding_authorities_v1_valid_successor
      .replace(`
            OR existing.source_sha256<>NEW.source_sha256
            OR existing.source_body_json IS NOT NEW.source_body_json))`, `
            OR existing.source_sha256<>NEW.source_sha256))`),
};

const rows = [];
if (mode === 'web-row') {
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
  rows.push(seeded.authority);
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
  db.exec(predecessorTableSql);
  const insert = db.prepare(`INSERT INTO main.${table} (
    contract,execution_key,execution_seq,intent_record_sha256,channel,
    conversation_id,message_id,source_kind,source_ref,source_sha256,
    source_generation,authority_sha256,issued_ts
  ) VALUES (@contract,@execution_key,@execution_seq,@intent_record_sha256,@channel,
    @conversation_id,@message_id,@source_kind,@source_ref,@source_sha256,
    @source_generation,@authority_sha256,@issued_ts)`);
  for (const row of rows) insert.run(row);
  for (const sql of Object.values(predecessorTriggers)) db.exec(sql);
}).immediate();
db.close();

const probe = spawnSync(process.execPath, ['--input-type=module', '-e', `
  try {
    const module = await import('./src/db.js');
    const columns = module.db.prepare(
      "PRAGMA main.table_info('eva_turn_understanding_authorities_v1')",
    ).all().map((column) => column.name);
    const status = module.turnUnderstandingAuthorityStoreStatus();
    const row = module.db.prepare(
      "SELECT channel,message_id,source_body_json FROM main.eva_turn_understanding_authorities_v1 LIMIT 1",
    ).get() || null;
    module.db.close();
    process.stdout.write('PREDECESSOR_PROBE='+JSON.stringify({ok:true,columns,status,row})+'\\n');
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
process.stdout.write(`${line}\n`);
fs.rmSync(dataDir, { recursive: true, force: true });
