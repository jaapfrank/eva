import assert from 'node:assert/strict';
import test from 'node:test';
import { setTimeout as delay } from 'node:timers/promises';
import { pollTelegramConcurrently } from '../src/integrations/telegrampolling.js';

const until = async (predicate) => {
  const deadline = Date.now() + 1000;
  while (!predicate()) {
    assert.ok(Date.now() < deadline, 'scheduler condition timed out');
    await delay(1);
  }
};

test('bounded polling keeps legacy serial, drains on stop and never acknowledges pending work', async () => {
  const controller = new AbortController();
  const updates = [1, 2, 3, 4, 5].map((id) => ({ update_id: id,
    message: { text: id < 3 ? 'legacy turn' : '/eva2 any content' } }));
  const started = [];
  const release = new Map();
  const offsets = [];
  let active = 0;
  let peak = 0;
  const bot = {
    init: async () => {},
    api: {
      deleteWebhook: async (options) => assert.equal(options.drop_pending_updates, false),
      getUpdates: async ({ offset }) => {
        offsets.push(offset);
        await delay(1);
        return updates.filter((u) => u.update_id >= offset);
      },
    },
    handleUpdate: async (update) => {
      started.push(update.update_id);
      peak = Math.max(peak, ++active);
      await new Promise((resolve) => release.set(update.update_id, resolve));
      active--;
    },
  };
  let ended = false;
  const polling = pollTelegramConcurrently(bot, {
    signal: controller.signal, concurrency: 3, pollIntervalMs: 1,
  }).then(() => { ended = true; });
  try {
    await until(() => started.length === 3);
    assert.deepEqual(started, [1, 3, 4]);
    release.get(3)();
    await until(() => started.includes(5));
    assert.equal(started.includes(2), false);
    assert.equal(peak, 3);
    assert.ok(offsets.every((offset) => offset <= 1));
    controller.abort();
    await delay(5);
    assert.equal(ended, false, 'shutdown waits for admitted work');
    for (const finish of release.values()) finish();
    await polling;
    assert.equal(started.includes(2), false, 'queued legacy turn remains for replay');
  } finally {
    controller.abort();
    for (const finish of release.values()) finish();
    await polling;
  }
});

test('handler errors release capacity and fatal polling conflicts drain admitted work', async () => {
  const errors = [];
  let finish;
  let calls = 0;
  let activeDone = false;
  const polling = pollTelegramConcurrently({
    init: async () => {},
    api: {
      deleteWebhook: async () => {},
      getUpdates: async () => {
        if (calls++ === 0) return [1, 2].map((id) => ({ update_id: id,
          message: { text: '/eva2 test' } }));
        throw Object.assign(new Error('conflicting poller'), { error_code: 409 });
      },
    },
    handleUpdate: async ({ update_id: id }) => {
      if (id === 1) throw new Error('handler failed');
      await new Promise((resolve) => { finish = resolve; });
      activeDone = true;
    },
  }, { onError: (error) => errors.push(error.message), pollIntervalMs: 1 });
  const rejection = assert.rejects(polling, /conflicting poller/);
  await until(() => calls === 2);
  assert.deepEqual(errors, ['handler failed']);
  assert.equal(activeDone, false);
  finish();
  await rejection;
  assert.equal(activeDone, true);
});
