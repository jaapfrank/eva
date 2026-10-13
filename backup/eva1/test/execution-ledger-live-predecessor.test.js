import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

async function livePredecessorScenario() {
  const assert = (await import('node:assert/strict')).default;
  const path = await import('node:path');
  const Database = (await import('better-sqlite3')).default;
  const {
    EXECUTION_LEDGER_INDEX_SQL,
    EXECUTION_LEDGER_TABLE_SQL,
    EXECUTION_LEDGER_TRIGGER_SQL,
    executionLedgerTriggersExact,
  } = await import('./src/executionledger.js');

  const predecessor = {
    ...EXECUTION_LEDGER_TRIGGER_SQL,
    turn_tool_executions_intent_immutable: `CREATE TRIGGER turn_tool_executions_intent_immutable
    BEFORE UPDATE OF turn_id,tool,args,semantic_hash,semantic_binding,
      intent_record_sha256,started_ts,execution_verification ON turn_tool_executions
    WHEN (
      OLD.turn_id IS NOT NEW.turn_id OR OLD.tool IS NOT NEW.tool
      OR OLD.args IS NOT NEW.args OR OLD.semantic_binding IS NOT NEW.semantic_binding
      OR OLD.started_ts IS NOT NEW.started_ts
      OR NOT (
        OLD.execution_contract='ExecutionRecord/v1'
        AND OLD.execution_verification='legacy_unverifiable'
        AND NEW.execution_verification='verified'
        AND OLD.semantic_hash IS NULL AND OLD.intent_record_sha256 IS NULL
        AND NEW.semantic_hash IS NOT NULL AND NEW.intent_record_sha256 IS NOT NULL
        AND OLD.semantic_binding IS NULL AND NEW.semantic_binding IS NULL
        AND instr(OLD.args,'[AFGESCHERMD:')=0
      ) AND (
        OLD.semantic_hash IS NOT NEW.semantic_hash
        OR OLD.intent_record_sha256 IS NOT NEW.intent_record_sha256
        OR OLD.execution_verification IS NOT NEW.execution_verification
      )
    )
    BEGIN SELECT RAISE(ABORT,'turn tool execution intent is immutable'); END`,
    turn_tool_executions_terminal_immutable: `CREATE TRIGGER turn_tool_executions_terminal_immutable
    BEFORE UPDATE OF status,finished_ts,output,output_sha256,material_sha256,
      completion_record_sha256,execution_verification ON turn_tool_executions
    WHEN OLD.status IN ('done','transport_accepted','transport_rejected','transport_uncertain')
      AND (
        OLD.status IS NOT NEW.status OR OLD.finished_ts IS NOT NEW.finished_ts
        OR OLD.output IS NOT NEW.output
        OR NOT (
          OLD.execution_contract='ExecutionRecord/v1'
          AND OLD.execution_verification='legacy_unverifiable'
          AND NEW.execution_verification='verified'
          AND OLD.output_sha256 IS NULL AND OLD.completion_record_sha256 IS NULL
          AND NEW.output_sha256 IS NOT NULL AND NEW.completion_record_sha256 IS NOT NULL
        ) AND (
          OLD.output_sha256 IS NOT NEW.output_sha256
          OR OLD.material_sha256 IS NOT NEW.material_sha256
          OR OLD.completion_record_sha256 IS NOT NEW.completion_record_sha256
          OR OLD.execution_verification IS NOT NEW.execution_verification
        )
      )
    BEGIN SELECT RAISE(ABORT,'turn tool execution terminal record is immutable'); END`,
  };
  const target = path.join(process.env.EVA_DATA_DIR, 'eva.db');
  const seeded = new Database(target);
  seeded.exec(`${EXECUTION_LEDGER_TABLE_SQL};
    ${Object.values(EXECUTION_LEDGER_INDEX_SQL).join(';\n')};`);
  seeded.prepare(`INSERT INTO turn_tool_executions
    (execution_key,turn_id,tool,args,status,started_ts,finished_ts,output,
     execution_seq,execution_contract,execution_verification)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)`).run(
    'live-predecessor:1', 'turn:live-predecessor', 'legacy_probe', '{"value":1}',
    'done', '2026-08-13T00:00:00.000Z', '2026-08-13T00:00:01.000Z',
    '{"legacy":true}', 1, 'ExecutionRecord/v1', 'legacy_unverifiable',
  );
  for (const sql of Object.values(predecessor)) seeded.exec(sql);
  seeded.close();

  const { db } = await import('./src/db.js');
  assert.equal(executionLedgerTriggersExact(db), true);
  assert.deepEqual(db.prepare(`SELECT execution_key,turn_id,tool,args,status,started_ts,
    finished_ts,output,execution_seq,execution_contract,execution_verification
    FROM turn_tool_executions WHERE execution_key=?`).get('live-predecessor:1'), {
    execution_key: 'live-predecessor:1',
    turn_id: 'turn:live-predecessor',
    tool: 'legacy_probe',
    args: '{"value":1}',
    status: 'done',
    started_ts: '2026-08-13T00:00:00.000Z',
    finished_ts: '2026-08-13T00:00:01.000Z',
    output: '{"legacy":true}',
    execution_seq: 1,
    execution_contract: 'ExecutionRecord/v1',
    execution_verification: 'legacy_unverifiable',
  });
  db.close();
}

test('de exacte vorige live-ledgertriggers worden zonder rijverlies definitief gemaakt', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-live-ledger-predecessor-'));
  const result = spawnSync(process.execPath, ['--input-type=module', '-e',
    `(${livePredecessorScenario.toString()})().catch((error) => {
      console.error(error?.stack || error);
      process.exitCode = 1;
    });`], {
    cwd: process.cwd(), encoding: 'utf8', timeout: 20_000,
    env: { ...process.env, EVA_DATA_DIR: dataDir },
  });
  fs.rmSync(dataDir, { recursive: true, force: true });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
});
