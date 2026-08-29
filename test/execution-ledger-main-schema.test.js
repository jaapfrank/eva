import assert from 'node:assert/strict';
import fs from 'node:fs';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync('/tmp/bottie-execution-main-schema-');
process.env.EVA_DATA_DIR = dataDir;

const {
  claimTurnToolExecution,
  completeTurnToolExecution,
  db,
  logTimeline,
  recentVerifiedTurnToolReceipts,
} = await import('../src/db.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('receipt-authority faalt gesloten ondanks main-qualified reads onder TEMP shadows', () => {
  const turnId = 'turn:main-ledger-proof';
  const conversationId = 'web:main-ledger-proof';
  claimTurnToolExecution({
    executionKey: 'execution:main-ledger-proof',
    turnId,
    tool: 'bewijs_tool',
    args: { waarde: 'duurzaam' },
    semanticBinding: {
      understandingSha256: 'a'.repeat(64),
      requirementId: 'r1',
      addressesRequirementIds: ['r1'],
      basisReceiptIds: [],
      capabilityId: 'proof.read',
      operationId: 'read',
      role: 'result',
      executionType: null,
    },
  });
  completeTurnToolExecution('execution:main-ledger-proof', JSON.stringify({ ok: true }));
  logTimeline('turn_understanding', 'duurzame understanding', { conversationId, turnId });

  db.exec(`CREATE TEMP TABLE turn_tool_executions AS
      SELECT * FROM main.turn_tool_executions WHERE 0;
    CREATE TEMP TABLE timeline AS SELECT * FROM main.timeline WHERE 0;`);

  try {
    assert.throws(() => recentVerifiedTurnToolReceipts(conversationId),
      /execution ledger unavailable.*TEMP shadow/u);
  } finally {
    db.exec('DROP TABLE temp.turn_tool_executions; DROP TABLE temp.timeline');
  }
});

test('recente receipts blijven gesprekgebonden en worden niet dubbel door dubbele authority', () => {
  const turnId = 'turn:bounded-receipt-selection';
  const conversationId = 'web:bounded-receipt-selection';
  claimTurnToolExecution({
    executionKey: 'execution:bounded-receipt-selection',
    turnId,
    tool: 'bewijs_tool',
    args: { waarde: 'eenmaal' },
  });
  completeTurnToolExecution(
    'execution:bounded-receipt-selection', JSON.stringify({ ok: true }),
  );
  logTimeline('turn_understanding', 'eerste authority', { conversationId, turnId });
  logTimeline('turn_understanding', 'dubbele authority', { conversationId, turnId });
  logTimeline('turn_understanding', 'vreemd gesprek', {
    conversationId: 'web:ander-gesprek', turnId,
  });

  const receipts = recentVerifiedTurnToolReceipts(conversationId);
  assert.equal(receipts.filter((row) => row.turn_id === turnId).length, 1);
  assert.equal(recentVerifiedTurnToolReceipts('web:onbekend-gesprek').length, 0);
});

test('execution_seq is store-owned, uniek en onveranderlijk', () => {
  const first = claimTurnToolExecution({
    executionKey: 'execution:sequence:first', turnId: 'turn:sequence',
    tool: 'bewijs_tool', args: { index: 1 },
  });
  const second = claimTurnToolExecution({
    executionKey: 'execution:sequence:second', turnId: 'turn:sequence',
    tool: 'bewijs_tool', args: { index: 2 },
  });
  assert.ok(Number.isSafeInteger(first.row.execution_seq));
  assert.equal(second.row.execution_seq, first.row.execution_seq + 1);
  assert.equal(claimTurnToolExecution({
    executionKey: 'execution:sequence:first', turnId: 'turn:sequence',
    tool: 'bewijs_tool', args: { index: 1 },
  }).row.execution_seq, first.row.execution_seq);
  assert.throws(() => db.prepare(`UPDATE main.turn_tool_executions
    SET execution_seq=? WHERE execution_key=?`).run(
    second.row.execution_seq + 1, first.row.execution_key,
  ), /sequence is immutable/u);
  assert.throws(() => db.prepare(`INSERT INTO main.turn_tool_executions
      (execution_key,turn_id,tool,args,status,started_ts,execution_seq)
    VALUES ('execution:sequence:forged','turn:sequence','bewijs_tool','null',
      'started',datetime('now'),0)`).run(), /sequence must be positive/u);
});
