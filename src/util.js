export const isoNow = () => new Date().toISOString();

export const clamp = (v, min = 0, max = 1) => Math.min(max, Math.max(min, v));

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

/** Woordoverlap (0..1) tussen twee teksten — getallen/leestekens genegeerd.
 *  Vangt "bijna hetzelfde bericht" (zoals herhaalde meldingen met andere cijfers). */
export function tekstOverlap(a, b) {
  const woorden = (s) =>
    new Set(
      String(s || '')
        .toLowerCase()
        .replace(/[0-9]+/g, ' ')
        .replace(/[^\p{L} ]/gu, ' ')
        .split(/\s+/)
        .filter((w) => w.length > 2)
    );
  const A = woorden(a);
  const B = woorden(b);
  if (!A.size || !B.size) return 0;
  let gedeeld = 0;
  for (const w of A) if (B.has(w)) gedeeld++;
  return gedeeld / Math.min(A.size, B.size);
}

export function parseHHMM(str) {
  const [h, m] = String(str).split(':').map(Number);
  return (h || 0) * 60 + (m || 0);
}

/** Is `date` binnen het slaapvenster [sleepStart, wakeTime)? Venster mag over middernacht lopen. */
export function isNight(date, sleepStart, wakeTime) {
  const mins = date.getHours() * 60 + date.getMinutes();
  const start = parseHHMM(sleepStart);
  const end = parseHHMM(wakeTime);
  if (start <= end) return mins >= start && mins < end;
  return mins >= start || mins < end;
}

const DAGEN = ['zondag', 'maandag', 'dinsdag', 'woensdag', 'donderdag', 'vrijdag', 'zaterdag'];
const MAANDEN = ['januari', 'februari', 'maart', 'april', 'mei', 'juni', 'juli', 'augustus', 'september', 'oktober', 'november', 'december'];

export function fmtDutch(date = new Date()) {
  const hh = String(date.getHours()).padStart(2, '0');
  const mm = String(date.getMinutes()).padStart(2, '0');
  return `${DAGEN[date.getDay()]} ${date.getDate()} ${MAANDEN[date.getMonth()]} ${date.getFullYear()}, ${hh}:${mm}`;
}

export const todayStr = (date = new Date()) => {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
};

export function startOfLocalDayIso(date = new Date()) {
  const start = new Date(date);
  start.setHours(0, 0, 0, 0);
  return start.toISOString();
}

export function dagdeel(date = new Date()) {
  const h = date.getHours();
  if (h < 6) return 'nacht';
  if (h < 12) return 'ochtend';
  if (h < 18) return 'middag';
  if (h < 23) return 'avond';
  return 'nacht';
}

/** Probeert een fout leesbaar samen te vatten zonder stacktrace-ruis. */
export const errMsg = (e) => (e && (e.message || String(e))) || 'onbekende fout';
