import { setTimeout as delay } from 'node:timers/promises';
import { eva2TelegramPrompt } from '../telegramdialogue.js';

/** Poll without acknowledging unfinished updates. All middleware still runs.
 * Only explicitly routed Eva2 turns overlap; legacy handlers retain one lane.
 * Telegram replays the unfinished prefix after a crash, where the existing
 * durable archive, processing claims and outbox deduplicate it.
 */
export async function pollTelegramConcurrently(bot, {
  signal,
  concurrency = 8,
  onError = () => {},
  pollIntervalMs = 250,
} = {}) {
  if (!Number.isInteger(concurrency) || concurrency < 2 || concurrency > 32) {
    throw new RangeError('Telegram concurrency must be between 2 and 32');
  }
  await bot.init();
  await bot.api.deleteWebhook({ drop_pending_updates: false }, signal);
  const records = new Map();
  const running = new Set();
  let offset = 0;
  let legacyRunning = false;
  const advance = () => {
    for (const [id, record] of records) {
      if (!record.done) break;
      offset = id + 1;
      records.delete(id);
    }
  };
  try {
    while (!signal?.aborted) {
      advance();
      let updates;
      try {
        updates = await bot.api.getUpdates({ offset, limit: 100, timeout: 30, allowed_updates: [] }, signal);
      } catch (error) {
        if (signal?.aborted) break;
        if (error.error_code === 401 || error.error_code === 409) throw error;
        onError(error);
        const retrySeconds = error.parameters?.retry_after;
        await delay(Number.isFinite(retrySeconds) ? Math.max(1, retrySeconds) * 1000 : 3000,
          undefined, { signal });
        continue;
      }
      for (const update of updates) {
        if (update.update_id < offset || records.has(update.update_id)) continue;
        records.set(update.update_id, { update, started: false, done: false });
      }
      for (const record of records.values()) {
        if (signal?.aborted || running.size >= concurrency) break;
        if (record.started) continue;
        const concurrent = typeof record.update.message?.text === 'string'
          && eva2TelegramPrompt(record.update.message.text) !== null;
        if (!concurrent && legacyRunning) continue;
        record.started = true;
        if (!concurrent) legacyRunning = true;
        const task = Promise.resolve().then(() => bot.handleUpdate(record.update))
          .catch((error) => { onError(error); }).finally(() => {
            record.done = true;
            if (!concurrent) legacyRunning = false;
            running.delete(task);
          });
        running.add(task);
        // Observe a failing error reporter too, even before shutdown drains.
        task.catch(() => {});
      }
      // An unfinished prefix is immediately replayed by getUpdates. Bound the
      // replay rate while continuing to discover later updates in that window.
      if (records.size) await delay(pollIntervalMs, undefined, { signal });
    }
  } catch (error) {
    if (!signal?.aborted) throw error;
  } finally {
    // Stopping polling does not cancel admitted model/tool work.
    await Promise.allSettled(running);
  }
}
