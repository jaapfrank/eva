import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

const projectRoot = path.resolve(import.meta.dirname, '..');

async function partialFanoutScenario() {
  const assert = (await import('node:assert/strict')).default;
  const { GrammyError } = await import('grammy');
  await import('./test/telegram-test-composition.js');
  const { db, upsertPerson } = await import('./src/db.js');
  const { setPersonHouseholdRole } = await import('./src/mind/household.js');
  const telegram = await import('./src/integrations/telegram.js');
  const tools = await import('./src/mind/tools.js');
  const {
    applyVisibleCameraDeliveryState,
    cameraReportHistory,
    shouldReportCameraEvent,
  } = await import('./src/mind/perception.js');

  const member = (name, telegramId) => {
    const personId = upsertPerson(name, {
      telegram_id: telegramId,
      notes: 'geautoriseerde testbewoner',
    });
    setPersonHouseholdRole(personId, 'household_member', { source: 'test' });
  };
  member('Partial Alpha', '9101');
  member('Partial Beta', '9102');

  const sends = [];
  telegram.__telegramTransportTest.setBotForTest({ api: {
    sendPhoto: async (chatId, _file, options = {}) => {
      sends.push(String(chatId));
      if (String(chatId) === '9102') {
        throw new GrammyError('definitief geweigerd', {
          ok: false, error_code: 400, description: 'chat not found',
        }, 'sendPhoto', {});
      }
      return {
        chat: { id: chatId }, message_id: 9_101, date: 1_786_200_101,
        caption: options.caption, photo: [{ file_id: 'partial-photo-9101' }],
      };
    },
  } });
  tools.registerPhotoSender(telegram.sendPhotoToFamily);

  const buffer = Buffer.from('camera-incident-partial-fanout');
  const caption = 'Er staat iemand bij de overkapping.';
  const options = {
    broadcast: true,
    householdOnly: true,
    expectedTargetIds: ['9101', '9102'],
    dedupeKey: 'camera:overkapping:partial-fanout-incident',
  };

  try {
    const first = await tools.sendRegisteredPhoto(
      buffer, 'image/jpeg', caption, null, options,
    );
    assert.deepEqual(sends, ['9101', '9102']);
    assert.equal(first.ok, false);
    assert.equal(first.accepted, false);
    assert.equal(first.partial, true);
    assert.equal(first.familyVisible, true,
      'een bewezen zichtbaar child mag niet als volledig onzichtbaar worden geprojecteerd');
    assert.deepEqual(first.acceptedChatIds, ['9101']);
    assert.deepEqual(first.failedChatIds, ['9102']);

    const state = { byCamera: {}, personByCamera: {}, unknownByCamera: {} };
    const fixedNow = 1_786_200_500_000;
    assert.equal(applyVisibleCameraDeliveryState({
      state, key: 'camera.overkapping', detail: { recognized: true, person: 'Jaap' },
      unknown: false, delivery: first, now: fixedNow,
    }), true);
    assert.equal(state.byCamera['camera.overkapping'], fixedNow,
      'partial zichtbaar transport moet de camera-rust/dedupe bijwerken');
    assert.equal(state.personByCamera['camera.overkapping'], 'Jaap');
    const history = cameraReportHistory(state, 'camera.overkapping');
    assert.equal(shouldReportCameraEvent({
      type: 'person_detected',
      detail: { recognized: true, learnable: true, person: 'Jaap' },
      previous: history.previous, previousPerson: history.previousPerson,
      now: fixedNow + 1_000,
    }), false, 'een onmiddellijk volgend incident valt onder de bijgewerkte camera-rust');

    const replay = await tools.sendRegisteredPhoto(
      buffer, 'image/jpeg', caption, null, options,
    );
    assert.equal(replay.replayed, true);
    assert.equal(replay.familyVisible, true);
    assert.deepEqual(replay.acceptedChatIds, ['9101']);
    assert.deepEqual(replay.failedChatIds, ['9102']);
    assert.deepEqual(sends, ['9101', '9102'],
      'replay van hetzelfde incident mag het geslaagde child niet opnieuw versturen');

    for (const forged of [
      {
        ...first,
        sent: [{ ...first.sent[0], operationKey: 'forged-accepted-child' }],
        familyVisible: true, acceptedChatIds: ['9101'], failedChatIds: ['9102'],
      },
      {
        ...first,
        failures: [{ ...first.failures[0], operationKey: 'forged-rejected-child' }],
        familyVisible: true, acceptedChatIds: ['9101'], failedChatIds: ['9102'],
      },
    ]) {
      tools.registerPhotoSender(async () => forged);
      const rejected = await tools.sendRegisteredPhoto(
        buffer, 'image/jpeg', caption, null, options,
      );
      assert.equal(rejected.ok, false);
      assert.equal(rejected.accepted, false);
      assert.equal(rejected.familyVisible, false,
        'door de sender aangeleverde zichtbaarheid is zonder childbewijs waardeloos');
      assert.deepEqual(rejected.acceptedChatIds, []);
      assert.deepEqual(rejected.failedChatIds, []);
      const untouched = { byCamera: {}, personByCamera: {}, unknownByCamera: {} };
      assert.equal(applyVisibleCameraDeliveryState({
        state: untouched, key: 'camera.overkapping', detail: null,
        unknown: true, delivery: rejected, now: fixedNow,
      }), false);
      assert.deepEqual(untouched, { byCamera: {}, personByCamera: {}, unknownByCamera: {} });
    }
  } finally {
    tools.registerPhotoSender(null);
    telegram.__telegramTransportTest.setBotForTest(null);
    db.close();
  }
}

test('partial Telegram-fanout projecteert alleen bewezen zichtbare children en replayt zonder resend', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-partial-fanout-'));
  try {
    const result = spawnSync(process.execPath, ['--input-type=module', '-e',
      `(${partialFanoutScenario.toString()})()
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
        TELEGRAM_ALLOWED_IDS: '9101,9102',
        TMPDIR: '/tmp',
      },
      encoding: 'utf8',
      timeout: 30_000,
    });
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
