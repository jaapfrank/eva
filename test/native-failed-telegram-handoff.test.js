import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('native startup backfill sluit pas na exact chat-, thread- en replygebonden receipt', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-native-failed-telegram-'));
  try {
    const child = spawnSync(process.execPath, [
      'test/native-failed-telegram-handoff-fixture.mjs', dataDir,
    ], {
      cwd: projectRoot, env: { ...process.env, EVA_DATA_DIR: dataDir, TMPDIR: '/tmp' },
      encoding: 'utf8', timeout: 30_000,
    });
    assert.equal(child.status, 0, child.stderr || child.stdout);
    const match = child.stdout.match(/NATIVE_FAILED_TELEGRAM=(\{.*\})/u);
    assert.ok(match, child.stdout);
    assert.deepEqual(JSON.parse(match[1]), {
      workItemId: 1, episodeId: 1, workStatus: 'done', episodeState: 'closed',
      inboundStatus: 'completed', sends: 1, providerCalls: 1, outboundChatId: '85001',
      outboundReplyTo: '61001', outboundThread: '19',
      receiptOriginConversationId: 'telegram:85001', receiptOriginMessageId: '61001',
    });
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
