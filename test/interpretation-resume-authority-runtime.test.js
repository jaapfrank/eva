import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-retry-runtime-authority-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_AUTONOMY_LEVEL = 'full';
process.env.OPENAI_API_KEY = 'test-only';

const { db } = await import('../src/db.js');
const { runAutonomousTurn } = await import('../src/mind/agent.js');
const {
  createInterpretationRetryHandoff,
} = await import('../src/mind/portfolio.js');

after(() => {
  if (db.open) db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('een retry met verloren exact-origin-authority bereikt model noch tools', async () => {
  const created = createInterpretationRetryHandoff({
    channel: 'web',
    conversationId: 'web:retry-authority-runtime',
    messageId: 'request-authority-runtime-1',
    requester: 'Jaap',
    originalText: 'Lees het bewijs en schrijf daarna alleen een aantoonbaar resultaat.',
    failureClass: 'material_completion_pending',
    sessionRef: 'initial-runtime-turn',
    supersessionScope: 'web:web:retry-authority-runtime',
    supersessionToken: 'request-authority-runtime-1',
  });
  const storedWork = db.prepare('SELECT state_json FROM eva_work_items WHERE id=?')
    .get(created.workItem.id);
  const forgedState = JSON.parse(storedWork.state_json);
  forgedState.interpretation_retry.originalText += ' [gemanipuleerd]';
  db.prepare(`UPDATE eva_work_items SET state_json=?,revision=revision+1 WHERE id=?`)
    .run(JSON.stringify(forgedState), created.workItem.id);

  const originalFetch = globalThis.fetch;
  let providerCalls = 0;
  globalThis.fetch = async () => {
    providerCalls += 1;
    throw new Error('provider hoort niet bereikbaar te zijn');
  };
  let result;
  try {
    result = await runAutonomousTurn({
      reason: 'hervat exact geregistreerd werk',
      workId: created.workItem.id,
      goalEpisodeId: created.episode.id,
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(providerCalls, 0, JSON.stringify(result));
  assert.equal(result.error, 'interpretation_retry_ingress_integrity');
  assert.equal(result.failureStage, 'authority');
  assert.equal(result.interpretationFailed, true);
  assert.deepEqual(result.tools, []);
  assert.equal(db.prepare(`SELECT COUNT(*) AS count FROM turn_tool_executions
    WHERE tool<>'@turn/understanding'`).get().count, 0);
});
