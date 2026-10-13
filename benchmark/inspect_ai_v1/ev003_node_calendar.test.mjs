import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { setTimeout as delay } from 'node:timers/promises';
import { installScenarioCalendar } from './ev003_node_calendar.mjs';

test('ticking Date-only calendar advances atomically and rejects invalid updates', async () => {
  const NativeDate = Date;
  const originalTimer = globalThis.setTimeout;
  const originalPerformance = performance.now;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-calendar-'));
  const file = path.join(dir, 'clock.json');
  const publish = (generation, offsetMilliseconds) => {
    fs.writeFileSync(file + '.next', JSON.stringify({
      contract: 'EvaScenarioCalendar/v1', generation, offsetMilliseconds,
    }));
    fs.renameSync(file + '.next', file);
  };
  let calendar;
  try {
    fs.writeFileSync(file, '{}');
    assert.throws(() => installScenarioCalendar(file));
    assert.equal(Date, NativeDate);
    publish(0, 86400000);
    calendar = installScenarioCalendar(file);
    assert.equal(globalThis.setTimeout, originalTimer);
    assert.equal(performance.now, originalPerformance);
    const real = NativeDate.now();
    assert.ok(Math.abs(Date.now() - real - 86400000) < 100);
    assert.ok(new Date() instanceof NativeDate);
    assert.equal(new Date(0).toISOString(), '1970-01-01T00:00:00.000Z');
    assert.equal(Date.parse('2000-01-01T00:00:00Z'), NativeDate.UTC(2000, 0, 1));
    assert.equal(Date(), new NativeDate(Date.now()).toString());
    const before = Date.now();
    const elapsed = performance.now();
    await delay(25);
    assert.ok(Date.now() - before >= 15);
    assert.ok(performance.now() - elapsed >= 15);
    const timer = delay(25).then(() => performance.now());
    publish(1, 172800000);
    assert.ok(Date.now() - before >= 86400000);
    await timer; // A calendar jump does not postpone the native timer.
    publish(0, 172800000);
    assert.throws(() => Date.now(), /backwards/);
    publish(1, 172800001);
    assert.throws(() => new Date(), /generation/);
    publish(2, 1);
    assert.throws(() => Date(), /backwards/);
    fs.writeFileSync(file, 'broken');
    assert.throws(() => Date.now());
    publish(2, 172800000);
    assert.equal(calendar.refresh().generation, 2);
    calendar.restore();
    calendar.restore();
    assert.equal(Date, NativeDate);
  } finally {
    calendar?.restore();
    fs.rmSync(dir, { recursive: true });
  }
});
