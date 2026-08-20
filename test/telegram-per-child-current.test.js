import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

const projectRoot = path.resolve(import.meta.dirname, '..');

async function perChildCurrentScenario(kind) {
  const assert = (await import('node:assert/strict')).default;
  await import('./test/telegram-test-composition.js');
  const {
    db,
    telegramMessage,
    upsertPerson,
  } = await import('./src/db.js');
  const { setPersonHouseholdRole } = await import('./src/mind/household.js');
  const telegram = await import('./src/integrations/telegram.js');
  const { setBotForTest } = telegram.__telegramTransportTest;

  const member = (name, telegramId) => {
    const personId = upsertPerson(name, { telegram_id: telegramId, notes: 'testbewoner' });
    setPersonHouseholdRole(personId, 'household_member', { source: 'test' });
    return personId;
  };
  const changeTelegramId = (personId, nextId) => {
    const changed = db.prepare('UPDATE people SET telegram_id=? WHERE id=?')
      .run(nextId, personId).changes;
    assert.equal(changed, 1, 'de proef moet aantoonbaar één gezinsbinding wijzigen');
  };

  try {
    if (kind === 'broadcast_text_drift') {
      member('Per-child Alpha Text', '8101');
      const secondPerson = member('Per-child Beta Text', '8102');
      const sends = [];
      const deletes = [];
      setBotForTest({ api: {
        sendMessage: async (chatId, body) => {
          sends.push({ chatId: String(chatId), body });
          if (sends.length === 1) changeTelegramId(secondPerson, '8199');
          return {
            chat: { id: chatId }, message_id: 9_101,
            date: 1_786_100_101, text: body,
          };
        },
        deleteMessage: async (chatId, messageId) => {
          deletes.push({ chatId: String(chatId), messageId: Number(messageId) });
          return true;
        },
      } });

      const result = await telegram.sendToFamily(
        `${'A'.repeat(4_000)} ${'B'.repeat(400)}`,
        'gezin',
        {
          dedupeKey: 'per-child-current-text-drift',
          broadcast: true,
          householdOnly: true,
          expectedTargetIds: ['8101', '8102'],
          checkCurrentBeforeEachTransport: true,
          isCurrent: () => true,
        },
      );

      assert.equal(sends.length, 1,
        'mappingdrift na de eerste acceptatie moet zowel het tweede chunk als de tweede ontvanger stoppen');
      assert.equal(sends[0].chatId, '8101');
      assert.equal(result.accepted, false);
      assert.equal(result.stale, true);
      assert.equal(result.visible, false);
      assert.equal(result.uncertain, false);
      assert.equal(result.sent.length, 1,
        'de geaccepteerde prefix moet in het fanoutresultaat worden meegenomen');
      assert.deepEqual(result.retraction, {
        ok: true, removed: 1, total: 1, visible: false,
      });
      assert.deepEqual(deletes, [{ chatId: '8101', messageId: 9_101 }]);
      const archived = telegramMessage('8101', '9101');
      assert.equal(archived?.deleted, true);
      assert.equal(archived?.meta?.retracted, true);
      assert.equal(archived?.meta?.retractionPending, false);
      assert.equal(archived?.meta?.retractionUncertain, false);
      return;
    }

    if (kind === 'family_photo_drift') {
      member('Per-child Alpha Photo', '8201');
      const secondPerson = member('Per-child Beta Photo', '8202');
      const sends = [];
      const deletes = [];
      setBotForTest({ api: {
        sendPhoto: async (chatId, _file, options = {}) => {
          sends.push({ chatId: String(chatId), caption: options.caption });
          if (sends.length === 1) changeTelegramId(secondPerson, '8299');
          return {
            chat: { id: chatId }, message_id: 9_201,
            date: 1_786_100_201, caption: options.caption,
            photo: [{ file_id: 'photo-9201' }],
          };
        },
        deleteMessage: async (chatId, messageId) => {
          deletes.push({ chatId: String(chatId), messageId: Number(messageId) });
          return true;
        },
      } });

      const result = await telegram.sendPhotoToFamily(
        Buffer.from('private-doorbell-photo'),
        'image/jpeg',
        'Iemand staat bij de deur.',
        'gezin',
        {
          dedupeKey: 'per-child-current-photo-drift',
          broadcast: true,
          householdOnly: true,
          expectedTargetIds: ['8201', '8202'],
          checkCurrentBeforeEachTransport: true,
          isCurrent: () => true,
        },
      );

      assert.deepEqual(sends, [{ chatId: '8201', caption: 'Iemand staat bij de deur.' }],
        'mappingdrift tijdens het eerste media-child moet de volgende ontvanger voorkomen');
      assert.equal(result.accepted, false);
      assert.equal(result.stale, true);
      assert.equal(result.visible, false);
      assert.equal(result.uncertain, false);
      assert.equal(result.sent.length, 1);
      assert.equal(result.photoRef, null,
        'een ingetrokken foto mag niet als beschikbaar fotobewijs worden onthouden');
      assert.deepEqual(result.retraction, {
        ok: true, removed: 1, total: 1, visible: false,
      });
      assert.deepEqual(deletes, [{ chatId: '8201', messageId: 9_201 }]);
      const archived = telegramMessage('8201', '9201');
      assert.equal(archived?.deleted, true);
      assert.equal(archived?.meta?.retracted, true);
      return;
    }

    if (kind === 'monitoring_deadline_between_chunks') {
      member('Monitoring Deadline', '8301');
      const NativeDate = Date;
      let nowMs = NativeDate.parse('2026-08-16T10:00:00.000Z');
      globalThis.Date = class TestDate extends NativeDate {
        constructor(...args) { super(...(args.length ? args : [nowMs])); }
        static now() { return nowMs; }
      };
      const sends = [];
      try {
        setBotForTest({ api: {
          sendMessage: async (chatId, body) => {
            sends.push({ chatId: String(chatId), body });
            if (sends.length === 1) {
              nowMs = NativeDate.parse('2026-08-16T10:01:30.001Z');
            }
            return {
              chat: { id: chatId }, message_id: 9_301 + sends.length,
              date: 1_786_100_301, text: body,
            };
          },
          deleteMessage: async () => true,
        } });
        const result = await telegram.sendToFamily(
          `${'A'.repeat(4_000)} ${'B'.repeat(400)}`,
          'Monitoring Deadline',
          {
            dedupeKey: 'monitoring-deadline-between-chunks',
            broadcast: false,
            directReply: false,
            checkCurrentBeforeEachTransport: true,
            isCurrent: () => true,
            monitoringTransportLimits: {
              horizonAt: '2026-08-16T10:02:00.000Z',
              latestWireStart: '2026-08-16T10:00:30.000Z',
              wireNotAfter: '2026-08-16T10:01:30.000Z',
            },
          },
        );
        assert.equal(sends.length, 1,
          `na de childdeadline mag geen tweede Telegram-API-aanbod volgen: ${JSON.stringify(result)}`);
        assert.equal(result.accepted, false);
        assert.equal(result.uncertain, true,
          'een zichtbare prefix zonder volledige fanout blijft onzeker en niet retrybaar');
      } finally {
        globalThis.Date = NativeDate;
      }
      return;
    }

    if (kind === 'monitoring_deadline_aborts_wire') {
      member('Monitoring Abort', '8401');
      let calls = 0;
      let receivedSignal = null;
      setBotForTest({ api: {
        sendMessage: async (_chatId, _body, _options, signal) => {
          calls += 1;
          receivedSignal = signal;
          return new Promise((_resolve, reject) => {
            signal.addEventListener('abort', () => {
              reject(Object.assign(new Error('test deadline abort'), { name: 'AbortError' }));
            }, { once: true });
          });
        },
      } });
      const base = Date.now();
      const iso = (offset) => new Date(base + offset).toISOString();
      const result = await telegram.sendToFamily(
        'Dit bericht mag de begrensde wire-call niet overleven.',
        'Monitoring Abort',
        {
          dedupeKey: 'monitoring-deadline-aborts-wire',
          broadcast: false,
          directReply: false,
          checkCurrentBeforeEachTransport: true,
          isCurrent: () => true,
          monitoringTransportLimits: {
            latestWireStart: iso(100), wireNotAfter: iso(150), horizonAt: iso(180),
          },
        },
      );
      assert.equal(calls, 1);
      assert.ok(receivedSignal instanceof AbortSignal);
      assert.equal(receivedSignal.aborted, true);
      assert.equal(result.accepted, false);
      assert.equal(result.uncertain, true);
      return;
    }

    throw new Error(`onbekend scenario ${kind}`);
  } finally {
    setBotForTest(null);
    db.close();
  }
}

