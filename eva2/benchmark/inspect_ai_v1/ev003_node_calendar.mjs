/** Evaluator-only ticking calendar. The controller must settle work before advancing. */
import fs from 'node:fs';

const NativeDate = globalThis.Date;
let installed = false;

export function installScenarioCalendar(clockFile) {
  if (installed || globalThis.Date !== NativeDate) throw new Error('Date already replaced');
  let previous;
  function refresh() {
    // One read opens one inode: the controller publishes updates by atomic rename.
    const value = JSON.parse(fs.readFileSync(clockFile, 'utf8'));
    if (!value || typeof value !== 'object' || Array.isArray(value)
        || Object.keys(value).sort().join(',') !== 'contract,generation,offsetMilliseconds'
        || value.contract !== 'EvaScenarioCalendar/v1'
        || !Number.isSafeInteger(value.generation) || value.generation < 0
        || typeof value.offsetMilliseconds !== 'number' || !Number.isFinite(value.offsetMilliseconds)
        || !Number.isFinite(new NativeDate(NativeDate.now() + value.offsetMilliseconds).getTime())) {
      throw new Error('Invalid scenario calendar');
    }
    if (previous && (value.generation < previous.generation
        || value.offsetMilliseconds < previous.offsetMilliseconds
        || (value.generation === previous.generation
            && value.offsetMilliseconds !== previous.offsetMilliseconds))) {
      throw new Error('Scenario calendar moved backwards or changed without a generation');
    }
    previous = Object.freeze({ ...value });
    return previous;
  }
  function now() { return NativeDate.now() + refresh().offsetMilliseconds; }
  function ScenarioDate(...args) {
    if (!new.target) return new NativeDate(now()).toString();
    return Reflect.construct(NativeDate, args.length ? args : [now()], new.target);
  }
  Object.setPrototypeOf(ScenarioDate, NativeDate);
  ScenarioDate.prototype = NativeDate.prototype;
  Object.defineProperty(ScenarioDate, 'now', { value: now });
  refresh(); // Refuse bad input before installing any patch.
  globalThis.Date = ScenarioDate;
  installed = true;
  let restored = false;
  return Object.freeze({
    refresh,
    restore() {
      if (restored) return;
      if (globalThis.Date !== ScenarioDate) throw new Error('Date replacement ownership changed');
      globalThis.Date = NativeDate;
      installed = false;
      restored = true;
    },
  });
}
