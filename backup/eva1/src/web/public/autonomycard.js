const DISPLAY_KEYS = Object.freeze([
  'choice_label', 'reason_label', 'result_expires_ts', 'result_label', 'result_ts',
  'selection_expires_ts', 'selection_ts',
].sort());
const DISPLAY_TEXT = /^[\p{L}\p{M}\p{N}][\p{L}\p{M}\p{N}\s.,;:!?'"()°%/+\-\u2013\u2014]+$/u;
const INTERNAL_REFERENCE = /(?:^|\s)(?:aspiration|exploration|goal|work|worker|episode|turn|signal|request|receipt|tool)(?::|#)[a-z0-9._:-]+(?:$|\s|[.,;!?])/iu;
const UUID = /(?:^|[^a-f0-9])[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}(?:$|[^a-f0-9])/iu;
const HASH = /(?:^|[^a-f0-9])(?:[a-f0-9]{32}|[a-f0-9]{40}|[a-f0-9]{64})(?:$|[^a-f0-9])/iu;

function displayText(value, limit = 500) {
  if (typeof value !== 'string' || value.length < 12 || value.length > limit
    || value !== value.normalize('NFKC')
    || value !== value.replace(/\s+/gu, ' ').trim()
    || !DISPLAY_TEXT.test(value) || /^\d+$/u.test(value) || /^worker$/iu.test(value)
    || INTERNAL_REFERENCE.test(value) || UUID.test(value) || HASH.test(value)) return null;
  const words = value.match(/\p{L}{2,}/gu) || [];
  return words.length >= 3 ? value : null;
}

function exactDisplay(value) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && JSON.stringify(Object.keys(value).sort()) === JSON.stringify(DISPLAY_KEYS)
    ? value : null;
}

function exactTimestamp(value) {
  if (typeof value !== 'string') return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) && new Date(parsed).toISOString() === value ? parsed : null;
}

function freshWindow(issuedValue, expiresValue, now) {
  const issued = exactTimestamp(issuedValue);
  const expires = exactTimestamp(expiresValue);
  return Number.isFinite(issued) && Number.isFinite(expires) && expires >= issued
    && Number(now) >= issued && Number(now) <= expires ? issuedValue : null;
}

export function autonomyCardCopy(status = {}, { now = Date.now() } = {}) {
  const autonomy = status?.autonomy && typeof status.autonomy === 'object'
    ? status.autonomy : {};
  const display = exactDisplay(autonomy.display);
  const selectionTs = autonomy.running === true
    ? freshWindow(display?.selection_ts, display?.selection_expires_ts, now) : null;
  const choice = selectionTs ? displayText(display?.choice_label, 360) : null;
  const reason = selectionTs ? displayText(display?.reason_label, 360) : null;
  const current = choice && reason ? { choice, reason } : null;
  const resultTs = freshWindow(display?.result_ts, display?.result_expires_ts, now);
  const result = resultTs ? displayText(display?.result_label, 500) : null;
  return Object.freeze({
    now: current ? `Nu gekozen: ${current.choice}` : '',
    why: current ? `Waarom: ${current.reason}` : '',
    result: result ? `Laatste resultaat: ${result}` : '',
  });
}

export function createLoadWork({
  fetchStatus, setCurrentFocus, renderStatus, now = () => Date.now(),
  runningFocusFallback = 'Eva werkt zelfstandig; de actuele keuze is niet betrouwbaar zichtbaar.',
} = /** @type {any} */ ({})) {
  if (typeof fetchStatus !== 'function' || typeof setCurrentFocus !== 'function'
    || typeof renderStatus !== 'function' || typeof now !== 'function') {
    throw new TypeError('createLoadWork vereist expliciete browserafhankelijkheden');
  }
  return async function loadWork() {
    const work = await fetchStatus();
    const copy = autonomyCardCopy(work, { now: now() });
    const attestedFocus = copy.now && copy.why ? `${copy.now}. ${copy.why}` : null;
    setCurrentFocus(work?.autonomy?.running === true
      ? attestedFocus || runningFocusFallback
      : work?.current_focus);
    renderStatus(work);
    return work;
  };
}

export function renderAutonomyCard(elements, status = {}, {
  nowFallback = '', whyFallback = '', resultPrefix = '', resultFallback = '', now = Date.now(),
} = {}) {
  const copy = autonomyCardCopy(status, { now });
  elements.now.textContent = copy.now || nowFallback;
  elements.why.textContent = copy.why || whyFallback;
  elements.result.textContent = [resultPrefix, copy.result || resultFallback]
    .filter(Boolean).join(' · ');
  return copy;
}