function runIsolatedScenario(kind) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `bottie-telegram-current-${kind}-`));
  try {
    const result = spawnSync(process.execPath, ['--input-type=module', '-e',
      `(${perChildCurrentScenario.toString()})(${JSON.stringify(kind)})
        .then(() => process.exit(0))
        .catch((error) => { console.error(error); process.exit(1); });`], {
      cwd: projectRoot,
      env: {
        ...process.env,
        EVA_DATA_DIR: dataDir,
        EVA_RUNTIME_DISABLE_INFERENCE: 'true',
        EVA_RUNTIME_DISABLE_WORLD_LEARNING: 'true',
        NODE_TEST_CONTEXT: process.env.NODE_TEST_CONTEXT || 'child-v8',
        TELEGRAM_BOT_TOKEN: '',
        TELEGRAM_ALLOWED_IDS: '8101,8102,8199,8301,8401',
        TMPDIR: '/tmp',
      },
      encoding: 'utf8',
      timeout: 30_000,
    });
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
}

test('broadcasttekst stopt en trekt haar prefix in bij mappingdrift tijdens het eerste child', () => {
  runIsolatedScenario('broadcast_text_drift');
});

test('familiefoto stopt vóór de tweede ontvanger bij mappingdrift tijdens het eerste child', () => {
  runIsolatedScenario('family_photo_drift');
});

test('monitoringdeadline wordt vlak vóór ieder Telegram-child opnieuw afgedwongen', () => {
  runIsolatedScenario('monitoring_deadline_between_chunks');
});

test('monitoringdeadline breekt een nog lopende Telegram-wirecall af', () => {
  runIsolatedScenario('monitoring_deadline_aborts_wire');
});
